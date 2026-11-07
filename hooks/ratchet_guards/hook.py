"""The Claude Code PreToolUse hook: JSON in on stdin, a decision out on stdout.

``hooks/guard.py`` puts ``hooks/`` on ``sys.path`` and calls ``main`` with the repository
root. For Edit, Write, MultiEdit and NotebookEdit on a guard path the hook answers ``ask``,
which needs a human at the permission prompt, or ``deny`` in a group whose ``edit`` is
``"deny"`` (paths Claude Code's bypass mode would let through an ``ask``); for a Bash
command that bypasses a gate, rewrites history, removes a plugin or writes a guard path it
answers ``deny``. When no rule fires it prints nothing, so Claude Code's own permission rules
decide; it never answers ``allow``. Any failure answers ``ask``.

A verdict covers the whole tool call: Claude Code runs none of a denied Bash command line,
so a write chained before the refused command is lost with it. The deny message says so and
names the matched command, and the method asks agents to keep a gated command in a tool
call of its own.

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
``~/.claude/settings.json`` and ``~/.claude/settings.local.json``.
"""

import json
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TextIO

from ratchet_guards import bash
from ratchet_guards.committed import committed_manifest
from ratchet_guards.context import Context, Guard
from ratchet_guards.manifest import EMPTY, Manifest, parse, with_plugin_group
from ratchet_guards.paths import Locator
from ratchet_guards.verdict import Decision, Verdict, strictest

EDIT_TOOLS = {
    "Edit": "file_path",
    "Write": "file_path",
    "MultiEdit": "file_path",
    "NotebookEdit": "notebook_path",
}
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


def decide(payload: object, guard: Guard) -> Verdict | None:
    """The strictest verdict for one PreToolUse payload, or ``None`` when no rule fires."""
    if not isinstance(payload, dict):
        return _unreadable("the hook input is not a JSON object")
    tool, tool_input, cwd = payload.get("tool_name"), payload.get("tool_input"), payload.get("cwd")
    if not isinstance(tool, str) or not isinstance(tool_input, dict) or not isinstance(cwd, str):
        return _unreadable("the hook input lacks tool_name, tool_input or cwd")
    key = "command" if tool == "Bash" else EDIT_TOOLS.get(tool)
    if key is None:
        return None
    target = tool_input.get(key)
    if not isinstance(target, str):
        return _unreadable(f"{tool} input has no {key}")
    if tool == "Bash":
        return strictest(bash.check(target, Context(Path(cwd)), guard))
    return _edit(Path(cwd) / target, guard)


def _edit(path: Path, guard: Guard) -> Verdict | None:
    hit = guard.locator.classify(path, destructive=False)  # pragma: no mutate # -- None is False
    if hit is None:
        return None
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


def respond(verdict: Verdict | None) -> str:
    """The hook's stdout: a PreToolUse decision, or nothing."""
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
    """Answer one hook call; every failure becomes an ``ask``."""
    try:
        verdict = decide(json.loads(text), make_guard())
    except Exception as error:  # noqa: BLE001 # -- fail closed: any failure must reach a human
        verdict = _unreadable(f"{type(error).__name__}: {error}")
    return respond(verdict)


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
