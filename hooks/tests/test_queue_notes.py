from merge_ready import Reason, report
from queue_notes import code_of, mechanical


def rows(reasons: list[Reason]) -> list[str]:
    return [line for line in report(reasons, "a" * 40).splitlines() if line.startswith("- ")]


def test_the_judges_pull_request_rows_read_as_their_codes() -> None:
    behind = Reason("behind", "`main` moved: update the branch: run `just update`")
    gates = Reason("gates", "ci is red: see the run")
    owner = Reason("guard", "a guard path changed: `quality/thresholds.toml`")
    assert [code_of(line) for line in rows([behind, gates, owner])] == ["behind", "gates", "guard"]
    assert [code_of(str(reason)) for reason in (behind, gates, owner)] == [
        "behind",
        "gates",
        "guard",
    ]


def test_only_mechanical_rows_leave_a_ticket_to_the_drain() -> None:
    behind, gates = Reason("behind", "main moved: x"), Reason("gates", "ci: red")
    owner = Reason("guard", "a guard path changed")
    assert mechanical(rows([behind, gates]))
    assert mechanical([str(behind), str(gates)])
    assert not mechanical(rows([owner]))
    assert not mechanical(rows([behind, owner]))
    assert not mechanical([])
