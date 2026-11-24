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
when no run in it counts. In every locale, standard input is read as the C locale reads it: a
byte that is not UTF-8 leaves its field equal to no word the commands compare it with.

``forge_runs.py deep --answer FILE`` reads the ``deep.yml`` runs of every status and writes what
the merge judge reads with ``merge_ready.py --deep FILE`` (ADR 0077): each run of this repository
that an event of the template started, green, red or running. A run that is not completed is
running, whatever word the forge gives its status, and a cancelled or skipped run is dropped. An
unreadable answer, a kept run whose time is not an instant, or a page cut short that holds no
finished run on the default branch's first-parent line, is not known. A red run on a commit off
that line sits on the merge that brought the commit onto it. It prints ``start`` when no finished
run covers the tip and no run goes on a commit nearer the tip than the nearest one a finished run
covers.

``forge_runs.py speaks --commit SHA --state green|red`` prints which merges of the default branch
the deep run that checked out SHA, a full commit name, speaks for: its commit, or the merge that
brought it onto the first-parent line, and each one below it down to, and not including, the
nearest whose deciding run is green.

Usage: ``forge_runs.py ci --pulls FILE --events FILE --number N --sha SHA --branch NAME --base
NAME --repository OWNER/NAME [--report FILE]``, ``forge_runs.py deep|tier1 --tip REF
--repository OWNER/NAME [--root DIR] [--answer FILE]`` (``--answer`` for ``deep`` only) and
``forge_runs.py speaks --tip REF --repository OWNER/NAME [--root DIR] --commit SHA --state
green|red``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import gatelib
import unproven

if TYPE_CHECKING:
    from collections.abc import Iterator

    import loop_budget

PAGE = 100
CI_PATH = ".github/workflows/ci.yml"
PULL_REQUEST = "pull_request"
DEEP_EVENTS = frozenset({"push", "schedule", "workflow_dispatch"})
ON_THE_DEFAULT_BRANCH = {"deep": DEEP_EVENTS, "tier1": frozenset({"push"})}
RETARGETS = frozenset({"base_ref_changed", "automatic_base_change_succeeded"})
UNCOUNTED = frozenset({"cancelled", "skipped"})
COMMIT = re.compile(r"^[0-9a-f]{40,64}$")
GREEN, RED, NONE = "green", "red", "none"
PASSING = (GREEN, "unbounded")
DONE = ("completed", "success")
UNREADABLE = "the forge's answer could not be read"
MARKER = "<!-- ratchet:merge -->"
OWNER = "merge: owner"
START = "start"
STARTS = "the merge job starts a deep run on the default branch's tip when it next runs"
LOOP_JOB = "loop"
UNSTARTED = frozenset({"queued", "waiting", "pending", "requested"})
JOB_LINE = re.compile(r"^([A-Za-z_][\w-]*):(?:\s|$)")
NEEDS_WORD = re.compile(r"[\w-]+")
WAIT_THRESHOLD = 0.1


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
        except (ValueError, RecursionError):
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
            beside = next(
                (
                    at(twin, "number")
                    for twin in twins
                    if at(twin, "state") == "open" and at(twin, "base", "ref") == pull.base
                ),
                None,
            )
            yield Doubt("target", gatelib.stacked(base, pull.base, beside), owner=True)
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
        text = (
            "its base branch was changed, so ci may have run against the earlier one; the owner"
            " merges this one, and a new commit on its branch starts a ci run against the branch"
            " it targets now"
        )
        yield Doubt("retargeted", text, owner=True)
    counted = [run for run in forge.runs if pull.tested(run)]
    if not counted:
        finished = any(at(run, "status") == "completed" for run in forge.runs)
        text = (
            f"no ci run on {pull.sha} names it against `{pull.base}`; while it targets"
            f" `{pull.base}`, a new commit on its branch starts one, and the merge job judges it"
            " again when that run is green"
        )
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
        try:
            counts = at(run, "status") == "completed" and at(run, "conclusion") not in UNCOUNTED
            if not ours or not counts or at(run, "event") not in events:
                continue
        except TypeError:
            return RED
        distance = _distance(root, at(run, "head_sha"), tip)
        if distance is not None:
            nearest.setdefault(distance, []).append(run)
    if not nearest:
        return NONE if whole else RED
    return GREEN if at(newest(nearest[min(nearest)]), "conclusion") == "success" else RED


def state_of(run: object) -> unproven.State | None:
    if at(run, "status") != "completed":
        return unproven.RUNNING
    conclusion = at(run, "conclusion")
    if isinstance(conclusion, str) and conclusion in UNCOUNTED:
        return None
    return unproven.GREEN if conclusion == "success" else unproven.RED


def deep_runs(items: list[object], repository: str) -> tuple[unproven.DeepRun, ...] | None:
    found = []
    for item in items:
        sha, state, event = at(item, "head_sha"), state_of(item), at(item, "event")
        ours = at(item, "head_repository", "full_name") == repository and isinstance(event, str)
        if not ours or event not in DEEP_EVENTS or state is None:
            continue
        if not isinstance(sha, str) or not COMMIT.match(sha):
            continue
        when = unproven.instant(at(item, "created_at"))
        if when is None:
            return None
        url, number = at(item, "html_url"), at(item, "id")
        if not url or not unproven.printable(url):
            url = f"run {number if isinstance(number, int) or unproven.printable(number) else '?'}"
        found.append(unproven.DeepRun(sha, state, when, url))
    return tuple(found)


def asks_start(line: tuple[unproven.Landed, ...], deep: unproven.Deep) -> bool:
    seen = None if deep.runs is None else unproven.reading(line, deep.runs)
    return seen is not None and seen.nearest > 0 and seen.ender is None


def speaks(line: tuple[unproven.Landed, ...], deep: unproven.Deep, *, sha: str, state: str) -> str:
    head = f"deep: {state} at `{sha[: unproven.SHORT]}`."
    if deep.runs is None:
        return (
            f"{head} The forge's answer could not be read, so which merges of the default branch"
            " it speaks for is not known.\n"
        )
    said: unproven.State = unproven.RED if state == RED else unproven.GREEN
    run = unproven.DeepRun(sha, said, datetime.now(UTC), "this run")
    rows = "".join(
        f"- `{merge.commit[: unproven.SHORT]}` {merge.subject}\n"
        for merge in unproven.speaks_for(line, deep, run)
    )
    return f"{head} The merges of the default branch it speaks for:\n{rows}"


def answer_or_speak(options: argparse.Namespace, runs: str) -> int:
    page = newest_page(runs)
    found = None if page is None else deep_runs(page[0], options.repository)
    asked = [options.commit] if options.command == "speaks" else []
    seen = [run.commit for run in found or ()]
    line = gatelib.chain(Path(options.root), options.tip, [*seen, *asked])
    covered = found is not None and unproven.reading(line, found).decider is not None
    whole = page is not None and page[1]
    deep = unproven.Deep(found if whole or covered else None, STARTS)
    if options.command == "speaks":
        print(speaks(line, deep, sha=options.commit, state=options.state), end="")
        return 0
    options.answer.write_text(unproven.written(deep), encoding="utf-8")
    if asks_start(line, deep):
        print(START)
    return 0


def read(path: str) -> str | None:
    return gatelib.read_text(Path(path))


def strip_yaml_comment(text: str) -> str:
    quote = ""
    for index, char in enumerate(text):
        if quote:
            if char == quote:
                quote = ""
        elif char in ("'", '"'):
            quote = char
        elif char == "#" and (index == 0 or text[index - 1].isspace()):
            return text[:index].rstrip()
    return text.rstrip()


def unquote_yaml(raw: str) -> str:
    stripped = strip_yaml_comment(raw).strip()
    if len(stripped) > 1 and stripped[0] == stripped[-1] and stripped[0] in ("'", '"'):
        return stripped[1:-1]
    return stripped


def collect_job_lines(content: str) -> list[tuple[str, list[str]]]:
    lines = content.splitlines()
    jobs_at = next((i for i, line in enumerate(lines) if strip_yaml_comment(line) == "jobs:"), None)
    if jobs_at is None:
        return []
    found: list[tuple[str, list[str]]] = []
    job_indent: int | None = None
    for line in lines[jobs_at + 1 :]:
        stripped = line.lstrip(" ")
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(stripped)
        if indent == 0:
            break
        if job_indent is None:
            job_indent = indent
        if indent == job_indent and (match := JOB_LINE.match(stripped)):
            found.append((match.group(1), []))
        elif found and indent > job_indent:
            found[-1][1].append(line)
    return found


def parse_job_needs(job_lines: list[str]) -> tuple[str, ...]:
    indents = [
        len(line) - len(line.lstrip(" "))
        for line in job_lines
        if strip_yaml_comment(line.lstrip(" "))
    ]
    if not indents:
        return ()
    attr_indent = min(indents)
    for idx, line in enumerate(job_lines):
        stripped = line.lstrip(" ")
        indent = len(line) - len(stripped)
        cleaned = strip_yaml_comment(stripped)
        if indent != attr_indent or not (cleaned == "needs:" or cleaned.startswith("needs: ")):
            continue
        rhs = cleaned.partition(":")[2].strip()
        if rhs.startswith("["):
            parts = [rhs]
            for follow in job_lines[idx + 1 :]:
                if "]" in parts[-1]:
                    break
                parts.append(strip_yaml_comment(follow))
            return tuple(NEEDS_WORD.findall(" ".join(parts)))
        if rhs:
            val = unquote_yaml(rhs)
            return (val,) if val else ()
        return block_needs(job_lines[idx + 1 :], attr_indent)
    return ()


def block_needs(following: list[str], attr_indent: int) -> tuple[str, ...]:
    items: list[str] = []
    for line in following:
        stripped = line.lstrip(" ")
        cleaned = strip_yaml_comment(stripped)
        indent = len(line) - len(stripped)
        if indent <= attr_indent and not cleaned.startswith("- "):
            break
        if cleaned.startswith("- "):
            val = unquote_yaml(cleaned[2:])
            if val:
                items.append(val)
    return tuple(items)


def workflow_needs(content: str) -> dict[str, tuple[str, ...]]:
    return {
        job: tuple(dep for dep in parse_job_needs(lines) if dep != LOOP_JOB)
        for job, lines in collect_job_lines(content)
        if job != LOOP_JOB
    }


@dataclass(frozen=True, slots=True)
class JobSpan:
    name: str
    wf_job: str
    created_at: datetime | None
    started_at: datetime
    completed_at: datetime
    seconds: float
    wait: float = 0.0


@dataclass(frozen=True, slots=True)
class ArtifactData:
    gates: tuple[loop_budget.GateTime, ...]
    by_label: dict[str, tuple[float, bool]]
    bite_labels: frozenset[str]
    fallback_machine: tuple[float, bool]
    forced: loop_budget.Forced | None


def combine_machine(pairs: list[tuple[float, float, bool]]) -> tuple[float, bool]:
    import machine  # noqa: PLC0415 # -- keep ci, deep and tier1 standalone without loop_budget

    total = sum(sec for sec, _, _ in pairs)
    if total <= 0:
        return 1.0, False
    unexplained = 0.0
    all_read = True
    for sec, m_val, read_ok in pairs:
        if not read_ok:
            all_read = False
            unexplained += sec
        else:
            eff = m_val if m_val >= machine.NOISE_FLOOR else 1.0
            unexplained += sec / eff
    return total / unexplained, all_read


def read_artifacts(artifacts_dir: Path | None, root: Path, *, run: str, tree: str) -> ArtifactData:
    import loop_budget  # noqa: PLC0415 # -- keep ci, deep and tier1 standalone without loop_budget
    import runrecord  # noqa: PLC0415 # -- keep ci, deep and tier1 standalone without loop_budget

    if artifacts_dir is None or not artifacts_dir.is_dir():
        return ArtifactData((), {}, frozenset(), (1.0, False), None)
    all_records: list[dict[str, object]] = []
    gates_map: dict[str, tuple[float, bool]] = {}
    by_label: dict[str, tuple[float, bool]] = {}
    bite_labels: set[str] = set()
    all_machine: list[tuple[float, float, bool]] = []
    for path in sorted(artifacts_dir.rglob("timing.jsonl")):
        stem = path.parent.name.removeprefix("timing-")
        records = list(runrecord.read(path))
        all_records.extend(records)
        label_pairs: list[tuple[float, float, bool]] = []
        for rec in records:
            if rec.get("key") == loop_budget.PROOF_GATE or loop_budget.forced_tag(
                rec.get("forced")
            ):
                bite_labels.add(stem)
            if "kind" in rec:
                continue
            key, sec = rec.get("key"), loop_budget.finite_seconds(rec.get("seconds"))
            if not isinstance(key, str) or sec is None:
                continue
            green = rec.get("status") in PASSING
            prev = gates_map.get(key)
            gates_map[key] = (
                max(prev[0], sec) if prev is not None else sec,
                (prev[1] if prev is not None else True) and green,
            )
            m_raw = loop_budget.finite_seconds(rec.get("m"))
            read_ok = isinstance(rec.get("machine_read"), bool) and bool(rec["machine_read"])
            pair = (max(sec, 1e-6), m_raw if m_raw is not None else 1.0, read_ok)
            label_pairs.append(pair)
            all_machine.append(pair)
        if label_pairs:
            by_label[stem] = combine_machine(label_pairs)
    gates = tuple(loop_budget.GateTime(k, sec, ok) for k, (sec, ok) in gates_map.items())
    forced = loop_budget.forced_from_records(root, all_records, run=run, tree=tree)
    return ArtifactData(
        gates=gates,
        by_label=by_label,
        bite_labels=frozenset(bite_labels),
        fallback_machine=combine_machine(all_machine),
        forced=forced,
    )


def map_workflow_job(name: object, dag: dict[str, tuple[str, ...]]) -> str | None:
    if not isinstance(name, str) or name == LOOP_JOB or name.startswith(f"{LOOP_JOB} ("):
        return None
    if name in dag:
        return name
    prefix = name.partition(" (")[0]
    return prefix if " (" in name and prefix in dag else None


def parse_jobs_payload(
    text: str, *, jobs_status: int, dag: dict[str, tuple[str, ...]]
) -> list[JobSpan] | str:
    if jobs_status != 0:
        return f"gh exited {jobs_status}"
    if not text.strip():
        return "jobs JSON is empty"
    pages = documents(text)
    if pages is None or not all(isinstance(page, dict) for page in pages):
        return "jobs JSON is malformed"
    jobs: list[object] = []
    total_count: int | None = None
    for page in pages:
        page_jobs = page["jobs"] if isinstance(page, dict) else None
        if not isinstance(page_jobs, list):
            return "jobs JSON has no jobs list"
        jobs.extend(page_jobs)
        tc = page.get("total_count") if isinstance(page, dict) else None
        if isinstance(tc, int) and not isinstance(tc, bool):
            total_count = tc
    if total_count is not None and len(jobs) < total_count:
        return f"jobs JSON is truncated ({len(jobs)} of {total_count})"
    return parse_job_spans(jobs, dag)


def parse_job_spans(jobs: list[object], dag: dict[str, tuple[str, ...]]) -> list[JobSpan] | str:
    spans: list[JobSpan] = []
    for entry in jobs:
        name = at(entry, "name")
        wf_job = map_workflow_job(name, dag)
        if wf_job is None:
            continue
        status, conclusion = at(entry, "status"), at(entry, "conclusion")
        if status in UNSTARTED or conclusion in UNCOUNTED:
            continue
        start = unproven.instant(at(entry, "started_at"))
        end = unproven.instant(at(entry, "completed_at"))
        if start is None or end is None:
            return f"job {name!r} has missing timestamps"
        if end < start:
            return f"job {name!r} completed before it started"
        created = unproven.instant(at(entry, "created_at"))
        spans.append(JobSpan(str(name), wf_job, created, start, end, (end - start).total_seconds()))
    if not spans:
        return "no completed jobs found"
    return spans


def topological_jobs(dag: dict[str, tuple[str, ...]]) -> list[str] | None:
    indegree = {job: len([d for d in deps if d in dag]) for job, deps in dag.items()}
    ready = [job for job, deg in indegree.items() if deg == 0]
    order: list[str] = []
    while ready:
        cur = ready.pop(0)
        order.append(cur)
        for job, deps in dag.items():
            if cur in deps:
                indegree[job] -= 1
                if indegree[job] == 0:
                    ready.append(job)
    return order if len(order) == len(dag) else None


def span_stems(span: JobSpan) -> tuple[str, ...]:
    if " (" not in span.name:
        return (span.wf_job,)
    inner = span.name.partition(" (")[2].removesuffix(")")
    parts = [part.strip() for part in inner.split(",")]
    joined = "-".join(parts)
    return (joined, f"{span.wf_job}-{joined}", span.wf_job)


def assign_waits(spans: list[JobSpan], dag: dict[str, tuple[str, ...]]) -> list[JobSpan]:
    by_wf: dict[str, list[JobSpan]] = {}
    for span in spans:
        by_wf.setdefault(span.wf_job, []).append(span)
    earliest_created = min(
        (span.created_at for span in spans if span.created_at is not None),
        default=min(span.started_at for span in spans),
    )
    updated: list[JobSpan] = []
    for span in spans:
        dep_ends = [
            dep_span.completed_at
            for dep in dag.get(span.wf_job, ())
            for dep_span in by_wf.get(dep, ())
        ]
        ready_at = max(dep_ends) if dep_ends else (span.created_at or earliest_created)
        wait = max(0.0, (span.started_at - ready_at).total_seconds())
        updated.append(
            JobSpan(
                span.name,
                span.wf_job,
                span.created_at,
                span.started_at,
                span.completed_at,
                span.seconds,
                wait,
            )
        )
    return updated


@dataclass(frozen=True, slots=True)
class LoopMetrics:
    root: Path
    place: loop_budget.Place
    run_id: str
    tree_id: str
    raw_wp: float | None
    wall_wait: float
    max_wait: float
    m_val: float
    machine_read: bool
    exit_code: int


def compute_work_path(
    spans: list[JobSpan],
    dag: dict[str, tuple[str, ...]],
    order: list[str],
    *,
    artifacts: ArtifactData,
) -> tuple[float, float, list[JobSpan]]:
    import loop_budget  # noqa: PLC0415 # -- keep ci, deep and tier1 standalone without loop_budget

    by_wf: dict[str, list[JobSpan]] = {}
    for span in spans:
        by_wf.setdefault(span.wf_job, []).append(span)
    forced = artifacts.forced
    raw_end: dict[str, float] = {}
    net_end: dict[str, float] = {}
    net_path: dict[str, list[JobSpan]] = {}
    subtracted_any = False
    for wf_job in order:
        deps = [dep for dep in dag[wf_job] if dep in raw_end]
        raw_base = max((raw_end[dep] for dep in deps), default=0.0)
        net_best_dep = max(deps, key=lambda dep: net_end[dep], default=None)
        net_base = net_end[net_best_dep] if net_best_dep is not None else 0.0
        base_path = list(net_path[net_best_dep]) if net_best_dep is not None else []
        job_spans = by_wf.get(wf_job, [])
        if not job_spans:
            raw_end[wf_job], net_end[wf_job], net_path[wf_job] = raw_base, net_base, base_path
            continue
        raw_end[wf_job] = raw_base + max(span.seconds for span in job_spans)
        best_span = job_spans[0]
        best_end = -1.0
        for span in job_spans:
            stems = span_stems(span)
            is_bite = forced is not None and (
                any(stem in artifacts.bite_labels for stem in stems)
                or f"({loop_budget.PROOF_GATE}" in span.name
            )
            dur = max(0.0, span.seconds - forced.seconds) if is_bite and forced else span.seconds
            if is_bite:
                subtracted_any = True
            if net_base + dur > best_end:
                best_end = net_base + dur
                best_span = span
        net_end[wf_job] = best_end
        net_path[wf_job] = [*base_path, best_span]
    raw_wp = max(raw_end.values(), default=0.0)
    best_job = max(net_end, key=lambda job: net_end[job])
    net_wp = net_end[best_job]
    if forced is not None and not subtracted_any:
        net_wp = max(0.0, raw_wp - forced.seconds)
    return raw_wp, net_wp, net_path[best_job]


def machine_for_path(path_spans: list[JobSpan], artifacts: ArtifactData) -> tuple[float, bool]:
    pairs: list[tuple[float, float, bool]] = []
    for span in path_spans:
        for stem in span_stems(span):
            if stem in artifacts.by_label:
                m_val, read_ok = artifacts.by_label[stem]
                pairs.append((max(span.seconds, 1e-6), m_val, read_ok))
                break
    return combine_machine(pairs) if pairs else artifacts.fallback_machine


def record_loop_run(
    report_path: Path | None,
    *,
    spans: list[JobSpan],
    artifacts: ArtifactData,
    result: loop_budget.Result,
    metrics: LoopMetrics,
) -> None:
    import runrecord  # noqa: PLC0415 # -- keep ci, deep and tier1 standalone without loop_budget

    if report_path is None:
        return
    for span in spans:
        runrecord.append(
            report_path,
            {
                "kind": "wait",
                "job": span.name,
                "workflow_job": span.wf_job,
                "seconds": round(span.seconds, 3),
                "wait": round(span.wait, 3),
                "run": metrics.run_id,
                "tree": metrics.tree_id,
            },
            root=metrics.root,
            who=LOOP_JOB,
        )
    for gate in artifacts.gates:
        runrecord.append(
            report_path,
            {
                "key": gate.name,
                "seconds": round(gate.seconds, 3),
                "status": GREEN if gate.green else RED,
                "run": metrics.run_id,
                "tree": metrics.tree_id,
            },
            root=metrics.root,
            who=LOOP_JOB,
        )
    runrecord.append(
        report_path,
        {
            "kind": runrecord.TIER,
            "tier": LOOP_JOB,
            "place": metrics.place.value,
            "gates": [gate.name for gate in artifacts.gates],
            "seconds": round(metrics.raw_wp, 3) if metrics.raw_wp is not None else 0.0,
            "runner_wait": round(metrics.wall_wait, 3),
            "max_wait": round(metrics.max_wait, 3),
            "status": GREEN if metrics.exit_code == 0 else RED,
            "m": round(metrics.m_val, 3),
            "machine_read": metrics.machine_read,
            **result.tier_fields(),
            "run": metrics.run_id,
            "tree": metrics.tree_id,
        },
        root=metrics.root,
        who=LOOP_JOB,
    )


def resolve_loop_spans(
    wf_text: str | None,
    jobs_text: str | None,
    options: argparse.Namespace,
) -> tuple[dict[str, tuple[str, ...]], list[str], list[JobSpan]] | str:
    if wf_text is None:
        return f"workflow {options.workflow} could not be read"
    dag = workflow_needs(wf_text)
    order = topological_jobs(dag)
    if order is None:
        return "workflow needs graph has a cycle"
    if jobs_text is None:
        return f"jobs file {options.jobs} could not be read"
    parsed = parse_jobs_payload(jobs_text, jobs_status=options.jobs_status, dag=dag)
    if isinstance(parsed, str):
        return parsed
    return dag, order, assign_waits(parsed, dag)


def analyze_loop_reading(
    spans: list[JobSpan],
    dag: dict[str, tuple[str, ...]],
    order: list[str],
    *,
    artifacts: ArtifactData,
) -> tuple[loop_budget.Reading, float, float, float]:
    import loop_budget  # noqa: PLC0415 # -- keep ci, deep and tier1 standalone without loop_budget

    raw_wp, net_wp, path_spans = compute_work_path(spans, dag, order, artifacts=artifacts)
    eff_forced: loop_budget.Forced | None = None
    if artifacts.forced is not None and raw_wp - net_wp > 0:
        eff_forced = loop_budget.Forced(
            artifacts.forced.cause, artifacts.forced.record, raw_wp - net_wp
        )
    m_val, machine_read = machine_for_path(path_spans, artifacts)
    reading = loop_budget.Reading(raw_wp, m_val, machine_read, forced=eff_forced)
    start_anchor = min(span.created_at or span.started_at for span in spans)
    run_wall = max(0.0, (max(s.completed_at for s in spans) - start_anchor).total_seconds())
    wall_wait = max(0.0, run_wall - raw_wp)
    max_wait = max(span.wait for span in spans)
    if run_wall > 0 and max_wait > WAIT_THRESHOLD * run_wall:
        print(
            f"loop: runner wait: {max_wait:.1f}s max"
            f" ({wall_wait:.1f}s across {run_wall:.1f}s run, not judged)"
        )
    return reading, raw_wp, wall_wait, max_wait


def run_loop_command(options: argparse.Namespace, jobs_text: str | None) -> int:
    import loop_budget  # noqa: PLC0415 # -- keep ci, deep and tier1 standalone without loop_budget
    import runrecord  # noqa: PLC0415 # -- keep ci, deep and tier1 standalone without loop_budget

    root = Path(options.root)
    run_id = os.environ.get("RATCHET_RUN") or runrecord.NO_RUN
    tree_id = runrecord.tree_id(root)
    place = loop_budget.Place(options.place)
    artifacts = read_artifacts(options.artifacts, root, run=run_id, tree=tree_id)
    resolved = resolve_loop_spans(gatelib.read_text(Path(options.workflow)), jobs_text, options)
    spans: list[JobSpan] = []
    raw_wp: float | None = None
    wall_wait, max_wait = 0.0, 0.0
    if isinstance(resolved, str):
        reading = loop_budget.Reading(None, 1.0, machine_read=False, detail=resolved)
    else:
        dag, order, spans = resolved
        reading, raw_wp, wall_wait, max_wait = analyze_loop_reading(
            spans, dag, order, artifacts=artifacts
        )
    eval_opts = loop_budget.EvalOptions(
        place=place,
        tree=tree_id,
        run=run_id,
        exclude_proof=artifacts.forced is not None,
    )
    result = loop_budget.evaluate_run(
        root, reading=reading, report=options.report, gates=artifacts.gates, options=eval_opts
    )
    exit_code = result.exit_for(0)
    if result.effect.annotation:
        print(f"::warning title=ratchet-loop::{result.line()}")
    print(result.line())
    metrics = LoopMetrics(
        root,
        place,
        run_id,
        tree_id,
        raw_wp,
        wall_wait,
        max_wait,
        reading.m,
        reading.machine_read,
        exit_code,
    )
    record_loop_run(
        options.report,
        spans=spans,
        artifacts=artifacts,
        result=result,
        metrics=metrics,
    )
    return exit_code


def build_parser() -> argparse.ArgumentParser:
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
    loop_cmd = commands.add_parser("loop")
    loop_cmd.add_argument("--workflow", type=Path, required=True)
    loop_cmd.add_argument("--jobs", type=Path)
    loop_cmd.add_argument("--jobs-status", type=int, default=0)
    loop_cmd.add_argument("--artifacts", type=Path)
    places = ("pull-request", "default-branch")
    loop_cmd.add_argument("--place", choices=places, required=True)
    loop_cmd.add_argument("--root", default=".")
    loop_cmd.add_argument("--report", type=Path)
    tiers = {}
    for name in (*ON_THE_DEFAULT_BRANCH, "speaks"):
        tiers[name] = commands.add_parser(name)
        tiers[name].add_argument("--tip", required=True)
        tiers[name].add_argument("--repository", required=True)
        tiers[name].add_argument("--root", default=".")
    tiers["deep"].add_argument("--answer", type=Path)
    tiers["speaks"].add_argument("--commit", required=True)
    tiers["speaks"].add_argument("--state", choices=(GREEN, RED), required=True)
    parser.set_defaults(answer=None)
    return parser


def main(argv: list[str] | None = None, runs: str | None = None) -> int:
    parser = build_parser()
    options = parser.parse_args(argv)
    if options.command == "loop":
        jobs_text = (
            gatelib.read_text(options.jobs)
            if options.jobs is not None
            else sys.stdin.buffer.read().decode("utf-8", "surrogateescape")
        )
        return run_loop_command(options, jobs_text)
    if options.command == "speaks" and not COMMIT.fullmatch(options.commit):
        parser.error(f"argument --commit: {options.commit!a} is not a full commit name")
    text = sys.stdin.buffer.read().decode("utf-8", "surrogateescape") if runs is None else runs
    if options.command == "speaks" or options.answer is not None:
        return answer_or_speak(options, text)
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
