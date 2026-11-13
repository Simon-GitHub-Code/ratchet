"""Drive one first-hour run: an empty directory, a scripted owner, and the route deciding each step.

The product session is one Claude Code session through the Agent SDK, with the plugin under test
loaded from a checkout (``ratchet@inline``) and the installed ratchet disabled for this session
only. Tools come through an allow-list, so the plugin's guard hook stays in the loop and every
prompt a user would see reaches ``can_use_tool``, which answers it as the owner. Questions go to
a small owner model (``owner.py``). Everything is logged as events (``reduce.py`` reads them) and
every SDK message is kept in ``transcript.jsonl``.

This module needs ``claude-agent-sdk`` and a signed-in ``claude``; ``run.py`` declares the
dependency. It is not in the unit suite: what it decides lives in the pure modules beside it.
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
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import claude_agent_sdk as sdk
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    HookMatcher,
    PermissionResultAllow,
    PermissionResultDeny,
    ResultMessage,
    SystemMessage,
    TaskNotificationMessage,
    query,
)

from first_hour import appcheck, gates, owner, reduce, safety, steps, transcript
from first_hour.persona import Persona

SETTLE_S = 20.0
IDLE_S = 30 * 60.0
PASTE_TIMEOUT_S = 30 * 60.0
TAIL = 4000
PROSE_REPLIES = 3
WAITED_TASKS = frozenset({"local_workflow", "local_agent", "remote_agent"})
TERMINAL = frozenset({"completed", "failed", "stopped", "killed", "error"})
OTHER_PLUGINS = ("mattpocock-skills", "pstack", "impeccable")
USER_FILES = (
    Path.home() / ".claude/settings.json",
    Path.home() / ".claude/plugins/installed_plugins.json",
    Path.home() / ".claude/plugins/known_marketplaces.json",
)
TOOLS = (
    "Read", "Glob", "Grep", "Bash", "Skill", "Agent", "Task", "Workflow", "TodoWrite",
    "TaskCreate", "TaskUpdate", "TaskList", "TaskGet", "TaskOutput", "WebFetch", "WebSearch",
    "ToolSearch", "Monitor",
)  # fmt: skip
WRITE_TOOLS = ("Edit", "Write", "NotebookEdit")
HARNESS = Path(__file__).resolve().parents[1]
MERGE_SUBJECT = re.compile(r"Merge (?:branch|remote-tracking branch) '([^']+)'")


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

    @property
    def app(self) -> Path:
        return self.root / "app"


def checked_root(path: Path) -> Path:
    """The run directory: under the system temp location, and empty or new."""
    root = path.resolve()
    temp = Path(tempfile.gettempdir()).resolve()
    if temp not in root.parents:
        msg = f"the run directory must be under {temp}, not {root}"
        raise SystemExit(msg)
    if root.exists() and any(root.iterdir()):
        msg = f"the run directory {root} is not empty"
        raise SystemExit(msg)
    return root


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


def run_git(app: Path, *args: str) -> str | None:
    done = subprocess.run(
        ["git", "-C", str(app), *args], capture_output=True, text=True, check=False
    )
    return done.stdout.strip() if done.returncode == 0 else None


@dataclass
class Log:
    """The run's event log and transcript, timed from the run's start."""

    root: Path
    t0: float = field(default_factory=time.monotonic)
    step: str = "setup"

    def elapsed(self) -> float:
        return time.monotonic() - self.t0

    def emit(self, kind: str, **fields: object) -> None:
        event = {"s": round(self.elapsed(), 1), "step": self.step, "kind": kind, **fields}
        with (self.root / "events.jsonl").open("a", encoding="utf-8") as out:
            out.write(json.dumps(event, default=str) + "\n")

    def message(self, message: object) -> None:
        name = type(message).__name__
        body = jsonable(message)
        assert isinstance(body, dict)  # noqa: S101
        record = {"s": round(self.elapsed(), 1), "step": self.step, "type": name, "msg": body}
        with (self.root / "transcript.jsonl").open("a", encoding="utf-8") as out:
            out.write(json.dumps(record, default=str) + "\n")
        for event in transcript.events(name, body):
            fields = {k: v for k, v in event.items() if k != "kind"}
            self.emit(str(event["kind"]), **fields)


def child_env(root: Path) -> dict[str, str]:
    """What every process of the run sees: no forge credentials, no editor, no prompts."""
    return {
        "GH_CONFIG_DIR": str(root / "gh"),
        "GH_TOKEN": "",
        "GITHUB_TOKEN": "",
        "GIT_MERGE_AUTOEDIT": "no",
        "GIT_TERMINAL_PROMPT": "0",
    }


class Owner:
    """The scripted owner: a small model in character, and the rule applied to its verdict."""

    def __init__(self, config: Config, log: Log) -> None:
        self.config = config
        self.log = log
        self.cwd = config.root / "owner"
        self.cwd.mkdir(exist_ok=True)
        self.usd = 0.0

    async def _ask(self, prompt: str, schema: dict[str, object]) -> object:
        options = ClaudeAgentOptions(
            model=self.config.owner_model,
            system_prompt=owner.system_prompt(self.config.persona),
            setting_sources=[],
            allowed_tools=[],
            tools=[],
            max_turns=3,
            output_format={"type": "json_schema", "schema": schema},
            cwd=str(self.cwd),
            cli_path=shutil.which("claude"),
            env=child_env(self.config.root),
        )
        found: object = ""
        async for message in query(prompt=prompt, options=options):
            if isinstance(message, ResultMessage):
                self.usd += message.total_cost_usd or 0.0
                self.log.emit("owner-cost", usd=message.total_cost_usd)
                found = (
                    message.structured_output
                    if message.structured_output is not None
                    else message.result or ""
                )
        return found

    async def answer(self, question: owner.Question, channel: owner.Channel) -> owner.Answer:
        prompt = owner.question_prompt(question)
        verdict = None
        for _ in range(2):
            try:
                verdict = owner.parse_verdict(await self._ask(prompt, owner.QUESTION_SCHEMA))
                break
            except (ValueError, TypeError) as error:
                self.log.emit("owner-error", error=str(error), question=question.text)
        if verdict is None:
            verdict = owner.Verdict(owner.Kind.DEFAULT_ABLE, (), "the owner model gave no verdict")
        reply = owner.answer(question, verdict)
        self.log.emit(**reply.event(channel))  # type: ignore[arg-type]
        return reply

    async def read_turn(self, text: str) -> owner.Reply:
        for _ in range(2):
            try:
                return owner.parse_reply(
                    await self._ask(owner.turn_prompt(text), owner.REPLY_SCHEMA)
                )
            except (ValueError, TypeError) as error:
                self.log.emit("owner-error", error=str(error), question="turn end")
        return owner.Reply(False, owner.Kind.DEFAULT_ABLE, "", "the owner model gave no reading")


class Session:
    """The product session: one Claude Code session the owner types into."""

    def __init__(self, config: Config, log: Log, owner_: Owner) -> None:
        self.config = config
        self.log = log
        self.owner = owner_
        self.queue: asyncio.Queue[object] = asyncio.Queue()
        self.waiting: set[str] = set()
        self.last_text = ""
        self.usd = 0.0
        self.stderr = (config.root / "stderr.log").open("a", encoding="utf-8")
        root = str(config.root)
        self.client = ClaudeSDKClient(
            ClaudeAgentOptions(
                cwd=str(config.app),
                model=config.model,
                cli_path=shutil.which("claude"),
                setting_sources=["user", "project", "local"],
                settings=json.dumps({"enabledPlugins": {"ratchet@ratchet": False}}),
                plugins=[{"type": "local", "path": str(config.plugin)}],
                allowed_tools=[*TOOLS, *(f"{t}(/{root}/**)" for t in WRITE_TOOLS)],
                disallowed_tools=list(safety.DENY_RULES),
                permission_mode="default",
                can_use_tool=self.can_use_tool,
                hooks={"PreToolUse": [HookMatcher(matcher="Bash", hooks=[self.refuse])]},
                max_budget_usd=config.budget.usd,
                env=child_env(config.root),
                stderr=lambda line: self.stderr.write(line + "\n"),
            )
        )
        self.pump: asyncio.Task[None] | None = None

    async def __aenter__(self) -> Session:
        await self.client.connect()
        self.pump = asyncio.create_task(self._pump())
        return self

    async def __aexit__(self, *_exc: object) -> None:
        if self.pump is not None:
            self.pump.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self.pump
        with contextlib.suppress(Exception):
            await self.client.disconnect()
        self.stderr.close()

    async def _pump(self) -> None:
        async for message in self.client.receive_messages():
            await self.queue.put(message)
        await self.queue.put(None)

    async def can_use_tool(
        self, tool_name: str, tool_input: dict[str, Any], context: Any
    ) -> PermissionResultAllow | PermissionResultDeny:
        if tool_name == "AskUserQuestion":
            answers = {}
            for question in owner.questions_of(tool_input):
                reply = await self.owner.answer(question, owner.Channel.DIALOG)
                answers[question.text] = reply.text
            return PermissionResultAllow(updated_input={**tool_input, "answers": answers})
        reason = getattr(context, "decision_reason", None) or ""
        origin = "guard" if reason.startswith("Ratchet guard") else "permission"
        refused = None
        if tool_name == "Bash":
            refused = safety.refusal(str(tool_input.get("command", "")))
        path = tool_input.get("file_path") or tool_input.get("notebook_path")
        if tool_name in WRITE_TOOLS and path and safety.outside(str(path), self.config.root):
            refused = "writes outside the run directory"
        self.log.emit(
            "prompt",
            tool=tool_name,
            origin=origin,
            decision="deny" if refused else "allow",
            reason=reason,
            title=getattr(context, "title", None),
            input=json.dumps(tool_input, default=str)[:600],
        )
        if refused:
            return PermissionResultDeny(message=f"The owner declined: this {refused}.")
        return PermissionResultAllow()

    async def refuse(self, data: dict[str, Any], _id: str | None, _ctx: Any) -> dict[str, Any]:
        command = str((data.get("tool_input") or {}).get("command", ""))
        why = safety.refusal(command)
        if why is None:
            return {}
        self.log.emit("refusal", why=why, command=command[:600])
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    f"The first-hour harness refuses this command: it {why}. This run never"
                    " reaches a remote and never changes the user's plugins or settings."
                ),
            }
        }

    def _track(self, message: object) -> None:
        if isinstance(message, SystemMessage) and message.subtype == "background_tasks_changed":
            tasks = message.data.get("tasks") or []
            self.waiting = {
                str(t.get("task_id"))
                for t in tasks
                if isinstance(t, dict) and t.get("task_type") in WAITED_TASKS
            }
        elif isinstance(message, TaskNotificationMessage) and message.status in TERMINAL:
            self.waiting.discard(message.task_id)
        elif isinstance(message, SystemMessage) and message.subtype == "task_updated":
            patch = message.data.get("patch") or {}
            if isinstance(patch, dict) and patch.get("status") in TERMINAL:
                self.waiting.discard(str(message.data.get("task_id")))

    async def turn(self, text: str, deadline: float) -> str:
        """Type ``text``; return the last result once no workflow or agent runs behind it."""
        await self.client.query(text)
        settled = False
        quiet_since = time.monotonic()
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                with contextlib.suppress(Exception):
                    await self.client.interrupt()
                raise Deadline
            wait = min(SETTLE_S if settled and not self.waiting else left, left)
            try:
                message = await asyncio.wait_for(self.queue.get(), timeout=wait)
            except TimeoutError:
                if settled and not self.waiting:
                    return self.last_text
                if time.monotonic() - quiet_since > IDLE_S:
                    self.log.emit("idle", waiting=sorted(self.waiting))
                    return self.last_text
                continue
            quiet_since = time.monotonic()
            if message is None:
                msg = "the product session ended"
                raise RuntimeError(msg)
            self.log.message(message)
            self._track(message)
            if isinstance(message, ResultMessage):
                settled = True
                self.last_text = message.result or self.last_text
                self.usd = max(self.usd, message.total_cost_usd or 0.0)
                if "budget" in (message.subtype or ""):
                    raise OverBudget
            elif isinstance(message, AssistantMessage):
                settled = False


@dataclass
class Driver:
    """The run: the route's instructions, the owner's moves, and the measurements after each."""

    config: Config
    log: Log
    owner: Owner
    session: Session
    history: list[steps.Seen] = field(default_factory=list)
    owner_text: str = ""
    usable: bool = False
    count: int = 0
    gate_runs: int = 0
    pasted: list[str] = field(default_factory=list)

    @property
    def app(self) -> Path:
        return self.config.app

    @property
    def deadline(self) -> float:
        return self.log.t0 + self.config.budget.minutes * 60

    def route(self) -> steps.Instruction:
        script = self.config.plugin / "scripts/route.py"
        done = subprocess.run(
            [sys.executable, str(script), "--root", str(self.app), "--json"],
            capture_output=True,
            text=True,
            check=True,
            env={**os.environ, **child_env(self.config.root)},
        )
        return steps.Instruction.parse(json.loads(done.stdout))

    def state(self) -> str:
        head = run_git(self.app, "rev-parse", "HEAD") or "no-head"
        status = run_git(self.app, "status", "--porcelain") or ""
        stashes = run_git(self.app, "stash", "list") or ""
        digest = hashlib.sha256((status + stashes).encode()).hexdigest()[:12]
        return f"{head[:12]}:{digest}"

    def default_branch(self) -> str | None:
        return next(
            (b for b in ("main", "master") if run_git(self.app, "rev-parse", "--verify", b)), None
        )

    def merges(self) -> list[str]:
        branch = self.default_branch()
        if branch is None:
            return []
        listed = run_git(self.app, "rev-list", "--merges", "--first-parent", branch) or ""
        return listed.split()

    async def life_events(self, instruction: steps.Instruction) -> steps.Move | None:
        """Each ``also:`` line that proposes a life event, put to the owner; a yes is the move."""
        for line in instruction.also:
            proposal = owner.life_event_question(line)
            if proposal is None:
                continue
            event, question = proposal
            reply = await self.owner.answer(question, owner.Channel.ALSO)
            if reply.text.startswith("Yes"):
                return steps.Move(steps.Action.TYPE, f"/ratchet:safeguard {event}")
            script = self.config.plugin / "scripts/route.py"
            subprocess.run(
                [sys.executable, str(script), "--root", str(self.app), "--decline", event],
                capture_output=True,
                check=False,
            )
        return None

    async def converse(self, text: str) -> None:
        """Type into the session, then answer what the turn's last message asks, in character."""
        self.owner_text = await self.session.turn(text, self.deadline)
        for _ in range(PROSE_REPLIES):
            reply = await self.owner.read_turn(self.owner_text)
            if not reply.needs_reply:
                return
            default = reply.kind is owner.Kind.DEFAULT_ABLE
            self.log.emit(
                "question",
                channel=owner.Channel.PROSE.value,
                **{"class": reply.kind.value},
                header="turn end",
                question=self.owner_text[-800:],
                options=[],
                recommended="the assistant's recommendation" if default else None,
                took_recommended=default,
                answer=reply.text,
                reason=reply.reason,
            )
            self.owner_text = await self.session.turn(reply.text, self.deadline)

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
        if any(g.status is gates.Status.RED for g in found):
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
        done = subprocess.run(  # noqa: S602
            command,
            shell=True,
            cwd=self.app,
            capture_output=True,
            text=True,
            check=False,
            stdin=subprocess.DEVNULL,
            timeout=PASTE_TIMEOUT_S,
            env={**os.environ, **child_env(self.config.root)},
        )
        return done.stdout + done.stderr, done.returncode

    def paste(self, commands: tuple[str, ...], why: str, depth: int = 0) -> None:
        """Run what the owner was told to paste; follow the ``next:`` lines it prints, once."""
        for command in commands:
            refused = safety.refusal(command)
            if refused:
                self.log.emit("refusal", why=refused, command=command)
                continue
            output, code = self.shell(command)
            self.count += 1
            self.pasted.append(command)
            (self.config.root / "logs" / f"{self.log.step}-paste-{self.count}.txt").write_text(
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
        tree = self.config.root / "appcheck" / label
        tree.parent.mkdir(exist_ok=True)
        if run_git(self.app, "worktree", "add", "--detach", str(tree), branch) is None:
            self.log.emit("app", finding=f"could not check out {branch} to start it", started=False)
            return None
        try:
            for name in ("node_modules", ".venv"):
                if (self.app / name).is_dir() and not (tree / name).exists():
                    (tree / name).symlink_to(self.app / name)
            result = appcheck.check(
                tree, self.config.persona.slice, self.config.root / "logs" / f"app-{label}.log"
            )
        finally:
            run_git(self.app, "worktree", "remove", "--force", str(tree))
        self.log.emit(**result.event())  # type: ignore[arg-type]
        return result

    def after(self, move: steps.Move, before: list[str]) -> None:
        """Record each merge the step made, then start the app on the default branch."""
        new = [c for c in self.merges() if c not in before]
        by_hand = any("git merge" in c for c in self.pasted)
        for commit in reversed(new):
            subject = run_git(self.app, "log", "-1", "--format=%s", commit) or ""
            match = MERGE_SUBJECT.search(subject)
            branch = match.group(1) if match else subject
            if move.action is not steps.Action.PASTE:
                how = "agent"
            elif by_hand:
                how = "hand"
            else:
                how = "entry-point"
            self.log.emit(
                "merge", commit=commit[:12], branch=branch, how=how, owner_text=self.owner_text
            )
        if new:
            result = self.app_check(self.log.step)
            if result is not None and result.slice_ok:
                self.usable = True
                self.log.emit("usable")

    async def step(self, move: steps.Move) -> None:
        before = self.merges()
        self.pasted = []
        started = time.monotonic()
        self.log.emit(
            "step", action=move.action.value, text=move.text, commands=list(move.commands)
        )
        if move.action is steps.Action.PASTE:
            self.paste(move.commands, "route")
        else:
            if move.action is steps.Action.ASK:
                self.log.emit("ask", text=move.text)
            await self.converse(move.text)
        self.log.emit("step-end", wall_s=round(time.monotonic() - started, 1))
        self.after(move, before)

    def spent(self) -> float:
        return self.session.usd + self.owner.usd

    async def loop(self) -> tuple[steps.Stop, str]:
        while True:
            instruction = self.route()
            self.log.emit(
                "route",
                command=instruction.command,
                stage=instruction.stage,
                why=instruction.why,
                then=instruction.then,
                also=list(instruction.also),
            )
            override = await self.life_events(instruction)
            self.history.append(steps.Seen(instruction.command, self.state()))
            reason = steps.stop_reason(
                self.config.budget,
                elapsed_s=self.log.elapsed(),
                cost_usd=self.spent(),
                usable=self.usable,
                history=self.history,
            )
            if reason is not None:
                return reason, instruction.command
            move = override or steps.move_for(instruction)
            if move.action is steps.Action.FORGE:
                return steps.Stop.FORGE, instruction.command
            self.log.step = (
                f"{len(self.history):02d}-"
                + re.sub(r"[^a-z0-9]+", "-", move.text.lower()).strip("-")[:40]
            )
            try:
                await self.step(move)
            except Deadline:
                return steps.Stop.WALL_CLOCK, "the budget ran out inside a step"
            except OverBudget:
                return steps.Stop.COST, "the session hit its cost budget"


def _plugin_meta(plugin: Path) -> dict[str, object]:
    manifest = json.loads((plugin / ".claude-plugin/plugin.json").read_text(encoding="utf-8"))
    return {
        "path": str(plugin),
        "version": manifest.get("version"),
        "commit": run_git(plugin, "rev-parse", "HEAD"),
        "dirty": bool(run_git(plugin, "status", "--porcelain")),
    }


def _other_plugins() -> dict[str, object]:
    path = Path.home() / ".claude/plugins/installed_plugins.json"
    data = json.loads(path.read_text(encoding="utf-8")).get("plugins", {})
    found: dict[str, object] = {}
    for key, entries in data.items():
        name = key.split("@", 1)[0]
        if name in OTHER_PLUGINS and entries:
            entry = entries[0]
            found[name] = {
                "id": key,
                "version": entry.get("version"),
                "commit": entry.get("gitCommitSha"),
            }
    return found


def _fingerprints() -> dict[str, str | None]:
    return {
        str(p): hashlib.sha256(p.read_bytes()).hexdigest()[:16] if p.is_file() else None
        for p in USER_FILES
    }


def _reap(root: Path) -> int:
    """Stop every process still running inside the run directory; how many there were."""
    reaped = 0
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit() or int(proc.name) == os.getpid():
            continue
        try:
            cwd = (proc / "cwd").resolve()
        except OSError:
            continue
        if cwd == root or root in cwd.parents:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.kill(int(proc.name), signal.SIGTERM)
                reaped += 1
    return reaped


def _version(command: list[str]) -> str:
    done = subprocess.run(command, capture_output=True, text=True, check=False)
    return done.stdout.strip()


async def drive(config: Config) -> dict[str, object]:
    """Run the first hour once; write the event log, the measurements and the table."""
    for name in ("app", "gh", "logs"):
        (config.root / name).mkdir(parents=True, exist_ok=True)
    log = Log(config.root)
    before = _fingerprints()
    log.emit(
        "run",
        started=dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        persona=config.persona.name,
        model=config.model,
        owner_model=config.owner_model,
        budget=dataclasses.asdict(config.budget),
        plugin=_plugin_meta(config.plugin),
        harness={
            "commit": run_git(HARNESS, "rev-parse", "HEAD"),
            "dirty": bool(run_git(HARNESS, "status", "--porcelain", "--", ".")),
        },
        plugins=_other_plugins(),
        claude=_version(["claude", "--version"]),
        sdk=getattr(sdk, "__version__", None),
        git_hooks_path=_version(["git", "config", "--global", "--get", "core.hooksPath"]) or None,
        load_avg=os.getloadavg(),
    )
    owner_ = Owner(config, log)
    reason, detail = steps.Stop.ERROR, ""
    async with Session(config, log, owner_) as session:
        driver = Driver(config, log, owner_, session)
        try:
            reason, detail = await driver.loop()
        except Exception as error:  # noqa: BLE001
            reason, detail = steps.Stop.ERROR, f"{type(error).__name__}: {error}"
        log.step = "end"
        log.emit("stop", reason=reason.value, detail=detail, load_avg=os.getloadavg())
        if not driver.usable:
            driver.app_check("final")
    after = _fingerprints()
    log.emit("integrity", unchanged=before == after, files=after, reaped=_reap(config.root))
    metrics = reduce.reduce(reduce.read(config.root / "events.jsonl"))
    (config.root / "metrics.json").write_text(json.dumps(metrics, indent=2) + "\n", "utf-8")
    (config.root / "table.md").write_text(reduce.table(metrics), "utf-8")
    return metrics
