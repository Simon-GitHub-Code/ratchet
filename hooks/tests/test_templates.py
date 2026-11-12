"""The adopt templates: pinned, naming the plugin by commit, and the records' placeholders."""

import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

import pins
import plugin_pin

TEMPLATES = Path(__file__).resolve().parents[2] / "skills/adopt/templates"
PLUGIN_ROOT = TEMPLATES.parents[2]
ADOPT = (TEMPLATES.parent / "SKILL.md").read_text(encoding="utf-8")
JUSTFILE = (TEMPLATES / "justfile").read_text(encoding="utf-8")
BINDING = (TEMPLATES / "docs/process/binding.md").read_text(encoding="utf-8")
MERGE = (TEMPLATES / ".github/workflows/merge.yml").read_text(encoding="utf-8")
WORKFLOWS = {
    name: (TEMPLATES / ".github/workflows" / name).read_text(encoding="utf-8")
    for name in ("ci.yml", "deep.yml")
}
SHARDS_STEP = re.compile(r"      - id: shards\n        run: \|\n((?:          .*\n)+)")


def gate_list(name: str) -> list[str]:
    """A gate list of the justfile, evaluated by ``just`` as CI's shape job reads it."""
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


def recipe(name: str) -> str:
    match = re.search(rf"^{name}:\n((?:    .*\n)+)", JUSTFILE, re.MULTILINE)
    assert match, name
    return match.group(1)


def shards(
    binding: str, tmp_path: Path, workflow: str, *, justfile: str = JUSTFILE
) -> tuple[int, dict[str, str], str]:
    """Run the workflow's shape step as the runner would: exit code, outputs, what it printed."""
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
    # Why: commitment 1, CI runs the same entry points. The justfile's list is the only list: the
    # shape job reads it, so a gate the tier lists is a job, and no job names a gate the tier lacks.
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
    # Why: observed on GitHub, 2026-11-12: a matrix vector with no values fails the whole run, and
    # a job-level `if` that is false skips the job before its matrix is read.
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
    """The command line a recipe would run, as ``just`` expands it."""
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


RUNNER = 'python3 "/plugin/scripts/parallel.py" --runner'


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
    # Why: a developer's wall clock is the slowest gate, not the sum, from the first adoption.
    needs_just()
    pin, runner, gates = dry_run(name).partition(RUNNER)
    said = runner + gates
    assert 'python3 "/plugin/scripts/plugin_pin.py"' in pin, "the pin is judged before any gate"
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
    for variable in ("serial0", "serial1", "serial2"):
        assert f'{variable} := ""' in JUSTFILE
    bound = tmp_path / "justfile"
    bound.write_text(
        JUSTFILE.replace('serial1 := ""', 'serial1 := "--serial coverage diff-coverage"'),
        encoding="utf-8",
    )
    assert " --serial coverage diff-coverage -- " in dry_run("check", bound)
    assert " --serial coverage diff-coverage -- " in dry_run("tier1", bound)
    assert "--serial" not in dry_run("tier0", bound)


def test_a_recipe_learns_its_shard_from_the_environment_and_is_whole_without_one() -> None:
    assert 'shard := env_var_or_default("SHARD", "1")' in JUSTFILE
    assert 'shards := env_var_or_default("SHARDS", "1")' in JUSTFILE
    for name in ("test", "mutation-changed", "mutation", "e2e"):
        assert "shard {{shard}} of {{shards}}" in recipe(name), name


def test_the_three_service_gates_arrive_bound_to_the_shipped_scripts() -> None:
    assert '/scripts/with_service.py" --boot "\' + boot + \'" --ready "\' + ready' in JUSTFILE
    assert recipe("smoke").strip() == "{{booted}}"
    assert recipe("load-smoke").startswith("    {{booted}} -- python3 ")
    assert 'scripts/load_smoke.py" --url "bind: METHOD URL' in recipe("load-smoke")
    assert "{{ready}}" not in recipe("load-smoke"), "readiness alone overlaps nothing a user asks"
    assert 'scripts/telemetry_sink.py" --service "{{service}}" -- {{booted}} --' in recipe(
        "telemetry-e2e"
    )
    for name in ("boot", "ready", "service"):
        assert re.search(rf'^{name} := "bind: ', JUSTFILE, re.MULTILINE), name
    for question, script in (
        ("starts and answers", "with_service.py"),
        ("load smoke", "load_smoke.py"),
        ("telemetry arrives", "telemetry_sink.py"),
    ):
        assert f"\n| {question} | `ratchet` plugin `scripts/{script}`" in BINDING


def test_the_ci_templates_are_green_on_the_pins_gate() -> None:
    # Why: item 6 of the owner-free brief; a template that fails its own gate teaches the
    # wrong shape to every adopter.
    for name in ("ci.yml", "deep.yml"):
        path = f".github/workflows/{name}"
        text = (TEMPLATES / path).read_text(encoding="utf-8")
        assert pins.check_workflow(path, text) == [], name
        assert pins.check_images(path, text) == [], name
        assert 'repository: Simon-GitHub-Code/ratchet, ref: "<ratchet-sha>"' in text
        assert "RATCHET_PLUGIN: ${{ github.workspace }}/ratchet" in text


def test_ci_reports_a_ceiling_on_a_pull_request_and_holds_each_job_to_its_tier() -> None:
    # Why: tiers.md, Speed. Runners vary, so a ceiling blocks on the default branch and nightly.
    ci, deep = WORKFLOWS["ci.yml"], WORKFLOWS["deep.yml"]
    assert "  OVER: ${{ github.event_name == 'pull_request' && 'report' || 'block' }}\n" in ci
    assert ci.count('budget.py" tier0 --over "$OVER" --report') == 1
    assert ci.count('budget.py" "${{ matrix.gate }}" --tier tier1 --over "$OVER" --report') == 2
    assert ci.count("budget.py") == 3
    assert deep.count('budget.py" "${{ matrix.gate }}" --tier tier2 --report') == 2
    assert "--over" not in deep, "the nightly and the default branch block"
    assert deep.count("budget.py") == 2


def test_the_plugin_version_has_one_home_the_binding() -> None:
    text = (TEMPLATES / "docs/adr/0001-adopt-the-ratchet-method.md").read_text(encoding="utf-8")
    assert "\n- Ratchet plugin: <X.Y.Z> at <ratchet-sha>\n" in BINDING
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
    assert f"plugin-pin: no Ratchet plugin at {tmp_path / 'absent'}" in said
    assert "set RATCHET_PLUGIN to a checkout of the commit docs/process/binding.md pins" in said


def test_a_tier_is_red_until_the_binding_pins_the_plugin_it_runs_from(tmp_path: Path) -> None:
    needs_just()
    version = plugin_pin.version_of(PLUGIN_ROOT)
    binding = tmp_path / "docs/process/binding.md"
    binding.parent.mkdir(parents=True)
    binding.write_text(f"- Ratchet plugin: {version}-other at {'1' * 40}\n", encoding="utf-8")
    code, said = pin_recipe(tmp_path, PLUGIN_ROOT)
    assert (code, said.splitlines()[-1]) == (1, "plugin-pin: red")
    commit = plugin_pin.commit_of(PLUGIN_ROOT) or "1" * 40
    binding.write_text(f"- Ratchet plugin: {version} at {commit}\n", encoding="utf-8")
    code, said = pin_recipe(tmp_path, PLUGIN_ROOT)
    assert (code, said.splitlines()[-1]) == (0, "plugin-pin: green")


def test_adopt_names_the_two_skills_and_the_owner_s_steps() -> None:
    assert "`mattpocock-skills:grilling`" in ADOPT
    assert "`mattpocock-skills:domain-modeling`" in ADOPT
    assert "Run `mattpocock-skills:grill-with-docs`" not in ADOPT
    assert "do not create `DESIGN.md`" in ADOPT
    assert "`pre-commit install`" in ADOPT
    assert "scripts/hooks_installed.py" in ADOPT
    assert "`git config --unset core.hooksPath`" not in ADOPT
    assert "exempts a range made only of such commits" in ADOPT
    assert 'ref: "<ratchet-sha>"' in ADOPT


FAKE_GH = """#!{python}
import json, os, sys
args = sys.argv[1:]
world = json.load(open(os.environ["WORLD"]))
with open(os.environ["CALLS"], "a") as log:
    log.write(" ".join(args) + "\\n")
joined = " ".join(args)
if args[:2] == ["pr", "list"]:
    print("\\n".join(world["open"]))
elif joined.startswith("run list --workflow deep.yml"):
    print("success")
elif joined.startswith("run list --workflow ci.yml"):
    print(world["ci"][args[args.index("--commit") + 1]])
elif args[0] == "api" and args[1].endswith("/comments"):
    print(world["comments"].get(args[1].split("/")[-2], ""))
elif args[0] == "api" and "-X" in args and args[args.index("-X") + 1] == "PUT":
    sha = [a for a in args if a.startswith("sha=")][0][4:]
    if sha in world["refused"]:
        print("Head branch was modified", file=sys.stderr)
        sys.exit(1)
    print("Pull Request successfully merged")
"""
FAKE_GIT = '#!/bin/sh\necho git "$@" >> "$CALLS"\n'
FAKE_JUDGE = """import json, os, sys
args = sys.argv[1:]
head = args[args.index("--head") + 1]
report = args[args.index("--report") + 1]
open(report, "w").write("<!-- ratchet:merge -->\\nverdict for " + head + "\\n")
sys.exit(0 if head in json.load(open(os.environ["WORLD"]))["agent"] else 1)
"""
MERGE_STEP = re.compile(
    r"      - name: Judge every green.*\n        run: \|\n((?:          .*\n)+)"
)


def test_the_merge_job_judges_every_green_pull_request_not_only_the_one_that_woke_it(
    tmp_path: Path,
) -> None:
    # Why: observed on GitHub, 2026-11-12: of four runs queued together, two were cancelled, and
    # the pull requests they stood for were never judged. A run that judges all of them loses none.
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
    (tmp_path / "plugin/scripts").mkdir(parents=True)
    (tmp_path / "plugin/scripts/merge_ready.py").write_text(FAKE_JUDGE, encoding="utf-8")
    world = {
        "open": [f"{n} {sha * 4} feat/{sha} main" for n, sha in enumerate("abcd", 1)],
        "ci": {
            "aaaa": "completed success",
            "bbbb": "completed success",
            "cccc": "completed failure",
            "dddd": "completed success",
        },
        "comments": {"2": "77"},
        "agent": ["aaaa", "dddd"],
        "refused": ["dddd"],
    }
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
    log = calls.read_text(encoding="utf-8")
    assert done.returncode == 1, "a merge the forge refused leaves the run red"
    assert "#3: ci is not green on cccc: left alone" in done.stdout
    assert "issues/3" not in log
    assert "-X POST repos/owner/repo/issues/1/comments" in log
    assert "-X PATCH repos/owner/repo/issues/comments/77" in log
    assert "-X POST repos/owner/repo/issues/4/comments" in log
    assert log.count("-X PUT") == 2
    assert "pulls/2/merge" not in log
    assert log.count("-X DELETE repos/owner/repo/git/refs/heads/feat/a") == 1
    assert "heads/feat/d" not in log
    assert log.count("workflow run deep.yml --ref main") == 1
    fetches = [line for line in log.splitlines() if line.startswith("git fetch")]
    assert [line.split()[-1] for line in fetches] == ["aaaa", "bbbb", "dddd"]
