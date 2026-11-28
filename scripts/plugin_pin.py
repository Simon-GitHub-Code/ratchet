#!/usr/bin/env python3
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
import markdown_text

PLUGIN = Path(__file__).resolve().parents[1]
MANIFEST = Path(".claude-plugin/plugin.json")
COMMIT_FILE = Path(".claude-plugin/commit")
COMMIT = re.compile(r"[0-9a-f]{40}")
PIN = re.compile(
    r"^- Ratchet plugin: (\S+) at ([0-9a-f]{40})(?: tree ([0-9a-f]{40}))?$", re.MULTILINE
)
PIN_LINE = re.compile(r"^- Ratchet plugin: \S+ at \S+(?: tree \S+)?[ \t]*(?=\r?$)", re.MULTILINE)
PLUGIN_REF = re.compile(r'repository:\s*\S*ratchet\S*?[,\s]+ref:\s*"?([^",\s}]+)')
CHECKED = "RATCHET_PLUGIN_CHECKED"
RUNNER = "## Runner"
WORKFLOWS = Path(".github/workflows")
UNREADABLE = "a commit git cannot read"
EXCLUDED_PATHS = frozenset(
    {
        ".git",
        ".claude-plugin/commit",
        ".in_use",
        ".orphaned_at",
        "package-lock.json",
        "hooks.json",
        "plugin.json",
        "hooks/hooks.json",
        "skills/init/templates/CLAUDE.md",
    }
)
DOTTED = re.compile(r"\d+(?:\.\d+)*")
NONE_RECORDED = (
    "plugin-pin: no plugin recorded for this repository: start an agent session here once, or set"
    " RATCHET_PLUGIN to the plugin's folder"
)


class Relation(Enum):
    UNPINNED = "unpinned"
    PINNED = "pinned"
    NEWER = "newer"
    OLDER = "older"
    UNORDERED = "unordered"


@dataclass(frozen=True)
class Pin:
    version: str
    commit: str
    tree: str | None = None


def pinned(binding: str | None) -> Pin | None:
    match = PIN.search(binding or "")
    return Pin(*match.groups()) if match else None


def version_of(plugin: Path) -> str | None:
    try:
        data = json.loads(gatelib.read_text(plugin / MANIFEST) or "")
    except ValueError:
        return None
    version = data.get("version") if isinstance(data, dict) else None
    return version if isinstance(version, str) else None


def release_commit(recorded: str | None) -> str | None:
    if recorded is None:
        return None
    text = recorded.removesuffix("\n")
    return text if COMMIT.fullmatch(text) else UNREADABLE


def git_output(plugin: Path, *args: str) -> str | None:
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
    folder = os.environ.get(CHECKED, "")[42:]
    return bool(folder) and os.path.realpath(folder) == os.path.realpath(plugin)


def working_tree_clean(plugin: Path) -> bool:
    status = git_output(plugin, "status", "--porcelain", "--no-renames", "-z")
    if status is None:
        return False
    return all(entry[3:] in EXCLUDED_PATHS for entry in status.split("\0") if entry)


def commit_of(plugin: Path) -> str | None:
    if checked_folder(plugin) or not (plugin / ".git").exists():
        return release_commit(gatelib.read_text(plugin / COMMIT_FILE))
    head = git_output(plugin, "rev-parse", "HEAD")
    if head is None:
        return UNREADABLE
    if git_output(plugin, "ls-tree", "--name-only", "HEAD", "--", str(COMMIT_FILE)):
        recorded = release_commit(git_output(plugin, "show", f"HEAD:{COMMIT_FILE}"))
        parents = (git_output(plugin, "rev-list", "--parents", "-n", "1", "HEAD") or "").split()
        parent = parents[1:]
        if working_tree_clean(plugin) and len(parent) == 1 and recorded in (UNREADABLE, parent[0]):
            return recorded
    return head.strip()


@dataclass(frozen=True)
class TreeFolder:
    dirs: dict[str, TreeFolder]
    files: dict[str, tuple[bytes, bytes]]


def object_id(kind: bytes, body: bytes) -> bytes:
    return hashlib.sha1(b"%s %d\0" % (kind, len(body)) + body, usedforsecurity=False).digest()


def excluded(path: str) -> bool:
    prefix = ""
    for part in path.split("/"):
        prefix = f"{prefix}/{part}" if prefix else part
        if part == "__pycache__" or prefix in EXCLUDED_PATHS:
            return True
    return False


def tree_digest(entries: list[tuple[bytes, bytes, bytes]]) -> bytes:
    body = b"".join(
        kind + b" " + name.removesuffix(b"/") + b"\0" + digest
        for name, kind, digest in sorted(entries)
    )
    return object_id(b"tree", body)


def tree_of(folder: Path, relative: str) -> bytes | None:
    entries = []
    for entry in folder.iterdir():
        name = entry.name
        path = f"{relative}{name}"
        if path in EXCLUDED_PATHS or name == "__pycache__":
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
    return tree_digest(entries) if entries else None


def hash_folder(folder: TreeFolder) -> bytes:
    entries = [
        *(
            (os.fsencode(name) + b"/", b"40000", hash_folder(sub))
            for name, sub in folder.dirs.items()
        ),
        *((os.fsencode(name), mode, digest) for name, (mode, digest) in folder.files.items()),
    ]
    return tree_digest(entries)


def tree_from_listing(listing: str) -> bytes:
    root = TreeFolder({}, {})
    for record in listing.split("\0"):
        if not record:
            continue
        header, _, path = record.partition("\t")
        if excluded(path):
            continue
        mode, _, sha = header.split(" ")
        folder = root
        *parents, leaf = path.split("/")
        for part in parents:
            folder = folder.dirs.setdefault(part, TreeFolder({}, {}))
        folder.files[leaf] = (mode.encode("ascii"), bytes.fromhex(sha))
    return hash_folder(root)


def copy_tree_id(plugin: Path) -> str | None:
    if not plugin.is_dir():
        return None
    try:
        tree = tree_of(plugin, "")
    except OSError:
        return None
    return (tree or object_id(b"tree", b"")).hex()


def release_tree(plugin: Path) -> str | None:
    if checked_folder(plugin) or not (plugin / ".git").exists():
        return copy_tree_id(plugin)
    commit = commit_of(plugin)
    if commit in (None, UNREADABLE):
        return None
    listing = git_output(plugin, "ls-tree", "-r", "-z", commit)
    return tree_from_listing(listing).hex() if listing is not None else None


def line_for(plugin: Path) -> str:
    version = version_of(plugin) or "<version>"
    commit, tree = commit_of(plugin), release_tree(plugin)
    if commit in (None, UNREADABLE):
        commit = f"<the full commit of {version}>"
    return f"- Ratchet plugin: {version} at {commit} tree {tree or f'<the tree of {version}>'}"


def refs(text: str) -> set[str]:
    return set(PLUGIN_REF.findall(text))


def with_refs(commit: str) -> gatelib.Want:
    def set_ref(match: re.Match[str]) -> str:
        whole, start = match.group(0), match.start()
        return whole[: match.start(1) - start] + commit + whole[match.end(1) - start :]

    return lambda text: PLUGIN_REF.sub(set_ref, text or "")


def with_pin(line: str) -> gatelib.Want:
    def pin(text: str | None) -> str:
        text = text or ""
        if PIN_LINE.search(text):
            return PIN_LINE.sub(lambda _: line, text)
        lines = text.split("\n")
        heading = next(
            (i for i, held in enumerate(lines) if markdown_text.strip_comment(held) == RUNNER),
            None,
        )
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
    commits = {pin.commit} if (pin := pinned(gatelib.read_text(root / gatelib.BINDING))) else set()
    for workflow in sorted((root / WORKFLOWS).glob("*.yml")):
        commits.update(
            ref for ref in refs(gatelib.read_text(workflow) or "") if COMMIT.fullmatch(ref)
        )
    return commits


def write_refusal(root: Path, plugin: Path) -> str | None:
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
    return f"python3 {shlex.quote(str(plugin / 'scripts/upgrade.py'))} --apply"


def in_use(plugin: Path) -> str:
    version, commit = version_of(plugin), commit_of(plugin)
    return (version or "an unknown version") + (f" at {commit}" if commit else "")


def newer(used: str | None, pinned: str) -> bool | None:
    if used is None or not DOTTED.fullmatch(used) or not DOTTED.fullmatch(pinned):
        return None
    left, right = (tuple(int(part) for part in v.split(".")) for v in (used, pinned))
    return None if left == right else left > right


def relation(pin: Pin | None, plugin: Path) -> Relation:
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
