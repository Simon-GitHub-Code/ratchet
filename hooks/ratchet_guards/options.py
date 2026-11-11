"""Getopt-style option scanning, just enough to tell flags from operands."""

from collections.abc import Collection, Sequence
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class Spec:
    """The options of one command that take a value: short letters and long names."""

    short_values: str = ""
    long_values: frozenset[str] = frozenset()


NO_VALUES = Spec()


@dataclass(frozen=True, slots=True)
class Options:
    """Flags seen (``-x`` per short letter, ``--name`` per long option), their values, operands."""

    flags: frozenset[str]
    values: dict[str, str] = field(default_factory=dict)
    operands: tuple[str, ...] = ()


@dataclass(slots=True)
class _Scan:
    spec: Spec
    flags: set[str] = field(default_factory=set)
    values: dict[str, str] = field(default_factory=dict)
    operands: list[str] = field(default_factory=list)
    awaiting: str | None = None
    operands_only: bool = False

    def take(self, arg: str) -> None:
        if self.awaiting is not None:
            self.values[self.awaiting] = arg
            self.awaiting = None
        elif self.operands_only or arg == "-" or not arg.startswith("-"):
            self.operands.append(arg)
        elif arg == "--":
            self.operands_only = True
        elif arg.startswith("--"):
            self._long(arg)
        else:
            self._short(arg)

    def _long(self, arg: str) -> None:
        name, equals, value = arg.partition("=")
        self.flags.add(name)
        if equals:
            self.values[name] = value
        elif name in self.spec.long_values:
            self.awaiting = name

    def _short(self, arg: str) -> None:
        for index, letter in enumerate(arg[1:], start=2):
            flag = f"-{letter}"
            self.flags.add(flag)
            if letter in self.spec.short_values:
                if index < len(arg):
                    self.values[flag] = arg[index:]
                else:
                    self.awaiting = flag
                return


def scan(args: Sequence[str], spec: Spec) -> Options:
    """Scan ``args``; the options ``spec`` names take a value."""
    state = _Scan(spec)
    for arg in args:
        state.take(arg)
    return Options(frozenset(state.flags), state.values, tuple(state.operands))


def expand_long(args: Sequence[str], options: Collection[str]) -> list[str]:
    """``args`` with each long option spelled as a prefix of exactly one of ``options`` made whole.

    Git takes any unambiguous prefix of a long option, and an exact name wins over a longer one
    that it prefixes (``--force`` is not ``--force-create``). ``options`` names the options a
    rule looks at. A prefix of two of them, which git refuses as ambiguous, stays as written, and
    so does everything after ``--``.
    """
    expanded: list[str] = []
    for index, arg in enumerate(args):
        if arg == "--":
            return [*expanded, *args[index:]]
        name, equals, value = arg.partition("=")
        matches = [option for option in options if option.startswith(name)]
        long = name.startswith("--") and len(name) > len("--")
        unique = long and name not in options and len(matches) == 1
        expanded.append(matches[0] + equals + value if unique else arg)
    return expanded
