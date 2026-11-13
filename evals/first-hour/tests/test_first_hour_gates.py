from first_hour import gates
from first_hour.gates import Gate, Status

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


def test_output_with_no_runner_lines_has_no_gates() -> None:
    assert gates.parse("later: code:layer\nmerge: owner\n") == []
