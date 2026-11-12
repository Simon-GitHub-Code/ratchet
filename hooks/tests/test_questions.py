"""``questions.py``: one line per answer, and a proposal once an answer has become a habit."""

from pathlib import Path

import pytest

import questions
from tests.repos import git, repository

Out = pytest.CaptureFixture[str]
LOG = ".ratchet/questions.tsv"


def record(root: Path, key: str, kind: str, *, took: str = "yes", skill: str = "adopt") -> int:
    argv = ["questions", "record", "--root", str(root), "--skill", skill, "--key", key]
    return questions.main([*argv, "--kind", kind, "--recommended", took])


def report(root: Path, capsys: Out) -> list[str]:
    capsys.readouterr()
    assert questions.main(["questions", "report", "--root", str(root)]) == 0
    return capsys.readouterr().out.splitlines()


def test_an_answer_is_one_line_that_no_commit_carries(
    tmp_path: Path, capsys: Out, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = repository(tmp_path)
    monkeypatch.setattr(questions, "today", lambda: "2026-11-12")
    assert record(root, "adopt.tools", "tooling") == 0
    assert record(root, "feature.short-links.1", "direction", took="no", skill="feature") == 0
    assert capsys.readouterr().out.splitlines() == [
        "questions: recorded adopt.tools",
        "questions: recorded feature.short-links.1",
    ]
    assert (root / LOG).read_text(encoding="utf-8").splitlines() == [
        "date\tskill\tkey\tkind\trecommended",
        "2026-11-12\tadopt\tadopt.tools\ttooling\tyes",
        "2026-11-12\tfeature\tfeature.short-links.1\tdirection\tno",
    ]
    assert git(root, "status", "--porcelain") == "", "the log dirties no work tree"


def test_a_key_that_is_not_a_plain_name_is_refused(tmp_path: Path, capsys: Out) -> None:
    assert record(tmp_path, "Adopt Tools", "tooling", skill="a\tb") == 2
    assert capsys.readouterr().out == (
        "questions: not a key of lower-case words joined by `.` or `-`: a\tb, Adopt Tools\n"
    )
    assert not (tmp_path / LOG).exists()
    assert len(questions.today()) == len("2026-11-12")


def test_three_recommended_answers_running_are_proposed_as_a_standing_answer(
    tmp_path: Path, capsys: Out
) -> None:
    root = repository(tmp_path)
    assert report(root, capsys) == []
    for _ in range(2):
        record(root, "adopt.tools", "tooling")
    assert report(root, capsys) == []
    record(root, "adopt.tools", "tooling")
    assert report(root, capsys) == [
        (
            "proposal: you have taken the recommendation for `adopt.tools` 3 times running: say the"
            " word and it becomes a standing answer in AGENTS.md, no longer asked"
        )
    ]
    record(root, "adopt.tools", "tooling", took="no")
    for _ in range(2):
        record(root, "adopt.tools", "tooling")
    assert report(root, capsys) == [], "an answer against the recommendation starts the count again"


def test_a_product_question_and_a_standing_answer_are_never_proposed(
    tmp_path: Path, capsys: Out
) -> None:
    root = repository(tmp_path)
    for key, kind in (("conjecture.fork", "direction"), ("init.remote", "irreversible")):
        for _ in range(4):
            record(root, key, kind)
    with (root / LOG).open("a", encoding="utf-8") as lines:
        lines.write("a line some other tool wrote\n")
    assert report(root, capsys) == [
        (
            "proposal: you have taken the recommendation for `init.remote` 4 times running: say the"
            " word and it becomes a standing answer in AGENTS.md, no longer asked"
        )
    ]
    agents = (
        "# P\n\n## Standing answers\n\n- `init.remote`: keep the repository local.\n\n## Next\n"
    )
    (root / "AGENTS.md").write_text(agents, encoding="utf-8")
    assert questions.standing(root) == {"init.remote"}
    assert report(root, capsys) == []
    (root / "AGENTS.md").write_text("# P\n\n## Next\n\n- `init.remote`\n", encoding="utf-8")
    assert questions.standing(root) == set()
