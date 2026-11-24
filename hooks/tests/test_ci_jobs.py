import argparse
import json
import os
import re
import shlex
import shutil
import signal
import sys
import textwrap
from collections import Counter
from collections.abc import Callable
from pathlib import Path

import pytest

import budget
import ci_jobs
import conformance
import justfile_text
import upgrade
from tests import adopted
from tests import test_conformance as base
from tests import test_parallel as parallel_tests
from tests.repos import git
from tests.support import PLUGIN_ROOT

JUSTFILE = "justfile"
CI = ".github/workflows/ci.yml"
BINDING = "docs/process/binding.md"
THRESHOLDS = "quality/thresholds.toml"
JOBS_LINE = 'tier1_jobs := "test mutation-changed bite rest"'
NEW_STEP = '+          python3 "$RATCHET_PLUGIN/scripts/ci_jobs.py" --output "$GITHUB_OUTPUT"'
TOOLCHAIN = "      - run: ./scripts/install-toolchain\n"
BUILD_STEP = "      - if: matrix.gate == 'build'\n        run: ./scripts/install-build-tools\n"
RECORDER = textwrap.dedent(
    """\
    import os, sys
    with open(os.environ["RECORDED"], "a", encoding="utf-8") as out:
        out.write(sys.argv[1] + "\\n")
    sys.exit(1 if sys.argv[1] in os.environ.get("REDS", "").split() else 0)
    """
)
Mutate = Callable[[Path], None]


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    shutil.copytree(base.pristine(), tmp_path / "repo", symlinks=True)
    return tmp_path / "repo"


def read(root: Path, path: str) -> str:
    return (root / path).read_text(encoding="utf-8")


def on_justfile(root: Path) -> list[conformance.Finding]:
    return [found for found in conformance.findings(root) if found.path == JUSTFILE]


def named(found: conformance.Finding) -> str:
    return found.problem.split("`")[1]


def mended(root: Path, found: conformance.Finding) -> None:
    assert isinstance(found.fix, conformance.Auto), found
    (root / found.path).write_text(found.fix.want(read(root, found.path)), encoding="utf-8")


def jobs(root: Path) -> dict[str, tuple[str, ...]]:
    found = ci_jobs.lists(read(root, JUSTFILE))
    assert found is not None
    return {job.name: job.gates for job in ci_jobs.resolved(found)}


def with_seconds(*keys: tuple[str, float]) -> Mutate:
    def mutate(root: Path) -> None:
        text = read(root, THRESHOLDS)
        for key, seconds in keys:
            text = budget.with_ceiling(text, key, seconds)
        (root / THRESHOLDS).write_text(text, encoding="utf-8")

    return mutate


def test_every_gate_of_tier_1_runs_in_exactly_one_job_and_rest_takes_the_others(
    repo: Path,
) -> None:
    grouped = 'tier1_jobs := "test mutation-changed bite suite=coverage,audit rest"'
    base.replace(JUSTFILE, JOBS_LINE, grouped)(repo)
    assert base.red(repo) == []
    gates = justfile_text.ordered(read(repo, JUSTFILE), "tier1_gates")
    found = jobs(repo)
    assert list(found) == ["test", "mutation-changed", "bite", "suite", "rest"]
    assert found["suite"] == ("coverage", "audit")
    named_elsewhere = {"bite", "test", "mutation-changed", "coverage", "audit"}
    assert found["rest"] == tuple(g for g in gates if g not in named_elsewhere)
    assert sorted(gate for each in found.values() for gate in each) == sorted(gates)


def test_a_gate_no_job_runs_is_a_finding_whose_edit_adds_rest(repo: Path) -> None:
    base.replace(JUSTFILE, JOBS_LINE, 'tier1_jobs := "test mutation-changed bite"')(repo)
    found = on_justfile(repo)
    gates = justfile_text.ordered(read(repo, JUSTFILE), "tier1_gates")
    alone = {"test", "mutation-changed", "bite"}
    assert [named(f) for f in found] == [g for g in gates if g not in alone]
    assert found[0].problem == (
        "`coverage` is in `tier1_gates` and no job of `tier1_jobs` runs it; add `rest`, the job of"
        " every gate no other job names"
    )
    mended(repo, found[0])
    assert JOBS_LINE in read(repo, JUSTFILE)
    assert base.red(repo) == []


def test_a_gate_in_two_jobs_is_a_finding_for_an_agent(repo: Path) -> None:
    twice = 'tier1_jobs := "test mutation-changed bite suite=coverage,audit again=coverage rest"'
    base.replace(JUSTFILE, JOBS_LINE, twice)(repo)
    [found] = on_justfile(repo)
    assert (
        found.problem
        == "`coverage` runs in the jobs `suite` and `again` of `tier1_jobs`; name it in one"
    )
    assert isinstance(found.fix, conformance.Agent)


@pytest.mark.parametrize(
    ("mutate", "gates", "why"),
    [
        (
            with_seconds(("audit", 60), ("bite-all", 600)),
            ["audit"],
            "its own ceiling under `[ceilings.seconds]`",
        ),
        (
            base.replace(JUSTFILE, JOBS_LINE, 'tier1_jobs := "bite rest"'),
            ["test", "mutation-changed"],
            "it is in `tier1_sharded`, whose gates run in the long job",
        ),
        (
            base.replace(CI, TOOLCHAIN, TOOLCHAIN + BUILD_STEP),
            ["build"],
            "the workflow sets up a step for it alone by `matrix.gate`",
        ),
    ],
    ids=["ceiling", "shards", "set-up"],
)
def test_a_gate_that_must_be_alone_inside_a_group_is_a_finding_whose_edit_frees_it(
    repo: Path, *, mutate: Mutate, gates: list[str], why: str
) -> None:
    mutate(repo)
    found = on_justfile(repo)
    assert [named(f) for f in found] == gates
    for each in found:
        assert each.problem.startswith(f"`{named(each)}` runs in the group `rest`, but {why}")
        mended(repo, each)
    assert all(jobs(repo)[gate] == (gate,) for gate in gates)
    assert not set(gates) & set(jobs(repo)["rest"])
    assert base.red(repo) == []


def test_a_gate_alone_for_no_reason_is_moved_into_rest_unless_the_binding_says_why(
    repo: Path,
) -> None:
    alone = 'tier1_jobs := "test mutation-changed bite audit rest"'
    base.replace(JUSTFILE, JOBS_LINE, alone)(repo)
    [found] = on_justfile(repo)
    assert found.problem.startswith(
        "`audit` runs in a job of its own with no reason the check can read: no ceiling of its own"
    )
    mended(repo, found)
    assert JOBS_LINE in read(repo, JUSTFILE)
    base.replace(JUSTFILE, JOBS_LINE, alone)(repo)
    own = "- Own jobs: `audit` reads the advisory feed, which another job's cache would poison"
    base.replace(BINDING, "- Own jobs: stated for this stack", own)(repo)
    assert base.red(repo) == []


def test_a_workflow_that_reads_the_job_list_its_own_way_passes_and_one_that_reads_none_fails(
    repo: Path,
) -> None:
    step = 'python3 "$RATCHET_PLUGIN/scripts/ci_jobs.py" --output "$GITHUB_OUTPUT"'
    assert "just --evaluate" not in read(repo, CI)
    base.replace(CI, step, './scripts/matrix "$(just --evaluate tier1_jobs)" >> "$GITHUB_OUTPUT"')(
        repo
    )
    assert base.red(repo) == []
    base.replace(CI, "tier1_jobs", "tier1_gates")(repo)
    [found] = [f for f in conformance.findings(repo) if f.path == CI]
    assert found.problem == (
        "nothing turns the justfile's job list into the jobs tier 1 runs ('ci_jobs.py' absent)"
    )
    assert isinstance(found.fix, conformance.Agent)
    assert NEW_STEP in found.fix.start


SHAPE_STEP = 'python3 "$RATCHET_PLUGIN/scripts/ci_jobs.py" --output "$GITHUB_OUTPUT"'
OWN_MATRIX = 'echo "tier1=$(./scripts/matrix $(just --evaluate tier1_jobs))" >> "$GITHUB_OUTPUT"'


def test_no_edit_on_its_own_adds_a_job_to_a_list_the_workflow_reads_without_the_ci_job_route(
    repo: Path,
) -> None:
    base.replace(CI, SHAPE_STEP, OWN_MATRIX)(repo)
    base.replace(CI, ' -- just ci-job "${{ matrix.gate }}"', ' -- just "${{ matrix.gate }}"')(repo)
    base.replace(JUSTFILE, JOBS_LINE, 'tier1_jobs := "bite audit"')(repo)
    found = on_justfile(repo)
    assert {named(f) for f in found} >= {"audit", "test"}
    assert [f for f in found if not isinstance(f.fix, conformance.Agent)] == []


@pytest.mark.parametrize(
    ("value", "read_as"),
    [
        ('env_var_or_default("EXTRA", base)', None),
        ('"a" + "b c"', ("ab", "c")),
        ('base + " d"', ("a", "b", "c", "d")),
        ("'x y' # the project's note", ("x", "y")),
        ('"a" "b"', None),
        ("base + missing", None),
        ("looped", None),
        ('"a \\" b"', None),
    ],
)
def test_the_list_reader_reads_a_value_exactly_or_not_at_all(
    value: str, read_as: tuple[str, ...] | None
) -> None:
    justfile = f'base := "a b c"\nlisted := {value}\nlooped := listed\n'
    assert justfile_text.ordered(justfile, "listed") == read_as


UNREADABLE = 'tier1_jobs := env_var_or_default("EXTRA", "bite rest")'
OWN_LINE = "so the plugin left the project's own job list as it is"


def test_a_job_list_the_reader_cannot_read_exactly_is_never_red_unless_the_plugin_must_read_it(
    repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    base.replace(JUSTFILE, JOBS_LINE, UNREADABLE)(repo)
    [refused] = on_justfile(repo)
    assert isinstance(refused.fix, conformance.Agent)
    assert "`ci_jobs.py` in `.github/workflows/ci.yml` reads it" in refused.problem
    output = tmp_path / "output"
    assert ci_jobs.main(["ci_jobs.py", "--root", str(repo), "--output", str(output)]) == 1
    assert ci_jobs.main(["ci_jobs.py", "--root", str(repo), "--run", "rest"]) == 2
    said = capsys.readouterr().out.splitlines()
    assert said == [
        "justfile: `tier1_jobs` is not one plain string, so no jobs are read from it",
        "justfile: `tier1_jobs` is not one plain string, so no job `rest` is read from it",
    ]
    base.replace(CI, SHAPE_STEP, OWN_MATRIX)(repo)
    [offered] = on_justfile(repo)
    assert OWN_LINE in offered.problem
    assert isinstance(offered.fix, conformance.Agent)
    assert NEW_STEP in offered.fix.start
    red, _ = conformance.owed_split(repo, conformance.findings(repo))
    assert red == []


WAITING_RECIPES = (
    'delta:\n    @echo "later: code:outbound-call"\n\ngamma:\n    @echo "none: nothing calls out"\n'
)


def project(
    root: Path,
    jobs_line: str,
    rows: str,
    *,
    recipes: str = WAITING_RECIPES,
    speed: str = "- Shards: 2\n",
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / JUSTFILE).write_text(
        'tier1_gates := "bite alpha beta gamma delta"\ntier1_sharded := "alpha"\n'
        f"tier1_jobs := {jobs_line}\n\n{recipes}",
        encoding="utf-8",
    )
    (root / "docs/process").mkdir(parents=True)
    (root / BINDING).write_text(
        "# Binding\n\n## Gates\n\n| Question | Tool | Tier | Threshold source | Entry point |\n"
        "| --- | --- | --- | --- | --- |\n"
        f"{rows}\n## Speed\n\n{speed}",
        encoding="utf-8",
    )
    return root


WAITING = (
    "| d | later: code:outbound-call | 1 | | `just delta` |\n"
    "| e | none: nothing calls out | 1 | | `just gamma` |\n"
    "| f | a tool | 1 | | `just gamma` |\n"
)
NOT_A_LIBRARY = "| n | none: not a library | 1 | | `just beta` |\n"
DOES_MORE = {
    "exits 1": 'beta:\n    @echo "bind: documentation coverage of the public surface"; exit 1\n',
    "has a dependency": 'beta: delta\n    @echo "none: not a library"\n',
    "has two lines": 'beta:\n    @echo "none: not a library"\n    ./scripts/doc-coverage\n',
}


@pytest.mark.parametrize("recipe", list(DOES_MORE))
def test_a_gate_whose_rows_wait_still_gets_a_runner_when_its_recipe_does_more_than_say_so(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    *,
    recipe: str,
) -> None:
    recipes = f"{WAITING_RECIPES}\n{DOES_MORE[recipe]}"
    root = project(tmp_path / "repo", '"bite rest"', WAITING + NOT_A_LIBRARY, recipes=recipes)
    output = tmp_path / "output"
    assert ci_jobs.main(["ci_jobs.py", "--root", str(root), "--output", str(output)]) == 0
    said = capsys.readouterr().out
    assert "`delta` (later: code:outbound-call)" in said
    assert "`beta`" not in said
    assert run_job(root, "rest", monkeypatch, "beta") == (1, ["alpha", "beta", "gamma"])
    assert "parallel: beta: red (exit 1)" in capsys.readouterr().out


def test_rest_keeps_the_binding_s_apart_and_alone_lines(tmp_path: Path) -> None:
    speed = "- Shards: 1\n- Apart: `a b`\n- Alone: `c`\n"
    root = project(tmp_path / "repo", '"bite rest"', "", recipes="", speed=speed)
    lists = 'tier1_gates := "bite a b c d e f"\ntier1_sharded := ""\ntier1_jobs := "bite rest"\n'
    (root / JUSTFILE).write_text(lists, encoding="utf-8")
    log = tmp_path / "log"
    runner = shlex.join([sys.executable, "-c", parallel_tests.PAIRED, str(log)])
    run = ["ci_jobs.py", "--root", str(root), "--run", "rest", "--runner", runner, "--jobs", "6"]
    assert ci_jobs.main(run) == 0
    parallel_tests.never_beside(parallel_tests.ran_at_once(log), ["ab"], "c")


def test_the_shape_job_starts_no_runner_for_a_gate_its_rows_wait_on_and_names_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = project(tmp_path / "repo", '"bite alpha delta rest"', WAITING)
    output = tmp_path / "output"
    assert ci_jobs.main(["ci_jobs.py", "--root", str(root), "--output", str(output)]) == 0
    outputs = dict(line.split("=", 1) for line in output.read_text(encoding="utf-8").splitlines())
    assert outputs == {
        "count": "2",
        "list": "[1,2]",
        "tier1": '["bite","rest"]',
        "tier1_long": '["alpha"]',
    }
    assert capsys.readouterr().out == (
        "ci_jobs: no runner for the gates whose every row of the binding's Gates table says"
        " none: or later: and whose recipe only says so, so each is green by its row: `delta`"
        " (later: code:outbound-call)\n"
    )


def test_a_gate_in_the_sharded_list_runs_alone_in_the_long_job_whatever_its_shard_count(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = project(tmp_path / "repo", '"bite rest"', "", recipes="", speed="- Shards: 1\n")
    found = ci_jobs.lists(read(root, JUSTFILE))
    assert isinstance(found, ci_jobs.Lists)
    known = ci_jobs.facts(root)
    [grouped] = ci_jobs.problems(found, known)
    assert (grouped.kind, grouped.gate) == (ci_jobs.Kind.GROUPED, "alpha")
    words = ci_jobs.default(found.gates, found.sharded, known)
    assert words == ("bite", "alpha", "rest")
    project(tmp_path / "own", f'"{" ".join(words)}"', "", recipes="", speed="- Shards: 1\n")
    own = ci_jobs.lists(read(tmp_path / "own", JUSTFILE))
    assert isinstance(own, ci_jobs.Lists)
    assert ci_jobs.problems(own, ci_jobs.facts(tmp_path / "own")) == []
    assert jobs(tmp_path / "own")["rest"] == ("beta", "gamma", "delta")
    output = tmp_path / "output"
    assert (
        ci_jobs.main(["ci_jobs.py", "--root", str(tmp_path / "own"), "--output", str(output)]) == 0
    )
    capsys.readouterr()
    outputs = dict(line.split("=", 1) for line in output.read_text(encoding="utf-8").splitlines())
    assert (outputs["tier1"], outputs["tier1_long"], outputs["list"]) == (
        '["bite","rest"]',
        '["alpha"]',
        "[1]",
    )


def test_the_shape_job_refuses_a_job_list_that_drops_a_gate(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = project(tmp_path / "repo", '"bite alpha"', WAITING)
    output = tmp_path / "output"
    assert ci_jobs.main(["ci_jobs.py", "--root", str(root), "--output", str(output)]) == 1
    assert not output.exists()
    said = capsys.readouterr().out.splitlines()
    assert [line.split("`")[1] for line in said] == ["beta", "gamma", "delta"]


def run_job(
    root: Path, name: str, monkeypatch: pytest.MonkeyPatch, *reds: str
) -> tuple[int, list[str]]:
    recorder = root.parent / "recorder.py"
    recorder.write_text(RECORDER, encoding="utf-8")
    recorded = root.parent / f"recorded-{name}"
    monkeypatch.setenv("RECORDED", str(recorded))
    monkeypatch.setenv("REDS", " ".join(reds))
    runner = f"{sys.executable} {recorder}"
    code = ci_jobs.main(["ci_jobs.py", "--root", str(root), "--run", name, "--runner", runner])
    ran = recorded.read_text(encoding="utf-8").split() if recorded.exists() else []
    return code, sorted(ran)


def test_a_group_runs_each_of_its_gates_through_the_parallel_runner_and_names_the_red(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = project(tmp_path / "repo", '"bite pair=alpha,beta rest"', WAITING)
    assert run_job(root, "pair", monkeypatch, "beta") == (1, ["alpha", "beta"])
    assert "parallel: beta: red (exit 1)" in capsys.readouterr().out
    assert run_job(root, "rest", monkeypatch) == (0, ["gamma"])
    assert "`delta` (later: code:outbound-call)" in capsys.readouterr().out
    assert run_job(root, "bite", monkeypatch, "bite") == (1, ["bite"])
    assert run_job(root, "nothing", monkeypatch) == (2, [])
    assert capsys.readouterr().out.endswith("justfile: `tier1_jobs` names no job `nothing`\n")


def test_alone_job_preserves_shard_env_when_running_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = project(tmp_path / "repo", '"bite rest"', WAITING)
    seen = tmp_path / "shard_env.txt"
    runner = tmp_path / "env_runner.py"
    runner.write_text(
        "import os, pathlib, sys\n"
        f"pathlib.Path({str(seen)!r}).write_text(\n"
        "    f\"{sys.argv[1]}:{os.environ.get('SHARD')}/{os.environ.get('SHARDS')}\",\n"
        "    encoding='utf-8',\n"
        ")\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("SHARD", "2")
    monkeypatch.setenv("SHARDS", "4")
    assert (
        ci_jobs.main(
            [
                "ci_jobs.py",
                "--root",
                str(root),
                "--run",
                "bite",
                "--runner",
                f"{sys.executable} {runner}",
            ]
        )
        == 0
    )
    assert seen.read_text(encoding="utf-8") == "bite:2/4"


SLEEPER = textwrap.dedent(
    """\
    import json, os, sys, time
    time.sleep(json.loads(os.environ["SLEEPS"]).get(sys.argv[1], 0))
    """
)
RECORD = ".ratchet/timing.jsonl"


def grouped_project(root: Path, thresholds: str) -> Path:
    project(root, '"bite rest"', "", recipes="", speed="- Shards: 1\n")
    lists = (
        'tier1_gates := "bite alpha beta gamma"\ntier1_sharded := ""\ntier1_jobs := "bite rest"\n'
    )
    (root / JUSTFILE).write_text(lists, encoding="utf-8")
    (root / "quality").mkdir()
    (root / THRESHOLDS).write_text(thresholds, encoding="utf-8")
    return root


def judged_in_ci(
    root: Path,
    sleeps: dict[str, float],
    *,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    command: list[str] | None = None,
) -> tuple[int, list[str]]:
    sleeper = root.parent / "sleeper.py"
    sleeper.write_text(SLEEPER, encoding="utf-8")
    monkeypatch.setenv("SLEEPS", json.dumps(sleeps))
    job = [
        *(sys.executable, str(PLUGIN_ROOT / "scripts/ci_jobs.py"), "--root", str(root)),
        *("--run", "rest", "--runner", f"{sys.executable} {sleeper}", "--jobs", "1"),
        *("--report", RECORD),
    ]
    argv = ["budget.py", "rest", "--root", str(root), "--tier", "tier1", "--report", RECORD]
    code = budget.main([*argv, "--", *(job if command is None else command)])
    said = capsys.readouterr().out.splitlines()
    return code, [line for line in said if line.startswith("ratchet-budget: ")]


def judged_gate(lines: list[str], gate: str) -> str:
    [line] = [line for line in lines if line.startswith(f"ratchet-budget: {gate} ")]
    return line


TIER_CEILING = "[ceilings.seconds]\ntier1 = 1.5\n"


def test_a_group_whose_gates_each_finish_under_the_tier_ceiling_is_green_however_long_it_took(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = grouped_project(tmp_path / "repo", "[ceilings.seconds]\ntier1 = 5.0\n")
    sleeps = {"alpha": 1.8, "beta": 1.8, "gamma": 1.8}
    code, lines = judged_in_ci(root, sleeps, monkeypatch=monkeypatch, capsys=capsys)
    assert code == 0, lines
    for gate in sleeps:
        assert re.fullmatch(
            rf"ratchet-budget: {gate} took \d+\.\ds \(ceiling 5s of tier1, \d+% used\): green",
            judged_gate(lines, gate),
        )
    assert re.fullmatch(
        r"ratchet-budget: rest took \d+\.\ds \(no ceiling recorded\): unbounded",
        judged_gate(lines, "rest"),
    )
    assert float(judged_gate(lines, "rest").split(" took ")[1].split("s")[0]) > 5.0


def test_a_gate_over_the_tier_ceiling_inside_a_group_is_red_and_its_line_names_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = grouped_project(tmp_path / "repo", "[ceilings.seconds]\ntier1 = 4.0\n")
    code, lines = judged_in_ci(root, {"beta": 4.1}, monkeypatch=monkeypatch, capsys=capsys)
    assert code == 1
    assert judged_gate(lines, "beta").endswith("(ceiling 4s of tier1): red")
    assert judged_gate(lines, "alpha").endswith(": green")
    assert judged_gate(lines, "gamma").endswith(": green")


def test_a_gate_with_its_own_key_below_the_tier_s_is_held_to_it_inside_a_group(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = grouped_project(tmp_path / "repo", "[ceilings.seconds]\ntier1 = 900\nalpha = 0.5\n")
    code, lines = judged_in_ci(root, {"alpha": 0.6}, monkeypatch=monkeypatch, capsys=capsys)
    assert code == 1
    assert judged_gate(lines, "alpha").endswith("(ceiling 0.5s): red")
    assert "(ceiling 900s of tier1" in judged_gate(lines, "beta")


def test_a_group_with_a_key_of_its_own_under_its_job_name_is_held_to_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = grouped_project(tmp_path / "repo", "[ceilings.seconds]\ntier1 = 900\nrest = 0.5\n")
    sleeps = {"alpha": 0.3, "beta": 0.3}
    code, lines = judged_in_ci(root, sleeps, monkeypatch=monkeypatch, capsys=capsys)
    assert code == 1
    assert judged_gate(lines, "rest").endswith("(ceiling 0.5s): red")
    assert all(judged_gate(lines, gate).endswith(": green") for gate in sleeps)


def test_an_earlier_run_s_lines_in_the_record_are_not_read_as_this_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = grouped_project(tmp_path / "repo", "[ceilings.seconds]\ntier1 = 10.0\nalpha = 0.5\n")
    code, lines = judged_in_ci(root, {"alpha": 0.6}, monkeypatch=monkeypatch, capsys=capsys)
    assert (code, judged_gate(lines, "alpha").endswith(": red")) == (1, True)
    nothing = [sys.executable, "-c", "pass"]
    code, lines = judged_in_ci(root, {}, monkeypatch=monkeypatch, capsys=capsys, command=nothing)
    assert code == 0, lines
    assert [line.split(" took ")[0] for line in lines] == ["ratchet-budget: rest"]
    assert re.search(r"\(ceiling 10s of tier1, \d+% used\): green$", lines[0])


GROUP_ONLY = textwrap.dedent(
    """\
    import json, sys
    lines = [
        {"key": "beta", "seconds": "slow", "exit": 0},
        {"key": "gamma", "seconds": 0.2, "exit": 0},
        {"kind": "group", "job": "rest", "gates": ["alpha", "beta", "gamma"]},
    ]
    with open(sys.argv[1], "a", encoding="utf-8") as out:
        out.writelines(json.dumps(line) + "\\n" for line in lines)
    """
)


def test_a_gate_whose_time_this_run_s_record_does_not_hold_is_named_and_not_judged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = grouped_project(tmp_path / "repo", TIER_CEILING)
    writer = tmp_path / "group_only.py"
    writer.write_text(GROUP_ONLY, encoding="utf-8")
    command = [sys.executable, str(writer), str(root / RECORD)]
    (root / RECORD).parent.mkdir()
    code, lines = judged_in_ci(root, {}, monkeypatch=monkeypatch, capsys=capsys, command=command)
    assert code == 0, lines
    for gate in ("alpha", "beta"):
        assert judged_gate(lines, gate) == (
            f"ratchet-budget: {gate} ran in rest, and this run's record holds no time of it:"
            " not judged"
        )
    assert judged_gate(lines, "gamma").endswith("(ceiling 1.5s of tier1, 13% used): green")


@pytest.mark.parametrize("kind", ["directory", "read-only", "symlink"])
def test_a_group_whose_record_cannot_be_written_is_not_judged_for_each_gate_and_not_held_to_tier(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    *,
    kind: str,
) -> None:
    if kind == "read-only" and os.geteuid() == 0:
        pytest.skip("root writes through modes")
    root = grouped_project(tmp_path / "repo", "[ceilings.seconds]\ntier1 = 0.4\n")
    record = root / RECORD
    if kind == "directory":
        record.mkdir(parents=True)
    elif kind == "read-only":
        record.parent.mkdir(parents=True)
        record.write_text("", encoding="utf-8")
        record.chmod(0o444)
    else:
        (root / "elsewhere").mkdir()
        record.parent.symlink_to(root / "elsewhere")
    try:
        sleeps = {"alpha": 0.2, "beta": 0.2, "gamma": 0.2}
        code, lines = judged_in_ci(root, sleeps, monkeypatch=monkeypatch, capsys=capsys)
    finally:
        if kind == "read-only":
            record.chmod(0o644)
    assert code == 0, lines
    assert judged_gate(lines, "rest").endswith("(no ceiling recorded): unbounded")
    assert [line.split()[1] for line in lines] == ["rest", "alpha", "beta", "gamma"]
    for gate in ("alpha", "beta", "gamma"):
        assert judged_gate(lines, gate) == (
            f"ratchet-budget: {gate} ran in rest, and this run's record holds no time of it:"
            " not judged"
        )


def test_not_judged_emits_warning_annotations_alongside_gate_lines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = grouped_project(tmp_path / "repo", "[ceilings.seconds]\ntier1 = 5.0\n")
    (root / RECORD).mkdir(parents=True)
    sleeper = tmp_path / "sleeper.py"
    sleeper.write_text(SLEEPER, encoding="utf-8")
    monkeypatch.setenv("SLEEPS", json.dumps({"alpha": 0.0, "beta": 0.0, "gamma": 0.0}))
    job = [
        *(sys.executable, str(PLUGIN_ROOT / "scripts/ci_jobs.py"), "--root", str(root)),
        *("--run", "rest", "--runner", f"{sys.executable} {sleeper}", "--jobs", "1"),
        *("--report", RECORD),
    ]
    argv = ["budget.py", "rest", "--root", str(root), "--tier", "tier1", "--report", RECORD]
    assert budget.main([*argv, "--", *job]) == 0
    out = capsys.readouterr().out
    for gate in ("alpha", "beta", "gamma"):
        assert (
            f"::warning title=ratchet-budget::ratchet-budget: {gate} ran in rest, and this run's"
            " record holds no time of it: not judged"
        ) in out


TRUNCATING_RUNNER = textwrap.dedent(
    """\
    import json, os, pathlib, sys, time
    time.sleep(json.loads(os.environ["SLEEPS"]).get(sys.argv[1], 0))
    if sys.argv[1] == "beta":
        pathlib.Path(os.environ["RECORD_PATH"]).write_text("", encoding="utf-8")
    """
)


def test_a_group_whose_record_is_truncated_mid_run_is_not_held_to_tier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = grouped_project(tmp_path / "repo", "[ceilings.seconds]\ntier1 = 10.0\n")
    record = root / RECORD
    record.parent.mkdir(parents=True)
    record.write_text('{"key": "old", "seconds": 0.1, "exit": 0}\n' * 10, encoding="utf-8")
    runner = tmp_path / "truncater.py"
    runner.write_text(TRUNCATING_RUNNER, encoding="utf-8")
    sleeps = {"alpha": 0.2, "beta": 0.0, "gamma": 0.0}
    monkeypatch.setenv("RECORD_PATH", str(record))
    job = [
        *(sys.executable, str(PLUGIN_ROOT / "scripts/ci_jobs.py"), "--root", str(root)),
        *("--run", "rest", "--runner", f"{sys.executable} {runner}", "--jobs", "1"),
        *("--report", RECORD),
    ]
    code, lines = judged_in_ci(root, sleeps, monkeypatch=monkeypatch, capsys=capsys, command=job)
    assert code == 0, lines
    assert judged_gate(lines, "rest").endswith("(no ceiling recorded): unbounded")
    assert [line.split()[1] for line in lines] == ["rest", "alpha", "beta", "gamma"]
    assert judged_gate(lines, "alpha").endswith("holds no time of it: not judged")
    assert judged_gate(lines, "beta").endswith(": green")
    assert judged_gate(lines, "gamma").endswith(": green")


INTERRUPTING_RUNNER = textwrap.dedent(
    """\
    import json, os, sys, time
    time.sleep(json.loads(os.environ["SLEEPS"]).get(sys.argv[1], 0))
    if sys.argv[1] == "beta":
        os.kill(os.getppid(), int(os.environ["STOP_SIGNAL"]))
    """
)


@pytest.mark.parametrize("sig", [signal.SIGKILL, signal.SIGTERM])
def test_an_interrupted_group_run_judges_finished_gates_and_leaves_rest_unjudged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    *,
    sig: signal.Signals,
) -> None:
    for key in list(os.environ):
        if key.startswith(("COV_CORE_", "COVERAGE_")):
            monkeypatch.delenv(key, raising=False)
    root = grouped_project(tmp_path / "repo", "[ceilings.seconds]\ntier1 = 10.0\n")
    runner = tmp_path / "interrupter.py"
    runner.write_text(INTERRUPTING_RUNNER, encoding="utf-8")
    monkeypatch.setenv("STOP_SIGNAL", str(int(sig)))
    sleeps = {"alpha": 0.0, "beta": 0.0}
    job = [
        *(sys.executable, str(PLUGIN_ROOT / "scripts/ci_jobs.py"), "--root", str(root)),
        *("--run", "rest", "--runner", f"{sys.executable} {runner}", "--jobs", "1"),
        *("--report", RECORD),
    ]
    code, lines = judged_in_ci(root, sleeps, monkeypatch=monkeypatch, capsys=capsys, command=job)
    assert code != 0
    assert judged_gate(lines, "rest").endswith("(no ceiling recorded): failed")
    assert [line.split()[1] for line in lines] == ["rest", "alpha", "beta", "gamma"]
    assert judged_gate(lines, "alpha").endswith(": green")
    assert judged_gate(lines, "beta").endswith("holds no time of it: not judged")
    assert judged_gate(lines, "gamma").endswith("holds no time of it: not judged")


SPOOFING_RUNNER = textwrap.dedent(
    """\
    import json, os, pathlib, subprocess, sys, time
    gate = sys.argv[1]
    assert "RATCHET_BUDGET_GROUP" not in os.environ
    time.sleep(json.loads(os.environ["SLEEPS"]).get(gate, 0))
    if gate == "beta":
        record = pathlib.Path(os.environ["RECORD_PATH"])
        parallel_py = os.environ["PARALLEL_PY"]
        fast = f"{sys.executable} -c pass"
        cmd = [sys.executable, parallel_py, "--runner", fast, "--report", str(record)]
        subprocess.run([*cmd, "--", "alpha"], check=True)
        lines = [json.loads(line) for line in record.read_text(encoding="utf-8").splitlines()]
        first = next(line for line in lines if line.get("key") == "alpha")
        stamp = {key: first[key] for key in ("at", "run", "tree") if key in first}
        fakes = [
            {"key": "alpha", "seconds": 0.01, "exit": 0},
            {"key": "alpha", "seconds": 0.01, "ceiling": None, "status": "green", "exit": 0}
            | stamp,
            {"key": "gamma", "seconds": 12.0, "exit": 0},
            {"key": "gamma", "seconds": 12.0, "ceiling": None, "status": "unbounded", "exit": 0}
            | stamp,
        ]
        with record.open("a", encoding="utf-8") as handle:
            handle.writelines(json.dumps(fake) + "\\n" for fake in fakes)
    """
)


def test_a_sibling_gate_cannot_spoof_or_overwrite_another_gate_s_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = grouped_project(tmp_path / "repo", "[ceilings.seconds]\ntier1 = 10.0\nalpha = 0.5\n")
    runner = tmp_path / "spoofer.py"
    runner.write_text(SPOOFING_RUNNER, encoding="utf-8")
    sleeps = {"alpha": 0.6, "beta": 0.0, "gamma": 0.0}
    monkeypatch.setenv("RECORD_PATH", str(root / RECORD))
    monkeypatch.setenv("PARALLEL_PY", str(PLUGIN_ROOT / "scripts/parallel.py"))
    job = [
        *(sys.executable, str(PLUGIN_ROOT / "scripts/ci_jobs.py"), "--root", str(root)),
        *("--run", "rest", "--runner", f"{sys.executable} {runner}", "--jobs", "1"),
        *("--report", RECORD),
    ]
    code, lines = judged_in_ci(root, sleeps, monkeypatch=monkeypatch, capsys=capsys, command=job)
    assert code == 1, lines
    assert judged_gate(lines, "alpha").endswith("(ceiling 0.5s): red")
    assert judged_gate(lines, "beta").endswith(": green")
    assert judged_gate(lines, "gamma").endswith(": green")
    assert judged_gate(lines, "rest").endswith("(no ceiling recorded): unbounded")


def test_a_sibling_gate_cannot_raise_a_fast_gate_above_the_tier_ceiling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = grouped_project(tmp_path / "repo", "[ceilings.seconds]\ntier1 = 5.0\n")
    runner = tmp_path / "raiser.py"
    sleeps = {"alpha": 0.0, "beta": 0.0, "gamma": 0.0}
    monkeypatch.setenv("RECORD_PATH", str(root / RECORD))
    raiser = textwrap.dedent(
        """\
        import json, os, pathlib, sys
        gate = sys.argv[1]
        assert "RATCHET_BUDGET_GROUP" not in os.environ
        if gate == "beta":
            record = pathlib.Path(os.environ["RECORD_PATH"])
            lines = [json.loads(line) for line in record.read_text(encoding="utf-8").splitlines()]
            first = next(line for line in lines if line.get("key") == "alpha")
            stamp = {key: first[key] for key in ("at", "run", "tree") if key in first}
            fakes = [
                {"key": "alpha", "seconds": 10.0, "exit": 0},
                {"key": "alpha", "seconds": 10.0, "ceiling": None, "status": "unbounded", "exit": 0}
                | stamp,
                {"key": "gamma", "seconds": 10.0, "exit": 0},
                {"key": "gamma", "seconds": 10.0, "ceiling": None, "status": "unbounded", "exit": 0}
                | stamp,
            ]
            with record.open("a", encoding="utf-8") as handle:
                handle.writelines(json.dumps(fake) + "\\n" for fake in fakes)
        """
    )
    runner.write_text(raiser, encoding="utf-8")
    job = [
        *(sys.executable, str(PLUGIN_ROOT / "scripts/ci_jobs.py"), "--root", str(root)),
        *("--run", "rest", "--runner", f"{sys.executable} {runner}", "--jobs", "1"),
        *("--report", RECORD),
    ]
    code, lines = judged_in_ci(root, sleeps, monkeypatch=monkeypatch, capsys=capsys, command=job)
    assert code == 0, lines
    for gate in ("alpha", "beta", "gamma"):
        assert judged_gate(lines, gate).endswith(": green")


def test_direct_ci_jobs_report_and_alone_and_direct_parallel_strip_budget_env(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = grouped_project(tmp_path / "repo", "[ceilings.seconds]\ntier1 = 60.0\n")
    env_checker = tmp_path / "check_env.py"
    env_checker.write_text(
        "import os; assert 'RATCHET_BUDGET_GROUP' not in os.environ\n",
        encoding="utf-8",
    )
    assert (
        ci_jobs.main(
            [
                "ci_jobs.py",
                "--root",
                str(root),
                "--run",
                "rest",
                "--runner",
                f"{sys.executable} {env_checker}",
                "--report",
                RECORD,
            ]
        )
        == 0
    )
    assert (
        budget.main(
            [
                "budget.py",
                "bite",
                "--root",
                str(root),
                "--tier",
                "tier1",
                "--",
                sys.executable,
                str(PLUGIN_ROOT / "scripts/ci_jobs.py"),
                "--root",
                str(root),
                "--run",
                "bite",
                "--runner",
                f"{sys.executable} {env_checker}",
            ]
        )
        == 0
    )
    assert (
        budget.main(
            [
                "budget.py",
                "direct",
                "--root",
                str(root),
                "--tier",
                "tier1",
                "--",
                sys.executable,
                str(PLUGIN_ROOT / "scripts/parallel.py"),
                "--root",
                str(root),
                "--runner",
                f"{sys.executable} {env_checker}",
                "--",
                "alpha",
            ]
        )
        == 0
    )
    wrong_job = [
        sys.executable,
        "-c",
        (
            "import json, os, pathlib;"
            " pathlib.Path(os.environ['RATCHET_BUDGET_GROUP']).write_text("
            "json.dumps({'kind': 'group', 'job': 'other', 'gates': ['alpha']}) + '\\n',"
            " encoding='utf-8')"
        ),
    ]
    assert (
        budget.main(["budget.py", "rest", "--root", str(root), "--tier", "tier1", "--", *wrong_job])
        == 0
    )
    capsys.readouterr()


ORDERED_GROUP = textwrap.dedent(
    """\
    import json, sys
    lines = [
        {"key": "alpha", "seconds": 0.1, "exit": 0},
        {"key": "beta", "seconds": "slow", "exit": 0},
        {"key": "gamma", "seconds": 0.2, "exit": 0},
        {"kind": "group", "job": "rest", "gates": ["alpha", "beta", "gamma"]},
    ]
    with open(sys.argv[1], "a", encoding="utf-8") as out:
        out.writelines(json.dumps(line) + "\\n" for line in lines)
    """
)


def test_not_judged_and_judged_lines_print_in_the_job_s_gate_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = grouped_project(tmp_path / "repo", "[ceilings.seconds]\ntier1 = 10.0\n")
    writer = tmp_path / "ordered_group.py"
    writer.write_text(ORDERED_GROUP, encoding="utf-8")
    command = [sys.executable, str(writer), str(root / RECORD)]
    (root / RECORD).parent.mkdir()
    code, lines = judged_in_ci(root, {}, monkeypatch=monkeypatch, capsys=capsys, command=command)
    assert code == 0, lines
    assert [line.split()[1] for line in lines] == ["rest", "alpha", "beta", "gamma"]
    assert judged_gate(lines, "beta").endswith("holds no time of it: not judged")


def problems_of(words: str) -> list[tuple[ci_jobs.Kind, str]]:
    found = ci_jobs.Lists(("bite", "alpha", "beta"), frozenset(), tuple(words.split()))
    return [(one.kind, one.gate) for one in ci_jobs.problems(found, ci_jobs.Facts(frozenset()))]


@pytest.mark.parametrize(
    ("words", "expected"),
    [
        ("bite rest rest", [(ci_jobs.Kind.NAME, "rest")]),
        ("bite alpha=alpha,beta", [(ci_jobs.Kind.NAME, "alpha")]),
        ("bite rest=alpha rest", [(ci_jobs.Kind.NAME, "rest"), (ci_jobs.Kind.NAME, "rest")]),
        ("bite pair=alpha,beta", []),
    ],
)
def test_a_job_list_that_names_a_job_twice_or_after_a_gate_is_refused(
    words: str, expected: list[tuple[ci_jobs.Kind, str]]
) -> None:
    assert problems_of(words) == expected


@pytest.mark.parametrize("word", ["pair=alpha,omega", "pair=", "omega", "=alpha"])
def test_a_job_list_with_a_word_that_is_no_gate_rest_or_group_of_gates_is_the_project_s_own(
    word: str,
) -> None:
    justfile = (
        f'tier1_gates := "bite alpha"\ntier1_sharded := ""\ntier1_jobs := "bite {word} rest"\n'
    )
    assert ci_jobs.lists(justfile) == ci_jobs.Own(
        f"`tier1_jobs` holds `{word}`, which is no gate of `tier1_gates`, `rest` or"
        " `NAME=gate,gate`"
    )


@pytest.mark.parametrize(
    ("values", "reason"),
    [
        (("base", '""', '"rest"'), "`tier1_gates` is not a value read exactly"),
        (('"a"', "x", '"rest"'), "`tier1_sharded` is not a value read exactly"),
        (('"a"', '""', '"a" + " rest"'), "`tier1_jobs` is not one plain string"),
    ],
)
def test_a_list_the_reader_cannot_read_exactly_makes_the_job_list_the_project_s_own(
    values: tuple[str, str, str], reason: str
) -> None:
    names = ("tier1_gates", "tier1_sharded", "tier1_jobs")
    justfile = "".join(f"{name} := {value}\n" for name, value in zip(names, values, strict=True))
    assert ci_jobs.lists(justfile) == ci_jobs.Own(reason)


@pytest.mark.parametrize(
    ("words", "gate", "expected"),
    [
        (["bite", "pair=alpha,beta", "rest"], "beta", ["bite", "pair=alpha", "beta", "rest"]),
        (["bite", "pair=alpha", "rest"], "alpha", ["bite", "alpha", "rest"]),
        (["bite", "alpha", "pair=alpha,beta"], "alpha", ["bite", "alpha", "pair=beta"]),
        (["bite", "pair=alpha,beta"], "beta", ["bite", "pair=alpha", "beta"]),
    ],
)
def test_a_gate_given_a_job_of_its_own_leaves_every_group_and_stands_before_rest(
    words: list[str], gate: str, expected: list[str]
) -> None:
    assert ci_jobs.given_own(gate)(words) == expected


def test_the_shape_job_and_a_run_refuse_a_justfile_without_the_lists(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / JUSTFILE).write_text('tier1_gates := "bite"\n', encoding="utf-8")
    output = tmp_path / "output"
    assert ci_jobs.main(["ci_jobs.py", "--root", str(tmp_path), "--output", str(output)]) == 1
    assert ci_jobs.main(["ci_jobs.py", "--root", str(tmp_path), "--run", "bite"]) == 2
    assert capsys.readouterr().out == (
        "justfile: no `tier1_gates`, `tier1_sharded` and `tier1_jobs` to read the jobs from\n"
        "justfile: `tier1_jobs` names no job `bite`\n"
    )


def test_a_job_whose_every_gate_waits_on_its_rows_runs_nothing_and_is_green(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rows = f"{WAITING}| g | a tool | 1 | | a command of its own |\n"
    root = project(tmp_path / "repo", '"bite alpha delta rest"', rows)
    assert run_job(root, "delta", monkeypatch) == (0, [])
    assert "`delta` (later: code:outbound-call)" in capsys.readouterr().out


def test_a_group_keeps_the_schedule_the_recipes_give_its_gates(tmp_path: Path) -> None:
    justfile = (
        "tier1:\n    python3 scripts/parallel.py --serial alpha beta --serial beta gamma"
        " --serial delta omega --alone delta --wide gamma -- alpha beta gamma delta omega\n"
    )
    options = argparse.Namespace(runner="just", root=tmp_path, jobs=None, report=None)
    assert ci_jobs.flags(justfile, ("alpha", "beta", "gamma", "delta"), options) == [
        "--runner",
        "just",
        "--root",
        str(tmp_path),
        "--partial",
        "--serial",
        "alpha",
        "beta",
        "gamma",
        "--alone",
        "delta",
        "--wide",
        "gamma",
    ]


def test_a_grouped_ci_job_runs_fast_gates_first_and_skips_heavy_gates_on_fast_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = project(tmp_path / "repo", '"bite rest"', "", recipes="", speed="- Shards: 1\n")
    lists = (
        'tier1_gates := "bite build artefact-scan change-size conformance"\n'
        'tier1_sharded := ""\n'
        'tier1_jobs := "bite rest"\n'
    )
    (root / JUSTFILE).write_text(lists, encoding="utf-8")
    gates = ("build", "artefact-scan", "change-size", "conformance")
    options = argparse.Namespace(runner="just", root=root, jobs=None, report=None)
    assert ci_jobs.flags(lists, gates, options) == [
        "--runner",
        "just",
        "--root",
        str(root),
        "--partial",
        "--first",
        "change-size",
        "conformance",
    ]
    code, ran = run_job(root, "rest", monkeypatch, "change-size")
    assert code == 1
    assert ran == ["change-size", "conformance"]
    out = capsys.readouterr().out
    assert "parallel: stopped before heavy gates after fast failure:" in out
    assert "artefact-scan" in out
    assert "build" in out


@pytest.mark.parametrize(
    "gates",
    [
        ("change-size", "conformance"),
        ("build", "artefact-scan"),
    ],
    ids=["all-fast", "no-fast"],
)
def test_a_grouped_ci_job_omits_first_when_all_or_no_gates_are_fast(
    tmp_path: Path, gates: tuple[str, ...]
) -> None:
    options = argparse.Namespace(runner="just", root=tmp_path, jobs=None, report=None)
    assert "--first" not in ci_jobs.flags("", gates, options)


def test_fast_first_excludes_build_consumers_alone_and_serial_with_heavy_gates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    speed = (
        "- Shards: 1\n"
        "- Build: gate `build`; caches `.cache`; cold 30s; warm 2s; reuse moved;"
        " consumers `conformance`, `artefact-scan`\n"
        "- Alone: `incidents`\n"
        "- Apart: `triggers build` `guard-diff settings-check` `change-size outside`\n"
    )
    root = project(tmp_path / "repo", '"bite rest"', "", recipes="", speed=speed)
    lists = (
        'tier1_gates := "bite build artefact-scan change-size conformance'
        ' settings-check incidents guard-diff triggers"\n'
        'tier1_sharded := ""\n'
        'tier1_jobs := "bite rest"\n'
    )
    (root / JUSTFILE).write_text(lists, encoding="utf-8")
    gates = (
        "build",
        "artefact-scan",
        "change-size",
        "conformance",
        "settings-check",
        "incidents",
        "guard-diff",
        "triggers",
    )
    options = argparse.Namespace(runner="just", root=root, jobs=None, report=None)
    assert ci_jobs.flags(lists, gates, options) == [
        "--runner",
        "just",
        "--root",
        str(root),
        "--partial",
        "--first",
        "change-size",
        "settings-check",
        "guard-diff",
    ]
    assert run_job(root, "rest", monkeypatch) == (0, sorted(gates))


def test_fast_first_excludes_recipe_alone_and_transitive_serial_groups(tmp_path: Path) -> None:
    justfile = (
        "tier1:\n"
        "    python3 scripts/parallel.py"
        " --serial change-size conformance"
        " --serial guard-diff build"
        " --alone conformance"
        " -- change-size conformance guard-diff incidents build\n"
    )
    options = argparse.Namespace(runner="just", root=tmp_path, jobs=None, report=None)
    gates = ("change-size", "conformance", "guard-diff", "incidents", "build")
    got = ci_jobs.flags(justfile, gates, options)
    assert got[got.index("--first") :] == ["--first", "incidents"]


def test_a_grouped_ci_job_runs_parallel_as_partial_and_skips_local_caches(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    speed = "- Shards: 1\n- Local caches: `~/.ratchet-nothing-here/x`\n"
    root = project(tmp_path / "repo", '"bite rest"', "", recipes="", speed=speed)
    runner = shlex.join([sys.executable, "-c", "pass"])
    argv = [
        "ci_jobs.py",
        "--root",
        str(root),
        "--run",
        "rest",
        "--runner",
        runner,
        "--report",
        RECORD,
    ]
    assert ci_jobs.main(argv) == 0
    assert "Local caches bullet names" not in capsys.readouterr().out
    rows = [json.loads(line) for line in (root / RECORD).read_text(encoding="utf-8").splitlines()]
    assert not [row for row in rows if row.get("kind") == "cache-missing"]
    assert any(row.get("kind") == "tier" for row in rows)
    assert any(row.get("kind") == "group" for row in rows)


@pytest.mark.parametrize(
    "thresholds", ["[ceilings\n", "ceilings = 3\n", "[ceilings]\nseconds = 3\n"]
)
def test_thresholds_that_hold_no_seconds_table_give_no_gate_a_ceiling(
    tmp_path: Path, thresholds: str
) -> None:
    (tmp_path / "quality").mkdir()
    (tmp_path / THRESHOLDS).write_text(thresholds, encoding="utf-8")
    assert ci_jobs.facts(tmp_path).ceilings == frozenset()


RELEASES = [
    pytest.param(version, marks=pytest.mark.shard_slot(slot))
    for version, slot in (("0.13.3", 3), ("0.13.4", 5), ("0.13.5", 7))
]
HAND_EDIT = "          for gate in $(just --evaluate tier1_gates); do"
JOB = re.compile(r"^  ([A-Za-z_][\w-]*):\s*$")
KEY = re.compile(r"^    ([A-Za-z_][\w-]*):")
OUTPUT = re.compile(r"^      ([A-Za-z_][\w-]*):")
MATRIX_KEY = re.compile(r"^        ([A-Za-z_][\w-]*):")
OUTPUT_READ = re.compile(r"needs\.([\w-]+)\.outputs\.([\w-]+)")
MATRIX_READ = re.compile(r"matrix\.([\w-]+)")


def keys_under(body: list[str], head: str, key: re.Pattern[str]) -> set[str]:
    if head not in body:
        return set()
    found: set[str] = set()
    depth = len(head) - len(head.lstrip())
    for line in body[body.index(head) + 1 :]:
        if line.strip() and len(line) - len(line.lstrip()) <= depth:
            break
        found |= {match[1] for match in [key.match(line)] if match}
    return found


def workflow_faults(text: str) -> list[str]:
    lines = text.replace("\r\n", "\n").split("\n")
    bodies: dict[str, list[str]] = {}
    for line in lines[lines.index("jobs:") + 1 :]:
        if head := JOB.match(line):
            bodies[head[1]] = []
        elif bodies:
            bodies[list(bodies)[-1]].append(line)
    outputs = {name: keys_under(body, "    outputs:", OUTPUT) for name, body in bodies.items()}
    faults = []
    for name, body in bodies.items():
        keys = Counter(match[1] for line in body if (match := KEY.match(line)))
        faults += [f"{name}: the key {key} {n} times" for key, n in keys.items() if n > 1]
        needs = {
            word
            for line in body
            if line.startswith("    needs:")
            for word in re.findall(r"[\w-]+", line.removeprefix("    needs:"))
        }
        matrix = keys_under(body, "      matrix:", MATRIX_KEY)
        for line in body:
            faults += [
                f"{name}: {job}.{out} is read and not given"
                for job, out in OUTPUT_READ.findall(line)
                if job not in needs or out not in outputs.get(job, set())
            ]
            faults += [
                f"{name}: matrix.{key} has no value"
                for key in MATRIX_READ.findall(line)
                if key not in matrix
            ]
    return faults


def job_names(root: Path, capsys: pytest.CaptureFixture[str]) -> list[str]:
    workflow, justfile = read(root, CI), conformance.read_justfile(root) or ""
    if "ci_jobs.py" not in workflow:
        listed = "tier1_jobs" if "tier1_jobs" in workflow else "tier1_gates"
        return list(justfile_text.ordered(justfile, listed) or ())
    given = shaped(root, capsys)
    return [*given["tier1"], *given["tier1_long"]]


def shaped(root: Path, capsys: pytest.CaptureFixture[str]) -> dict[str, list[str]]:
    output = root.parent / "shape-output"
    output.write_text("", encoding="utf-8")
    assert ci_jobs.main(["ci_jobs.py", "--root", str(root), "--output", str(output)]) == 0
    capsys.readouterr()
    given = dict(line.split("=", 1) for line in output.read_text(encoding="utf-8").splitlines())
    return {key: json.loads(given[key]) for key in ("tier1", "tier1_long")}


def unresolved(root: Path, capsys: pytest.CaptureFixture[str]) -> list[str]:
    workflow, justfile = read(root, CI), conformance.read_justfile(root) or ""
    names = job_names(root, capsys)
    if 'just ci-job "${{ matrix.gate }}"' in workflow and justfile_text.defines_recipe(
        justfile, "ci-job"
    ):
        return [name for name in names if name not in jobs(root)]
    return [name for name in names if not justfile_text.defines_recipe(justfile, name)]


def held(root: Path) -> set[tuple[str, str, float | None]]:
    text = read(root, THRESHOLDS)
    workflow = read(root, CI)
    if "ci_jobs.py" in workflow:
        assert 'just ci-job "${{ matrix.gate }}"' in workflow
        gates = [gate for each in jobs(root).values() for gate in each]
    else:
        assert HAND_EDIT in workflow
        assert ' -- just "${{ matrix.gate }}"' in workflow
        gates = list(justfile_text.ordered(read(root, JUSTFILE), "tier1_gates") or ())
    return {(gate, f"just {gate}", budget.held(text, gate, "tier1")[0]) for gate in gates}


def leftover(root: Path) -> list[conformance.Finding]:
    return [
        found
        for found in conformance.findings(root)
        if found.path in {CI, JUSTFILE} and "still says 'bind:" not in found.problem
    ]


def pinned(root: Path) -> str:
    return read(root, CI).split('ref: "', 1)[1].split('"', 1)[0]


def upgraded(root: Path) -> None:
    git(root, "checkout", "-q", "-b", "upgrade")
    assert upgrade.main(["upgrade.py", "--root", str(root), "--apply"]) == 0


TIER1_HEAD = "  tier1:\n    runs-on: ubuntu-latest"
CACHE_KEY = "            tier1-${{ runner.os }}-${{ matrix.gate }}-\n"
SHAPE_RUN = "      - id: shards\n        run: |\n"
GUARD = "  tier1:\n    if: needs.shape.outputs.tier1 != '[]'"
LONG_OUTPUT = '          echo "tier1_long=$(json "$long")" >> "$GITHUB_OUTPUT"\n'
LINE_ENDS = "its own line ends"
OWN_EDITS = {
    "a step of its own": (CACHE_KEY, CACHE_KEY + "      - run: ./scripts/warm-cache\n"),
    "a job of its own": (
        "jobs:\n",
        (
            "jobs:\n  docs:\n    runs-on: ubuntu-latest\n    timeout-minutes: 5\n    steps:\n"
            "      - run: echo docs\n"
        ),
    ),
    "a runner of its own": (
        TIER1_HEAD,
        "  tier1:\n    runs-on: ubuntu-24.04",
        f"{GUARD}\n    runs-on: ubuntu-latest",
        f"{GUARD}\n    runs-on: ubuntu-24.04",
    ),
    "its keys in its own order": (
        "    runs-on: ubuntu-latest\n    timeout-minutes: 30\n    needs: [tier0, shape]\n",
        "    timeout-minutes: 30\n    runs-on: ubuntu-latest\n    needs: [tier0, shape]\n",
        f"{GUARD}\n    runs-on: ubuntu-latest\n    timeout-minutes: 30\n",
        f"{GUARD}\n    timeout-minutes: 30\n    runs-on: ubuntu-latest\n",
    ),
    "a value of its own": ("    timeout-minutes: 10", "    timeout-minutes: 20"),
    "a comment of its own": ("jobs:\n", "jobs:\n  # the project's own note\n"),
}
KEPT_EDITS = {
    "a line of its own in the shape step": (
        HAND_EDIT,
        HAND_EDIT.replace("tier1_gates)", "tier1_gates) smoke"),
        "the shape job's steps are not as a released template wrote them",
    ),
    "a comment inside the shape step": (
        SHAPE_RUN,
        SHAPE_RUN + "          # count the shards\n",
        "the shape job's steps are not as a released template wrote them",
    ),
    "a need of its own": (
        "    needs: [tier0, shape]",
        "    needs: [tier0, shape, docs]",
        "the tier 1 jobs' `needs:` lines are not as a released template wrote them",
    ),
    "a key the template's job lacks": (
        TIER1_HEAD,
        "  tier1:\n    if: github.event_name != 'schedule'\n    runs-on: ubuntu-latest",
        "the job `tier1` has the key `if`, which the template's job lacks",
    ),
    "a job that reads the shape's outputs": (
        "jobs:\n",
        (
            "jobs:\n  gate-list:\n    needs: shape\n    runs-on: ubuntu-latest\n    strategy:\n"
            "      matrix:\n        gate: ${{ fromJSON(needs.shape.outputs.tier1) }}\n    steps:\n"
            '      - run: just --dry-run "${{ matrix.gate }}"\n'
        ),
        "reads `needs.shape.outputs.`, which the new shape changes",
    ),
    "a line of its own after the shape script": (
        LONG_OUTPUT,
        LONG_OUTPUT + '          echo "extra=1" >> "$GITHUB_OUTPUT"\n',
        "a line the plugin did not write runs on from the shape job's steps",
    ),
    "a step that reads tier 0's result": (
        CACHE_KEY,
        CACHE_KEY + "      - if: needs.tier0.result == 'success'\n        run: ./scripts/after\n",
        "reads `needs.tier0.`, which the new shape changes",
    ),
    "a step chosen by its gate": (
        CACHE_KEY,
        CACHE_KEY + "      - if: matrix.gate == 'audit'\n        run: ./scripts/install-audit-db\n",
        "reads `matrix.gate`, which the new shape changes",
        'tier1_jobs := "test mutation-changed audit bite rest"',
    ),
}


def test_the_template_the_upgrade_compares_with_is_the_ci_yml_the_releases_wrote() -> None:
    for version in ("0.13.3", "0.13.4", "0.13.5"):
        wrote = adopted.template_files(adopted.LATER[version])[CI]
        assert "\n".join(conformance.released()).rstrip("\n") == wrote.rstrip("\n")


def edited(root: Path, edit: str | None) -> bytes:
    text = read(root, CI)
    if edit == LINE_ENDS:
        text = text.replace("\n", "\r\n")
    elif edit is not None:
        old, new, *_ = {**OWN_EDITS, **KEPT_EDITS}[edit]
        assert old in text, edit
        text = text.replace(old, new, 1)
    (root / CI).write_bytes(text.encode())
    git(root, "commit", "-q", "--allow-empty", "-am", "ci: the project's own workflow")
    return text.encode()


APPLIED = [
    *RELEASES,
    *(
        pytest.param(edit, marks=pytest.mark.shard_slot(slot), id=edit)
        for slot, edit in enumerate([*OWN_EDITS, LINE_ENDS])
    ),
]


@pytest.mark.parametrize("case", APPLIED)
def test_the_upgrade_groups_a_template_shaped_workflow_and_keeps_every_gate_its_command_and_ceiling(
    case: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    version, edit = (case, None) if case in adopted.LATER else ("0.13.5", case)
    root = adopted.copy(adopted.adopted(version), tmp_path / "repo")
    edited(root, edit)
    before = held(root)
    long_before = justfile_text.ordered(read(root, JUSTFILE), "tier1_sharded")
    assert long_before == ("test", "mutation-changed")
    upgraded(root)
    capsys.readouterr()
    assert held(root) == before
    assert shaped(root, capsys) == {"tier1": ["bite", "rest"], "tier1_long": list(long_before)}
    gates = justfile_text.ordered(read(root, JUSTFILE), "tier1_gates") or ()
    assert jobs(root)["rest"] == tuple(g for g in gates if g not in {"bite", *long_before})
    written = (root / CI).read_bytes().decode()
    template = conformance.template_file(CI).replace("<ratchet-sha>", pinned(root))
    if edit is None:
        assert written == template
    elif edit == LINE_ENDS:
        assert written == template.replace("\n", "\r\n")
    else:
        old, new, *on_template = OWN_EDITS[edit]
        old, new = on_template or (old, new)
        assert written == template.replace(old, new, 1)
    assert workflow_faults(written) == []
    assert unresolved(root, capsys) == []
    assert JOBS_LINE in read(root, JUSTFILE)
    assert "doc-coverage" in jobs(root)["rest"]
    assert leftover(root) == []
    files = {path: (root / path).read_bytes() for path in (CI, JUSTFILE, BINDING)}
    plan = upgrade.plan(root, PLUGIN_ROOT)
    assert isinstance(plan, upgrade.Plan)
    assert not {CI, JUSTFILE, BINDING} & set(plan.changes), "a second run writes nothing"
    assert {path: (root / path).read_bytes() for path in files} == files
    assert conformance.run(root) == 0


KEPT = [
    *(
        pytest.param(version, edit, marks=pytest.mark.shard_slot(slot))
        for slot, (version, edit) in enumerate(
            [
                ("0.13.3", "a line of its own in the shape step"),
                ("0.13.4", "a need of its own"),
                *(("0.13.5", edit) for edit in KEPT_EDITS),
            ]
        )
    ),
]


def upgraded_kept_ci(text: str, old_ref: str, new_ref: str) -> str:
    return (
        text.replace(old_ref, new_ref)
        .replace("  OVER: ${{ github.event_name == 'pull_request' && 'report' || 'block' }}\n", "")
        .replace(' --over "$OVER"', " --over report")
        .replace(
            "          path: |\n            .cache\n",
            "          path: |\n            .cache\n            .ratchet/timing.jsonl\n",
        )
    )


@pytest.mark.parametrize(("version", "edit"), KEPT)
def test_the_upgrade_keeps_a_workflow_it_cannot_regroup_without_changing_a_line_of_the_project(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], *, version: str, edit: str
) -> None:
    root = adopted.copy(adopted.adopted(version), tmp_path / "repo")
    before = edited(root, edit).decode()
    upgraded(root)
    assert read(root, CI) == upgraded_kept_ci(before, adopted.LATER[version], pinned(root))
    out = capsys.readouterr().out
    assert "nothing turns the justfile's job list into the jobs tier 1 runs" in out
    [found] = leftover(root)
    assert found.path == CI
    assert isinstance(found.fix, conformance.Agent)
    assert KEPT_EDITS[edit][2] in found.fix.start[0]
    assert NEW_STEP in found.fix.start
    assert "-    needs: [tier0, shape]" in found.fix.start
    assert (*KEPT_EDITS[edit][3:], JOBS_LINE)[0] in read(root, JUSTFILE)
    assert unresolved(root, capsys) == []
    assert conformance.run(root) == 0


@pytest.mark.shard_slot(6)
def test_a_template_shaped_workflow_waits_for_the_recipe_that_runs_each_job_by_its_name(
    tmp_path: Path,
) -> None:
    root = adopted.copy(adopted.adopted("0.13.5"), tmp_path / "repo")
    (root / JUSTFILE).write_text(f"{read(root, JUSTFILE)}\n{JOBS_LINE}\n", encoding="utf-8")
    [found] = [f for f in conformance.findings(root) if f.path == CI and "ci_jobs.py" in f.problem]
    assert isinstance(found.fix, conformance.Agent)
    assert "the justfile has no `ci-job` recipe to run each job by its name" in found.fix.start[0]


OWN_LIST = 'tier1_jobs := "suite boot bite others"'
OWN_RECIPES = (
    "\nsuite:\n    just test coverage\n\nboot:\n    just build smoke\n\nothers:\n    just tier1\n"
)
OWN_READ = (
    (
        "          for gate in $(just --evaluate tier1_gates); do\n"
        '            case " $long " in *" $gate "*) ;; *) short="$short $gate" ;; esac\n'
        "          done\n"
        '          echo "tier1=$(json "$short")" >> "$GITHUB_OUTPUT"\n'
    ),
    '          echo "tier1=$(json "$(just --evaluate tier1_jobs)")" >> "$GITHUB_OUTPUT"\n',
)


@pytest.mark.shard_slot(4)
def test_the_upgrade_leaves_a_job_list_of_the_project_s_own_recipes_and_offers_one_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = adopted.copy(adopted.adopted("0.13.5"), tmp_path / "repo")
    (root / JUSTFILE).write_text(f"{read(root, JUSTFILE)}\n{OWN_LIST}\n{OWN_RECIPES}", "utf-8")
    old, new = OWN_READ
    assert old in read(root, CI)
    (root / CI).write_text(read(root, CI).replace(old, new), encoding="utf-8")
    git(root, "commit", "-q", "-am", "ci: tier 1 runs as the project's own jobs")
    workflow, matrix = read(root, CI), job_names(root, capsys)
    assert matrix == ["suite", "boot", "bite", "others"]
    upgraded(root)
    out = capsys.readouterr().out
    assert OWN_LIST in read(root, JUSTFILE)
    assert read(root, CI) == upgraded_kept_ci(workflow, adopted.LATER["0.13.5"], pinned(root))
    assert job_names(root, capsys) == matrix
    assert unresolved(root, capsys) == []
    assert "does not list" not in out
    found = conformance.findings(root)
    assert not [f for f in found if "does not list" in f.problem]
    [offered] = [f for f in found if f.path == JUSTFILE and "tier1_jobs" in f.problem]
    assert offered.problem == (
        "`tier1_jobs` holds `suite`, which is no gate of `tier1_gates`, `rest` or"
        f" `NAME=gate,gate`, {OWN_LINE}"
    )
    assert isinstance(offered.fix, conformance.Agent)
    assert NEW_STEP in offered.fix.start
    assert conformance.run(root) == 0
