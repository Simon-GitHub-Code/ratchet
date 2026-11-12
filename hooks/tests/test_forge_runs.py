"""``forge_runs.py``: which runs the merge job counts, for ``ci`` and for tier 2 (ADR 0024).

The answers of the forge are written here as the REST API documents them. No test here reaches
a forge: what GitHub does with a retargeted pull request or a tag named like a branch is the
ADR's to say was observed or not.
"""

import io
import json
import sys
from pathlib import Path

import pytest

import forge_runs
from tests.repos import commit, git, repository

REPO = "owner/repo"
SHA = "a" * 40
BRANCH = "feat/a"
CI = ".github/workflows/ci.yml"


def pull(number: int = 1, base: str = "main", **over: object) -> dict[str, object]:
    head = {"ref": BRANCH, "sha": SHA, "repo": {"full_name": REPO}}
    return {"number": number, "state": "open", "head": head, "base": {"ref": base}, **over}


def run(conclusion: str | None = "success", **over: object) -> dict[str, object]:
    """A ``ci`` run on the head commit that names pull request 1 against ``main``."""
    return {
        "id": 1,
        "event": "pull_request",
        "path": CI,
        "status": "completed",
        "conclusion": conclusion,
        "head_sha": SHA,
        "head_branch": BRANCH,
        "head_repository": {"full_name": REPO},
        "created_at": "2026-11-12T10:00:00Z",
        "pull_requests": [{"number": 1, "base": {"ref": "main"}}],
        **over,
    }


def page(*runs: object) -> str:
    return json.dumps({"total_count": len(runs), "workflow_runs": list(runs)})


def ci(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    runs: str,
    *,
    pulls: str | None = None,
    events: str = "[]",
) -> tuple[int, str]:
    (tmp_path / "pulls.json").write_text(json.dumps([pull()]) if pulls is None else pulls)
    (tmp_path / "events.json").write_text(events)
    argv = [
        "ci",
        "--pulls",
        str(tmp_path / "pulls.json"),
        "--events",
        str(tmp_path / "events.json"),
    ]
    argv += ["--number", "1", "--sha", SHA, "--branch", BRANCH, "--base", "main"]
    code = forge_runs.main([*argv, "--repository", REPO], runs)
    return code, capsys.readouterr().out.strip()


def test_a_green_run_that_tested_this_pull_request_against_the_default_branch_counts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert ci(tmp_path, capsys, page(run())) == (0, "green")


def test_the_runs_arrive_on_standard_input(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO(page(run())))
    (tmp_path / "pulls.json").write_text(json.dumps([pull()]))
    (tmp_path / "events.json").write_text("[]")
    argv = [
        "ci",
        "--pulls",
        str(tmp_path / "pulls.json"),
        "--events",
        str(tmp_path / "events.json"),
    ]
    argv += ["--number", "1", "--sha", SHA, "--branch", BRANCH, "--base", "main"]
    assert forge_runs.main([*argv, "--repository", REPO]) == 0
    assert capsys.readouterr().out == "green\n"


@pytest.mark.parametrize("state", ["open", "closed"])
def test_a_second_pull_request_from_the_branch_against_another_base_leaves_ci_uncounted(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], state: str
) -> None:
    # Why: the forge lists on a run every open pull request with its head, so the green run of
    # the one against `weak`, whose merge used the `ci.yml` of `weak`, names this one too.
    both = [{"number": 2, "base": {"ref": "weak"}}, {"number": 1, "base": {"ref": "main"}}]
    pulls = json.dumps([pull(), pull(2, "weak", state=state)])
    code, said = ci(tmp_path, capsys, page(run(pull_requests=both)), pulls=pulls)
    assert code == 1
    assert said.startswith("#2 is a pull request from `feat/a` against `weak`")


def test_a_pull_request_from_a_fork_s_branch_of_the_same_name_is_not_this_branch(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    theirs = {"ref": BRANCH, "sha": SHA, "repo": {"full_name": "fork/repo"}}
    pulls = json.dumps([pull(), pull(2, "weak", head=theirs), pull(3, "weak", head={"ref": "x"})])
    assert ci(tmp_path, capsys, page(run()), pulls=pulls) == (0, "green")


@pytest.mark.parametrize("event", sorted(forge_runs.RETARGETS))
def test_a_retargeted_pull_request_leaves_ci_uncounted(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], event: str
) -> None:
    # Why: retargeting starts no run, so the green run is the one against the earlier base.
    events = json.dumps([{"event": "labeled"}]) + json.dumps([{"event": event}])
    code, said = ci(tmp_path, capsys, page(run()), events=events)
    assert (code, said) == (
        1,
        "its base branch was changed, so ci may have run against the earlier one",
    )
    assert ci(tmp_path, capsys, page(run()), events='[{"event": "labeled"}]') == (0, "green")


@pytest.mark.parametrize(
    "other",
    [
        {"pull_requests": []},
        {"pull_requests": None},
        {"pull_requests": [{"number": 1, "base": {"ref": "weak"}}]},
        {"pull_requests": [{"number": 2, "base": {"ref": "main"}}]},
        {"event": "push"},
        {"path": ".github/workflows/other.yml"},
        {"head_sha": "b" * 40},
        {"head_branch": "feat/b"},
        {"head_repository": {"full_name": "fork/repo"}},
        {"head_repository": None},
    ],
)
def test_a_run_that_did_not_test_this_pull_request_does_not_count(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], other: dict[str, object]
) -> None:
    code, said = ci(tmp_path, capsys, page(run(**other)))
    assert (code, said) == (1, f"no ci run on {SHA} names it against `main`")
    assert ci(tmp_path, capsys, page()) == (code, said)


def test_the_newest_counted_run_decides(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    old, new = "2026-11-12T10:00:00Z", "2026-11-12T11:00:00Z"
    red_last = page(run(created_at=old), run("failure", created_at=new))
    green_last = page(run(created_at=new), run("failure", created_at=old))
    later_id = page(run("failure", id=2), run(id=1))
    running = page(run(None, status="in_progress", created_at=new), run(created_at=old))
    assert ci(tmp_path, capsys, red_last) == (1, f"ci is not green on {SHA}")
    assert ci(tmp_path, capsys, green_last) == (0, "green")
    assert ci(tmp_path, capsys, later_id) == (1, f"ci is not green on {SHA}")
    assert ci(tmp_path, capsys, running) == (1, f"ci is not green on {SHA}")


@pytest.mark.parametrize(
    "now",
    [
        pull(state="closed"),
        pull(base="weak"),
        pull(head={"ref": BRANCH, "sha": "b" * 40, "repo": {"full_name": REPO}}),
        pull(7),
    ],
)
def test_a_pull_request_that_is_no_longer_what_the_job_read_is_left(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], now: dict[str, object]
) -> None:
    code, said = ci(tmp_path, capsys, page(run()), pulls=json.dumps([now]))
    assert (code, said.split(", so")[0] != "green") == (1, True)
    assert ci(tmp_path, capsys, page(run()), pulls="[]") == (
        1,
        f"it is not open on {SHA} against `main`",
    )


@pytest.mark.parametrize(
    ("runs", "pulls", "events"),
    [
        ("", None, "[]"),
        ("{}", None, "[]"),
        ("not json", None, "[]"),
        (page(run()) + page(run()), None, "[]"),
        (json.dumps({"total_count": 2, "workflow_runs": [run()]}), None, "[]"),
        (json.dumps({"total_count": 1, "workflow_runs": "x"}), None, "[]"),
        (page(run()), "", "[]"),
        (page(run()), '{"message": "Not Found"}', "[]"),
        (page(run()), json.dumps([pull()] * forge_runs.PAGE), "[]"),
        (page(run()), None, ""),
        (page(run()), None, '[] {"message": "rate limited"}'),
        (page(run()), None, "[] ["),
    ],
)
def test_an_answer_that_is_unreadable_or_cut_short_leaves_ci_uncounted(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], *, runs: str, pulls: str | None, events: str
) -> None:
    assert ci(tmp_path, capsys, runs, pulls=pulls, events=events) == (1, forge_runs.UNREADABLE)


def test_a_file_the_job_could_not_write_leaves_ci_uncounted(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    argv = ["ci", "--pulls", str(tmp_path / "none"), "--events", str(tmp_path / "none")]
    argv += ["--number", "1", "--sha", SHA, "--branch", BRANCH, "--base", "main"]
    assert forge_runs.main([*argv, "--repository", REPO], page(run())) == 1
    assert capsys.readouterr().out.strip() == forge_runs.UNREADABLE


def history(root: Path) -> dict[str, str]:
    """``main`` with three commits, and ``side`` one commit off its first."""
    repository(root)
    commits = {"first": git(root, "rev-parse", "HEAD")}
    git(root, "switch", "-q", "-c", "side")
    commits["side"] = commit(root, "feat(core): nobody merged this")
    git(root, "switch", "-q", "main")
    commits["second"] = commit(root, "feat(core): second")
    commits["tip"] = commit(root, "feat(core): third")
    return commits


def tier2(sha: str, conclusion: str = "success", **over: object) -> dict[str, object]:
    """A completed ``deep`` run of this repository, started by a push, on ``sha``."""
    return {
        "id": 1,
        "event": "push",
        "status": "completed",
        "conclusion": conclusion,
        "head_sha": sha,
        "head_branch": "main",
        "head_repository": {"full_name": REPO},
        "created_at": "2026-11-12T10:00:00Z",
        **over,
    }


def deep(root: Path, capsys: pytest.CaptureFixture[str], runs: str) -> str:
    argv = ["deep", "--tip", "main", "--repository", REPO, "--root", str(root)]
    assert forge_runs.main(argv, runs) == 0
    return capsys.readouterr().out.strip()


def test_tier_2_is_the_newest_result_on_the_commit_nearest_the_tip(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    at = history(tmp_path)
    later = "2026-11-12T12:00:00Z"
    assert deep(tmp_path, capsys, page()) == "none"
    assert deep(tmp_path, capsys, page(tier2(at["tip"]))) == "green"
    assert deep(tmp_path, capsys, page(tier2(at["tip"], "failure"))) == "red"
    assert deep(tmp_path, capsys, page(tier2(at["tip"], "timed_out"))) == "red"
    assert deep(tmp_path, capsys, page(tier2(at["first"]), tier2(at["second"], "failure"))) == "red"
    twice = page(tier2(at["tip"], "failure"), tier2(at["tip"], created_at=later, id=2))
    assert deep(tmp_path, capsys, twice) == "green"


def test_an_old_green_run_started_again_does_not_hide_a_red_one_nearer_the_tip(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    at = history(tmp_path)
    again = tier2(at["first"], event="workflow_dispatch", created_at="2026-11-12T12:00:00Z")
    assert deep(tmp_path, capsys, page(again, tier2(at["second"], "failure"))) == "red"


@pytest.mark.parametrize(
    "other",
    [
        {"head_sha": "side"},
        {"head_sha": "c" * 40},
        {"head_sha": "--help"},
        {"head_sha": None},
        {"event": "pull_request"},
        {"event": "workflow_run"},
        {"head_repository": {"full_name": "fork/repo"}},
        {"conclusion": "cancelled"},
        {"conclusion": "skipped"},
        {"status": "in_progress", "conclusion": None},
    ],
)
def test_a_run_that_is_not_tier_2_on_a_commit_of_the_default_branch_does_not_count(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], other: dict[str, object]
) -> None:
    # Why: a tag named `main` on a commit nobody merged gives a run whose branch reads `main`.
    at = history(tmp_path)
    if other.get("head_sha") == "side":
        other = {"head_sha": at["side"]}
    green = tier2(at["tip"], **other)
    assert deep(tmp_path, capsys, page(green)) == "none"
    assert deep(tmp_path, capsys, page(green, tier2(at["second"], "failure"))) == "red"


@pytest.mark.parametrize("runs", ["", "{}", "[]", '{"total_count": 3, "workflow_runs": []}'])
def test_tier_2_that_cannot_be_read_is_red(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], runs: str
) -> None:
    history(tmp_path)
    assert deep(tmp_path, capsys, runs) == "red"
