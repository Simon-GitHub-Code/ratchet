import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
import tomllib
from pathlib import Path
from typing import Any

import pytest

import change_size
import conformance
import parallel
import pins
import plugin_pin
import suites as suites_module
from tests import repos

TEMPLATES = Path(__file__).resolve().parents[2] / "skills/adopt/templates"
PLUGIN_ROOT = TEMPLATES.parents[2]
ADOPT = (TEMPLATES.parent / "SKILL.md").read_text(encoding="utf-8")
JUSTFILE = (TEMPLATES / "justfile").read_text(encoding="utf-8")
BINDING = (TEMPLATES / "docs/process/binding.md").read_text(encoding="utf-8")
MERGE = (TEMPLATES / ".github/workflows/merge.yml").read_text(encoding="utf-8")
WEEKLY = (TEMPLATES / ".github/workflows/weekly.yml").read_text(encoding="utf-8")
WORKFLOWS = {
    name: (TEMPLATES / ".github/workflows" / name).read_text(encoding="utf-8")
    for name in ("ci.yml", "deep.yml")
}
SHARDS_STEP = re.compile(r"      - id: shards\n        run: \|\n((?:          .*\n)+)")


def gate_list(name: str) -> list[str]:
    just = shutil.which("just")
    if just is None:
        pytest.skip("just is not installed")
    done = subprocess.run(  # noqa: S603 # -- a fixed argv; the justfile is the template's own
        [just, "--justfile", str(TEMPLATES / "justfile"), "--evaluate", name],
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout.split()


def suites_of(binding: str) -> tuple[list[suites_module.Suite] | None, list[str]]:
    return suites_module.parse(binding)


def recipe(name: str) -> str:
    match = re.search(
        rf"^{name}(?: [*+]\w+)?:(?: _plugin)?\n((?:    .*\n)+)", JUSTFILE, re.MULTILINE
    )
    assert match, name
    return match.group(1)


def shards(
    binding: str, tmp_path: Path, workflow: str, *, justfile: str = JUSTFILE
) -> tuple[int, dict[str, str], str]:
    step = SHARDS_STEP.search(WORKFLOWS[workflow])
    bash = shutil.which("bash")
    assert step, workflow
    assert bash
    (tmp_path / "docs/process").mkdir(parents=True, exist_ok=True)
    (tmp_path / "docs/process/binding.md").write_text(binding, encoding="utf-8")
    (tmp_path / "justfile").write_text(justfile, encoding="utf-8")
    output = tmp_path / "output"
    output.write_text("", encoding="utf-8")
    done = subprocess.run(  # noqa: S603 # -- a fixed argv; the script is the template's own
        [bash, "-e", "-c", textwrap.dedent(step.group(1))],
        cwd=tmp_path,
        env={**os.environ, "GITHUB_OUTPUT": str(output)},
        capture_output=True,
        text=True,
        check=False,
    )
    outputs = dict(line.split("=", 1) for line in output.read_text(encoding="utf-8").splitlines())
    return done.returncode, outputs, done.stdout


def needs_just() -> None:
    if shutil.which("just") is None:
        pytest.skip("just is not installed")


@pytest.mark.parametrize(("workflow", "tier"), [("ci.yml", "tier1"), ("deep.yml", "tier2")])
def test_ci_runs_every_gate_of_its_tier_once(workflow: str, tier: str, tmp_path: Path) -> None:
    needs_just()
    code, outputs, _ = shards(BINDING, tmp_path, workflow)
    assert code == 0
    plain, long = json.loads(outputs[tier]), json.loads(outputs[f"{tier}_long"])
    assert long == gate_list(f"{tier}_sharded")
    assert sorted([*plain, *long]) == sorted(gate_list(f"{tier}_gates"))
    assert f"gate: ${{{{ fromJSON(needs.shape.outputs.{tier}) }}}}" in WORKFLOWS[workflow]
    assert f"gate: ${{{{ fromJSON(needs.shape.outputs.{tier}_long) }}}}" in WORKFLOWS[workflow]


def test_no_workflow_keeps_a_gate_list_of_its_own() -> None:
    for name, text in WORKFLOWS.items():
        assert not re.search(r"^ +gate: \[", text, re.MULTILINE), name


@pytest.mark.parametrize(("workflow", "tier"), [("ci.yml", "tier1"), ("deep.yml", "tier2")])
def test_a_tier_with_no_long_gate_skips_its_long_job_rather_than_an_empty_matrix(
    workflow: str, tier: str, tmp_path: Path
) -> None:
    needs_just()
    emptied = f'{tier}_sharded := ""'
    none = re.sub(rf'^{tier}_sharded := ".*"$', emptied, JUSTFILE, flags=re.MULTILINE)
    assert none != JUSTFILE
    code, outputs, _ = shards(BINDING, tmp_path, workflow, justfile=none)
    assert (code, outputs[f"{tier}_long"]) == (0, "[]")
    assert f"    if: needs.shape.outputs.{tier}_long != '[]'\n" in WORKFLOWS[workflow]


@pytest.mark.parametrize("workflow", sorted(WORKFLOWS))
def test_the_long_gates_run_one_job_per_shard_the_binding_counts(
    workflow: str, tmp_path: Path
) -> None:
    text = WORKFLOWS[workflow]
    assert "shard: ${{ fromJSON(needs.shape.outputs.shards) }}" in text
    assert "SHARD: ${{ matrix.shard }}" in text
    assert "SHARDS: ${{ needs.shape.outputs.count }}" in text
    needs_just()
    code, outputs, _ = shards(BINDING, tmp_path, workflow)
    assert (code, outputs["count"], outputs["list"]) == (0, "1", "[1]")
    four = BINDING.replace("- Shards: 1", "- Shards: 4")
    code, outputs, _ = shards(four, tmp_path, workflow)
    assert (code, outputs["count"], outputs["list"]) == (0, "4", "[1,2,3,4]")
    for unreadable in ("- Shards: 0", "- Shards: a few", "- Shards: 2 for now"):
        code, _, said = shards(BINDING.replace("- Shards: 1", unreadable), tmp_path, workflow)
        assert code == 1, unreadable
        assert said == "docs/process/binding.md: no '- Shards: N' line\n"


def dry_run(recipe_name: str, justfile: Path = TEMPLATES / "justfile") -> str:
    just = shutil.which("just")
    assert just
    done = subprocess.run(  # noqa: S603 # -- a fixed argv; the justfile is the template's own
        [just, "--justfile", str(justfile), "--dry-run", recipe_name],
        env={**os.environ, "RATCHET_PLUGIN": "/plugin"},
        capture_output=True,
        text=True,
        check=True,
    )
    return " ".join(done.stderr.split())


PIN_CALL = re.compile(r'python3 -E -s .*"\$RATCHET_PLUGIN_DIR/scripts/plugin_pin.py"')
RUNNER = '"$RATCHET_PLUGIN_DIR/scripts/parallel.py" --runner'


@pytest.mark.parametrize(
    ("name", "lists", "flag"),
    [
        ("tier0", ["tier0_gates"], ""),
        ("tier0-tree", ["tier0_tree_gates"], ""),
        ("tier1", ["tier1_gates"], ""),
        ("tier2", ["tier2_gates"], ""),
        ("check", ["tier0_gates", "tier1_gates"], ""),
        ("check-fast", ["tier0_gates", "tier1_gates"], "--fail-fast"),
        ("check-deep", ["tier0_gates", "tier1_gates", "tier2_gates"], ""),
    ],
)
def test_every_tier_runs_its_gates_at_once_through_the_runner(
    name: str, lists: list[str], flag: str
) -> None:
    needs_just()
    pin, runner, gates = dry_run(name).partition(RUNNER)
    said = runner + gates
    assert PIN_CALL.search(pin), "the pin is judged before any gate"
    assert runner
    assert ("--fail-fast" in said) is bool(flag)
    assert "--report .ratchet/timing.jsonl" in said
    assert said.endswith(" -- " + " ".join(gate for item in lists for gate in gate_list(item)))


def test_tier_zero_is_the_tree_gates_and_the_history_gate() -> None:
    assert gate_list("tier0_gates") == [*gate_list("tier0_tree_gates"), "commits"]
    assert "just tier0-tree" in recipe("pre-commit")


def test_every_listed_gate_is_a_recipe_and_a_sharded_gate_is_listed() -> None:
    defined = set(re.findall(r"^([a-z][a-z0-9-]*)(?: [^:\n]*)?:", JUSTFILE, re.MULTILINE))
    listed = [g for tier in ("tier0_gates", "tier1_gates", "tier2_gates") for g in gate_list(tier)]
    assert len(set(listed)) == len(listed), "one run holds a gate once"
    assert set(listed) <= defined
    assert set(gate_list("tier1_sharded")) <= set(gate_list("tier1_gates"))
    assert set(gate_list("tier2_sharded")) <= set(gate_list("tier2_gates"))


def test_a_serial_group_is_declared_per_tier_and_empty_until_the_stack_needs_one(
    tmp_path: Path,
) -> None:
    needs_just()
    assert 'serial0 := ""' in JUSTFILE
    bound = tmp_path / "justfile"
    bound.write_text(
        JUSTFILE.replace(
            'serial1 := "--serial coverage mutation-changed"',
            'serial1 := "--serial coverage diff-coverage"',
        ),
        encoding="utf-8",
    )
    assert " --serial coverage diff-coverage -- " in dry_run("check", bound)
    assert " --serial coverage diff-coverage -- " in dry_run("tier1", bound)
    assert "--serial" not in dry_run("tier0", bound)


def test_the_gates_seen_to_starve_each_other_never_run_side_by_side_in_any_run_of_tiers() -> None:
    needs_just()
    seen = {"coverage", "mutation-changed", "mutation", "test-stability"}
    tiers = [gate_list(f"tier{n}_gates") for n in range(3)]
    groups = [
        [
            group.split()
            for group in " ".join(gate_list(f"serial{n}")).split("--serial")
            if group.split()
        ]
        for n in range(3)
    ]
    alone = set(gate_list("alone")[1:])
    for joined in ((1,), (2,), (0, 1), (0, 1, 2)):
        gates = [gate for n in joined for gate in tiers[n]]
        serial = [group for n in joined for group in groups[n]]
        units = parallel.plan(gates, serial, frozenset(alone & set(gates)))
        beside = [unit for unit in units if seen & set(unit)]
        assert len(beside) <= 1, (joined, beside)
        assert {"mutation", "test-stability"} & set(gates) <= alone


def test_a_recipe_learns_its_shard_from_the_environment_and_is_whole_without_one() -> None:
    assert 'shard := env_var_or_default("SHARD", "1")' in JUSTFILE
    assert 'shards := env_var_or_default("SHARDS", "1")' in JUSTFILE
    for name in ("suite-report", "mutation-changed", "mutation", "e2e"):
        assert "shard {{shard}} of {{shards}}" in recipe(name), name
    for gate in ("test", "coverage", "diff-coverage", "test-counts"):
        assert f"report.py --reader {gate}" in recipe(gate), f"{gate} reads the shared suite report"
    body = recipe("coverage")
    assert 'report="$(' in body, "coverage keeps report.py's exit status"
    assert "--reader coverage --path " in body, "coverage asks for its path"
    assert (
        '&& {{plugin_py}} "{{ratchet_plugin}}/scripts/coverage_groups.py" --report "$report"'
        in body
    )
    assert "--report coverage.xml" not in recipe("coverage")
    assert "test_runner" in recipe("suite-report"), "the producer runs the runner adoption set up"


def test_a_gate_that_requests_routes_boots_with_the_flags_due_for_removal_on() -> None:
    assert re.search(r'^flags_on := "bind: NAME=VALUE, ', JUSTFILE, re.MULTILINE)
    assert "probed := booted + ' --env \"' + flags_on + '\"'" in JUSTFILE
    assert "flags.py --temporary" in JUSTFILE
    for name in ("api-deep", "dast", "pentest", "load"):
        assert "booted through probed" in recipe(name), name
    for name in ("smoke", "telemetry-e2e"):
        assert "probed" not in recipe(name), f"{name} judges the defaults, which are what ships"


def test_the_three_service_gates_arrive_bound_to_the_shipped_scripts() -> None:
    assert (
        "/scripts/with_service.py\" --boot ' + quote(boot) + ' --ready ' + quote(ready)" in JUSTFILE
    )
    assert recipe("smoke").strip() == "{{booted}}"
    assert recipe("load-smoke").startswith("    {{probed}} -- {{plugin_py}} ")
    assert 'scripts/load_smoke.py" --url "bind: METHOD URL' in recipe("load-smoke")
    assert "{{ready}}" not in recipe("load-smoke"), "readiness alone overlaps nothing a user asks"
    assert 'scripts/telemetry_sink.py" --service "{{service}}" -- {{booted}} --' in recipe(
        "telemetry-e2e"
    )
    for name in ("boot", "ready", "service", "test_runner"):
        assert re.search(rf'^{name} := "bind: ', JUSTFILE, re.MULTILINE), name
    for question, script in (
        ("starts and answers", "with_service.py"),
        ("load smoke", "load_smoke.py"),
        ("telemetry arrives", "telemetry_sink.py"),
    ):
        assert f"\n| {question} | `ratchet` plugin `scripts/{script}`" in BINDING


def all_recipes() -> set[str]:
    just = shutil.which("just")
    assert just
    done = subprocess.run(  # noqa: S603 # -- a fixed argv, no shell
        [just, "--justfile", str(TEMPLATES / "justfile"), "--summary"],
        capture_output=True,
        text=True,
        check=True,
    )
    return set(done.stdout.split())


def run_template(tmp_path: Path, settings: dict[str, str], *words: str) -> str:
    just = shutil.which("just")
    assert just
    sets = [word for name, value in settings.items() for word in ("--set", name, value)]
    done = subprocess.run(  # noqa: S603 # -- a fixed argv, no shell
        [just, "--justfile", str(TEMPLATES / "justfile"), *sets, *words],
        cwd=tmp_path,
        env={**os.environ, "RATCHET_PLUGIN": "/plugin"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    return done.stdout


FIRST_SLICE_FILES = 20
FIRST_SLICE_LINES = 683


def test_the_change_size_ceiling_is_there_before_the_first_feature_and_its_first_slice_fits(
    tmp_path: Path,
) -> None:
    path = TEMPLATES / "quality/thresholds.toml"
    ceiling = tomllib.loads(path.read_text(encoding="utf-8"))["ceilings"]["change"]
    assert ceiling["product_files"] >= FIRST_SLICE_FILES, "the first slice's files fit"
    assert ceiling["product_lines"] >= FIRST_SLICE_LINES, "the first slice's lines fit"
    (tmp_path / "quality").mkdir()
    shutil.copy(path, tmp_path / "quality/thresholds.toml")
    assert change_size.ceilings(tmp_path) == change_size.Ceilings(25, 1000, 3000), (
        "the gate reads the template's ceilings"
    )


def test_the_binding_template_has_a_test_paths_row_the_gate_finds_unfilled() -> None:
    text = (TEMPLATES / "docs/process/binding.md").read_text(encoding="utf-8")
    assert "## Test paths\n" in text
    assert "- Tests: <" in text, "adoption fills the placeholder"


def test_start_runs_the_deliverable_on_its_port_and_says_where_to_open_it(tmp_path: Path) -> None:
    needs_just()
    server = {"boot": "echo started on {port}", "ready": "http://127.0.0.1:{port}/", "port": "3000"}
    out = run_template(tmp_path, server, "start")
    assert "open http://127.0.0.1:3000/ once it is up" in out
    assert "started on 3000" in out
    assert "{port}" not in out


def test_start_hands_a_tool_with_no_address_the_words_after_it(tmp_path: Path) -> None:
    needs_just()
    out = run_template(tmp_path, {"boot": "echo cli", "ready": "none"}, "start", "--version")
    assert "cli --version" in out
    assert "open " not in out, "no address to open"


def test_start_gives_the_port_to_the_whole_boot_line_as_the_gates_do(tmp_path: Path) -> None:
    needs_just()
    boot = {"boot": "true && sh -c 'echo port is $PORT'", "ready": "none", "port": "3000"}
    assert "port is 3000" in run_template(tmp_path, boot, "start")


SERVE = f"{sys.executable} -m http.server --bind 127.0.0.1"


@pytest.mark.parametrize(
    ("boot", "port"),
    [
        (f"exec {SERVE} {{port}}", None),
        (f"exec {SERVE} $PORT", None),
        (f"exec {SERVE} $PORT", "1"),
        (f'exec "{sys.executable}" -m http.server --bind 127.0.0.1 "$PORT"', None),
    ],
    ids=["the placeholder", "PORT", "PORT set to another port", "a double quote"],
)
def test_smoke_boots_a_deliverable_on_the_port_the_gate_picked(
    tmp_path: Path, boot: str, port: str | None
) -> None:
    just = shutil.which("just")
    if just is None:
        pytest.skip("just is not installed")
    copy = repos.installed_copy()
    pin = f"- Ratchet plugin: {plugin_pin.version_of(copy)} at {'1' * 40}"
    binding = tmp_path / "docs/process/binding.md"
    binding.parent.mkdir(parents=True)
    binding.write_text(f"{pin} tree {plugin_pin.copy_tree_id(copy)}\n", encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k != "PORT"} | {"RATCHET_PLUGIN": str(copy)}
    sets = ["--set", "boot", boot, "--set", "ready", "http://127.0.0.1:{port}/"]
    done = subprocess.run(  # noqa: S603 # -- a fixed argv; the justfile is the template's own
        [just, "--justfile", str(TEMPLATES / "justfile"), "-d", str(tmp_path), *sets, "smoke"],
        cwd=tmp_path,
        env=env | ({"PORT": port} if port else {}),
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert "with_service: green" in done.stdout + done.stderr


@pytest.mark.parametrize(
    ("files", "ran"),
    [(["a.test.ts", "b.test.ts"], "ran a.test.ts b.test.ts"), ([], "ran")],
    ids=["the files named", "every test with none"],
)
def test_test_files_runs_the_runner_over_the_files_named(
    tmp_path: Path, files: list[str], ran: str
) -> None:
    needs_just()
    out = run_template(tmp_path, {"test_runner": "echo ran"}, "test-files", *files)
    assert out.strip() == ran


def test_start_and_test_files_are_entry_points_and_in_no_tier() -> None:
    needs_just()
    lists = (
        "tier0_gates",
        "tier0_tree_gates",
        "tier1_gates",
        "tier2_gates",
        "tier1_sharded",
        "tier2_sharded",
        "weekly_gates",
    )
    for name in lists:
        assert not {"start", "test-files"} & set(gate_list(name)), name
    assert {"start", "test-files"} <= all_recipes()


def entry_point_blocks() -> tuple[list[str], list[list[str]]]:
    section = BINDING.split("\n## Entry points\n", 1)[1].split("\n## ", 1)[0]
    blocks = section.strip().split("\n\n")
    tables = [block.splitlines()[2:] for block in blocks if block.startswith("|")]
    return [block for block in blocks if not block.startswith("|")], tables


def test_every_entry_point_the_binding_names_is_a_recipe_of_the_justfile() -> None:
    needs_just()
    rows = [row for table in entry_point_blocks()[1] for row in table]
    row = re.compile(r"^\| [^|]+ \| `just ([a-z][a-z0-9-]*)[^`]*` \|$", re.MULTILINE)
    named = row.findall("\n".join(rows))
    assert len(named) == len(rows), "every row of the tables is read"
    assert set(named) <= all_recipes(), set(named) - all_recipes()


def test_the_binding_says_which_entry_points_the_conformance_gate_requires() -> None:
    paragraphs, tables = entry_point_blocks()
    names = [[row.split("|")[1].strip() for row in table] for table in tables]
    assert set(names[0]) == set(conformance.ENTRY_POINTS), "the first table is what it requires"
    assert len(names[0]) == len(conformance.ENTRY_POINTS), "each once"
    assert len(paragraphs) == len(tables) == 2, "a paragraph above each table"
    (intro, between), (gated_names, convenience_names) = paragraphs, names
    assert not set(convenience_names) & set(conformance.ENTRY_POINTS), (
        "the second, nothing it requires"
    )
    assert set(gated_names + convenience_names) <= set(conformance.template_rows())
    assert "every entry point of the first table below" in intro
    assert "the conformance gate requires each" in intro
    assert "which no gate requires or runs" in between
    for name in convenience_names:
        assert f"`{name}" in between, name
        assert f"`{name}" not in intro, name


def test_the_binding_names_the_tests_every_role_writes_and_how_they_run() -> None:
    tests = BINDING.split("\n## Tests\n", 1)[1].split("\n## ", 1)[0]
    assert "just test-files" in tests


def test_the_ci_templates_are_green_on_the_pins_gate() -> None:
    for name in ("ci.yml", "deep.yml", "weekly.yml"):
        path = f".github/workflows/{name}"
        text = (TEMPLATES / path).read_text(encoding="utf-8")
        assert pins.check_workflow(path, text) == [], name
        assert pins.check_images(path, text) == [], name
        assert 'repository: Simon-GitHub-Code/ratchet, ref: "<ratchet-sha>"' in text
        assert "RATCHET_PLUGIN: ${{ github.workspace }}/ratchet" in text


def test_ci_reports_a_ceiling_on_a_pull_request_and_holds_each_job_to_its_tier() -> None:
    ci, deep = WORKFLOWS["ci.yml"], WORKFLOWS["deep.yml"]
    assert "  OVER: ${{ github.event_name == 'pull_request' && 'report' || 'block' }}\n" in ci
    assert ci.count('budget.py" tier0 --over "$OVER" --report') == 1
    assert ci.count('budget.py" "${{ matrix.gate }}" --tier tier1 --over "$OVER" --report') == 2
    assert ci.count("budget.py") == 3
    assert deep.count('budget.py" "${{ matrix.gate }}" --tier tier2 --report') == 2
    assert "--over" not in deep, "the nightly and the default branch block"
    assert deep.count("budget.py") == 2


def test_the_plugins_own_ci_reports_a_ceiling_on_a_pull_request_as_the_template_does() -> None:
    own = (PLUGIN_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    over = "  OVER: ${{ github.event_name == 'pull_request' && 'report' || 'block' }}\n"
    assert over in WORKFLOWS["ci.yml"]
    assert over in own
    assert own.count('budget.py "${{ matrix.gate }}" --over "$OVER" --report') == 2
    assert own.count("budget.py") == 2


def test_the_plugins_own_checkers_are_held_at_every_line_with_only_tests_omitted() -> None:
    config = tomllib.loads((PLUGIN_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    coverage = config["tool"]["coverage"]
    assert coverage["report"]["fail_under"] == 100
    assert coverage["run"]["branch"] is True
    assert coverage["run"]["source"] == ["scripts", "hooks", "evals/first-hour/first_hour"]
    assert coverage["run"]["omit"] == [
        "*/hooks/tests/*",
        "*/evals/first-hour/first_hour/drive.py",
    ]
    assert coverage["run"]["patch"] == ["subprocess"]


def test_the_weekly_workflow_is_scheduled_and_may_only_read_and_write_issues() -> None:
    assert "on:\n  schedule:\n" in WEEKLY
    assert "\npermissions:\n  contents: read\n" in WEEKLY
    assert "    permissions:\n      contents: read\n      issues: write\n" in WEEKLY
    assert re.findall(r"^ +([a-z-]+): write$", WEEKLY, re.MULTILINE) == ["issues"]
    assert "        shell: bash\n        run: just weekly 2>&1 | tee weekly.txt\n" in WEEKLY


def test_the_weekly_entry_point_runs_its_gates_one_after_another() -> None:
    needs_just()
    gates = gate_list("weekly_gates")
    assert gates == [
        *("audit-all", "licenses", "drift", "load-soak"),
        *("restore-drill", "prod-probe"),
    ]
    said = dry_run("weekly")
    assert PIN_CALL.search(said)
    assert said.endswith(f"--serial {' '.join(gates)} -- {' '.join(gates)}")


FAKE_ISSUES = """#!{python}
import os, sys
args = sys.argv[1:]
with open(os.environ["CALLS"], "a") as log:
    log.write(" ".join(args) + "\\n")
if args[:2] == ["issue", "list"]:
    print(os.environ["OPEN_ISSUE"])
"""
ISSUE_STEP = re.compile(
    r"      - name: Open or update.*\n        if: failure\(\)\n"
    r"        run: \|\n((?:          .*\n)+)"
)


def finding(root: Path, open_issue: str) -> tuple[list[str], str]:
    step = ISSUE_STEP.search(WEEKLY)
    bash = shutil.which("bash")
    assert step
    assert bash
    gh = root / "bin/gh"
    gh.parent.mkdir(exist_ok=True)
    gh.write_text(FAKE_ISSUES.format(python=sys.executable), encoding="utf-8")
    gh.chmod(0o755)
    calls = root / "calls"
    calls.write_text("", encoding="utf-8")
    env = {
        "PATH": f"{gh.parent}:{os.environ['PATH']}",
        "CALLS": str(calls),
        "OPEN_ISSUE": open_issue,
        "RUN": "https://forge.example/run/1",
        "RATCHET_PLUGIN": str(PLUGIN_ROOT),
    }
    subprocess.run(  # noqa: S603 # -- a fixed argv; the script is the template's own
        [bash, "-e", "-c", textwrap.dedent(step.group(1))],
        cwd=root,
        env=env,
        check=True,
        capture_output=True,
    )
    return calls.read_text(encoding="utf-8").splitlines(), (root / "finding.md").read_text()


def test_a_red_weekly_run_opens_one_issue_and_then_comments_on_it(tmp_path: Path) -> None:
    (tmp_path / "weekly.txt").write_text("parallel: red: licenses\n", encoding="utf-8")
    calls, body = finding(tmp_path, "")
    assert calls[0].startswith("issue list --state open --search")
    assert calls[1:] == ["issue create --title Weekly gates: a finding --body-file finding.md"]
    assert "were red in https://forge.example/run/1" in body
    assert "parallel: red: licenses" in body
    calls, _ = finding(tmp_path, "7")
    assert calls[1:] == ["issue comment 7 --body-file finding.md"]
    (tmp_path / "weekly.txt").unlink()
    _, body = finding(tmp_path, "7")
    assert "the run failed before the gates started" in body


def test_the_issue_carries_the_output_of_each_red_gate_and_not_the_green_gates_that_followed(
    tmp_path: Path,
) -> None:
    noise = "".join(f"a green gate says {n}\n" for n in range(100))
    (tmp_path / "weekly.txt").write_text(
        "parallel: audit-all: started\n==> audit-all <==\nthe audit found CVE-1\n"
        "parallel: audit-all: red (exit 1) in 1.0s\n==> drift <==\n"
        + noise
        + "parallel: drift: green in 4.3s\naudit-all: red (exit 1)\nparallel: red\n",
        encoding="utf-8",
    )
    _, body = finding(tmp_path, "")
    assert "==> audit-all <==\nthe audit found CVE-1\nparallel: audit-all: red (exit 1)" in body
    assert "a green gate says" not in body
    (tmp_path / "weekly.txt").write_text("parallel: red\nsome other runner\n", encoding="utf-8")
    _, body = finding(tmp_path, "")
    assert "some other runner" in body


def test_the_plugin_version_has_one_home_the_binding() -> None:
    text = (TEMPLATES / "docs/adr/0001-adopt-the-ratchet-method.md").read_text(encoding="utf-8")
    assert "\n- Ratchet plugin: <X.Y.Z> at <ratchet-sha> tree <ratchet-tree-id>\n" in BINDING
    assert "<X.Y.Z>" not in text
    assert "<ratchet-sha>" not in text
    assert "https://github.com/Simon-GitHub-Code/ratchet" in text


def pin_recipe(root: Path, plugin: Path) -> tuple[int, str]:
    just = shutil.which("just")
    assert just
    done = subprocess.run(  # noqa: S603 # -- a fixed argv; the justfile is the template's own
        [just, "--justfile", str(TEMPLATES / "justfile"), "-d", str(root), "plugin-pin"],
        env={**os.environ, "RATCHET_PLUGIN": str(plugin)},
        capture_output=True,
        text=True,
        check=False,
    )
    return done.returncode, done.stdout


def test_a_tier_says_what_to_set_when_no_plugin_is_where_the_justfile_looks(
    tmp_path: Path,
) -> None:
    needs_just()
    code, said = pin_recipe(tmp_path, tmp_path / "absent")
    assert code == 1
    assert f"the plugin at {tmp_path / 'absent'} cannot be checked" in said


def test_a_tier_is_red_until_the_binding_pins_the_plugin_it_runs_from(tmp_path: Path) -> None:
    needs_just()
    version = plugin_pin.version_of(PLUGIN_ROOT)
    binding = tmp_path / "docs/process/binding.md"
    binding.parent.mkdir(parents=True)
    copy = repos.installed_copy()
    held = plugin_pin.copy_tree_id(copy)
    binding.write_text(
        f"- Ratchet plugin: {version}-other at {'1' * 40} tree {held}\n", encoding="utf-8"
    )
    code, said = pin_recipe(tmp_path, copy)
    assert (code, said.splitlines()[-1]) == (1, "plugin-pin: red")
    commit = plugin_pin.commit_of(copy) or "1" * 40
    binding.write_text(f"- Ratchet plugin: {version} at {commit} tree {held}\n", encoding="utf-8")
    code, said = pin_recipe(tmp_path, copy)
    assert (code, said.splitlines()[-1]) == (0, "plugin-pin: green")


def test_adopt_names_the_records_skill_and_the_owner_s_steps() -> None:
    assert "mattpocock-skills:grilling" not in ADOPT
    assert "`/mattpocock-skills:grill-with-docs`" in ADOPT
    assert "`mattpocock-skills:domain-modeling`" in ADOPT
    assert "Run `mattpocock-skills:grill-with-docs`" not in ADOPT
    assert "do not create `DESIGN.md`" in ADOPT
    assert "run `just hooks-install`" in ADOPT
    assert "An `owner:` line" in ADOPT
    assert "`git config --unset core.hooksPath`" not in ADOPT
    assert "exempts a range made only of such commits" in ADOPT
    assert 'ref: "<ratchet-sha>"' in ADOPT


FAKE_GH = """#!{python} -S
import json, os, sys
def path_of(args):
    return " ".join(a for a in args if a.startswith("repos/"))
args = sys.argv[1:]
world = json.load(open(os.environ["WORLD"]))
with open(os.environ["CALLS"], "a") as log:
    log.write(" ".join(args) + "\\n")
fields = dict(a.split("=", 1) for a in args if "=" in a)
for a in args:
    if a.startswith("body=@"):
        with open(os.environ["CALLS"] + ".bodies", "a") as bodies:
            bodies.write(path_of(args) + "\\n" + open(a[6:]).read() + "---\\n")
path = [a for a in args if a.startswith("repos/")]
path = path[0] if path else ""
if args[:2] == ["pr", "list"]:
    print("\\n".join(world["open"]))
elif path.endswith("/workflows/deep.yml/runs"):
    print(json.dumps(world["deep"]))
elif path.endswith("/workflows/ci.yml/runs") and "branch" in fields:
    print(json.dumps(world["tier1"]))
elif path.endswith("/workflows/ci.yml/runs"):
    print(json.dumps(world["ci"][fields["head_sha"]]))
elif path.endswith("/pulls") and "GET" in args:
    print(json.dumps(world["pulls"][fields["head"].split(":", 1)[1]]))
elif "/events" in path:
    print(json.dumps(world["events"].get(path.split("/")[-2], [])))
elif path.endswith("/comments") and "-X" not in args:
    print(world["comments"].get(path.split("/")[-2], ""))
elif "/pulls/" in path and "-X" not in args:
    print(world["now"][path.split("/")[-1]])
elif "PUT" in args:
    if fields["sha"] in world["refused"]:
        print("Head branch was modified", file=sys.stderr)
        sys.exit(1)
    print("Pull Request successfully merged")
"""
FAKE_GIT = """#!/bin/sh
echo git "$@" >> "$CALLS"
if [ "$3" = rev-list ]; then printf '0\\t0\\n'; fi
"""
FAKE_JUDGE = """import json, os, sys
args = sys.argv[1:]
head = args[args.index("--head") + 1]
report = args[args.index("--report") + 1]
open(os.environ["CALLS"], "a").write("judge " + " ".join(args) + "\\n")
open(report, "w").write("<!-- ratchet:merge -->\\nverdict for " + head + "\\n")
sys.exit(0 if head in json.load(open(os.environ["WORLD"]))["agent"] else 1)
"""
MERGE_STEP = re.compile(
    r"      - name: Judge every green.*\n        run: \|\n((?:          .*\n)+)"
)
OURS = {"full_name": "owner/repo"}
ON_THE_TIP = {"event": "push", "status": "completed", "conclusion": "success", "head_sha": "f" * 40}


def one_pull(number: int, sha: str, branch: str, *, base: str = "main") -> dict[str, object]:
    head = {"ref": branch, "sha": sha, "repo": OURS}
    return {"number": number, "state": "open", "head": head, "base": {"ref": base}}


def ci_runs(
    number: int, sha: str, branch: str, *, conclusion: str = "success"
) -> dict[str, object]:
    run = {
        "id": number,
        "event": "pull_request",
        "path": ".github/workflows/ci.yml",
        "status": "completed",
        "conclusion": conclusion,
        "head_sha": sha,
        "head_branch": branch,
        "head_repository": OURS,
        "created_at": "2026-11-12T10:00:00Z",
        "pull_requests": [{"number": number, "base": {"ref": "main"}}],
    }
    return {"total_count": 1, "workflow_runs": [run]}


def forge(heads: dict[int, tuple[str, str]]) -> dict[str, Any]:
    return {
        "open": [f"{n} {sha} {branch} main" for n, (sha, branch) in heads.items()],
        "deep": {"total_count": 1, "workflow_runs": [{**ON_THE_TIP, "head_repository": OURS}]},
        "tier1": {"total_count": 1, "workflow_runs": [{**ON_THE_TIP, "head_repository": OURS}]},
        "ci": {sha: ci_runs(n, sha, branch) for n, (sha, branch) in heads.items()},
        "pulls": {branch: [one_pull(n, sha, branch)] for n, (sha, branch) in heads.items()},
        "events": {},
        "now": {str(n): f"open {sha} main" for n, (sha, _) in heads.items()},
        "comments": {},
        "agent": [sha for sha, _ in heads.values()],
        "refused": [],
    }


def merge_job(tmp_path: Path, world: dict[str, Any]) -> tuple[int, str, str]:
    step = MERGE_STEP.search(MERGE)
    bash = shutil.which("bash")
    assert step
    assert bash
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "gh").write_text(FAKE_GH.format(python=sys.executable), encoding="utf-8")
    (bin_dir / "git").write_text(FAKE_GIT, encoding="utf-8")
    for tool in (bin_dir / "gh", bin_dir / "git"):
        tool.chmod(0o755)
    scripts = tmp_path / "plugin/scripts"
    scripts.mkdir(parents=True)
    (scripts / "merge_ready.py").write_text(FAKE_JUDGE, encoding="utf-8")
    for name in ("forge_runs.py", "gatelib.py"):
        shutil.copy(TEMPLATES.parents[2] / "scripts" / name, scripts / name)
    (tmp_path / "world.json").write_text(json.dumps(world), encoding="utf-8")
    calls = tmp_path / "calls"
    done = subprocess.run(  # noqa: S603 # -- a fixed argv; the script is the template's own
        [bash, "-e", "-c", textwrap.dedent(step.group(1))],
        cwd=tmp_path,
        env={
            **os.environ,
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "WORLD": str(tmp_path / "world.json"),
            "CALLS": str(calls),
            "RUNNER_TEMP": str(tmp_path),
            "RATCHET_PLUGIN": str(tmp_path / "plugin"),
            "DEFAULT_BRANCH": "main",
            "GITHUB_REPOSITORY": "owner/repo",
        },
        capture_output=True,
        text=True,
        check=False,
    )
    return done.returncode, done.stdout, calls.read_text(encoding="utf-8")


def test_the_merge_job_judges_every_green_pull_request_not_only_the_one_that_woke_it(
    tmp_path: Path,
) -> None:
    heads = {n: (letter * 4, f"feat/{letter}") for n, letter in enumerate("abcdef", 1)}
    heads[7] = ("gggg", "feat/g#x")
    world = forge(heads)
    world["ci"] = {**world["ci"], "cccc": ci_runs(3, "cccc", "feat/c", conclusion="failure")}
    world["pulls"]["feat/e"].append(one_pull(8, "eeee", "feat/e", base="weak"))
    world["now"] = {**world["now"], "6": "open 9999 main"}
    world |= {"comments": {"2": "77"}, "refused": ["dddd"]}
    world["agent"] = ["aaaa", "dddd", "eeee", "ffff", "gggg"]
    code, out, log = merge_job(tmp_path, world)
    assert code == 1, "a merge the forge refused leaves the run red"
    assert "#3: ci is not green on cccc: left alone" in out
    assert "#5: #8 is a pull request from `feat/e` against `weak`" in out
    assert "::error::#6: its head or its base changed while it was judged" in out
    assert "branch feat/g#x stays" in out
    assert "issues/3/comments" not in log
    assert "-X POST repos/owner/repo/issues/5/comments" in log
    assert "-X POST repos/owner/repo/issues/1/comments" in log
    assert "-X PATCH repos/owner/repo/issues/comments/77" in log
    assert "-X POST repos/owner/repo/issues/4/comments" in log
    assert log.count("-X PUT") == 3
    for number in (2, 3, 5, 6):
        assert f"pulls/{number}/merge" not in log
    assert log.count("-X DELETE repos/owner/repo/git/refs/heads/feat/a") == 1
    assert log.count("-X DELETE") == 1
    assert log.count("workflow run deep.yml --ref main") == 1
    judged = [line for line in log.splitlines() if line.startswith("judge ")]
    assert len(judged) == 5
    assert all(" --tier1 green --tier2 green " in line for line in judged)
    fetches = [line for line in log.splitlines() if line.startswith("git fetch")]
    assert fetches[0].endswith("+refs/heads/main:refs/remotes/origin/main")
    assert [line.split()[-1] for line in fetches[1:]] == ["aaaa", "bbbb", "dddd", "ffff", "gggg"]


def test_the_merge_job_counts_nothing_the_forge_did_not_answer(tmp_path: Path) -> None:
    world = forge({1: ("aaaa", "feat/a"), 2: ("bbbb", "feat/b"), 3: ("cccc", "feat/c")})
    del world["deep"]
    del world["tier1"]
    del world["ci"]["aaaa"]
    del world["pulls"]["feat/b"]
    code, out, log = merge_job(tmp_path, world)
    assert code == 0
    assert "#1: the forge's answer could not be read: left alone" in out
    assert "#2: the forge's answer could not be read: left alone" in out
    judged = [line for line in log.splitlines() if line.startswith("judge ")]
    assert [" --head cccc " in line and " --tier1 red --tier2 red " in line for line in judged] == [
        True
    ]
    assert log.count("-X PUT") == 1


def test_the_merge_job_hands_the_judge_the_ci_a_push_started_on_the_default_branch(
    tmp_path: Path,
) -> None:
    world = forge({1: ("aaaa", "feat/a"), 2: ("bbbb", "feat/b")})
    world["tier1"]["workflow_runs"][0]["conclusion"] = "failure"
    _, _, log = merge_job(tmp_path, world)
    asked = [line for line in log.splitlines() if "ci.yml/runs" in line and "branch=main" in line]
    assert len(asked) == 1
    assert all(f" {field} " in f"{asked[0]} " for field in ("event=push", "status=completed"))
    judged = [line for line in log.splitlines() if line.startswith("judge ")]
    assert len(judged) == 2
    assert all(" --tier1 red --tier2 green " in line for line in judged)


def bodies(root: Path) -> dict[str, str]:
    sent = (root / "calls.bodies").read_text(encoding="utf-8").split("---\n")[:-1]
    return dict(entry.split("\n", 1) for entry in sent)


def test_the_merge_job_tells_the_owner_of_a_pull_request_it_leaves_alone_for_the_owner_s_sake(
    tmp_path: Path,
) -> None:
    heads = {n: (letter * 4, f"feat/{letter}") for n, letter in enumerate("abcde", 1)}
    world = forge(heads)
    world["ci"]["aaaa"]["workflow_runs"][0]["pull_requests"].append(
        {"number": 9, "base": {"ref": "weak"}}
    )
    world["pulls"]["feat/a"].append(one_pull(9, "aaaa", "feat/a", base="weak"))
    world["open"][1] = "2 bbbb feat/b develop"
    world["ci"]["bbbb"]["workflow_runs"][0]["pull_requests"] = [
        {"number": 2, "base": {"ref": "develop"}}
    ]
    world["pulls"]["feat/b"] = [one_pull(2, "bbbb", "feat/b", base="develop")]
    world["events"] = {"3": [{"event": "base_ref_changed"}]}
    world["ci"]["dddd"] = ci_runs(4, "dddd", "feat/d", conclusion="failure")
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    code, out, log = merge_job(first, world)
    assert code == 0
    for number in (1, 2, 3):
        assert f"#{number}: merge: owner" in out
        assert log.count(f"-X POST repos/owner/repo/issues/{number}/comments") == 1
    sent = bodies(first)
    twin = "- `twin`: #9 is a pull request from `feat/a` against `weak`"
    target = "- `target`: the pull request targets `develop`, not the default branch `main`"
    assert twin in sent["repos/owner/repo/issues/1/comments"]
    assert target in sent["repos/owner/repo/issues/2/comments"]
    assert "- `retargeted`: its base" in sent["repos/owner/repo/issues/3/comments"]
    for number in (1, 2, 3):
        body = sent[f"repos/owner/repo/issues/{number}/comments"]
        assert body.startswith("<!-- ratchet:merge -->\n**merge: owner** at `")
    assert "#4: ci is not green on dddd: left alone" in out
    assert "issues/4/comments" not in log
    assert "pulls/1/merge" not in log
    assert log.count("-X PUT") == 1, "the clean pull request still merges"
    world["comments"] = {"1": "11", "2": "22", "3": "33"}
    code, _, log = merge_job(second, world)
    assert code == 0
    for number, comment in ((1, 11), (2, 22), (3, 33)):
        assert f"-X PATCH repos/owner/repo/issues/comments/{comment}" in log
        assert f"-X POST repos/owner/repo/issues/{number}/comments" not in log


def test_no_markdown_template_escapes_a_backtick_inside_inline_code() -> None:
    skills = TEMPLATES.parents[1]
    files = [*TEMPLATES.rglob("*.md"), *(skills / "init/templates").rglob("*.md")]
    assert files
    bad = [str(f.relative_to(PLUGIN_ROOT)) for f in files if "\\`" in f.read_text("utf-8")]
    assert not bad, f"use a double-backtick span instead of a backslash-escaped backtick: {bad}"


def test_every_plugin_script_runs_through_the_one_interpreter_variable() -> None:
    allowed = ("plugin_py :=", "printf ")
    bare = [
        line
        for line in JUSTFILE.splitlines()
        if not line.lstrip().startswith("#")
        and re.search(r"\bpython3 (?!-I )", line)
        and not any(part in line for part in allowed)
    ]
    assert bare == []


def test_the_tests_changed_recipe_runs_the_plugin_script_through_changed_and_takes_a_scope() -> (
    None
):
    body = recipe("tests-changed")
    assert body.strip() == (
        '{{changed}} -- {{plugin_py}} "{{ratchet_plugin}}/scripts/changed_tests.py"'
        " --base {base} {{suites}}"
    )
    assert re.search(r"^tests-changed \*suites: _plugin$", JUSTFILE, re.MULTILINE)
    assert "tests-changed" in gate_list("tier1_gates")
    assert "tests-changed" not in gate_list("tier1_sharded")


def test_the_later_tier_suite_recipe_of_the_template_takes_files() -> None:
    assert re.search(r"^e2e \*files:$", JUSTFILE, re.MULTILINE)
    assert "with files given, only those files" in recipe("e2e")
    assert re.search(r"^test-files \*files:$", JUSTFILE, re.MULTILINE)


def test_the_binding_template_holds_the_suites_table_and_names_the_gate_in_affected_only() -> None:
    suites, problems = suites_of(BINDING)
    assert suites is None
    assert problems == [suites_module.PLACEHOLDER]
    assert "| changed tests run |" in BINDING
    assert "| `just tests-changed` |" in BINDING
    affected = next(line for line in BINDING.splitlines() if line.startswith("- Affected only:"))
    assert "`tests-changed`" in affected


def test_the_ci_template_job_that_runs_tests_changed_asks_for_the_later_tier_setup() -> None:
    job = WORKFLOWS["ci.yml"].partition("  tier1:\n")[2].partition("  tier1-long:")[0]
    assert '- { name: "bind: the setup each later-tier suite needs' in job
    assert "if: matrix.gate == 'tests-changed'" in job
    assert "deep.yml sets up the whole suite" in job
    assert "tests-changed" not in WORKFLOWS["deep.yml"]


def test_every_toolchain_placeholder_is_a_step_that_fails_until_the_project_replaces_it() -> None:
    for text in (*WORKFLOWS.values(), WEEKLY):
        placeholders = [line for line in text.splitlines() if "bind:" in line]
        assert placeholders
        for line in placeholders:
            assert re.fullmatch(r' +- \{ name: "bind: [^"]+", run: exit 1 \}', line), line


def concurrency_of(text: str) -> str:
    return text.partition("\nconcurrency:\n")[2].partition("\nenv:\n")[0]


def test_a_superseded_run_is_cancelled_but_a_default_branch_ci_or_a_merge_in_flight_runs_out() -> (
    None
):
    pulls_only = "  cancel-in-progress: ${{ github.event_name == 'pull_request' }}"
    assert concurrency_of(WORKFLOWS["ci.yml"]) == f"  group: ci-${{{{ github.ref }}}}\n{pulls_only}"
    assert concurrency_of(WORKFLOWS["deep.yml"]).endswith("\n  cancel-in-progress: true")
    assert concurrency_of(WEEKLY).endswith("\n  cancel-in-progress: true")
    assert concurrency_of(MERGE) == "  group: merge\n  cancel-in-progress: false"


def test_the_merge_job_runs_only_after_a_green_ci_of_a_pull_request_from_this_repository() -> None:
    condition = MERGE.partition("  merge:\n    if: >-\n")[2].partition("\n    runs-on:")[0]
    assert [line.strip() for line in condition.splitlines()] == [
        "github.event.workflow_run.event == 'pull_request'",
        "&& github.event.workflow_run.conclusion == 'success'",
        "&& github.event.workflow_run.path == '.github/workflows/ci.yml'",
        "&& github.event.workflow_run.head_repository.full_name == github.repository",
    ]
    assert "on:\n  workflow_run:\n    workflows: [ci]\n    types: [completed]\n" in MERGE


def test_the_merge_job_merges_only_the_head_it_judged_and_as_a_merge_commit(
    tmp_path: Path,
) -> None:
    code, _, log = merge_job(tmp_path, forge({1: ("aaaa", "feat/a")}))
    assert code == 0
    [merge] = [line for line in log.splitlines() if "-X PUT" in line]
    assert "repos/owner/repo/pulls/1/merge -f sha=aaaa -f merge_method=merge" in merge


def mutation_value(env_set: dict[str, str], tmp_path: Path) -> str:
    just = shutil.which("just")
    assert just
    bound = JUSTFILE.replace(
        "bind: the mutation tool's option that reuses the last run's results", "--mode-on"
    )
    path = tmp_path / "justfile"
    path.write_text(
        f'{bound}\nshow:\n    @echo "[{{{{mutation_incremental}}}}]"\n', encoding="utf-8"
    )
    env = {k: v for k, v in os.environ.items() if k not in ("CI", "RATCHET_BITING")}
    done = subprocess.run(  # noqa: S603 # -- a fixed argv; the justfile is the template's own
        [just, "--justfile", str(path), "show"],
        env={**env, **env_set},
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout.strip()


def test_the_mutation_incremental_variable_holds_the_bound_mode_locally_only(
    tmp_path: Path,
) -> None:
    needs_just()
    assert mutation_value({}, tmp_path) == "[--mode-on]"
    assert mutation_value({"CI": "true"}, tmp_path) == "[]"
    assert mutation_value({"RATCHET_BITING": "1"}, tmp_path) == "[]"


def test_the_binding_template_has_an_incremental_line_naming_the_justfile_variable() -> None:
    line = next(item for item in BINDING.splitlines() if item.startswith("- Incremental:"))
    assert "`mutation_incremental`" in line
    assert "`none: <reason>`" in line
    assert "mutation_incremental :=" in JUSTFILE
    assert "`mutation_incremental` option" in recipe("mutation-changed")


HASH_COMMENTED = sorted(
    path
    for root in (
        TEMPLATES,
        PLUGIN_ROOT / "skills/init/templates",
        PLUGIN_ROOT / "skills/method",
    )
    for path in root.rglob("*")
    if path.is_file()
    and (path.suffix in {".yml", ".yaml", ".toml"} or path.name in {"justfile", "gitignore"})
)


def prose_comment_lines(text: str) -> list[int]:
    found = []
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.lstrip()
        if stripped.startswith("#!"):
            continue
        note = line[line.index(" # ") + 1 :] if " # " in line else ""
        if stripped.startswith("#") or (note and not pins.VERSION_COMMENT.match(note)):
            found.append(number)
    return found


@pytest.mark.parametrize(
    "path", HASH_COMMENTED, ids=lambda path: str(path.relative_to(PLUGIN_ROOT))
)
def test_a_template_or_catalogue_file_ships_no_comment_but_a_pin_note(path: Path) -> None:
    assert prose_comment_lines(path.read_text(encoding="utf-8")) == []


def test_a_comment_planted_in_a_template_is_found_and_a_pin_note_is_not() -> None:
    planted = JUSTFILE + "# a reason\nlint:\n    true  # a reason\n"
    pinned = "      - uses: owner/action@" + "0" * 40 + " # v4.2.2\n"
    lines = len(JUSTFILE.splitlines())
    assert prose_comment_lines(planted) == [lines + 1, lines + 3]
    assert prose_comment_lines("#!/bin/sh\n" + pinned) == []
