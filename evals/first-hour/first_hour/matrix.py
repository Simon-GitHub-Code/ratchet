"""The matrix: one first-hour run per persona, one after another, and one table of all of them.

Runs are sequential on purpose: each is a whole Claude Code session with builds and a sandbox, and
two at once on one machine move each other's minutes (the README's noise section). Each run is its
own ``run.py run`` process in its own directory under the matrix directory, so a run that fails
leaves a row that says so and the next one still starts. The table is rewritten after every run, so
a matrix that is stopped keeps what it finished.
"""

from __future__ import annotations

import json
import signal
import subprocess
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from pathlib import Path

    from first_hour.persona import Persona

HEADER = (
    "Persona",
    "Stack",
    "Harness",
    "Stopped",
    "Min to first feature merge",
    "Min to first usable merge",
    "Questions",
    "Product / other",
    "Guard prompts",
    "Automatic merges",
    "Architecture",
    "Stack leaks",
    "Usable app merged",
)
STOP_WAIT_S = 180.0


@dataclass(frozen=True)
class Settings:
    """What every run of the matrix is given, besides its persona and directory."""

    plugin: Path
    minutes: float
    usd: float
    model: str
    owner_model: str
    toolchains: tuple[Path, ...] = ()


def command(run_py: Path, settings: Settings, *, persona: str, directory: Path) -> list[str]:
    """The command that drives one persona's run."""
    return [
        "uv",
        "run",
        str(run_py),
        "run",
        "--plugin",
        str(settings.plugin),
        "--dir",
        str(directory),
        "--persona",
        persona,
        "--minutes",
        f"{settings.minutes:g}",
        "--usd",
        f"{settings.usd:g}",
        "--model",
        settings.model,
        "--owner-model",
        settings.owner_model,
        *(w for t in settings.toolchains for w in ("--toolchain", str(t))),
    ]


def _at(data: Mapping[str, object], path: str) -> object:
    value: object = data
    for key in path.split("."):
        value = value.get(key) if isinstance(value, dict) else None
    return value


def _cell(value: object) -> str:
    return "n/a" if value is None else str(value).replace("|", "/")


def _yn(value: object) -> str:
    return "yes" if value else "no"


def architecture_cell(metrics: Mapping[str, object]) -> str:
    """The architecture measures of one run on one line."""
    if not _at(metrics, "architecture.read"):
        return f"asked {_yn(_at(metrics, 'architecture.asked'))}; repository not read"
    done = _cell(_at(metrics, "architecture.features.with_design"))
    specs = _cell(_at(metrics, "architecture.features.specs"))
    return (
        f"asked {_yn(_at(metrics, 'architecture.asked'))}"
        f" ({_cell(_at(metrics, 'architecture.options'))} options,"
        f" recommended {_yn(_at(metrics, 'architecture.recommended'))});"
        f" record {_yn(_at(metrics, 'architecture.adr.complete'))};"
        f" parts {_cell(_at(metrics, 'architecture.parts_declared'))};"
        f" design {done}/{specs};"
        f" axis F {_cell(_at(metrics, 'architecture.reviews.design_issues'))} issues,"
        f" {_cell(_at(metrics, 'architecture.reviews.mentions'))} mentions;"
        f" change questions {_cell(_at(metrics, 'architecture.change_questions'))}"
    )


def harness_cell(metrics: Mapping[str, object]) -> str:
    """The commit of the eval code that drove the run, marked when its tree had changes."""
    commit = _at(metrics, "meta.harness.commit")
    if not commit:
        return "not recorded"
    given = _at(metrics, "meta.toolchains")
    count = len(given) if isinstance(given, list) else 0
    toolchains = f", {count} toolchain{'s' * (count > 1)}" if count else ""
    return str(commit)[:7] + (" (dirty)" if _at(metrics, "meta.harness.dirty") else "") + toolchains


def stopped(metrics: Mapping[str, object], error: str) -> str:
    """Why the run stopped and when, led by its exit code when that was not zero."""
    reason, minute = _cell(_at(metrics, "stop.reason")), _cell(_at(metrics, "stop.minute"))
    return (f"{error}; " if error else "") + f"{reason} at {minute} min"


def row(persona: Persona, metrics: Mapping[str, object] | None, error: str = "") -> list[str]:
    """One persona's cells; a run with no measurements says why in its first cells."""
    if metrics is None:
        why = error or "no measurements"
        return [persona.name, _cell(persona.stack or "unstated"), "n/a", why] + ["n/a"] * (
            len(HEADER) - 4
        )
    merges = _at(metrics, "merges")
    total = len(merges) if isinstance(merges, list) else 0
    hand = _at(metrics, "hand_merges")
    usable = _at(metrics, "minutes.to_first_usable_merge") is not None
    leaks = _at(metrics, "stack_leaks.count")
    names = _at(metrics, "stack_leaks.names")
    listed = ", ".join(map(str, names)) if isinstance(names, list) and names else ""
    return [
        persona.name,
        _cell(persona.stack or "unstated"),
        harness_cell(metrics),
        stopped(metrics, error),
        _cell(_at(metrics, "minutes.to_first_feature_merge")),
        _cell(_at(metrics, "minutes.to_first_usable_merge")),
        _cell(_at(metrics, "questions.total")),
        " / ".join(_cell(_at(metrics, f"questions.{k}")) for k in ("product", "default_able")),
        _cell(_at(metrics, "prompts.guard")),
        _cell(total - int(str(hand or 0))),
        architecture_cell(metrics),
        "not read" if leaks is None else f"{leaks}" + (f" ({listed})" if listed else ""),
        _yn(usable),
    ]


def table(rows: Sequence[Sequence[str]]) -> str:
    """The rows as a Markdown table."""
    lines = ["| " + " | ".join(HEADER) + " |", "| " + " | ".join("---" for _ in HEADER) + " |"]
    lines += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(lines) + "\n"


def read_metrics(directory: Path) -> Mapping[str, object] | None:
    """A run's ``metrics.json``, or ``None`` when the run wrote none."""
    try:
        data = json.loads((directory / "metrics.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def run_all(
    personas: Sequence[Persona],
    root: Path,
    launch: Callable[[Persona, Path], int],
) -> list[list[str]]:
    """Run each persona in turn through ``launch``, writing ``matrix.md`` and ``matrix.json``.

    ``launch`` runs one persona in its directory and gives the exit code. A run that fails to
    start or ends in an error still gets a row, and the next persona still runs.
    """
    rows: list[list[str]] = []
    raw: dict[str, object] = {}
    root.mkdir(parents=True, exist_ok=True)
    try:
        for persona in personas:
            directory = root / persona.name
            code = launch(persona, directory)
            metrics = read_metrics(directory)
            rows.append(row(persona, metrics, "" if code == 0 else f"exit {code}"))
            raw[persona.name] = metrics
            write(root, rows, raw)
    finally:
        write(root, rows, raw)
    return rows


def write(root: Path, rows: Sequence[Sequence[str]], raw: Mapping[str, object]) -> None:
    """Write the rows as ``matrix.md`` and each run's measurements by persona as ``matrix.json``."""
    (root / "matrix.md").write_text(table(rows), encoding="utf-8")
    (root / "matrix.json").write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")


def launch_process(argv: Sequence[str]) -> int:
    """Run one command to its end.

    An interrupt goes on to it, and it gets time to write its results.
    """
    process = subprocess.Popen(list(argv))
    try:
        return process.wait()
    except KeyboardInterrupt:
        process.send_signal(signal.SIGINT)
        try:
            process.wait(timeout=STOP_WAIT_S)
        except subprocess.TimeoutExpired:
            process.terminate()
            process.wait()
        raise
