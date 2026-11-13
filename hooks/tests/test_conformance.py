"""The conformance gate: green on the pristine templates, and one red line for each fault."""

import atexit
import functools
import re
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path

import pytest

import application_types
import conformance
import justfile_text
import markdown_text
from tests import adopted
from tests.repos import repository
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


def start(finding: conformance.Finding) -> tuple[str, ...]:
    """The template text an agent starts from, for a finding an agent fixes."""
    assert isinstance(finding.fix, conformance.Agent), finding
    return finding.fix.start


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
        (
            remove("docs/disputes"),
            "docs/disputes/.gitkeep: the directory docs/disputes is missing",
        ),
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


def test_a_missing_kind_of_door_and_a_door_with_no_paths_are_the_owner_s_to_settle(
    repo: Path,
) -> None:
    replace(MANIFEST, 'on = "removal"\n', 'on = "rewrite"\n')(repo)
    [missing] = conformance.findings(repo)
    assert missing.problem.startswith("no [[door]] with on = 'removal'")
    assert isinstance(missing.fix, conformance.Owner)
    assert missing.fix.ask.startswith("Does this repository have a door of kind 'removal'")
    write(MANIFEST, (pristine() / MANIFEST).read_text(encoding="utf-8"))(repo)
    bound = 'on = "removal"\npaths = [\n    "bound/**",\n]'
    replace(MANIFEST, bound, 'on = "removal"\npaths = []')(repo)
    [unbound] = conformance.findings(repo)
    assert unbound.problem.startswith("door 'contracts' has no paths yet")
    assert isinstance(unbound.fix, conformance.Owner)
    assert unbound.fix.ask.startswith("Which paths shut the door 'contracts' (Contract artefacts")


def test_the_life_events_question_shows_the_signs_the_tree_holds(tmp_path: Path) -> None:
    files = {
        str(path.relative_to(pristine())): path.read_text(encoding="utf-8")
        for path in pristine().rglob("*")
        if path.is_file() and ".git" not in path.parts
    }
    files[BINDING] = files[BINDING].replace("- Life events: none\n", "")
    files["fly.toml"] = "app = 'a'\n"
    root = repository(tmp_path / "repo", files)
    [event] = [f for f in conformance.findings(root) if "Life events" in f.problem]
    assert isinstance(event.fix, conformance.Owner)
    assert event.fix.evidence == ("`fly.toml` matches `fly.toml`, a sign of `deployed`",)


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
    assert isinstance(finding.fix, conformance.Auto)
    assert finding.fix.does == "copy the template's `hooks-install` recipe"
    replace(JUSTFILE, "set shell", 'import "more.just"\n\nset shell')(repo)
    write("more.just", "hooks-install:\n    echo installed\n")(repo)
    assert red(repo) == [], "a recipe an imported file defines is the justfile's own"


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
            "justfile: not every tier runs plugin_pin.py, so a local run can use a plugin other"
            " than"
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


CI, DEEP = ".github/workflows/ci.yml", ".github/workflows/deep.yml"


def without_variable(repo: Path, name: str) -> None:
    text = (repo / JUSTFILE).read_text(encoding="utf-8")
    (repo / JUSTFILE).write_text(
        re.sub(rf"^{name} :=.*\n", "", text, flags=re.MULTILINE), encoding="utf-8"
    )


@pytest.mark.parametrize(("name", "workflow"), [("tier1_gates", CI), ("tier2_gates", DEEP)])
def test_a_tier_list_a_workflow_reads_and_the_justfile_lacks_is_named_with_the_template_s(
    repo: Path, name: str, workflow: str
) -> None:
    # Why: observed on a forge, 2026-11-12: an 0.9 adoption's `shape` job failed with "does not
    # contain variable", and nothing in the upgrade had named the list.
    without_variable(repo, name)
    found = conformance.findings(repo)
    assert len(found) == 1, found
    assert found[0].path == JUSTFILE
    assert found[0].problem.startswith(
        f"has no `{name}` variable, which the template's `shape` job of `"
    )
    assert f"`{Path(workflow).name}`" in found[0].problem
    assert start(found[0]) == conformance.template_definition(name)
    assert start(found[0])[-1].startswith(f"{name} :=")


def test_a_tier_list_may_be_empty_or_exported(repo: Path) -> None:
    replace(JUSTFILE, 'tier1_sharded := "test mutation-changed"', 'tier1_sharded := ""')(repo)
    replace(JUSTFILE, "tier2_gates :=", "export tier2_gates :=")(repo)
    assert red(repo) == []


def test_a_tier_list_is_asked_wherever_a_tier_workflow_exists(repo: Path) -> None:
    names = ("tier1_gates", "tier1_sharded", "tier2_gates", "tier2_sharded")
    for name in names:
        without_variable(repo, name)
    for workflow in (CI, DEEP):
        text = (repo / workflow).read_text(encoding="utf-8")
        (repo / workflow).write_text(text.replace("--evaluate", "--list"), encoding="utf-8")
    asked = [line for line in red(repo) if "variable" in line]
    assert [line.split("`")[1] for line in asked] == list(names)
    remove(CI, DEEP)(repo)
    assert [line for line in red(repo) if "variable" in line] == []


def test_the_template_justfile_names_each_runner_item_in_code() -> None:
    code = justfile_text.code_of(TEMPLATE_JUSTFILE)
    for item in ("changed.py", "RATCHET_JOBS", "--alone", "--temporary"):
        assert item in code, item


def test_a_repository_whose_runner_is_not_just_is_not_read_for_recipes(repo: Path) -> None:
    replace(BINDING, "| tier0-tree | `just tier0-tree` |\n", "")(repo)
    remove(JUSTFILE)(repo)
    assert [line.split(":")[0] for line in red(repo)] == [BINDING]
    assert justfile_text.definition(TEMPLATE_JUSTFILE, "no-such-recipe") == ()


LATER = "| rolls back safely | later: life:deployed and code:stored-format |"
GATE = "docs/process/binding.md"
GATES = "## Gates"


def waiting(root: Path, question: str, trigger: str) -> None:
    """Rebind a Gates row to ``later: <trigger>``, whatever its Tool cell said."""
    path = root / BINDING
    text = path.read_text(encoding="utf-8")
    cell = rf"^(\| {re.escape(question)} \|)[^|]*\|"
    bound = re.sub(cell, rf"\1 later: {trigger} |", text, flags=re.MULTILINE)
    assert bound != text, question
    path.write_text(bound, encoding="utf-8")


def test_a_row_may_wait_for_its_trigger_and_needs_no_bite_until_it_is_bound(repo: Path) -> None:
    # Why: ADR 0028. The pristine binding bound `rolls back safely` to `later:` and planted no
    # bite for it; binding it to a tool without one is red.
    assert LATER in (repo / BINDING).read_text(encoding="utf-8")
    assert red(repo) == []
    replace(BINDING, LATER, "| rolls back safely | a tool |")(repo)
    assert red(repo) == [
        (
            "quality/bites.toml: gate 'rollback' (rolls back safely) is bound to a tool and no"
            " [[bite]] plants a fault for it"
        )
    ]


def test_a_later_row_with_the_wrong_trigger_names_the_right_one(repo: Path) -> None:
    replace(BINDING, LATER, "| rolls back safely | later: code:stored-format |")(repo)
    assert red(repo) == [
        (
            f"{GATE}: gate 'rolls back safely' says `later: code:stored-format`, but its trigger"
            " is `life:deployed and code:stored-format`:"
            " write `later: life:deployed and code:stored-format`"
        )
    ]


def test_a_gate_that_waits_is_not_an_answered_gate_for_the_runner() -> None:
    binding = (
        "# Binding\n\n## Gates\n\n| Question | Tool | Tier | Threshold source | Entry point |\n"
        "| --- | --- | --- | --- | --- |\n"
        "| a | a tool | 1 | | `just a` |\n| b | later: code:listener | 1 | | `just b` |\n"
        "| c | none: no | 1 | | `just c` |\n"
    )
    assert conformance.answered_gates(binding) == {"a"}


def test_the_telemetry_section_is_asked_while_one_of_its_rows_is_bound(repo: Path) -> None:
    heading = "## Telemetry"
    replace(BINDING, heading, "## Spans")(repo)
    assert red(repo) == [f"{GATE}: has no '{heading}' section, or it is empty"]
    waiting(repo, "telemetry arrives", "life:deployed")
    assert red(repo) == [f"{GATE}: has no '{heading}' section, or it is empty"]
    waiting(repo, "telemetry emitted", "code:entry-point")
    assert red(repo) == []


def test_both_sections_may_go_while_all_their_rows_wait(repo: Path) -> None:
    rows = {
        "telemetry emitted": "code:entry-point",
        "telemetry arrives": "life:deployed",
        "changed flags off and on": "code:flag",
        "no flag past its removal date": "code:flag",
    }
    for row, trigger in rows.items():
        waiting(repo, row, trigger)
    replace(BINDING, "## Telemetry", "## Spans")(repo)
    replace(BINDING, "## Feature flags", "## Toggles")(repo)
    assert red(repo) == []


def test_the_life_events_line_is_asked_for_and_names_only_known_events(repo: Path) -> None:
    line = "- Life events: none\n"
    replace(BINDING, line, "")(repo)
    [found] = conformance.findings(repo)
    assert found.path == BINDING
    assert found.problem.startswith("has no '- Life events:' line; add it beside")
    assert isinstance(found.fix, conformance.Owner)
    assert found.fix.ask.startswith("Which of these has already happened to this project:")
    assert (
        "`deployed`, when it runs somewhere people other than the owner can reach" in found.fix.ask
    )
    assert found.fix.evidence == ("no file in the tree is a sign of a life event",)
    owed = application_types.load().owed_by("irreplaceable-data")
    assert f"`irreplaceable-data` owes the Gates rows {', '.join(owed)}" in found.fix.then
    assert (
        "`none` turns off the owner's review of one-way doors and unflagged surface"
        in found.problem
    )
    replace(BINDING, "- Application types: cli\n", f"- Application types: cli\n{line}")(repo)
    replace(BINDING, line, "- Life events: deployed, `irreplaceable-data`\n")(repo)
    assert red(repo) == []
    replace(BINDING, "deployed,", "famous,")(repo)
    assert red(repo) == [
        (
            f"{GATE}: '- Life events:' names 'famous'; the events are"
            " deployed, irreplaceable-data, depended-on"
        )
    ]


def test_the_triggers_table_is_written_from_the_template_s_text(repo: Path) -> None:
    replace(BINDING, "## Triggers\n", "## Hunches\n")(repo)
    [found] = conformance.findings(repo)
    assert found.problem.startswith("has no '## Triggers' section; add the template's")
    assert isinstance(found.fix, conformance.Auto)
    assert found.fix.does == "add the template's '## Triggers' section"
    after = found.fix.want((repo / BINDING).read_text(encoding="utf-8"))
    table = markdown_text.table(after, "## Triggers")
    assert table is not None
    assert table.header == ("Kind", "Paths", "Pattern")
    assert "| <kind> |" not in markdown_text.section(after, "## Triggers")


def test_the_triggers_table_needs_a_row_per_kind_a_question_of_the_types_names(
    repo: Path,
) -> None:
    assert "| listener |" not in (repo / BINDING).read_text(encoding="utf-8")
    assert red(repo) == [], "a command line tool is never asked about listeners"
    replace(BINDING, "| config-read | none: the self-test has none | |\n", "")(repo)
    [line] = red(repo)
    assert line.startswith(f"{GATE}: the Triggers table has no row for kind 'config-read', which")
    assert "(a read of a setting from the environment or a configuration file)" in line
    assert line.endswith("write its Paths, or `none: <reason>`")


def test_a_triggers_row_with_an_unknown_kind_or_a_bad_pattern_is_red(repo: Path) -> None:
    none = "| flag | none: the self-test has none | |"
    replace(BINDING, none, "| flag | none: no | |\n| gizmo | x | |")(repo)
    [line] = red(repo)
    assert line.startswith(f"{GATE}: the Triggers table names kind 'gizmo', which the catalogue")
    replace(BINDING, "| gizmo | x | |", "| gizmo | x | ( |")(repo)
    [line] = red(repo)
    assert line.startswith(f"{GATE}: Triggers row 'gizmo' has a Pattern that is not a regex")


def test_a_dropped_row_of_the_catalogue_prints_the_template_row_to_add(repo: Path) -> None:
    row = "| backup restores | a tool | weekly | | `just restore-drill` |\n"
    replace(BINDING, row, "")(repo)
    found = conformance.findings(repo)[0]
    assert found.problem == (
        "the Gates table has no 'backup restores' row; a row is bound or none, never dropped;"
        " add the template's row, bound, or `later: life:irreplaceable-data` only while that"
        " trigger has not fired"
    )
    assert isinstance(found.fix, conformance.Auto)
    assert found.fix.does == "add the 'backup restores' row as `later: life:irreplaceable-data`"
    written = found.fix.want((repo / BINDING).read_text(encoding="utf-8"))
    assert "| backup restores | later: life:irreplaceable-data | weekly | |" in written
    replace(BINDING, "- Life events: none", "- Life events: irreplaceable-data")(repo)
    [fired] = [f for f in conformance.findings(repo) if "'backup restores'" in f.problem]
    assert start(fired) == ("| backup restores | | weekly | | `just restore-drill` |",)
    assert isinstance(fired.fix, conformance.Agent)
    needs = (
        "binding 'backup restores' to a tool needs a [[bite]] for gate 'restore-drill' in"
        " quality/bites.toml"
    )
    assert fired.fix.then == (
        "the binding declares the life event 'irreplaceable-data'",
        needs,
    )


def test_a_missing_types_line_names_the_triggers_each_type_will_ask(repo: Path) -> None:
    replace(BINDING, "- Application types: cli\n", "")(repo)
    [found] = conformance.findings(repo)
    assert found.problem.startswith("has no '- Application types:' line")
    assert isinstance(found.fix, conformance.Agent)
    asks = application_types.load().kinds_asked({"service"})
    assert f"`service` asks the Triggers table for the kinds {', '.join(sorted(asks))}" in (
        found.fix.then
    )


def test_nothing_hides_a_gates_row_behind_the_types_line(tmp_path: Path) -> None:
    root = adopted.copy(adopted.adopted("0.8.2"), tmp_path / "repo")

    def gates() -> list[str]:
        return [f.problem for f in conformance.findings(root) if "Gates table has no" in f.problem]

    without = gates()
    text = (root / BINDING).read_text(encoding="utf-8")
    held = {
        question for question, _ in markdown_text.table_cells(markdown_text.section(text, GATES))
    }
    owed = application_types.load().questions - held
    assert owed, "an 0.8.2 adoption lacks rows the catalogue has since gained"
    for question in owed:
        assert any(line.startswith(application_types.dropped(question)) for line in without)
    assert len(without) == len(set(without)), "no row is asked twice"
    assert any("'backup restores'" in line for line in without)
    assert any("types:' line" in f.problem for f in conformance.findings(root))
    (root / BINDING).write_text("- Application types: cli\n" + text, encoding="utf-8")
    assert gates() == without
    assert not any("types:' line" in f.problem for f in conformance.findings(root))


def test_every_question_of_the_catalogue_has_a_row_in_the_template_binding() -> None:
    for question in conformance.application_types.load().questions:
        assert conformance.template_gate_row(question).startswith(f"| {question} |")


WEEKLY = ".github/workflows/weekly.yml"


def test_a_workflow_with_the_toolchain_placeholder_in_front_of_the_template_s_step_is_red(
    repo: Path,
) -> None:
    replace(WEEKLY, conformance.TOOLCHAIN_STEP, conformance.TOOLCHAIN_PLACEHOLDER)(repo)
    [found] = conformance.findings(repo)
    assert found.path == WEEKLY
    assert found.problem.startswith("line 39 in job `weekly` still says '# bind: toolchain setup'")
    assert isinstance(found.fix, conformance.Agent)


def test_a_toolchain_the_project_bound_is_not_asked_for_again(repo: Path) -> None:
    marker = conformance.TOOLCHAIN_PLACEHOLDER
    project = "      - uses: actions/setup-node@v5"
    replace(WEEKLY, conformance.TOOLCHAIN_STEP, f"{marker}\n{project}")(repo)
    assert red(repo) == [], "the comment may stay above the project's own step"
    text = (repo / WEEKLY).read_text(encoding="utf-8")
    (repo / WEEKLY).write_text(text.rstrip("\n") + f"\n{marker}", encoding="utf-8")
    assert red(repo) == [], "a marker with nothing after it has no template line to match"
    write(".github/workflows/release.yml", f"concurrency: cache\n{marker}\n- run: x\n")(repo)
    assert red(repo) == [], "a workflow the template lacks has no unbound toolchain to compare"


def test_a_question_the_catalogue_asks_is_not_asked_twice_of_a_binding_without_types(
    repo: Path,
) -> None:
    replace(BINDING, "- Application types: cli\n", "")(repo)
    replace(BINDING, "| telemetry emitted |", "| tracing |")(repo)
    lines = red(repo)
    assert len([line for line in lines if "'telemetry emitted'" in line]) == 1
    replace(BINDING, "| feature flags declared |", "| flags |")(repo)
    assert f"{BINDING}: the Gates table has no 'feature flags declared' row" in red(repo)


def record(name: str, text: str) -> Mutate:
    return write(f"docs/incidents/{name}.md", text)


def test_an_incident_record_names_a_bite_the_registry_holds(repo: Path) -> None:
    assert red(repo) == [], "no incident, nothing to hold"
    record("2026-01-01-outage", "# Outage\n\nbite: lint/a fault\n")(repo)
    assert red(repo) == []
    record("2026-01-02-slow", "# Slow\n\n- bite: `fmt-check/a fault`\n- bite: lint/a fault\n")(repo)
    assert red(repo) == []
    record("2026-01-03-none", "# None\n\nIt broke.\n")(repo)
    record("2026-01-04-gone", "# Gone\n\nbite: lint/never planted\n")(repo)
    assert red(repo) == [
        (
            "docs/incidents/2026-01-03-none.md: names no bite; add a line `bite: <gate>/<name>`"
            " for a [[bite]] of quality/bites.toml that reproduces it"
        ),
        (
            "docs/incidents/2026-01-04-gone.md: names bite 'lint/never planted', which"
            " quality/bites.toml does not hold"
        ),
    ]


def test_the_incidents_gate_judges_the_records_alone(
    repo: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    remove("GLOSSARY.md")(repo)
    assert conformance.main(["conformance.py", "--incidents", str(repo)]) == 0
    assert capsys.readouterr().out == "incidents: green (0 failures)\n"
    record("2026-01-03-none", "# None\n")(repo)
    assert conformance.main(["conformance.py", "--incidents", str(repo)]) == 1
    assert capsys.readouterr().out.splitlines()[-1] == "incidents: red (1 failures)"
    monkeypatch.chdir(repo)
    assert conformance.main(["conformance.py", "--incidents"]) == 1
