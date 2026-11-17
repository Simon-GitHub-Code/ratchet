#!/usr/bin/env python3
"""Which workflow runs the merge job may count: ``ci`` on a pull request, tier 2 on the default.

The merge job (``merge.yml``) asks the forge for runs and hands the answer to this script, so the
workflow only fetches and the decision is code with tests. ADR 0024 says what each rule answers.

``forge_runs.py ci`` reads the ``ci.yml`` runs on one head commit from stdin. It prints ``green``
and exits 0 only when the newest run that tested this pull request against the default branch
succeeded. A run on the same commit proves nothing by itself: a second pull request from the same
branch against another base, or this one before it was retargeted, was tested on a merge with
that base, whose ``ci.yml`` is that base's. So the pull request must be the only one its branch
ever had against any other base, must never have been retargeted, and the run must name it.
Anything else, and anything unreadable, prints the reason and exits 1. With ``--report FILE`` it
also writes the comment the merge job posts when a reason is one the owner must act on: a twin
pull request against another base, a base that was changed, a pull request against another
branch, or a run that finished without naming this pull request. A ``ci`` that is red or still
running is the pull request's own checks' to say, and a pull request that changed while the job
read it is read again on the next run, so those stay a line in the job's log and no file is
written.

``forge_runs.py deep`` reads the completed ``deep.yml`` runs from stdin and prints ``green``,
``red`` or ``none``: the result on the commit nearest the default branch's tip, among the runs of
this repository that an event of the template started on a commit of the default branch. A run
on a tag named like the branch, or an old run started again, does not change the answer.
Unreadable input is ``red``. ``forge_runs.py tier1`` answers the same of the ``ci.yml`` runs a
push started. Where more runs match than one page holds, the newest page decides, and is ``red``
when no run in it counts.

Usage: ``forge_runs.py ci --pulls FILE --events FILE --number N --sha SHA --branch NAME --base
NAME --repository OWNER/NAME [--report FILE]`` and ``forge_runs.py deep|tier1 --tip REF
--repository OWNER/NAME [--root DIR]``.
"""

import argparse
import json
import re
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import gatelib

PAGE = 100
CI_PATH = ".github/workflows/ci.yml"
PULL_REQUEST = "pull_request"
DEEP_EVENTS = frozenset({"push", "schedule", "workflow_dispatch"})
ON_THE_DEFAULT_BRANCH = {"deep": DEEP_EVENTS, "tier1": frozenset({"push"})}
RETARGETS = frozenset({"base_ref_changed", "automatic_base_change_succeeded"})
UNCOUNTED = frozenset({"cancelled", "skipped"})
COMMIT = re.compile(r"^[0-9a-f]{40,64}$")
GREEN, RED, NONE = "green", "red", "none"
DONE = ("completed", "success")
UNREADABLE = "the forge's answer could not be read"
MARKER = "<!-- ratchet:merge -->"
OWNER = "merge: owner"


def at(value: object, *keys: str) -> object:
    for key in keys:
        value = value.get(key) if isinstance(value, dict) else None
    return value


def documents(text: str) -> list[object] | None:
    decoder = json.JSONDecoder()
    found: list[object] = []
    rest = text.strip()
    while rest:
        try:
            document, end = decoder.raw_decode(rest)
        except ValueError:
            return None
        found.append(document)
        rest = rest[end:].lstrip()
    return found or None


def rows(text: str | None) -> list[object] | None:
    pages = documents(text or "")
    if pages is None or not all(isinstance(page, list) for page in pages):
        return None
    return [item for page in pages if isinstance(page, list) for item in page]


def newest_page(text: str) -> tuple[list[object], bool] | None:
    pages = documents(text) or []
    page = pages[0] if len(pages) == 1 else None
    runs, total = at(page, "workflow_runs"), at(page, "total_count")
    if not isinstance(runs, list) or not isinstance(total, int) or total < len(runs):
        return None
    return runs, total == len(runs)


def listed(text: str) -> list[object] | None:
    page = newest_page(text)
    return page[0] if page is not None and page[1] else None


def newest(runs: list[object]) -> object:
    return max(runs, key=lambda run: (str(at(run, "created_at")), str(at(run, "id"))))


@dataclass(frozen=True)
class Pull:
    number: int
    sha: str
    branch: str
    base: str
    repository: str

    def twin(self, pull: object) -> bool:
        return (
            at(pull, "head", "ref") == self.branch
            and at(pull, "head", "repo", "full_name") == self.repository
        )

    def is_this(self, pull: object) -> bool:
        return (
            at(pull, "number") == self.number
            and at(pull, "state") == "open"
            and at(pull, "head", "sha") == self.sha
            and at(pull, "base", "ref") == self.base
        )

    def tested(self, run: object) -> bool:
        named = at(run, "pull_requests")
        return (
            at(run, "event") == PULL_REQUEST
            and at(run, "path") == CI_PATH
            and at(run, "head_sha") == self.sha
            and at(run, "head_branch") == self.branch
            and at(run, "head_repository", "full_name") == self.repository
            and isinstance(named, list)
            and any(
                at(pull, "number") == self.number and at(pull, "base", "ref") == self.base
                for pull in named
            )
        )


@dataclass(frozen=True)
class Forge:
    runs: list[object]
    pulls: list[object]
    events: list[object]


def answered(runs: str, pulls: str | None, events: str | None) -> Forge | None:
    twins, changes, ran = rows(pulls), rows(events), listed(runs)
    if twins is None or changes is None or ran is None or len(twins) >= PAGE:
        return None
    return Forge(ran, twins, changes)


@dataclass(frozen=True)
class Doubt:
    code: str
    text: str
    owner: bool


def doubts(pull: Pull, forge: Forge) -> Iterator[Doubt]:
    twins = [twin for twin in forge.pulls if pull.twin(twin)]
    if not any(pull.is_this(twin) for twin in twins):
        this = next((twin for twin in twins if at(twin, "number") == pull.number), None)
        base = at(this, "base", "ref")
        if at(this, "state") == "open" and isinstance(base, str) and base != pull.base:
            text = f"the pull request targets `{base}`, not the default branch `{pull.base}`"
            yield Doubt("target", text, owner=True)
        else:
            text = f"it is not open on {pull.sha} against `{pull.base}`"
            yield Doubt("moved", text, owner=False)
    for twin in twins:
        base = at(twin, "base", "ref")
        if base != pull.base and at(twin, "number") != pull.number:
            text = (
                f"#{at(twin, 'number')} is a pull request from `{pull.branch}` against `{base}`, "
                "so ci on this commit may have run against that branch"
            )
            yield Doubt("twin", text, owner=True)
    if any(at(change, "event") in RETARGETS for change in forge.events):
        text = "its base branch was changed, so ci may have run against the earlier one"
        yield Doubt("retargeted", text, owner=True)
    counted = [run for run in forge.runs if pull.tested(run)]
    if not counted:
        finished = any(at(run, "status") == "completed" for run in forge.runs)
        text = f"no ci run on {pull.sha} names it against `{pull.base}`"
        yield Doubt("unnamed", text, owner=finished)
    elif (at(newest(counted), "status"), at(newest(counted), "conclusion")) != DONE:
        yield Doubt("ci", f"ci is not green on {pull.sha}", owner=False)


def ci(pull: Pull, forge: Forge | None) -> list[Doubt]:
    if forge is None:
        return [Doubt("unreadable", UNREADABLE, owner=False)]
    return list(doubts(pull, forge))


def report(pull: Pull, owners: list[Doubt]) -> str:
    rows = "".join(f"- `{doubt.code}`: {doubt.text}\n" for doubt in owners)
    return (
        f"{MARKER}\n**{OWNER}** at `{pull.sha[:12]}`. The merge job leaves this pull request to"
        f" the owner:\n\n{rows}"
    )


def _distance(root: Path, sha: object, tip: str) -> int | None:
    if not isinstance(sha, str) or not COMMIT.match(sha):
        return None
    apart = gatelib.git(root, "rev-list", "--left-right", "--count", f"{sha}...{tip}") or ""
    only_sha, _, only_tip = apart.partition("\t")
    return int(only_tip) if only_sha == "0" else None


def on_the_tip(runs: str, *, root: Path, tip: str, repository: str, events: frozenset[str]) -> str:
    page = newest_page(runs)
    if page is None:
        return RED
    ran, whole = page
    nearest: dict[int, list[object]] = {}
    for run in ran:
        ours = at(run, "head_repository", "full_name") == repository
        counts = at(run, "status") == "completed" and at(run, "conclusion") not in UNCOUNTED
        if not ours or not counts or at(run, "event") not in events:
            continue
        distance = _distance(root, at(run, "head_sha"), tip)
        if distance is not None:
            nearest.setdefault(distance, []).append(run)
    if not nearest:
        return NONE if whole else RED
    return GREEN if at(newest(nearest[min(nearest)]), "conclusion") == "success" else RED


def read(path: str) -> str | None:
    return gatelib.read_text(Path(path))


def main(argv: list[str] | None = None, runs: str | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    commands = parser.add_subparsers(dest="command", required=True)
    one = commands.add_parser("ci")
    one.add_argument("--pulls", required=True)
    one.add_argument("--events", required=True)
    one.add_argument("--number", type=int, required=True)
    one.add_argument("--sha", required=True)
    one.add_argument("--branch", required=True)
    one.add_argument("--base", required=True)
    one.add_argument("--repository", required=True)
    one.add_argument("--report", type=Path)
    for name in ON_THE_DEFAULT_BRANCH:
        tier = commands.add_parser(name)
        tier.add_argument("--tip", required=True)
        tier.add_argument("--repository", required=True)
        tier.add_argument("--root", default=".")
    options = parser.parse_args(argv)
    text = sys.stdin.read() if runs is None else runs
    if options.command in ON_THE_DEFAULT_BRANCH:
        events = ON_THE_DEFAULT_BRANCH[options.command]
        root, tip, repository = Path(options.root), options.tip, options.repository
        print(on_the_tip(text, root=root, tip=tip, repository=repository, events=events))
        return 0
    pull = Pull(options.number, options.sha, options.branch, options.base, options.repository)
    found = ci(pull, answered(text, read(options.pulls), read(options.events)))
    print(found[0].text if found else GREEN)
    if options.report is not None:
        owners = [doubt for doubt in found if doubt.owner]
        options.report.unlink(missing_ok=True)
        if owners:
            options.report.write_text(report(pull, owners), encoding="utf-8")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
