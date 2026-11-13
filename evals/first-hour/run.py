# /// script
# requires-python = ">=3.11"
# dependencies = ["claude-agent-sdk==0.2.181"]
# ///
"""The first hour of a scripted owner on a plugin checkout, end to end, unattended.

    uv run evals/first-hour/run.py run --plugin /path/to/ratchet --dir /tmp/first-hour-1
    uv run evals/first-hour/run.py reduce /tmp/first-hour-1
    uv run evals/first-hour/run.py oneoff <one-off logs dir> <out dir>

``run`` drives one run and writes ``events.jsonl``, ``transcript.jsonl``, ``metrics.json`` and
``table.md`` in the directory, which must be under the system temp location and empty. ``reduce``
rewrites the measurements from a run's event log. ``oneoff`` turns the logs of the hand-driven
dogfood run into an event log and reduces it, as a cross-check of the reducer.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from first_hour import oneoff, persona, reduce, steps


def _reduce(directory: Path) -> None:
    metrics = reduce.reduce(reduce.read(directory / "events.jsonl"))
    (directory / "metrics.json").write_text(json.dumps(metrics, indent=2) + "\n", "utf-8")
    (directory / "table.md").write_text(reduce.table(metrics), "utf-8")
    print(reduce.table(metrics))


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="drive one run")
    run.add_argument("--plugin", type=Path, required=True, help="the plugin checkout under test")
    run.add_argument(
        "--dir", type=Path, required=True, help="an empty directory under the temp location"
    )
    run.add_argument("--persona", default="read-later", help="a stem under personas/ or a path")
    run.add_argument("--minutes", type=float, default=120.0, help="wall-clock budget")
    run.add_argument("--usd", type=float, default=40.0, help="cost budget")
    run.add_argument("--max-steps", type=int, default=None, help="stop after this many steps")
    run.add_argument("--model", default="opus", help="the product session's model")
    run.add_argument("--owner-model", default="haiku", help="the scripted owner's model")
    again = sub.add_parser("reduce", help="rewrite a run's measurements from its event log")
    again.add_argument("dir", type=Path)
    old = sub.add_parser("oneoff", help="reduce the hand-driven dogfood run's logs")
    old.add_argument("logs", type=Path)
    old.add_argument("out", type=Path)
    options = parser.parse_args(argv[1:])
    if options.command == "reduce":
        _reduce(options.dir)
        return 0
    if options.command == "oneoff":
        options.out.mkdir(parents=True, exist_ok=True)
        oneoff.convert(options.logs, options.out / "events.jsonl")
        _reduce(options.out)
        return 0
    from first_hour import drive  # noqa: PLC0415

    config = drive.Config(
        plugin=options.plugin.resolve(),
        root=drive.checked_root(options.dir),
        persona=persona.load(options.persona),
        budget=steps.Budget(minutes=options.minutes, usd=options.usd, steps=options.max_steps),
        model=options.model,
        owner_model=options.owner_model,
    )
    asyncio.run(drive.drive(config))
    print((config.root / "table.md").read_text(encoding="utf-8"))
    print(f"measurements: {config.root / 'metrics.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
