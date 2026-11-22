import json
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest

import already_proven
import gatelib
from tests.repos import commit, git, repository, write

REPO = "owner/repo"
CI_PATH = ".github/workflows/ci.yml"
NOW = datetime(2026, 11, 22, 12, 0, 0, tzinfo=UTC)
NOW_ISO = "2026-11-22T12:00:00Z"


@dataclass
class JudgeEnv:
    root: Path
    shas: dict[str, str]
    run_file: Path
    proven_file: Path
    timings_dir: Path
    out_file: Path


def iso(delta_seconds: float = 0) -> str:
    return (NOW + timedelta(seconds=delta_seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")


def make_ff_merge_repo(root: Path, *, diverge_tree: bool = False) -> dict[str, str]:
    repository(root)
    base_sha = git(root, "rev-parse", "HEAD")
    git(root, "switch", "-q", "-c", "feat")
    write(root, "app.py", "x = 1\n")
    head_sha = commit(root, "feat(app): add app")
    git(root, "switch", "-q", "main")
    if diverge_tree:
        write(root, "other.txt", "main moved\n")
        base_sha = commit(root, "chore(other): move main")
    git(root, "merge", "--no-ff", "--no-commit", "feat")
    git(root, "commit", "-q", "-m", "feat(app): merge feat")
    merge_sha = git(root, "rev-parse", "HEAD")
    tree_sha = git(root, "rev-parse", "HEAD^{tree}")
    return {"base": base_sha, "head": head_sha, "merge": merge_sha, "tree": tree_sha}


def make_run(
    head_sha: str,
    *,
    run_id: int = 101,
    created_at: str | None = None,
    **over: object,
) -> dict[str, object]:
    data: dict[str, object] = {
        "id": run_id,
        "repository": {"full_name": REPO},
        "path": CI_PATH,
        "event": "pull_request",
        "status": "completed",
        "conclusion": "success",
        "head_sha": head_sha,
        "created_at": iso(-60) if created_at is None else created_at,
        "run_started_at": iso(-55),
        "pull_requests": [{"number": 7, "base": {"ref": "main"}}],
    }
    data.update(over)
    return data


def read_outputs(path: Path) -> dict[str, str]:
    return dict(line.split("=", 1) for line in path.read_text(encoding="utf-8").splitlines())


def write_jsonl(path: Path, records: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")


def setup_ci_project(
    root: Path,
    *,
    shards: int = 1,
    thresholds: str = "[ceilings.seconds]\ntier0 = 60\ntier1 = 900\nbite-all = 300\n",
    tier1_jobs: str = "test mutation-changed bite rest",
    idle_delta: bool = False,
) -> None:
    write(
        root,
        "justfile",
        f'tier1_gates := "test mutation-changed bite coverage delta"\n'
        f'tier1_sharded := "test mutation-changed"\n'
        f'tier1_jobs := "{tier1_jobs}"\n'
        + ('delta:\n    @echo "none: not used"\n' if idle_delta else ""),
    )
    delta_row = "| d | none: not used | 1 | | `just delta` |\n" if idle_delta else ""
    write(
        root,
        "docs/process/binding.md",
        "# Binding\n\n## Gates\n\n| Question | Tool | Tier | Threshold source | Entry point |\n"
        f"| --- | --- | --- | --- | --- |\n{delta_row}"
        f"## Speed\n\n- Shards: {shards}\n",
    )
    if thresholds:
        write(root, "quality/thresholds.toml", thresholds)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "chore(ci): configure gates")


def populate_timings(timings_dir: Path, *, shards: int = 1, include_delta: bool = True) -> None:
    write_jsonl(
        timings_dir / "timing-tier0" / "timing.jsonl",
        [
            {"key": "lint", "seconds": 1.0, "ceiling": None, "status": "unbounded", "exit": 0},
            {"kind": "tier", "seconds": 1.2, "status": "green", "exit": 0, "gates": ["lint"]},
            {"key": "tier0", "seconds": 2.0, "ceiling": 60.0, "status": "green", "exit": 0},
        ],
    )
    write_jsonl(
        timings_dir / "timing-bite" / "timing.jsonl",
        [
            {"kind": "fault", "gate": "lint", "bite": "b", "seconds": 0.5, "narrowed": False},
            {"key": "bite", "seconds": 5.0, "ceiling": 300.0, "status": "green", "exit": 0},
        ],
    )
    rest_gates = ["coverage", "delta"] if include_delta else ["coverage"]
    rest_records: list[dict[str, object]] = [
        {"key": "coverage", "seconds": 3.0, "ceiling": None, "status": "unbounded", "exit": 0}
    ]
    if include_delta:
        rest_records.append(
            {"key": "delta", "seconds": 0.2, "ceiling": None, "status": "unbounded", "exit": 0}
        )
    rest_records.extend(
        [
            {"kind": "tier", "seconds": 3.5, "status": "green", "exit": 0, "gates": rest_gates},
            {"kind": "cache-missing", "path": ".cache"},
            {"kind": "group", "job": "rest", "gates": rest_gates},
            {"key": "rest", "seconds": 4.0, "ceiling": None, "status": "unbounded", "exit": 0},
        ]
    )
    write_jsonl(timings_dir / "timing-rest" / "timing.jsonl", rest_records)
    for gate in ("test", "mutation-changed"):
        for shard in range(1, shards + 1):
            write_jsonl(
                timings_dir / f"timing-{gate}-{shard}" / "timing.jsonl",
                [{"key": gate, "seconds": 10.0, "ceiling": 900.0, "status": "green", "exit": 0}],
            )


def prepare_judge_inputs(
    tmp_path: Path,
    *,
    shards: int = 1,
    thresholds: str = "[ceilings.seconds]\ntier0 = 60\ntier1 = 900\nbite-all = 300\n",
    idle_delta: bool = False,
    with_project: bool = True,
) -> JudgeEnv:
    root = tmp_path / "repo"
    if with_project:
        repository(root)
        setup_ci_project(root, shards=shards, thresholds=thresholds, idle_delta=idle_delta)
        base_sha = git(root, "rev-parse", "HEAD")
        git(root, "switch", "-q", "-c", "feat")
        write(root, "app.py", "x = 1\n")
        head_sha = commit(root, "feat(app): add app")
        git(root, "switch", "-q", "main")
        git(root, "merge", "--no-ff", "--no-commit", "feat")
        git(root, "commit", "-q", "-m", "feat(app): merge feat")
        shas = {
            "base": base_sha,
            "head": head_sha,
            "merge": git(root, "rev-parse", "HEAD"),
            "tree": git(root, "rev-parse", "HEAD^{tree}"),
        }
    else:
        shas = make_ff_merge_repo(root)
    run_file = tmp_path / "run.json"
    proven_file = tmp_path / "proven.json"
    timings_dir = tmp_path / "timings"
    out_file = tmp_path / "out.txt"
    run_file.write_text(json.dumps(make_run(shas["head"])), encoding="utf-8")
    proven_file.write_text(
        json.dumps({"schema": 1, "base": shas["base"], "head": shas["head"], "tree": shas["tree"]}),
        encoding="utf-8",
    )
    populate_timings(timings_dir, shards=shards, include_delta=not idle_delta)
    return JudgeEnv(root, shas, run_file, proven_file, timings_dir, out_file)


def run_judge(
    env: JudgeEnv, *, attempt: str = "1", now: str = NOW_ISO, max_age: str = "3600"
) -> int:
    return already_proven.main(
        [
            "judge",
            "--root",
            str(env.root),
            "--repository",
            REPO,
            "--attempt",
            attempt,
            "--run",
            str(env.run_file),
            "--proven",
            str(env.proven_file),
            "--timings",
            str(env.timings_dir),
            "--max-age",
            max_age,
            "--now",
            now,
            "--output",
            str(env.out_file),
        ]
    )


def assert_judge_refuses(
    env: JudgeEnv,
    capsys: pytest.CaptureFixture[str],
    *,
    attempt: str = "1",
    now: str = NOW_ISO,
    max_age: str = "3600",
) -> None:
    code = run_judge(env, attempt=attempt, now=now, max_age=max_age)
    assert code == 0
    assert capsys.readouterr().out.strip().startswith("already_proven: run:")
    assert read_outputs(env.out_file) == {"skip": "false"}


def test_record_writes_proven_json_atomically_for_two_parent_merge(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    shas = make_ff_merge_repo(root, diverge_tree=True)
    out = tmp_path / "nested/dir/proven.json"
    out.parent.mkdir(parents=True)
    out.write_text("stale", encoding="utf-8")
    assert already_proven.main(["record", "--root", str(root), "--out", str(out)]) == 0
    assert json.loads(out.read_text(encoding="utf-8")) == {
        "schema": 1,
        "base": shas["base"],
        "head": shas["head"],
        "tree": shas["tree"],
    }


def test_record_writes_nothing_and_exits_zero_for_non_merge_or_bad_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    repository(root)
    out = tmp_path / "proven.json"
    assert already_proven.main(["record", "--root", str(root), "--out", str(out)]) == 0
    assert not out.exists()
    commit(root, "feat(core): one parent")
    assert already_proven.main(["record", "--root", str(root), "--out", str(out)]) == 0
    assert not out.exists()
    git(root, "switch", "-q", "-c", "b1")
    commit(root, "feat(core): b1")
    git(root, "switch", "-q", "main")
    git(root, "switch", "-q", "-c", "b2")
    commit(root, "feat(core): b2")
    git(root, "switch", "-q", "main")
    git(root, "merge", "--no-ff", "b1", "b2")
    assert already_proven.main(["record", "--root", str(root), "--out", str(out)]) == 0
    assert not out.exists()
    missing = tmp_path / "missing"
    assert already_proven.main(["record", "--root", str(missing), "--out", str(out)]) == 0
    assert not out.exists()
    monkeypatch.setattr(
        gatelib,
        "git",
        lambda _r, *args: f"{'a' * 40} {'b' * 40}" if args[0] == "show" else None,
    )
    assert already_proven.main(["record", "--root", str(root), "--out", str(out)]) == 0
    assert not out.exists()


def test_record_ignores_atomic_write_error_on_directory_target(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    make_ff_merge_repo(root)
    target_dir = tmp_path / "as_dir"
    target_dir.mkdir()
    assert already_proven.main(["record", "--root", str(root), "--out", str(target_dir)]) == 0
    assert set(target_dir.iterdir()) == set()


def test_select_picks_newest_qualifying_run_and_handles_empty_pull_requests(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "repo"
    shas = make_ff_merge_repo(root)
    runs_file = tmp_path / "runs.json"
    out_file = tmp_path / "out.txt"
    payload = {
        "workflow_runs": [
            make_run(shas["head"], run_id=10, created_at=iso(-300), pull_requests=[]),
            make_run(shas["head"], run_id=20, created_at=iso(-100)),
            make_run(shas["head"], run_id=25, created_at=iso(-100), pull_requests=None),
            make_run(shas["head"], run_id=99, created_at=iso(-50), conclusion="failure"),
        ]
    }
    runs_file.write_text(json.dumps(payload), encoding="utf-8")
    code = already_proven.main(
        [
            "select",
            "--root",
            str(root),
            "--repository",
            REPO,
            "--base-branch",
            "main",
            "--attempt",
            "1",
            "--now",
            NOW_ISO,
            "--runs",
            str(runs_file),
            "--output",
            str(out_file),
        ]
    )
    assert code == 0
    assert capsys.readouterr().out.strip() == "already_proven: candidate 25"
    assert read_outputs(out_file) == {"run_id": "25"}


def test_select_reads_stdin_with_non_utf8_bytes_and_accepts_small_clock_skew(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    shas = make_ff_merge_repo(root)
    run = make_run(shas["head"], run_id=42, created_at=iso(30), run_started_at=None, name="x")
    raw = json.dumps({"workflow_runs": [run]}).replace('"x"', '"x\xff"').encode("latin-1")

    class FakeStdin:
        def __init__(self, data: bytes) -> None:
            self.buffer = type("Buf", (), {"read": staticmethod(lambda: data)})()

    monkeypatch.setattr(sys, "stdin", FakeStdin(raw))
    code = already_proven.main(
        [
            "select",
            "--root",
            str(root),
            "--repository",
            REPO,
            "--base-branch",
            "main",
            "--attempt",
            "1",
            "--now",
            NOW_ISO,
        ]
    )
    assert code == 0
    assert capsys.readouterr().out.strip() == "already_proven: candidate 42"


SELECT_RUN_OVERRIDES: dict[str, dict[str, object]] = {
    "wrong_repo": {"repository": {"full_name": "other/repo"}},
    "wrong_path": {"path": ".github/workflows/other.yml"},
    "wrong_event": {"event": "push"},
    "wrong_status": {"status": "in_progress"},
    "wrong_conclusion": {"conclusion": "failure"},
    "wrong_head": {"head_sha": "0" * 40},
    "wrong_pr_base": {"pull_requests": [{"number": 7, "base": {"ref": "dev"}}]},
    "bad_pr_type": {"pull_requests": "bad"},
    "bad_id": {"id": True},
    "bad_created": {"created_at": "not-a-date"},
    "bad_calendar": {"created_at": "2026-13-01T10:00:00Z"},
    "bad_started": {"run_started_at": "not-a-date"},
    "stale_started": {"created_at": iso(-100), "run_started_at": iso(-4000)},
    "stale_created": {"created_at": iso(-3601), "run_started_at": None},
    "future_created": {"created_at": iso(61), "run_started_at": None},
}


@pytest.mark.parametrize(
    ("mutator", "reason"),
    [
        ("attempt_2", "attempt"),
        ("attempt_bad", "attempt"),
        ("single_parent", "parent"),
        ("diverged_tree", "tree"),
        ("missing_runs", "read"),
        ("bad_json", "JSON"),
        ("runs_not_list", "runs"),
        *((key, "qualifying") for key in SELECT_RUN_OVERRIDES),
        ("bad_now", "now"),
        ("bad_max_age", "max-age"),
    ],
)
def test_select_refuses_and_exits_zero_on_every_ineligible_input(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], *, mutator: str, reason: str
) -> None:
    root = tmp_path / "repo"
    if mutator == "single_parent":
        repository(root)
        shas = {"head": commit(root, "feat(a): single")}
    elif mutator == "diverged_tree":
        shas = make_ff_merge_repo(root, diverge_tree=True)
    else:
        shas = make_ff_merge_repo(root)
    runs_file = tmp_path / "runs.json"
    out_file = tmp_path / "out.txt"
    run = make_run(shas["head"])
    run.update(SELECT_RUN_OVERRIDES.get(mutator, {}))
    if mutator == "bad_json":
        runs_file.write_text("{broken", encoding="utf-8")
    elif mutator == "runs_not_list":
        runs_file.write_text(json.dumps({"workflow_runs": "nope"}), encoding="utf-8")
    elif mutator != "missing_runs":
        runs_file.write_text(json.dumps({"workflow_runs": ["bad-item", run]}), encoding="utf-8")
    code = already_proven.main(
        [
            "select",
            "--root",
            str(root),
            "--repository",
            REPO,
            "--base-branch",
            "main",
            "--attempt",
            "2" if mutator == "attempt_2" else ("abc" if mutator == "attempt_bad" else "1"),
            "--max-age",
            "-1" if mutator == "bad_max_age" else "3600",
            "--now",
            "nope" if mutator == "bad_now" else NOW_ISO,
            "--runs",
            str(runs_file),
            "--output",
            str(out_file),
        ]
    )
    assert code == 0
    out = capsys.readouterr().out.strip()
    assert out.startswith("already_proven: run:")
    assert reason.lower() in out.lower()
    assert read_outputs(out_file) == {"run_id": ""}


def test_select_fails_safe_on_unexpected_exception(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    out_file = tmp_path / "out.txt"

    def boom(_a: str) -> int:
        msg = "boom"
        raise RuntimeError(msg)

    monkeypatch.setattr(already_proven, "parse_attempt", boom)
    code = already_proven.main(
        [
            "select",
            "--root",
            str(tmp_path),
            "--repository",
            REPO,
            "--base-branch",
            "main",
            "--attempt",
            "1",
            "--output",
            str(out_file),
        ]
    )
    assert code == 0
    assert "already_proven: run:" in capsys.readouterr().out
    assert read_outputs(out_file) == {"run_id": ""}


@pytest.mark.parametrize("shards", [1, 2])
def test_judge_allows_skip_when_all_six_invariants_hold(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], shards: int
) -> None:
    env = prepare_judge_inputs(tmp_path, shards=shards, idle_delta=shards == 2)
    assert run_judge(env) == 0
    expected = (
        f"already_proven: skip: PR run 101 already settled tree {env.shas['tree'][:12]}"
        f" against base {env.shas['base'][:12]}"
    )
    assert capsys.readouterr().out.strip() == expected
    assert read_outputs(env.out_file) == {"skip": "true"}


def test_judge_handles_minimal_repo_without_justfile_or_thresholds(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env = prepare_judge_inputs(tmp_path, with_project=False)
    write_jsonl(
        env.timings_dir / "timing-tier0" / "timing.jsonl",
        [{"key": "tier0", "seconds": 1.0, "over": False, "exit": 0}],
    )
    assert run_judge(env) == 0
    assert "already_proven: skip:" in capsys.readouterr().out
    assert read_outputs(env.out_file) == {"skip": "true"}
    write(env.root, "justfile", "default:\n    @true\n")
    assert run_judge(env) == 0
    assert read_outputs(env.out_file) == {"skip": "true"}


def test_judge_does_not_apply_tier1_ceiling_to_group_job_wall_clock(tmp_path: Path) -> None:
    env = prepare_judge_inputs(
        tmp_path,
        thresholds="[ceilings.seconds]\ntier0 = 60\ntier1 = 5\nbite-all = 300\n",
    )
    write_jsonl(
        env.timings_dir / "timing-rest" / "timing.jsonl",
        [
            {"key": "coverage", "seconds": 4.0, "ceiling": None, "status": "unbounded", "exit": 0},
            {"key": "delta", "seconds": 4.0, "ceiling": None, "status": "unbounded", "exit": 0},
            {
                "kind": "tier",
                "seconds": 8.0,
                "status": "green",
                "exit": 0,
                "gates": ["coverage", "delta"],
            },
            {"kind": "group", "job": "rest", "gates": ["coverage", "delta"]},
            {"key": "rest", "seconds": 8.5, "ceiling": None, "status": "unbounded", "exit": 0},
        ],
    )
    for gate in ("test", "mutation-changed"):
        write_jsonl(
            env.timings_dir / f"timing-{gate}-1" / "timing.jsonl",
            [{"key": gate, "seconds": 4.0, "ceiling": 5.0, "status": "green", "exit": 0}],
        )
    assert run_judge(env) == 0
    assert read_outputs(env.out_file) == {"skip": "true"}


RUN_MODS: dict[str, dict[str, object]] = {
    "run_wrong_repo": {"repository": {"full_name": "other/repo"}},
    "run_wrong_path": {"path": "other.yml"},
    "run_wrong_event": {"event": "push"},
    "run_wrong_status": {"status": "queued"},
    "run_wrong_conclusion": {"conclusion": "failure"},
    "run_wrong_head": {"head_sha": "0" * 40},
    "run_bad_id": {"id": 0},
    "run_stale": {"created_at": iso(-5000)},
    "run_future": {"created_at": iso(120), "run_started_at": None},
}
PROVEN_MODS: dict[str, dict[str, object]] = {
    "proven_bad_schema": {"schema": 2},
    "proven_base_mismatch": {"base": "0" * 40},
    "proven_head_mismatch": {"head": "0" * 40},
    "proven_tree_mismatch": {"tree": "0" * 40},
}


@pytest.mark.parametrize("case", list(RUN_MODS))
def test_judge_refuses_bad_run_fields(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], case: str
) -> None:
    env = prepare_judge_inputs(tmp_path)
    run_obj = make_run(env.shas["head"])
    run_obj.update(RUN_MODS[case])
    env.run_file.write_text(json.dumps(run_obj), encoding="utf-8")
    assert_judge_refuses(env, capsys)


@pytest.mark.parametrize("case", list(PROVEN_MODS))
def test_judge_refuses_bad_proven_fields(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], case: str
) -> None:
    env = prepare_judge_inputs(tmp_path)
    proven_obj: dict[str, object] = {
        "schema": 1,
        "base": env.shas["base"],
        "head": env.shas["head"],
        "tree": env.shas["tree"],
        **PROVEN_MODS[case],
    }
    env.proven_file.write_text(json.dumps(proven_obj), encoding="utf-8")
    assert_judge_refuses(env, capsys)


def test_judge_refuses_bad_cli_git_and_json_files(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    for case in ("attempt_2", "bad_now", "bad_max_age"):
        env = prepare_judge_inputs(tmp_path / case)
        assert_judge_refuses(
            env,
            capsys,
            attempt="2" if case == "attempt_2" else "1",
            now="bad" if case == "bad_now" else NOW_ISO,
            max_age="-5" if case == "bad_max_age" else "3600",
        )
    env_single = prepare_judge_inputs(tmp_path / "single")
    commit(env_single.root, "feat(x): single")
    assert_judge_refuses(env_single, capsys)
    env_div = prepare_judge_inputs(tmp_path / "div")
    env_div.root = tmp_path / "div_repo"
    make_ff_merge_repo(env_div.root, diverge_tree=True)
    assert_judge_refuses(env_div, capsys)
    for label, action in (
        ("missing_run", lambda e: e.run_file.unlink()),
        ("bad_run", lambda e: e.run_file.write_text("{", encoding="utf-8")),
        ("list_run", lambda e: e.run_file.write_text("[]", encoding="utf-8")),
        ("missing_proven", lambda e: e.proven_file.unlink()),
        ("bad_proven", lambda e: e.proven_file.write_text("{", encoding="utf-8")),
        ("list_proven", lambda e: e.proven_file.write_text("[]", encoding="utf-8")),
    ):
        env_meta = prepare_judge_inputs(tmp_path / label)
        action(env_meta)
        assert_judge_refuses(env_meta, capsys)


TIMING_RECORDS: dict[str, list[dict[str, object]]] = {
    "exit_nonzero": [{"key": "bite", "seconds": 1.0, "exit": 1}],
    "exit_bool": [{"key": "bite", "seconds": 1.0, "exit": False}],
    "missing_exit": [{"key": "bite", "seconds": 1.0}],
    "over_true": [{"key": "bite", "seconds": 1.0, "over": True, "exit": 0}],
    "over_non_bool": [{"key": "bite", "seconds": 1.0, "over": "no", "exit": 0}],
    "status_red": [{"key": "bite", "seconds": 1.0, "status": "red", "exit": 0}],
    "status_failed": [{"key": "bite", "seconds": 1.0, "status": "failed", "exit": 0}],
    "bad_seconds": [{"key": "bite", "seconds": -1.0, "exit": 0}],
    "missing_key": [{"seconds": 1.0, "exit": 0}],
    "bite_over_threshold": [{"key": "bite", "seconds": 301.0, "status": "green", "exit": 0}],
}


@pytest.mark.parametrize("case", list(TIMING_RECORDS))
def test_judge_refuses_bad_budget_record(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], case: str
) -> None:
    env = prepare_judge_inputs(tmp_path)
    write_jsonl(env.timings_dir / "timing-bite" / "timing.jsonl", TIMING_RECORDS[case])
    assert_judge_refuses(env, capsys)


def _break_missing_dir(env: JudgeEnv) -> None:
    env.timings_dir = env.timings_dir.parent / "no-such-timings"


def _break_no_jsonl(env: JudgeEnv) -> None:
    for path in env.timings_dir.rglob("*.jsonl"):
        path.unlink()


def _break_symlink(env: JudgeEnv) -> None:
    target = env.timings_dir / "timing-bite" / "timing.jsonl"
    target.unlink()
    target.symlink_to(env.timings_dir.parent / "does-not-exist")


TIMING_MUTATIONS: dict[str, Callable[[JudgeEnv], None]] = {
    "missing_timings_dir": _break_missing_dir,
    "no_jsonl_files": _break_no_jsonl,
    "empty_jsonl": lambda e: (e.timings_dir / "timing-bite" / "timing.jsonl").write_text(
        "", "utf-8"
    ),
    "blank_line_jsonl": lambda e: (e.timings_dir / "timing-bite" / "timing.jsonl").write_text(
        json.dumps({"key": "bite", "seconds": 1.0, "exit": 0}) + "\n\n", "utf-8"
    ),
    "corrupt_jsonl": lambda e: (e.timings_dir / "timing-bite" / "timing.jsonl").write_text(
        "{not json\n", "utf-8"
    ),
    "non_dict_jsonl": lambda e: (e.timings_dir / "timing-bite" / "timing.jsonl").write_text(
        "[]\n", "utf-8"
    ),
    "non_utf8_jsonl": lambda e: (e.timings_dir / "timing-bite" / "timing.jsonl").write_bytes(
        b"\xff\n"
    ),
    "broken_symlink_jsonl": _break_symlink,
    "tier0_over_threshold": lambda e: write_jsonl(
        e.timings_dir / "timing-tier0" / "timing.jsonl",
        [{"key": "tier0", "seconds": 61.0, "status": "green", "exit": 0}],
    ),
    "group_gate_over_threshold": lambda e: (e.root / "quality/thresholds.toml").write_text(
        "[ceilings.seconds]\ntier0 = 60\ntier1 = 900\ncoverage = 2\n", "utf-8"
    ),
    "group_job_over_own_threshold": lambda e: (e.root / "quality/thresholds.toml").write_text(
        "[ceilings.seconds]\ntier0 = 60\ntier1 = 900\nrest = 3\n", "utf-8"
    ),
    "bad_thresholds_toml": lambda e: (e.root / "quality/thresholds.toml").write_text(
        "= invalid toml\n", "utf-8"
    ),
    "missing_tier0_record": lambda e: write_jsonl(
        e.timings_dir / "timing-tier0" / "timing.jsonl",
        [{"key": "lint", "seconds": 1.0, "exit": 0}],
    ),
    "missing_tier1_job": lambda e: (e.timings_dir / "timing-bite" / "timing.jsonl").unlink(),
    "missing_shard": lambda e: (e.timings_dir / "timing-test-2" / "timing.jsonl").unlink(),
    "bad_justfile_own": lambda e: (e.root / "justfile").write_text(
        'tier1_gates := "bite"\ntier1_sharded := ""\ntier1_jobs := "unknown"\n', "utf-8"
    ),
    "bad_justfile_refused": lambda e: (e.root / "justfile").write_text(
        'tier1_gates := "bite coverage"\ntier1_sharded := ""\ntier1_jobs := "bite"\n', "utf-8"
    ),
    "missing_shards_line": lambda e: (e.root / "docs/process/binding.md").write_text(
        "# Binding\n", "utf-8"
    ),
}


@pytest.mark.parametrize("case", list(TIMING_MUTATIONS))
def test_judge_refuses_bad_timings_structure_or_project(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], case: str
) -> None:
    env = prepare_judge_inputs(tmp_path, shards=2 if case == "missing_shard" else 1)
    TIMING_MUTATIONS[case](env)
    assert_judge_refuses(env, capsys)


def test_judge_refuses_unreadable_thresholds_toml(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    env = prepare_judge_inputs(tmp_path)
    orig = already_proven.read_bytes_file
    monkeypatch.setattr(
        already_proven,
        "read_bytes_file",
        lambda p: None if p.name == "thresholds.toml" else orig(p),
    )
    assert_judge_refuses(env, capsys)


def test_judge_fails_safe_on_unexpected_exception(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    env = prepare_judge_inputs(tmp_path)

    def boom(_o: object) -> tuple[bool, str]:
        msg = "boom"
        raise RuntimeError(msg)

    monkeypatch.setattr(already_proven, "judge_skip", boom)
    assert_judge_refuses(env, capsys)


def test_cli_entrypoint_executes_record_cleanly(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    shas = make_ff_merge_repo(root)
    out = tmp_path / "proven.json"
    script = Path(__file__).resolve().parents[2] / "scripts/already_proven.py"
    done = subprocess.run(  # noqa: S603 # -- fixed argv; runs the script under test
        [sys.executable, str(script), "record", "--root", str(root), "--out", str(out)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0
    assert (
        cast("dict[str, str]", json.loads(out.read_text(encoding="utf-8")))["tree"] == shas["tree"]
    )
