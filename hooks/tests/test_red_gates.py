""":mod:`red_gates`: a red gate's own output out of a ``parallel.py`` log, for the weekly issue."""

import shlex
import subprocess
import sys
from pathlib import Path

import pytest

import red_gates
from tests.support import PLUGIN_ROOT

PARALLEL = PLUGIN_ROOT / "scripts/parallel.py"
SPEAK = "import sys\nprint(open(sys.argv[1]).read(), end='')\nraise SystemExit(int(sys.argv[2]))\n"


class Tier:
    """Gates that print a file and exit with a code, run by the real ``parallel.py``."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.gates: list[str] = []
        (root / "speak.py").write_text(SPEAK, encoding="utf-8")

    def add(self, name: str, code: int, *lines: str) -> "Tier":
        """A gate called ``name``, whose command line holds none of what it prints.

        Its last word is ``name``, the entry point a row of a binding at the root may name.
        """
        (self.root / name).write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")
        speak = [sys.executable, str(self.root / "speak.py"), str(self.root / name)]
        self.gates.append(shlex.join([*speak, str(code), name]))
        return self

    def options(self) -> list[str]:
        """One gate at a time, run at the root, where a binding may say which gates wait."""
        return ["--jobs", "1", "--root", str(self.root)]

    def log(self) -> str:
        """The log of the run, as the weekly workflow's ``tee`` keeps it."""
        done = subprocess.run(  # noqa: S603 # -- a fixed argv; the runner is the plugin's own
            [sys.executable, str(PARALLEL), *self.options(), "--", *self.gates],
            capture_output=True,
            text=True,
            check=False,
        )
        return done.stdout + done.stderr


def test_the_output_of_a_red_gate_is_kept_and_a_green_gate_s_is_not(tmp_path: Path) -> None:
    log = (
        Tier(tmp_path)
        .add("one", 0, "green one speaks")
        .add("audit", 3, "the audit found CVE-1", "two packages are affected")
        .add("two", 0, *[f"green two line {n}" for n in range(100)])
        .log()
    )
    assert "green two line 99" in log
    report = red_gates.report(log)
    assert report is not None
    assert "the audit found CVE-1\ntwo packages are affected\nparallel: " in report
    assert report.count("==>") == 1
    assert report.splitlines()[0].startswith("==> ")
    assert "audit" in report.splitlines()[0]
    assert report.splitlines()[-1].split(": ")[2].startswith("red (exit 3) in ")
    assert "green one speaks" not in report
    assert "green two line" not in report


def test_every_red_gate_is_reported_in_the_order_the_gates_finished(tmp_path: Path) -> None:
    log = (
        Tier(tmp_path)
        .add("a", 1, "first reason")
        .add("b", 0, "fine")
        .add("c", 2, "second reason")
        .log()
    )
    report = red_gates.report(log)
    assert report is not None
    assert report.index("first reason") < report.index("second reason")
    assert report.count("==>") == 2
    assert "\nfine\n" not in report


def test_a_red_gate_that_printed_nothing_is_still_named(tmp_path: Path) -> None:
    report = red_gates.report(Tier(tmp_path).add("quiet", 5).log())
    assert report is not None
    assert report.splitlines()[0].startswith("==> ")
    assert report.splitlines()[-1].startswith("parallel: ")
    assert len(report.splitlines()) == 2
    assert red_gates.report("parallel: lint: red (exit 4) in 0.1s\n") == (
        "==> lint <==\nparallel: lint: red (exit 4) in 0.1s"
    )


def test_a_gate_that_judged_nothing_is_not_red_and_leaves_the_red_ones_whole(
    tmp_path: Path,
) -> None:
    (tmp_path / "docs/process").mkdir(parents=True)
    (tmp_path / "docs/process/binding.md").write_text(
        "# Binding\n\n## Gates\n\n| Question | Tool | Tier | Threshold source | Entry point |\n"
        "|---|---|---|---|---|\n"
        "| a question | later: code:source | 1 | | `just waits` |\n",
        encoding="utf-8",
    )
    log = (
        Tier(tmp_path)
        .add("first", 3, "the first red gate")
        .add("waits", 0, "later: code:source")
        .add("second", 4, "the second red gate")
        .log()
    )
    assert "not run yet (later: code:source)" in log
    report = red_gates.report(log)
    assert report is not None
    assert report.count("==>") == 2
    assert "the first red gate\nparallel: " in report
    assert "the second red gate\nparallel: " in report
    assert "later:" not in report


def test_a_header_before_the_previous_gate_belongs_to_another_gate() -> None:
    log = "==> a <==\nsaid a\nparallel: a: green in 1.0s\nparallel: a: red (exit 1) in 1.0s\n"
    assert red_gates.report(log) == "==> a <==\nparallel: a: red (exit 1) in 1.0s"


@pytest.mark.parametrize(
    "log", ["", "no gates here\n", "parallel: a: green in 1.0s\n", "parallel: a: red\n"]
)
def test_a_log_with_no_red_gate_has_nothing_to_report(log: str) -> None:
    assert red_gates.report(log) is None


def test_a_long_output_keeps_its_end_and_says_so(tmp_path: Path) -> None:
    lines = [f"line {n}" for n in range(100)]
    report = red_gates.report(Tier(tmp_path).add("long", 1, *lines).log(), lines=3)
    assert report is not None
    assert f"{red_gates.CLIPPED}\nline 97\nline 98\nline 99\nparallel: " in report
    assert "line 96" not in report


def test_a_huge_line_is_cut_to_fit_what_a_forge_accepts(tmp_path: Path) -> None:
    report = red_gates.report(Tier(tmp_path).add("wide", 1, "x" * 50000).log())
    assert report is not None
    assert len(report) < red_gates.CHARACTERS + 1500
    assert red_gates.CLIPPED in report


def test_the_command_prints_the_report_and_says_when_there_is_none(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    log = tmp_path / "weekly.txt"
    log.write_text(Tier(tmp_path).add("gate", 1, "why it failed").log(), encoding="utf-8")
    assert red_gates.main(["red_gates.py", str(log), "--lines", "5"]) == 0
    assert "why it failed" in capsys.readouterr().out
    log.write_text(Tier(tmp_path).add("fine", 0).log(), encoding="utf-8")
    assert red_gates.main(["red_gates.py", str(log)]) == 1
    assert red_gates.main(["red_gates.py", str(tmp_path / "missing.txt")]) == 1
    assert capsys.readouterr().out == ""
    with pytest.raises(SystemExit):
        red_gates.main(["red_gates.py", str(log), "--lines", "0"])
