"""Drive one first-hour run: an empty directory, a scripted owner, and the route deciding each step.

The driver never chooses a step: it runs the plugin's route and does what its ``next:`` line
names (``steps.py``). Questions go to the owner model (``owner.py``). Every process of the run
starts in the run's sandbox (``sandbox.py``), and every tool call of the product session passes
the policy (``policy.py``). Everything is logged as events, which ``reduce.py`` folds into the
measurements. This module starts no model itself: ``Models`` does, so a test can drive a whole
run with fakes and the Agent SDK stays in ``drive.py``.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, TypeVar

from first_hour import appcheck, gates, merges, owner, reduce, sandbox, steps, transcript

if TYPE_CHECKING:
    from collections.abc import Awaitable, Iterable, Mapping, Sequence

    from first_hour.persona import Persona

PASTE_TIMEOUT_S = 30 * 60.0
ROUTE_TIMEOUT_S = 120.0
GIT_TIMEOUT_S = 60.0
CLOSE_S = 30.0
TAIL = 4000
PROSE_REPLIES = 3
OTHER_PLUGINS = ("mattpocock-skills", "pstack", "impeccable")
WATCHED = (
    ".claude/settings.json",
    ".claude/settings.local.json",
    ".claude/CLAUDE.md",
    ".claude/plugins/installed_plugins.json",
    ".claude/plugins/known_marketplaces.json",
)
WATCHED_DIRS = (".claude/rules", ".claude/agents")
HARNESS = Path(__file__).resolve().parents[1]
SIGNALS = (signal.SIGINT, signal.SIGTERM)
T = TypeVar("T")


class Deadline(Exception):  # noqa: N818
    """The wall-clock budget ran out."""


class OverBudget(Exception):  # noqa: N818
    """The cost budget ran out."""


@dataclass(frozen=True)
class Config:
    """One run's inputs."""

    plugin: Path
    root: Path
    persona: Persona
    budget: steps.Budget
    model: str = "opus"
    owner_model: str = "haiku"


@dataclass(frozen=True)
class Operator:
    """What the harness reads of the operator's machine, once, before the run starts.

    After this the run reads nothing of the operator's: the companions are copied, the tools are
    bound read-only, and the watched files are only hashed again at the end.
    """

    tools: Mapping[str, Path]
    plugins: Sequence[sandbox.Plugin]
    claude: str
    watched: Sequence[Path]
    projects: Path

    @classmethod
    def read(cls, home: Path, found: Mapping[str, str | None], claude: str) -> Operator:
        """The operator's toolchain, companion plugins and the files the integrity check hashes."""
        plugins_dir = home / ".claude" / "plugins"
        registry = _json(plugins_dir / "installed_plugins.json")
        markets = _json(plugins_dir / "known_marketplaces.json")
        watched = [home / p for p in WATCHED] + [
            f for d in WATCHED_DIRS if (home / d).is_dir() for f in sorted((home / d).rglob("*"))
        ]
        return cls(
            tools=sandbox.toolchain(found),
            plugins=sandbox.installed(registry, markets, OTHER_PLUGINS),
            claude=claude,
            watched=watched,
            projects=home / ".claude" / "projects",
        )


def _json(path: Path) -> dict[str, object]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def jsonable(value: object) -> object:
    """An SDK message as plain JSON, its dataclasses named by ``_type``."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        fields = {f.name: jsonable(getattr(value, f.name)) for f in dataclasses.fields(value)}
        return {"_type": type(value).__name__, **fields}
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


@dataclass
class Log:
    """The run's event log and transcript, timed from the run's start."""

    root: Path
    t0: float = field(default_factory=time.monotonic)
    epoch: float = field(default_factory=time.time)
    step: str = "setup"

    def elapsed(self) -> float:
        """Seconds since the run started."""
        return time.monotonic() - self.t0

    def emit(self, kind: str, **fields: object) -> None:
        """Append one event to the log."""
        event: dict[str, object] = {"s": round(self.elapsed(), 1), "step": self.step, "kind": kind}
        event.update(fields)
        with (self.root / "events.jsonl").open("a", encoding="utf-8") as out:
            out.write(json.dumps(event, default=str) + "\n")

    def message(self, message: object) -> None:
        """Keep one SDK message whole, and log the events it carries."""
        name = type(message).__name__
        body = jsonable(message)
        body = body if isinstance(body, dict) else {"value": body}
        record = {"s": round(self.elapsed(), 1), "step": self.step, "type": name, "msg": body}
        with (self.root / "transcript.jsonl").open("a", encoding="utf-8") as out:
            out.write(json.dumps(record, default=str) + "\n")
        for event in transcript.events(name, body):
            fields = {k: v for k, v in event.items() if k != "kind"}
            self.emit(str(event["kind"]), **fields)


@dataclass
class Run:
    """One run's place on disk, its clock, its environment, and the processes it started."""

    config: Config
    layout: sandbox.Layout
    log: Log
    env: dict[str, str]
    tools: Mapping[str, Path]
    sandboxed: bool = True
    children: set[subprocess.Popen[bytes]] = field(default_factory=set)
    halted: bool = False

    @property
    def deadline(self) -> float:
        """When the wall-clock budget runs out, on the monotonic clock."""
        return self.log.t0 + self.config.budget.minutes * 60

    def left(self) -> float:
        """Seconds left on the wall clock; ``Deadline`` once there are none."""
        left = self.deadline - time.monotonic()
        if left <= 0:
            raise Deadline
        return left

    def wrap(self, command: Sequence[str], *, writable: Sequence[Path] | None = None) -> list[str]:
        """``command`` as the run starts it: in the sandbox, unless a test turned it off."""
        if not self.sandboxed:
            return list(command)
        return sandbox.argv(self.layout, command, tools=self.tools.values(), writable=writable)

    def track(self, process: subprocess.Popen[bytes]) -> None:
        """Remember a process the run started, so that a stop can end it."""
        self.children.add(process)

    def call(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        timeout: float,
        writable: Sequence[Path] | None = None,
    ) -> tuple[str, int]:
        """Run one command to its end; its output and exit code. A timeout ends its group.

        Once the run is halted nothing new starts: a thread still finishing a paste after a stop
        gets ``Deadline`` instead of a process.
        """
        if self.halted:
            raise Deadline
        with subprocess.Popen(
            self.wrap(command, writable=writable),
            cwd=cwd,
            env=self.env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        ) as process:
            self.track(process)
            try:
                out, _ = process.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                appcheck.stop(process)
                raise
            finally:
                self.children.discard(process)
        return out.decode("utf-8", errors="replace"), process.returncode

    def git(self, *args: str, writable: Sequence[Path] = ()) -> str | None:
        """Git's output in the owner's project, or ``None`` when git fails."""
        out, code = self.call(
            ["git", "-C", str(self.layout.app), *args],
            cwd=self.layout.app,
            timeout=GIT_TIMEOUT_S,
            writable=writable,
        )
        return out.strip() if code == 0 else None

    def halt(self) -> int:
        """Start nothing more, and end every process the run started; how many were running."""
        self.halted = True
        running = [p for p in list(self.children) if p.poll() is None]
        for process in running:
            appcheck.stop(process)
        self.children.clear()
        return len(running)


class Session(Protocol):
    """The product session: one Claude Code session the owner types into."""

    costs: dict[str, float]

    async def turn(self, text: str, deadline: float) -> str:
        """Type ``text``; the last result, once nothing runs behind it."""
        ...

    async def close(self) -> None:
        """Stop the session and everything it started."""
        ...


class OwnerModel(Protocol):
    """The owner model: a small model in character."""

    usd: float

    async def answer(self, question: owner.Question, channel: owner.Channel) -> owner.Answer:
        """Answer one question in character."""
        ...

    async def read_turn(self, text: str) -> owner.Reply:
        """Whether a turn's last message asks the owner something."""
        ...


class Models(Protocol):
    """What starts the run's models: the owner model, then the product session."""

    def owner(self, run: Run) -> OwnerModel:
        """The owner model for this run."""
        ...

    async def session(self, run: Run, owner_: OwnerModel) -> Session:
        """The product session for this run, connected."""
        ...


async def bounded(run: Run, work: Awaitable[T]) -> T:
    """``work``, given until the wall clock runs out; ``Deadline`` when it does not finish."""
    try:
        return await asyncio.wait_for(work, timeout=run.left())
    except TimeoutError:
        raise Deadline from None


@dataclass
class Driver:
    """The run: the route's instructions, the owner's moves, and the measurements after each."""

    run: Run
    owner: OwnerModel
    session: Session
    history: list[steps.Seen] = field(default_factory=list)
    owner_text: str = ""
    usable: bool = False
    count: int = 0
    gate_runs: int = 0
    windows: list[merges.Window] = field(default_factory=list)
    landed: list[merges.Landing] = field(default_factory=list)

    @property
    def log(self) -> Log:
        """The run's log."""
        return self.run.log

    @property
    def layout(self) -> sandbox.Layout:
        """The run's directories."""
        return self.run.layout

    def route(self) -> steps.Instruction:
        """The route's instruction for the checkout now."""
        script = self.layout.pinned / "scripts" / "route.py"
        out, code = self.run.call(
            ["python3", str(script), "--root", str(self.layout.app), "--json"],
            cwd=self.layout.app,
            timeout=min(ROUTE_TIMEOUT_S, self.run.left()),
        )
        if code != 0:
            msg = f"the route failed (exit {code}): {out[-400:]}"
            raise RuntimeError(msg)
        return steps.Instruction.parse(json.loads(out))

    def state(self) -> str:
        """The checkout's state: HEAD, the tree and the stash."""
        head = self.run.git("rev-parse", "HEAD") or "no-head"
        status = self.run.git("status", "--porcelain") or ""
        stashes = self.run.git("stash", "list") or ""
        digest = hashlib.sha256((status + stashes).encode()).hexdigest()[:12]
        return f"{head[:12]}:{digest}"

    def default_branch(self) -> str | None:
        """The default branch, once one exists."""
        return next(
            (b for b in ("main", "master") if self.run.git("rev-parse", "--verify", "-q", b)),
            None,
        )

    async def life_events(self, instruction: steps.Instruction) -> steps.Move | None:
        """Each ``also:`` line that proposes a life event, put to the owner; a yes is the move."""
        for line in instruction.also:
            proposal = owner.life_event_question(line)
            if proposal is None:
                continue
            event, question = proposal
            reply = await bounded(self.run, self.owner.answer(question, owner.Channel.ALSO))
            if reply.text.startswith("Yes"):
                return steps.Move(steps.Action.TYPE, f"/ratchet:safeguard {event}")
            script = self.layout.pinned / "scripts" / "route.py"
            await asyncio.to_thread(
                self.run.call,
                ["python3", str(script), "--root", str(self.layout.app), "--decline", event],
                cwd=self.layout.app,
                timeout=min(ROUTE_TIMEOUT_S, self.run.left()),
            )
        return None

    async def converse(self, text: str) -> None:
        """Type into the session, then answer what the turn's last message asks, in character."""
        self.owner_text = await self.session.turn(text, self.run.deadline)
        for _ in range(PROSE_REPLIES):
            reply = await bounded(self.run, self.owner.read_turn(self.owner_text))
            if not reply.needs_reply:
                return
            self.log.emit(**owner.prose_question(self.owner_text, reply))  # type: ignore[arg-type]
            self.owner_text = await self.session.turn(reply.text, self.run.deadline)

    def run_gates(self, command: str, output: str) -> None:
        """Record the gates in a pasted command's output; rerun a red one once."""
        found = gates.parse(output)
        if not found:
            return
        self.gate_runs += 1
        run_id = self.gate_runs
        self.log.emit(
            "gates",
            id=run_id,
            attempt=1,
            command=command,
            gates=[dataclasses.asdict(g) for g in found],
        )
        # Why: rerunning a merge command would land the merge on a flaky second try.
        if "merge" not in command and any(g.status is gates.Status.RED for g in found):
            again, _code = self.shell(command)
            self.log.emit(
                "gates",
                id=run_id,
                of=run_id,
                attempt=2,
                command=command,
                gates=[dataclasses.asdict(g) for g in gates.parse(again)],
            )

    def shell(self, command: str) -> tuple[str, int]:
        """Run one command line in the project as the owner would, inside the sandbox."""
        timeout = min(PASTE_TIMEOUT_S, self.run.left())
        try:
            return self.run.call(["sh", "-c", command], cwd=self.layout.app, timeout=timeout)
        except subprocess.TimeoutExpired:
            self.log.emit("paste-timeout", command=command, after_s=round(timeout, 1))
            raise Deadline from None

    def paste(self, commands: Sequence[str], why: str, depth: int = 0) -> None:
        """Run what the owner was told to paste; follow the ``next:`` lines it prints, once."""
        for command in commands:
            started = time.time()
            try:
                output, code = self.shell(command)
            finally:
                self.windows.append(merges.Window(command, started, time.time()))
            self.count += 1
            (self.layout.logs / f"{self.log.step}-paste-{self.count}.txt").write_text(
                f"$ {command}\n{output}", encoding="utf-8"
            )
            self.log.emit("paste", command=command, exit=code, why=why, tail=output[-TAIL:])
            self.owner_text = output[-TAIL:]
            self.run_gates(command, output)
            follow = tuple(c for c in steps.next_commands(output) if c not in commands)
            if follow and depth == 0:
                self.paste(follow, "next-line", depth + 1)

    def app_check(self, label: str) -> appcheck.Result | None:
        """Start the default branch's app in a work tree of its own and exercise the slice."""
        branch = self.default_branch()
        if branch is None:
            return None
        tree = self.layout.appcheck / label
        writable = (*self.layout.writable, self.layout.appcheck)
        if (
            self.run.git("worktree", "add", "--detach", str(tree), branch, writable=writable)
            is None
        ):
            self.log.emit("app", finding=f"could not check out {branch} to start it", started=False)
            return None
        try:
            for name in ("node_modules", ".venv"):
                if (self.layout.app / name).is_dir() and not (tree / name).exists():
                    (tree / name).symlink_to(self.layout.app / name)
            result = appcheck.check(
                tree,
                self.run.config.persona.slice,
                self.layout.logs / f"app-{label}.log",
                launch=appcheck.Launch(
                    wrap=lambda command: self.run.wrap(command, writable=writable),
                    env=self.run.env,
                    on_start=self.run.track,
                ),
            )
        finally:
            self.run.git("worktree", "remove", "--force", str(tree), writable=writable)
        self.log.emit(**result.event())  # type: ignore[arg-type]
        return result

    def record_merges(self) -> list[merges.Landing]:
        """Log each merge onto the default branch since the last look, with who made it."""
        branch = self.default_branch()
        if branch is None:
            return []
        reflog = self.run.git(
            "log", "-g", f"--format={merges.REFLOG_FORMAT}", "--date=unix", branch
        )
        found = merges.new(self.landed, merges.landings(reflog or ""))
        for landing in found:
            self.landed.append(landing)
            self.log.emit(
                "merge",
                # Why: the reflog keeps whole seconds, so a merge in the run's first second
                # would otherwise land before the run started.
                s=round(max(landing.at - self.log.epoch, 0.0), 1),
                commit=landing.sha[:12],
                branch=landing.branch,
                how=merges.how(landing, self.windows).value,
                reflog=landing.message,
                owner_text=self.owner_text,
            )
        return found

    async def step(self, move: steps.Move) -> None:
        """Do one move, then measure what it merged; merges are logged even when it is cut off."""
        started = time.monotonic()
        self.log.emit(
            "step", action=move.action.value, text=move.text, commands=list(move.commands)
        )
        try:
            if move.action is steps.Action.PASTE:
                await asyncio.to_thread(self.paste, move.commands, "route")
            else:
                if move.action is steps.Action.ASK:
                    self.log.emit("ask", text=move.text)
                await self.converse(move.text)
        finally:
            self.log.emit("step-end", wall_s=round(time.monotonic() - started, 1))
            landed = await asyncio.to_thread(self.record_merges)
        if landed:
            result = await asyncio.to_thread(self.app_check, self.log.step)
            if result is not None and result.usable:
                self.usable = True
                self.log.emit("usable")

    def spent(self) -> float:
        """Dollars spent so far, product session and owner model."""
        return sum(self.session.costs.values()) + self.owner.usd

    async def next_move(self) -> tuple[steps.Move | None, steps.Stop | None, str]:
        """The route's instruction now, and either the owner's move or why the run stops."""
        instruction = await asyncio.to_thread(self.route)
        self.log.emit(
            "route",
            command=instruction.command,
            stage=instruction.stage,
            why=instruction.why,
            then=instruction.then,
            also=list(instruction.also),
        )
        self.history.append(steps.Seen(instruction.command, await asyncio.to_thread(self.state)))
        reason = steps.stop_reason(
            self.run.config.budget,
            elapsed_s=self.log.elapsed(),
            cost_usd=self.spent(),
            usable=self.usable,
            history=self.history,
        )
        if reason is not None:
            return None, reason, instruction.command
        move = await self.life_events(instruction) or steps.move_for(instruction)
        if move.action is steps.Action.FORGE:
            return None, steps.Stop.FORGE, instruction.command
        return move, None, ""

    async def loop(self) -> tuple[steps.Stop, str]:
        """Follow the route until a stop condition holds; every wait is bounded by the clock."""
        while True:
            try:
                move, reason, detail = await self.next_move()
                if move is None:
                    return reason or steps.Stop.ERROR, detail
                slug = re.sub(r"[^a-z0-9]+", "-", move.text.lower()).strip("-")[:40]
                self.log.step = f"{len(self.history):02d}-{slug}"
                await self.step(move)
            except Deadline:
                return steps.Stop.WALL_CLOCK, "the budget ran out inside a step"
            except OverBudget:
                return steps.Stop.COST, "the session hit its cost budget"


def fingerprints(paths: Iterable[Path]) -> dict[str, str | None]:
    """A short hash of each file, ``None`` for one that does not exist."""
    return {
        str(p): hashlib.sha256(p.read_bytes()).hexdigest()[:16] if p.is_file() else None
        for p in paths
    }


def reap(roots: Sequence[Path]) -> int:
    """Stop every process still running inside the run's directories; how many there were."""
    reaped = 0
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit() or int(proc.name) == os.getpid():
            continue
        try:
            cwd = (proc / "cwd").readlink()
        except OSError:  # another user's process, or one that just ended
            continue
        if any(cwd == root or root in cwd.parents for root in roots):
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.kill(int(proc.name), signal.SIGKILL)
                reaped += 1
    return reaped


def slug(path: Path) -> str:
    """Claude Code's name for a project directory under ``~/.claude/projects``."""
    return re.sub(r"[^A-Za-z0-9]", "-", str(path))


def tidy(run: Run, projects: Path) -> dict[str, object]:
    """Clean up after the run, and name what it left in the operator's projects.

    The drain's leftover work trees and the run's runtime directory are removed; the operator's
    ``~/.claude/projects`` is only listed, never deleted from.
    """
    listed = run.git("worktree", "list", "--porcelain") or ""
    trees = [
        Path(line.split(" ", 1)[1]) for line in listed.splitlines() if line.startswith("worktree ")
    ]
    removed = []
    for tree in trees:
        if tree != run.layout.app and run.layout.root in tree.parents:
            run.git("worktree", "remove", "--force", str(tree))
            removed.append(str(tree))
    run.git("worktree", "prune")
    shutil.rmtree(run.layout.runtime, ignore_errors=True)
    mine = slug(run.layout.root)
    left = sorted(str(p) for p in projects.glob(f"{mine}*")) if projects.is_dir() else []
    return {"removed_work_trees": removed, "claude_projects": left}


def _pin(config: Config, layout: sandbox.Layout) -> dict[str, object]:
    """Clone the checkout's HEAD into the run, so a commit landing on it mid-run changes nothing.

    Why: the first baseline ran against the main checkout and a merge landed on it eight minutes
    in; the run measured two plugins. Uncommitted changes in the checkout are not under test.
    """
    subprocess.run(
        ["git", "clone", "--quiet", "--no-hardlinks", str(config.plugin), str(layout.pinned)],
        check=True,
        capture_output=True,
    )

    def git(path: Path, *args: str) -> str:
        done = subprocess.run(
            ["git", "-C", str(path), *args], capture_output=True, text=True, check=False
        )
        return done.stdout.strip()

    manifest = _json(layout.pinned / ".claude-plugin" / "plugin.json")
    return {
        "version": manifest.get("version"),
        "commit": git(layout.pinned, "rev-parse", "HEAD"),
        "dirty": bool(git(config.plugin, "status", "--porcelain")),
    }


def under_test(layout: sandbox.Layout, meta: Mapping[str, object]) -> sandbox.Plugin:
    """The pinned clone as the registry names it."""
    return sandbox.Plugin(
        key="ratchet@ratchet",
        version=str(meta.get("version")),
        commit=str(meta.get("commit")) or None,
        path=layout.pinned,
        source={"source": "github", "repo": "Simon-GitHub-Code/ratchet"},
    )


def setup(config: Config, operator: Operator, environ: Mapping[str, str]) -> Run:
    """The run's directory, home and environment, ready for its first process."""
    layout = sandbox.Layout(config.root)
    layout.root.mkdir(parents=True, exist_ok=True)
    plugin = _pin(config, layout)
    sandbox.prepare(layout, operator.tools, [*operator.plugins, under_test(layout, plugin)])
    for name in ("events.jsonl", "transcript.jsonl", "stderr.log"):
        (layout.root / name).touch()
    log = Log(layout.root)
    log.emit(
        "run",
        started=dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        persona=config.persona.name,
        model=config.model,
        owner_model=config.owner_model,
        budget=dataclasses.asdict(config.budget),
        plugin=plugin,
        plugins={
            p.name: {"id": p.key, "version": p.version, "commit": p.commit}
            for p in operator.plugins
        },
        claude=operator.claude,
        harness=_harness(),
        load_avg=os.getloadavg(),
    )
    env = sandbox.environment(layout, environ)
    return Run(config=config, layout=layout, log=log, env=env, tools=operator.tools)


def _harness() -> dict[str, object]:
    def git(*args: str) -> str:
        done = subprocess.run(
            ["git", "-C", str(HARNESS), *args], capture_output=True, text=True, check=False
        )
        return done.stdout.strip()

    return {
        "commit": git("rev-parse", "HEAD"),
        "dirty": bool(git("status", "--porcelain", "--", ".")),
    }


async def _close(session: Session | None) -> None:
    if session is None:
        return
    with contextlib.suppress(Exception):
        await asyncio.wait_for(session.close(), timeout=CLOSE_S)


async def _follow(run: Run, models: Models) -> tuple[steps.Stop, str, Driver | None]:
    """Start the models and follow the route; why the run stopped, and its driver.

    The driver is ``None`` when the models never started. The session is closed however the
    run ends, a cancel included.
    """
    driver: Driver | None = None
    session: Session | None = None
    try:
        owner_ = models.owner(run)
        session = await models.session(run, owner_)
        driver = Driver(run, owner_, session)
        reason, detail = await driver.loop()
    except Exception as error:  # noqa: BLE001
        reason, detail = steps.Stop.ERROR, f"{type(error).__name__}: {error}"
    finally:
        await _close(session)
    return reason, detail, driver


@dataclass
class Interrupt:
    """The first SIGINT or SIGTERM, which cancels the run's work; a second changes nothing."""

    task: asyncio.Task[tuple[steps.Stop, str, Driver | None]]
    name: str = ""

    def __call__(self, signum: int) -> None:
        """Cancel the work once, and remember which signal did it."""
        if not self.name:
            self.name = signal.Signals(signum).name
            self.task.cancel()


async def drive(run: Run, operator: Operator, models: Models) -> dict[str, object]:
    """Run the first hour once; write the event log, the measurements and the table.

    SIGINT and SIGTERM stop the run the same way the budget does: the sessions are closed, every
    process the run started is ended, and the measurements are written from what was logged.
    """
    before = fingerprints(operator.watched)
    loop = asyncio.get_running_loop()
    interrupt = Interrupt(asyncio.ensure_future(_follow(run, models)))
    for signum in SIGNALS:
        loop.add_signal_handler(signum, interrupt, signum)
    try:
        reason, detail, driver = await interrupt.task
    except asyncio.CancelledError:
        if not interrupt.name:
            raise
        reason, detail, driver = steps.Stop.INTERRUPTED, interrupt.name, None
    finally:
        for signum in SIGNALS:
            loop.remove_signal_handler(signum)
        stopped = run.halt()
    run.log.step = "end"
    run.log.emit("stop", reason=reason.value, detail=detail, load_avg=os.getloadavg())
    # Why: after an interrupt a cancelled paste's thread may still be unwinding on `run`, which
    # stays halted; the epilogue gets a run of its own.
    epilogue = dataclasses.replace(run, children=set(), halted=False)
    if driver is not None:
        driver.run = epilogue
        await asyncio.to_thread(driver.record_merges)
        if not driver.usable:
            await asyncio.to_thread(driver.app_check, "final")
    left = tidy(epilogue, operator.projects)
    after = fingerprints(operator.watched)
    run.log.emit(
        "integrity",
        unchanged=before == after,
        before=before,
        after=after,
        changed=sorted(p for p in after if before.get(p) != after[p]),
        stopped=stopped + epilogue.halt(),
        reaped=reap([run.layout.root]),
        **left,
    )
    metrics = reduce.reduce(reduce.read(run.layout.root / "events.jsonl"))
    (run.layout.root / "metrics.json").write_text(json.dumps(metrics, indent=2) + "\n", "utf-8")
    (run.layout.root / "table.md").write_text(reduce.table(metrics), "utf-8")
    return metrics


def checked_root(path: Path, temp: Path) -> Path:
    """The run directory: under the system temp location, and empty or new."""
    root = path.resolve()
    if temp.resolve() not in root.parents:
        msg = f"the run directory must be under {temp}, not {root}"
        raise SystemExit(msg)
    if root.exists() and any(root.iterdir()):
        msg = f"the run directory {root} is not empty"
        raise SystemExit(msg)
    return root
