"""What a scripted session may do: a short list of tools, and a shell that reaches no forge.

The sandbox (``sandbox.py``) is what makes a write outside the run, a read of the operator's files
and a forge login impossible. This policy is the layer that says no first, so that each refusal
is recorded and the session is told why. The driver puts every tool call through ``decide``
twice: in a PreToolUse hook, which sees each call, and in ``can_use_tool``, which sees each
prompt. A tool that is not in ``TOOLS`` is denied; nothing is approved by default.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping
    from pathlib import Path


class Kind(StrEnum):
    """What a tool touches, which decides how its input is judged."""

    READ = "read"
    WRITE = "write"
    SHELL = "shell"
    QUESTION = "question"
    SESSION = "session"
    MESSAGE = "message"


@dataclass(frozen=True)
class Tool:
    """How a tool on the list is judged: what it touches, and which input keys name a path."""

    kind: Kind
    paths: tuple[str, ...] = ()


TOOLS: Mapping[str, Tool] = {
    "Read": Tool(Kind.READ, ("file_path",)),
    "Glob": Tool(Kind.READ, ("path", "pattern")),
    "Grep": Tool(Kind.READ, ("path",)),
    "Workflow": Tool(Kind.READ, ("scriptPath",)),
    "Edit": Tool(Kind.WRITE, ("file_path",)),
    "Write": Tool(Kind.WRITE, ("file_path",)),
    "NotebookEdit": Tool(Kind.WRITE, ("notebook_path",)),
    "Bash": Tool(Kind.SHELL),
    "Monitor": Tool(Kind.SHELL),
    "AskUserQuestion": Tool(Kind.QUESTION),
    "Skill": Tool(Kind.SESSION),
    "Agent": Tool(Kind.SESSION),
    "Task": Tool(Kind.SESSION),
    "TodoWrite": Tool(Kind.SESSION),
    "TaskCreate": Tool(Kind.SESSION),
    "TaskUpdate": Tool(Kind.SESSION),
    "TaskList": Tool(Kind.SESSION),
    "TaskGet": Tool(Kind.SESSION),
    "TaskOutput": Tool(Kind.SESSION),
    "TaskStop": Tool(Kind.SESSION),
    "ToolSearch": Tool(Kind.SESSION),
    "SendMessage": Tool(Kind.MESSAGE),
}
# Why: named so the CLI refuses them before the model can call them; `decide` denies every tool
# not in TOOLS whatever this list says.
DENIED_TOOLS = (
    "WebFetch",
    "WebSearch",
    "RemoteTrigger",
    "PushNotification",
    "SendToProject",
    "SendToProjectContext",
    "CronCreate",
    "CronDelete",
    "CronList",
    "ScheduleWakeup",
    "DesignSync",
    "ListMcpResourcesTool",
    "ReadMcpResourceTool",
    "ReadMcpResourceDirTool",
    "ListAgents",
    "SendUserFile",
    "ShareOnboardingGuide",
    "SendFeedback",
    "Artifact",
    "ArtifactComments",
    "ArtifactData",
)
URL = re.compile(r"\b[a-z][\w+.-]*://", re.IGNORECASE)
SYSTEM = ("/usr", "/bin", "/sbin", "/lib", "/lib32", "/lib64", "/etc", "/opt", "/proc/self")
SINKS = ("/dev/null", "/dev/stdout", "/dev/stderr", "/dev/tty", "/dev/fd", "/dev/zero")
SECRET = re.compile(
    r"\.credentials\.json|CLAUDE_CODE_OAUTH_TOKEN|ANTHROPIC_API_KEY|/proc/\S*environ"
)

# Why: a program counts wherever it can start a command: after a separator, a `$(`, a quote, a
# path prefix or `env`/`xargs`. A word in prose ("use curl") is refused too, which is cheap.
_AT = r"(?:^|(?<=[\s;&|()`'\"/{]))"
# Why: `command -v glab` and `which gh` look a program up; they do not run it.
_RUN = _AT + r"(?<!-v )(?<!which )(?<!type )"
_END = r"(?=$|[\s;&|()`'\"}])"
GIT = rf"{_AT}git(?:\s+-[cC]\s+\S+|\s+--[\w-]+(?:=\S+)?)*\s+"
FORBIDDEN = (
    (rf"{GIT}push\b", "pushes to a remote"),
    (rf"{GIT}remote\s+(?:add|set-url)\b", "adds a remote"),
    (r"\bremote\.[\w.-]+\.(?:url|pushurl)\b", "adds a remote"),
    (rf"{GIT}(?:send-email|credential)\b", "reaches a remote"),
    (rf"{_RUN}(?:gh|glab)(?!\s+(?:auth\s+status|--version)\b){_END}", "calls a forge"),
    (rf"{_RUN}hub{_END}", "calls a forge"),
    (r"\b(?:github|gitlab|bitbucket)\.(?:com|org|io)\b", "names a forge"),
    (
        (
            rf"{_RUN}(?:curl|wget|ssh|scp|sftp|rsync|nc|ncat|netcat|socat|telnet|ftp|lftp|aria2c"
            rf"|cloudflared|ntfy){_END}"
        ),
        "opens a network connection",
    ),
    (
        (
            rf"{_RUN}(?:npm|pnpm|yarn|bun|uv|poetry|flit|hatch|cargo|gem|docker|podman)"
            r"(?:\s+\S+)*?\s+(?:publish|push|login|adduser|owner|token)\b"
        ),
        "publishes or logs in to a registry",
    ),
    (rf"{_RUN}twine{_END}", "publishes to a registry"),
    (
        rf"{_RUN}claude(?!\s+(?:plugins?\s+list|--version|-v)(?:$|[\s;&|)]))(?:{_END}|\s)",
        "starts or changes Claude Code",
    ),
    (r"--dangerously-skip-permissions", "skips permissions"),
    (
        (
            r"\b(?:python3?(?:\.\d+)?|node|deno|bun|perl|ruby)\s+(?:-\S*[ce]\b|-\s|<<)"
            r"[\s\S]*\b(?:urllib|requests|http\.client|httpx|aiohttp|socket|fetch\(|https?\.request"
            r"|net\.connect|Net::HTTP|open-uri|LWP)"
        ),
        "runs inline code that opens a network connection",
    ),
    (SECRET.pattern, "reads the session's model credential"),
)
_FORBIDDEN = tuple((re.compile(pattern), why) for pattern, why in FORBIDDEN)
# Why: inside the sandbox these are the run's own temp directory.
TEMP = ("/tmp", "/var/tmp")
PATHS = re.compile(
    r"(?:^|(?<=[\s=('\"<>`]))"
    r"(~[^\s'\";|&<>()`]*|\$\{?(?:HOME|TMPDIR)\}?[^\s'\";|&<>()`]*|/[^\s'\";|&<>()`]*"
    r"|\.\.(?:/[^\s'\";|&<>()`]*)?)"
)


@dataclass(frozen=True)
class Scope:
    """Where a session lives: its run, its project, its home and the trees it may write."""

    root: Path
    cwd: Path
    home: Path
    tmp: Path
    writable: tuple[Path, ...]
    readonly: tuple[Path, ...] = ()


@dataclass(frozen=True)
class Decision:
    """Whether a call may go ahead, and if not, why: the reason the session is told."""

    allowed: bool
    why: str = ""


ALLOW = Decision(allowed=True)


def _under(path: str, parent: str) -> bool:
    return path == parent or path.startswith(parent.rstrip("/") + "/")


def _resolve(raw: str, scope: Scope) -> str:
    """A path as the session's shell would see it, without following links."""
    text = raw.split("*", 1)[0] if "*" in raw else raw
    for name, value in (("${HOME}", scope.home), ("$HOME", scope.home), ("~", scope.home)):
        if text.startswith(name):
            text = str(value) + text[len(name) :]
    inside = _under(text, str(scope.root))
    aliases = ("${TMPDIR}", "$TMPDIR") if inside else ("${TMPDIR}", "$TMPDIR", *TEMP)
    for name in aliases:
        if text == name or text.startswith(name + "/"):
            text = str(scope.tmp) + text[len(name) :]
    joined = os.path.join(str(scope.cwd), text)  # noqa: PTH118
    return os.path.normpath(joined)


def _readable(path: str, scope: Scope) -> bool:
    return _under(path, str(scope.root)) or any(_under(path, p) for p in (*SYSTEM, *SINKS))


def _writable(path: str, scope: Scope) -> bool:
    if any(_under(path, str(p)) for p in scope.readonly):
        return False
    return any(_under(path, str(p)) for p in scope.writable) or any(_under(path, p) for p in SINKS)


def shell(command: str, scope: Scope) -> Decision:
    """Whether a shell command line may run; it is judged as a whole, chained or not.

    A path outside the run directory and the system's read-only trees is refused whatever the
    command does with it. A write into a system tree, or into a read-only part of the run, is
    left to the sandbox, which mounts them read-only: telling a write from a read in a shell
    string is the arms race this layer does not run.
    """
    for pattern, why in _FORBIDDEN:
        if pattern.search(command):
            return Decision(allowed=False, why=why)
    for raw in PATHS.findall(command):
        if not _readable(_resolve(raw, scope), scope):
            return Decision(allowed=False, why=f"reaches outside the run directory ({raw})")
    return ALLOW


def decide(
    tool: str, tool_input: Mapping[str, object], scope: Scope, *, agents: Collection[str] = ()
) -> Decision:
    """Whether one tool call may go ahead.

    ``agents`` are the ids of the subagents and tasks this session started: the only ones a
    message may go to.
    """
    known = TOOLS.get(tool)
    if known is None:
        return Decision(allowed=False, why=f"`{tool}` is not on the first-hour run's tool list")
    if known.kind is Kind.SHELL:
        return _command(tool_input, scope)
    if known.kind is Kind.MESSAGE:
        to_ours = str(tool_input.get("to", "")) in agents
        elsewhere = "messages go only to a subagent this session started"
        return ALLOW if to_ours else Decision(allowed=False, why=elsewhere)
    for key in known.paths:
        raw = tool_input.get(key)
        refused = _refusal(raw, known.kind, scope) if isinstance(raw, str) and raw else None
        if refused is not None:
            return Decision(allowed=False, why=refused)
    return ALLOW


def _command(tool_input: Mapping[str, object], scope: Scope) -> Decision:
    """A shell tool's call: its command line, and no URL to watch."""
    # Why: Monitor can watch a URL as well as a command; only the command is a shell line.
    if any(k != "command" and isinstance(v, str) and URL.search(v) for k, v in tool_input.items()):
        return Decision(allowed=False, why="opens a network connection")
    return shell(str(tool_input.get("command", "")), scope)


def _refusal(raw: str, kind: Kind, scope: Scope) -> str | None:
    """Why a path a file tool names is refused, or ``None``."""
    if SECRET.search(raw):
        return "reads the session's model credential"
    path = _resolve(raw, scope)
    if kind is Kind.WRITE and not _writable(path, scope):
        return f"writes outside the owner's trees ({raw})"
    if not _readable(path, scope):
        return f"reaches outside the run directory ({raw})"
    return None
