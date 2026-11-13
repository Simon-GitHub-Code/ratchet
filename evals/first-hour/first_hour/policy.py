"""What a scripted session may do: a short list of tools, and a shell that reaches no forge.

The driver puts every tool call through ``decide`` twice: in a PreToolUse hook, which sees each
call, and in ``can_use_tool``, which sees each prompt. A tool that is not in ``TOOLS`` is denied;
nothing is approved by default.

For a shell command this is the layer that says no first, so that each refusal is recorded and
the session is told why; the sandbox (``sandbox.py``) is what makes a write outside the run, a
read of the operator's files and a forge login impossible when a command gets past it. The file
tools run in the Claude Code process, outside the sandbox, so for them this is the only layer:
their paths are judged as that process sees them, links followed.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

from first_hour import registry, shellpaths

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping


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
    # Why: a workflow agent or subagent given a `schema` returns its result through this tool.
    # Without it the `/ratchet:feature` pipeline cannot finish its first step.
    "StructuredOutput": Tool(Kind.SESSION),
}
# Why: named so the CLI refuses them before the model can call them; `decide` denies every tool
# not in TOOLS whatever this list says.
DENIED_TOOLS = (
    "Monitor",
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
            rf"{_RUN}(?:wget|ssh|scp|sftp|rsync|nc|ncat|netcat|socat|telnet|ftp|lftp|aria2c"
            rf"|cloudflared|ntfy){_END}"
        ),
        "opens a network connection",
    ),
    (rf"{_RUN}twine{_END}", "publishes to a registry"),
    (
        rf"{_RUN}claude(?!\s+(?:plugins?\s+list|--version|-v)(?:$|[\s;&|)]))(?:{_END}|\s)",
        "starts or changes Claude Code",
    ),
    (r"--dangerously-skip-permissions", "skips permissions"),
    (SECRET.pattern, "reads the session's model credential"),
)
_FORBIDDEN = tuple((re.compile(pattern), why) for pattern, why in FORBIDDEN)

# Why: judged on the commands the line runs (`shellpaths.scan`), not on its text, so a word in a
# heredoc that writes a file or in a string literal of a script is not a command. A line that
# cannot be read that way falls back to the text.
_CURL = re.compile(rf"{_RUN}curl{_END}")
PUBLISHERS = frozenset(
    {
        "npm",
        "pnpm",
        "yarn",
        "bun",
        "uv",
        "poetry",
        "flit",
        "hatch",
        "cargo",
        "gem",
        "docker",
        "podman",
    }
)
PUBLISH_VERBS = frozenset({"publish", "push", "login", "adduser", "owner", "token"})
_PUBLISH_TEXT = re.compile(
    rf"{_RUN}(?:{'|'.join(sorted(PUBLISHERS))})(?:[ \t]+\S+)*?[ \t]+"
    rf"(?:{'|'.join(sorted(PUBLISH_VERBS))})\b"
)
# Why: the network modules of the languages a session writes inline. Module names are looked for
# outside string literals, so a script that edits a file about `urllib` is not one that uses it.
_NETWORK_CODE = re.compile(
    r"\b(?:urllib|requests|http\.client|httpx|aiohttp|socket|fetch\(|https?\.request"
    r"|net\.connect|Net::HTTP|open-uri|LWP)"
)
_DYNAMIC_IMPORT = re.compile(
    r"(?:__import__|import_module|require)\s*\(\s*['\"](?:node:)?"
    r"(?:socket|urllib\S*|http\S*|requests|httpx|aiohttp|net|tls)['\"]"
)
_STRING = re.compile(
    r"\'\'\'[\s\S]*?\'\'\'|\"\"\"[\s\S]*?\"\"\"|\"(?:\\.|[^\"\\\n])*\"|'(?:\\.|[^'\\\n])*'|`[^`]*`"
)
_INLINE_TEXT = re.compile(
    r"\b(?:python3?(?:\.\d+)?|node|deno|bun|perl|ruby)\s+(?:-\S*[ce]\b|-\s|<<)[\s\S]*"
    + _NETWORK_CODE.pattern
)
# Why: inside the sandbox these are the run's own temp directory; outside it they are not.
TEMP = ("/tmp", "/var/tmp")


@dataclass(frozen=True)
class Scope:
    """Where a session lives: its run, its project, its home and the trees it may write."""

    root: Path
    cwd: Path
    home: Path
    tmp: Path
    writable: tuple[Path, ...]
    readonly: tuple[Path, ...] = ()


# Why: a refusal that names only what is refused leaves a model to guess the rest, and it guessed
# that the whole network was gone. Every refusal says what still works.
STILL_WORKS = (
    "Only this call is refused. Package installs and reads of public package-registry metadata "
    "still work (for example `curl -s https://pypi.org/pypi/<name>/json`, `curl -s "
    "https://registry.npmjs.org/<name>`), and so do the project's files, tests and gates."
)


@dataclass(frozen=True)
class Decision:
    """Whether a call may go ahead, and if not, why: ``why`` is a verb phrase the log keeps."""

    allowed: bool
    why: str = ""

    @property
    def told(self) -> str:
        """What the session is told when the call is refused: what is refused, and what works."""
        return f"This call is refused: it {self.why}. {STILL_WORKS}"


ALLOW = Decision(allowed=True)


def _under(path: str, parent: str) -> bool:
    return path == parent or path.startswith(parent.rstrip("/") + "/")


def _resolve(raw: str, scope: Scope, *, sandboxed: bool) -> str:
    """A path as the process that uses it sees it.

    A sandboxed shell sees the run's temp at ``/tmp``; a file tool in the Claude Code process
    sees the real ``/tmp``, and follows links, so its path is the link's target.
    """
    text = raw.split("*", 1)[0] if "*" in raw else raw
    for name, value in (("${HOME}", scope.home), ("$HOME", scope.home), ("~", scope.home)):
        if text.startswith(name):
            text = str(value) + text[len(name) :]
    inside = _under(text, str(scope.root))
    aliases = ("${TMPDIR}", "$TMPDIR", *(TEMP if sandboxed and not inside else ()))
    for name in aliases:
        if text == name or text.startswith(name + "/"):
            text = str(scope.tmp) + text[len(name) :]
    joined = os.path.normpath(os.path.join(str(scope.cwd), text))  # noqa: PTH118
    return joined if sandboxed else os.path.realpath(joined)


def _readable(path: str, scope: Scope) -> bool:
    return _under(path, str(scope.root)) or any(_under(path, p) for p in (*SYSTEM, *SINKS))


def _writable(path: str, scope: Scope) -> bool:
    if any(_under(path, str(p)) for p in scope.readonly):
        return False
    return any(_under(path, str(p)) for p in scope.writable) or any(_under(path, p) for p in SINKS)


def shell(command: str, scope: Scope) -> Decision:
    """Whether a shell command line may run; it is judged as a whole, chained or not.

    A path outside the run directory and the system's read-only trees is refused whatever the
    command does with it. What counts as a path is ``shellpaths.words``: a word that starts like
    one, not the text a ``sed`` script, a message or a heredoc body holds.

    A write into a system tree, or into a read-only part of the run, is left to the sandbox,
    which mounts them read-only: telling a write from a read in a shell string is the arms race
    this layer does not run.
    """
    for pattern, why in _FORBIDDEN:
        if pattern.search(command):
            return Decision(allowed=False, why=why)
    refused = _refused_by_what_runs(command)
    if refused is not None:
        return Decision(allowed=False, why=refused)
    here = scope
    for word in shellpaths.words(command):
        path = _resolve(word.raw, here, sandboxed=True)
        if not _readable(path, scope):
            return Decision(allowed=False, why=f"reaches outside the run directory ({word.raw})")
        if word.cd:
            here = replace(scope, cwd=Path(path))
    return ALLOW


def _refused_by_what_runs(command: str) -> str | None:
    """Why a line is refused for the commands it runs, or ``None``.

    ``curl`` is allowed to read a public registry and nothing else (``registry.is_registry_read``);
    a registry tool may not publish or sign in; inline code may not open a connection.
    """
    scanned = shellpaths.scan(command)
    mentions = len(_CURL.findall(command))
    if scanned is None:
        return _refused_as_text(command, mentions=mentions)
    curls = [call for call in scanned.calls if call.name == "curl"]
    if mentions != len(curls) or not all(registry.is_registry_read(c.args) for c in curls):
        return "opens a network connection"
    if any(c.name in PUBLISHERS and PUBLISH_VERBS & set(c.args) for c in scanned.calls):
        return "publishes or logs in to a registry"
    if any(_opens_connection(code) for code in scanned.code):
        return "runs inline code that opens a network connection"
    return None


def _refused_as_text(command: str, *, mentions: int) -> str | None:
    """The same, for a line a shell would not read: any mention counts."""
    if mentions:
        return "opens a network connection"
    if _PUBLISH_TEXT.search(command):
        return "publishes or logs in to a registry"
    if _INLINE_TEXT.search(command):
        return "runs inline code that opens a network connection"
    return None


def _opens_connection(code: str) -> bool:
    return bool(_DYNAMIC_IMPORT.search(code) or _NETWORK_CODE.search(_STRING.sub('""', code)))


def decide(
    tool: str, tool_input: Mapping[str, object], scope: Scope, *, agents: Collection[str] = ()
) -> Decision:
    """Whether one tool call may go ahead.

    ``agents`` are the ids of the subagents and tasks this session started: the only ones a
    message may go to.
    """
    known = TOOLS.get(tool)
    if known is None:
        return Decision(
            allowed=False, why=f"uses `{tool}`, which is not on the first-hour run's tool list"
        )
    if known.kind is Kind.SHELL:
        return shell(str(tool_input.get("command", "")), scope)
    if known.kind is Kind.MESSAGE:
        to_ours = str(tool_input.get("to", "")) in agents
        elsewhere = "sends a message to something other than a subagent this session started"
        return ALLOW if to_ours else Decision(allowed=False, why=elsewhere)
    for key in known.paths:
        raw = tool_input.get(key)
        refused = _refusal(raw, known.kind, scope) if isinstance(raw, str) and raw else None
        if refused is not None:
            return Decision(allowed=False, why=refused)
    return ALLOW


def _refusal(raw: str, kind: Kind, scope: Scope) -> str | None:
    """Why a path a file tool names is refused, or ``None``."""
    if SECRET.search(raw):
        return "reads the session's model credential"
    path = _resolve(raw, scope, sandboxed=False)
    if kind is Kind.WRITE and not _writable(path, scope):
        return f"writes outside the owner's trees ({raw})"
    if not _readable(path, scope):
        return f"reaches outside the run directory ({raw})"
    return None
