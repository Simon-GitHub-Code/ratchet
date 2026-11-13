"""Who fixes each finding: the script (auto), an agent, or the owner, decided where it is raised."""

import json
import shutil
from pathlib import Path

import pytest

import conformance
import gatelib
from tests.test_conformance import (
    BINDING,
    JUSTFILE,
    MANIFEST,
    Mutate,
    pristine,
    remove,
    replace,
    write,
)

CI = ".github/workflows/ci.yml"
SETTINGS = ".claude/settings.json"
PIN_LINE = f"- Ratchet plugin: 0.0.0 at {'0' * 40}"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    shutil.copytree(pristine(), tmp_path / "repo", symlinks=True)
    return tmp_path / "repo"


def kind(root: Path, prefix: str) -> str:
    """The kind of the one finding whose line starts with ``prefix``."""
    [found] = [f for f in conformance.findings(root) if str(f).startswith(prefix)]
    return type(found.fix).__name__


def move_rule(root: Path, rule: str, *, source: str, target: str) -> None:
    path = root / SETTINGS
    data = json.loads(path.read_text(encoding="utf-8"))
    data["permissions"][source].remove(rule)
    data["permissions"][target].append(rule)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def foreign_in(root: Path, key: str, rule: str) -> None:
    path = root / SETTINGS
    data = json.loads(path.read_text(encoding="utf-8"))
    data["permissions"][key].append(rule)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


AUTO = [
    (replace(BINDING, PIN_LINE, ""), f"{BINDING}: no '- Ratchet plugin"),
    (replace(CI, "0" * 40, "<ratchet-sha>"), f"{CI}: checks the plugin out at '<ratchet-sha>'"),
    (
        replace("AGENTS.md", "## Asking the owner", "## Asking"),
        "AGENTS.md: has no '## Asking the owner'",
    ),
    (
        replace(".github/pull_request_template.md", "## Guard paths", "## Guards"),
        ".github/pull_request_template.md: has no '## Guard paths' section",
    ),
    (remove(".github/pull_request_template.md"), ".github/pull_request_template.md: missing"),
    (remove("flags.toml"), "flags.toml: missing"),
    (remove("quality/bites.toml"), "quality/bites.toml: missing"),
    (remove("docs/features"), "docs/features/.gitkeep: "),
    (remove("docs/disputes"), "docs/disputes/.gitkeep: "),
    (remove(".github/workflows/merge.yml"), ".github/workflows/merge.yml: missing"),
    (remove(".github/workflows/weekly.yml"), ".github/workflows/weekly.yml: missing"),
    (
        replace(BINDING, "| tier0-tree | `just tier0-tree` |\n", ""),
        f"{BINDING}: the Entry points table has no row for 'tier0-tree'",
    ),
    (
        replace(JUSTFILE, "hooks-install:\n", "hooks-installs:\n"),
        "justfile: has no `hooks-install`",
    ),
    (
        replace(JUSTFILE, "ratchet:\n    just hooks-installed\n", "ratchet:\n"),
        "justfile: the `ratchet` recipe raises",
    ),
    (
        lambda root: move_rule(root, "Bash(git push --force:*)", source="deny", target="ask"),
        f"{SETTINGS}: 'Bash(git push --force:*)' in ask",
    ),
    (
        replace(SETTINGS, '      "Edit(/GLOSSARY-MAP.md)",\n', ""),
        f"{SETTINGS}: 'Edit(/GLOSSARY-MAP.md)' is missing",
    ),
]


@pytest.mark.parametrize(("mutate", "line"), AUTO)
def test_a_finding_the_script_can_fix_says_so(repo: Path, mutate: Mutate, line: str) -> None:
    mutate(repo)
    assert kind(repo, line) == "Auto"


def test_a_plugin_pin_recipe_is_copied_and_run_first_when_it_stands_on_its_own(
    repo: Path,
) -> None:
    replace(JUSTFILE, "plugin_pin.py", "pin.py")(repo)
    replace(JUSTFILE, "plugin-pin", "pinned")(repo)
    [found] = [f for f in conformance.findings(repo) if f.problem.startswith("not every tier runs")]
    assert isinstance(found.fix, conformance.Auto)
    made = found.fix.want((repo / JUSTFILE).read_text(encoding="utf-8"))
    assert "plugin-pin:" in made
    assert "tier0: plugin-pin pinned" in made


def test_a_plugin_pin_recipe_that_cannot_be_copied_is_left_to_an_agent(repo: Path) -> None:
    replace(JUSTFILE, "plugin_pin.py", "pin.py")(repo)
    assert kind(repo, "justfile: not every tier runs plugin_pin.py") == "Agent"
    replace(JUSTFILE, "plugin-pin", "pinned")(repo)
    replace(JUSTFILE, "ratchet_plugin", "plugin_home")(repo)
    [found] = [f for f in conformance.findings(repo) if f.problem.startswith("not every tier runs")]
    assert isinstance(found.fix, conformance.Agent)
    assert any("ratchet_plugin" in reason for reason in found.fix.then)


def test_a_recipe_that_needs_what_the_justfile_lacks_is_left_to_an_agent(repo: Path) -> None:
    replace(JUSTFILE, "weekly_gates", "gates_of_the_week")(repo)
    replace(JUSTFILE, "weekly: plugin-pin", "weeklies: plugin-pin")(repo)
    [found] = [f for f in conformance.findings(repo) if "`weekly` recipe" in f.problem]
    assert isinstance(found.fix, conformance.Agent)
    assert "it needs `weekly_gates`, which the justfile does not define" in found.problem
    assert found.fix.start[-2].startswith("weekly:")


def test_an_entry_point_row_is_left_to_an_agent_unless_the_runner_is_just(repo: Path) -> None:
    replace(BINDING, "| tier0 | `just tier0` |", "| tier0 | `make tier0` |")(repo)
    replace(BINDING, "| tier0-tree | `just tier0-tree` |\n", "")(repo)
    assert kind(repo, f"{BINDING}: the Entry points table has no row for 'tier0-tree'") == "Agent"


def test_an_entry_point_row_is_left_to_an_agent_when_the_table_cannot_take_it(
    repo: Path,
) -> None:
    replace(BINDING, "| tier0-tree | `just tier0-tree` |\n", "")(repo)
    replace(BINDING, "| tier0 | `just tier0` |", "| tier0 | `just tier0` | extra |")(repo)
    assert kind(repo, f"{BINDING}: the Entry points table has no row for 'tier0-tree'") == "Agent"


def test_a_ratchet_recipe_that_cannot_ask_first_is_left_to_an_agent(repo: Path) -> None:
    replace(JUSTFILE, "ratchet:\n    just hooks-installed\n", "ratchet:\n")(repo)
    replace(JUSTFILE, "hooks-installed:\n", "hooks-asked:\n")(repo)
    replace(JUSTFILE, "ratchet_plugin", "plugin_home")(repo)
    [found] = [f for f in conformance.findings(repo) if "`ratchet` recipe" in f.problem]
    assert isinstance(found.fix, conformance.Agent)
    assert any("plugin_home" not in reason for reason in found.fix.then)


def test_a_ratchet_recipe_written_as_a_script_is_left_to_an_agent(repo: Path) -> None:
    replace(
        JUSTFILE, "ratchet:\n    just hooks-installed\n", "ratchet:\n    #!/usr/bin/env bash\n"
    )(repo)
    assert kind(repo, "justfile: the `ratchet` recipe raises") == "Agent"


def test_a_rule_no_version_generates_is_a_question_for_the_owner(repo: Path) -> None:
    foreign_in(repo, "deny", "Bash(gh label create:*)")
    assert kind(repo, f"{SETTINGS}: 'Bash(gh label create:*)' in deny") == "Owner"
    [found] = [f for f in conformance.findings(repo) if "gh label create" in str(f)]
    assert isinstance(found.fix, conformance.Owner)
    assert "Is `Bash(gh label create:*)` a rule you chose to keep?" in found.fix.ask


def test_settings_that_are_not_a_json_object_are_left_to_an_agent(repo: Path) -> None:
    write(SETTINGS, "[]")(repo)
    [first, *_] = [f for f in conformance.findings(repo) if f.path == SETTINGS]
    assert isinstance(first.fix, conformance.Agent)
    assert first.fix.then == (f"{SETTINGS} is not a JSON object, so it is left as it is",)


def test_a_manifest_that_uses_exclude_is_left_to_an_agent(repo: Path) -> None:
    replace(MANIFEST, "[[group]]\n", '[[group]]\nexclude = ["x"]\n')(repo)
    found = [f for f in conformance.findings(repo) if "uses exclude" in str(f)]
    assert found
    assert all(isinstance(f.fix, conformance.Agent) for f in found)


def test_nothing_under_guards_is_ever_an_auto_finding() -> None:
    fix = conformance.Auto("write it", gatelib.created("x"))
    with pytest.raises(ValueError, match="never writes under guards/"):
        conformance.Finding("guards/manifest.toml", "a problem", fix)
    assert conformance.Finding("guardsmith.toml", "a problem", fix).path == "guardsmith.toml"


def test_an_edit_that_changes_nothing_or_clears_nothing_is_left_to_its_otherwise(
    tmp_path: Path,
) -> None:
    (tmp_path / "notes.md").write_text("a\n")

    def check(root: Path) -> list[conformance.Finding]:
        done = (root / "notes.md").read_text() == "a\ndone\n"
        return [] if done else [conformance.Finding("notes.md", "is not done", conformance.Agent())]

    def trial(want: gatelib.Want, otherwise: conformance.Agent | conformance.Owner) -> object:
        edit = conformance.Edit("d", want, otherwise)
        fix = conformance.tried(
            tmp_path, check, conformance.Finding("notes.md", "is not done", edit)
        ).fix
        assert (tmp_path / "notes.md").read_text() == "a\n"
        return fix

    agent, owner = conformance.Agent(), conformance.Owner("which?", "none yet")
    assert trial(lambda text: text or "", agent) is agent
    assert trial(lambda text: f"{text}more\n", agent) is agent
    assert trial(lambda text: text or "", owner) is owner
    assert isinstance(trial(lambda text: f"{text}done\n", agent), conformance.Auto)


def test_the_footer_names_how_many_findings_the_upgrade_makes(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    remove("flags.toml")(repo)
    replace(BINDING, "## Speed", "## Slowness")(repo)
    assert conformance.main(["conformance.py", str(repo)]) == 1
    last = capsys.readouterr().out.splitlines()[-1]
    assert last == "conformance: red (2 failures; `upgrade.py --apply` makes 1 of them)"
