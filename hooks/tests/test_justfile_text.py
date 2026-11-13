"""Justfile edits: the template's recipes a project may be given, and the lines that join them."""

import shutil
import subprocess
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

import justfile_text
from tests import adopted
from tests.support import PLUGIN_ROOT

TEMPLATE_PATH = PLUGIN_ROOT / "skills/adopt/templates/justfile"
TEMPLATE = """set shell := ["bash", "-euo", "pipefail", "-c"]

ratchet_plugin := "plugin"

# Red before any gate runs when the plugin is not the pin.
plugin-pin:
    @test -f "{{ratchet_plugin}}/pin.py"
    @python3 "{{ratchet_plugin}}/pin.py"

tier0: plugin-pin
    python3 "{{ratchet_plugin}}/tier.py"

# Would a commit here run tier 0?
# Red, with the repair, when it would not.
hooks-installed:
    python3 "{{ratchet_plugin}}/hooks_installed.py"

[private]
commit-msg file:
    python3 "{{ratchet_plugin}}/commits.py" "{{file}}"

settings:
    python3 "{{ratchet_plugin}}/settings.py"

weekly: plugin-pin
    echo weekly
"""
COPIED = ("plugin-pin", "hooks-installed", "commit-msg", "settings", "weekly")
INDENTS = st.sampled_from(["\t", "    ", "  "])
KNOWN: dict[str, list[str]] = {
    "ratchet_plugin": ['ratchet_plugin := "mine: a place"'],
    "plugin-pin": ["plugin-pin:", "{i}echo the project's own"],
    "tier0": ["tier0: pre-commit commits", "{i}just pre-commit", "", "{i}echo done"],
    "hooks-installed": ["# mine", "hooks-installed:", "{i}./check-hooks"],
    "commit-msg": ["[private]", "# mine", "commit-msg file:", "{i}./lint {{file}}"],
    "settings": ["@settings:", "{i}#!/usr/bin/env bash", "{i}set -e"],
    "weekly": ["weekly: && notify", "{i}echo weekly"],
    "deploy": [
        "# ship it",
        "deploy target='prod' *flags: build (test fast)",
        "{i}./deploy {{target}}",
    ],
    "build": ["build:", "{i}make"],
    "flag := 'x'": ["flag := 'x: y'"],
}


@st.composite
def justfiles(draw: st.DrawFn) -> str:
    """A project's justfile: some of the template's recipes as the project has them, and more."""
    names = draw(st.lists(st.sampled_from(sorted(KNOWN)), unique=True))
    indent = draw(INDENTS)
    gaps = draw(st.lists(st.sampled_from(["", "\n", "\n\n"]), min_size=len(names), max_size=20))
    text = draw(st.sampled_from(["", 'set shell := ["sh", "-c"]\n\n', "# A header.\n\n"]))
    for name, gap in zip(names, gaps, strict=False):
        text += "\n".join(line.replace("{i}", indent) for line in KNOWN[name]) + "\n" + gap
    text += draw(st.sampled_from(["", "# a note at the end\n"]))
    return text if draw(st.booleans()) else text.rstrip("\n")


def without(text: str, line: str | None = None) -> list[str]:
    return [candidate for candidate in text.split("\n") if candidate != line]


def kept(old: str, new: str) -> bool:
    """Every non-blank line of ``old`` is in ``new``, in the same order."""
    later = iter(new.split("\n"))
    return all(any(line == candidate for candidate in later) for line in old.split("\n") if line)


def copy_of(name: str) -> list[str]:
    """The template's recipe as it stands: its comment, its attribute, its head and its body."""
    lines = TEMPLATE.split("\n")
    block = next(b for b in justfile_text.blocks(lines) if b.name == name)
    return lines[block.start : block.end]


def names_of(text: str) -> list[str]:
    return [block.name for block in justfile_text.blocks(text.split("\n"))]


@given(justfiles(), st.sampled_from(COPIED))
def test_a_recipe_is_copied_once_and_every_line_is_kept(project: str, name: str) -> None:
    want = justfile_text.with_recipe(TEMPLATE, name)
    once = want(project)
    assert want(once) == once
    assert kept(project, once)
    assert names_of(once).count(name) == 1
    if name not in names_of(project):
        copy = copy_of(name)
        assert "\n".join(copy) in once
        assert not set(without(once)) - set(without(project)) - set(copy) - {""}


@given(justfiles(), st.sampled_from(COPIED), st.sampled_from(COPIED))
def test_two_recipes_copied_in_either_order_give_the_same_text(
    project: str, first: str, second: str
) -> None:
    one = justfile_text.with_recipe(TEMPLATE, first)
    two = justfile_text.with_recipe(TEMPLATE, second)
    assert one(two(project)) == two(one(project))


@given(justfiles(), st.sampled_from(COPIED))
def test_a_copied_recipe_stands_apart_with_its_comment_and_attribute(
    project: str, name: str
) -> None:
    if name in names_of(project):
        return
    lines = justfile_text.with_recipe(TEMPLATE, name)(project).split("\n")
    block = next(b for b in justfile_text.blocks(lines) if b.name == name)
    assert block.start == 0 or lines[block.start - 1] == ""
    assert block.end == len(lines) or lines[block.end] == ""
    assert lines[block.start : block.end] == copy_of(name)


def test_a_recipe_goes_at_the_end_less_the_recipes_the_template_puts_after_it() -> None:
    project = "ratchet_plugin := 'p'\n\nweekly: plugin-pin\n    echo weekly\n\ndeploy:\n    ./d\n"
    once = justfile_text.with_recipe(TEMPLATE, "hooks-installed")(project)
    assert once == (
        "ratchet_plugin := 'p'\n\nweekly: plugin-pin\n    echo weekly\n\ndeploy:\n    ./d\n\n"
        "# Would a commit here run tier 0?\n# Red, with the repair, when it would not.\n"
        'hooks-installed:\n    python3 "{{ratchet_plugin}}/hooks_installed.py"\n'
    )
    only = "weekly: plugin-pin\n    echo weekly\n"
    assert names_of(justfile_text.with_recipe(TEMPLATE, "settings")(only)) == ["settings", "weekly"]


def test_a_recipe_goes_before_a_later_one_that_has_no_blank_line_above() -> None:
    project = "weekly: plugin-pin\n    echo weekly\n"
    once = justfile_text.with_recipe(TEMPLATE, "settings")(project)
    assert once.startswith('settings:\n    python3 "{{ratchet_plugin}}/settings.py"\n\nweekly:')
    glued = "flag := 1\n# about weekly\nweekly:\n    echo\n"
    assert justfile_text.with_recipe(TEMPLATE, "settings")(glued) == (
        'flag := 1\n\nsettings:\n    python3 "{{ratchet_plugin}}/settings.py"\n\n'
        "# about weekly\nweekly:\n    echo\n"
    )


def test_a_recipe_goes_into_a_file_with_nothing_but_comments_or_nothing() -> None:
    want = justfile_text.with_recipe(TEMPLATE, "settings")
    body = 'settings:\n    python3 "{{ratchet_plugin}}/settings.py"'
    assert want("") == body + "\n"
    assert want(None) == body + "\n"
    assert want("# nothing yet\n\n\n") == f"# nothing yet\n\n{body}\n\n\n"


def test_a_recipe_the_file_defines_as_a_recipe_or_a_variable_is_left_alone() -> None:
    mine = "settings:\n    echo mine\n"
    assert justfile_text.with_recipe(TEMPLATE, "settings")(mine) == mine
    variable = "settings := 'x'\n"
    assert justfile_text.with_recipe(TEMPLATE, "settings")(variable) == variable


def test_only_a_recipe_of_the_template_can_be_copied() -> None:
    with pytest.raises(ValueError, match="no recipe 'nothing'"):
        justfile_text.with_recipe(TEMPLATE, "nothing")
    with pytest.raises(ValueError, match="no recipe 'ratchet_plugin'"):
        justfile_text.with_recipe(TEMPLATE, "ratchet_plugin")


HEADS = [
    ("tier0:", "tier0: plugin-pin"),
    ("tier0: a b", "tier0: plugin-pin a b"),
    ("tier0:   a", "tier0:   plugin-pin a"),
    ("tier0:a", "tier0: plugin-pin a"),
    ("tier0: (build debug) && notify", "tier0: plugin-pin (build debug) && notify"),
    ("tier0: && notify", "tier0: plugin-pin && notify"),
    ("tier0: a # comment", "tier0: plugin-pin a # comment"),
    ("tier0: # comment", "tier0: plugin-pin # comment"),
    ("tier0:   ", "tier0: plugin-pin   "),
    ("@tier0 x='a:b' *y: a", "@tier0 x='a:b' *y: plugin-pin a"),
    ("tier0: a && plugin-pin", "tier0: plugin-pin a && plugin-pin"),
    ("tier0: plugin-pin a", "tier0: plugin-pin a"),
    ("tier0: a plugin-pin", "tier0: a plugin-pin"),
    ("tier0: (plugin-pin x) a", "tier0: (plugin-pin x) a"),
    ("tier0: (", "tier0: plugin-pin ("),
]


@pytest.mark.parametrize(("head", "expected"), HEADS)
@pytest.mark.parametrize("indent", ["\t", "    "])
def test_a_dependency_goes_first_and_nothing_else_on_the_head_moves(
    head: str, expected: str, indent: str
) -> None:
    project = f"x := 1\n\n{head}\n{indent}echo a\n\nother: tier0\n{indent}echo b\n"
    want = justfile_text.with_first_dependency("tier0", "plugin-pin")
    once = want(project)
    assert once == project.replace(head, expected, 1)
    assert want(once) == once


@pytest.mark.parametrize("text", ["", "tier0 := 'a'\n", "other: a\n    echo\n", "tier0 a\n"])
def test_a_dependency_is_not_added_to_what_is_not_a_recipe(text: str) -> None:
    assert justfile_text.with_first_dependency("tier0", "plugin-pin")(text) == text


@given(justfiles(), st.sampled_from(["plugin-pin", "tier0", "weekly", "deploy", "settings"]))
def test_a_first_dependency_adds_one_name_and_keeps_every_other_line(
    project: str, recipe: str
) -> None:
    want = justfile_text.with_first_dependency(recipe, "gate")
    once = want(project)
    assert want(once) == once
    changed = [(a, b) for a, b in zip(project.split("\n"), once.split("\n"), strict=True) if a != b]
    assert len(changed) <= 1
    for before, after in changed:
        assert after.replace("gate ", "", 1) == before or after.replace(" gate", "", 1) == before


@given(
    justfiles(),
    st.sampled_from(["tier0", "weekly", "deploy"]),
    st.sampled_from(["tier0", "weekly", "deploy"]),
)
def test_first_dependencies_of_two_recipes_come_in_either_order(
    project: str, first: str, second: str
) -> None:
    if first == second:
        return
    one = justfile_text.with_first_dependency(first, "gate")
    two = justfile_text.with_first_dependency(second, "gate")
    assert one(two(project)) == two(one(project))


@pytest.mark.parametrize("indent", ["\t", "    ", "  "])
def test_a_line_starts_the_body_at_the_bodys_own_indentation(indent: str) -> None:
    project = f"ratchet:\n{indent}@echo hi\n{indent}echo bye\n"
    want = justfile_text.with_first_line("ratchet", "just hooks-installed")
    once = want(project)
    assert once == f"ratchet:\n{indent}just hooks-installed\n{indent}@echo hi\n{indent}echo bye\n"
    assert want(once) == once


def test_a_line_already_first_is_not_added_again_with_or_without_the_at_sign() -> None:
    want = justfile_text.with_first_line("ratchet", "just hooks-installed")
    for first in ("just hooks-installed", "@just hooks-installed"):
        project = f"ratchet:\n    {first}\n    echo\n"
        assert want(project) == project


def test_a_line_is_not_added_where_the_text_cannot_take_it_exactly() -> None:
    want = justfile_text.with_first_line("ratchet", "just hooks-installed")
    shebang = "ratchet:\n    #!/usr/bin/env bash\n    echo hi\n"
    for text in (
        shebang,
        "ratchet:\n",
        "ratchet: a\n\nnext:\n    x\n",
        "ratchet := 1\n",
        "",
        "a:\n  b\n",
    ):
        assert want(text) == text


@given(justfiles(), st.sampled_from(["tier0", "weekly", "deploy", "settings", "build"]))
def test_a_first_line_adds_one_line_and_keeps_every_other(project: str, recipe: str) -> None:
    want = justfile_text.with_first_line(recipe, "just gate")
    once = want(project)
    assert want(once) == once
    assert kept(project, once)
    assert len(once.split("\n")) - len(project.split("\n")) in (0, 1)


@given(
    justfiles(),
    st.sampled_from(["tier0", "weekly", "deploy"]),
    st.sampled_from(["tier0", "weekly", "deploy"]),
)
def test_first_lines_of_two_recipes_come_in_either_order(
    project: str, first: str, second: str
) -> None:
    if first == second:
        return
    one = justfile_text.with_first_line(first, "just one")
    two = justfile_text.with_first_line(second, "just two")
    assert one(two(project)) == two(one(project))


def test_the_readers_find_what_conformance_finds_in_a_justfile() -> None:
    text = "# a note\nbuild:\n    # inside\n    make\n\nflag := 'x'\n"
    assert justfile_text.code_of(text) == "build:\n    make\n\nflag := 'x'"
    assert justfile_text.definition(text, "build") == (
        "# a note",
        "build:",
        "    # inside",
        "    make",
    )
    assert justfile_text.definition(text, "flag") == ("flag := 'x'",)
    assert justfile_text.definition(text, "nothing") == ()
    assert justfile_text.defines_recipe(text, "build")
    assert not justfile_text.defines_recipe(text, "make")
    assert justfile_text.defines_variable(text, "flag")
    assert justfile_text.defines_variable("export flag := 'x'\n", "flag")
    assert not justfile_text.defines_variable(text, "build")


def test_the_heads_of_a_justfile_split_into_names() -> None:
    head = justfile_text.parse_head("deploy t='a b:c' *f: build (test fast) && notify # why", 6)
    assert head is not None
    assert head.params == ("t", "f")
    assert head.before == ("build", "test")
    assert head.after == ("notify",)
    assert justfile_text.parse_head("flag := 'a: b'", 4) is None
    assert justfile_text.parse_head("import 'other.just'", 6) is None
    assert justfile_text.words('a "b c" (d e) f') == ["a", '"b c"', "(d e)", "f"]
    assert justfile_text.mask('a "b: \\" c" d') == 'a "_______" d'


UNMET_TEMPLATE = """\
boot := "bind: the command"
ratchet_plugin := "p"

fine file:
    python3 "{{ratchet_plugin}}/x.py" "{{file}}"

fuzz each="30" cap="600":
    echo {{each}} {{cap}} {{ratchet_plugin}}

calls:
    echo {{ env_var("HOME") }} {{ a + b }}

needs: (build debug) && notify
    just --list --unsorted
    @just other-recipe now; just -q third
    # {{ignored}} in a comment

binds:
    @echo "bind: formatter --check"; exit 1
"""


def test_a_recipe_is_unmet_for_each_name_the_project_lacks_and_each_placeholder() -> None:
    project = 'ratchet_plugin := "q"\nbuild:\n    x\n'
    unmet = justfile_text.unmet
    assert unmet(UNMET_TEMPLATE, project, "fine") == ()
    assert unmet(UNMET_TEMPLATE, project, "fuzz") == ()
    assert unmet(UNMET_TEMPLATE, "", "fine") == (
        "it names `ratchet_plugin`, which the justfile does not define",
    )
    assert unmet(UNMET_TEMPLATE, project, "calls") == (
        "it computes `a + b`",
        'it calls `env_var("HOME")`',
    )
    assert unmet(UNMET_TEMPLATE, project, "needs") == (
        "it names `notify`, which the justfile does not define",
        "it names `other-recipe`, which the justfile does not define",
        "it names `third`, which the justfile does not define",
    )
    assert unmet(UNMET_TEMPLATE, project, "binds") == ("a `bind:` placeholder is in it",)
    assert unmet(UNMET_TEMPLATE, project, "boot") == ("the template has no recipe `boot`",)
    assert unmet(UNMET_TEMPLATE, project, "nothing") == ("the template has no recipe `nothing`",)


def test_a_dependency_or_a_call_the_project_defines_is_met() -> None:
    project = "ratchet_plugin := 'p'\nnotify:\n    x\nother-recipe now:\n    x\nthird:\n    x\n"
    project += "build:\n    x\n"
    assert justfile_text.unmet(UNMET_TEMPLATE, project, "needs") == ()


def old_template(release: str) -> str:
    return adopted.history("show", f"{adopted.RELEASES[release]}:skills/adopt/templates/justfile")


def copyable(template: str, project: str) -> set[str]:
    lines = template.split("\n")
    recipes = [block.name for block in justfile_text.blocks(lines) if block.parsed is not None]
    return {name for name in recipes if not justfile_text.unmet(template, project, name)}


COPYABLE_ON_THE_OLD_TEMPLATE = {
    "default",
    "plugin-pin",
    "commit-msg",
    "ratchet-lowered",
    "guard-diff",
    "settings-check",
    "settings",
    "conformance",
    "triggers",
    "incidents",
    "budget",
    "merge-ready",
    "merge",
    "hooks-installed",
    "hooks-install",
    "snapshots",
    "snapshots-check",
    "suppressions",
    "flags",
    "flags-expired",
    "commits",
    "markers",
    "prose",
    "bite",
    "change-size",
    "bite-all",
    "bite-one",
}
NOT_COPYABLE_ON_THE_OLD_TEMPLATE = {"tier0", "tier0-tree", "pre-commit", "check", "weekly", "smoke"}


@pytest.mark.parametrize("release", sorted(adopted.RELEASES))
def test_the_templates_recipes_a_released_justfile_can_be_given(release: str) -> None:
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    found = copyable(template, old_template(release))
    assert found >= COPYABLE_ON_THE_OLD_TEMPLATE
    assert NOT_COPYABLE_ON_THE_OLD_TEMPLATE.isdisjoint(found)


@pytest.mark.parametrize("release", sorted(adopted.RELEASES))
def test_the_recipes_that_name_what_the_project_lacks_say_which(release: str) -> None:
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    old = old_template(release)
    named = "it names `{}`, which the justfile does not define".format
    assert justfile_text.unmet(template, old, "tier0-tree") == (
        named("parallel"),
        named("plugin-pin"),
        named("serial0"),
        named("tier0_tree_gates"),
    )
    assert justfile_text.unmet(template, old, "check-fast") == (
        named("parallel"),
        named("plugin-pin"),
        named("serial0"),
        named("serial1"),
        named("tier0_gates"),
        named("tier1_gates"),
    )
    assert justfile_text.unmet(template, old, "weekly") == (
        named("parallel"),
        named("plugin-pin"),
        named("weekly_gates"),
    )
    assert justfile_text.unmet(template, old, "fmt-check") == ("a `bind:` placeholder is in it",)
    assert justfile_text.unmet(template, old, "smoke") == (named("booted"),)


@pytest.mark.parametrize("release", sorted(adopted.RELEASES))
def test_a_released_justfile_is_given_every_copyable_recipe_and_still_loads(
    release: str, tmp_path: Path
) -> None:
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    project = old_template(release)
    for _ in range(3):
        for name in sorted(copyable(template, project)):
            project = justfile_text.with_recipe(template, name)(project)
    assert copyable(template, project) <= set(names_of(project))
    assert "plugin-pin" in names_of(project)
    assert kept(old_template(release), project)
    just = shutil.which("just")
    if just is None:
        pytest.skip("just is not installed")
    file = tmp_path / "justfile"
    file.write_text(project, encoding="utf-8")
    done = subprocess.run(  # noqa: S603 # -- a fixed argv; the justfile is the one built here
        [just, "--justfile", str(file), "--summary"], capture_output=True, text=True, check=False
    )
    assert done.returncode == 0, done.stderr
    assert "plugin-pin" in done.stdout.split()


@pytest.mark.parametrize("indent", ["\t", "    "])
def test_a_project_justfile_edited_in_both_ways_still_loads(indent: str, tmp_path: Path) -> None:
    just = shutil.which("just")
    if just is None:
        pytest.skip("just is not installed")
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    project = (
        f"ratchet_plugin := 'p'\n\ntier0: pre-commit\n{indent}echo tier\n\n"
        f"pre-commit:\n{indent}echo\n"
    )
    project = justfile_text.with_recipe(template, "plugin-pin")(project)
    project = justfile_text.with_first_dependency("tier0", "plugin-pin")(project)
    project = justfile_text.with_first_line("pre-commit", "just plugin-pin")(project)
    file = tmp_path / "justfile"
    file.write_text(project, encoding="utf-8")
    done = subprocess.run(  # noqa: S603 # -- a fixed argv; the justfile is the one built here
        [just, "--justfile", str(file), "--dry-run", "tier0"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert "tier0: plugin-pin pre-commit" in project
    assert f"pre-commit:\n{indent}just plugin-pin\n{indent}echo" in project
