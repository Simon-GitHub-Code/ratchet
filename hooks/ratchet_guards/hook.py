import contextlib
import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from ratchet_guards import bash, events, records, tickets
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
PATH_KEYS = ("file_path", "path", "notebook_path", "target_notebook")
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
ANTIGRAVITY_DECISIONS = {Decision.DENY: "deny", Decision.ASK: "force_ask"}
ANTIGRAVITY_NO_RULE = json.dumps({"decision": "ask"})
PATCH_TOOL = "apply_patch"
PATCH_HEADER = re.compile(
    r"^\*\*\* (?P<verb>Add File|Update File|Delete File|Move to): (?P<path>.+)$", re.MULTILINE
)
PATCH_DESTRUCTIVE = frozenset({"Delete File", "Move to"})
GIT_TIMEOUT_SECONDS = 5
LOG_TIMEOUT_SECONDS = 1.0
PLUGIN_ROOT = Path(__file__).resolve().parents[2]
STANDARD_MANIFEST = "skills/adopt/templates/guards/manifest.toml"


@dataclass(frozen=True, slots=True)
class Call:
    reported: str
    tool: str
    tool_input: Mapping[str, object]


class LocalFilesystem:
    def __init__(self, project: Path | None = None) -> None:
        self._project = project
        self._root = None if project is None else self.canonical(project)
        self._trees: list[Path] | None = None
        self._submodules: dict[Path, bool] = {}
        self._manifests: dict[Path, Manifest | None] = {}

    @property
    def home(self) -> Path:
        return Path.home()

    def canonical(self, path: Path) -> Path:
        return Path(os.path.realpath(path))

    def committed(self, root: Path) -> Manifest | None:
        if root not in self._manifests:
            found = committed_manifest(root)
            if found is None:
                self._manifests[root] = None
            else:
                manifest = parse(found.text)
                self._manifests[root] = gone(manifest) if found.gone else manifest
        return self._manifests[root]

    def guarded_checkout(self, path: Path) -> Checkout | None:
        root = self.checkout(path)
        manifest = None if root is None else self.committed(root)
        return None if root is None or manifest is None else Checkout(root, manifest)

    def checkout(self, path: Path) -> Path | None:
        nearest = next((c for c in (path, *path.parents) if (c / ".git").exists()), None)
        if nearest is not None and nearest == self._root:
            return nearest
        tree = next((tree for tree in self._worktrees() if path.is_relative_to(tree)), None)
        return nearest if tree is None else self._within(tree, path)

    def _worktrees(self) -> list[Path]:
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
        below = (c for c in (path, *path.parents) if c != root and c.is_relative_to(root))
        held = next((c for c in below if (c / ".git").exists() and self._recorded(root, c)), None)
        return root if held is None else self._within(held, path)

    def _recorded(self, root: Path, directory: Path) -> bool:
        if directory not in self._submodules:
            ref = default_branch(root)
            relative = directory.relative_to(root).as_posix()
            entry = _git(root, "ls-tree", ref, "--", relative) if ref else None
            self._submodules[directory] = bool(entry) and str(entry).startswith(_GITLINK)
        return self._submodules[directory]

    def read_text(self, path: Path) -> str | None:
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None

    def merge_base(self, checkout: Path, relative: str) -> records.Before | None:
        ref = default_branch(checkout)
        commit = _git(checkout, "merge-base", ref, "HEAD") if ref else None
        if not commit:
            return None
        return records.before(lambda args: _output(checkout, *args), commit, relative)

    def glob(self, pattern: Path) -> list[Path]:
        anchor = Path(pattern.anchor)
        shell = re.sub(r"\*\*+", "*", str(pattern.relative_to(anchor)))
        return sorted(anchor.glob(shell))

    def expand(self, word: str) -> str:
        return os.path.expandvars(Path(word).expanduser())


def _run(
    cwd: Path, *args: str, timeout: float = GIT_TIMEOUT_SECONDS
) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(  # noqa: S603 # -- a fixed argv, no shell
            ["git", "-C", str(cwd), *args],  # noqa: S607 # -- git is resolved from PATH on purpose
            capture_output=True,
            text=True,
            timeout=timeout,  # pragma: no mutate # -- only bounds a hung git
            check=False,  # pragma: no mutate # -- the caller reads the exit status
        )
    except (OSError, subprocess.SubprocessError):
        return None


def _output(cwd: Path, *args: str, timeout: float = GIT_TIMEOUT_SECONDS) -> str | None:
    completed = _run(cwd, *args, timeout=timeout)
    return completed.stdout if completed is not None and completed.returncode == 0 else None


def _git(cwd: Path, *args: str, timeout: float = GIT_TIMEOUT_SECONDS) -> str | None:
    output = _output(cwd, *args, timeout=timeout)
    return None if output is None else output.strip()


_HEADS = "refs/heads/"
_GITLINK = "160000 commit "
_TRACKING = f"refs/remotes/{REMOTE}/"
_NOT_SYMBOLIC = 1


def _head(root: Path, timeout: float = GIT_TIMEOUT_SECONDS) -> str | None:
    completed = _run(root, "symbolic-ref", "-q", "HEAD", timeout=timeout)
    if completed is None:
        return None
    if completed.returncode == _NOT_SYMBOLIC:
        return DETACHED
    head = completed.stdout.strip() if completed.returncode == 0 else ""
    return head.removeprefix(_HEADS) or None


def _awaiting(path: Path, tree: Path) -> str | None:
    try:
        note: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if (
        not isinstance(note, dict)
        or not note.get("start")
        or (note.get("passed") is True and not note.get("rebasing"))
    ):
        return None
    where, branch = note.get("worktree"), note.get("branch")
    if (
        isinstance(where, str)
        and isinstance(branch, str)
        and Path(where).resolve() == tree.resolve()
    ):
        return branch
    return None


class GitRepoState:
    def __init__(self) -> None:
        self._heads: dict[Path, str | None] = {}
        self._defaults: dict[Path, frozenset[str]] = {}

    def current_branch(self, cwd: Path) -> str | None:
        head = _git(cwd, "symbolic-ref", "HEAD")
        return head.removeprefix(_HEADS) if head else None

    def checked_out(self, root: Path) -> str | None:
        if root not in self._heads:
            self._heads[root] = _head(root)
        return self._heads[root]

    def default_branches(self, root: Path) -> frozenset[str]:
        if root not in self._defaults:
            name = (default_branch(root) or "").removeprefix(_TRACKING).removeprefix(_HEADS)
            self._defaults[root] = MAIN_BRANCHES | ({name} if name else frozenset())
        return self._defaults[root]

    def default_file(self, root: Path, relative: str) -> str | None:
        ref = default_branch(root)
        return None if ref is None else _output(root, "show", f"{ref}:{relative}")

    def awaiting_final(self, cwd: Path) -> str | None:
        tree = _git(cwd, "rev-parse", "--show-toplevel")
        common = _git(cwd, "rev-parse", "--path-format=absolute", "--git-common-dir")
        if not tree or not common:
            return None
        here = self.current_branch(cwd)
        drained = Path(common).parent / ".ratchet" / "queue" / "tickets"
        alone = Path(common) / events.FEATURE_STARTS
        for directory, one_ticket in ((drained, False), (alone, True)):
            for path in sorted(directory.glob("*.json")):
                branch = _awaiting(path, Path(tree))
                if branch is not None and not (one_ticket and branch != here):
                    return branch
        return None

    def head_is_pushed(self, cwd: Path) -> bool | None:
        listed = _git(cwd, "branch", "--remotes", "--contains")
        return None if listed is None else bool(listed)

    def full_name(self, cwd: Path, name: str) -> str | None:
        return _git(cwd, "rev-parse", "--verify", "--quiet", "--symbolic-full-name", name) or None


def _unreadable(detail: str) -> Verdict:
    return Verdict("unreadable", detail)


def is_antigravity(payload: object) -> bool:
    return isinstance(payload, dict) and ANTIGRAVITY_CALL in payload


def _claude_args(args: dict[str, object]) -> dict[str, object]:
    renamed = {ANTIGRAVITY_ARGS.get(key, key): value for key, value in args.items()}
    chunks = args.get(ANTIGRAVITY_CHUNKS)
    if isinstance(chunks, list):
        renamed["edits"] = [_claude_args(chunk) for chunk in chunks if isinstance(chunk, dict)]
    return renamed


def from_antigravity(payload: dict[str, object]) -> dict[str, object]:
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
    if verdict.rule in OWNER_ASKS:
        return f"Ratchet: {verdict.because} ({verdict.detail}). {verdict.way_out}"
    stop = "" if verdict.detail.endswith(".") else "."
    if verdict.source:
        stop += f" {verdict.source}"
    if verdict.decision is Decision.DENY:
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
    payload, verdict = _judged(text, make_guard)
    return respond(verdict, antigravity=is_antigravity(payload))


def _judged(text: str, make_guard: Callable[[], Guard]) -> tuple[object, Verdict | None]:
    payload: object = None
    try:
        payload = json.loads(text)
        verdict = decide(payload, make_guard())
    except Exception as error:  # noqa: BLE001 # -- fail closed: any failure must reach a human
        verdict = _unreadable(f"{type(error).__name__}: {error}")
    return payload, verdict


def common_directory(root: Path) -> Path | None:
    common = _git(root, "rev-parse", "--git-common-dir", timeout=LOG_TIMEOUT_SECONDS)
    return root / common if common else None


def _log(root: Path, payload: object, verdict: Verdict) -> None:
    deadline = time.monotonic() + LOG_TIMEOUT_SECONDS
    common = common_directory(root)
    if common is None:
        return
    tool = None
    if isinstance(payload, dict):
        call = from_antigravity(payload) if is_antigravity(payload) else payload
        tool = call.get("tool_name")
    event = events.Event(
        at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        decision=verdict.decision.name.lower(),
        tool=tool if isinstance(tool, str) else None,
        rule=verdict.rule,
        detail=verdict.detail,
        branch=_head(root, max(deadline - time.monotonic(), 0)),
    )
    events.append(common / events.LOG_NAME, event.line())


def repository_root(path: Path) -> Path:
    return next((c for c in (path, *path.parents) if (c / ".git").exists()), path)


def read_manifest(root: Path, filesystem: LocalFilesystem | None = None) -> Manifest:
    filesystem = filesystem or LocalFilesystem()
    checkout = filesystem.checkout(filesystem.canonical(root))
    manifest = filesystem.committed(checkout or root)
    return EMPTY if manifest is None else manifest


def standard_manifest(plugin_root: Path) -> Manifest:
    try:
        return parse((plugin_root / STANDARD_MANIFEST).read_text(encoding="utf-8"))
    except OSError:
        return EMPTY


def local_guard(root: Path, plugin_root: Path = PLUGIN_ROOT, project: Path | None = None) -> Guard:
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
    payload, verdict = _judged(stdin.read(), lambda: local_guard(root, project=project))
    answer = respond(verdict, antigravity=is_antigravity(payload))
    if answer:
        stdout.write(answer + "\n")
        stdout.flush()
    if verdict is not None:
        with contextlib.suppress(Exception):
            _log(root, payload, verdict)
    return 0
