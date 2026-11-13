"""Shell commands that write, move or delete files, and the paths each one touches.

A shell change to a guard path is judged as an Edit of it is: refused for a locked path, and for
any other where the commit lands on the default branch (ADR 0033). An interpreter writing files
itself is judged by its program text, not by the paths on its command line
(``ratchet_guards.interpreters``): ``python3 -c``,
``python3 - <<'EOF'``, ``node -e``, ``perl -e``, ``ruby -e``, ``deno eval`` and ``bun -e`` are
denied when the program names a guard path in a string literal and holds a write call.
``perl -i`` and ``ruby -i`` rewrite the files named on the command line, so they are writers
here. Still out of reach, and left to diff detection: a script file run by path, a path built
at run time, a program fetched or decoded before it runs.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath

from ratchet_guards.options import NO_VALUES, Options, Spec, scan

_COPY = Spec("tSmog", frozenset({"--target-directory"}))
_MOVE = Spec("tS", frozenset({"--target-directory"}))
_REMOVE = Spec("s")
# Why: `--rec` is the shortest unambiguous prefix of `--recursive` (`--reference` shares `--re`).
_RECURSIVE_SHORTEST = len("--rec")
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


def _modes(args: Sequence[str]) -> Touched:
    """``chmod``, ``chown``, ``chgrp``: each operand is written; with ``-R`` it is a whole tree.

    The mode or owner operand never names a guard path, so it needs no parsing out; clearing
    the execute bit on a git hook switches it off as surely as deleting it. Without ``-R`` only
    the named path changes, so ``chmod 755 .`` does not touch what lies below ``.``.
    """
    options = scan(args, NO_VALUES)
    recursive = "-R" in options.flags or any(
        len(flag) >= _RECURSIVE_SHORTEST and "--recursive".startswith(flag)
        for flag in options.flags
    )
    if recursive:
        return Touched(removed=options.operands)
    return Touched(written=options.operands)


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
    "ruby": _in_place,
    "dd": _dd,
    "chmod": _modes,
    "chown": _modes,
    "chgrp": _modes,
}


def touched(head: str, args: Sequence[str]) -> Touched:
    """The paths ``head args`` writes or removes; nothing for a command that is not a writer."""
    writer = WRITERS.get(head)
    return Touched() if writer is None else writer(args)
