from __future__ import annotations

import contextlib
import contextvars
import errno
import fnmatch
import ipaddress
import itertools
import json
import os
import re
import secrets
import selectors
import shutil
import signal
import socket
import subprocess
import sys
import time
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from http.client import HTTPConnection, HTTPSConnection
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, NamedTuple, cast
from urllib.parse import urlsplit

import unproven

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator
    from types import FrameType

FALLBACK_BRANCHES = ("main", "master")
BINDING = Path("docs/process/binding.md")
READY_ENV = "RATCHET_READY"
STOPPING_ENV = "RATCHET_STOPPING"
SENDING_ENV = "RATCHET_SENDING"
ANSWER_POLL = 0.002
PORT_RANGE = Path("/proc/sys/net/ipv4/ip_local_port_range")
DYNAMIC_PORTS = 49152
UNPRIVILEGED = 1024
THRESHOLDS = Path("quality/thresholds.toml")
STATE = Path(".ratchet")
DEFAULT_BRANCH_LINE = re.compile(r"^- Default branch: `([^`]+)`$", re.MULTILINE)

REPOSITORY_VARIABLES = frozenset(
    {
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_COMMON_DIR",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_PREFIX",
    }
)

Want = Callable[[str | None], str]


def interpreter(*, inline: bool = False) -> list[str]:
    argv = [sys.executable]
    if sys.flags.ignore_environment:
        argv.append("-E")
    if sys.flags.no_user_site:
        argv.append("-s")
    if sys.flags.dont_write_bytecode:
        argv.append("-B")
    if sys.pycache_prefix is not None:
        argv.extend(["-X", f"pycache_prefix={sys.pycache_prefix}"])
    if inline:
        argv.append("-P")
    return argv


def git(root: Path, *args: str, errors: str | None = None) -> str | None:
    result = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, errors=errors, check=False
    )
    return result.stdout.strip() if result.returncode == 0 else None


def chain(root: Path, tip: str, commits: Iterable[str] = ()) -> tuple[unproven.Landed, ...]:
    listed = git(root, "log", "--format=%H %P%x09%s", tip, "--", errors="replace") or ""
    parents: dict[str, list[str]] = {}
    subjects: dict[str, str] = {}
    for row in listed.splitlines():
        shas, _, subject = row.partition("\t")
        sha, *above = shas.split()
        parents[sha], subjects[sha] = above, subject
    line: list[str] = []
    step = next(iter(subjects), None)
    while step is not None:
        line.append(step)
        step = next(iter(parents[step]), None)
    landing: dict[str, int] = {}
    for index in reversed(range(len(line))):
        reached = [line[index]]
        while reached:
            one = reached.pop()
            if one not in landing:
                landing[one] = index
                reached.extend(parents[one])
    merged: dict[int, set[str]] = {}
    for one in set(commits).intersection(landing).difference(line):
        merged.setdefault(landing[one], set()).add(one)
    return tuple(
        unproven.Landed(sha, subjects[sha], frozenset(merged.get(index, ())))
        for index, sha in enumerate(line)
    )


def default_branches(root: Path) -> list[str]:
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


def stacked(target: str, default: str) -> str:
    return (
        f"the pull request targets `{target}`, not the default branch `{default}`. Merge the pull"
        f" request from `{target}` first, and before it merges change this one's target to"
        f" `{default}` on the pull request's page on the forge, which keeps it open; the owner then"
        f" merges this one. Do not delete `{target}` before this one is retargeted: the merge job"
        " deletes the branch of each pull request it merges, and a pull request whose target"
        " branch is gone is closed and cannot be reopened or retargeted"
    )


def base_commit(root: Path, base: str | None = None) -> str | None:
    head = git(root, "rev-parse", "--abbrev-ref", "HEAD")
    for branch in [base] if base else default_branches(root):
        merge_base = git(root, "merge-base", "HEAD", branch)
        if merge_base is None:
            continue
        if head in (branch, branch.removeprefix("origin/")):
            return git(root, "rev-parse", "HEAD~1")
        return merge_base
    return git(root, "rev-parse", "HEAD~1")


def adoption(root: Path, base: str | None) -> bool:
    return base is not None and show(root, base, str(BINDING)) is None


def changed_paths(root: Path, base: str) -> list[str]:
    tracked = git(root, "diff", "--name-only", "--no-renames", "-z", base) or ""
    untracked = git(root, "ls-files", "--others", "--exclude-standard", "--full-name", "-z", ":/")
    return sorted({p for p in (tracked + (untracked or "")).split("\0") if p})


def renamed(root: Path, base: str) -> dict[str, str]:
    listed = git(root, "diff", "--find-renames", "--name-status", "-z", base) or ""
    fields = listed.split("\0")
    moves: dict[str, str] = {}
    at = 0
    while at < len(fields) and fields[at]:
        status = fields[at]
        if status.startswith("R") and at + 2 < len(fields):
            moves[fields[at + 1]] = fields[at + 2]
            at += 3
        else:
            at += 3 if status.startswith("C") else 2
    return moves


listings: contextvars.ContextVar[dict[Path, list[str]] | None] = contextvars.ContextVar(
    "listings", default=None
)


def tracked_paths(root: Path, ref: str | None = None) -> list[str]:
    if ref is None and (kept := listings.get()) is not None:
        if root not in kept:
            kept[root] = listed_paths(root)
        return list(kept[root])
    return listed_paths(root, ref)


def listed_paths(root: Path, ref: str | None = None) -> list[str]:
    if ref is not None:
        listed = git(root, "ls-tree", "-r", "--name-only", "-z", ref) or ""
        return sorted(p for p in listed.split("\0") if p)
    listed = git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard") or ""
    return sorted({p for p in listed.split("\0") if p and (root / p).is_file()})


def show(root: Path, ref: str, path: str) -> str | None:
    return git(root, "show", f"{ref}:{path}")


def default_jobs(root: Path | None = None) -> int:
    for value in (os.environ.get("RATCHET_JOBS", ""), configured_jobs(root or Path.cwd())):
        if value.isdigit() and int(value) > 0:
            return int(value)
    return os.cpu_count() or 1


def configured_jobs(root: Path) -> str:
    try:
        return git(root, "config", "--get", "ratchet.jobs") or ""
    except OSError:
        return ""


def entry_point(root: Path, name: str) -> str | None:
    text = read_text(root / BINDING) or ""
    row = re.search(rf"^\|\s*{re.escape(name)}\s*\|\s*`([^`]+)`\s*\|", text, re.MULTILINE)
    return row.group(1) if row else None


def state_dir(root: Path) -> Path:
    directory = root / STATE
    directory.mkdir(parents=True, exist_ok=True)
    ignore = directory / ".gitignore"
    if not ignore.is_file():
        ignore.write_text("*\n", encoding="utf-8")
    return directory


def subjects(root: Path, base: str) -> list[str]:
    listed = git(root, "log", "--no-merges", "--format=%s", f"{base}..HEAD")
    return [line for line in (listed or "").splitlines() if line]


def read_text(path: Path) -> str | None:
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if b"\0" in data[:8000]:
        return None
    return data.decode("utf-8", errors="replace")


def edited(want: Want, data: bytes | None) -> bytes | None:
    text = None if data is None else data.decode("utf-8")
    crlf = text is not None and "\r\n" in text and text.count("\n") == text.count("\r\n")
    plain = text.replace("\r\n", "\n") if text is not None and crlf else text
    made = want(plain)
    if plain is not None and made == plain:
        return None
    return (made.replace("\n", "\r\n") if crlf else made).encode("utf-8")


def seen(root: Path) -> list[str]:
    listed = git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    if listed is not None:
        return sorted({name for name in listed.split("\0") if name})
    found = []
    for directory, folders, names in os.walk(root):
        here = Path(directory)
        links = [folder for folder in folders if (here / folder).is_symlink()]
        found += [str((here / name).relative_to(root)) for name in [*names, *links]]
    return sorted(found)


def copy_seen(root: Path, target: Path) -> dict[str, str]:
    home = root.resolve()
    foreign = {}
    for name in seen(root):
        source, copy = root / name, target / name
        lands, link = Path(os.path.realpath(source)), source.is_symlink()
        copy.parent.mkdir(parents=True, exist_ok=True)
        if link and lands.is_relative_to(home):
            copy.symlink_to(os.path.relpath(target / lands.relative_to(home), copy.parent))
            continue
        if lands.is_file():
            shutil.copy2(lands, copy)
        elif lands.is_dir():
            shutil.copytree(lands, copy, symlinks=True, ignore=shutil.ignore_patterns(".git"))
        if link:
            foreign[name] = "leads outside the repository"
        elif lands.is_dir():
            foreign[name] = f"is in the submodule {name}"
    share_history(root, target, "HEAD")
    git_checked(target, "add", "--all", "--force")
    return foreign


def share_history(root: Path, target: Path, head: str) -> None:
    common = git_checked(
        root, "rev-parse", "--path-format=absolute", "--git-common-dir", ok=(0, 128)
    ).strip()
    if not common:
        git_checked(target, "init", "-q")
        return
    held = Path(common)
    formats = git_checked(root, "rev-parse", "--show-object-format").strip()
    reftable = git_checked(root, "rev-parse", "--show-ref-format").strip() == "reftable"
    storage = {"GIT_DEFAULT_REF_FORMAT": "reftable" if reftable else "files"}
    git_bytes(target, "init", "-q", f"--object-format={formats}", variables=storage)
    mine = target / ".git"
    (mine / "objects/info/alternates").write_text(f"{held / 'objects'}\n", encoding="utf-8")
    for name in ("shallow", "info/exclude", "logs/HEAD"):
        if (held / name).is_file():
            (mine / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(held / name, mine / name)
    listed = git_bytes(root, "for-each-ref", "--format=%(objectname) %(refname) %(symref)")
    refs = [line.split(b" ") for line in listed.splitlines()]
    plain = [(oid, ref) for oid, ref, symref in refs if not symref]
    if reftable:
        git_bytes(
            target,
            "update-ref",
            "--stdin",
            stdin=b"".join(b"update %s %s\n" % (ref, oid) for oid, ref in plain),
        )
    else:
        (mine / "packed-refs").write_bytes(b"".join(b"%s %s\n" % pair for pair in plain))
    for _, ref, symref in refs:
        if symref:
            git_checked(target, "symbolic-ref", os.fsdecode(ref), os.fsdecode(symref))
    branch = git_checked(root, "symbolic-ref", "-q", head, ok=(0, 1, 128)).strip()
    if branch:
        git_checked(target, "symbolic-ref", "HEAD", branch)
        return
    commit = git_checked(root, "rev-parse", "--verify", "-q", f"{head}^{{commit}}", ok=(0, 1))
    if commit:
        git_checked(target, "update-ref", "--no-deref", "HEAD", commit.strip())


def export(root: Path, ref: str, target: Path) -> None:
    listed = git_checked(root, "ls-tree", "-r", "-z", "--full-tree", ref).split("\0")
    entries = [(line.split("\t", 1)[0].split(), line.split("\t", 1)[1]) for line in listed if line]
    blobs = [(mode, sha, name) for (mode, _, sha), name in entries if mode != "160000"]
    batch = git_bytes(
        root, "cat-file", "--batch", stdin="".join(f"{sha}\n" for _, sha, _ in blobs).encode()
    )
    at = 0
    for mode, _, name in blobs:
        header_end = batch.index(b"\n", at)
        size = int(batch[at:header_end].split()[2])
        data = batch[header_end + 1 : header_end + 1 + size]
        at = header_end + 2 + size
        path = target / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if mode == "120000":
            path.symlink_to(data.decode())
            continue
        path.write_bytes(data)
        path.chmod(0o755 if mode == "100755" else 0o644)
    target.mkdir(parents=True, exist_ok=True)
    share_history(root, target, ref)
    git_checked(target, "add", "--all", "--force")


def git_bytes(
    root: Path, *args: str, stdin: bytes = b"", variables: dict[str, str] | None = None
) -> bytes:
    environment = {k: v for k, v in os.environ.items() if k not in REPOSITORY_VARIABLES}
    return subprocess.run(
        ["git", "-C", str(root), *args],
        input=stdin,
        capture_output=True,
        check=True,
        env={**environment, **(variables or {})},
    ).stdout


def git_checked(root: Path, *args: str, ok: tuple[int, ...] = (0,)) -> str:
    environment = {k: v for k, v in os.environ.items() if k not in REPOSITORY_VARIABLES}
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=False,
        env=environment,
    )
    if result.returncode not in ok:
        raise subprocess.CalledProcessError(result.returncode, result.args, result.stderr)
    return result.stdout


def chained(*wants: Want) -> Want:
    def want(current: str | None) -> str:
        text = current
        for step in wants:
            text = step(text)
        return text or ""

    return want


def created(text: str) -> Want:
    def want(current: str | None) -> str:
        return text if current is None else current

    return want


def thresholds(root: Path) -> dict[str, object]:
    path = root / THRESHOLDS
    return tomllib.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def table(data: dict[str, object], *keys: str) -> dict[str, object]:
    current: object = data
    for key in keys:
        current = current.get(key, {}) if isinstance(current, dict) else {}
    return current if isinstance(current, dict) else {}


def matches(path: str, patterns: list[str]) -> bool:
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
    parts = _glob_segments(pattern)
    expression = ""
    separator_due = False
    for index, segment in enumerate(parts):
        last = index == len(parts) - 1
        if segment == "**":
            if last:
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
    translated = fnmatch.translate(segment[start : end + 1])
    return translated[len("(?s:") : -len(")\\Z")], end


def report(name: str, problems: list[str], against: str | None = None) -> int:
    for line in problems:
        print(line)
    where = f" against {against[:12]}" if against else ""
    print(f"{name}: {'red' if problems else 'green'}{where}")
    return 1 if problems else 0


class Interrupted(Exception):  # noqa: N818 # -- a signal, not a fault
    def __init__(self, signum: int) -> None:
        super().__init__(f"interrupted by signal {signum}")
        self.signum = signum


class _Signals:
    fired = False
    holding = False
    pending: int | None = None

    def reset(self, *, holding: bool) -> None:
        self.holding, self.pending = holding, None


_SIGNALS = _Signals()
INTERRUPTS = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)


@contextlib.contextmanager
def interrupts_raise() -> Iterator[None]:
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

    previous = [(name, signal.signal(name, handler)) for name in INTERRUPTS]
    try:
        yield
    finally:
        for name, old in previous:
            signal.signal(name, old)


@contextlib.contextmanager
def signals_held() -> Iterator[None]:
    _SIGNALS.reset(holding=True)
    try:
        yield
    finally:
        _SIGNALS.holding = False
        if _SIGNALS.pending is not None:
            _SIGNALS.fired = True
            raise Interrupted(_SIGNALS.pending)


def free_port() -> int:
    try:
        ephemeral = int(PORT_RANGE.read_text(encoding="utf-8").split()[0])
    except (OSError, ValueError, IndexError):
        ephemeral = DYNAMIC_PORTS
    below = max(0, ephemeral - UNPRIVILEGED)
    start = UNPRIVILEGED + secrets.randbelow(below) if below else UNPRIVILEGED
    for port in itertools.chain(range(start, ephemeral), range(UNPRIVILEGED, start)):
        with socket.socket() as probe, contextlib.suppress(OSError):
            probe.bind(("127.0.0.1", port))
            return port
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        picked: int = probe.getsockname()[1]
    return picked


def listening(url: str) -> bool:
    parts = urlsplit(url)
    default = 443 if parts.scheme == "https" else 80
    try:
        socket.create_connection((parts.hostname or "", parts.port or default), timeout=1).close()
    except OSError:
        return False
    return True


def stop_group(process: subprocess.Popen[bytes], grace: float = 5.0, settle: float = 1.0) -> None:
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
    try:
        os.killpg(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        return False
    return True


def end_running_from(tree: Path, proc: Path = Path("/proc")) -> list[int]:
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


class Connection(NamedTuple):
    host: str
    port: int
    client: str
    client_port: int


def connection(service: tuple[object, ...], client: tuple[object, ...]) -> Connection:
    return Connection(*end(service), *end(client))


def end(address: tuple[object, ...]) -> tuple[str, int]:
    host, port = address[:2]
    ip = ipaddress.ip_address(str(host))
    mapped = ip.ipv4_mapped if isinstance(ip, ipaddress.IPv6Address) else None
    return str(mapped or ip), cast("int", port)


@dataclass(frozen=True)
class Stopping:
    ports: dict[int, str | None]
    elsewhere: str
    taken: frozenset[Connection]
    answered: bool = False

    def inexact(self, port: int) -> str | None:
        return self.ports.get(port, self.elsewhere)


def write_stopping(path: Path, stopping: Stopping) -> None:
    data = {
        "ports": stopping.ports,
        "elsewhere": stopping.elsewhere,
        "taken": sorted(stopping.taken),
        "answered": stopping.answered,
    }
    pending = path.with_name(f"{path.name}.pending")
    pending.write_text(json.dumps(data), encoding="utf-8")
    pending.replace(path)


def read_stopping(path: Path) -> Stopping:
    data = json.loads(path.read_text(encoding="utf-8"))
    return Stopping(
        {int(port): why for port, why in data["ports"].items()},
        data["elsewhere"],
        frozenset(Connection(*taken) for taken in data["taken"]),
        data["answered"],
    )


def asking(path: Path) -> Path:
    return path.with_name(f"{path.name}.asked")


def ask(path: Path, within: float) -> Stopping:
    asking(path).touch()
    deadline = time.monotonic() + within
    while not (told := read_stopping(path)).answered:
        if time.monotonic() >= deadline:
            return Stopping({}, f"the gate did not answer within {within:g}s", told.taken)
        time.sleep(ANSWER_POLL)
    return told


def reach(
    sock: socket.socket,
    address: tuple[object, ...],
    *,
    timeout: float | None,
    connecting: Callable[[Connection], None] | None,
) -> None:
    sock.settimeout(0)
    code = sock.connect_ex(address)
    if connecting:
        connecting(connection(address, sock.getsockname()))
    if code == errno.EINPROGRESS:
        with selectors.DefaultSelector() as waiting:
            waiting.register(sock, selectors.EVENT_WRITE)
            if not waiting.select(timeout):
                message = "timed out"
                raise TimeoutError(message)
        code = sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
    if code:
        raise OSError(code, os.strerror(code))
    sock.settimeout(timeout)


class Told(HTTPConnection):
    connecting: Callable[[Connection], None] | None = None

    def connect(self) -> None:
        failed: OSError = socket.gaierror(f"{self.host} has no address")
        for family, kind, proto, _, address in socket.getaddrinfo(
            self.host, self.port, type=socket.SOCK_STREAM
        ):
            sock = socket.socket(family, kind, proto)
            try:
                reach(sock, address, timeout=self.timeout, connecting=self.connecting)
            except OSError as error:
                sock.close()
                failed = error
            else:
                self.sock = sock
                return
        raise failed


class ToldTLS(HTTPSConnection, Told): ...


def http_status(
    method: str,
    url: str,
    *,
    body: bytes | None = None,
    timeout: float = 5.0,
    connecting: Callable[[Connection], None] | None = None,
) -> int:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        message = f"not an http(s) URL with a host: {url}"
        raise ValueError(message)
    connect = ToldTLS if parts.scheme == "https" else Told
    connection = connect(parts.hostname, parts.port, timeout=timeout)
    connection.connecting = connecting
    target = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    headers = {"Content-Type": "application/json"} if body is not None else {}
    try:
        connection.request(method, target, body=body, headers=headers)
        response = connection.getresponse()
        response.read()
        return response.status
    finally:
        connection.close()
