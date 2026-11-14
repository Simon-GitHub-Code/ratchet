# /// script
# requires-python = ">=3.11"
# dependencies = ["claude-agent-sdk==0.2.181"]
# ///
"""The first hour of a scripted owner on a plugin checkout, end to end, unattended.

    uv run evals/first-hour/run.py run --plugin /path/to/ratchet --dir /tmp/first-hour-1
    uv run evals/first-hour/run.py probe --plugin /path/to/ratchet --dir /tmp/first-hour-probe
    uv run evals/first-hour/run.py personas
    uv run evals/first-hour/run.py matrix --plugin /path/to/ratchet --dir /tmp/first-hour-matrix
    uv run evals/first-hour/run.py reduce /tmp/first-hour-1
    uv run evals/first-hour/run.py oneoff <one-off logs dir> <out dir>

``--persona`` names the owner of one run (``personas`` lists them). ``matrix`` runs the personas
that state a stack, one after another, each in its own directory under ``--dir``, and writes
``matrix.md``, the one table of them all. ``run`` drives one run and writes ``events.jsonl``,
``transcript.jsonl``, ``metrics.json`` and ``table.md`` in the directory, which must be under the
system temp location and empty. ``probe``
starts one session with the run's launch and prints what it can reach. ``reduce`` rewrites the
measurements from a run's event log. ``oneoff`` turns the logs of the hand-driven dogfood run
into an event log and reduces it, as a cross-check of the reducer.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from first_hour import harness, matrix, oneoff, persona, reduce, sandbox, steps


def _reduce(directory: Path) -> None:
    metrics = reduce.reduce(reduce.read(directory / "events.jsonl"))
    (directory / "metrics.json").write_text(json.dumps(metrics, indent=2) + "\n", "utf-8")
    (directory / "table.md").write_text(reduce.table(metrics), "utf-8")
    print(reduce.table(metrics))


def _prepare(options: argparse.Namespace) -> tuple[harness.Run, harness.Operator, str]:
    """Read the operator's side once, make the run, and leave the process the run's environment."""
    if shutil.which("bwrap") is None:
        msg = "the run needs bubblewrap (`bwrap`) to sandbox its sessions; install it first"
        raise SystemExit(msg)
    home = Path.home()
    credentials = json.loads((home / ".claude" / ".credentials.json").read_text("utf-8"))
    token = sandbox.model_token(credentials, now=time.time(), minutes=options.minutes)
    found = {n: shutil.which(n) for n in sandbox.TOOLCHAIN}
    claude = found["claude"]
    if claude is None:
        msg = "the run needs Claude Code (`claude`) on PATH"
        raise SystemExit(msg)
    version = subprocess.run([claude, "--version"], capture_output=True, text=True, check=False)
    operator = harness.Operator.read(home, found, version.stdout.strip())
    config = harness.Config(
        plugin=options.plugin.resolve(),
        root=harness.checked_root(options.dir, Path(tempfile.gettempdir())),
        persona=persona.load(options.persona),
        budget=steps.Budget(minutes=options.minutes, usd=options.usd, steps=options.max_steps),
        model=options.model,
        owner_model=options.owner_model,
    )
    run = harness.setup(config, operator, os.environ)
    # Why: the Agent SDK starts Claude Code with this process's environment under its own; the
    # run's is the only one it may see.
    os.environ.clear()
    os.environ.update(run.env)
    return run, operator, token


def _matrix(options: argparse.Namespace) -> int:
    """Run each chosen persona in turn and print the table of the runs."""
    stems = options.personas.split(",") if options.personas else persona.names()
    chosen = [persona.load(stem) for stem in stems]
    if not options.personas:
        chosen = [p for p in chosen if p.stack]
    settings = matrix.Settings(
        plugin=options.plugin.resolve(),
        minutes=options.minutes,
        usd=options.usd,
        model=options.model,
        owner_model=options.owner_model,
    )
    here = Path(__file__).resolve()
    rows = matrix.run_all(
        chosen,
        options.dir.resolve(),
        lambda one, directory: matrix.launch_process(
            matrix.command(here, settings, persona=one.name, directory=directory)
        ),
    )
    print(matrix.table(rows))
    print(f"table: {options.dir.resolve() / 'matrix.md'}")
    return 0


def _parser() -> argparse.ArgumentParser:
    """The command line: one subcommand each for a run, a probe, a matrix and the readers."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name, text in (("run", "drive one run"), ("probe", "show what a session can reach")):
        one = sub.add_parser(name, help=text)
        one.add_argument("--plugin", type=Path, required=True, help="the plugin checkout")
        one.add_argument("--dir", type=Path, required=True, help="an empty directory under temp")
        one.add_argument("--persona", default="read-later", help="a stem under personas/ or a path")
        one.add_argument("--minutes", type=float, default=120.0, help="wall-clock budget")
        one.add_argument("--usd", type=float, default=40.0, help="cost budget")
        one.add_argument("--max-steps", type=int, default=None, help="stop after this many steps")
        one.add_argument("--model", default="opus", help="the product session's model")
        one.add_argument("--owner-model", default="haiku", help="the scripted owner's model")
    many = sub.add_parser("matrix", help="run the personas that state a stack, one after another")
    many.add_argument("--plugin", type=Path, required=True, help="the plugin checkout")
    many.add_argument("--dir", type=Path, required=True, help="a new directory under temp")
    many.add_argument(
        "--personas", default="", help="comma-separated stems; default: all with a stack"
    )
    many.add_argument("--minutes", type=float, default=90.0, help="wall-clock budget of each run")
    many.add_argument("--usd", type=float, default=40.0, help="cost budget of each run")
    many.add_argument("--model", default="opus", help="the product session's model")
    many.add_argument("--owner-model", default="haiku", help="the scripted owner's model")
    sub.add_parser("personas", help="list the personas and the stack each insists on")
    again = sub.add_parser("reduce", help="rewrite a run's measurements from its event log")
    again.add_argument("dir", type=Path)
    old = sub.add_parser("oneoff", help="reduce the hand-driven dogfood run's logs")
    old.add_argument("logs", type=Path)
    old.add_argument("out", type=Path)
    return parser


def main(argv: list[str]) -> int:
    """Run, probe, reduce, or cross-check, as the subcommand says."""
    options = _parser().parse_args(argv[1:])
    if options.command == "personas":
        for stem in persona.names():
            print(f"{stem}\t{persona.load(stem).stack or 'unstated'}")
        return 0
    if options.command == "matrix":
        return _matrix(options)
    if options.command == "reduce":
        _reduce(options.dir)
        return 0
    if options.command == "oneoff":
        options.out.mkdir(parents=True, exist_ok=True)
        oneoff.convert(options.logs, options.out / "events.jsonl")
        _reduce(options.out)
        return 0
    from first_hour import drive  # noqa: PLC0415

    run, operator, token = _prepare(options)
    if options.command == "probe":
        print(json.dumps(asyncio.run(drive.probe(run, token)), indent=2))
        return 0
    asyncio.run(harness.drive(run, operator, drive.SdkModels(token)))
    print((run.layout.root / "table.md").read_text(encoding="utf-8"))
    print(f"measurements: {run.layout.root / 'metrics.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
