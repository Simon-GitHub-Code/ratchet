"""``bite.py``: every shipped gate goes red on its violation and green without it."""

from pathlib import Path

import pytest

import bite


def test_every_shipped_gate_bites(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert bite.bite(tmp_path) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == len(bite.SCENARIOS) + len(bite.SERVICE_SCENARIOS)
    assert all(line.endswith(": bites") for line in lines)


def test_a_gate_that_does_not_bite_fails_the_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        bite,
        "SCENARIOS",
        [
            ("asleep", lambda _argv: 0, bite.add_marker, []),
            ("broken", lambda _argv: 1, bite.add_marker, []),
        ],
    )
    monkeypatch.setattr(
        bite,
        "SERVICE_SCENARIOS",
        [("deaf", lambda _argv: 0, ["--url", "{stub}"], ["--url", "{stub}"])],
    )
    assert bite.bite(tmp_path) == 1
    assert capsys.readouterr().out == (
        "bite: asleep: DOES NOT BITE\nbite: broken: RED ON CLEAN STATE\nbite: deaf: DOES NOT BITE\n"
    )


def test_main_runs_in_a_scratch_directory(capsys: pytest.CaptureFixture[str]) -> None:
    assert bite.main(["bite"]) == 0
    assert "bite: markers: bites" in capsys.readouterr().out
