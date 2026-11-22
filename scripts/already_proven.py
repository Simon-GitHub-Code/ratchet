#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import tempfile
import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import budget
import ci_jobs
import gatelib
import justfile_text
import runrecord
import schedule

CI_PATH = ".github/workflows/ci.yml"
PULL_REQUEST = "pull_request"
COMPLETED = "completed"
SUCCESS = "success"
DEFAULT_MAX_AGE = 3600.0
FUTURE_SKEW = 60.0
SCHEMA = 1
PARENTS_COUNT = 2
COMMIT = re.compile(r"^[0-9a-f]{40,64}$")
DIGITS = re.compile(r"^[0-9]+$")
INSTANT = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{1,6})?(?:Z|[+-][0-9]{2}:[0-9]{2})"
)


@dataclass(frozen=True)
class MergeHead:
    base: str
    head: str
    tree: str
    head_tree: str


def at(value: object, *keys: str) -> object:
    for key in keys:
        value = value.get(key) if isinstance(value, dict) else None
    return value


def is_exact_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def parse_attempt(raw: str) -> int | None:
    stripped = raw.strip()
    return int(stripped) if DIGITS.fullmatch(stripped) else None


def parse_instant(value: object) -> datetime | None:
    if not isinstance(value, str) or not INSTANT.fullmatch(value):
        return None
    try:
        return datetime.fromisoformat(value).astimezone(UTC)
    except ValueError:
        return None


def parse_now(raw: str | None) -> datetime | None:
    return datetime.now(UTC) if raw is None else parse_instant(raw)


def valid_max_age(max_age: float) -> bool:
    return math.isfinite(max_age) and max_age >= 0


def merge_head(root: Path) -> MergeHead | str:
    parents_raw = gatelib.git(root, "show", "-s", "--format=%P", "HEAD")
    if parents_raw is None:
        return "HEAD could not be read from git"
    parents = parents_raw.split()
    if len(parents) != PARENTS_COUNT or not all(COMMIT.fullmatch(sha) for sha in parents):
        return "HEAD does not have exactly 2 parents"
    tree = gatelib.git(root, "rev-parse", "--verify", "HEAD^{tree}")
    head_tree = gatelib.git(root, "rev-parse", "--verify", f"{parents[1]}^{{tree}}")
    if not (tree and head_tree):
        return "HEAD tree could not be read from git"
    return MergeHead(parents[0], parents[1], tree, head_tree)


def preflight(options: argparse.Namespace) -> tuple[MergeHead, datetime] | str:
    if parse_attempt(options.attempt) != 1:
        return "attempt is not 1"
    now = parse_now(options.now)
    if now is None:
        return "invalid --now timestamp"
    if not valid_max_age(options.max_age):
        return "invalid --max-age"
    head = merge_head(Path(options.root))
    if isinstance(head, str):
        return head
    if head.tree != head.head_tree:
        return "HEAD tree differs from PR head tree"
    return head, now


def write_output(path: Path | None, key: str, value: str) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"{key}={value}\n")


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        tmp.replace(path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def read_bytes_file(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except OSError:
        return None


def decode_json(raw: bytes) -> tuple[object, str]:
    text = raw.decode("utf-8", "surrogateescape")
    try:
        return json.loads(text), ""
    except (ValueError, RecursionError):
        return None, "malformed JSON"


def read_json_dict(path: Path, label: str) -> dict[str, object] | str:
    raw = read_bytes_file(path)
    if raw is None:
        return f"{label} file could not be read"
    data, err = decode_json(raw)
    if err:
        return err
    if not isinstance(data, dict):
        return f"{label} JSON is not an object"
    return data


def run_timestamp(run: dict[str, object]) -> datetime | None:
    created = parse_instant(run.get("created_at"))
    if created is None:
        return None
    raw_started = run.get("run_started_at")
    if raw_started is None:
        return created
    started = parse_instant(raw_started)
    return None if started is None else min(created, started)


def within_age(ts: datetime, now: datetime, max_age: float) -> bool:
    return -FUTURE_SKEW <= (now - ts).total_seconds() <= max_age


def run_matches_common(run: dict[str, object], repository: str, head_sha: str) -> bool:
    run_id = run.get("id")
    return (
        is_exact_int(run_id)
        and cast("int", run_id) > 0
        and at(run, "repository", "full_name") == repository
        and run.get("path") == CI_PATH
        and run.get("event") == PULL_REQUEST
        and run.get("status") == COMPLETED
        and run.get("conclusion") == SUCCESS
        and run.get("head_sha") == head_sha
    )


def run_matches_branch(run: dict[str, object], base_branch: str) -> bool:
    prs = run.get("pull_requests")
    if prs is None or prs == []:
        return True
    if not isinstance(prs, list):
        return False
    return any(at(pr, "base", "ref") == base_branch for pr in prs)


def cmd_record(root: Path, out: Path) -> int:
    head = merge_head(root)
    if isinstance(head, str):
        return 0
    payload = {"schema": SCHEMA, "base": head.base, "head": head.head, "tree": head.tree}
    try:
        atomic_write(out, json.dumps(payload) + "\n")
    except OSError:
        return 0
    return 0


def read_runs_payload(runs_arg: str | None) -> list[object] | str:
    raw = sys.stdin.buffer.read() if runs_arg is None else read_bytes_file(Path(runs_arg))
    if raw is None:
        return "runs input could not be read"
    data, err = decode_json(raw)
    if err:
        return err
    runs = data.get("workflow_runs") if isinstance(data, dict) else None
    if not isinstance(runs, list):
        return "workflow_runs is not a list"
    return runs


def candidate_key(
    item: object, *, options: argparse.Namespace, head: MergeHead, now: datetime
) -> tuple[datetime, int] | None:
    if not isinstance(item, dict):
        return None
    if not run_matches_common(item, options.repository, head.head):
        return None
    if not run_matches_branch(item, options.base_branch):
        return None
    ts = run_timestamp(item)
    created = parse_instant(item.get("created_at"))
    if ts is None or created is None or not within_age(ts, now, options.max_age):
        return None
    return created, cast("int", item["id"])


def select_candidate(options: argparse.Namespace) -> int | str:
    checked = preflight(options)
    if isinstance(checked, str):
        return checked
    head, now = checked
    runs = read_runs_payload(options.runs)
    if isinstance(runs, str):
        return runs
    candidates = [
        key
        for item in runs
        if (key := candidate_key(item, options=options, head=head, now=now)) is not None
    ]
    if not candidates:
        return "no qualifying pull_request run found"
    _, best_id = max(candidates)
    return best_id


def cmd_select(options: argparse.Namespace) -> int:
    output = options.output
    try:
        result = select_candidate(options)
    except Exception as error:  # noqa: BLE001 # -- fail-safe fallback must never exit non-zero
        result = str(error)
    if isinstance(result, str):
        print(f"already_proven: run: {result}")
        write_output(output, "run_id", "")
        return 0
    print(f"already_proven: candidate {result}")
    write_output(output, "run_id", str(result))
    return 0


def read_thresholds(root: Path) -> tuple[str, str]:
    path = root / gatelib.THRESHOLDS
    if not path.is_file():
        return "", ""
    raw = read_bytes_file(path)
    if raw is None:
        return "", "thresholds.toml could not be read"
    try:
        text = raw.decode("utf-8")
        tomllib.loads(text)
    except (UnicodeDecodeError, tomllib.TOMLDecodeError):
        return "", "thresholds.toml is invalid"
    return text, ""


def expected_tier1_jobs(root: Path) -> tuple[dict[str, int], set[str]] | str:
    if not (root / "justfile").is_file():
        return {}, set()
    justfile = schedule.justfile(root)
    found = ci_jobs.lists(justfile)
    if found is None:
        return {}, set()
    if isinstance(found, ci_jobs.Own):
        return f"justfile: {found.reason}"
    if ci_jobs.refused(found):
        return "justfile has invalid tier1_jobs"
    binding = gatelib.read_text(root / gatelib.BINDING) or ""
    count = ci_jobs.shard_count(binding)
    if count is None:
        return "binding has no valid - Shards: N line"
    plan = ci_jobs.planned(ci_jobs.resolved(found), ci_jobs.waiting(binding, justfile))
    expected = {
        job.name: count if (job.alone and job.name in found.sharded) else 1 for job in plan.jobs
    }
    groups = {job.name for job in plan.jobs if not job.alone}
    return expected, groups


def parse_jsonl_line(line: str, name: str) -> dict[str, object] | str:
    if not line.strip():
        return f"blank line in {name}"
    try:
        item: object = json.loads(line)
    except (ValueError, RecursionError):
        return f"corrupt JSON line in {name}"
    if not isinstance(item, dict):
        return f"non-object record in {name}"
    return item


def parse_jsonl_file(path: Path) -> list[dict[str, object]] | str:
    raw = read_bytes_file(path)
    if raw is None:
        return f"could not read {path.name}"
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return f"invalid UTF-8 in {path.name}"
    lines = text.splitlines()
    if not lines:
        return f"empty timing file {path.name}"
    records: list[dict[str, object]] = []
    for line in lines:
        parsed = parse_jsonl_line(line, path.name)
        if isinstance(parsed, str):
            return parsed
        records.append(parsed)
    return records


def check_record_flags(record: dict[str, object]) -> str:
    if "over" in record and record["over"] is not False:
        return "timing record has over != false"
    if "status" in record and record["status"] not in ("green", "unbounded"):
        return f"timing record has status {record['status']!r}"
    if "exit" in record and (not is_exact_int(record["exit"]) or record["exit"] != 0):
        return f"timing record has exit {record['exit']!r}"
    return ""


def validate_budget_record(
    record: dict[str, object], *, thresholds: str, unshared_keys: set[str]
) -> str:
    key = record.get("key")
    seconds = record.get("seconds")
    if not isinstance(key, str) or not key:
        return "budget record missing key"
    valid_seconds = (
        not isinstance(seconds, bool)
        and isinstance(seconds, int | float)
        and 0 <= seconds < math.inf
    )
    if not valid_seconds:
        return f"budget record {key} has invalid seconds"
    if "exit" not in record:
        return f"budget record {key} missing exit"
    tier = None if (key == "tier0" or key in unshared_keys) else "tier1"
    ceiling, _ = budget.held(thresholds, key, tier)
    if ceiling is not None and float(cast("float", seconds)) > ceiling:
        return f"{key} took {seconds}s over ceiling {ceiling}s"
    return ""


def tier0_file_gates(records: list[dict[str, object]]) -> set[str]:
    if not any("kind" not in r and r.get("key") == "tier0" for r in records):
        return set()
    return {
        str(gate)
        for r in records
        if r.get("kind") == runrecord.TIER and isinstance(gates := r.get("gates"), list)
        for gate in gates
    }


def validate_file_records(
    records: list[dict[str, object]],
    *,
    thresholds: str,
    tier0_gates: set[str],
    counts: dict[str, int],
) -> str:
    group_jobs = {
        cast("str", r["job"])
        for r in records
        if r.get("kind") == budget.GROUP and isinstance(r.get("job"), str)
    }
    unshared = group_jobs | tier0_gates | tier0_file_gates(records)
    for record in records:
        if err := check_record_flags(record):
            return err
        if "kind" in record:
            continue
        if err := validate_budget_record(record, thresholds=thresholds, unshared_keys=unshared):
            return err
        key = cast("str", record["key"])
        counts[key] = counts.get(key, 0) + 1
    return ""


def validate_timings_dir(
    timings_dir: Path, thresholds: str, tier0_gates: set[str]
) -> dict[str, int] | str:
    if not timings_dir.is_dir():
        return "timings directory missing"
    files = sorted(timings_dir.rglob("*.jsonl"))
    if not files:
        return "no timing jsonl files found"
    counts: dict[str, int] = {}
    for path in files:
        records = parse_jsonl_file(path)
        if isinstance(records, str):
            return records
        if err := validate_file_records(
            records, thresholds=thresholds, tier0_gates=tier0_gates, counts=counts
        ):
            return err
    return counts


def verify_run_and_proven(options: argparse.Namespace, head: MergeHead, now: datetime) -> int | str:
    run_obj = read_json_dict(Path(options.run), "run")
    if isinstance(run_obj, str):
        return run_obj
    if not run_matches_common(run_obj, options.repository, head.head):
        return "run does not match repository, workflow, event, status, conclusion, or head"
    ts = run_timestamp(run_obj)
    if ts is None or not within_age(ts, now, options.max_age):
        return "run timestamp is outside allowed age window"
    proven = read_json_dict(Path(options.proven), "proven")
    if isinstance(proven, str):
        return proven
    schema = proven.get("schema")
    if (
        not is_exact_int(schema)
        or schema != SCHEMA
        or proven.get("base") != head.base
        or proven.get("head") != head.head
        or proven.get("tree") != head.tree
    ):
        return "proven.json does not match HEAD base, head, and tree"
    return cast("int", run_obj["id"])


def verify_timings_match(root: Path, timings_dir: Path) -> str:
    planned = expected_tier1_jobs(root)
    if isinstance(planned, str):
        return planned
    expected, planned_groups = planned
    thresholds, thresh_err = read_thresholds(root)
    if thresh_err:
        return thresh_err
    tier0_gates = set(
        justfile_text.ordered(schedule.justfile(root), "tier0_gates") or ()
        if (root / "justfile").is_file()
        else ()
    )
    counts = validate_timings_dir(timings_dir, thresholds, tier0_gates | planned_groups)
    if isinstance(counts, str):
        return counts
    if counts.get("tier0", 0) < 1:
        return "missing passing tier0 timing record"
    missing = next((job for job, need in expected.items() if counts.get(job, 0) < need), None)
    return "" if missing is None else f"missing passing timing record for {missing}"


def judge_skip(options: argparse.Namespace) -> tuple[bool, str]:
    checked = preflight(options)
    if isinstance(checked, str):
        return False, checked
    head, now = checked
    run_id = verify_run_and_proven(options, head, now)
    if isinstance(run_id, str):
        return False, run_id
    if err := verify_timings_match(Path(options.root), Path(options.timings)):
        return False, err
    return (
        True,
        f"PR run {run_id} already settled tree {head.tree[:12]} against base {head.base[:12]}",
    )


def cmd_judge(options: argparse.Namespace) -> int:
    output = options.output
    try:
        skip, message = judge_skip(options)
    except Exception as error:  # noqa: BLE001 # -- fail-safe fallback must never exit non-zero
        skip, message = False, str(error)
    prefix = "skip" if skip else "run"
    print(f"already_proven: {prefix}: {message}")
    write_output(output, "skip", "true" if skip else "false")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="already_proven.py")
    sub = parser.add_subparsers(dest="command", required=True)
    rec = sub.add_parser("record")
    rec.add_argument("--root", default=".")
    rec.add_argument("--out", type=Path, required=True)
    sel = sub.add_parser("select")
    sel.add_argument("--root", default=".")
    sel.add_argument("--repository", required=True)
    sel.add_argument("--base-branch", required=True)
    sel.add_argument("--attempt", required=True)
    sel.add_argument("--max-age", type=float, default=DEFAULT_MAX_AGE)
    sel.add_argument("--now")
    sel.add_argument("--runs")
    sel.add_argument("--output", type=Path)
    jdg = sub.add_parser("judge")
    jdg.add_argument("--root", default=".")
    jdg.add_argument("--repository", required=True)
    jdg.add_argument("--attempt", required=True)
    jdg.add_argument("--run", required=True)
    jdg.add_argument("--proven", required=True)
    jdg.add_argument("--timings", required=True)
    jdg.add_argument("--max-age", type=float, default=DEFAULT_MAX_AGE)
    jdg.add_argument("--now")
    jdg.add_argument("--output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    options = build_parser().parse_args(argv)
    if options.command == "record":
        return cmd_record(Path(options.root), options.out)
    if options.command == "select":
        return cmd_select(options)
    return cmd_judge(options)


if __name__ == "__main__":
    raise SystemExit(main())
