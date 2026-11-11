"""A git command line split around its subcommand: global options, then the subcommand's own."""

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

_GLOBAL_VALUES = frozenset({"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env"})
_CONFIG_VALUES = frozenset({"-c", "--config-env"})


@dataclass(frozen=True, slots=True)
class Invocation:
    """A git command line split around its subcommand."""

    cwd: Path
    configs: tuple[str, ...]
    subcommand: str
    args: tuple[str, ...]


def _joined_config(option: str) -> str | None:
    """The config of ``-cNAME=VALUE`` or ``--config-env=NAME=ENV``, else ``None``."""
    if option.startswith("--config-env="):
        return option.partition("=")[2]
    if option.startswith("-c") and not option.startswith("--") and len(option) > len("-c"):
        return option[len("-c") :]
    return None


def invocation(words: Sequence[str], cwd: Path) -> Invocation:
    """Parse ``git [global options] <subcommand> args``; ``-C`` moves the working directory."""
    configs: list[str] = []
    rest = list(words[1:])
    while rest and rest[0].startswith("-"):
        option = rest.pop(0)
        joined = _joined_config(option)
        if joined is not None:
            configs.append(joined)
        elif option in _GLOBAL_VALUES and rest:
            value = rest.pop(0)
            cwd = cwd / value if option == "-C" else cwd
            configs.extend([value] if option in _CONFIG_VALUES else [])
    subcommand = rest.pop(0) if rest else ""
    return Invocation(cwd, tuple(configs), subcommand, tuple(rest))
