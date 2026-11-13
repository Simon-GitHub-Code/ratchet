"""The manifest the guards come from: an agent can neither lose it nor shrink it.

A default branch that held ``guards/manifest.toml`` and no longer does locks every guard path its
last manifest named; only one that never held one is not adopted yet, and the adoption writes with
no prompt. On every branch the manifest itself is locked against a deletion, a rename and any
change but an addition by Edit or Write: a path at the end of a group's or a door's paths, or a
whole group or door after the ones the default branch holds. A path added to a group that also
matches a pattern of a group below it would take paths from that group, and is no addition.
"""

import io
import json
from pathlib import Path

import pytest

from ratchet_guards import hook
from tests.support import PLUGIN_ROOT
from tests.test_hook import _git, checkout, switch_to_a_branch

TEMPLATE = (PLUGIN_ROOT / "skills/adopt/templates/guards/manifest.toml").read_text(encoding="utf-8")
MANIFEST = "guards/manifest.toml"
GONE = (
    "guards/manifest.toml is gone from the default branch, which held it, so every guard path it "
    "named is locked until the owner puts it back."
)
COMMITTED = "Guard paths come from guards/manifest.toml"
EXTRA_GROUP = '\n[[group]]\nname = "extra"\nreason = "More guard paths."\npaths = ["extra/**"]\n'
EXTRA_DOOR = '\n[[door]]\nname = "schema"\nreason = "A schema ships once."\npaths = ["db/**"]\n'


def answer(root: Path, tool: str, **tool_input: str) -> dict[str, str]:
    """What the hook prints for one call in a session at ``root``, or ``{}``."""
    case = {"tool_name": tool, "tool_input": tool_input, "cwd": str(root)}
    stdout = io.StringIO()
    assert hook.main(root, io.StringIO(json.dumps(case)), stdout, project=root) == 0
    return json.loads(stdout.getvalue())["hookSpecificOutput"] if stdout.getvalue() else {}


def write(root: Path, path: str, content: str = "x\n") -> dict[str, str]:
    return answer(root, "Write", file_path=str(root / path), content=content)


def shell(root: Path, command: str) -> dict[str, str]:
    return answer(root, "Bash", command=command)


def commit_all(root: Path, subject: str) -> None:
    _git(root, "add", "-A")
    _git(root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", subject)


@pytest.fixture
def adopted(tmp_path: Path) -> Path:
    """A checkout whose default branch holds the template manifest, on a branch."""
    root = checkout(tmp_path, manifest=TEMPLATE)
    switch_to_a_branch(root)
    return root


@pytest.fixture
def vanished(tmp_path: Path) -> Path:
    """A checkout whose default branch held two manifests, then lost the file, on a branch."""
    root = checkout(tmp_path, manifest=TEMPLATE)
    (root / MANIFEST).write_text(TEMPLATE + EXTRA_GROUP, encoding="utf-8")
    commit_all(root, "more guard paths")
    _git(root, "rm", "-q", MANIFEST)
    commit_all(root, "no manifest")
    switch_to_a_branch(root)
    return root


@pytest.mark.parametrize("path", ["justfile", ".github/workflows/ci.yml", "extra/x", "guards/x"])
def test_a_manifest_gone_from_the_default_branch_locks_every_path_it_named(
    vanished: Path, path: str
) -> None:
    said = write(vanished, path)
    assert said["permissionDecision"] == "deny"
    assert f": {path} is in guard group" in said["permissionDecisionReason"]
    assert GONE in said["permissionDecisionReason"]
    assert COMMITTED not in said["permissionDecisionReason"]


def test_a_manifest_gone_from_the_default_branch_locks_its_paths_from_the_shell(
    vanished: Path,
) -> None:
    said = shell(vanished, "cp README.md justfile")
    assert said["permissionDecision"] == "deny"
    assert f"justfile (gate-commands). {GONE}" in said["permissionDecisionReason"]
    assert write(vanished, "src/app.py") == {}


def test_a_default_branch_that_never_held_a_manifest_is_not_adopted_yet(tmp_path: Path) -> None:
    root = checkout(tmp_path, manifest=False)
    switch_to_a_branch(root)
    (root / "guards").mkdir()
    (root / MANIFEST).write_text(TEMPLATE, encoding="utf-8")
    commit_all(root, "the adoption, on its branch")
    for path in ("justfile", ".claude/settings.json", ".github/workflows/ci.yml", "AGENTS.md"):
        assert write(root, path) == {}
    assert write(root, MANIFEST, TEMPLATE + EXTRA_GROUP) == {}
    assert shell(root, f"git rm -q {MANIFEST}") == {}


@pytest.mark.parametrize(
    "command",
    [
        f"git rm -q {MANIFEST}",
        f"rm {MANIFEST}",
        f"rm -r {Path(MANIFEST).parent}",
        f"mv {MANIFEST} guards/old.toml",
        f"git mv {MANIFEST} guards/old.toml",
        f"printf '' > {MANIFEST}",
        f"cat extra.toml >> {MANIFEST}",
    ],
)
def test_the_manifest_cannot_be_deleted_renamed_or_written_from_the_shell_on_a_branch(
    adopted: Path, command: str
) -> None:
    said = shell(adopted, command)
    assert said["permissionDecision"] == "deny"
    assert "(manifest-file)" in said["permissionDecisionReason"]
    assert COMMITTED not in said["permissionDecisionReason"]


def test_a_patch_cannot_delete_or_change_the_manifest_on_a_branch(adopted: Path) -> None:
    for verb in ("Delete File", "Update File"):
        patch = f"*** Begin Patch\n*** {verb}: {MANIFEST}\n*** End Patch\n"
        said = answer(adopted, "apply_patch", input=patch)
        assert said["permissionDecision"] == "deny"


@pytest.mark.parametrize(
    ("written", "why"),
    [
        ("", "does not parse"),
        (TEMPLATE.replace('    "justfile",\n', ""), "changes, removes or reorders a [[group]]"),
        (
            TEMPLATE.replace('    "justfile",\n', '    "x/**",\n    "justfile",\n', 1),
            "changes, removes or reorders a [[group]]",
        ),
        (
            TEMPLATE.replace('    "quality/**",\n', '    "quality/**",\n]\nexclude = [\n    "x",\n'),
            "changes, removes or reorders a [[group]]",
        ),
        (TEMPLATE.replace('on = "rewrite"\n', ""), "changes, removes or reorders a [[door]]"),
        (EXTRA_GROUP + TEMPLATE, "changes, removes or reorders a [[group]]"),
        (
            TEMPLATE.replace(
                "    # bind: the config files of every gate tool\n", '    ".claude/settings.json",\n'
            ),
            "'.claude/settings.json' in [[group]] 'tool-config' also matches paths of 'harness'",
        ),
        (
            TEMPLATE.replace("    # bind: the config files of every gate tool\n", '    ".c*/**",\n'),
            "'.c*/**' in [[group]] 'tool-config' also matches paths of 'harness'",
        ),
    ],
)
def test_a_write_that_does_more_than_add_to_the_manifest_is_denied_on_a_branch(
    adopted: Path, written: str, why: str
) -> None:
    said = write(adopted, MANIFEST, written)
    assert said["permissionDecision"] == "deny"
    assert "This edit is not an addition" in said["permissionDecisionReason"]
    assert why in said["permissionDecisionReason"]


def test_an_edit_the_guard_cannot_apply_to_the_manifest_is_denied(adopted: Path) -> None:
    said = answer(
        adopted,
        "Edit",
        file_path=str(adopted / MANIFEST),
        old_string="no such text",
        new_string=EXTRA_GROUP,
    )
    assert said["permissionDecision"] == "deny"
    assert (
        "the guard cannot tell what this Edit call would write" in said["permissionDecisionReason"]
    )


def test_a_write_or_edit_that_appends_whole_groups_or_doors_passes_on_a_branch(
    adopted: Path,
) -> None:
    assert write(adopted, MANIFEST, TEMPLATE + EXTRA_GROUP + EXTRA_DOOR) == {}
    assert write(adopted, MANIFEST, "# the owner's words\n" + TEMPLATE + EXTRA_GROUP) == {}
    last = "    # bind: the artefacts the contracts-check and api-check gates diff\n]\n"
    edit = answer(
        adopted,
        "Edit",
        file_path=str(adopted / MANIFEST),
        old_string=last,
        new_string=last + EXTRA_GROUP,
    )
    assert edit == {}
    assert write(adopted, "guards/notes.md") == {}


@pytest.mark.parametrize(
    ("bind", "pattern"),
    [
        ("    # bind: the config files of every gate tool\n", '"eslint.config.js"'),
        ("    # bind: the fitness test directories\n", '"tests/fitness/**"'),
        ("    # bind: migrations, schema definitions, infrastructure code\n", '"db/migrations/**"'),
    ],
)
def test_an_edit_that_adds_a_path_to_a_group_or_door_passes_on_a_branch(
    adopted: Path, bind: str, pattern: str
) -> None:
    # Why: /ratchet:safeguard adds a new tool's config to the tool-config group, and a binding
    # fills the template's `# bind:` lines; neither takes a path from a group below.
    edit = answer(
        adopted,
        "Edit",
        file_path=str(adopted / MANIFEST),
        old_string=bind,
        new_string=f"    {pattern},\n",
    )
    assert edit == {}


def test_an_addition_to_the_manifest_on_the_default_branch_is_made_on_a_branch(
    tmp_path: Path,
) -> None:
    root = checkout(tmp_path, manifest=TEMPLATE)
    said = write(root, MANIFEST, TEMPLATE + EXTRA_GROUP)
    assert said["permissionDecision"] == "deny"
    assert "Make the change on a branch" in said["permissionDecisionReason"]


def test_a_manifest_that_locks_itself_takes_no_addition_either(tmp_path: Path) -> None:
    locked = TEMPLATE.replace('name = "manifest"\n', 'name = "manifest"\nedit = "deny"\n')
    root = checkout(tmp_path, manifest=locked)
    switch_to_a_branch(root)
    said = write(root, MANIFEST, locked + EXTRA_GROUP)
    assert said["permissionDecision"] == "deny"
    assert f"{MANIFEST} is in guard group 'manifest'" in said["permissionDecisionReason"]
