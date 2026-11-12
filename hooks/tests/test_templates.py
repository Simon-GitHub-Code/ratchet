"""The adopt templates: pinned, naming the plugin by commit, and the records' placeholders."""

import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest

import pins
import plugin_pin

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


def test_a_gate_that_requests_routes_boots_with_the_flags_due_for_removal_on() -> None:
    assert re.search(r'^flags_on := "bind: NAME=VALUE, ', JUSTFILE, re.MULTILINE)
    assert "probed := booted + ' --env \"' + flags_on + '\"'" in JUSTFILE
    assert "flags.py --temporary" in JUSTFILE
    for name in ("api-deep", "dast", "pentest", "load"):
        assert "booted through probed" in recipe(name), name
    for name in ("smoke", "telemetry-e2e"):
        assert "probed" not in recipe(name), f"{name} judges the defaults, which are what ships"


def test_the_three_service_gates_arrive_bound_to_the_shipped_scripts() -> None:
    assert '/scripts/with_service.py" --boot "\' + boot + \'" --ready "\' + ready' in JUSTFILE
    assert recipe("smoke").strip() == "{{booted}}"
    assert recipe("load-smoke").startswith("    {{probed}} -- python3 ")
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
    for name in ("ci.yml", "deep.yml", "weekly.yml"):
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


def test_the_weekly_workflow_is_scheduled_and_may_only_read_and_write_issues() -> None:
    assert "on:\n  schedule:\n" in WEEKLY
    assert "\npermissions:\n  contents: read\n" in WEEKLY
    assert "    permissions:\n      contents: read\n      issues: write\n" in WEEKLY
    assert re.findall(r"^ +([a-z-]+): write$", WEEKLY, re.MULTILINE) == ["issues"]
    assert "        shell: bash\n        run: just weekly 2>&1 | tee weekly.txt\n" in WEEKLY


def test_the_weekly_entry_point_runs_the_four_gates_one_after_another() -> None:
    needs_just()
    gates = gate_list("weekly_gates")
    assert gates == ["audit-all", "licenses", "drift", "load-soak"]
    said = dry_run("weekly")
    assert 'python3 "/plugin/scripts/plugin_pin.py"' in said
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
    """The calls the issue step makes to ``gh`` and the body it files, given the open issue."""
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
    # Why: observed on a forge, 2026-11-12: the last 60 lines of the log were the green gates'
    # output, and the red gate's reason was above them.
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
    assert "run `just hooks-install`" in ADOPT
    assert "An `owner:` line" in ADOPT
    assert "`git config --unset core.hooksPath`" not in ADOPT
    assert "exempts a range made only of such commits" in ADOPT
    assert 'ref: "<ratchet-sha>"' in ADOPT


FAKE_GH = """#!{python}
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
TIER2 = {"event": "push", "status": "completed", "conclusion": "success", "head_sha": "f" * 40}


def one_pull(number: int, sha: str, branch: str, *, base: str = "main") -> dict[str, object]:
    head = {"ref": branch, "sha": sha, "repo": OURS}
    return {"number": number, "state": "open", "head": head, "base": {"ref": base}}


def ci_runs(
    number: int, sha: str, branch: str, *, conclusion: str = "success"
) -> dict[str, object]:
    """The forge's answer for the ``ci`` runs on ``sha``: one, for pull request ``number``."""
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
    """A forge where every pull request in ``heads`` is green, alone on its branch and unmoved."""
    return {
        "open": [f"{n} {sha} {branch} main" for n, (sha, branch) in heads.items()],
        "deep": {"total_count": 1, "workflow_runs": [{**TIER2, "head_repository": OURS}]},
        "ci": {sha: ci_runs(n, sha, branch) for n, (sha, branch) in heads.items()},
        "pulls": {branch: [one_pull(n, sha, branch)] for n, (sha, branch) in heads.items()},
        "events": {},
        "now": {str(n): f"open {sha} main" for n, (sha, _) in heads.items()},
        "comments": {},
        "agent": [sha for sha, _ in heads.values()],
        "refused": [],
    }


def merge_job(tmp_path: Path, world: dict[str, Any]) -> tuple[int, str, str]:
    """Run the template's merge step against ``world``: its exit code, its output, its calls."""
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
    # Why: observed on GitHub, 2026-11-12: of four runs queued together, two were cancelled, and
    # the pull requests they stood for were never judged. A run that judges all of them loses none.
    # One pull request per way the job goes: 1 merges, 2 is the owner's, 3 is red, 4 the forge
    # refuses, 5 has a twin against another base (ADR 0024), 6 moved while it was judged, and 7
    # has a branch name a URL would read.
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
    assert all(" --tier2 green " in line for line in judged)
    fetches = [line for line in log.splitlines() if line.startswith("git fetch")]
    assert fetches[0].endswith("+refs/heads/main:refs/remotes/origin/main")
    assert [line.split()[-1] for line in fetches[1:]] == ["aaaa", "bbbb", "dddd", "ffff", "gggg"]


def test_the_merge_job_counts_nothing_the_forge_did_not_answer(tmp_path: Path) -> None:
    # Why: an answer that is missing or cut short must read as red, and never as green.
    world = forge({1: ("aaaa", "feat/a"), 2: ("bbbb", "feat/b"), 3: ("cccc", "feat/c")})
    del world["deep"]
    del world["ci"]["aaaa"]
    del world["pulls"]["feat/b"]
    code, out, log = merge_job(tmp_path, world)
    assert code == 0
    assert "#1: the forge's answer could not be read: left alone" in out
    assert "#2: the forge's answer could not be read: left alone" in out
    judged = [line for line in log.splitlines() if line.startswith("judge ")]
    assert [" --head cccc " in line and " --tier2 red " in line for line in judged] == [True]
    assert log.count("-X PUT") == 1


def bodies(root: Path) -> dict[str, str]:
    """The comment bodies the merge job sent, by the API path they went to."""
    sent = (root / "calls.bodies").read_text(encoding="utf-8").split("---\n")[:-1]
    return dict(entry.split("\n", 1) for entry in sent)


def test_the_merge_job_tells_the_owner_of_a_pull_request_it_leaves_alone_for_the_owner_s_sake(
    tmp_path: Path,
) -> None:
    # Why: observed on a forge, 2026-11-12: a twin against another base, a retarget and a pull
    # request against another branch were left alone with a line in the log and no comment. 1 has
    # a twin, 2 is against `develop`, 3 was retargeted, 4 has a red ci and 5 is clean.
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
