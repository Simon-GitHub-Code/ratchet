"""Who fixes each finding: the script (auto), an agent, or the owner, decided where it is raised."""

import json
import shutil
from pathlib import Path

import pytest

import budget
import conformance
import gatelib
import justfile_text
import project_bite
from tests import repos
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
PIN_LINE = f"- Ratchet plugin: 0.0.0 at {'0' * 40} tree {'0' * 40}"


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
        replace(JUSTFILE, "hooks-install: _plugin\n", "hooks-installs: _plugin\n"),
        "justfile: has no `hooks-install`",
    ),
    (
        replace(JUSTFILE, "ratchet: _plugin\n    just hooks-installed\n", "ratchet: _plugin\n"),
        "justfile: the `ratchet` recipe raises",
    ),
    (
        lambda root: move_rule(root, "Bash(git push --force:*)", source="deny", target="ask"),
        f"{SETTINGS}: 'Bash(git push --force:*)' in ask",
    ),
    (
        replace(SETTINGS, '      "Edit(/.claude/hooks/**)",\n', ""),
        f"{SETTINGS}: 'Edit(/.claude/hooks/**)' is missing",
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
    replace(JUSTFILE, "ratchet: _plugin\n    just hooks-installed\n", "ratchet: _plugin\n")(repo)
    replace(JUSTFILE, "hooks-installed: _plugin\n", "hooks-asked: _plugin\n")(repo)
    replace(JUSTFILE, "ratchet_plugin", "plugin_home")(repo)
    [found] = [f for f in conformance.findings(repo) if "`ratchet` recipe" in f.problem]
    assert isinstance(found.fix, conformance.Agent)
    assert any("plugin_home" not in reason for reason in found.fix.then)


def test_a_ratchet_recipe_written_as_a_script_is_left_to_an_agent(repo: Path) -> None:
    replace(
        JUSTFILE,
        "ratchet: _plugin\n    just hooks-installed\n",
        "ratchet: _plugin\n    #!/usr/bin/env bash\n",
    )(repo)
    assert kind(repo, "justfile: the `ratchet` recipe raises") == "Agent"


def test_a_rule_no_version_generates_is_a_question_for_the_owner(repo: Path) -> None:
    foreign_in(repo, "deny", "Bash(gh label create:*)")
    assert kind(repo, f"{SETTINGS}: 'Bash(gh label create:*)' in deny") == "Owner"
    [found] = [f for f in conformance.findings(repo) if "gh label create" in str(f)]
    assert isinstance(found.fix, conformance.Owner)
    assert "Should Claude keep this rule: Claude may never run `gh label create`?" in found.fix.ask


def test_settings_that_are_not_a_json_object_are_left_to_an_agent(repo: Path) -> None:
    write(SETTINGS, "[]")(repo)
    [first, *_] = [f for f in conformance.findings(repo) if f.path == SETTINGS]
    assert isinstance(first.fix, conformance.Agent)
    assert first.fix.then == (f"{SETTINGS} is not a JSON object, so it is left as it is",)


def test_a_manifest_that_uses_exclude_leaves_the_fixed_settings_as_they_are(repo: Path) -> None:
    # Why: the permission rules no longer mirror the manifest, so nothing in it can stop them.
    replace(MANIFEST, "[[group]]\n", '[[group]]\nexclude = ["x"]\n')(repo)
    assert not [f for f in conformance.findings(repo) if f.path == SETTINGS or "exclude" in str(f)]


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


SCRIPT = '"{{ratchet_plugin}}/scripts/mutation_survivors.py"'
UNFILTERED = (
    f'files="$(python3 {SCRIPT} --tool made-up --changed --base "$B")"; '
    '[ -z "$files" ] && exit 0; made-up run --mutate "$files"'
)


@pytest.mark.parametrize(
    "line",
    [
        UNFILTERED,
        UNFILTERED.replace("&& exit 0", "|| true"),
        f'changed=$(python3 {SCRIPT} --tool made-up --changed); made-up "$changed"',
    ],
)
def test_a_recipe_that_hands_changed_output_to_its_tool_unfiltered_is_found(line: str) -> None:
    assert conformance.unfiltered_changed(line)


@pytest.mark.parametrize(
    "line",
    [
        f'files="$(python3 {SCRIPT} --tool made-up --changed | grep -E "[.]x$")"; made-up "$files"',
        f'files="$(python3 {SCRIPT} --tool made-up --changed)"; files="$(echo "$files" | keep)"',
        f'files="$(python3 {SCRIPT} --tool made-up --changed)"; echo "$files" | keep | made-up',
        f"python3 {SCRIPT} --tool made-up --mutants out.jsonl",
        f"python3 {SCRIPT} --tool made-up --changed",
    ],
)
def test_a_recipe_that_filters_the_list_or_uses_the_mutants_form_is_not_found(line: str) -> None:
    assert not conformance.unfiltered_changed(line)


def test_the_finding_names_both_fixes_and_is_left_to_an_agent(repo: Path) -> None:
    path = repo / JUSTFILE
    path.write_text(
        path.read_text(encoding="utf-8") + f"\n# {UNFILTERED}\nmutation-own:\n    {UNFILTERED}\n",
        encoding="utf-8",
    )
    [found] = [f for f in conformance.findings(repo) if f.rule == "mutation_filter"]
    assert isinstance(found.fix, conformance.Agent)
    assert found.path == JUSTFILE
    assert "unfiltered" in found.problem
    assert "filter the list in the recipe" in found.problem
    assert "`--mutants` form (ADR 0050)" in found.problem


def test_a_second_unfiltered_recipe_is_not_a_second_finding(repo: Path) -> None:
    path = repo / JUSTFILE
    path.write_text(
        path.read_text(encoding="utf-8") + f"\na:\n    {UNFILTERED}\nb:\n    {UNFILTERED}\n",
        encoding="utf-8",
    )
    assert [f.rule for f in conformance.findings(repo)].count("mutation_filter") == 1


CAPTURE = f'files="$(python3 {SCRIPT} --tool made-up --changed)"'


def mutation_filter_findings(repo: Path, recipe: str) -> list[conformance.Finding]:
    path = repo / JUSTFILE
    path.write_text(path.read_text(encoding="utf-8") + recipe, encoding="utf-8")
    return [f for f in conformance.findings(repo) if f.rule == "mutation_filter"]


def script_recipe(*body: str) -> str:
    lines = "".join(f"    {line}\n" for line in body)
    return f"\nmutation-own:\n    #!/usr/bin/env bash\n{lines}"


def test_a_script_recipe_that_filters_on_a_later_line_is_not_found(repo: Path) -> None:
    recipe = script_recipe(
        CAPTURE,
        'files="$(printf \'%s\\n\' "$files" | keep)"',
        'made-up run --mutate "$files"',
    )
    assert mutation_filter_findings(repo, recipe) == []


def test_a_script_recipe_that_filters_through_a_loop_is_not_found(repo: Path) -> None:
    recipe = script_recipe(CAPTURE, 'while read -r f; do keep "$f"; done <<< "$files"')
    assert mutation_filter_findings(repo, recipe) == []


def test_a_script_recipe_that_never_filters_is_found(repo: Path) -> None:
    recipe = script_recipe(CAPTURE, '[ -z "$files" ] && exit 0', 'made-up run --mutate "$files"')
    assert len(mutation_filter_findings(repo, recipe)) == 1


def test_a_filter_in_the_next_recipe_does_not_clear_this_one(repo: Path) -> None:
    other = '\nother:\n    echo "$files" | keep\n'
    recipe = script_recipe(CAPTURE, 'made-up run --mutate "$files"') + other
    assert len(mutation_filter_findings(repo, recipe)) == 1


def test_a_linewise_recipe_does_not_read_its_later_lines(repo: Path) -> None:
    recipe = f'\nmutation-own:\n    {CAPTURE}\n    echo "$files" | keep\n'
    assert len(mutation_filter_findings(repo, recipe)) == 1


def test_a_here_string_fed_with_the_variable_is_a_filter() -> None:
    assert not conformance.unfiltered_changed(f'{CAPTURE}; keep <<< "$files"')


THRESHOLDS = "quality/thresholds.toml"


def ceiling_findings(root: Path) -> list[conformance.Finding]:
    return [f for f in conformance.findings(root) if "bite-all" in f.problem]


def test_a_seconds_table_with_no_bite_all_ceiling_is_an_agent_finding_naming_the_command(
    repo: Path,
) -> None:
    path = repo / THRESHOLDS
    path.write_text(budget.with_ceiling(path.read_text(encoding="utf-8"), "tier2", 600.0))
    [found] = ceiling_findings(repo)
    assert (found.path, found.rule) == (THRESHOLDS, "bite_all_ceiling")
    assert isinstance(found.fix, conformance.Agent)
    assert "`just budget bite-all --set`" in " ".join(found.fix.then)
    path.write_text(budget.with_ceiling(path.read_text(encoding="utf-8"), "bite-all", 3600.0))
    assert ceiling_findings(repo) == []


def seconds_problem(repo: Path, tier2: float | None, took: float | None) -> str:
    path = repo / THRESHOLDS
    text = path.read_text(encoding="utf-8")
    path.write_text(
        budget.with_ceiling(text, "tier2", tier2)
        if tier2 is not None
        else text + "\n[ceilings.seconds]\nlint = 5\n"
    )
    if took is not None:
        timing = repo / ".ratchet/timing.jsonl"
        timing.parent.mkdir(exist_ok=True)
        timing.write_text(json.dumps({"key": "bite-all", "seconds": took}) + "\n")
    [found] = ceiling_findings(repo)
    return found.problem


def test_the_missing_bite_all_ceiling_says_the_full_proof_goes_red_over_tier_2s(
    repo: Path,
) -> None:
    problem = seconds_problem(repo, 900.0, None)
    assert "is held to tier 2's ceiling of 900s and goes red when it runs longer" in problem
    assert "last full proof" not in problem


def test_the_missing_bite_all_ceiling_names_the_last_measured_full_proof(repo: Path) -> None:
    problem = seconds_problem(repo, 900.0, 1052.4)
    assert problem.endswith("; the last full proof took 1052.4s")


def test_the_last_full_proof_skips_unreadable_lines_and_other_keys(repo: Path) -> None:
    timing = repo / ".ratchet/timing.jsonl"
    timing.parent.mkdir(exist_ok=True)
    lines = [
        json.dumps({"key": "bite-all", "seconds": 1000}),
        "not json",
        json.dumps({"key": "lint", "seconds": 5}),
        json.dumps(["bite-all"]),
        json.dumps({"key": "bite-all", "seconds": "slow"}),
    ]
    timing.write_text("\n".join(lines) + "\n")
    assert conformance.last_full_proof(repo) == 1000


def test_the_missing_bite_all_ceiling_with_no_tier_1_ceiling_says_there_is_none(
    repo: Path,
) -> None:
    assert "has no ceiling at all" in seconds_problem(repo, None, None)


READS = "\n## What each gate reads\n\n"


def read_problems(root: Path, bullets: str) -> list[str]:
    binding = root / BINDING
    head, marker, rest = binding.read_text(encoding="utf-8").partition(READS.rstrip("\n"))
    body, divider, tail = rest.partition("\n## ")
    text = head + marker + body.rstrip("\n") + "\n\n" + bullets + "\n" + divider + tail
    binding.write_text(text if marker else head + READS + bullets, encoding="utf-8")
    repos.git(root, "init", "-q")
    ours = ("'## What", "gate ")
    return [f.problem for f in conformance.findings(root) if f.problem.startswith(ours)]


def test_a_bullet_of_what_each_gate_reads_is_red_for_no_gate_no_path_or_no_entry(
    repo: Path,
) -> None:
    gate = project_bite.gate_rows((repo / BINDING).read_text(encoding="utf-8"))[0].gate
    bullets = f"- {gate}: AGENTS.md, missing/**\n- nosuch: AGENTS.md\n- {gate}2:\n"
    assert read_problems(repo, bullets) == [
        f"gate '{gate}' reads 'missing/**', matching no file",
        "'## What each gate reads' has a bullet for 'nosuch', no entry point",
        f"'## What each gate reads' has a bullet for '{gate}2', no entry point",
        f"'## What each gate reads' has an empty bullet for '{gate}2'",
    ]


def test_a_bullet_that_names_a_gate_and_a_file_that_exists_is_green(repo: Path) -> None:
    gate = project_bite.gate_rows((repo / BINDING).read_text(encoding="utf-8"))[0].gate
    assert read_problems(repo, f"- {gate}: AGENTS.md, docs\n") == []


def test_a_line_that_is_no_bullet_after_the_first_bullet_is_red(repo: Path) -> None:
    gate = project_bite.gate_rows((repo / BINDING).read_text(encoding="utf-8"))[0].gate
    assert read_problems(repo, f"- {gate}: AGENTS.md\nstray words\n") == [
        "'## What each gate reads' has a line that is no bullet: 'stray words'"
    ]


def test_a_bullet_that_wraps_is_read_on_its_second_line_too(repo: Path) -> None:
    gate = project_bite.gate_rows((repo / BINDING).read_text(encoding="utf-8"))[0].gate
    assert read_problems(repo, f"- {gate}: AGENTS.md,\n  missing/**\n") == [
        f"gate '{gate}' reads 'missing/**', matching no file"
    ]


OLD_PLUGIN_LINE = (
    'ratchet_plugin := env_var_or_default("RATCHET_PLUGIN", '
    'home_directory() / ".claude/plugins/marketplaces/ratchet")'
)


OLD_COMMIT_MSG = (
    "commit-msg file:\n"
    '    @if [ -n "${RATCHET_HOOK_PROBE:-}" ]; then echo commit-msg > "$RATCHET_HOOK_PROBE"; '
    'else python3 "{{ratchet_plugin}}/scripts/commits.py" --message-file "{{file}}"; fi'
)


def old_plugin_justfile(repo: Path) -> str:
    template = (repo / JUSTFILE).read_text(encoding="utf-8")
    line = conformance.PLUGIN_LINE.search(template)
    assert line
    start = template.index("# The one guard")
    end = template.index("# Every tier starts here")
    old = (template[:start] + template[end:]).replace(line.group(0), OLD_PLUGIN_LINE)
    old = old.replace(f": {conformance.PLUGIN_GUARD}\n", ":\n").replace(
        f" {conformance.PLUGIN_GUARD}\n", "\n"
    )
    held = "\n".join(justfile_text.definition(old, "commit-msg"))
    return old.replace(held, OLD_COMMIT_MSG)


def fallback_findings(repo: Path) -> list[conformance.Finding]:
    return [f for f in conformance.findings(repo) if "falls back to the marketplace" in f.problem]


def test_a_justfile_that_falls_back_to_the_marketplace_clone_is_rewritten_to_the_templates(
    repo: Path,
) -> None:
    (repo / JUSTFILE).write_text(old_plugin_justfile(repo), encoding="utf-8")
    [found] = fallback_findings(repo)
    assert isinstance(found.fix, conformance.Auto)
    made = found.fix.want((repo / JUSTFILE).read_text(encoding="utf-8"))
    assert made == (conformance.ADOPT_TEMPLATES / "justfile").read_text(encoding="utf-8")


def test_a_justfile_with_the_templates_plugin_line_is_not_held_to_the_marketplace(
    repo: Path,
) -> None:
    assert fallback_findings(repo) == []


def test_a_fallback_check_has_nothing_to_rewrite_to_when_the_template_has_no_line(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (repo / JUSTFILE).write_text(old_plugin_justfile(repo), encoding="utf-8")
    monkeypatch.setattr(conformance, "PLUGIN_GUARD", "no_such_recipe")
    assert fallback_findings(repo) == []


def block_findings(repo: Path) -> list[conformance.Finding]:
    return [f for f in conformance.findings(repo) if "is not the template's" in f.problem]


def test_a_plugin_block_that_is_not_the_templates_is_rewritten_to_it(repo: Path) -> None:
    path = repo / JUSTFILE
    text = path.read_text(encoding="utf-8")
    line = 'ratchet_plugin := "$RATCHET_PLUGIN_DIR"'
    assert line in text
    path.write_text(
        text.replace(line, 'ratchet_plugin := env_var_or_default("RATCHET_PLUGIN", "")'),
        encoding="utf-8",
    )
    [found] = block_findings(repo)
    assert "plugin block above `ratchet_plugin`" in found.problem
    assert isinstance(found.fix, conformance.Auto)
    assert found.fix.want(path.read_text(encoding="utf-8")) == text


OLD_PYC_EXPORTS = (
    'export PYTHONPYCACHEPREFIX := if plugin_pyc_ok == "" { "/nonexistent/x" }'
    ' else { plugin_pyc_ok + "/" + plugin_tree }\n'
    'export PYTHONDONTWRITEBYTECODE := if plugin_pyc_ok == "" { "1" } else { "" }\n'
)


def test_a_block_that_exports_the_compiled_file_variables_is_rewritten_with_nothing_left(
    repo: Path,
) -> None:
    path = repo / JUSTFILE
    text = path.read_text(encoding="utf-8")
    [variable] = [
        line for line in text.splitlines(keepends=True) if line.startswith("plugin_py :=")
    ]
    path.write_text(text.replace(variable, OLD_PYC_EXPORTS), encoding="utf-8")
    [found] = block_findings(repo)
    assert isinstance(found.fix, conformance.Auto)
    made = found.fix.want(path.read_text(encoding="utf-8"))
    assert made == text
    assert "PYTHONPYCACHEPREFIX" not in made


def test_a_bare_plugin_script_call_is_named_by_its_line_and_recipe(repo: Path) -> None:
    path = repo / JUSTFILE
    text = path.read_text(encoding="utf-8")
    good = '{{plugin_py}} "{{ratchet_plugin}}/scripts/markers.py"'
    assert good in text
    path.write_text(
        text.replace(good, 'python3 "{{ratchet_plugin}}/scripts/markers.py"'), encoding="utf-8"
    )
    number = next(
        i
        for i, line in enumerate(path.read_text(encoding="utf-8").split("\n"), start=1)
        if 'python3 "{{ratchet_plugin}}/scripts/markers.py"' in line
    )
    [found] = [f for f in conformance.findings(repo) if "bare `python3`" in f.problem]
    assert f"justfile:{number}: recipe `markers` runs a plugin script" in found.problem
    assert isinstance(found.fix, conformance.Auto)


def test_the_templates_plugin_block_is_not_a_finding(repo: Path) -> None:
    assert block_findings(repo) == []


PRE_COMMIT = "pre-commit"


def pre_commit_findings(repo: Path) -> list[conformance.Finding]:
    return [f for f in conformance.findings(repo) if "`pre-commit` neither" in f.problem]


@pytest.mark.parametrize(
    "own",
    [
        "pre-commit:\n    @just tier0-tree\n",
        "[no-exit-message]\npre-commit:\n    @echo fast\n",
    ],
)
def test_a_pre_commit_recipe_that_skips_the_pin_or_shows_just_errors_is_the_templates(
    repo: Path, own: str
) -> None:
    path = repo / JUSTFILE
    text = path.read_text(encoding="utf-8")
    held = "\n".join(justfile_text.definition(text, PRE_COMMIT))
    assert pre_commit_findings(repo) == []
    path.write_text(text.replace(held, own.rstrip("\n")), encoding="utf-8")
    [found] = pre_commit_findings(repo)
    assert isinstance(found.fix, conformance.Auto)
    assert found.fix.want(path.read_text(encoding="utf-8")) == text


def test_a_justfile_with_no_pre_commit_recipe_has_nothing_to_offer(repo: Path) -> None:
    path = repo / JUSTFILE
    text = path.read_text(encoding="utf-8")
    held = "\n".join(justfile_text.definition(text, PRE_COMMIT))
    path.write_text(text.replace(held, ""), encoding="utf-8")
    assert pre_commit_findings(repo) == []


def value_findings(repo: Path) -> list[conformance.Finding]:
    return [
        f for f in conformance.findings(repo) if "read `ratchet_plugin` as a folder" in f.problem
    ]


@pytest.mark.parametrize(
    "owner_text",
    [
        '\nmine:\n    #!/usr/bin/env python3\n    print("{{ratchet_plugin}}")\n',
        '\nbase := ratchet_plugin + "/scripts"\n',
        "\nthere := path_exists(ratchet_plugin)\n",
        "\nmine:\n    echo {{ratchet_plugin}}/x\n",
        "\nmine:\n    echo '{{ratchet_plugin}}/x'\n",
    ],
)
def test_an_owner_use_of_the_plugin_variable_as_a_folder_is_one_red_line(
    repo: Path, owner_text: str
) -> None:
    assert value_findings(repo) == []
    path = repo / JUSTFILE
    path.write_text(path.read_text(encoding="utf-8") + owner_text, encoding="utf-8")
    [found] = value_findings(repo)
    assert isinstance(found.fix, conformance.Agent)
    assert '"$RATCHET_PLUGIN_DIR"' in found.problem


def test_a_pin_with_no_tree_id_is_a_finding_for_an_agent_when_the_plugin_is_another_commit(
    repo: Path,
) -> None:
    pin = f"- Ratchet plugin: 0.0.0 at {'0' * 40} tree {'0' * 40}"
    replace(BINDING, pin, f"- Ratchet plugin: 0.0.0 at {'0' * 40}")(repo)
    assert kind(repo, f"{BINDING}: the pin names no tree id") == "Agent"


def test_a_pin_with_no_tree_id_at_the_plugin_in_use_is_fixed_by_the_upgrade(repo: Path) -> None:
    commit = conformance.plugin_pin.commit_of(conformance.plugin_pin.PLUGIN)
    pin = f"- Ratchet plugin: 0.0.0 at {'0' * 40} tree {'0' * 40}"
    replace(BINDING, pin, f"- Ratchet plugin: 0.0.0 at {commit}")(repo)
    assert kind(repo, f"{BINDING}: the pin names no tree id") == "Auto"


def test_a_quote_inside_a_command_substitution_is_its_own_level() -> None:
    assert conformance.inside_quotes('x="$(echo "a") ')
    assert not conformance.inside_quotes('x="$(echo "a")" ')
    assert not conformance.inside_quotes('x=$(echo "a"; ')
