#!/usr/bin/env python3
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
TIMEOUT = 120.0
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
    ANSWERED = "answered"
    SILENT = "silent"
    FAILED = "failed"
    TIMED_OUT = "timed out"


@dataclass(frozen=True)
class Probe:
    answer: Answer
    detail: str = ""


@dataclass(frozen=True)
class Problem:
    detail: str
    fix: str
    owner: bool = True

    @property
    def produce(self) -> str:
        return f"the owner: {self.fix}" if self.owner else self.fix


def hooks_dir(root: Path) -> Path | None:
    common = gatelib.git(root, "rev-parse", "--path-format=absolute", "--git-common-dir")
    return None if common is None else Path(common) / "hooks"


def hooks_path_scopes(root: Path) -> dict[str, str]:
    listed = gatelib.git(root, "config", "--show-scope", "--get-all", "core.hooksPath") or ""
    scopes: dict[str, str] = {}
    for line in listed.splitlines():
        scope, _, value = line.partition("\t")
        scopes[scope] = value
    return scopes


def git_version() -> tuple[int, ...]:
    out = subprocess.run(["git", "--version"], capture_output=True, text=True, check=False).stdout
    numbers = re.findall(r"\d+", out)[:3]
    return tuple(int(number) for number in numbers)


def stale_recipes(root: Path) -> bool:
    for name in JUSTFILES:
        text = gatelib.read_text(root / name)
        if text is not None:
            return PROBE_ENV not in text
    return False


def run_hook(argv: list[str], *, token: Path, log: Path, deadline: float) -> int | None:
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
    tail = log.read_text(encoding="utf-8", errors="replace").strip().splitlines()[-OUTPUT_LINES:]
    shown = " / ".join(line.strip() for line in tail) or "no output"
    return f"the {hook} hook exited {code} before it answered ({shown})"


def mechanism(root: Path) -> Mechanism:
    match = MECHANISM.search(gatelib.read_text(root / gatelib.BINDING) or "")
    return (match.group(1), match.group(2)) if match else (COMMIT_HOOKS, None)


def installer() -> list[str]:
    return [*([] if shutil.which("pre-commit") else ["uvx"]), "pre-commit", "install"]


def set_aside(outside: Sequence[str]) -> dict[str, str]:
    return {f"GIT_CONFIG_{scope.upper()}": os.devnull for scope in outside}


def install_command(outside: Sequence[str], source: Mechanism = (COMMIT_HOOKS, None)) -> str:
    path, named = source
    if path != COMMIT_HOOKS:
        return named or f"<the command that installs the hooks of {path}>"
    prefix = "".join(f"{name}={value} " for name, value in set_aside(outside).items())
    return prefix + " ".join(installer())


def install_entry(root: Path) -> str:
    script = f'python3 "{PLUGIN_ROOT / "scripts/hooks_install.py"}"'
    return gatelib.entry_point(root, "hooks-install") or script


def diagnose(
    scopes: dict[str, str],
    *,
    repo_installed: bool,
    hooks: Path,
    source: Mechanism = (COMMIT_HOOKS, None),
    entry: str = "",
) -> Problem:
    local = [scope for scope in UNSET if scope in scopes]
    outside = [scope for scope in OUTSIDE if scope in scopes]
    reasons = [
        f"core.hooksPath is set in the {scope} git config to {scopes[scope]}" for scope in local
    ]
    commands = [UNSET[scope] for scope in local]
    path, named = source
    install = "" if local else (entry if path == COMMIT_HOOKS else named or "")
    if not repo_installed:
        reasons.append(f"the repository's own hooks are not installed in {hooks}")
        commands.append(install or install_command(outside, source))
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
        owner=repo_installed or not install,
    )


def unprobeable(root: Path) -> Problem | None:
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
            f"copy the `tier0`, `tier0-tree`, `tier0-staged`, `pre-commit` and `commit-msg` recipes"
            f" of {TEMPLATE} into the justfile",
        )
    return None


def unanswered(root: Path, hooks: Path, probe: Probe) -> Problem:
    if probe.answer is Answer.FAILED:
        return Problem(
            f"{probe.detail}, so whether a commit would run tier 0 is not known; git would refuse"
            " the commit",
            "run the hook yourself (`git hook run pre-commit`, and `git hook run commit-msg --"
            " <message file>`), repair what it reports, then run the gate again",
        )
    if probe.answer is Answer.TIMED_OUT:
        return Problem(
            f"a commit hook had not finished after {TIMEOUT:g} seconds, so whether a commit would"
            " run tier 0 is not known. A busy machine is the usual cause",
            "run this again when the machine is quieter. If it happens on a quiet machine, time"
            " `git hook run pre-commit`: something the hooks run before they reach the"
            " repository's own (a global hook, as a rule) is slow",
            owner=False,
        )
    scopes = hooks_path_scopes(root)
    own = bool(scopes) and run_hooks(root, ("-c", f"core.hooksPath={hooks}")).answer is (
        Answer.ANSWERED
    )
    return diagnose(
        scopes, repo_installed=own, hooks=hooks, source=mechanism(root), entry=install_entry(root)
    )


def check(root: Path) -> Problem | None:
    hooks = hooks_dir(root)
    if hooks is None:
        return Problem(f"{root} is not a git repository", "run `git init` there")
    blocked = unprobeable(root)
    if blocked is not None:
        return blocked
    probe = run_hooks(root, ())
    return None if probe.answer is Answer.ANSWERED else unanswered(root, hooks, probe)


def report(problem: Problem | None) -> int:
    if problem is None:
        print("hooks: green")
        return 0
    print(f"hooks: {problem.detail}")
    print(f"  {'owner' if problem.owner else 'fix'}: {problem.fix}")
    print("hooks: red")
    return 1


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    options = parser.parse_args(argv[1:])
    return report(check(Path(options.root).resolve()))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
