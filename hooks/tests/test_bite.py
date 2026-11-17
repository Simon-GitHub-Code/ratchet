"""``bite.py``: every shipped gate goes red on its violation and green without it."""

import os
import subprocess
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


# The smoke says it is sending, and its first request reaches the service a second later, as on a
# machine too busy to run it promptly.
LATE = (
    "import os, pathlib, subprocess, sys, time; "
    "pathlib.Path(os.environ['RATCHET_SENDING']).touch(); time.sleep(1); "
    "sys.exit(subprocess.run([sys.executable, *sys.argv[1:]]).returncode)"
)


def test_the_graceful_stop_bites_when_requests_reach_the_service_late(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (label, gate, clean, faulty) = next(
        entry for entry in bite.SERVICE_SCENARIOS if "drops requests" in entry[0]
    )

    def late(arguments: list[str]) -> list[str]:
        return [
            word
            for argument in arguments
            for word in (["-c", LATE, argument] if argument == bite.LOAD_SMOKE else [argument])
        ]

    monkeypatch.setattr(bite, "SCENARIOS", [])
    monkeypatch.setattr(bite, "SERVICE_SCENARIOS", [(label, gate, late(clean), late(faulty))])
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
        [("deaf", says_from_a_command, ["--url", "{stub}"], ["--url", "{stub}"])],
    )
    assert bite.bite(tmp_path) == 1
    assert capsys.readouterr().out == (
        "bite: asleep: DOES NOT BITE\n  the gate is green with the fault planted\n"
        "bite: broken: RED ON CLEAN STATE\n  the gate is red with no fault planted\n"
        "  why it is red\n"
        "bite: deaf: DOES NOT BITE\n  the gate is green with the fault planted\n"
        "  said by its command\n  said by the gate\n"
    )


def test_a_lister_that_fails_turns_the_comments_gate_red_and_says_what_it_said(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", os.devnull)
    bite.scratch(tmp_path)
    assert bite.run(bite.comments_gate, tmp_path, [])[0] == 0
    bite.lister_that_fails(tmp_path)
    code, said = bite.run(bite.comments_gate, tmp_path, [])
    assert code == 1
    assert "comments: the lister failed: the lister cannot read its files" in said


def says_from_a_command(_argv: list[str]) -> int:
    subprocess.run([sys.executable, "-c", "print('said by its command')"], check=True)
    sys.stdout.write("said by the gate\n")
    return 0


def test_main_runs_in_a_scratch_directory(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(bite, "SCENARIOS", [bite.SCENARIOS[0]])
    monkeypatch.setattr(bite, "SERVICE_SCENARIOS", [])
    assert bite.main(["bite"]) == 0
    assert "bite: markers: bites" in capsys.readouterr().out
