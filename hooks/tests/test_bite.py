"""``bite.py``: every shipped gate goes red on its violation and green without it."""

import sys
from pathlib import Path

import pytest

import bite

SCENARIOS = [pytest.param(entry, id=entry[0]) for entry in bite.SCENARIOS]
SERVICES = [pytest.param(entry, id=entry[0]) for entry in bite.SERVICE_SCENARIOS]


@pytest.mark.parametrize("entry", SCENARIOS)
def test_every_shipped_gate_bites(
    entry: tuple[str, bite.Gate, bite.Violation, list[str]],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(bite, "SCENARIOS", [entry])
    monkeypatch.setattr(bite, "SERVICE_SCENARIOS", [])
    assert bite.bite(tmp_path) == 0


@pytest.mark.parametrize("entry", SERVICES)
def test_every_shipped_service_gate_bites(
    entry: tuple[str, bite.Gate, list[str], list[str]],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(bite, "SCENARIOS", [])
    monkeypatch.setattr(bite, "SERVICE_SCENARIOS", [entry])
    assert bite.bite(tmp_path) == 0


def test_a_gate_that_does_not_bite_fails_the_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        bite,
        "SCENARIOS",
        [
            ("asleep", lambda _argv: 0, bite.add_marker, []),
            (
                "broken",
                lambda _argv: sys.stdout.write("why it is red\n") and 1,
                bite.add_marker,
                [],
            ),
        ],
    )
    monkeypatch.setattr(
        bite,
        "SERVICE_SCENARIOS",
        [("deaf", lambda _argv: 0, ["--url", "{stub}"], ["--url", "{stub}"])],
    )
    assert bite.bite(tmp_path) == 1
    assert capsys.readouterr().out == (
        "bite: asleep: DOES NOT BITE\nbite: broken: RED ON CLEAN STATE\n  why it is red\n"
        "bite: deaf: DOES NOT BITE\n"
    )


def test_main_runs_in_a_scratch_directory(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(bite, "SCENARIOS", [bite.SCENARIOS[0]])
    monkeypatch.setattr(bite, "SERVICE_SCENARIOS", [])
    assert bite.main(["bite"]) == 0
    assert "bite: markers: bites" in capsys.readouterr().out
