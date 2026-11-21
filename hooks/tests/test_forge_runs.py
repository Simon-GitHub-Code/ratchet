import io
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

import forge_runs
import gatelib
import merge_ready
import unproven
from tests.repos import commit, git, repository
from tests.test_gatelib import STEPS, grown, undecodable

REPO = "owner/repo"
SHA = "a" * 40
BRANCH = "feat/a"
CI = ".github/workflows/ci.yml"


def pull(number: int = 1, base: str = "main", **over: object) -> dict[str, object]:
    head = {"ref": BRANCH, "sha": SHA, "repo": {"full_name": REPO}}
    return {"number": number, "state": "open", "head": head, "base": {"ref": base}, **over}


def run(conclusion: str | None = "success", **over: object) -> dict[str, object]:
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
        "--report",
        str(tmp_path / "left.md"),
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
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(page(run()).encode())))
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
    unread = io.TextIOWrapper(io.BytesIO(page(run()).encode() + b"\xff"), encoding="utf-8")
    monkeypatch.setattr(sys, "stdin", unread)
    assert forge_runs.main([*argv, "--repository", REPO]) == 1
    assert capsys.readouterr().out == f"{forge_runs.UNREADABLE}\n"


@pytest.mark.parametrize("state", ["open", "closed"])
def test_a_second_pull_request_from_the_branch_against_another_base_leaves_ci_uncounted(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], state: str
) -> None:
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


def comment_for(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    runs: str,
    *,
    pulls: str | None = None,
    events: str = "[]",
) -> tuple[int, str, str | None]:
    report = tmp_path / "left.md"
    report.write_text("a comment left by an earlier pull request", encoding="utf-8")
    code, said = ci(tmp_path, capsys, runs, pulls=pulls, events=events)
    return code, said, report.read_text(encoding="utf-8") if report.exists() else None


BOTH = [{"number": 1, "base": {"ref": "main"}}, {"number": 2, "base": {"ref": "weak"}}]
TWIN = json.dumps([pull(), pull(2, "weak", state="closed")])
TWIN_ROW = (
    "- `twin`: #2 is a pull request from `feat/a` against `weak`, so ci on this commit may have"
    " run against that branch\n"
)
RETARGET_ROW = (
    "- `retargeted`: its base branch was changed, so ci may have run against the earlier one\n"
)
TARGET_ROW = (
    "- `target`: the pull request targets `weak`, not the default branch `main`. Merge the pull"
    " request from `weak` first, and before it merges change this one's target to `main` on the"
    " pull request's page on the forge, which keeps it open; the owner then merges this one. Do"
    " not delete `weak` before this one is retargeted: the merge job deletes the branch of each"
    " pull request it merges, and a pull request whose target branch is gone is closed and cannot"
    " be reopened or retargeted\n"
)
UNNAMED_ROW = f"- `unnamed`: no ci run on {SHA} names it against `main`\n"


@pytest.mark.parametrize(
    ("row", "case"),
    [
        (TWIN_ROW, (page(run(pull_requests=BOTH)), TWIN, "[]")),
        (RETARGET_ROW, (page(run()), None, '[{"event": "base_ref_changed"}]')),
        (TARGET_ROW, (page(run()), json.dumps([pull(base="weak")]), "[]")),
        (UNNAMED_ROW, (page(run(pull_requests=[])), None, "[]")),
    ],
)
def test_a_reason_the_owner_must_act_on_is_written_as_the_judge_s_comment(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    *,
    row: str,
    case: tuple[str, str | None, str],
) -> None:
    runs, pulls, events = case
    code, said, written = comment_for(tmp_path, capsys, runs, pulls=pulls, events=events)
    judged = merge_ready.report([merge_ready.Reason("code", "text")], SHA)
    assert code == 1
    assert said == row.split("`: ", 1)[1].strip()
    head = f"{merge_ready.MARKER}\n**{merge_ready.OWNER}** at `{SHA[:12]}`. "
    assert written is not None
    assert judged.startswith(head)
    assert written.startswith(head)
    assert written.split("\n\n", 1)[1] == row
    assert judged.split("\n\n", 1)[1] == "- `code`: text\n"


def test_every_reason_the_owner_must_act_on_is_in_the_one_comment(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runs = page(run(pull_requests=BOTH))
    events = '[{"event": "base_ref_changed"}]'
    code, _, written = comment_for(tmp_path, capsys, runs, pulls=TWIN, events=events)
    assert code == 1
    assert written is not None
    assert written.split("\n\n", 1)[1] == TWIN_ROW + RETARGET_ROW


@pytest.mark.parametrize(
    ("runs", "pulls", "reason"),
    [
        (page(run("failure")), None, f"ci is not green on {SHA}"),
        (page(run(None, status="in_progress")), None, f"ci is not green on {SHA}"),
        (page(), None, f"no ci run on {SHA} names it"),
        (page(run(None, status="queued", pull_requests=[])), None, f"no ci run on {SHA} names"),
        (page(run()), json.dumps([pull(state="closed")]), f"it is not open on {SHA}"),
        (page(run()), json.dumps([pull(7)]), f"it is not open on {SHA}"),
        (page(run()), "[]", f"it is not open on {SHA}"),
        (page(run()), json.dumps([pull(base="weak", state="closed")]), f"it is not open on {SHA}"),
        ("not json", None, forge_runs.UNREADABLE),
    ],
)
def test_a_ci_red_or_pending_and_a_pull_request_that_moved_stay_a_line_in_the_log(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], *, runs: str, pulls: str | None, reason: str
) -> None:
    code, said, written = comment_for(tmp_path, capsys, runs, pulls=pulls)
    assert (code, said.startswith(reason), written) == (1, True, None)


def test_a_green_pull_request_writes_no_comment_and_a_stale_one_is_removed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert comment_for(tmp_path, capsys, page(run())) == (0, "green", None)


def test_a_red_ci_beside_a_twin_still_tells_the_owner_about_the_twin(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runs = page(run("failure", pull_requests=BOTH))
    code, _, written = comment_for(tmp_path, capsys, runs, pulls=TWIN)
    assert code == 1
    assert written is not None
    assert written.split("\n\n", 1)[1] == TWIN_ROW


def history(root: Path) -> dict[str, str]:
    repository(root)
    commits = {"first": git(root, "rev-parse", "HEAD")}
    git(root, "switch", "-q", "-c", "side")
    commits["side"] = commit(root, "feat(core): nobody merged this")
    git(root, "switch", "-q", "main")
    commits["second"] = commit(root, "feat(core): second")
    commits["tip"] = commit(root, "feat(core): third")
    return commits


def tier2(sha: str, conclusion: str = "success", **over: object) -> dict[str, object]:
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


def tier1(root: Path, capsys: pytest.CaptureFixture[str], runs: str) -> str:
    argv = ["tier1", "--tip", "main", "--repository", REPO, "--root", str(root)]
    assert forge_runs.main(argv, runs) == 0
    return capsys.readouterr().out.strip()


def test_tier_1_is_the_newest_ci_a_push_started_on_the_commit_nearest_the_tip(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    at = history(tmp_path)
    later = "2026-11-12T12:00:00Z"
    assert tier1(tmp_path, capsys, page()) == "none"
    assert tier1(tmp_path, capsys, page(tier2(at["tip"]))) == "green"
    assert tier1(tmp_path, capsys, page(tier2(at["tip"], "failure"))) == "red"
    behind = page(tier2(at["first"]), tier2(at["second"], "failure"))
    assert tier1(tmp_path, capsys, behind) == "red"
    twice = page(tier2(at["tip"], "failure"), tier2(at["tip"], created_at=later, id=2))
    assert tier1(tmp_path, capsys, twice) == "green"


@pytest.mark.parametrize("event", ["pull_request", "schedule", "workflow_dispatch"])
def test_a_ci_run_no_push_started_is_not_tier_1_on_the_default_branch(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], event: str
) -> None:
    at = history(tmp_path)
    red = tier2(at["tip"], "failure", event=event)
    assert tier1(tmp_path, capsys, page(red)) == "none"
    assert tier1(tmp_path, capsys, page(red, tier2(at["second"]))) == "green"


@pytest.mark.parametrize("command", ["tier1", "deep"])
def test_more_runs_than_one_page_holds_still_read_the_newest_page(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], command: str
) -> None:
    at = history(tmp_path)
    argv = [command, "--tip", "main", "--repository", REPO, "--root", str(tmp_path)]

    def state(*runs: object) -> str:
        assert forge_runs.main(argv, json.dumps({"total_count": 250, "workflow_runs": runs})) == 0
        return capsys.readouterr().out.strip()

    assert state(tier2(at["tip"]), tier2(at["second"], "failure")) == "green"
    assert state(tier2(at["tip"], "failure"), tier2(at["second"])) == "red"
    assert state(tier2(at["tip"], "cancelled")) == "red"
    assert state() == "red"


def test_help_prints_the_whole_description(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        forge_runs.main(["--help"])
    shown = capsys.readouterr().out
    assert "prints ``green``" in shown


def answered(
    root: Path, capsys: pytest.CaptureFixture[str], runs: str
) -> tuple[unproven.Deep, str]:
    target = root / "answer.json"
    argv = ["deep", "--tip", "main", "--repository", REPO, "--root", str(root), "--answer"]
    assert forge_runs.main([*argv, str(target)], runs) == 0
    return unproven.read(target), capsys.readouterr().out


@pytest.mark.parametrize(
    "over",
    [
        {"status": "queued"},
        {"status": "pending"},
        {"status": "requested"},
        {"status": "waiting"},
        {"status": "in_progress"},
        {"status": "a word the forge adds later"},
        {"status": None},
    ],
)
def test_an_answer_reads_every_status_but_completed_as_running(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], over: dict[str, object]
) -> None:
    at = history(tmp_path)
    going = tier2(at["tip"], None, html_url="https://forge.example/runs/7", **over)
    read, out = answered(tmp_path, capsys, page(going, tier2(at["second"], id=2)))
    assert read.runs is not None
    assert [(run.commit, run.state, run.name) for run in read.runs] == [
        (at["tip"], "running", "https://forge.example/runs/7"),
        (at["second"], "green", "run 2"),
    ]
    assert out == ""
    assert deep(tmp_path, capsys, page(going, tier2(at["second"], id=2))) == "green"


@pytest.mark.parametrize(
    ("conclusion", "state"),
    [
        ("success", ["green"]),
        ("failure", ["red"]),
        ("timed_out", ["red"]),
        (None, ["red"]),
        ("cancelled", []),
        ("skipped", []),
    ],
)
def test_an_answer_keeps_each_finished_run_but_a_cancelled_or_skipped_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], *, conclusion: str | None, state: list[str]
) -> None:
    at = history(tmp_path)
    read, _ = answered(tmp_path, capsys, page(tier2(at["tip"], conclusion)))
    assert [run.state for run in read.runs or ()] == state


@pytest.mark.parametrize(
    "other",
    [
        {"head_sha": "--help"},
        {"head_sha": None},
        {"event": "pull_request"},
        {"head_repository": {"full_name": "fork/repo"}},
    ],
)
def test_an_answer_drops_a_run_that_is_not_tier_2_of_this_repository(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], other: dict[str, object]
) -> None:
    at = history(tmp_path)
    read, _ = answered(tmp_path, capsys, page(tier2(at["tip"], **other)))
    assert read.runs == ()


def test_the_answer_asks_for_a_start_when_nothing_proves_the_tip(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    at = history(tmp_path)
    read, out = answered(tmp_path, capsys, page(tier2(at["second"])))
    assert out == "start\n"
    assert read.start
    assert answered(tmp_path, capsys, page())[1] == "start\n"
    assert answered(tmp_path, capsys, page(tier2(at["tip"])))[1] == ""
    going = tier2(at["tip"], None, status="in_progress", id=2)
    assert answered(tmp_path, capsys, page(going, tier2(at["second"])))[1] == ""


@pytest.mark.parametrize(
    ("runs", "known"),
    [
        ("", False),
        ('{"total_count": 3, "workflow_runs": []}', False),
        ("A", False),
        ("B", True),
        ("C", True),
    ],
)
def test_an_answer_the_forge_cut_short_is_known_only_with_a_finished_run_on_the_chain(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    *,
    runs: str,
    known: bool,
) -> None:
    at = history(tmp_path)
    going = tier2(at["tip"], None, status="queued")
    pages = {
        "A": [going, tier2(at["side"])],
        "B": [going, tier2(at["second"], "failure")],
        "C": [tier2(at["tip"])],
    }
    if runs in pages:
        runs = json.dumps({"total_count": 250, "workflow_runs": pages[runs]})
    read, out = answered(tmp_path, capsys, runs)
    assert (read.runs is not None) == known
    assert out == ""


def spoken(
    root: Path, capsys: pytest.CaptureFixture[str], runs: str, *, sha: str, state: str
) -> list[str]:
    argv = ["speaks", "--tip", "main", "--repository", REPO, "--root", str(root)]
    assert forge_runs.main([*argv, "--commit", sha, "--state", state], runs) == 0
    return capsys.readouterr().out.splitlines()


def test_a_deep_run_says_which_merges_it_speaks_for(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    at = history(tmp_path)
    first, second, tip = (at[name][:12] for name in ("first", "second", "tip"))
    itself = tier2(at["tip"], None, status="in_progress", id=3)
    red = page(itself, tier2(at["second"], "failure", id=2), tier2(at["first"]))
    assert spoken(tmp_path, capsys, red, sha=at["tip"], state="red") == [
        f"deep: red at `{tip}`. The merges of the default branch it speaks for:",
        f"- `{tip}` feat(core): third",
        f"- `{second}` feat(core): second",
    ]
    green = page(itself, tier2(at["second"], id=2))
    assert spoken(tmp_path, capsys, green, sha=at["tip"], state="green") == [
        f"deep: green at `{tip}`. The merges of the default branch it speaks for:",
        f"- `{tip}` feat(core): third",
    ]
    assert spoken(tmp_path, capsys, page(), sha=at["first"], state="red") == [
        f"deep: red at `{first}`. The merges of the default branch it speaks for:",
        f"- `{first}` feat(core): the clean state",
    ]
    assert spoken(tmp_path, capsys, page(), sha=at["side"], state="red") == [
        f"deep: red at `{at['side'][:12]}`. The merges of the default branch it speaks for:",
    ]
    assert spoken(tmp_path, capsys, "", sha=at["tip"], state="red") == [
        (
            f"deep: red at `{tip}`. The forge's answer could not be read, so which merges of the"
            " default branch it speaks for is not known."
        ),
    ]


NESTED = "[" * 200_000 + "]" * 200_000


def test_runs_nested_past_the_interpreters_limit_are_an_answer_that_could_not_be_read(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    history(tmp_path)
    assert deep(tmp_path, capsys, NESTED) == "red"
    assert tier1(tmp_path, capsys, NESTED) == "red"
    assert ci(tmp_path, capsys, NESTED) == (1, forge_runs.UNREADABLE)
    assert ci(tmp_path, capsys, page(run()), pulls=NESTED) == (1, forge_runs.UNREADABLE)
    read, out = answered(tmp_path, capsys, NESTED)
    assert (read.runs, out) == (None, "")


@pytest.mark.parametrize(
    ("over", "states", "named"),
    [
        ({"html_url": ["https://forge.example/runs/7"]}, ["green"], "run 1"),
        ({"event": ["push"]}, [], None),
        ({"conclusion": ["success"]}, ["red"], "run 1"),
        ({"id": [1]}, ["green"], "run ?"),
        ({"html_url": "https://forge.example/runs/7\ud800"}, ["green"], "run 1"),
        ({"html_url": "https://forge.example/runs/7\nbehind: a line"}, ["green"], "run 1"),
        ({"html_url": None, "id": "7\nbehind: a line"}, ["green"], "run ?"),
        ({"html_url": None, "id": "7"}, ["green"], "run 7"),
    ],
)
def test_an_answer_reads_a_field_of_the_wrong_type_without_trusting_it(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    *,
    over: dict[str, object],
    states: list[str],
    named: str | None,
) -> None:
    at = history(tmp_path)
    read, _ = answered(tmp_path, capsys, page(tier2(at["tip"], **over)))
    assert [run.state for run in read.runs or ()] == states
    assert [run.name for run in read.runs or ()] == ([named] if named else [])


@pytest.mark.parametrize(
    "created",
    [
        ["2026-11-12T10:00:00Z"],
        None,
        "yesterday",
        "2026-11-12T10:00:00",
        "2026-W46-4T10:00:00Z",
        "20261112T100000Z",
        "2026-11-12 10:00:00Z",
        "2026-11-12T10:00Z",
    ],
)
def test_an_answer_with_a_time_that_is_not_an_instant_is_not_known(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], created: object
) -> None:
    at = history(tmp_path)
    read, _ = answered(tmp_path, capsys, page(tier2(at["tip"], created_at=created)))
    assert read.runs is None


@pytest.mark.parametrize("field", ["conclusion", "event"])
@pytest.mark.parametrize("command", ["deep", "tier1"])
def test_a_field_that_is_a_list_or_an_object_is_a_page_that_could_not_be_read(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], *, field: str, command: str
) -> None:
    at = history(tmp_path)
    reader = deep if command == "deep" else tier1
    for odd in ([1], {"a": 1}):
        odd_run = tier2(at["tip"], **{field: odd})
        assert reader(tmp_path, capsys, page(odd_run, tier2(at["second"]))) == "red"


STRICT = {"PYTHONIOENCODING": "utf-8:strict", "LC_ALL": "C.UTF-8"}
C_LOCALE = {"LC_ALL": "C"}


def run_script(
    root: Path, argv: list[str], data: bytes, *, locale: dict[str, str]
) -> subprocess.CompletedProcess[bytes]:
    kept = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("PYTHON", "LC_", "LANG"))
    }
    script = Path(forge_runs.__file__)
    return subprocess.run(  # noqa: S603 # -- a fixed argv, no shell
        [sys.executable, str(script), *argv],
        input=data,
        capture_output=True,
        check=False,
        cwd=root,
        env=kept | locale,
    )


def hostile(at: dict[str, str]) -> dict[str, bytes]:
    lone = page(tier2(at["tip"], html_url="https://forge.example/runs/\ud800"))
    return {
        "after the page": page(tier2(at["tip"])).encode() + b"\xff",
        "inside a string": page(tier2(at["tip"], html_url="u")).encode().replace(b'"u"', b'"\xff"'),
        "a lone surrogate": lone.encode(),
        "latin-1": page(tier2(at["tip"], html_url="u")).encode().replace(b'"u"', b'"caf\xe9"'),
    }


@pytest.mark.parametrize("locale", [STRICT, C_LOCALE], ids=["strict", "C"])
def test_bytes_that_are_not_utf_8_on_standard_input_are_read_as_the_c_locale_reads_them(
    tmp_path: Path, locale: dict[str, str]
) -> None:
    at = history(tmp_path)
    tip = undecodable(tmp_path, at["tip"], b"feat(core): caf\xe9")
    where = ["--tip", tip, "--repository", REPO, "--root", str(tmp_path)]
    target = tmp_path / "answer.json"
    for kind, data in hostile(at).items():
        answered = run_script(
            tmp_path, ["deep", *where, "--answer", str(target)], data, locale=locale
        )
        assert (answered.returncode, answered.stderr) == (0, b""), kind
        read = unproven.read(target)
        if kind == "after the page":
            assert read.runs is None, kind
        else:
            assert [run.name for run in read.runs or ()] == ["run 1"], kind
        spoke = run_script(
            tmp_path, ["speaks", *where, "--commit", tip, "--state", "red"], data, locale=locale
        )
        assert (spoke.returncode, spoke.stderr) == (0, b""), kind
        assert spoke.stdout.decode().startswith(f"deep: red at `{tip[:12]}`."), kind
        said = b"red\n" if kind == "after the page" else b"green\n"
        for word in ("deep", "tier1"):
            legacy = run_script(tmp_path, [word, *where], data, locale=locale)
            assert (legacy.returncode, legacy.stderr, legacy.stdout) == (0, b"", said), (kind, word)


LOCALES = {"C": C_LOCALE, "C.UTF-8": {"LC_ALL": "C.UTF-8"}, "POSIX": {"LC_ALL": "POSIX"}}


def spoilt(document: str, field: str) -> bytes:
    marker = f'"{field}": "'.encode()
    assert marker in document.encode()
    return document.encode().replace(marker, marker + b"\xe9")


def ci_script(root: Path, data: bytes, locale: dict[str, str]) -> tuple[int, bytes, bytes]:
    (root / "pulls.json").write_text(json.dumps([pull()]))
    (root / "events.json").write_text("[]")
    argv = ["ci", "--pulls", str(root / "pulls.json"), "--events", str(root / "events.json")]
    argv += ["--number", "1", "--sha", SHA, "--branch", BRANCH, "--base", "main"]
    done = run_script(root, [*argv, "--repository", REPO], data, locale=locale)
    return done.returncode, done.stdout, done.stderr


@pytest.mark.parametrize("locale", [*LOCALES.values(), STRICT], ids=[*LOCALES, "strict"])
def test_bytes_that_are_not_utf_8_in_a_field_no_logic_reads_change_no_answer(
    tmp_path: Path, locale: dict[str, str]
) -> None:
    at = history(tmp_path)
    where = ["--tip", at["tip"], "--repository", REPO, "--root", str(tmp_path)]
    data = spoilt(page(tier2(at["tip"], display_title="cafe")), "display_title")
    for word in ("deep", "tier1"):
        done = run_script(tmp_path, [word, *where], data, locale=locale)
        assert (done.returncode, done.stdout, done.stderr) == (0, b"green\n", b""), word
    tested = spoilt(page(run(display_title="cafe")), "display_title")
    assert ci_script(tmp_path, tested, locale) == (0, b"green\n", b"")


UNNAMED = f"no ci run on {SHA} names it against `main`\n".encode()
READ = {
    "conclusion": ("red", (1, f"ci is not green on {SHA}\n".encode()), [("red", "u")]),
    "event": ("none", (1, UNNAMED), []),
    "head_sha": ("none", (1, UNNAMED), []),
    "created_at": ("green", (0, b"green\n"), None),
    "html_url": ("green", (0, b"green\n"), [("green", "run 1")]),
}


@pytest.mark.parametrize("locale", [C_LOCALE, STRICT], ids=["C", "strict"])
@pytest.mark.parametrize("field", READ)
def test_bytes_that_are_not_utf_8_in_a_field_the_logic_reads_are_a_value_it_cannot_trust(
    tmp_path: Path, field: str, locale: dict[str, str]
) -> None:
    word, (code, said), kept = READ[field]
    at = history(tmp_path)
    where = ["--tip", at["tip"], "--repository", REPO, "--root", str(tmp_path)]
    data = spoilt(page(tier2(at["tip"], html_url="u")), field)
    for legacy in ("deep", "tier1"):
        done = run_script(tmp_path, [legacy, *where], data, locale=locale)
        assert (done.returncode, done.stdout, done.stderr) == (0, f"{word}\n".encode(), b"")
    target = tmp_path / "answer.json"
    done = run_script(tmp_path, ["deep", *where, "--answer", str(target)], data, locale=locale)
    assert (done.returncode, done.stderr) == (0, b"")
    runs = unproven.read(target).runs
    assert kept == (None if runs is None else [(run.state, run.name) for run in runs])
    asked = ["speaks", *where, "--commit", at["tip"], "--state", "red"]
    spoke = run_script(tmp_path, asked, data, locale=locale)
    assert (spoke.returncode, spoke.stderr) == (0, b"")
    tested = spoilt(page(run(html_url="u")), field)
    assert ci_script(tmp_path, tested, locale) == (code, said, b"")


def test_speaks_refuses_a_commit_that_is_not_a_commit_name(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    history(tmp_path)
    argv = ["speaks", "--tip", "main", "--repository", REPO, "--root", str(tmp_path)]
    for odd in ("main", "\udcff" * 40, "A" * 40, "a" * 40 + "\n"):
        with pytest.raises(SystemExit) as stopped:
            forge_runs.main([*argv, "--commit", odd, "--state", "red"], page())
        assert stopped.value.code == 2
        assert "--commit" in capsys.readouterr().err


def pull_merged(root: Path) -> dict[str, str]:
    repository(root)
    shas = {"base": git(root, "rev-parse", "HEAD")}
    git(root, "switch", "-q", "-c", "earlier")
    shas["remote"] = commit(root, "feat(core): the default branch's tip then")
    git(root, "switch", "-q", "main")
    shas["local"] = commit(root, "feat(core): committed on a main that moved")
    git(root, "merge", "-q", "--no-ff", "-m", "Merge the default branch", "earlier")
    shas["pulled"] = git(root, "rev-parse", "HEAD")
    return shas


def test_a_red_a_pull_merge_left_off_the_line_holds_and_speaks_from_that_merge(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    shas = pull_merged(tmp_path)
    red = page(tier2(shas["remote"], "failure", id=2), tier2(shas["base"]))
    assert deep(tmp_path, capsys, red) == "red"
    read, _ = answered(tmp_path, capsys, red)
    line = gatelib.chain(tmp_path, "main", [run.commit for run in read.runs or ()])
    hold = unproven.held(line, read, gated=False, bound=None)
    assert hold is not None
    assert hold.code == "tier2"
    pulled, local = shas["pulled"][:12], shas["local"][:12]
    assert spoken(tmp_path, capsys, red, sha=shas["remote"], state="red") == [
        f"deep: red at `{shas['remote'][:12]}`. The merges of the default branch it speaks for:",
        f"- `{pulled}` Merge the default branch",
        f"- `{local}` feat(core): committed on a main that moved",
    ]


RUNS = st.lists(
    st.tuples(
        st.integers(0, 30),
        st.sampled_from(["completed", "completed", "in_progress"]),
        st.sampled_from(["success", "failure", "cancelled", None]),
        st.integers(0, 3),
    ),
    max_size=6,
)


@settings(max_examples=60)
@given(STEPS, RUNS, st.booleans())
def test_a_red_that_todays_deep_reader_sees_on_any_history_holds(
    steps: list[tuple[str, int, int]],
    runs: list[tuple[int, str, str | None, int]],
    cut: bool,  # noqa: FBT001
) -> None:
    with tempfile.TemporaryDirectory() as scratch:
        root = Path(scratch) / "repo"
        repository(root)
        shas = grown(root, steps)
        listed = [
            tier2(
                shas[index % len(shas)] if index < 2 * len(shas) else "f" * 40,
                conclusion,
                status=status,
                created_at=f"2026-11-20T09:{minute * 20:02d}:00Z",
                id=number,
            )
            for number, (index, status, conclusion, minute) in enumerate(runs)
        ]
        text = json.dumps({"total_count": len(listed) + cut, "workflow_runs": listed})
        tip, events = shas[-1], forge_runs.DEEP_EVENTS
        today = forge_runs.on_the_tip(text, root=root, tip=tip, repository=REPO, events=events)
        target = Path(scratch) / "answer.json"
        argv = ["deep", "--tip", tip, "--repository", REPO, "--root", str(root)]
        assert forge_runs.main([*argv, "--answer", str(target)], text) == 0
        read = unproven.read(target)
        line = gatelib.chain(root, tip, [run.commit for run in read.runs or ()])
        if today == "red":
            assert unproven.held(line, read, gated=False, bound=None) is not None, text
