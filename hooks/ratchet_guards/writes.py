"""Shell commands that write, move or delete files, and the paths each one touches.

The guarded channel for changing a guard path is the Edit tool, which asks the owner; the
shell route is denied outright. Interpreters writing files themselves (``python -c``,
``node -e``) are out of reach of any command-line check and are left to diff detection.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath

from ratchet_guards.options import NO_VALUES, Options, Spec, scan

_COPY = Spec("tSmog", frozenset({"--target-directory"}))
_MOVE = Spec("tS", frozenset({"--target-directory"}))
_REMOVE = Spec("s")
_IN_PLACE = Spec("efl", frozenset({"--expression", "--file"}))


@dataclass(frozen=True, slots=True)
class Touched:
    """Paths a command writes and paths it removes (everything below a removed directory)."""

    written: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()


def _tee(args: Sequence[str]) -> Touched:
    return Touched(written=scan(args, NO_VALUES).operands)


def _copy_targets(options: Options) -> tuple[list[str], list[str]]:
    operands = list(options.operands)
    directory = options.values.get("-t") or options.values.get("--target-directory")
    if directory is not None:
        return [directory], operands
    if len(operands) < 2:  # noqa: PLR2004 # -- a copy needs a source and a destination
        return operands, []
    return operands[-1:], operands[:-1]


def _joined(destination: str, sources: Sequence[str]) -> tuple[str, ...]:
    names = (PurePosixPath(source).name for source in sources)
    return (destination, *(str(PurePosixPath(destination) / name) for name in names if name))


def _copy(args: Sequence[str]) -> Touched:
    destinations, sources = _copy_targets(scan(args, _COPY))
    written = tuple(path for target in destinations for path in _joined(target, sources))
    return Touched(written=written)


def _move(args: Sequence[str]) -> Touched:
    destinations, sources = _copy_targets(scan(args, _MOVE))
    written = tuple(path for target in destinations for path in _joined(target, sources))
    return Touched(written=written, removed=tuple(sources))


def _remove(args: Sequence[str]) -> Touched:
    return Touched(removed=scan(args, _REMOVE).operands)


def _in_place(args: Sequence[str]) -> Touched:
    options = scan(args, _IN_PLACE)
    in_place = "-i" in options.flags or "--in-place" in options.flags
    return Touched(written=options.operands if in_place else ())


def _dd(args: Sequence[str]) -> Touched:
    return Touched(written=tuple(arg.removeprefix("of=") for arg in args if arg.startswith("of=")))


WRITERS: dict[str, Callable[[Sequence[str]], Touched]] = {
    "tee": _tee,
    "cp": _copy,
    "install": _copy,
    "ln": _copy,
    "rsync": _copy,
    "mv": _move,
    "rm": _remove,
    "rmdir": _remove,
    "unlink": _remove,
    "shred": _remove,
    "truncate": _remove,
    "sed": _in_place,
    "perl": _in_place,
    "dd": _dd,
}


def touched(head: str, args: Sequence[str]) -> Touched:
    """The paths ``head args`` writes or removes; nothing for a command that is not a writer."""
    writer = WRITERS.get(head)
    return Touched() if writer is None else writer(args)
