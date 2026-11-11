"""The Claude Code PreToolUse hook: JSON in on stdin, a decision out on stdout.

``hooks/guard.py`` puts ``hooks/`` on ``sys.path`` and calls ``main`` with the repository
root. For Edit, Write, MultiEdit and NotebookEdit on a guard path, and for a Codex ``apply_patch``
whose headers name one, the hook answers ``ask``. Grok's own tool names
(``run_terminal_command``, ``search_replace``, ``write``, ...) are judged as the Claude tools
they stand for. It answers ``ask``,
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
nearest checkout holding the path being judged (an ancestor with ``.git``), read from git
(``ratchet_guards.committed``) once per checkout. The session's working directory does not
choose the manifest: an edit from a subagent, a monorepo parent or a sibling checkout is
judged by the manifest of the checkout it lands in. An uncommitted manifest, or one committed
only on a branch, guards nothing yet: that is the adoption in progress, and only the command
rules apply until it merges. The plugin's own files are locked either way: ``PLUGIN_ROOT`` (two
parents above this package) and ``~/.claude/plugins/**``, together with
``~/.claude/settings.json`` and ``~/.claude/settings.local.json``.
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
from ratchet_guards.paths import Checkout, Hit, Locator
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
}
# Where an edit tool names its file when not under the Claude tool's own key.
PATH_KEYS = ("file_path", "path", "notebook_path", "target_notebook")
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

    def __init__(self) -> None:
        """An empty cache of committed manifests, by checkout root."""
        self._manifests: dict[Path, Manifest | None] = {}

    def committed(self, root: Path) -> Manifest | None:
        """The manifest committed on the default branch of the checkout at ``root``.

        Read from git once per checkout per process. A manifest that does not parse raises
        ``ManifestError`` and is not cached, so every judged path in that checkout asks.
        """
        if root not in self._manifests:
            text = committed_manifest(root)
            self._manifests[root] = None if text is None else parse(text)
        return self._manifests[root]

    def guarded_checkout(self, path: Path) -> Checkout | None:
        """The nearest ancestor holding ``.git`` with its committed manifest, if it has one."""
        root = self.checkout(path)
        manifest = None if root is None else self.committed(root)
        return None if root is None or manifest is None else Checkout(root, manifest)

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
    label = tickets.check(tool, tool_input, path, guard=guard)
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


def read_manifest(root: Path, filesystem: LocalFilesystem | None = None) -> Manifest:
    """The manifest committed on the default branch of the checkout holding ``root``."""
    manifest = (filesystem or LocalFilesystem()).committed(repository_root(root))
    return EMPTY if manifest is None else manifest


def local_guard(root: Path, plugin_root: Path = PLUGIN_ROOT) -> Guard:
    """The guard over the real filesystem and git.

    ``root`` is the session's working directory. Its checkout's manifest answers the home and
    absolute patterns; a path in the repository is judged by the manifest of the checkout
    holding that path, ``root``'s or not.
    """
    filesystem = LocalFilesystem()
    manifest = with_plugin_group(read_manifest(root, filesystem), plugin_root)
    return Guard(Locator(manifest, filesystem), GitRepoState())


def main(root: Path, stdin: TextIO = sys.stdin, stdout: TextIO = sys.stdout) -> int:
    """Read the payload, write the decision, exit 0 (a decision is carried in the JSON)."""
    answer = run(stdin.read(), lambda: local_guard(root))
    if answer:
        stdout.write(answer + "\n")
    return 0
