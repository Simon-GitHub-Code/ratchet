"""A git command line split around its subcommand: global options, then the subcommand's own."""

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

_GLOBAL_VALUES = frozenset({"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env"})
_CONFIG_VALUES = frozenset({"-c", "--config-env"})
# Why: with one of these git works on a repository other than the one in the working directory.
_REPOSITORIES = frozenset({"--git-dir", "--work-tree", "--namespace"})


@dataclass(frozen=True, slots=True)
class Invocation:
    """A git command line split around its subcommand."""

    cwd: Path
    configs: tuple[str, ...]
    subcommand: str
    args: tuple[str, ...]
    redirected: bool = False


def _joined_config(option: str) -> str | None:
    """The config of ``-cNAME=VALUE`` or ``--config-env=NAME=ENV``, else ``None``."""
    if option.startswith("--config-env="):
        return option.partition("=")[2]
    if option.startswith("-c") and not option.startswith("--") and len(option) > len("-c"):
        return option[len("-c") :]
    return None


def invocation(words: Sequence[str], cwd: Path) -> Invocation:
    """Parse ``git [global options] <subcommand> args``; ``-C`` moves the working directory.

    ``redirected`` is set by ``--git-dir``, ``--work-tree`` and ``--namespace``, joined to their
    value or apart from it.
    """
    configs: list[str] = []
    rest = list(words[1:])
    redirected = False
    while rest and rest[0].startswith("-"):
        option = rest.pop(0)
        redirected = redirected or option.partition("=")[0] in _REPOSITORIES
        joined = _joined_config(option)
        if joined is not None:
            configs.append(joined)
        elif option in _GLOBAL_VALUES and rest:
            value = rest.pop(0)
            cwd = cwd / value if option == "-C" else cwd
            configs.extend([value] if option in _CONFIG_VALUES else [])
    subcommand = rest.pop(0) if rest else ""
    return Invocation(cwd, tuple(configs), subcommand, tuple(rest), redirected)
