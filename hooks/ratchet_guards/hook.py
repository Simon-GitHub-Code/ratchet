"""The Claude Code PreToolUse hook: JSON in on stdin, a decision out on stdout.

``hooks/guard.py`` puts ``hooks/`` on ``sys.path`` and calls ``main`` with the repository
root. Grok's own tool names (``run_terminal_command``, ``search_replace``, ``write``, ...) are
judged as the Claude tools they stand for. Antigravity's input (``toolCall`` with ``name`` and
``args``) is translated to Claude's shape first, and the answer goes back in Antigravity's
(``decision`` and ``reason``).

A change to a guard path, by Edit, Write, MultiEdit, NotebookEdit, a Codex ``apply_patch`` or the
shell, gets no verdict when its commit lands on a branch other than the default: it reaches the
default branch only through a merge, and the merge judge sends it to the owner (ADR 0033). Where
the commit would land on the default branch it is denied, with the way out: make it on a branch.
In a group whose ``edit`` is ``"deny"`` (the files the owner edits by hand) every change is
denied. A write the hook reads of a file that acts the moment it is written, a CI workflow or an
agent-instruction file at any depth (``ratchet_guards.early``), asks the owner on every branch, in
one plain sentence. The hook reads a write whose path the tool call names: it is a check on
ordinary writes, not a boundary against an agent that sets out to get round it (ADR 0033). In a
group whose ``edit`` is ``"append"`` (the glossary and the decision
records) an Edit, Write or MultiEdit that only adds passes on the default branch too:
``ratchet_guards.records`` compares the file the call would leave with the merge base of ``HEAD``
and the default branch (ADR 0014). For a Bash command that bypasses a gate, rewrites history or
removes a plugin it answers ``deny``. It answers ``ask`` on a merge into the default branch and on
the agent-ready label, the owner's to give. When no rule fires it prints nothing, so the harness's
own permission rules decide; Antigravity, which reads no answer as a deny, gets a plain ``ask``,
its own prompt. It never answers ``allow``. Any failure answers ``ask``, but a git that cannot
start or runs out of time reads as no manifest, and the path rules then give no verdict.

A verdict covers the whole tool call: Claude Code runs none of a denied Bash command line,
so a write chained before the refused command is lost with it. The deny message says so and
names the matched command, and the method asks agents to keep a gated command in a tool
call of its own.

An Edit, Write or MultiEdit that gives a local ticket under ``.scratch/`` the agent-ready status
asks, as applying that label on a forge does, and so does a command line or a patch that names
the label and writes the ticket (``ratchet_guards.tickets``).

One forge write is allowed by a record: ``gh label create <name>`` in a checkout whose
``docs/agents/triage-labels.md`` lists ``<name>`` in its "Label in our tracker" column (see
``ratchet_guards.gh``). The file is a guard path in the decisions group once the repository
is adopted, so the list is the owner's.

The guard paths come from ``guards/manifest.toml`` as committed on the default branch of the
checkout holding the path being judged, read from git (``ratchet_guards.committed``) once per
checkout. In the session's project (``$CLAUDE_PROJECT_DIR``) that checkout is one git records:
the project's own work tree, a worktree under ``.claude/worktrees/`` or a recorded submodule, so
a ``.git`` an agent makes moves nothing; elsewhere it is the nearest ancestor holding ``.git``.
The session's working directory does not choose the manifest: an edit from a subagent, a
monorepo parent or a sibling checkout is judged by the manifest of the checkout it lands in.
An uncommitted manifest, or one committed
only on a branch, guards nothing yet: that is the adoption in progress, and only the command
rules apply until it merges. Once merged, the manifest file itself takes only appended groups
and doors from an agent, and a default branch that loses it keeps every path its last version
named locked (``ratchet_guards.manifest``). The plugin's own files are locked either way:
``PLUGIN_ROOT`` (two parents above this package) and ``~/.claude/plugins/**``, together with
``~/.claude/settings.json`` and ``~/.claude/settings.local.json``, and the same switches of
the other harnesses (``ratchet_guards.manifest.PLUGIN_HOME_PATTERNS``).
"""

import json
import os
import re
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from ratchet_guards import bash, records, tickets
from ratchet_guards.committed import committed_manifest, default_branch
from ratchet_guards.context import DETACHED, Context, Guard
from ratchet_guards.manifest import (
    EMPTY,
    MANIFEST_LOCK,
    Manifest,
    gone,
    grown,
    parse,
    with_plugin_group,
)
from ratchet_guards.paths import Checkout, Hit, Locator
from ratchet_guards.refs import MAIN_BRANCHES, REMOTE
from ratchet_guards.verdict import OWNER_ASKS, Decision, Verdict, strictest

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
# cached "Always Allow", which would let a later merge or label through unseen; `force_ask`
# always prompts. It never answers `allow`, which would skip Antigravity's own prompts.
ANTIGRAVITY_DECISIONS = {Decision.DENY: "deny", Decision.ASK: "force_ask"}
# Why: Antigravity has no "no opinion" answer. A missing or empty `decision` is a deny (seen live
# in agy 1.5.6: `git status` was refused with an empty reason), and `allow` auto-approves past its
# own permission prompt. `ask` is that prompt, cached "Always Allow" included, so it changes
# nothing Antigravity would have done without the guard.
ANTIGRAVITY_NO_RULE = json.dumps({"decision": "ask"})
# Why: Codex edits through apply_patch, whose input is the patch text; the paths are its headers.
PATCH_TOOL = "apply_patch"
PATCH_HEADER = re.compile(
    r"^\*\*\* (?P<verb>Add File|Update File|Delete File|Move to): (?P<path>.+)$", re.MULTILINE
)
PATCH_DESTRUCTIVE = frozenset({"Delete File", "Move to"})
GIT_TIMEOUT_SECONDS = 5
PLUGIN_ROOT = Path(__file__).resolve().parents[2]
STANDARD_MANIFEST = "skills/adopt/templates/guards/manifest.toml"


@dataclass(frozen=True, slots=True)
class Call:
    """One edit: the tool as the harness named it, the Claude tool it stands for, its input."""

    reported: str
    tool: str
    tool_input: Mapping[str, object]


class LocalFilesystem:
    """The real filesystem."""

    def __init__(self, project: Path | None = None) -> None:
        """A filesystem whose checkouts are judged from ``project``'s, when one is named.

        The project's checkouts are the work trees git lists for it, read once, on first use.
        """
        self._project = project
        self._root = None if project is None else self.canonical(project)
        self._trees: list[Path] | None = None
        self._submodules: dict[Path, bool] = {}
        self._manifests: dict[Path, Manifest | None] = {}

    @property
    def home(self) -> Path:
        """The user's home directory."""
        return Path.home()

    def canonical(self, path: Path) -> Path:
        """``path`` with symlinks and ``..`` resolved."""
        return Path(os.path.realpath(path))

    def committed(self, root: Path) -> Manifest | None:
        """The manifest committed on the default branch of the checkout at ``root``.

        Read from git once per checkout per process. When the branch's tip lost the file, it is
        the last one the branch held with every group locked. A manifest that does not parse
        raises ``ManifestError`` and is not cached, so every judged path in that checkout asks.
        """
        if root not in self._manifests:
            found = committed_manifest(root)
            if found is None:
                self._manifests[root] = None
            else:
                manifest = parse(found.text)
                self._manifests[root] = gone(manifest) if found.gone else manifest
        return self._manifests[root]

    def guarded_checkout(self, path: Path) -> Checkout | None:
        """The checkout holding ``path`` with its committed manifest, if it has one."""
        root = self.checkout(path)
        manifest = None if root is None else self.committed(root)
        return None if root is None or manifest is None else Checkout(root, manifest)

    def checkout(self, path: Path) -> Path | None:
        """The checkout holding ``path``.

        In the project it is the deepest of the project's work trees that holds ``path``, or a
        submodule of it: a ``.git`` an agent made in between is no checkout. Outside it, the
        nearest ancestor holding ``.git``. When that ancestor is the project itself, nothing lies
        in between to ask git about.
        """
        nearest = next((c for c in (path, *path.parents) if (c / ".git").exists()), None)
        if nearest is not None and nearest == self._root:
            return nearest
        tree = next((tree for tree in self._worktrees() if path.is_relative_to(tree)), None)
        return nearest if tree is None else self._within(tree, path)

    def _worktrees(self) -> list[Path]:
        """The project's work trees, deepest first.

        Why: a work tree inside another is a checkout of its own only under that one's
        ``.claude/worktrees/``, where Claude Code puts its agents' worktrees. One added at
        ``.claude/skills/x`` would otherwise make a skill written there a file at its root.
        """
        if self._trees is None:
            project = self._project
            listed = "" if project is None else _output(project, "worktree", "list", "--porcelain")
            trees = [
                self.canonical(Path(line.removeprefix("worktree ")))
                for line in (listed or "").splitlines()
                if line.startswith("worktree ")
            ]
            admitted = [
                tree
                for tree in trees
                if all(
                    tree.is_relative_to(other / ".claude/worktrees")
                    for other in trees
                    if other != tree and tree.is_relative_to(other)
                )
            ]
            self._trees = sorted(admitted, key=lambda tree: len(tree.parts), reverse=True)
        return self._trees

    def _within(self, root: Path, path: Path) -> Path:
        """``root``, or the submodule its default branch records that holds ``path``."""
        below = (c for c in (path, *path.parents) if c != root and c.is_relative_to(root))
        held = next((c for c in below if (c / ".git").exists() and self._recorded(root, c)), None)
        return root if held is None else self._within(held, path)

    def _recorded(self, root: Path, directory: Path) -> bool:
        """Whether the default branch of ``root`` records ``directory`` as a submodule.

        Why: the default branch is the owner's. A gitlink in the index or on a branch is one
        ``git add`` of a directory holding ``git init`` away.
        """
        if directory not in self._submodules:
            ref = default_branch(root)
            relative = directory.relative_to(root).as_posix()
            entry = _git(root, "ls-tree", ref, "--", relative) if ref else None
            self._submodules[directory] = bool(entry) and str(entry).startswith(_GITLINK)
        return self._submodules[directory]

    def read_text(self, path: Path) -> str | None:
        """The file's text, or ``None`` when it is missing or unreadable."""
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None

    def merge_base(self, checkout: Path, relative: str) -> records.Before | None:
        """``relative`` at the merge base of ``HEAD`` with the default branch, read from git.

        The default branch is the one the manifest is read from, so the commit is one the owner
        merged, never one the branch made.
        """
        ref = default_branch(checkout)
        commit = _git(checkout, "merge-base", ref, "HEAD") if ref else None
        if not commit:
            return None
        return records.before(lambda args: _output(checkout, *args), commit, relative)

    def glob(self, pattern: Path) -> list[Path]:
        """Existing paths matching ``pattern``, as a shell without ``globstar`` expands it.

        Why: ``**`` is ``*`` to such a shell, and read as Python reads it a word like ``/** A
        doc comment`` in a program's text walked the whole filesystem and outran the hook's
        timeout, which lets the tool call run unjudged.
        """
        anchor = Path(pattern.anchor)
        shell = re.sub(r"\*\*+", "*", str(pattern.relative_to(anchor)))
        return sorted(anchor.glob(shell))

    def expand(self, word: str) -> str:
        """``word`` with ``~`` and ``$VARIABLES`` expanded from this process's environment."""
        return os.path.expandvars(Path(word).expanduser())


def _run(cwd: Path, *args: str) -> subprocess.CompletedProcess[str] | None:
    """``git -C cwd args`` as it finished, or ``None`` when git is missing or hangs."""
    try:
        return subprocess.run(  # noqa: S603 # -- a fixed argv, no shell
            ["git", "-C", str(cwd), *args],  # noqa: S607 # -- git is resolved from PATH on purpose
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SECONDS,  # pragma: no mutate # -- only bounds a hung git
            check=False,  # pragma: no mutate # -- the caller reads the exit status
        )
    except (OSError, subprocess.SubprocessError):
        return None


def _output(cwd: Path, *args: str) -> str | None:
    """``git -C cwd args`` stdout as git wrote it, or ``None`` when git fails or is missing."""
    completed = _run(cwd, *args)
    return completed.stdout if completed is not None and completed.returncode == 0 else None


def _git(cwd: Path, *args: str) -> str | None:
    output = _output(cwd, *args)
    return None if output is None else output.strip()


_HEADS = "refs/heads/"
# What `git ls-tree` prints first for a submodule.
_GITLINK = "160000 commit "
_TRACKING = f"refs/remotes/{REMOTE}/"
# `git symbolic-ref -q` exits 1, silently, when HEAD names a commit rather than a branch.
_NOT_SYMBOLIC = 1


def _head(root: Path) -> str | None:
    completed = _run(root, "symbolic-ref", "-q", "HEAD")
    if completed is None:
        return None
    if completed.returncode == _NOT_SYMBOLIC:
        return DETACHED
    head = completed.stdout.strip() if completed.returncode == 0 else ""
    return head.removeprefix(_HEADS) or None


class GitRepoState:
    """Git state read with the ``git`` command.

    What branch a checkout has, and which are its default ones, is read once per instance: one
    tool call may name hundreds of files in one checkout, and git's answer does not change while
    the guard judges it.
    """

    def __init__(self) -> None:
        """Empty caches of what each checkout has checked out and calls its default branches."""
        self._heads: dict[Path, str | None] = {}
        self._defaults: dict[Path, frozenset[str]] = {}

    def current_branch(self, cwd: Path) -> str | None:
        """The checked-out branch, or ``None`` when detached or not a repository.

        Read by its full name: ``--short`` prints ``heads/main`` when a tag is also named
        ``main``, which no rule would read as the default branch.
        """
        head = _git(cwd, "symbolic-ref", "HEAD")
        return head.removeprefix(_HEADS) if head else None

    def checked_out(self, root: Path) -> str | None:
        """The branch the checkout at ``root`` has, ``DETACHED``, or ``None`` when git fails.

        A branch with no commit yet is still a branch: ``HEAD`` names it.
        """
        if root not in self._heads:
            self._heads[root] = _head(root)
        return self._heads[root]

    def default_branches(self, root: Path) -> frozenset[str]:
        """``main``, ``master`` and the branch the committed manifest is read from at ``root``."""
        if root not in self._defaults:
            name = (default_branch(root) or "").removeprefix(_TRACKING).removeprefix(_HEADS)
            self._defaults[root] = MAIN_BRANCHES | ({name} if name else frozenset())
        return self._defaults[root]

    def head_is_pushed(self, cwd: Path) -> bool | None:
        """Whether any remote-tracking branch contains ``HEAD`` (``--contains`` defaults to it)."""
        listed = _git(cwd, "branch", "--remotes", "--contains")
        return None if listed is None else bool(listed)

    def full_name(self, cwd: Path, name: str) -> str | None:
        """The full name of the one ref git reads ``name`` as, or ``None``.

        ``None`` covers a name git does not know, one it reads as two refs (a branch and a tag
        named alike), and one that is a commit and no ref.
        """
        return _git(cwd, "rev-parse", "--verify", "--quiet", "--symbolic-full-name", name) or None


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
    call = Call(tool, HARNESS_TOOLS.get(tool, tool), tool_input)
    key = "command" if call.tool == "Bash" else EDIT_TOOLS.get(call.tool)
    if key is None:
        return None
    target = tool_input.get(key)
    if not isinstance(target, str) and key != "command":
        target = next(
            (tool_input[k] for k in PATH_KEYS if isinstance(tool_input.get(k), str)), None
        )
    if not isinstance(target, str):
        return _unreadable(f"{call.tool} input has no {key}")
    return _judge(call, target, Context(Path(cwd)), guard=guard)


def _judge(call: Call, target: str, context: Context, *, guard: Guard) -> Verdict | None:
    if call.tool == "Bash":
        return strictest(bash.check(target, context, guard))
    path = context.cwd / target
    label = tickets.check(call.tool, call.tool_input, path, guard=guard)
    verdicts = [v for v in (_edit(call, path, context, guard=guard), label) if v is not None]
    return strictest(verdicts + guard.acting([path]))


def _patch(tool_input: dict[str, object], cwd: Path, guard: Guard) -> Verdict | None:
    """The strictest verdict over the files an ``apply_patch`` adds, updates, deletes or moves.

    A patch is not computed, so on the default branch even an addition to a record is refused.
    """
    text = "\n".join(value for value in tool_input.values() if isinstance(value, str))
    context = Context(cwd)
    verdicts = []
    for match in PATCH_HEADER.finditer(text):
        destructive = match.group("verb") in PATCH_DESTRUCTIVE
        hit = guard.locator.classify(cwd / match.group("path").strip(), destructive=destructive)
        if hit is not None and (hit.group.locked or guard.lands_now(context, hit.at)):
            verdicts.append(_hit_verdict(hit))
    paths = [match.group("path").strip() for match in PATCH_HEADER.finditer(text)]
    acting = guard.acting(cwd / path for path in paths)
    return strictest(verdicts + acting + tickets.named(paths, text, context, guard=guard))


def _edit(call: Call, path: Path, context: Context, *, guard: Guard) -> Verdict | None:
    """A locked path is refused; any other guard path only where the edit lands on main."""
    hit = guard.locator.classify(path, destructive=False)  # pragma: no mutate # -- None is False
    if hit is None or not (hit.group.locked or guard.lands_now(context, hit.at)):
        return None
    if hit.group == MANIFEST_LOCK:
        return _manifest_edit(call, hit, context, guard=guard)
    if hit.group.locked or not hit.group.additive:
        return _hit_verdict(hit)
    reason = _not_additive(call, path, guard)
    return None if reason is None else _hit_verdict(hit, f" This edit is not an addition: {reason}")


def _not_additive(call: Call, path: Path, guard: Guard) -> str | None:
    """Why the edit does more than add to a record, or ``None`` when adding is all it does.

    The file the call would leave is compared with the merge base, not with the work tree: a
    branch may rework what it added, and may not touch what the owner merged.
    """
    filesystem = guard.locator.filesystem
    canonical = filesystem.canonical(path)
    root = filesystem.checkout(canonical)
    after = records.written(call.reported, call.tool_input, filesystem.read_text(canonical))
    if root is None or after is None:
        return f"the guard cannot tell what this {call.reported} call would write"
    relative = canonical.relative_to(root).as_posix()
    before = filesystem.merge_base(root, relative)
    if before is None:
        return "there is no merge base with the default branch to compare it with"
    try:
        records.additions(relative, before, after)
    except records.NotAdditiveError as error:
        return str(error)
    return None


def _manifest_edit(call: Call, hit: Hit, context: Context, *, guard: Guard) -> Verdict | None:
    """An Edit or Write that only adds patterns, groups or doors to the manifest, on a branch.

    It is compared with the manifest the default branch holds, which the guards are read from.
    """
    filesystem = guard.locator.filesystem
    after = records.written(call.reported, call.tool_input, filesystem.read_text(hit.at))
    checkout = filesystem.guarded_checkout(hit.at)
    reason: str | None = f"the guard cannot tell what this {call.reported} call would write"
    if after is not None and checkout is not None:
        reason = grown(checkout.manifest, after)
    if reason is not None:
        return _hit_verdict(hit, f" This edit is not an addition: {reason}.")
    if guard.lands_now(context, hit.at):
        return _hit_verdict(hit, rule="guard-path-default")
    return None


def _hit_verdict(hit: Hit, more: str = "", rule: str | None = None) -> Verdict:
    detail = f"{hit.path} is in guard group '{hit.group.name}'. {hit.group.reason}{more}"
    named = rule or ("guard-path-locked" if hit.group.locked else "guard-path-default")
    return Verdict(named, detail, hit.group.source)


def reason(verdict: Verdict) -> str:
    """The sentence the agent (deny) or the owner (ask) reads, ending in what to do next."""
    if verdict.rule in OWNER_ASKS:
        return f"Ratchet: {verdict.because} ({verdict.detail}). {verdict.way_out}"
    stop = "" if verdict.detail.endswith(".") else "."
    if verdict.source:
        # Why: the agent should know where a guard path comes from, and that it is not the tree.
        stop += f" {verdict.source}"
    if verdict.decision is Decision.DENY:
        # Why: a denied Bash call runs none of its commands, so an unrelated write chained
        # with the refused one is lost; the agent must hear that, and the matched token.
        return (
            f"Ratchet guard denied this tool call; no part of it ran. It {verdict.because}: "
            f"{verdict.detail}{stop} {verdict.way_out} If the call also did unrelated work, redo "
            f"that in a tool call of its own."
        )
    return (
        f"Ratchet: an agent asks to run a tool call that {verdict.because}: {verdict.detail}"
        f"{stop} {verdict.way_out} Approve only if you have read them and want this change in "
        f"now."
    )


def respond(verdict: Verdict | None, *, antigravity: bool = False) -> str:
    """The hook's stdout: a PreToolUse decision, or nothing (Antigravity's plain ``ask``)."""
    if antigravity:
        if verdict is None:
            return ANTIGRAVITY_NO_RULE
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


def read_manifest(root: Path, filesystem: LocalFilesystem | None = None) -> Manifest:
    """The manifest committed on the default branch of the checkout holding ``root``."""
    filesystem = filesystem or LocalFilesystem()
    checkout = filesystem.checkout(filesystem.canonical(root))
    manifest = filesystem.committed(checkout or root)
    return EMPTY if manifest is None else manifest


def standard_manifest(plugin_root: Path) -> Manifest:
    """The manifest ``/ratchet:adopt`` starts a repository from, or ``EMPTY`` without the template.

    It is the guard's best guess at the guard paths of a repository it cannot see.
    """
    try:
        return parse((plugin_root / STANDARD_MANIFEST).read_text(encoding="utf-8"))
    except OSError:
        return EMPTY


def local_guard(root: Path, plugin_root: Path = PLUGIN_ROOT, project: Path | None = None) -> Guard:
    """The guard over the real filesystem and git.

    ``root`` is the session's working directory and ``project`` the directory the harness
    started the session in, ``root`` when it names none. Its checkout's manifest answers the home
    and absolute patterns; a path in the repository is judged by the manifest of the checkout
    holding that path, ``root``'s or not, and a path in the project from the project's checkout.
    """
    filesystem = LocalFilesystem(project or root)
    manifest = with_plugin_group(read_manifest(root, filesystem), plugin_root)
    standard = standard_manifest(plugin_root)
    return Guard(Locator(manifest, filesystem, standard), GitRepoState())


def main(
    root: Path,
    stdin: TextIO = sys.stdin,
    stdout: TextIO = sys.stdout,
    *,
    project: Path | None = None,
) -> int:
    """Read the payload, write the decision, exit 0 (a decision is carried in the JSON)."""
    answer = run(stdin.read(), lambda: local_guard(root, project=project))
    if answer:
        stdout.write(answer + "\n")
    return 0
