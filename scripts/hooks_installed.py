#!/usr/bin/env python3
"""Would a commit in this checkout run tier 0? Red, with the owner's command, when it would not.

Tier 0 runs on every commit through the commit hooks: ``pre-commit`` runs ``just pre-commit`` on
the tree and ``commit-msg`` runs ``just commit-msg`` on the message. A hook that is not installed
fails silently: the commit goes through and nothing says tier 0 never ran. The usual cause is a
``core.hooksPath`` in the git config, which makes ``pre-commit install`` refuse. The guard
refuses hook installs and hook-path changes from an agent, so the repair is the owner's one
command, and the method stays red until they run it.

The gate proves the answer instead of inferring it. It runs the hooks git would run for a commit
(``git hook run``, so every ``core.hooksPath``, local or global, and every chain from a global
hook to the repository's own is exercised as a commit would exercise it) with
``RATCHET_HOOK_PROBE`` naming a scratch file. The template ``justfile``'s ``pre-commit`` and
``commit-msg`` recipes, which the hooks call, answer the probe by writing their own name to that
file and exiting before any gate runs. The gate is green only when both names are there. The
answer goes through a file because the ``pre-commit`` framework hides the output of a hook that
passes. Tier 0 itself never runs. The hooks run as for a commit, so ``pre-commit`` stashes
unstaged changes while the probe runs, exactly as it does at every commit, and the probe needs git
2.36 or later.

A red names the command. A local ``core.hooksPath`` is unset; a global or system one is stepped
around when the repository's own hooks are installed (``GIT_CONFIG_GLOBAL=/dev/null pre-commit
install``, since ``pre-commit`` refuses while the variable is set); a global hook that does not
chain to the repository's hooks is bypassed by pointing this repository at its own hooks.

The hooks run one after the other, as git runs them. A hook that exits non-zero before it answers
has run and failed, so it is not read as "not installed": the red quotes its exit code and the end
of its output. The usual cause is the ``pre-commit`` framework failing on its cache database while
another process holds it. A hook still running after 20 seconds is killed and reported as
unknown.

The hooks need not come from the ``pre-commit`` framework. The binding's ``- Commit hooks:``
line names the file or directory another mechanism (lefthook, husky, a directory of plain
scripts) installs them from, and the command that installs them. The probe is the same, since
it runs whatever git would run, and a red then names that command.

Forging a green means writing ``.git/hooks/*`` (the ``local-git`` guard group refuses it) or
changing the recipes (``justfile`` is a ``gate-commands`` guard path). Setting
``RATCHET_HOOK_PROBE`` on a commit would skip tier 0 for it, and the guard refuses that like it
refuses ``SKIP=``.

Usage: ``hooks_installed.py [--root DIR]``. Exits 1 when a commit would not run tier 0.
"""

from __future__ import annotations

import argparse
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

import gatelib

if TYPE_CHECKING:
    from collections.abc import Sequence

PROBE_ENV = "RATCHET_HOOK_PROBE"
HOOKS = ("pre-commit", "commit-msg")
COMMIT_HOOKS = ".pre-commit-config.yaml"
MECHANISM = re.compile(r"^- Commit hooks: `([^`]+)`(?:, installed by `([^`]+)`)?", re.MULTILINE)
Mechanism = tuple[str, str | None]
TIMEOUT = 20.0
OUTPUT_LINES = 3
JUSTFILES = ("justfile", "Justfile", ".justfile")
MIN_GIT = (2, 36)
PLUGIN_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = PLUGIN_ROOT / "skills/adopt/templates/justfile"
UNSET = {
    "local": "git config --unset core.hooksPath",
    "worktree": "git config --worktree --unset core.hooksPath",
}
OUTSIDE = ("global", "system")


class Answer(Enum):
    """What came back from running the hooks with the probe set."""

    ANSWERED = "answered"
    SILENT = "silent"
    FAILED = "failed"
    TIMED_OUT = "timed out"


@dataclass(frozen=True)
class Probe:
    """The hooks' answer, and for a hook that failed, which one and what it printed."""

    answer: Answer
    detail: str = ""


@dataclass(frozen=True)
class Problem:
    """Why a commit would not run tier 0, and what the owner does about it."""

    detail: str
    fix: str


def hooks_dir(root: Path) -> Path | None:
    """The repository's own hooks directory, which no ``core.hooksPath`` moves."""
    common = gatelib.git(root, "rev-parse", "--path-format=absolute", "--git-common-dir")
    return None if common is None else Path(common) / "hooks"


def hooks_path_scopes(root: Path) -> dict[str, str]:
    """Where ``core.hooksPath`` is set, by git's scope name (local, global, ...), and to what."""
    listed = gatelib.git(root, "config", "--show-scope", "--get-all", "core.hooksPath") or ""
    scopes: dict[str, str] = {}
    for line in listed.splitlines():
        scope, _, value = line.partition("\t")
        scopes[scope] = value
    return scopes


def git_version() -> tuple[int, ...]:
    """The installed git's version as numbers; ``git hook run`` arrived in 2.36."""
    out = subprocess.run(["git", "--version"], capture_output=True, text=True, check=False).stdout
    numbers = re.findall(r"\d+", out)[:3]
    return tuple(int(number) for number in numbers)


def stale_recipes(root: Path) -> bool:
    """Whether the justfile exists and its recipes cannot answer the probe.

    Run against such a justfile the probe would run tier 0 for real, so it is not run.
    """
    for name in JUSTFILES:
        text = gatelib.read_text(root / name)
        if text is not None:
            return PROBE_ENV not in text
    return False


def run_hook(argv: list[str], *, token: Path, log: Path, deadline: float) -> int | None:
    """The exit code of one hook run with the probe set to ``token``; ``None`` past the deadline.

    The hook's output goes to ``log``. A hook still running at the deadline is killed with its
    process group.
    """
    with log.open("wb") as output:
        process = subprocess.Popen(
            argv,
            env={**os.environ, PROBE_ENV: str(token)},
            stdout=output,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            return process.wait(timeout=max(0.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            return None


def run_hooks(root: Path, git_options: Sequence[str]) -> Probe:
    """Run both hooks as a commit runs them, one after the other: did each answer the probe?

    ``git_options`` come before ``hook`` (``-c core.hooksPath=...`` to try the repository's own
    hooks). The answer is the recipe's write to a scratch file. A missing hook is silent, not an
    error (``--ignore-missing``), so a hook that exits non-zero without answering is one that
    ran and failed: the ``pre-commit`` framework failing on a locked cache database is the usual
    case, and a commit would be refused, not run without tier 0. The hooks run one at a time as
    git runs them, since two ``pre-commit`` processes contend for that database.
    """
    with tempfile.TemporaryDirectory(prefix="ratchet-hooks-") as scratch:
        message = Path(scratch) / "message"
        message.write_text("probe\n", encoding="utf-8")
        for hook in HOOKS:
            deadline = time.monotonic() + TIMEOUT
            token, log = Path(scratch) / hook, Path(scratch) / f"{hook}.log"
            argv = ["git", "-C", str(root), *git_options, "hook", "run", "--ignore-missing", hook]
            if hook == "commit-msg":
                argv += ["--", str(message)]
            code = run_hook(argv, token=token, log=log, deadline=deadline)
            if code is None:
                return Probe(Answer.TIMED_OUT)
            if token.is_file() and token.read_text(encoding="utf-8").strip() == hook:
                continue
            if code != 0:
                return Probe(Answer.FAILED, failure(hook, code, log))
            return Probe(Answer.SILENT)
        return Probe(Answer.ANSWERED)


def failure(hook: str, code: int, log: Path) -> str:
    """What the owner reads of a hook that failed: its exit code and the end of its output."""
    tail = log.read_text(encoding="utf-8", errors="replace").strip().splitlines()[-OUTPUT_LINES:]
    shown = " / ".join(line.strip() for line in tail) or "no output"
    return f"the {hook} hook exited {code} before it answered ({shown})"


def mechanism(root: Path) -> Mechanism:
    """What the binding says the commit hooks are installed from, and the command that does it.

    The ``pre-commit`` framework's config when the binding does not say. Its command is worked
    out by ``install_command``, since it depends on the machine's git config.
    """
    match = MECHANISM.search(gatelib.read_text(root / gatelib.BINDING) or "")
    return (match.group(1), match.group(2)) if match else (COMMIT_HOOKS, None)


def install_command(outside: Sequence[str], source: Mechanism = (COMMIT_HOOKS, None)) -> str:
    """The command that installs the hooks.

    The binding's own for another mechanism. For the ``pre-commit`` framework,
    ``pre-commit install`` with the git config scopes that set a hooks path set aside.
    """
    path, named = source
    if path != COMMIT_HOOKS:
        return named or f"<the command that installs the hooks of {path}>"
    installer = "pre-commit" if shutil.which("pre-commit") else "uvx pre-commit"
    prefix = "".join(f"GIT_CONFIG_{scope.upper()}=/dev/null " for scope in outside)
    return f"{prefix}{installer} install"


def diagnose(
    scopes: dict[str, str],
    *,
    repo_installed: bool,
    hooks: Path,
    source: Mechanism = (COMMIT_HOOKS, None),
) -> Problem:
    """The reasons the effective hooks stay silent and the commands that remove them."""
    local = [scope for scope in UNSET if scope in scopes]
    outside = [scope for scope in OUTSIDE if scope in scopes]
    reasons = [
        f"core.hooksPath is set in the {scope} git config to {scopes[scope]}" for scope in local
    ]
    commands = [UNSET[scope] for scope in local]
    if not repo_installed:
        reasons.append(f"the repository's own hooks are not installed in {hooks}")
        commands.append(install_command(outside, source))
    elif not local:
        scope, path = next(iter(scopes.items()))
        reasons.append(
            f"core.hooksPath is set in the {scope} git config to {path}, and the hooks there"
            " do not run the repository's own"
        )
        commands.append(f"git config core.hooksPath {shlex.quote(str(hooks))}")
    return Problem(
        "a commit here would skip tier 0: " + "; ".join(reasons),
        f"run `{' && '.join(commands)}`",
    )


def unprobeable(root: Path) -> Problem | None:
    """Why the hooks cannot be run as a commit runs them, if they cannot."""
    if git_version() < MIN_GIT:
        return Problem(
            "git is older than 2.36, which has no `git hook run`, so the hooks cannot be run"
            " as a commit runs them",
            "upgrade git to 2.36 or later",
        )
    if stale_recipes(root):
        return Problem(
            f"the justfile's `pre-commit` and `commit-msg` recipes do not answer {PROBE_ENV}, so"
            " the hooks cannot be probed without running tier 0",
            f"copy the `tier0`, `tier0-tree`, `pre-commit` and `commit-msg` recipes of {TEMPLATE}"
            " into the justfile",
        )
    return None


def unanswered(root: Path, hooks: Path, probe: Probe) -> Problem:
    """Why the hooks did not answer, and the owner's fix; ``probe`` is not an answer."""
    if probe.answer is Answer.FAILED:
        return Problem(
            f"{probe.detail}, so whether a commit would run tier 0 is not known; git would refuse"
            " the commit",
            "run the hook yourself (`git hook run pre-commit`, and `git hook run commit-msg --"
            " <message file>`), repair what it reports, then run the gate again",
        )
    if probe.answer is Answer.TIMED_OUT:
        return Problem(
            f"a hook was still running after {TIMEOUT:g} seconds, so whether a commit would run"
            " tier 0 is not known",
            "find what the hooks run before they reach the repository's own (a slow global hook"
            " is the usual cause), then run the gate again",
        )
    scopes = hooks_path_scopes(root)
    # Why: without a hooksPath the effective hooks are the repository's own, which just failed.
    own = bool(scopes) and run_hooks(root, ("-c", f"core.hooksPath={hooks}")).answer is (
        Answer.ANSWERED
    )
    return diagnose(scopes, repo_installed=own, hooks=hooks, source=mechanism(root))


def check(root: Path) -> Problem | None:
    """``None`` when a commit in ``root`` would run tier 0, else why not and the owner's fix."""
    hooks = hooks_dir(root)
    if hooks is None:
        return Problem(f"{root} is not a git repository", "run `git init` there")
    blocked = unprobeable(root)
    if blocked is not None:
        return blocked
    probe = run_hooks(root, ())
    return None if probe.answer is Answer.ANSWERED else unanswered(root, hooks, probe)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    options = parser.parse_args(argv[1:])
    problem = check(Path(options.root).resolve())
    if problem is None:
        print("hooks: green")
        return 0
    print(f"hooks: {problem.detail}")
    print(f"  owner: {problem.fix}")
    print("hooks: red")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
