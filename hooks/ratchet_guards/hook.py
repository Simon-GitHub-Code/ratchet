"""The Claude Code PreToolUse hook: JSON in on stdin, a decision out on stdout.

``hooks/guard.py`` puts ``hooks/`` on ``sys.path`` and calls ``main`` with the repository
root. For Edit, Write, MultiEdit and NotebookEdit on a guard path, and for a Codex ``apply_patch``
whose headers name one, the hook answers ``ask``. Grok's own tool names
(``run_terminal_command``, ``search_replace``, ``write``, ...) are judged as the Claude tools
they stand for. Antigravity's input (``toolCall`` with ``name`` and ``args``) is translated to
Claude's shape first, and the answer goes back in Antigravity's (``decision`` and ``reason``).
It answers ``ask``,
which needs a human at the permission prompt, or ``deny`` in a group whose ``edit`` is
``"deny"`` (paths Claude Code's bypass mode would let through an ``ask``); for a Bash
command that bypasses a gate, rewrites history, removes a plugin or writes a guard path it
answers ``deny``. When no rule fires it prints nothing, so Claude Code's own permission rules
decide; it never answers ``allow``. Any failure answers ``ask``.

A verdict covers the whole tool call: Claude Code runs none of a denied Bash command line,
so a write chained before the refused command is lost with it. The deny message says so and
names the matched command, and the method asks agents to keep a gated command in a tool
call of its own.

An Edit, Write or MultiEdit that gives a local ticket under ``.scratch/`` the agent-ready status
asks, as applying that label on a forge does (``ratchet_guards.tickets``).

One forge write is allowed by a record: ``gh label create <name>`` in a checkout whose
``docs/agents/triage-labels.md`` lists ``<name>`` in its "Label in our tracker" column (see
``ratchet_guards.gh``). The file is a guard path in the decisions group once the repository
is adopted, so the list is the owner's.

The guard paths come from ``guards/manifest.toml`` as committed on the default branch of the
nearest checkout holding the root (an ancestor with ``.git``, else the root itself), read from
git (``ratchet_guards.committed``). An uncommitted manifest, or one committed only on a
branch, guards nothing yet: that is the adoption in progress, and only the command rules
apply until it merges. The plugin's own files are locked either way: ``PLUGIN_ROOT`` (two
parents above this package) and ``~/.claude/plugins/**``, together with
``~/.claude/settings.json`` and ``~/.claude/settings.local.json``, and the same switches of
the other harnesses (``ratchet_guards.manifest.PLUGIN_HOME_PATTERNS``).
"""

import json
import os
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TextIO

from ratchet_guards import bash, tickets
from ratchet_guards.committed import committed_manifest
from ratchet_guards.context import Context, Guard
from ratchet_guards.manifest import EMPTY, Manifest, parse, with_plugin_group
from ratchet_guards.paths import Hit, Locator
from ratchet_guards.verdict import Decision, Verdict, strictest

EDIT_TOOLS = {
    "Edit": "file_path",
    "Write": "file_path",
    "MultiEdit": "file_path",
    "NotebookEdit": "notebook_path",
}
# Why: Grok fires the hook through its matcher aliases but reports its own tool names, and the
# Cursor preset's tools name the file in `path`. Judged as the Claude tool they stand for; an
# unknown name would otherwise pass every rule.
HARNESS_TOOLS = {
    "run_terminal_command": "Bash",
    "Shell": "Bash",
    "search_replace": "Edit",
    "hashline_edit": "Edit",
    "write": "Write",
    "StrReplace": "Edit",
    "EditNotebook": "NotebookEdit",
    "run_command": "Bash",
    "write_to_file": "Write",
    "write_file": "Write",
    "replace_file_content": "Edit",
    "edit_file": "Edit",
    "multi_replace_file_content": "MultiEdit",
    "notebook_edit": "NotebookEdit",
}
# Where an edit tool names its file when not under the Claude tool's own key.
PATH_KEYS = ("file_path", "path", "notebook_path", "target_notebook")
# Why: Antigravity (agy) sends `{"toolCall": {"name", "args"}}` with CamelCase arguments and
# reads `{"decision", "reason"}` back; none of Claude Code's fields are in its input.
ANTIGRAVITY_CALL = "toolCall"
ANTIGRAVITY_ARGS = {
    "CommandLine": "command",
    "TargetFile": "file_path",
    "CodeContent": "content",
    "TargetContent": "old_string",
    "ReplacementContent": "new_string",
    "AllowMultiple": "replace_all",
}
ANTIGRAVITY_CHUNKS = "ReplacementChunks"
# Why: ratchet's `ask` means the owner approves this one call. Antigravity's `ask` honours a
# cached "Always Allow", which would let a later guard-path edit through unseen; `force_ask`
# always prompts. It never answers `allow`, which would skip Antigravity's own prompts.
ANTIGRAVITY_DECISIONS = {Decision.DENY: "deny", Decision.ASK: "force_ask"}
# Why: Antigravity documents `decision` as required and says nothing of empty stdout. An object
# with no decision is valid JSON that its permission manager treats as no opinion (agy 1.5.6
# changelog: empty decision strings are handled safely), so its own rules decide.
ANTIGRAVITY_SILENT = "{}"
# Why: Codex edits through apply_patch, whose input is the patch text; the paths are its headers.
PATCH_TOOL = "apply_patch"
PATCH_HEADER = re.compile(
    r"^\*\*\* (?P<verb>Add File|Update File|Delete File|Move to): (?P<path>.+)$", re.MULTILINE
)
PATCH_DESTRUCTIVE = frozenset({"Delete File", "Move to"})
GIT_TIMEOUT_SECONDS = 5
DISPUTES = "docs/disputes/NNNN-<slug>.md"
COMMITTED = "Guard paths come from guards/manifest.toml as committed on the default branch."
PLUGIN_ROOT = Path(__file__).resolve().parents[2]


class LocalFilesystem:
    """The real filesystem."""

    @property
    def home(self) -> Path:
        """The user's home directory."""
        return Path.home()

    def canonical(self, path: Path) -> Path:
        """``path`` with symlinks and ``..`` resolved."""
        return Path(os.path.realpath(path))

    def guarded_root(self, path: Path) -> Path | None:
        """The nearest ancestor holding ``.git``, if its default branch carries the manifest."""
        root = self.checkout(path)
        if root is None:
            return None
        return root if committed_manifest(root) is not None else None

    def checkout(self, path: Path) -> Path | None:
        """The nearest ancestor holding ``.git``."""
        return next((c for c in (path, *path.parents) if (c / ".git").exists()), None)

    def read_text(self, path: Path) -> str | None:
        """The file's text, or ``None`` when it is missing or unreadable."""
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None

    def glob(self, pattern: Path) -> list[Path]:
        """Existing paths matching ``pattern``."""
        anchor = Path(pattern.anchor)
        return sorted(anchor.glob(str(pattern.relative_to(anchor))))

    def expand(self, word: str) -> str:
        """``word`` with ``~`` and ``$VARIABLES`` expanded from this process's environment."""
        return os.path.expandvars(Path(word).expanduser())


def _git(cwd: Path, *args: str) -> str | None:
    try:
        completed = subprocess.run(  # noqa: S603 # -- a fixed argv, no shell
            ["git", "-C", str(cwd), *args],  # noqa: S607 # -- git is resolved from PATH on purpose
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SECONDS,  # pragma: no mutate # -- only bounds a hung git
            check=False,  # pragma: no mutate # -- a failure returns None either way
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout.strip() if completed.returncode == 0 else None


class GitRepoState:
    """Git state read with the ``git`` command."""

    def current_branch(self, cwd: Path) -> str | None:
        """The checked-out branch, or ``None`` when detached or not a repository."""
        return _git(cwd, "symbolic-ref", "--short", "HEAD") or None

    def head_is_pushed(self, cwd: Path) -> bool | None:
        """Whether any remote-tracking branch contains ``HEAD`` (``--contains`` defaults to it)."""
        listed = _git(cwd, "branch", "--remotes", "--contains")
        return None if listed is None else bool(listed)


def _unreadable(detail: str) -> Verdict:
    return Verdict("unreadable", detail)


def is_antigravity(payload: object) -> bool:
    """Whether ``payload`` is Antigravity's PreToolUse input rather than Claude Code's."""
    return isinstance(payload, dict) and ANTIGRAVITY_CALL in payload


def _claude_args(args: dict[str, object]) -> dict[str, object]:
    renamed = {ANTIGRAVITY_ARGS.get(key, key): value for key, value in args.items()}
    chunks = args.get(ANTIGRAVITY_CHUNKS)
    if isinstance(chunks, list):
        renamed["edits"] = [_claude_args(chunk) for chunk in chunks if isinstance(chunk, dict)]
    return renamed


def from_antigravity(payload: dict[str, object]) -> dict[str, object]:
    """Antigravity's PreToolUse input in Claude Code's shape; what is missing stays missing.

    The command's own ``Cwd`` is where it runs, so it wins over the agent's ``cwd``.
    """
    call = payload.get(ANTIGRAVITY_CALL)
    call = call if isinstance(call, dict) else {}
    args = call.get("args")
    own = args.get("Cwd") if isinstance(args, dict) else None
    return {
        "tool_name": call.get("name"),
        "tool_input": _claude_args(args) if isinstance(args, dict) else args,
        "cwd": own if isinstance(own, str) and own else payload.get("cwd"),
    }


def decide(payload: object, guard: Guard) -> Verdict | None:
    """The strictest verdict for one PreToolUse payload, or ``None`` when no rule fires."""
    if not isinstance(payload, dict):
        return _unreadable("the hook input is not a JSON object")
    if is_antigravity(payload):
        payload = from_antigravity(payload)
    tool, tool_input, cwd = payload.get("tool_name"), payload.get("tool_input"), payload.get("cwd")
    if not isinstance(tool, str) or not isinstance(tool_input, dict) or not isinstance(cwd, str):
        return _unreadable("the hook input lacks tool_name, tool_input or cwd")
    if tool == PATCH_TOOL:
        return _patch(tool_input, Path(cwd), guard)
    tool = HARNESS_TOOLS.get(tool, tool)
    key = "command" if tool == "Bash" else EDIT_TOOLS.get(tool)
    if key is None:
        return None
    target = tool_input.get(key)
    if not isinstance(target, str) and key != "command":
        target = next(
            (tool_input[k] for k in PATH_KEYS if isinstance(tool_input.get(k), str)), None
        )
    if not isinstance(target, str):
        return _unreadable(f"{tool} input has no {key}")
    return _judge(tool, target, Context(Path(cwd)), guard=guard, tool_input=tool_input)


def _judge(
    tool: str, target: str, context: Context, *, guard: Guard, tool_input: dict[str, object]
) -> Verdict | None:
    if tool == "Bash":
        return strictest(bash.check(target, context, guard))
    path = context.cwd / target
    label = tickets.check(tool, tool_input, path, context=context, guard=guard)
    return strictest(v for v in (_edit(path, guard), label) if v is not None)


def _patch(tool_input: dict[str, object], cwd: Path, guard: Guard) -> Verdict | None:
    """The strictest verdict over the files an ``apply_patch`` adds, updates, deletes or moves."""
    text = "\n".join(value for value in tool_input.values() if isinstance(value, str))
    verdicts = []
    for match in PATCH_HEADER.finditer(text):
        destructive = match.group("verb") in PATCH_DESTRUCTIVE
        hit = guard.locator.classify(cwd / match.group("path").strip(), destructive=destructive)
        if hit is not None:
            verdicts.append(_hit_verdict(hit))
    return strictest(verdicts)


def _edit(path: Path, guard: Guard) -> Verdict | None:
    hit = guard.locator.classify(path, destructive=False)  # pragma: no mutate # -- None is False
    return None if hit is None else _hit_verdict(hit)


def _hit_verdict(hit: Hit) -> Verdict:
    detail = f"{hit.path} is in guard group '{hit.group.name}'. {hit.group.reason}"
    return Verdict("guard-path-locked" if hit.group.locked else "guard-path-edit", detail)


def reason(verdict: Verdict) -> str:
    """The sentence Claude (deny) or the human (ask) reads."""
    stop = "" if verdict.detail.endswith(".") else "."
    if verdict.rule.startswith("guard-path"):
        # Why: the agent should know where the rule comes from, and that it is not in the tree.
        stop += f" {COMMITTED}"
    if verdict.decision is Decision.DENY:
        # Why: a denied Bash call runs none of its commands, so an unrelated write chained
        # with the refused one is lost; the agent must hear that, and the matched token.
        return (
            f"Ratchet guard denied this tool call; no part of it ran. It {verdict.because}: "
            f"{verdict.detail}{stop} Do not work around it. If you believe the gate is wrong, "
            f"stop, write a gate dispute ({DISPUTES}) and report it to whoever started you. If "
            f"the call also did unrelated work, redo that in a tool call of its own."
        )
    return (
        f"Ratchet guard: this tool call {verdict.because}: {verdict.detail}{stop} Approve only "
        f"if you, the owner, intend this. An agent that thinks a gate is wrong writes a gate "
        f"dispute ({DISPUTES}) instead."
    )


def respond(verdict: Verdict | None, *, antigravity: bool = False) -> str:
    """The hook's stdout: a PreToolUse decision, or nothing (``{}`` for Antigravity)."""
    if antigravity:
        if verdict is None:
            return ANTIGRAVITY_SILENT
        answer = {"decision": ANTIGRAVITY_DECISIONS[verdict.decision], "reason": reason(verdict)}
        return json.dumps(answer)
    if verdict is None:
        return ""
    decision = "deny" if verdict.decision is Decision.DENY else "ask"
    output = {
        "hookEventName": "PreToolUse",
        "permissionDecision": decision,
        "permissionDecisionReason": reason(verdict),
    }
    return json.dumps({"hookSpecificOutput": output})


def run(text: str, make_guard: Callable[[], Guard]) -> str:
    """Answer one hook call in its harness's form; every failure becomes an ``ask``."""
    payload: object = None
    try:
        payload = json.loads(text)
        verdict = decide(payload, make_guard())
    except Exception as error:  # noqa: BLE001 # -- fail closed: any failure must reach a human
        verdict = _unreadable(f"{type(error).__name__}: {error}")
    return respond(verdict, antigravity=is_antigravity(payload))


def repository_root(path: Path) -> Path:
    """The nearest checkout holding ``path`` (an ancestor with ``.git``), else ``path`` itself."""
    return next((c for c in (path, *path.parents) if (c / ".git").exists()), path)


def read_manifest(root: Path) -> Manifest:
    """The manifest committed on the default branch of the checkout holding ``root``."""
    text = committed_manifest(repository_root(root))
    return EMPTY if text is None else parse(text)


def local_guard(root: Path, plugin_root: Path = PLUGIN_ROOT) -> Guard:
    """The guard over the real filesystem and git, with the manifest under ``root``."""
    manifest = with_plugin_group(read_manifest(root), plugin_root)
    return Guard(Locator(manifest, LocalFilesystem()), GitRepoState())


def main(root: Path, stdin: TextIO = sys.stdin, stdout: TextIO = sys.stdout) -> int:
    """Read the payload, write the decision, exit 0 (a decision is carried in the JSON)."""
    answer = run(stdin.read(), lambda: local_guard(root))
    if answer:
        stdout.write(answer + "\n")
    return 0
