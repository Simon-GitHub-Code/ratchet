import subprocess
import sys
from pathlib import Path

from first_hour import gates
from first_hour.gates import Gate, Status

PARALLEL = Path(__file__).resolve().parents[3] / "scripts" / "parallel.py"

RUN = """\
parallel: fmt-check: started
==> lint <==
lint: no TypeScript in the tree yet, so there is nothing to judge
parallel: lint: green in 0.1s
==> imports <==
later: code:layer
parallel: imports: green in 0.1s
==> bite-all <==
bite: smoke: a service that drops requests in flight when stopped: RED ON CLEAN STATE
parallel: bite-all: red (exit 1) in 23.0s
later: printed after the section closed
parallel: 3 of 3 gates finished in 23.1s
parallel: red
"""


def test_each_gate_reads_as_green_red_or_waiting_for_its_trigger() -> None:
    assert gates.parse(RUN) == [
        Gate("lint", Status.GREEN),
        Gate("imports", Status.LATER, "code:layer"),
        Gate("bite-all", Status.RED),
    ]
    assert gates.red(RUN) == ["bite-all"]


NEWER = """\
parallel: imports: judged nothing (later: code:layer) in 0.1s
parallel: load: judged nothing (none: not a service or a job) in 0.1s
parallel: lint: green in 2.0s
"""


def test_a_newer_runner_says_judged_nothing_and_why() -> None:
    assert gates.parse(NEWER) == [
        Gate("imports", Status.LATER, "code:layer"),
        Gate("load", Status.NONE, "not a service or a job"),
        Gate("lint", Status.GREEN),
    ]


def test_the_plugins_runner_reads_as_each_gate_stood(tmp_path: Path) -> None:
    bodies = {
        "imports": "echo later: code:layer",
        "load": "echo none: not a service or a job",
        "lint": "true",
        "bite": "exit 1",
        "stop": "exit 3",
    }
    for name, body in bodies.items():
        (tmp_path / name).write_text(f"{body}\n", encoding="utf-8")
    runner = [sys.executable, str(PARALLEL), "--runner", "sh", "--jobs", "1", "--root", "."]
    done = subprocess.run(
        [*runner, "--", *bodies],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert gates.parse(done.stdout + done.stderr) == [
        Gate("imports", Status.LATER, "code:layer"),
        Gate("load", Status.NONE, "not a service or a job"),
        Gate("lint", Status.GREEN),
        Gate("bite", Status.RED),
        Gate("stop", Status.UNJUDGED),
    ]


def test_output_with_no_runner_lines_has_no_gates() -> None:
    assert gates.parse("later: code:layer\nmerge: owner\n") == []
