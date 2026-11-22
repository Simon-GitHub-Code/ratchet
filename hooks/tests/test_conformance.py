import atexit
import functools
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path

import pytest

import application_types
import conformance
import justfile_text
import markdown_text
import owed
import project_bite
import upgrade
from tests import adopted, repos
from tests.repos import git, repository
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


def test_a_red_finding_is_followed_by_what_it_asks_next(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    then = ("the recipes `a` do not depend on `install`", "the lockfile is not named")
    finding = conformance.Finding("justfile", "no install", conformance.Agent(then=then))
    edited = conformance.Finding(
        "ci.yml",
        "bare key",
        conformance.Edit("end it", lambda text: text or "", conformance.Agent(then=then[:1])),
    )
    owner = conformance.Finding("binding.md", "asks", conformance.Agent(start=("template",)))
    made = conformance.Finding(
        "flags.toml", "made", conformance.Auto("make it", lambda text: text or "")
    )
    findings = [finding, edited, owner, made]
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(conformance, "findings", lambda _root, **_: findings)
        assert conformance.run(repo) == 1
    assert capsys.readouterr().out.splitlines()[:7] == [
        "justfile: no install",
        f"  then: {then[0]}",
        f"  then: {then[1]}",
        "ci.yml: bare key",
        f"  then: {then[0]}",
        "binding.md: asks",
        "flags.toml: made",
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
        (remove(ADR, conformance.SELF_TEST_RECORD), "docs/adr: no NNNN-<slug>.md record"),
        (
            write("docs/adr/0004-later.md", "# Later\n"),
            "docs/adr: numbering has gaps or duplicates: [1, 2, 4]",
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
            replace(MANIFEST, '    "guards/**",\n', ""),
            "guards/manifest.toml: 'guards/**' is in no group",
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


DEEP_RECORD_LINE = '    ".git/ratchet-deep.jsonl",\n'
DEEP_RECORD_FINDING = (
    "guards/manifest.toml: '.git/ratchet-deep.jsonl' is not in an edit = \"deny\" group"
)


def test_a_manifest_that_lets_an_agent_write_the_deep_record_is_the_owner_s_and_can_be_owed(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    replace(MANIFEST, DEEP_RECORD_LINE, "")(repo)
    [finding] = conformance.findings(repo)
    assert str(finding) == DEEP_RECORD_FINDING
    assert isinstance(finding.fix, conformance.Owner)
    assert finding.fix.ask.startswith("Add '.git/ratchet-deep.jsonl' to the `local-git` group")
    entry = owed.Entry(finding.path, finding.rule, finding.problem, "0.13.0")
    (repo / owed.LEDGER).write_text(owed.render([entry]), encoding="utf-8")
    capsys.readouterr()
    assert conformance.run(repo) == 0
    assert capsys.readouterr().out == (
        f"owed: {DEEP_RECORD_FINDING}\nconformance: green (0 failures; 1 owed since an upgrade,"
        " listed in quality/owed.toml)\n"
    )


def test_a_deny_pattern_that_covers_the_deep_record_needs_no_line_of_its_own(repo: Path) -> None:
    replace(MANIFEST, DEEP_RECORD_LINE, '    ".git/ratchet-*",\n')(repo)
    assert red(repo) == []


def test_the_life_events_question_shows_the_signs_the_tree_holds(tmp_path: Path) -> None:
    files = {
        str(path.relative_to(pristine())): path.read_text(encoding="utf-8")
        for path in pristine().rglob("*")
        if path.is_file() and ".git" not in path.parts
    }
    files[BINDING] = files[BINDING].replace("- Life events: none\n", "")
    files[BINDING] = files[BINDING].replace(
        "| deployed | none: the self-test has no such file |", "| deployed | `launch.cfg` |"
    )
    files["launch.cfg"] = "app = 'a'\n"
    root = repository(tmp_path / "repo", files)
    [event] = [f for f in conformance.findings(root) if "Life events" in f.problem]
    assert isinstance(event.fix, conformance.Owner)
    assert event.fix.evidence == ("`launch.cfg` matches `launch.cfg`, a sign of `deployed`",)


def test_a_self_test_mutation_refuses_a_template_it_no_longer_fits(repo: Path) -> None:
    with pytest.raises(SystemExit, match="has no 'absent text' to mutate"):
        conformance.rewrite(MANIFEST, "absent text", "anything")(repo)
    with pytest.raises(SystemExit, match="has no 'absent text' to mutate"):
        conformance.reregister("absent text", "anything")(repo)


JUSTFILE = "justfile"
TEMPLATE_JUSTFILE = (SKILLS / "adopt/templates/justfile").read_text(encoding="utf-8")


@pytest.mark.skipif(shutil.which("just") is None, reason="just is not installed")
@pytest.mark.parametrize(
    "case",
    [
        ({"RATCHET_JOBS": "2"}, "3", "2"),
        ({}, "3", "3"),
        ({}, None, str(os.cpu_count())),
    ],
)
def test_the_template_caps_the_gates_by_the_environment_then_git_then_every_core(
    tmp_path: Path, case: tuple[dict[str, str], str | None, str]
) -> None:
    environment, configured, expected = case
    git(tmp_path, "init", "-q")
    if configured is not None:
        git(tmp_path, "config", "ratchet.jobs", configured)
    (tmp_path / JUSTFILE).write_text(TEMPLATE_JUSTFILE, encoding="utf-8")
    shell = {k: v for k, v in os.environ.items() if k != "RATCHET_JOBS"}
    shell |= {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull, **environment}
    result = subprocess.run(
        ["just", "--evaluate", "jobs"],  # noqa: S607 # -- just is resolved from PATH on purpose
        cwd=tmp_path,
        env=shell,
        capture_output=True,
        text=True,
        check=False,
    )
    assert (result.stdout.strip(), result.stderr) == (expected, "")


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
    replace(JUSTFILE, "hooks-install: _plugin\n", "hooks-installs: _plugin\n")(repo)
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
    pin = f"- Ratchet plugin: 0.0.0 at {'0' * 40} tree {'0' * 40}"
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
        (
            "justfile: `pre-commit` neither runs plugin_pin.py first nor carries"
            " `[no-exit-message]`, so the commit hook can run a plugin no check held to the"
            " release"
        ),
    ]


def test_the_pin_line_says_what_a_copy_without_history_cannot(tmp_path: Path) -> None:
    (tmp_path / ".claude-plugin").mkdir()
    (tmp_path / ".claude-plugin/plugin.json").write_text('{"version": "1.2.3"}')
    line = conformance.plugin_pin.line_for(tmp_path)
    tree = conformance.plugin_pin.copy_tree_id(tmp_path)
    assert line == f"- Ratchet plugin: 1.2.3 at <the full commit of 1.2.3> tree {tree}"


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
            replace(JUSTFILE, "ratchet: _plugin\n    just hooks-installed\n", "ratchet: _plugin\n"),
            "justfile: the `ratchet` recipe raises the thresholds without asking",
        ),
        (
            replace(JUSTFILE, '"RATCHET_JOBS"', '"JOBS"'),
            "justfile: passes `--jobs` without reading RATCHET_JOBS",
        ),
        (
            replace(
                JUSTFILE, 'alone := "--alone perf load mutation test-stability"', 'alone := ""'
            ),
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
        ('alone := "--alone perf load mutation test-stability"', 'alone := ""'),
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
    replace(JUSTFILE, '"test mutation-changed bite rest"', '"bite rest"')(repo)
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
    path = root / BINDING
    text = path.read_text(encoding="utf-8")
    cell = rf"^(\| {re.escape(question)} \|)[^|]*\|"
    bound = re.sub(cell, rf"\1 later: {trigger} |", text, flags=re.MULTILINE)
    assert bound != text, question
    path.write_text(bound, encoding="utf-8")


def test_a_row_may_wait_for_its_trigger_and_needs_no_bite_until_it_is_bound(repo: Path) -> None:
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
    assert "| scheduled-work |" in (repo / BINDING).read_text(encoding="utf-8")
    assert red(repo) == []
    listener = "| listener | none: the self-test has none | |\n"
    replace(BINDING, listener, "")(repo)
    [line] = red(repo)
    assert "no row for kind 'listener', whose arrival reopens the architecture (ADR 0052)" in line
    replace(BINDING, "| config-read |", listener + "| config-read |")(repo)
    assert red(repo) == []
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
    asks = application_types.load().kinds_required({"service"})
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
TEMPLATE_WORKFLOWS = sorted((SKILLS / "adopt/templates/.github/workflows").glob("*.yml"))
SHAPES = {
    "merge.yml": conformance.MERGE_SHAPE,
    "weekly.yml": conformance.WEEKLY_SHAPE,
    "ci.yml": conformance.CI_SHAPE + conformance.TIER_STEP_SHAPE["ci.yml"],
    "deep.yml": conformance.CI_SHAPE + conformance.TIER_STEP_SHAPE["deep.yml"],
}


def test_a_workflow_with_the_toolchain_placeholder_in_front_of_the_template_s_step_is_red(
    repo: Path,
) -> None:
    replace(WEEKLY, conformance.TOOLCHAIN_STEP, conformance.TOOLCHAIN_PLACEHOLDER)(repo)
    [found] = conformance.findings(repo)
    assert found.path == WEEKLY
    assert found.problem.startswith(
        "line 36 in job `weekly` still says 'bind: toolchain setup', so no step sets up"
    )
    assert isinstance(found.fix, conformance.Agent)


def test_the_placeholder_step_is_red_wherever_it_stands_since_it_exits_non_zero(
    repo: Path,
) -> None:
    project = "      - uses: actions/setup-node@v5"
    placeholder = conformance.TOOLCHAIN_PLACEHOLDER
    replace(WEEKLY, conformance.TOOLCHAIN_STEP, f"{placeholder}\n{project}")(repo)
    [found] = conformance.findings(repo)
    assert "still says 'bind: toolchain setup'" in found.problem


def test_a_toolchain_the_project_bound_below_an_older_template_s_comment_is_not_asked_again(
    repo: Path,
) -> None:
    marker = f"      # {conformance.TOOLCHAIN_MARKER}, pinned, with the setup action's own cache on"
    project = "      - uses: actions/setup-node@v5"
    replace(WEEKLY, conformance.TOOLCHAIN_STEP, f"{marker}\n{project}")(repo)
    assert red(repo) == [], "the comment may stay above the project's own step"
    text = (repo / WEEKLY).read_text(encoding="utf-8")
    (repo / WEEKLY).write_text(text.rstrip("\n") + f"\n{marker}", encoding="utf-8")
    assert red(repo) == [], "a marker with nothing after it has no template line to match"
    write(".github/workflows/release.yml", f"concurrency: cache\n{marker}\n- run: x\n")(repo)
    assert red(repo) == [], "a workflow the template lacks has no unbound toolchain to compare"


def test_an_older_template_s_comment_in_front_of_the_template_s_step_is_red(repo: Path) -> None:
    marker = f"      # {conformance.TOOLCHAIN_MARKER}, pinned, with the setup action's own cache on"
    replace(WEEKLY, conformance.TOOLCHAIN_STEP, marker)(repo)
    [found] = conformance.findings(repo)
    assert "still says '# bind: toolchain setup' and the next line is the template's" in (
        found.problem
    )


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


def test_a_default_branch_found_only_through_the_reflog_is_red(repo: Path) -> None:
    git(repo, "init", "-q", "-b", "trunk")
    git(repo, "config", "user.email", "t@example.invalid")
    git(repo, "config", "user.name", "t")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "chore: start")
    lines = red(repo)
    assert len(lines) == 1
    assert "found only through the main work tree's history of branch switches" in lines[0]
    git(repo, "branch", "-m", "trunk", "main")
    assert red(repo) == []


@pytest.mark.parametrize("path", TEMPLATE_WORKFLOWS, ids=lambda path: path.name)
def test_every_marker_of_a_workflow_shape_is_in_its_own_template(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    absent = [m for m in conformance.workflow_markers(path.name) if m not in text]
    assert absent == [], f"{path.name} lacks its own shape's markers"
    for marker, _ in SHAPES[path.name]:
        assert marker in text


@pytest.mark.parametrize("path", TEMPLATE_WORKFLOWS, ids=lambda path: path.name)
def test_every_permission_and_run_step_of_a_template_is_named_by_a_marker_of_its_shape(
    path: Path,
) -> None:
    unmarked = conformance.unmarked_template_lines(path.name, path.read_text(encoding="utf-8"))
    assert unmarked == [], "\n".join(
        f"{path.name}:{number}: {line}: add a marker with its meaning to the shape beside"
        " MERGE_SHAPE, or declare it in EXEMPT_STEPS with a reason"
        for number, line in unmarked
    )


def test_every_exemption_is_still_in_a_template_and_gives_its_reason() -> None:
    texts = [path.read_text(encoding="utf-8") for path in TEMPLATE_WORKFLOWS]
    for held, reason in conformance.EXEMPT_STEPS:
        assert any(held in text for text in texts), held
        assert len(reason.split()) > 3


def test_a_step_or_permission_no_marker_names_is_listed_with_its_line() -> None:
    text = (
        "permissions:\n"
        "  # a comment is no entry\n"
        "  contents: read\n"
        "\n"
        "  issues: write\n"
        "  models: read\n"
        "jobs:\n"
        "  merge:\n"
        "    permissions: read-all\n"
        "    steps:\n"
        "      - run: python3 merge_ready.py\n"
        "      - run: echo nothing\n"
        "      - name: a block\n"
        "        run: |\n"
        "          # forge_runs.py is only a comment\n"
        "          echo still nothing\n"
        "        if: always()\n"
        "      - run: |\n"
        "          echo closingIssuesReferences\n"
    )
    assert conformance.unmarked_template_lines("merge.yml", text) == [
        (6, "models: read"),
        (12, "- run: echo nothing"),
        (14, "run: |\n          echo still nothing"),
    ]


TEMPLATE_ROW = (
    "| `suite` | `just suite-report` | <`path` of the coverage report, Cobertura or LCOV>,"
    " <`path` of the test results, JUnit XML> | `just test`, `just coverage`,"
    " `just diff-coverage`, `just test-counts` | `just test` |"
)
SELF_TEST_NONE = "| none: the self-test shares no report | none | none | none | none |"
SHARED = (
    "| suite | `just build` | `suite.xml` | `just coverage`, `just diff-coverage` |"
    " `just coverage` |"
)
SCRIPT = '{{plugin_py}} "{{ratchet_plugin}}/scripts/report.py" suite'


def sharing(repo: Path) -> None:
    replace(BINDING, SELF_TEST_NONE, SHARED)(repo)


def test_a_reader_that_opens_its_shared_reports_path_itself_is_red_with_the_line(
    repo: Path,
) -> None:
    sharing(repo)
    assert red(repo) == []
    replace("justfile", "coverage: _plugin\n", "coverage: _plugin\n    cat suite.xml\n")(repo)
    problem = (
        "justfile: the recipe 'coverage' reads the shared report 'suite' (suite.xml) other than"
        " as the --path argument of scripts/report.py: cat suite.xml"
    )
    assert red(repo) == [problem]


@pytest.mark.parametrize(
    "written", ["--path suite.xml", "--path 'suite.xml'", '--path="suite.xml"']
)
def test_a_reader_that_asks_the_script_for_the_path_is_green(repo: Path, written: str) -> None:
    sharing(repo)
    replace(
        "justfile",
        "coverage: _plugin\n",
        f"coverage: _plugin\n    cat $({SCRIPT} --reader coverage {written})\n",
    )(repo)
    assert red(repo) == []


def test_a_path_named_beside_a_call_of_the_script_is_still_red(repo: Path) -> None:
    sharing(repo)
    line = f"{SCRIPT} --reader coverage --path suite.xml && cat suite.xml"
    replace("justfile", "coverage: _plugin\n", f"coverage: _plugin\n    {line}\n")(repo)
    assert [found.split(": ")[-1] for found in red(repo)] == [line]


def test_a_path_named_in_a_comment_or_by_a_recipe_that_does_not_read_is_green(repo: Path) -> None:
    sharing(repo)
    replace("justfile", "coverage: _plugin\n", "coverage: _plugin\n    # cat suite.xml\n")(repo)
    replace("justfile", "lint:\n", "lint:\n    cat suite.xml\n")(repo)
    assert red(repo) == []


def test_a_continued_line_is_one_line(repo: Path) -> None:
    sharing(repo)
    replace(
        "justfile",
        "coverage: _plugin\n",
        f"coverage: _plugin\n    {SCRIPT} --reader coverage --path suite.xml \\\n      && cat x\n",
    )(repo)
    assert red(repo) == []


@pytest.mark.parametrize("kind", ["producer", "reader"])
def test_a_shared_report_names_recipes_the_justfile_defines(repo: Path, kind: str) -> None:
    sharing(repo)
    row = (
        SHARED.replace("just build", "just gone")
        if kind == "producer"
        else SHARED.replace("just diff-coverage", "just gone")
    )
    replace(BINDING, SHARED, row)(repo)
    problem = (
        "justfile: the shared report 'suite' names the recipe 'gone', which the justfile does not"
        " define"
    )
    assert red(repo) == [problem]


def test_a_binding_without_the_shared_reports_section_gets_the_templates(repo: Path) -> None:
    replace(BINDING, conformance.report.SECTION, "## Elsewhere")(repo)
    found = conformance.findings(repo)
    problem = (
        "has no '## Shared reports' section; add the template's, bind `suite-report` first to run"
        " the suite once, then bind the `test`, `coverage`, `diff-coverage` and `test-counts`"
        " gates as readers of the shared suite report (ADR 0071)"
    )
    assert [finding.problem for finding in found] == [problem]
    assert isinstance(found[0].fix, conformance.Auto)


def test_a_shared_reports_table_the_template_left_is_red(repo: Path) -> None:
    replace(
        BINDING,
        SELF_TEST_NONE,
        TEMPLATE_ROW,
    )(repo)
    assert red(repo) == [f"{BINDING}: {conformance.report.PLACEHOLDER}"]


def test_a_repository_with_no_binding_has_no_shared_reports_to_hold(repo: Path) -> None:
    remove(BINDING)(repo)
    assert not any("shared report" in line for line in red(repo))


def test_a_repository_with_no_justfile_has_no_recipes_to_hold_a_report_to(repo: Path) -> None:
    sharing(repo)
    remove("justfile")(repo)
    assert not any("shared report" in line for line in red(repo))


def test_a_report_path_git_does_not_ignore_is_red_with_the_reason(repo: Path) -> None:
    sharing(repo)
    git(repo, "init", "-q")
    problem = (
        ".gitignore: the shared report 'suite' writes 'suite.xml', which git does not ignore; a"
        " report that is tracked or untracked changes the tree's content, so it is never reused"
    )
    assert red(repo) == [problem]
    write(".gitignore", "suite.xml\n")(repo)
    assert red(repo) == []


@pytest.mark.parametrize("judged", ["`just build`", "`just coverage`, `just diff-coverage`", ""])
def test_a_judge_that_is_not_exactly_one_of_the_readers_is_red(repo: Path, judged: str) -> None:
    sharing(repo)
    replace(
        BINDING,
        SHARED,
        SHARED.replace(
            "`just diff-coverage` | `just coverage` |", f"`just diff-coverage` | {judged} |"
        ),
    )(repo)
    problem = (
        f"{BINDING}: the shared reports row 'suite' needs exactly one judge in `Judged by`, and"
        " it is one of the row's readers"
    )
    assert red(repo) == [problem]


def test_a_producer_the_template_defines_and_the_justfile_lacks_is_copied(repo: Path) -> None:
    replace(BINDING, SELF_TEST_NONE, SHARED.replace("just build", "just suite-report"))(repo)
    text = (repo / "justfile").read_text(encoding="utf-8")
    block = "\n".join(justfile_text.definition(text, "suite-report"))
    assert block
    replace("justfile", block, "")(repo)
    found = [f for f in conformance.findings(repo) if "`suite-report`" in f.problem]
    problem = (
        "has no `suite-report` recipe, which the shared report 'suite' names; copy it from the"
        " template justfile; it is a placeholder for the project to bind"
    )
    assert [f.problem for f in found] == [problem]
    assert isinstance(found[0].fix, conformance.Auto)


def test_a_report_with_one_reader_is_red(repo: Path) -> None:
    sharing(repo)
    replace(BINDING, "`just coverage`, `just diff-coverage` |", "`just coverage` |")(repo)
    problem = (
        f"{BINDING}: the shared reports row 'suite' has fewer than two readers, so it is not"
        " shared: say `none: <reason>` in its Report cell"
    )
    assert red(repo) == [problem]


def test_the_template_row_is_red_until_its_paths_are_bound_and_still_brings_the_producer(
    repo: Path,
) -> None:
    replace(BINDING, SELF_TEST_NONE, TEMPLATE_ROW)(repo)
    text = (repo / "justfile").read_text(encoding="utf-8")
    block = "\n".join(justfile_text.definition(text, "suite-report"))
    replace("justfile", block, "")(repo)
    placeholder = f"{BINDING}: {conformance.report.PLACEHOLDER}"
    producer = (
        "justfile: has no `suite-report` recipe, which the shared report 'suite' names; copy it"
        " from the template justfile; it is a placeholder for the project to bind"
    )
    assert sorted(red(repo)) == sorted([placeholder, producer])


def speed_findings(repo: Path) -> list[str]:
    return [
        line
        for line in red(repo)
        if line.startswith("docs/process/binding.md") and "Incremental" in line
    ]


def test_a_speed_section_without_an_incremental_line_is_found(repo: Path) -> None:
    replace(BINDING, "- Incremental: ", "- Mode: ")(repo)
    missing = (
        "docs/process/binding.md: the Speed section has no '- Incremental:' line naming the"
        " mutation tool's incremental mode, or `none: <reason>` where the tool has none"
    )
    assert speed_findings(repo) == [missing]


def test_an_incremental_line_left_as_the_template_placeholder_is_found(repo: Path) -> None:
    replace(BINDING, "- Incremental: stated for this stack", "- Incremental: <the mode>")(repo)
    assert len(speed_findings(repo)) == 1


@pytest.mark.parametrize("value", ["none:", "none: ", "none", "none:\t"])
def test_an_incremental_line_of_none_with_no_reason_is_found(repo: Path, value: str) -> None:
    replace(BINDING, "- Incremental: stated for this stack", f"- Incremental: {value}")(repo)
    unreasoned = (
        "docs/process/binding.md: the Speed section's '- Incremental:' line says `none` with no"
        " reason: write `none: <reason>`, or name the mode"
    )
    assert speed_findings(repo) == [unreasoned]


@pytest.mark.parametrize("value", ["--mode on", "none: the tool keeps no results between runs"])
def test_an_incremental_line_naming_a_mode_or_none_with_a_reason_is_green(
    repo: Path, value: str
) -> None:
    replace(BINDING, "- Incremental: stated for this stack", f"- Incremental: {value}")(repo)
    assert speed_findings(repo) == []


def justfile_findings(repo: Path) -> list[str]:
    return [line for line in red(repo) if line.startswith("justfile") and "mutation" in line]


def bound_recipe(repo: Path) -> None:
    text = (repo / "justfile").read_text(encoding="utf-8")
    old = text[text.index("\nmutation-changed:") :].split("\n\n", 1)[0]
    new = "\nmutation-changed:\n    ./run-mutants {{mutation_incremental}} --shard {{shard}}"
    (repo / "justfile").write_text(text.replace(old, new), encoding="utf-8")


def test_a_bound_incremental_mode_the_recipe_runs_and_ci_leaves_out_is_green(repo: Path) -> None:
    bound_recipe(repo)
    assert justfile_findings(repo) == []


def test_a_recipe_that_does_not_use_the_incremental_variable_is_found(repo: Path) -> None:
    bound_recipe(repo)
    replace("justfile", "./run-mutants {{mutation_incremental}} ", "./run-mutants ")(repo)
    assert justfile_findings(repo) == [
        (
            "justfile: the recipe `mutation-changed` does not use `{{mutation_incremental}}`,"
            " so the binding's '- Incremental:' line is not what it runs; add the variable to"
            " the mutation tool's command"
        )
    ]


def test_an_incremental_variable_that_is_not_empty_in_ci_or_the_bite_is_found(repo: Path) -> None:
    bound_recipe(repo)
    switch = 'if env_var_or_default("CI", "") + env_var_or_default("RATCHET_BITING", "") == ""'
    for kept in ('if env_var_or_default("CI", "") == ""', "if true"):
        replace("justfile", switch, kept)(repo)
        assert justfile_findings(repo) == [
            (
                "justfile: `mutation_incremental` is not empty when `CI` or `RATCHET_BITING` is"
                " set; CI and the bite proof's copy both run without the cache, so give it the"
                " template's switch, which is empty when either is set"
            )
        ]
        replace("justfile", kept, switch)(repo)


def test_an_incremental_variable_left_as_the_placeholder_is_found(repo: Path) -> None:
    bound_recipe(repo)
    replace("justfile", '"stated for this stack"', '"bind: the option"')(repo)
    assert justfile_findings(repo) == [
        (
            "justfile: `mutation_incremental` still holds the template's `bind:` placeholder;"
            " bind it to the mutation tool's option that the binding's '- Incremental:' line names"
        )
    ]


def test_a_justfile_without_the_incremental_variable_is_given_the_template_s(repo: Path) -> None:
    text = (repo / "justfile").read_text(encoding="utf-8")
    dropped = "\n".join(
        line for line in text.splitlines() if not line.startswith("mutation_incremental :=")
    )
    (repo / "justfile").write_text(dropped, encoding="utf-8")
    [found] = [f for f in conformance.findings(repo) if "mutation_incremental" in f.problem]
    assert found.problem.startswith("has no `mutation_incremental` variable")
    assert isinstance(found.fix, conformance.Auto)
    assert "mutation_incremental := if env_var_or_default" in found.fix.want(dropped)


def test_an_incremental_line_of_none_wants_the_variable_empty_or_absent(repo: Path) -> None:
    replace(BINDING, "- Incremental: stated for this stack", "- Incremental: none: no cache")(repo)
    assert justfile_findings(repo) == [
        (
            "justfile: the binding's '- Incremental:' line says `none`, and `mutation_incremental`"
            ' names a mode; set it to "": a tool with no incremental mode has no option to add'
        )
    ]
    text = (repo / "justfile").read_text(encoding="utf-8")
    emptied = re.sub(
        r"^mutation_incremental :=.*$", 'mutation_incremental := ""', text, flags=re.MULTILINE
    )
    (repo / "justfile").write_text(emptied, encoding="utf-8")
    assert justfile_findings(repo) == []
    text = (repo / "justfile").read_text(encoding="utf-8")
    (repo / "justfile").write_text(
        "\n".join(x for x in text.splitlines() if not x.startswith("mutation_incremental :=")),
        encoding="utf-8",
    )
    assert justfile_findings(repo) == []


def test_a_repository_with_no_justfile_has_no_incremental_finding(repo: Path) -> None:
    remove("justfile")(repo)
    assert not [f for f in conformance.findings(repo) if "mutation_incremental" in f.problem]


COMMENTS_ROW = conformance.SELF_TEST_COMMENTS
LISTER_LINE = '    @echo "bind: list the comments of the files given'


def finding(root: Path, line: str) -> conformance.Finding:
    return next(found for found in conformance.findings(root) if str(found) == line)


def test_a_binding_without_the_comments_section_is_owed_it_from_the_template(repo: Path) -> None:
    replace(BINDING, "## Comments\n", "## Remarks\n")(repo)
    line = (
        f"{BINDING}: has no '## Comments' section, which names the lister the `comments` gate"
        " reads (ADR 0063); add the template's section and fill it"
    )
    assert line in red(repo)
    found = finding(repo, line)
    assert isinstance(found.fix, conformance.Auto)
    assert found.rule == "comments_section"


@pytest.mark.parametrize(
    ("row", "problem"),
    [
        (
            "| <`globs` of one kind of file> | <`listed`, or `none: <reason>`> |",
            "the Comments table still has the template's placeholder row",
        ),
        ("| `**` | sometimes |", "says 'sometimes': write `listed`, or `none: <reason>`"),
        ("| `**` | none: x |", "says 'none: x': write `listed`, or `none: <reason>`"),
        ("| ** | listed |", "the Comments row '**' gives no glob in backticks"),
    ],
)
def test_a_comments_table_that_is_unfilled_or_malformed_is_red(
    repo: Path, row: str, problem: str
) -> None:
    replace(BINDING, COMMENTS_ROW, row)(repo)
    assert any(problem in line for line in red(repo)), red(repo)


def test_a_tracked_text_file_no_comments_row_matches_is_red(repo: Path) -> None:
    git(repo, "init", "-q")
    replace(BINDING, COMMENTS_ROW, "| `src/**` | none: the self-test writes no code by hand |")(
        repo
    )
    lines = red(repo)
    assert (
        f"{BINDING}: '## Comments' has no row matching `justfile`; add a row for its kind,"
        " `listed` or `none: <reason>`"
    ) in lines
    assert not any("matching `README.md`" in line for line in lines), "a document needs no row"


def test_a_listed_row_needs_a_bound_lister_and_a_bite_that_plants_into_its_files(
    repo: Path,
) -> None:
    replace(BINDING, COMMENTS_ROW, "| `lib/**` | listed |")(repo)
    lines = red(repo)
    unbound = "justfile: the `comments-list` recipe still holds the template's `bind:` placeholder"
    assert unbound in lines
    unplanted = (
        "quality/bites.toml: the Comments row `lib/**` is `listed`, and no [[bite]] of gate"
        " 'comments' plants into a file it matches"
    )
    assert any(line.startswith(unplanted) for line in lines), lines
    text = (repo / "justfile").read_text(encoding="utf-8")
    bound = re.sub(
        rf"^{re.escape(LISTER_LINE)}[^\n]*$", "    ./list {{files}}", text, flags=re.MULTILINE
    )
    (repo / "justfile").write_text(bound, encoding="utf-8")
    replace(BINDING, "| `lib/**` | listed |", "| `src/**` | listed |")(repo)
    assert red(repo) == []


PLANTS = """
[[bite]]
gate = "comments"
name = "a replaced line"
replace = { path = "lib/a.txt", old = "x", new = "x // a reason" }
expect = "a comment"

[[bite]]
gate = "comments"
name = "a patched line"
patch = "quality/bites/comment.patch"
expect = "a comment"

[[bite]]
gate = "comments"
name = "a removed file"
remove = ["old/**"]
expect = "a comment"
"""
COMMENT_PATCH = """\
--- a/app/a.txt
+++ b/app/a.txt
@@ -1 +1 @@
-x
+x // a reason
"""


def test_a_replace_or_patch_bite_plants_into_its_files_and_a_remove_bite_plants_nothing(
    repo: Path,
) -> None:
    rows = "\n".join(f"| `{glob}` | listed |" for glob in ("lib/**", "app/**", "old/**"))
    replace(BINDING, COMMENTS_ROW, rows)(repo)
    registry = (repo / "quality/bites.toml").read_text(encoding="utf-8")
    write("quality/bites.toml", registry + PLANTS)(repo)
    write("quality/bites/comment.patch", COMMENT_PATCH)(repo)
    unplanted = [line for line in red(repo) if "and no [[bite]] of gate 'comments'" in line]
    only_old = (
        "quality/bites.toml: the Comments row `old/**` is `listed`, and no [[bite]] of gate"
        " 'comments' plants into a file it matches; add one, so the lister is shown to find a"
        " comment of that kind (ADR 0063)"
    )
    assert unplanted == [only_old]


@pytest.mark.parametrize(
    ("old", "problem"),
    [
        (
            f"| Files | Comments |\n| --- | --- |\n{COMMENTS_ROW}\n",
            "the section lacks its table with the columns Files | Comments",
        ),
        (f"{COMMENTS_ROW}\n", "the Comments table has no row: one row per kind of tracked file"),
    ],
)
def test_a_comments_section_with_no_table_or_no_row_is_red(
    repo: Path, old: str, problem: str
) -> None:
    replace(BINDING, old, "")(repo)
    assert any(problem in line for line in red(repo)), red(repo)


def test_the_published_interface_is_named_or_said_to_be_none_outside_a_library(
    repo: Path,
) -> None:
    published = "- Published interface: none: not a library"
    replace(BINDING, published + "\n", "")(repo)
    assert any("has no '- Published interface:' line" in line for line in red(repo))
    replace(BINDING, "- Decision:", f"{published}\n- Decision:")(repo)
    assert red(repo) == []
    replace(BINDING, "- Application types: cli", "- Application types: cli, library")(repo)
    assert f"{BINDING}: the types declare `library`, and '- Published interface:' says none:" in (
        "\n".join(red(repo))
    )
    replace(BINDING, published, "- Published interface: `src/api/**`")(repo)
    assert not any("Published interface" in line for line in red(repo))
    replace(BINDING, "`src/api/**`", "src/api/**")(repo)
    assert (
        f"{BINDING}: the Architecture section's '- Published interface:' line names no glob in"
        " backticks"
    ) in red(repo)


def test_documentation_coverage_bound_outside_a_library_is_unbound_by_the_upgrade(
    repo: Path,
) -> None:
    question = "documentation coverage, ratcheted"
    replace(BINDING, f"| {question} | none: not a library |", f"| {question} | a tool |")(repo)
    line = (
        f"{BINDING}: gate '{question}' is asked of a library only, and the types do not declare"
        " one; write `none: not a library` (ADR 0063: the owner merges this unbinding)"
    )
    assert line in red(repo)
    assert isinstance(finding(repo, line).fix, conformance.Auto)
    replace(BINDING, "- Application types: cli", "- Application types: cli, library")(repo)
    assert line not in red(repo)


DOCUMENTS_ROW = conformance.SELF_TEST_DOCUMENTS
DOCUMENTS_MISSING = (
    "has no '## Documents' section, which gives this project's own Markdown files a kind"
    " (ADR 0064); add the template's section and fill it"
)


def no_kind(path: str) -> str:
    return (
        f"{BINDING}: `{path}` is a Markdown file of no kind; add a row for it to '## Documents',"
        " `never` or `pass`, or move it under a kind's paths (ADR 0064)"
    )


def test_a_binding_without_the_documents_section_is_owed_it_from_the_template(repo: Path) -> None:
    replace(BINDING, "## Documents\n", "## Pages\n")(repo)
    line = f"{BINDING}: {DOCUMENTS_MISSING}"
    assert line in red(repo)
    found = finding(repo, line)
    assert isinstance(found.fix, conformance.Auto)
    assert found.rule == "documents"
    assert "documents" in owed.FILL_FIRST


def test_a_markdown_file_of_no_kind_is_red_with_its_path(repo: Path) -> None:
    git(repo, "init", "-q")
    replace(BINDING, DOCUMENTS_ROW, "| pages | `docs/pages/*.md` | `never` |")(repo)
    assert no_kind(".github/pull_request_template.md") in red(repo)
    write("docs/pages/a.md", "# A\n")(repo)
    assert no_kind("docs/pages/a.md") not in red(repo)
    write("notes/stray.md", "# Stray\n")(repo)
    assert no_kind("notes/stray.md") in red(repo)


def test_a_file_the_methods_own_kinds_match_needs_no_row(repo: Path) -> None:
    git(repo, "init", "-q")
    replace(BINDING, DOCUMENTS_ROW, "| pages | `docs/pages/*.md` | `never` |")(repo)
    write(".scratch/slug/spec.md", "# Spec\n")(repo)
    write("docs/features/slug/spec.md", "# Spec\n")(repo)
    assert not any(".scratch/slug/spec.md" in line for line in red(repo))
    assert not any("docs/features/slug/spec.md" in line for line in red(repo))


def test_a_no_forge_change_description_is_a_markdown_file_of_a_kind(repo: Path) -> None:
    git(repo, "init", "-q")
    replace(BINDING, DOCUMENTS_ROW, "| pages | `docs/pages/*.md` | `never` |")(repo)
    write(".scratch/changes/feature-4-thing.md", "# Thing\n\n## Why\n\nA reason.\n")(repo)
    write(".scratch/stray.md", "# Stray\n")(repo)
    lines = red(repo)
    assert no_kind(".scratch/stray.md") in lines
    assert no_kind(".scratch/changes/feature-4-thing.md") not in lines


def test_a_pass_row_keeping_a_heading_its_files_lack_is_not_a_finding(repo: Path) -> None:
    replace(BINDING, DOCUMENTS_ROW, "| pages | `**/*.md` | `pass` keeping `## Nowhere` |")(repo)
    assert red(repo) == []


@pytest.mark.parametrize(
    ("row", "problem"),
    [
        (
            "| <kind> | <`globs` of its files> | <`never`, or `pass` keeping <`## heading`>> |",
            "the table still has the template's placeholder row",
        ),
        (
            "| self-test pages | `**/*.md` | sometimes |",
            "has a Prune of neither `never` nor `pass`",
        ),
        ("| self-test pages | none | `never` |", "names no paths: write the globs in backticks"),
        (
            f"{DOCUMENTS_ROW}\n| self-test pages | `*.md` | `never` |",
            "the kind 'self-test pages' is listed twice",
        ),
        (
            "| self-test pages | `**/*.md` | `never` keeping `## A` |",
            "is `never` and keeps headings: only a `pass` row keeps any",
        ),
    ],
)
def test_a_documents_row_with_a_wrong_shape_is_red(repo: Path, row: str, problem: str) -> None:
    replace(BINDING, DOCUMENTS_ROW, row)(repo)
    assert any(problem in line for line in red(repo)), red(repo)


def test_a_documents_section_without_its_table_is_red(repo: Path) -> None:
    replace(BINDING, f"| Kind | Paths | Prune |\n| --- | --- | --- |\n{DOCUMENTS_ROW}\n", "")(repo)
    assert any(
        "the section lacks its table with the columns Kind | Paths | Prune" in line
        for line in red(repo)
    )


def test_the_depended_on_event_may_carry_the_forges_signs(repo: Path) -> None:
    replace(
        BINDING,
        "| depended-on | none: the self-test has no such file |",
        "| depended-on | `.github/FUNDING.yml` |",
    )(repo)
    assert red(repo) == []
    replace(BINDING, "`.github/FUNDING.yml`", "words")(repo)
    assert any("'depended-on' names no globs" in line for line in red(repo))


def test_a_repository_without_a_binding_raises_no_documents_finding(repo: Path) -> None:
    remove(BINDING)(repo)
    assert not any(found.rule == "documents" for found in conformance.findings(repo))


APP = "".join(f"line{number}\n" for number in range(1, 41))
TWO_HUNKS = (
    "--- a/src/app.txt\n+++ b/src/app.txt\n"
    "@@ -4,3 +4,3 @@\n line4\n-line5\n+P\n line6\n"
    "@@ -29,3 +29,3 @@\n line29\n-line30\n+Q\n line31\n"
)
OWN_BITES = "".join(
    f'[[bite]]\ngate = "{gate}"\nname = "{gate} word"\n'
    f'write = {{ path = "src/x_{gate}.txt", content = "x" }}\nexpect = "x"\n'
    for gate in ("fmt-check", "lint", "types", "imports", "deadcode", "lint-workflows", "secrets")
) + (
    '[[bite]]\ngate = "limits"\nname = "limits word"\n'
    'write = { path = "src/x_limits.txt", content = "x" }\nexpect = "x"\n'
    '[[bite]]\ngate = "test"\nname = "no tests"\nremove = ["tests/**"]\nexpect = "no test"\n'
    '[[bite]]\ngate = "perf"\nname = "slow turn"\npatch = "quality/bites/slow.patch"\n'
    'expect = "slow"\n'
)


def still_red(root: Path) -> tuple[list[str], list[str]]:
    red, _ = conformance.owed_split(root, conformance.findings(root))
    source = (root / project_bite.REGISTRY).read_text(encoding="utf-8")
    registry, _ = project_bite.read_registry(source)
    bite, _ = project_bite.owing(root, project_bite.plants(root, registry)[0])
    return [str(finding) for finding in red], bite


def test_a_fault_an_upgrade_owes_stays_owed_while_later_changes_move_why_it_fails(
    tmp_path: Path,
) -> None:
    root = adopted.copy(adopted.adopted("0.9.2"), tmp_path / "repo")
    write("src/app.txt", APP.replace("line31", "renamed31"))(root)
    write("quality/bites/slow.patch", TWO_HUNKS)(root)
    write(str(project_bite.REGISTRY), OWN_BITES)(root)
    repos.commit(root, "feat(core): the project's own bites")
    repos.branch(root, "upgrade")
    assert upgrade.main(["upgrade.py", "--root", str(root), "--apply"]) == 0
    repos.commit(root, "chore: upgrade")
    owing = [entry for entry in owed.read(root).entries if "cannot be planted" in entry.problem]
    assert len(owing) == 2
    assert still_red(root) == ([], [])
    repos.branch(root, "feature")
    replace("src/app.txt", "line6\n", "renamed6\n")(root)
    repos.commit(root, "feat(core): an edit above the owed fault")
    assert still_red(root) == ([], [])
    (root / "src/app.txt").rename(root / "src/moved.txt")
    repos.commit(root, "refactor(core): move the file the owed fault patches")
    assert still_red(root) == ([], [])
    broken = 'replace = { path = "src/moved.txt", old = "line2\\n", new = "P\\n" }'
    write(
        str(project_bite.REGISTRY),
        OWN_BITES.replace('write = { path = "src/x_lint.txt", content = "x" }', broken),
    )(root)
    replace("src/moved.txt", "line2\n", "renamed2\n")(root)
    repos.commit(root, "feat(core): a fault, then an edit that breaks it")
    says = (
        f"{project_bite.REGISTRY}: bite 'lint word' of gate 'lint' cannot be planted:"
        " src/moved.txt no longer holds the text the fault replaces; write 'old' as the file has"
        " it now: 'line2\\n'"
    )
    assert still_red(root) == ([says], [says])
