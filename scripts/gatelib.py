"""What the gate scripts share: git, the merge base, the changed paths, the thresholds file.

Every gate that compares head with "before" means the same before: the merge base of ``HEAD``
with the default branch, which is the remote's ``HEAD``, else the branch the binding names under
``- Default branch:``, else ``main`` or ``master``. On the default branch itself, or when ``HEAD``
is the merge base, before is ``HEAD~1``. ``lowered``, ``guard-diff`` and the gates here all call
``base_commit`` so no two of them can disagree about it.
"""

from __future__ import annotations

import contextlib
import fnmatch
import os
import re
import signal
import socket
import subprocess
import time
import tomllib
from functools import lru_cache
from http.client import HTTPConnection, HTTPSConnection
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from collections.abc import Iterator
    from types import FrameType

FALLBACK_BRANCHES = ("main", "master")
BINDING = Path("docs/process/binding.md")
# Why: `with_service.py` exports the URL it waited on, so a command run inside it can tell the
# readiness route from the routes a user requests.
READY_ENV = "RATCHET_READY"
THRESHOLDS = Path("quality/thresholds.toml")
STATE = Path(".ratchet")
DEFAULT_BRANCH_LINE = re.compile(r"^- Default branch: `([^`]+)`$", re.MULTILINE)


def git(root: Path, *args: str) -> str | None:
    """``git -C root args`` stdout, or ``None`` when git fails."""
    result = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=False
    )
    return result.stdout.strip() if result.returncode == 0 else None


def default_branches(root: Path) -> list[str]:
    """Candidate default branches, most authoritative first."""
    candidates: list[str] = []
    remote_head = git(root, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    if remote_head:
        candidates.append(remote_head)
    binding = root / BINDING
    if binding.is_file():
        match = DEFAULT_BRANCH_LINE.search(binding.read_text(encoding="utf-8"))
        if match:
            candidates.extend((f"origin/{match.group(1)}", match.group(1)))
    for name in FALLBACK_BRANCHES:
        candidates.extend((f"origin/{name}", name))
    return list(dict.fromkeys(candidates))


def base_commit(root: Path, base: str | None = None) -> str | None:
    """The merge base with the default branch, ``HEAD~1`` on that branch, ``None`` when neither."""
    head = git(root, "rev-parse", "--abbrev-ref", "HEAD")
    for branch in [base] if base else default_branches(root):
        merge_base = git(root, "merge-base", "HEAD", branch)
        if merge_base is None:
            continue
        if head in (branch, branch.removeprefix("origin/")):
            return git(root, "rev-parse", "HEAD~1")
        # Why: a fresh branch with uncommitted work has HEAD as its merge base; the work tree
        # is then compared with HEAD, not with the commit before it.
        return merge_base
    return git(root, "rev-parse", "HEAD~1")


def adoption(root: Path, base: str | None) -> bool:
    """Whether the change since ``base`` is the adoption: ``base`` holds no binding.

    The method was not there to judge that commit, so every line is new against it. Read from
    the commit, so no later change can claim it: once the adoption has merged, every base holds
    a binding.
    """
    return base is not None and show(root, base, str(BINDING)) is None


def changed_paths(root: Path, base: str) -> list[str]:
    """Paths changed since ``base``, committed or not, untracked files included; renames split."""
    tracked = git(root, "diff", "--name-only", "--no-renames", "-z", base) or ""
    untracked = git(root, "ls-files", "--others", "--exclude-standard", "--full-name", "-z", ":/")
    return sorted({p for p in (tracked + (untracked or "")).split("\0") if p})


def tracked_paths(root: Path, ref: str | None = None) -> list[str]:
    """Every path git tracks at ``ref`` (the work tree when ``None``), untracked files included."""
    if ref is not None:
        listed = git(root, "ls-tree", "-r", "--name-only", "-z", ref) or ""
        return sorted(p for p in listed.split("\0") if p)
    listed = git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard") or ""
    return sorted({p for p in listed.split("\0") if p and (root / p).is_file()})


def show(root: Path, ref: str, path: str) -> str | None:
    """The file's text at ``ref``, or ``None`` when it is not there."""
    return git(root, "show", f"{ref}:{path}")


def default_jobs() -> int:
    """``RATCHET_JOBS`` when it is a count, else every core: the cap on what runs at once.

    Why: the gates a tier runs at once and the scratch copies a bite proof runs at once share
    one machine, so one number caps both, the one a shared machine sets.
    """
    value = os.environ.get("RATCHET_JOBS", "")
    return int(value) if value.isdigit() and int(value) > 0 else os.cpu_count() or 1


def entry_point(root: Path, name: str) -> str | None:
    """The command the binding's Entry points table gives ``name``, or ``None``."""
    text = read_text(root / BINDING) or ""
    row = re.search(rf"^\|\s*{re.escape(name)}\s*\|\s*`([^`]+)`\s*\|", text, re.MULTILINE)
    return row.group(1) if row else None


def state_dir(root: Path) -> Path:
    """``root``'s directory of uncommitted method state, made on first use and ignored by itself.

    Why: the gates' timings, the queue's ledger and the question log belong to one clone and to no
    commit, and a work tree they made dirty would turn the feature preflight red.
    """
    directory = root / STATE
    directory.mkdir(parents=True, exist_ok=True)
    ignore = directory / ".gitignore"
    if not ignore.is_file():
        ignore.write_text("*\n", encoding="utf-8")
    return directory


def subjects(root: Path, base: str) -> list[str]:
    """The subject line of each non-merge commit after ``base`` up to ``HEAD``."""
    listed = git(root, "log", "--no-merges", "--format=%s", f"{base}..HEAD")
    return [line for line in (listed or "").splitlines() if line]


def read_text(path: Path) -> str | None:
    """The file's text, decoded leniently, or ``None`` when it is missing or binary-looking."""
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if b"\0" in data[:8000]:
        return None
    return data.decode("utf-8", errors="replace")


def thresholds(root: Path) -> dict[str, object]:
    """``quality/thresholds.toml`` parsed, or an empty table when it is missing."""
    path = root / THRESHOLDS
    return tomllib.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def table(data: dict[str, object], *keys: str) -> dict[str, object]:
    """The nested table at ``keys``, or an empty one."""
    current: object = data
    for key in keys:
        current = current.get(key, {}) if isinstance(current, dict) else {}
    return current if isinstance(current, dict) else {}


def matches(path: str, patterns: list[str]) -> bool:
    """Whether a repository-relative path matches one of the globs (``**`` spans segments).

    The ``PurePosixPath.full_match`` rules, written out because that method arrived in Python
    3.13 and the gates run on the runner's ``python3``. ``ratchet_guards.globs`` is the same
    function for the hook; ``test_globs.py`` holds the two to the same answers.
    """
    # Why: the empty path (``.``) matches exactly the patterns whose regex matches the empty
    # string, as pathlib has it: ``**`` and ``***`` do, ``*`` and ``?`` do not.
    normal = _glob_normalised(path)
    return any(glob_regex(pattern).fullmatch(normal) for pattern in patterns)


def _glob_normalised(text: str) -> str:
    posix = PurePosixPath(text).as_posix()
    return "" if posix == "." else posix


def _glob_segments(pattern: str) -> list[str]:
    normal = _glob_normalised(pattern)
    return normal.split("/") if normal else []


@lru_cache(maxsize=4096)
def glob_regex(pattern: str) -> re.Pattern[str]:
    """The compiled regular expression for a glob pattern."""
    parts = _glob_segments(pattern)
    expression = ""
    separator_due = False
    for index, segment in enumerate(parts):
        last = index == len(parts) - 1
        if segment == "**":
            if last:
                # Why: as pathlib has it, `a/**` needs the slash, so `a` itself is not matched.
                expression += "/.*" if separator_due else ".*"
            else:
                expression += ("/" if separator_due else "") + "(?:[^/]+/)*"
            separator_due = False
            continue
        expression += ("/" if separator_due else "") + _glob_segment(segment)
        separator_due = True
    return re.compile(expression)


def _glob_segment(segment: str) -> str:
    if segment == "*":
        # Why: pathlib gives a lone `*` one non-empty segment, so `/*` does not match `/`.
        return "[^/]+"
    out = ""
    index = 0
    while index < len(segment):
        char = segment[index]
        if char == "*":
            while index + 1 < len(segment) and segment[index + 1] == "*":
                index += 1
            out += "[^/]*"
        elif char == "?":
            out += "[^/]"
        elif char == "[":
            body, index = _glob_class(segment, index)
            out += body
        else:
            out += re.escape(char)
        index += 1
    return out


def _glob_class(segment: str, start: int) -> tuple[str, int]:
    end = start + 1
    if end < len(segment) and segment[end] == "!":
        end += 1
    if end < len(segment) and segment[end] == "]":
        end += 1
    while end < len(segment) and segment[end] != "]":
        end += 1
    if end >= len(segment):
        return re.escape("["), start
    # Why: ranges, negation and escaping inside a class follow fnmatch, as pathlib's do.
    translated = fnmatch.translate(segment[start : end + 1])
    return translated[len("(?s:") : -len(")\\Z")], end


def report(name: str, problems: list[str], against: str | None = None) -> int:
    """Print each problem and the verdict line; the exit code."""
    for line in problems:
        print(line)
    where = f" against {against[:12]}" if against else ""
    print(f"{name}: {'red' if problems else 'green'}{where}")
    return 1 if problems else 0


class Interrupted(Exception):  # noqa: N818 # -- a signal, not a fault
    """A signal the gate was asked to stop on; ``signum`` says which."""

    def __init__(self, signum: int) -> None:
        """Remember the signal number."""
        super().__init__(f"interrupted by signal {signum}")
        self.signum = signum


class _Signals:
    """What the signal handler shares with ``signals_held``: whether it fired, is held or waits."""

    fired = False
    holding = False
    pending: int | None = None

    def reset(self, *, holding: bool) -> None:
        """Forget any signal kept; ``holding`` says whether the next ones are to wait."""
        self.holding, self.pending = holding, None


_SIGNALS = _Signals()


@contextlib.contextmanager
def interrupts_raise() -> Iterator[None]:
    """Turn SIGINT, SIGTERM and SIGHUP into ``Interrupted`` once, so ``finally`` blocks run.

    The first signal raises; later ones are ignored, so a second Ctrl-C cannot cut the cleanup
    short. The previous handlers come back on exit. SIGKILL cannot be caught by anything.
    """
    _SIGNALS.fired = False
    _SIGNALS.reset(holding=False)

    def handler(signum: int, frame: FrameType | None) -> None:
        del frame
        if _SIGNALS.fired:
            return
        if _SIGNALS.holding:
            _SIGNALS.pending = _SIGNALS.pending or signum
            return
        _SIGNALS.fired = True
        raise Interrupted(signum)

    names = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
    previous = [(name, signal.signal(name, handler)) for name in names]
    try:
        yield
    finally:
        for name, old in previous:
            signal.signal(name, old)


@contextlib.contextmanager
def signals_held() -> Iterator[None]:
    """Start a child and record it inside this block; a signal waits for the block to end.

    A handler that raises can run between the call that starts a child and the line that
    records it, and the child is then never stopped. A signal that lands inside the block is
    kept and raised as ``Interrupted`` when the block ends, by which time the child is recorded.
    Put the block inside the ``try`` that stops what was recorded. Outside ``interrupts_raise``
    it holds nothing.
    """
    _SIGNALS.reset(holding=True)
    try:
        yield
    finally:
        _SIGNALS.holding = False
        if _SIGNALS.pending is not None:
            _SIGNALS.fired = True
            raise Interrupted(_SIGNALS.pending)


def free_port() -> int:
    """A TCP port nothing listens on now, picked by the kernel on the loopback interface."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
    return port


def listening(url: str) -> bool:
    """Whether something accepts a TCP connection at the host and port of ``url``."""
    parts = urlsplit(url)
    default = 443 if parts.scheme == "https" else 80
    try:
        socket.create_connection((parts.hostname or "", parts.port or default), timeout=1).close()
    except OSError:
        return False
    return True


def stop_group(process: subprocess.Popen[bytes], grace: float = 5.0, settle: float = 1.0) -> None:
    """Stop a process started with ``start_new_session=True`` and everything it forked.

    SIGTERM goes to the whole process group. The leader gets ``grace`` seconds to exit, any
    other member ``settle`` more, then SIGKILL ends what is left. Returns with the leader reaped.
    """

    def signal_group(signum: int) -> bool:
        try:
            os.killpg(process.pid, signum)
        except ProcessLookupError:
            return False
        return True

    signal_group(signal.SIGTERM)
    with contextlib.suppress(subprocess.TimeoutExpired):
        process.wait(timeout=grace)
    deadline = time.monotonic() + settle
    while process.poll() is not None and signal_group(0) and time.monotonic() < deadline:
        time.sleep(0.02)
    signal_group(signal.SIGKILL)
    process.wait()


LEFT_RUNNING = (
    "something it started was still running when it exited, and was ended; a gate stops what it"
    " starts"
)


def end_group(pid: int) -> bool:
    """End what is left of the process group ``pid`` led; whether anything was left.

    For a process started with ``start_new_session=True`` that has exited and been reaped. What
    it forked and did not stop (a service a test booted) is still in its group, holds its ports
    and its output, and would otherwise run until somebody found it.
    """
    try:
        os.killpg(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        return False
    return True


def end_running_from(tree: Path, proc: Path = Path("/proc")) -> list[int]:
    """End every process whose program is a file under ``tree``; the ids of those ended.

    For a work tree about to be removed. A service an agent built there and started by hand is in
    no gate's process group, so nothing else ends it: it would hold its port and a deleted
    program until somebody found it. ``proc`` is where the kernel lists processes; a system
    without one ends nothing. A process of another user cannot be read and is left.
    """
    ended = []
    for entry in sorted(proc.glob("[0-9]*")):
        try:
            program = (entry / "exe").readlink()
            if not program.is_relative_to(tree):
                continue
            os.kill(int(entry.name), signal.SIGKILL)
        except OSError:
            continue
        ended.append(int(entry.name))
    return ended


def run_command(command: list[str], env: dict[str, str]) -> tuple[int, list[str]]:
    """Run ``command`` in a session of its own: its exit code, and what went wrong.

    A command ended by a signal exits 128 plus the signal, as a shell reports it.

    A signal that interrupts the start or the wait stops the command's whole process group
    before it propagates.
    """
    inner: subprocess.Popen[bytes] | None = None
    try:
        with signals_held():
            inner = subprocess.Popen(command, env=env, start_new_session=True)
        code = inner.wait()
    except BaseException as error:
        if inner is not None:
            stop_group(inner)
        elif isinstance(error, OSError):
            return 1, [f"cannot run {command[0]}: {error}"]
        raise
    code = 128 - code if code < 0 else code
    return code, ([] if code == 0 else [f"the command exited with code {code}"])


def http_status(method: str, url: str, *, body: bytes | None = None, timeout: float = 5.0) -> int:
    """The status of one request, redirects not followed; raises ``OSError`` or ``HTTPException``.

    ``http`` and ``https`` only. A body is sent as JSON.
    """
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        message = f"not an http(s) URL with a host: {url}"
        raise ValueError(message)
    connect = HTTPSConnection if parts.scheme == "https" else HTTPConnection
    connection = connect(parts.hostname, parts.port, timeout=timeout)
    target = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    headers = {"Content-Type": "application/json"} if body is not None else {}
    try:
        connection.request(method, target, body=body, headers=headers)
        response = connection.getresponse()
        response.read()
        return response.status
    finally:
        connection.close()
