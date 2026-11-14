"""The binding says whether an install comes first, and the justfile keeps to it (ADR 0043).

The stack here is made up: a lockfile called ``tool.lock`` and an install that copies it to a stamp.
The plugin names no package manager, so nothing below needs one.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

import conformance
import installs
import owed
import upgrade
from tests.test_conformance import pristine

NONE = "- Install: `none: the self-test tools need no install`"
NEEDED = "- Install: `just install`\n- Lockfile: `tool.lock`\n- Needed by: `fmt-check`, `lint`"
STAMP = "@cmp -s tool.lock .stamp || { echo fetch; cp tool.lock .stamp; }"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    shutil.copytree(pristine(), tmp_path / "repo", symlinks=True)
    return tmp_path / "repo"


def justfile(root: Path) -> str:
    return (root / "justfile").read_text(encoding="utf-8")


def binding(root: Path) -> str:
    return (root / "docs/process/binding.md").read_text(encoding="utf-8")


def without_install(text: str) -> str:
    """The binding with its whole Install section cut out."""
    start = text.index(installs.SECTION)
    return text[:start] + text[text.index("\n## ", start + 1) + 1 :]


def rebind(root: Path, lines: str) -> None:
    path = root / "docs/process/binding.md"
    assert NONE in binding(root)
    path.write_text(binding(root).replace(NONE, lines), encoding="utf-8")


def stack(root: Path, lines: str = NEEDED, *, wired: bool = True) -> None:
    """A project whose tools need an install: its lockfile, section, recipe and dependencies."""
    (root / "tool.lock").write_text("locked\n", encoding="utf-8")
    rebind(root, lines)
    text = justfile(root).replace("install:\n    @true", f"install:\n    {STAMP}")
    if wired:
        text = text.replace("\nfmt-check:\n", "\nfmt-check: install\n", 1)
        text = text.replace("\nlint:\n", "\nlint: install\n", 1)
        for tier in (
            "tier0",
            "tier0-tree",
            "tier1",
            "tier2",
            "check",
            "check-fast",
            "check-deep",
            "weekly",
        ):
            text = text.replace(f"\n{tier}: plugin-pin\n", f"\n{tier}: plugin-pin install\n")
    (root / "justfile").write_text(text, encoding="utf-8")


def found(root: Path) -> list[conformance.Finding]:
    return [f for f in conformance.findings(root) if f.rule == "installs"]


def messages(root: Path) -> str:
    return "\n".join(f.problem for f in found(root))


def agent_reasons(root: Path) -> str:
    [finding] = [f for f in found(root) if f.path == "justfile"]
    fix = finding.fix.otherwise if isinstance(finding.fix, conformance.Edit) else finding.fix
    assert isinstance(fix, conformance.Agent)
    return "\n".join(fix.then)


# --- the section ----------------------------------------------------------------------------


def test_the_template_with_none_and_a_reason_asks_nothing(repo: Path) -> None:
    assert found(repo) == []
    spec = installs.parse(binding(repo))
    assert spec is not None
    assert (spec.needed, spec.problems) == (False, ())


def test_a_binding_without_the_section_has_no_spec() -> None:
    assert installs.parse("# Binding\n") is None
    assert installs.parse(None) is None


def test_a_section_that_says_an_install_comes_first_names_its_lockfile_and_recipes() -> None:
    spec = installs.parse(f"# B\n\n## Install\n\n{NEEDED}\n")
    assert spec == installs.Spec(
        needed=True, lockfile="tool.lock", recipes=("fmt-check", "lint"), problems=()
    )


@pytest.mark.parametrize(
    ("lines", "problem"),
    [
        ("- Install: <`just install`, or `none: <reason>`>", "is still the template's"),
        ("- Install: `none: x`", "needs the reason"),
        ("- Install: `make install`", "is `just install` or `none: <reason>`"),
        ("- Install: `just install`\n- Needed by: `lint`", "names the one file"),
        (
            "- Install: `just install`\n- Lockfile: `a`, `b`\n- Needed by: `lint`",
            "names the one file",
        ),
        ("- Install: `just install`\n- Lockfile: `tool.lock`", "names the recipes"),
    ],
)
def test_a_section_that_is_unfilled_or_malformed_is_red(
    repo: Path, lines: str, problem: str
) -> None:
    rebind(repo, lines)
    [finding] = found(repo)
    assert problem in finding.problem
    assert finding.path == "docs/process/binding.md"
    assert finding.stops == conformance.INSTALL_STOPS
    assert not owed.never(finding.key())


def test_a_binding_with_no_section_is_red_and_the_template_section_is_offered(repo: Path) -> None:
    text = without_install(binding(repo))
    (repo / "docs/process/binding.md").write_text(text, encoding="utf-8")
    [finding] = found(repo)
    assert "has no '## Install' section" in finding.problem


def test_the_upgrade_writes_the_section_where_the_justfile_states_it(repo: Path) -> None:
    text = without_install(binding(repo))
    (repo / "docs/process/binding.md").write_text(text, encoding="utf-8")
    upgrade.converge(repo, {})
    spec = installs.parse(binding(repo))
    assert spec is not None
    assert spec.needed is False
    assert "install recipe does nothing" in binding(repo)
    assert found(repo) == []


def test_the_upgrade_writes_the_lockfile_and_waiting_recipes_when_the_justfile_names_them(
    repo: Path,
) -> None:
    stack(repo)
    text = without_install(binding(repo))
    (repo / "docs/process/binding.md").write_text(text, encoding="utf-8")
    upgrade.converge(repo, {})
    spec = installs.parse(binding(repo))
    assert spec is not None
    assert spec.lockfile == "tool.lock"
    assert set(spec.recipes) == {"fmt-check", "lint"}
    assert found(repo) == []


def test_the_upgrade_owes_the_section_when_the_justfile_does_not_state_it(repo: Path) -> None:
    (repo / "justfile").write_text(
        justfile(repo).replace("install:\n    @true", "install:\n    echo something"),
        encoding="utf-8",
    )
    text = without_install(binding(repo))
    (repo / "docs/process/binding.md").write_text(text, encoding="utf-8")
    left = upgrade.converge(repo, {}).left
    spec = installs.parse(binding(repo))
    assert spec is not None
    assert "is still the template's" in spec.problems[0]
    assert [f.rule for f in left] == ["installs"]


# --- derived from a justfile ----------------------------------------------------------------


def derived(text: str, tiers: tuple[str, ...] = ("tier0",)) -> dict[str, str] | None:
    return installs.derived(text, tracked=lambda name: name == "tool.lock", tiers=tiers)


def test_an_install_that_does_nothing_and_that_nothing_waits_on_says_none() -> None:
    found_ = derived("install:\n    @true\n\ntier0: install\n    x\n")
    assert found_ is not None
    assert found_["Install"].startswith("`none:")
    assert derived("install:\n\nlint: install\n    x\n") is None, "a recipe waits on it"


def test_an_install_that_names_a_tracked_file_says_it_and_the_recipes_that_wait() -> None:
    text = f"install:\n    {STAMP}\n\nlint: install\n    x\n\ntier0: install\n    x\n"
    assert derived(text) == {
        "Install": "`just install`",
        "Lockfile": "`tool.lock`",
        "Needed by": "`lint`",
    }


def test_an_install_the_justfile_does_not_explain_is_left_to_the_agent() -> None:
    assert derived("install:\n    fetch --all\n\nlint: install\n    x\n") is None
    assert derived(f"install:\n    {STAMP}\n\nlint:\n    x\n") is None
    assert derived("lint:\n    x\n") is None


# --- the justfile held to the section ------------------------------------------------------


def test_a_justfile_that_keeps_to_the_section_is_green(repo: Path) -> None:
    stack(repo)
    assert found(repo) == []


def test_a_listed_recipe_with_no_install_ahead_of_it_is_named_and_the_edit_wires_it(
    repo: Path,
) -> None:
    stack(repo, wired=False)
    [finding] = found(repo)
    assert "`fmt-check`, `lint`" in finding.problem
    assert finding.path == "justfile"
    assert isinstance(finding.fix, conformance.Auto)
    (repo / "justfile").write_text(finding.fix.want(justfile(repo)), encoding="utf-8")
    assert found(repo) == []
    assert "fmt-check: install\n" in justfile(repo)
    assert "tier0: plugin-pin install\n" in justfile(repo)


def test_an_install_that_does_not_name_the_lockfile_is_red(repo: Path) -> None:
    stack(repo)
    (repo / "justfile").write_text(
        justfile(repo).replace("tool.lock", "other.lock"), encoding="utf-8"
    )
    assert "does not name tool.lock" in agent_reasons(repo)


def test_a_tier_that_starts_its_gates_before_any_install_is_named(repo: Path) -> None:
    stack(repo)
    (repo / "justfile").write_text(
        justfile(repo).replace("tier0: plugin-pin install\n", "tier0: plugin-pin\n"),
        encoding="utf-8",
    )
    [finding] = found(repo)
    assert isinstance(finding.fix, conformance.Auto)
    made = finding.fix.want(justfile(repo))
    assert "tier0: plugin-pin install\n" in made


def test_a_recipe_that_reaches_install_through_another_is_wired(repo: Path) -> None:
    stack(repo, wired=False)
    text = justfile(repo).replace("\nfmt-check:\n", "\nfmt-check: lint\n", 1)
    text = text.replace("\nlint:\n", "\nlint: install\n", 1)
    for tier in (
        "tier0",
        "tier0-tree",
        "tier1",
        "tier2",
        "check",
        "check-fast",
        "check-deep",
        "weekly",
    ):
        text = text.replace(f"\n{tier}: plugin-pin\n", f"\n{tier}: plugin-pin install\n")
    (repo / "justfile").write_text(text, encoding="utf-8")
    assert found(repo) == []


def test_a_lockfile_that_is_not_a_file_and_a_recipe_that_is_not_defined_are_red(
    repo: Path,
) -> None:
    stack(repo, NEEDED.replace("`lint`", "`lint`, `ghost`"))
    (repo / "tool.lock").unlink()
    text = messages(repo)
    assert "the Install section's lockfile 'tool.lock' is not a file" in text
    assert "lists the recipes `ghost`, which the justfile does not define" in text


def test_a_section_that_says_none_asks_nothing_of_the_justfile(repo: Path) -> None:
    (repo / "justfile").write_text(
        justfile(repo).replace("install:\n    @true", ""), encoding="utf-8"
    )
    assert found(repo) == []


def test_the_structural_findings_cannot_be_owed_and_the_section_ones_can(repo: Path) -> None:
    stack(repo, wired=False)
    [finding] = found(repo)
    assert owed.never(finding.key())
    upgrade.converge(repo, {})
    assert found(repo) == [] or all(f.path != "justfile" for f in found(repo))


def test_the_recipe_the_section_describes_installs_once_and_again_when_the_lockfile_changes(
    tmp_path: Path,
) -> None:
    just = shutil.which("just")
    if just is None:
        pytest.skip("just is not installed")
    project = tmp_path / "app"
    project.mkdir()
    (project / "tool.lock").write_text("one\n", encoding="utf-8")
    (project / "justfile").write_text(
        f"install:\n    {STAMP}\n\nlint: install\n    @echo linted\n", encoding="utf-8"
    )

    def fetches() -> int:
        done = subprocess.run(  # noqa: S603 # -- a fixed argv in a project this test built
            [just, "lint"], cwd=project, capture_output=True, check=True, text=True
        )
        return done.stdout.count("fetch") + done.stderr.count("fetch")

    assert fetches() >= 1
    assert fetches() == 0
    (project / "tool.lock").write_text("two\n", encoding="utf-8")
    assert fetches() >= 1
