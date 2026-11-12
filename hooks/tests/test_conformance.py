"""The conformance gate: green on the pristine templates, and one red line for each fault."""

import atexit
import functools
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path

import pytest

import conformance
from tests.support import PLUGIN_ROOT

SKILLS = PLUGIN_ROOT / "skills"
Mutate = Callable[[Path], None]
ADR = "docs/adr/0001-adopt-the-ratchet-method.md"
BINDING = "docs/process/binding.md"
MANIFEST = "guards/manifest.toml"
REFUTATION = "A long enough observation that would show the method wrong."
DISPUTE = (
    "- Status: open\n\n## Gate\n\n## Exact failure\n\n## Why the gate is wrong\n\n"
    "## Options\n\n## Recommendation\n"
)


@functools.cache
def pristine() -> Path:
    """What adopt leaves: the init and adopt templates with every placeholder filled, once."""
    scratch = Path(tempfile.mkdtemp(prefix="ratchet-conformance-"))
    atexit.register(shutil.rmtree, scratch, ignore_errors=True)
    directory = scratch / "pristine"
    shutil.copytree(SKILLS / "init/templates", directory, symlinks=True)
    shutil.copytree(SKILLS / "adopt/templates", directory, symlinks=True, dirs_exist_ok=True)
    conformance.fill_placeholders(directory)
    return directory


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    shutil.copytree(pristine(), tmp_path / "repo", symlinks=True)
    return tmp_path / "repo"


def replace(path: str, old: str, new: str) -> Mutate:
    def mutate(root: Path) -> None:
        text = (root / path).read_text(encoding="utf-8")
        assert old in text, f"{path} has no {old!r} to replace"
        (root / path).write_text(text.replace(old, new), encoding="utf-8")

    return mutate


def write(path: str, text: str) -> Mutate:
    def mutate(root: Path) -> None:
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text(text, encoding="utf-8")

    return mutate


def remove(*paths: str) -> Mutate:
    def mutate(root: Path) -> None:
        for path in paths:
            target = root / path
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()

    return mutate


def adr(status: str) -> str:
    return (
        f"---\nstatus: {status}\n---\n# Ratchet method\n\n## Refutation criterion\n\n{REFUTATION}\n"
    )


def red(root: Path) -> list[str]:
    return conformance.failures(root)


def test_the_pristine_templates_pass() -> None:
    assert red(pristine()) == []


def test_the_self_test_shows_every_mutation_bites(capsys: pytest.CaptureFixture[str]) -> None:
    assert conformance.main(["conformance.py", "--self-test"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "self-test: pristine templates pass"
    assert len(lines) == 1 + len(conformance.mutations())
    assert all(line.endswith(": bites") for line in lines[1:])


def test_a_workflow_is_held_to_the_pin_only_where_it_runs_the_plugin(repo: Path) -> None:
    ci, deep = ".github/workflows/ci.yml", ".github/workflows/deep.yml"
    pinned, other = "0" * 40, "1" * 40
    replace(ci, f'ref: "{pinned}", ', "")(repo)
    unheld = "so nothing holds it to the commit the binding pins"
    assert red(repo) == [f"{ci}: checks no plugin out by `repository:` then `ref:`, {unheld}"]
    replace(ci, "RATCHET_PLUGIN", "ELSEWHERE")(repo)
    assert red(repo) == []
    replace(deep, pinned, other)(repo)
    elsewhere = f"checks the plugin out at '{other}', not the commit the binding pins ({pinned})"
    assert red(repo) == [f"{deep}: {elsewhere}"]


NAMED = "- Commit hooks: `.pre-commit-config.yaml`"
LEFTHOOK = (
    "pre-commit:\n  commands:\n    tree: {run: just pre-commit}\n"
    "commit-msg:\n  commands:\n    subject: {run: just commit-msg {1}}\n"
)


def test_the_binding_may_name_another_hook_mechanism(repo: Path) -> None:
    remove(".pre-commit-config.yaml")(repo)
    replace(BINDING, NAMED, "- Commit hooks: `lefthook.yml`, installed by `lefthook install`")(repo)
    assert red(repo) == ["lefthook.yml: missing; the tier 0 commit hooks are installed from it"]
    write("lefthook.yml", LEFTHOOK)(repo)
    assert red(repo) == []
    write("lefthook.yml", "pre-commit:\n  commands:\n    tree: {run: just tier0}\n")(repo)
    whole = "a hook runs the whole tier0, whose history gate judges the commit an amend replaces"
    assert red(repo) == [
        "lefthook.yml: names no `commit-msg` hook, so a commit would skip that half of tier 0",
        f"lefthook.yml: {whole}; run `pre-commit` and `commit-msg` instead",
    ]


def test_a_directory_of_plain_hook_scripts_must_say_how_it_is_installed(repo: Path) -> None:
    remove(".pre-commit-config.yaml")(repo)
    replace(BINDING, NAMED, "- Commit hooks: `hooks`")(repo)
    write("hooks/pre-commit", "#!/bin/sh\n# tier0, the tree\nexec just pre-commit\n")(repo)
    write("hooks/commit-msg", '#!/bin/sh\nexec just commit-msg "$1"\n')(repo)
    (repo / "hooks/_").mkdir()
    unnamed = "the `- Commit hooks:` line names no command after ', installed by'"
    blind = "so a red `hooks-installed` cannot say what the owner runs"
    assert red(repo) == [f"docs/process/binding.md: {unnamed}, {blind}"]
    installed = "- Commit hooks: `hooks`, installed by `git config core.hooksPath hooks`"
    replace(BINDING, "- Commit hooks: `hooks`", installed)(repo)
    assert red(repo) == []


def test_a_pristine_template_that_fails_is_reported_and_stops_the_self_test(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(conformance, "fill_placeholders", lambda _root: None)
    assert conformance.main(["conformance.py", "--self-test"]) == 1
    out = capsys.readouterr().out
    assert out.startswith("self-test: the pristine templates fail:\n  ")
    assert ": bites" not in out


def test_a_mutation_that_changes_nothing_is_reported_as_not_biting(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(conformance, "mutations", lambda: [("a no-op", lambda _root: None)])
    assert conformance.main(["conformance.py", "--self-test"]) == 1
    assert "self-test: a no-op: DOES NOT BITE" in capsys.readouterr().out


def test_a_mutation_of_something_the_binding_lacks_stops_the_self_test(repo: Path) -> None:
    with pytest.raises(SystemExit, match="has no 'nowhere' to mutate"):
        conformance.rebind("nowhere", "somewhere")(repo)


def test_the_gate_names_each_failure_and_the_count(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert conformance.main(["conformance.py", str(repo)]) == 0
    assert capsys.readouterr().out == "conformance: green (0 failures)\n"
    remove("GLOSSARY.md", "docs/design-brief.md")(repo)
    assert conformance.main(["conformance.py", str(repo)]) == 1
    assert capsys.readouterr().out.splitlines() == [
        "GLOSSARY.md (or GLOSSARY-MAP.md): missing",
        "docs/design-brief.md: missing",
        "conformance: red (2 failures)",
    ]


def test_without_a_root_the_gate_reads_the_working_directory(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(repo)
    assert conformance.main(["conformance.py"]) == 0
    assert capsys.readouterr().out == "conformance: green (0 failures)\n"


def test_claude_md_stands_in_for_agents_md_and_a_glossary_map_for_the_glossary(
    repo: Path,
) -> None:
    text = (repo / "AGENTS.md").read_text(encoding="utf-8")
    (repo / "CLAUDE.md").unlink()
    (repo / "AGENTS.md").rename(repo / "CLAUDE.md")
    write("GLOSSARY-MAP.md", "# Contexts\n")(repo)
    remove("GLOSSARY.md")(repo)
    assert "ratchet:method" in text
    assert red(repo) == []


@pytest.mark.parametrize(
    ("mutate", "failure"),
    [
        (remove("AGENTS.md"), "AGENTS.md (or CLAUDE.md): missing"),
        (remove("GLOSSARY.md"), "GLOSSARY.md (or GLOSSARY-MAP.md): missing"),
        (
            replace("GLOSSARY.md", "## Language", "## Words"),
            "GLOSSARY.md: has no '## Language' section",
        ),
        (remove("docs/design-brief.md"), "docs/design-brief.md: missing"),
        (
            replace("docs/design-brief.md", "## Slice one", "## First"),
            "docs/design-brief.md: has no '## Slice one' section",
        ),
        (remove("docs/adr"), "docs/adr: missing"),
        (remove(ADR), "docs/adr: no NNNN-<slug>.md record"),
        (
            write("docs/adr/0003-later.md", "# Later\n"),
            "docs/adr: numbering has gaps or duplicates: [1, 3]",
        ),
        (
            write(ADR, "No heading here.\n"),
            f"{ADR}: opens with a '# ' title, optionally after 'status:' front matter",
        ),
        (
            write(
                ADR,
                adr("wonderful"),
            ),
            f"{ADR}: status 'wonderful' is not proposed|accepted|deprecated|rejected|superseded",
        ),
        (
            write(
                ADR,
                adr("superseded"),
            ),
            f"{ADR}: a superseded ADR names its successor: 'superseded-by: NNNN'",
        ),
        (
            write(ADR, "# The Ratchet method\n\n## Refutation criterion\n\nTBD\n"),
            f"{ADR}: '## Refutation criterion' names no observation that would show it wrong",
        ),
        (
            write(ADR, f"# An ADR\n\n## Refutation criterion\n\n{REFUTATION}\n"),
            f"{ADR}: ADR 0001 records adopting the Ratchet method",
        ),
        (remove(BINDING), "docs/process/binding.md: missing"),
        (
            replace(BINDING, "## Gates", "## Rows"),
            "docs/process/binding.md: has no '## Gates' section",
        ),
        (remove(".github/pull_request_template.md"), ".github/pull_request_template.md: missing"),
        (remove(MANIFEST), "guards/manifest.toml: missing"),
        (write(MANIFEST, "not = [toml"), "guards/manifest.toml: Invalid value"),
        (write(MANIFEST, "x = 1\n"), "guards/manifest.toml: has no [[group]]"),
        (
            write(MANIFEST, '[[group]]\nname = "g"\nreason = "short"\npaths = ["a"]\n'),
            "guards/manifest.toml: group 'g' needs a reason of 10+ characters",
        ),
        (
            write(
                MANIFEST, '[[group]]\nname = "g"\nreason = "a long enough reason"\npaths = "a"\n'
            ),
            "guards/manifest.toml: group 'g' needs a paths list",
        ),
        (
            write(MANIFEST, '[[group]]\nreason = "a long enough reason"\npaths = []\n'),
            "guards/manifest.toml: group '<unnamed>' has no paths yet; bind or remove it",
        ),
        (
            remove(".pre-commit-config.yaml"),
            ".pre-commit-config.yaml: missing; the tier 0 commit hooks are installed from it",
        ),
        (remove("quality/thresholds.toml"), "quality/thresholds.toml: missing"),
        (remove("quality/mutation-register.toml"), "quality/mutation-register.toml: missing"),
        (
            write("quality/thresholds.toml", "= broken"),
            "quality/thresholds.toml: Invalid statement",
        ),
        (remove("quality/snapshots"), "quality/snapshots: missing"),
        (
            write("quality/mutation-register.toml", "survivor = 3\n"),
            "quality/mutation-register.toml: 'survivor' is not an array of tables",
        ),
        (remove("docs/disputes"), "docs/disputes: missing"),
        (
            write("docs/disputes/0001-gate.md", "- Status: maybe\n"),
            "docs/disputes/0001-gate.md: needs '- Status: open|upheld|rejected'",
        ),
        (
            write("docs/disputes/0001-gate.md", "- Status: open\n"),
            "docs/disputes/0001-gate.md: has no '## Options' section",
        ),
    ],
)
def test_each_fault_is_one_red_line(repo: Path, mutate: Mutate, failure: str) -> None:
    mutate(repo)
    found = red(repo)
    assert any(line.startswith(failure) for line in found), found


def test_a_survivor_in_the_register_needs_its_fields_and_a_reason(repo: Path) -> None:
    kept = (
        '[[survivor]]\ntool = "t"\nidentity = "i"\n'
        'reason = "this reason is long enough"\ndate = "2026-01-01"\n'
    )
    write("quality/mutation-register.toml", kept)(repo)
    assert red(repo) == []
    write(
        "quality/mutation-register.toml", kept + '\n[[survivor]]\ntool = "t"\nreason = "short"\n'
    )(repo)
    assert red(repo) == [
        "quality/mutation-register.toml: survivor 1 lacks 'identity'",
        "quality/mutation-register.toml: survivor 1 lacks 'date'",
        "quality/mutation-register.toml: survivor 1 needs a reason of 20+ characters",
    ]


def test_a_dispute_with_its_status_and_sections_is_accepted(repo: Path) -> None:
    write("docs/disputes/0001-gate.md", DISPUTE)(repo)
    write("docs/disputes/notes.md", "not a numbered record\n")(repo)
    assert red(repo) == []


def test_a_repository_without_workflows_is_not_asked_about_their_shape(repo: Path) -> None:
    remove(".github/workflows")(repo)
    assert red(repo) == []


def test_a_pull_request_template_the_binding_names_is_the_one_checked(repo: Path) -> None:
    replace(
        BINDING,
        "- Pull request template: `.github/pull_request_template.md`",
        "- Change template: `docs/change.md`, for this forge",
    )(repo)
    assert red(repo) == ["docs/change.md: missing (the binding names the pull request template)"]
    (repo / "docs/change.md").write_text("## Conjecture\n", encoding="utf-8")
    assert red(repo)[0] == "docs/change.md: has no '## Operability' section"


def test_a_binding_that_is_missing_leaves_the_speed_check_to_the_workflows(repo: Path) -> None:
    remove(BINDING)(repo)
    replace(".github/workflows/ci.yml", "concurrency:", "serial:")(repo)
    found = red(repo)
    assert (
        ".github/workflows/ci.yml: nothing cancels superseded runs ('concurrency:' absent)" in found
    )
    assert "docs/process/binding.md: missing" in found


def test_a_one_way_door_needs_a_reason(repo: Path) -> None:
    reason = "Fixtures of data that earlier versions wrote: rewriting one hides"
    text = (repo / MANIFEST).read_text(encoding="utf-8")
    line = next(row for row in text.splitlines() if reason in row)
    replace(MANIFEST, line, 'reason = "Old data."')(repo)
    expected = f"door 'stored-data' needs a reason of {conformance.MIN_REASON}+ characters"
    assert f"guards/manifest.toml: {expected}" in red(repo)


def test_a_self_test_mutation_refuses_a_template_it_no_longer_fits(repo: Path) -> None:
    with pytest.raises(SystemExit, match="has no 'absent text' to mutate"):
        conformance.rewrite(MANIFEST, "absent text", "anything")(repo)
    with pytest.raises(SystemExit, match="has no 'absent text' to mutate"):
        conformance.reregister("absent text", "anything")(repo)


JUSTFILE = "justfile"
TEMPLATE_JUSTFILE = (SKILLS / "adopt/templates/justfile").read_text(encoding="utf-8")


def test_an_entry_point_with_no_row_names_the_row_to_add(repo: Path) -> None:
    replace(BINDING, "| tier0-tree | `just tier0-tree` |\n", "")(repo)
    assert red(repo) == [
        (
            f"{BINDING}: the Entry points table has no row for 'tier0-tree'; add the template's row"
            " | tier0-tree | `just tier0-tree` |, with your runner's command"
        )
    ]
    replace(BINDING, "| tier0 |", "| tier0-tree | |\n| tier0 |")(repo)
    assert red(repo) == [f"{BINDING}: entry point 'tier0-tree' names no command"]


def test_an_entry_point_whose_recipe_is_missing_names_the_recipe_to_copy(repo: Path) -> None:
    replace(JUSTFILE, "hooks-install:\n", "hooks-installs:\n")(repo)
    assert red(repo) == [
        (
            "justfile: has no `hooks-install` recipe, which the binding's entry point"
            " 'hooks-install'"
            " runs; copy it from the template justfile"
        )
    ]
    [finding] = conformance.findings(repo)
    assert finding.add[-2:] == (
        "hooks-install:",
        '    python3 "{{ratchet_plugin}}/scripts/hooks_install.py"',
    )
    replace(JUSTFILE, "set shell", 'import "more.just"\n\nset shell')(repo)
    assert red(repo) == [], "a justfile that imports another is not read for recipes"


def test_a_missing_pin_prints_the_line_for_the_plugin_in_use(repo: Path) -> None:
    pin = f"- Ratchet plugin: 0.0.0 at {'0' * 40}"
    replace(BINDING, pin, "")(repo)
    replace(JUSTFILE, "plugin_pin.py", "pin.py")(repo)
    line = conformance.plugin_pin.line_for(conformance.plugin_pin.PLUGIN)
    assert line.startswith("- Ratchet plugin: ")
    assert red(repo) == [
        (
            f"{BINDING}: no '- Ratchet plugin: <version> at <commit>' line; add under '## Runner',"
            f" for the plugin in use: {line}"
        ),
        (
            "justfile: no tier runs plugin_pin.py, so a local run can use a plugin other than"
            " the one"
            " the binding pins; copy `plugin-pin` from the template justfile, as the first"
            " dependency of each tier"
        ),
    ]


def test_the_pin_line_says_what_a_copy_without_history_cannot(tmp_path: Path) -> None:
    (tmp_path / ".claude-plugin").mkdir()
    (tmp_path / ".claude-plugin/plugin.json").write_text('{"version": "1.2.3"}')
    line = conformance.plugin_pin.line_for(tmp_path)
    assert line == "- Ratchet plugin: 1.2.3 at <the full commit of 1.2.3>"


@pytest.mark.parametrize(
    ("mutate", "failure"),
    [
        (
            replace("AGENTS.md", "## Asking the owner", "## Asking"),
            "AGENTS.md: has no '## Asking the owner' section; copy it from the template AGENTS.md",
        ),
        (
            replace(JUSTFILE, "/scripts/changed.py", "/scripts/base.py"),
            "justfile: no gate runs through `changed.py`",
        ),
        (
            replace(JUSTFILE, "ratchet:\n    just hooks-installed\n", "ratchet:\n"),
            "justfile: the `ratchet` recipe raises the thresholds without asking",
        ),
        (
            replace(JUSTFILE, '"RATCHET_JOBS"', '"JOBS"'),
            "justfile: passes `--jobs` without reading RATCHET_JOBS",
        ),
        (
            replace(JUSTFILE, 'alone := "--alone perf load"', 'alone := ""'),
            "justfile: nothing runs `perf` and `load` alone",
        ),
        (
            replace(JUSTFILE, "each key of flags.py --temporary on", "every flag on"),
            (
                "justfile: no gate that requests the deliverable's routes boots it with the"
                " temporary flags on"
            ),
        ),
    ],
)
def test_each_upgrade_item_is_one_red_line(repo: Path, mutate: Mutate, failure: str) -> None:
    mutate(repo)
    found = red(repo)
    assert len(found) == 1, found
    assert found[0].startswith(failure), found


def test_a_runner_item_is_asked_only_where_its_gates_are_bound(repo: Path) -> None:
    for gate in ("perf", "load", "load-smoke", "diff-coverage", "test-counts", "mutation-changed"):
        text = (repo / BINDING).read_text(encoding="utf-8")
        row = next(line for line in text.splitlines() if line.endswith(f"`just {gate}` |"))
        cells = row.split("|")
        cells[2] = " none: not this repository's concern "
        replace(BINDING, row, "|".join(cells))(repo)
    for old, new in (
        ('alone := "--alone perf load"', 'alone := ""'),
        ("each key of flags.py --temporary on", "every flag on"),
        ("/scripts/changed.py", "/scripts/base.py"),
    ):
        replace(JUSTFILE, old, new)(repo)
    assert [line for line in red(repo) if line.startswith("justfile")] == []


def test_a_comment_does_not_count_as_the_runner_item(repo: Path) -> None:
    replace(JUSTFILE, "/scripts/changed.py\"'", "/scripts/base.py\"'\n# no changed.py here")(repo)
    assert red(repo)[0].startswith("justfile: no gate runs through `changed.py`")


def test_the_template_justfile_names_each_runner_item_in_code() -> None:
    code = conformance.code_of(TEMPLATE_JUSTFILE)
    for item in ("changed.py", "RATCHET_JOBS", "--alone", "--temporary"):
        assert item in code, item


def test_a_repository_whose_runner_is_not_just_is_not_read_for_recipes(repo: Path) -> None:
    replace(BINDING, "| tier0-tree | `just tier0-tree` |\n", "")(repo)
    remove(JUSTFILE)(repo)
    assert [line.split(":")[0] for line in red(repo)] == [BINDING]
    assert conformance.definition(TEMPLATE_JUSTFILE, "no-such-recipe") == ()
