"""``split.py``: judging a plan and filing its parts, locally and on the tracker (ADR 0046)."""

import json
from collections.abc import Sequence
from pathlib import Path

import pytest

import feature_preflight as preflight
import split
from tests.repos import repository

PARENT_REF = ".scratch/f/issues/01-a.md"
PARENT = "# 01: A\n\nStatus: ready-for-agent\n\n- [ ] It saves.\n- [ ] It lists.\n"
HALVES = {
    "parts": [
        {"title": "Save", "promises": ["It saves."]},
        {"title": "List", "promises": ["It lists."], "after": [0]},
    ]
}
Out = pytest.CaptureFixture[str]


def issue(body: str = PARENT, labels: tuple[str, ...] = ("ready-for-agent",)) -> str:
    return json.dumps(
        {
            "number": 7,
            "title": "A",
            "body": body,
            "labels": [{"name": name} for name in labels],
            "state": "OPEN",
        }
    )


def tracker(output: str | None) -> preflight.Runner:
    return lambda _args: output


def test_a_plan_that_is_not_the_shape_has_no_parts() -> None:
    assert split.parts_of([]) == []
    assert split.parts_of({"parts": "x"}) == []
    found = split.parts_of(
        {"parts": ["x", {"title": " T ", "promises": "no", "after": ["a", 1]}, {"title": "U"}]}
    )
    assert [(p.title, p.promises, p.after) for p in found] == [
        ("T", (), (1,)),
        ("U", (), (1,)),
    ]


def test_a_split_is_refused_for_each_reason_it_names(tmp_path: Path) -> None:
    root = repository(tmp_path, {PARENT_REF: PARENT})
    parent = preflight.local_ticket(PARENT_REF, PARENT)
    parts = split.parts_of(HALVES)
    assert split.problems(None, parts, "L", holds=True) == ["the ticket is not there"]
    assert any("does not carry" in p for p in split.problems(parent, parts, "L", holds=False))
    child = preflight.local_ticket("02-b.md", PARENT + "\nSplit from: 01-a.md\n")
    assert any("not split again" in p for p in split.problems(child, parts, "L", holds=True))
    assert any(
        "two parts at least" in p for p in split.problems(parent, parts[:1], "L", holds=True)
    )
    untitled = split.parts_of({"parts": [{"promises": ["It saves."]}, {"title": "x"}]})
    assert "part 1 has no title" in split.problems(parent, untitled, "L", holds=True)
    assert split.parent_now(root, "missing.md", tracker(None)) is None


def test_the_parent_of_an_issue_is_what_the_tracker_says(tmp_path: Path) -> None:
    root = repository(tmp_path)
    found = split.parent_now(root, "7", tracker(issue()))
    assert found is not None
    assert found.title == "A"


def test_the_parts_of_an_issue_are_filed_on_the_tracker_each_waiting_on_the_one_before(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = repository(tmp_path)
    calls: list[Sequence[str]] = []

    def made(args: Sequence[str]) -> str:
        calls.append(args)
        return f"https://example.test/o/r/issues/{10 + len(calls)}\n"

    monkeypatch.setattr(preflight, "run_gh", made)
    children, refused = split.apply(root, "7", HALVES, gh=tracker(issue()))
    assert (children, refused) == (["#11", "#12"], [])
    assert all(call[:2] == ["issue", "create"] and "ready-for-agent" in call for call in calls)
    body = (root / ".ratchet" / "split-body.md").read_text(encoding="utf-8")
    assert "Blocked by: #11" in body
    assert "Split from: 7" in body


def test_a_tracker_that_files_nothing_stops_the_split_and_says_how_far_it_got(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = repository(tmp_path)
    monkeypatch.setattr(preflight, "run_gh", lambda _args: None)
    with pytest.raises(RuntimeError, match="did not file part 1"):
        split.apply(root, "7", HALVES, gh=tracker(issue()))


def test_main_checks_then_applies_a_plan_and_refuses_one_it_cannot_read(
    tmp_path: Path, capsys: Out
) -> None:
    base = tmp_path / "repo"
    base.mkdir()
    root = repository(base, {PARENT_REF: PARENT})
    plan = tmp_path / "plan.json"
    argv = ["split", "check", PARENT_REF, "--plan", str(plan), "--root", str(root), "--offline"]
    assert split.main(argv) == 2, "no plan file"
    assert "does not read" in capsys.readouterr().out
    plan.write_text(json.dumps({"parts": HALVES["parts"][:1]}), encoding="utf-8")
    assert split.main(argv) == 1
    assert "refused" in capsys.readouterr().out
    plan.write_text(json.dumps(HALVES), encoding="utf-8")
    assert split.main(argv) == 0
    assert not (root / ".scratch/f/issues/02-save.md").exists(), "check files nothing"
    assert split.main([*argv[:1], "apply", *argv[2:]]) == 0
    assert json.loads(capsys.readouterr().out.splitlines()[-1])["children"] == [
        ".scratch/f/issues/02-save.md",
        ".scratch/f/issues/03-list.md",
    ]
