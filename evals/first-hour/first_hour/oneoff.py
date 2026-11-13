"""Turn the logs of the hand-driven dogfood run into an event log the reducer reads.

That run had no event log. Its steps are in ``timeline.jsonl``, its answers and prompts in
``owner.jsonl`` (classified by text patterns), each step's SDK messages in ``<step>.jsonl``, and
each merge the observer did by hand in ``*-owner-merge-*.txt``, timed by the file's mtime. Its
cross-check is approximate where the logs are: prompts carry no origin, and the pastes per merge
are read back from what the merge file shows was run.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from typing import TYPE_CHECKING

from first_hour import appcheck, gates, transcript

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

Event = dict[str, object]
CLASSES = {"product": "product", "technical": "default-able", "UNSCRIPTED": "default-able"}
MERGE_FILE = re.compile(r"^(\d+)-owner-merge-(.+)\.txt$")
NO_FF = re.compile(r"merge --no-ff (\S+?)`")


def _clock(text: str) -> int:
    hours, minutes, seconds = (int(part) for part in text.split(":"))
    return hours * 3600 + minutes * 60 + seconds


def _lines(path: Path) -> Iterator[dict[str, object]]:
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                yield json.loads(line)


def _steps(logs: Path, zero: int) -> Iterator[Event]:
    for item in _lines(logs / "timeline.jsonl"):
        step = str(item["step"])
        if step == "probe":
            continue
        at = _clock(str(item["t"])) - zero
        if item["event"] == "start":
            yield {"s": at, "step": step, "kind": "step", "action": "type", "text": item["prompt"]}
            continue
        if item["event"] == "end":
            for record in _lines(logs / f"{step}.jsonl"):
                msg = record.get("msg")
                for event in transcript.events(
                    str(record["type"]), msg if isinstance(msg, dict) else {}
                ):
                    yield {"s": at, "step": step, **event}
            yield {"s": at, "step": step, "kind": "step-end", "wall_s": item.get("wall_s", 0)}


def _owner(logs: Path, zero: int) -> Iterator[Event]:
    for item in _lines(logs / "owner.jsonl"):
        if item["step"] == "probe":
            continue
        at = _clock(str(item["t"])) - zero
        if item["kind"] == "permission-prompt":
            yield {
                "s": at,
                "step": item["step"],
                "kind": "prompt",
                "tool": item.get("tool"),
                "origin": "unknown",
                "decision": item.get("decision"),
            }
            continue
        label = str(item.get("class", ""))
        options = item.get("options")
        labels = [str(o.get("label")) for o in options] if isinstance(options, list) else []
        recommended = next((x for x in labels if "recommend" in x.lower()), None)
        yield {
            "s": at,
            "step": item["step"],
            "kind": "question",
            "channel": "dialog",
            "class": CLASSES[label.split(":", maxsplit=1)[0]],
            "header": item.get("header"),
            "question": item.get("question"),
            "options": labels,
            "recommended": recommended,
            "took_recommended": recommended is not None and item.get("answer") == recommended,
            "answer": item.get("answer"),
        }


def _merges(logs: Path, zero: int, day: dt.date) -> Iterator[Event]:
    """Each hand merge, the commands its file shows the owner ran, and the gates they printed."""
    run_id = 0
    for path in sorted(logs.glob("*-owner-merge-*.txt")):
        match = MERGE_FILE.match(path.name)
        if match is None:
            continue
        number, label = match.groups()
        stamp = dt.datetime.fromtimestamp(path.stat().st_mtime, dt.UTC)
        at = int((stamp - dt.datetime.combine(day, dt.time(), dt.UTC)).total_seconds()) - zero
        text = path.read_text(encoding="utf-8", errors="replace")
        step = f"{number}-owner-merge-{label}"
        branch = (NO_FF.findall(text) or [label])[0]
        commands = [f"just merge {branch}"] if "merge.py" in text else []
        commands.append(f"git switch main && git merge --no-ff {branch}")
        check = logs / f"{number}-owner-check-deep.txt"
        deep = (
            text
            if gates.parse(text)
            else check.read_text(encoding="utf-8")
            if check.is_file()
            else ""
        )
        if deep:
            commands.append("just check-deep")
        for command in commands:
            yield {"s": at, "step": step, "kind": "paste", "command": command, "exit": None}
        yield {
            "s": at,
            "step": step,
            "kind": "merge",
            "branch": branch,
            "how": "hand",
            "owner_text": text[:2000],
        }
        if deep:
            run_id += 1
            found = [
                {"name": g.name, "status": g.status.value, "trigger": g.trigger}
                for g in gates.parse(deep)
            ]
            yield {
                "s": at,
                "step": step,
                "kind": "gates",
                "id": run_id,
                "attempt": 1,
                "command": "just check-deep",
                "gates": found,
            }
            rerun = logs / f"{number}b-check-deep-rerun1.txt"
            if rerun.is_file():
                again = [
                    {"name": g.name, "status": g.status.value, "trigger": g.trigger}
                    for g in gates.parse(rerun.read_text(encoding="utf-8"))
                ]
                yield {
                    "s": at,
                    "step": step,
                    "kind": "gates",
                    "id": run_id,
                    "of": run_id,
                    "attempt": 2,
                    "command": "just check-deep",
                    "gates": again,
                }


def events(logs: Path) -> list[Event]:
    """The one-off run's events, in time order."""
    starts = [i for i in _lines(logs / "timeline.jsonl") if i["step"] != "probe"]
    zero = _clock(str(starts[0]["t"]))
    day = dt.datetime.fromtimestamp((logs / "timeline.jsonl").stat().st_mtime, dt.UTC).date()
    found = [*_steps(logs, zero), *_owner(logs, zero), *_merges(logs, zero, day)]
    app = logs.parent / "app"
    if app.is_dir():
        start = appcheck.start_command(app)
        found.append(
            {
                "s": max(float(str(e["s"])) for e in found),
                "step": "end",
                "kind": "app",
                "start_command": start.command if start else None,
                "source": start.source if start else None,
                "started": False,
                "page_ok": False,
                "slice_ok": False,
                "finding": appcheck.NO_WAY if start is None else "not started by the adapter",
            }
        )
    found.append(
        {
            "s": max(float(str(e["s"])) for e in found),
            "step": "end",
            "kind": "stop",
            "reason": "observer-stopped",
        }
    )
    return sorted(found, key=lambda e: float(str(e["s"])))


def convert(logs: Path, out: Path) -> None:
    """Write the one-off run's events to ``out``."""
    with out.open("w", encoding="utf-8") as lines:
        for event in events(logs):
            lines.write(json.dumps(event, default=str) + "\n")
