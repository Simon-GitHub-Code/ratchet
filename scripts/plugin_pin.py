#!/usr/bin/env python3
"""The plugin pin: a tier runs from the plugin version the binding names, or it is red.

CI checks the plugin out at one commit. A local run takes it from wherever the harness keeps
it, and that copy moves whenever the harness updates the plugin, so the two could run different
gates with nothing saying so. The version is therefore one fact with one home, a line of
``docs/process/binding.md``::

    - Ratchet plugin: 0.9.0 at <the 40-character commit> tree <the 40-character tree id>

The tree id is the commit's ``<commit>^{tree}``. The conformance gate holds every workflow's plugin
checkout to the commit. CI holds the pin's tree id to the release's: the pin's side cannot be
forged, since the checkout there comes from the forge (ADR 0068). The template justfile, before any
recipe runs anything from the plugin, computes the tree id of the copy's files in memory, as
``git write-tree`` would, less the release file, ``.git``, ``__pycache__``, and the harness's
``.in_use`` and ``.orphaned_at``, and runs the copy only when it is the pin's. This script holds
the same hash again.

Usage: ``plugin_pin.py [--root DIR] [--plugin DIR]``. The plugin is the one this script runs
from unless ``--plugin`` names another. A ``--plugin`` that is empty or names nothing is red with
one line: no plugin is recorded for the repository (the session hook records it, ADR 0068), and
``RATCHET_PLUGIN`` names one instead.

* A plugin whose files hash to the pin's tree id is green. Its commit and version are printed
  from the release file and the manifest, and no git command runs in it when a tier's ``just``
  has hashed it (``RATCHET_PLUGIN_CHECKED``).
* A plugin whose files hash to another tree is red, and the red names the way out:
  ``python3 <plugin>/scripts/upgrade.py --apply`` when the plugin is newer than the pin, which
  moves the pin, every workflow ``ref`` and the permission rules to it in one change, or a
  checkout of the pinned release, by setting ``RATCHET_PLUGIN`` to it. The binding and the
  workflows are guard paths, so the owner sees an upgrade.
* A plugin older than the pin, or whose order against the pin cannot be told, is red too.

Exit status: 0 green, 1 red.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import stat
import subprocess
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import gatelib

PLUGIN = Path(__file__).resolve().parents[1]
MANIFEST = Path(".claude-plugin/plugin.json")
COMMIT_FILE = Path(".claude-plugin/commit")
COMMIT = re.compile(r"[0-9a-f]{40}")
PIN = re.compile(
    r"^- Ratchet plugin: (\S+) at ([0-9a-f]{40})(?: tree ([0-9a-f]{40}))?$", re.MULTILINE
)
# Why: a line that only starts like the pin, such as a note on where the plugin comes from, is
# the project's own; only a line of the pin's shape, real or placeholder, is the pin.
PIN_LINE = re.compile(r"^- Ratchet plugin: \S+ at \S+(?: tree \S+)?[ \t]*(?=\r?$)", re.MULTILINE)
PLUGIN_REF = re.compile(r'repository:\s*\S*ratchet\S*?[,\s]+ref:\s*"?([^",\s}]+)')
CHECKED = "RATCHET_PLUGIN_CHECKED"
RUNNER = "## Runner"
WORKFLOWS = Path(".github/workflows")
UNREADABLE = "a commit git cannot read"
EXCLUDED_AT_ROOT = frozenset({".git", ".claude-plugin/commit", ".in_use", ".orphaned_at"})
DOTTED = re.compile(r"\d+(?:\.\d+)*")
NONE_RECORDED = (
    "plugin-pin: no plugin recorded for this repository: start an agent session here once, or set"
    " RATCHET_PLUGIN to the plugin's folder"
)


class Relation(Enum):
    """Where the plugin in use stands against the pin."""

    UNPINNED = "unpinned"
    PINNED = "pinned"
    NEWER = "newer"
    OLDER = "older"
    UNORDERED = "unordered"


@dataclass(frozen=True)
class Pin:
    """The plugin version a repository's gates run at."""

    version: str
    commit: str
    tree: str | None = None


def pinned(binding: str | None) -> Pin | None:
    """The pin a binding carries, or ``None`` while the line is absent or a placeholder."""
    match = PIN.search(binding or "")
    return Pin(*match.groups()) if match else None


def version_of(plugin: Path) -> str | None:
    """The version in the plugin's manifest, or ``None`` when there is none to read."""
    try:
        data = json.loads(gatelib.read_text(plugin / MANIFEST) or "")
    except ValueError:
        return None
    version = data.get("version") if isinstance(data, dict) else None
    return version if isinstance(version, str) else None


def release_commit(recorded: str | None) -> str | None:
    """The commit a release file holds: ``None`` for no file, ``UNREADABLE`` for another content."""
    if recorded is None:
        return None
    text = recorded.removesuffix("\n")
    return text if COMMIT.fullmatch(text) else UNREADABLE


def git_output(plugin: Path, *args: str) -> str | None:
    # Why: git exports GIT_DIR to a hook, and `git -C` obeys it over the directory it is given.
    env = {name: value for name, value in os.environ.items() if not name.startswith("GIT_")}
    result = subprocess.run(
        ["git", "-C", str(plugin), *args],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    return result.stdout if result.returncode == 0 else None


def checked_folder(plugin: Path) -> bool:
    """Whether a tier's ``just`` hashed ``plugin`` and found the tree the binding pins."""
    folder = os.environ.get(CHECKED, "")[42:]
    return bool(folder) and os.path.realpath(folder) == os.path.realpath(plugin)


def commit_of(plugin: Path) -> str | None:
    """The commit a plugin is at.

    A copy with ``.claude-plugin/commit`` is a release (ADR 0068), and the commit is the one that
    file names. A clone is a release only while ``HEAD`` is the release commit itself, a single
    commit on the one the file names, and nothing in the work tree differs from it. Otherwise the
    checkout's ``HEAD``, or ``None`` for a copy with no file. ``UNREADABLE`` when the file holds
    anything but a commit. The commit says where a plugin came from, never what it holds: only
    ``copy_tree_id`` does.

    Why: a folder a tier's ``just`` has hashed runs no git command, since its ``.git`` is left out
    of the hash and its config can name a command that ``git status`` runs.
    """
    if checked_folder(plugin) or not (plugin / ".git").exists():
        return release_commit(gatelib.read_text(plugin / COMMIT_FILE))
    head = git_output(plugin, "rev-parse", "HEAD")
    if head is None:
        return UNREADABLE
    if git_output(plugin, "ls-tree", "--name-only", "HEAD", "--", str(COMMIT_FILE)):
        recorded = release_commit(git_output(plugin, "show", f"HEAD:{COMMIT_FILE}"))
        parents = (git_output(plugin, "rev-list", "--parents", "-n", "1", "HEAD") or "").split()
        clean = git_output(plugin, "status", "--porcelain") == ""
        parent = parents[1:]
        if clean and len(parent) == 1 and recorded in (UNREADABLE, parent[0]):
            return recorded
    return head.strip()


def object_id(kind: bytes, body: bytes) -> bytes:
    return hashlib.sha1(b"%s %d\0" % (kind, len(body)) + body, usedforsecurity=False).digest()


def tree_of(folder: Path, relative: str) -> bytes | None:
    """The id of the git tree of ``folder``, built in memory; ``None`` when nothing is in it."""
    entries = []
    for entry in folder.iterdir():
        name = entry.name
        path = f"{relative}{name}"
        if path in EXCLUDED_AT_ROOT or name == "__pycache__":
            continue
        full = entry
        mode = full.lstat().st_mode
        if stat.S_ISLNK(mode):
            entries.append(
                (os.fsencode(name), b"120000", object_id(b"blob", os.fsencode(full.readlink())))
            )
        elif stat.S_ISDIR(mode):
            if (sub := tree_of(full, f"{path}/")) is not None:
                entries.append((os.fsencode(name) + b"/", b"40000", sub))
        elif stat.S_ISREG(mode):
            kind = b"100755" if mode & stat.S_IXUSR else b"100644"
            entries.append((os.fsencode(name), kind, object_id(b"blob", full.read_bytes())))
    if not entries:
        return None
    body = b"".join(
        kind + b" " + name.removesuffix(b"/") + b"\0" + digest
        for name, kind, digest in sorted(entries)
    )
    return object_id(b"tree", body)


def copy_tree_id(plugin: Path) -> str | None:
    """The tree id of a copy's files, as the template justfile computes it, or ``None``.

    Why: hashed in memory as git hashes a tree, with the conversions off, so the id is the same on
    every machine, equals ``git write-tree`` of the same files, and nothing is stored anywhere.
    The release file, ``.git``, ``.in_use``, ``.orphaned_at`` and each ``__pycache__`` are left out.
    """
    if not plugin.is_dir():
        return None
    try:
        tree = tree_of(plugin, "")
    except OSError:
        return None
    return (tree or object_id(b"tree", b"")).hex()


def release_tree(plugin: Path) -> str | None:
    """The tree id the pin of the commit ``plugin`` is at holds, or of a copy's files."""
    if checked_folder(plugin) or not (plugin / ".git").exists():
        return copy_tree_id(plugin)
    commit = commit_of(plugin)
    if commit in (None, UNREADABLE):
        return None
    tree = git_output(plugin, "rev-parse", f"{commit}^{{tree}}")
    return tree.strip() if tree else None


def line_for(plugin: Path) -> str:
    """The binding's pin line naming ``plugin``, with a stand-in for what the copy cannot say."""
    version = version_of(plugin) or "<version>"
    commit, tree = commit_of(plugin), release_tree(plugin)
    if commit in (None, UNREADABLE):
        commit = f"<the full commit of {version}>"
    return f"- Ratchet plugin: {version} at {commit} tree {tree or f'<the tree of {version}>'}"


def refs(text: str) -> set[str]:
    """The values of the plugin-checkout ``ref:`` in a workflow, placeholders included."""
    return set(PLUGIN_REF.findall(text))


def with_refs(commit: str) -> gatelib.Want:
    """The want that sets every plugin-checkout ``ref:`` to ``commit`` and changes nothing else."""

    def set_ref(match: re.Match[str]) -> str:
        whole, start = match.group(0), match.start()
        return whole[: match.start(1) - start] + commit + whole[match.end(1) - start :]

    return lambda text: PLUGIN_REF.sub(set_ref, text or "")


def with_pin(line: str) -> gatelib.Want:
    """The want that makes ``line`` the binding's pin line: replaced where it stands, else added."""

    def pin(text: str | None) -> str:
        text = text or ""
        if PIN_LINE.search(text):
            return PIN_LINE.sub(lambda _: line, text)
        lines = text.split("\n")
        heading = next((i for i, held in enumerate(lines) if held.rstrip() == RUNNER), None)
        if heading is None:
            kept = text if text.endswith("\n") or not text else text + "\n"
            gap = "\n" if kept else ""
            return f"{kept}{gap}{RUNNER}\n\n{line}\n"
        end = heading + 1
        while end < len(lines) and not lines[end].strip():
            end += 1
        while end < len(lines) and lines[end].strip() and not lines[end].startswith("## "):
            end += 1
        return "\n".join([*lines[:end], "", line, *lines[end:]])

    return pin


def ci_commits(root: Path) -> set[str]:
    """The commits CI checks the plugin out at: the pin's and each full ``ref`` of a workflow."""
    commits = {pin.commit} if (pin := pinned(gatelib.read_text(root / gatelib.BINDING))) else set()
    for workflow in sorted((root / WORKFLOWS).glob("*.yml")):
        commits.update(
            ref for ref in refs(gatelib.read_text(workflow) or "") if COMMIT.fullmatch(ref)
        )
    return commits


def write_refusal(root: Path, plugin: Path) -> str | None:
    """Why ``plugin`` must not write a file CI reads, or ``None`` when it may."""
    commits = ci_commits(root)
    if not commits:
        return None
    used = commit_of(plugin)
    pin = pinned(gatelib.read_text(root / gatelib.BINDING))
    if commits == {used} or (
        len(commits) == 1 and used is None and pin and version_of(plugin) == pin.version
    ):
        return None
    if len(commits) > 1:
        named = ", ".join(sorted(commit[:12] for commit in commits))
        return (
            f"the pin and the workflows name {len(commits)} plugin commits ({named}), so an"
            " upgrade is half done and a file written now would suit one of them only. Finish it"
            f" with `{upgrade_command(plugin)}`, or point RATCHET_PLUGIN at a checkout of the"
            " commit CI should run"
        )
    (commit,) = commits
    return (
        f"CI runs the plugin at {commit[:12]} and this one is {in_use(plugin)}, so a file it"
        " writes may be one CI rejects. Run it from a checkout of that commit, by pointing"
        " RATCHET_PLUGIN at it, or move the pin, the workflow refs and this file together with"
        f" `{upgrade_command(plugin)}`"
    )


def upgrade_command(plugin: Path) -> str:
    """The command that moves a repository to ``plugin``: the pin, the workflow refs, the rules."""
    return f"python3 {shlex.quote(str(plugin / 'scripts/upgrade.py'))} --apply"


def in_use(plugin: Path) -> str:
    """``plugin`` as a line names it: its version, and its commit when it has one."""
    version, commit = version_of(plugin), commit_of(plugin)
    return (version or "an unknown version") + (f" at {commit}" if commit else "")


def newer(used: str | None, pinned: str) -> bool | None:
    """Whether version ``used`` is newer than ``pinned``; ``None`` when that cannot be told."""
    if used is None or not DOTTED.fullmatch(used) or not DOTTED.fullmatch(pinned):
        return None
    left, right = (tuple(int(part) for part in v.split(".")) for v in (used, pinned))
    return None if left == right else left > right


def relation(pin: Pin | None, plugin: Path) -> Relation:
    """Where ``plugin`` stands against ``pin``."""
    if pin is None:
        return Relation.UNPINNED
    version, commit = version_of(plugin), commit_of(plugin)
    if version == pin.version and commit in (None, pin.commit):
        return Relation.PINNED
    order = None if commit == UNREADABLE else newer(version, pin.version)
    if order is None:
        return Relation.UNORDERED
    return Relation.NEWER if order else Relation.OLDER


def tree_problem(pin: Pin, plugin: Path) -> str | None:
    """Why ``plugin`` is not the release the pin's tree id names, or ``None`` when it is."""
    home, apply = gatelib.BINDING, upgrade_command(plugin)
    if pin.tree is None:
        return (
            f"{home} pins no tree id, so nothing holds the plugin at {plugin} to the release:"
            f" run `{apply}`, which writes `{line_for(plugin)}`"
        )
    committed = release_tree(plugin)
    if committed != pin.tree:
        return (
            f"the plugin at {plugin} is at a commit whose tree is {committed or 'unreadable'},"
            f" not the {pin.tree} that {home} pins: the pin names no release. Run `{apply}`"
            " from the release it should name"
        )
    held = copy_tree_id(plugin)
    if held != pin.tree:
        return (
            f"the plugin at {plugin} is tree {held or 'unreadable'}, not the {pin.tree} that"
            f" {home} pins: it is not the pinned release. Install the pinned release, or"
            f" upgrade this project with `{apply}`"
        )
    return None


def judged(pin: Pin | None, plugin: Path) -> tuple[str, bool]:
    """The line to print about ``plugin`` against ``pin``, and whether it is a problem."""
    version, commit = version_of(plugin), commit_of(plugin)
    used = in_use(plugin)
    home = gatelib.BINDING
    apply = upgrade_command(plugin)
    standing = relation(pin, plugin)
    if pin is None:
        absent = f"{home}: no `- Ratchet plugin: <version> at <commit>` line"
        return (
            f"{absent}, so nothing says which plugin the gates run at; this run uses {used}",
            True,
        )
    if standing is Relation.PINNED:
        problem = tree_problem(pin, plugin)
        compared = "this copy carries no commit, and its tree is the pinned one"
        at = f" at {commit[:12]}" if commit else ""
        green = f"plugin-pin: {version}{at}, as {home} pins" + ("" if commit else f"; {compared}")
        return (problem or green), problem is not None
    if standing is Relation.NEWER:
        return (
            (
                f"the plugin at {plugin} is {used}, newer than the {pin.version} that {home} pins,"
                f" so this run would judge by {version}'s gates and CI by {pin.version}'s. Run"
                f" `{apply}`: it moves the pin in {home} to `{line_for(plugin)}`, every workflow"
                " `ref` and the permission rules together, and lists what it leaves to an agent"
                " or to you"
            ),
            True,
        )
    pinned = f"{pin.version} at {pin.commit}"
    if standing is Relation.OLDER:
        return (
            (
                f"the plugin at {plugin} is {used}, older than the {pinned} that {home} pins, so a"
                " local tier would run older gates than CI. Bring the plugin up to date, or point"
                " RATCHET_PLUGIN at a checkout of the pinned commit."
            ),
            True,
        )
    return (
        (
            f"the plugin at {plugin} is {used}; {home} pins {pinned}, and which is newer cannot be"
            " told. Either set RATCHET_PLUGIN to a checkout of the pinned commit, or, where the"
            f" plugin in use is the newer, set the pin line to `{line_for(plugin)}` and run"
            f" `{apply}`, which moves every workflow `ref` and the permission rules to it."
        ),
        True,
    )


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="plugin_pin.py")
    parser.add_argument("--root", type=Path, default=Path())
    parser.add_argument("--plugin", default=str(PLUGIN))
    options = parser.parse_args(argv[1:])
    if not options.plugin or not Path(options.plugin).exists():
        return gatelib.report("plugin-pin", [NONE_RECORDED])
    plugin = Path(options.plugin)
    print(f"plugin-pin: using {in_use(plugin)}, in {plugin}")
    line, problem = judged(pinned(gatelib.read_text(options.root / gatelib.BINDING)), plugin)
    if not problem:
        print(line)
    return gatelib.report("plugin-pin", [line] if problem else [])


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
