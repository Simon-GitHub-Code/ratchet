#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import application_types
import conformance
import documents
import formal_model
import gatelib
import hooks_installed
import owed
import plugin_pin
import project_bite
import route
import runrecord
import settings_rules
import triggers
import upgrade

Status = Literal["green", "warn", "red"]
DISPUTE_OPEN = re.compile(r"^-\s*Status:\s*open\b", re.IGNORECASE | re.MULTILINE)
NOT_A_TYPE = re.compile(r"^none:\s*not a\b", re.IGNORECASE)


@dataclass(frozen=True)
class Section:
    name: str
    status: Status
    summary: str
    items: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "status": self.status,
            "summary": self.summary,
            "items": list(self.items),
        }


def default_branch(root: Path) -> str:
    checkout = route.observe(root)
    return checkout.default_branch if checkout is not None else "main"


def conformance_section(
    red: list[conformance.Finding],
    unchecked: list[str],
) -> Section:
    if red:
        items: list[str] = []
        for finding in red:
            lines = conformance.then_lines(finding.fix)
            hint = f" ({lines[0]})" if lines else ""
            items.append(f"{finding}{hint}")
        return Section("conformance", "red", f"{len(red)} failure(s)", tuple(items))
    if unchecked:
        items = [f"not checked: {project_bite.REGISTRY}: {item}" for item in unchecked]
        return Section(
            "conformance",
            "warn",
            f"0 failures, {len(unchecked)} unchecked",
            tuple(items),
        )
    return Section("conformance", "green", "0 failures")


def plugin_pin_section(binding: str, plugin: Path) -> Section:
    line, is_red = plugin_pin.judged(plugin_pin.pinned(binding), plugin)
    if is_red:
        return Section("plugin-pin", "red", "pin mismatch", (line,))
    return Section("plugin-pin", "green", line.removeprefix("plugin-pin: "))


def hooks_section(root: Path) -> Section:
    problem = hooks_installed.check(root)
    if problem is not None:
        return Section("hooks", "red", problem.detail, (f"fix: {problem.produce}",))
    return Section("hooks", "green", "installed and answering")


def settings_section(root: Path) -> Section:
    drifts = settings_rules.drifts(root)
    if drifts:
        return Section(
            "settings",
            "red",
            f"{len(drifts)} drift(s)",
            tuple(drift.line for drift in drifts),
        )
    return Section("settings", "green", "aligned with guards/manifest.toml")


def incidents_section(root: Path) -> Section:
    found = list(conformance.check_incidents(root))
    if found:
        return Section(
            "incidents",
            "red",
            f"{len(found)} failure(s)",
            tuple(str(item) for item in found),
        )
    return Section("incidents", "green", "0 failures")


def triggers_section(root: Path) -> Section:
    safeguards = triggers.owed(root)
    reviews = triggers.reviews_owed(root)
    items = [
        f"safeguard owed: {item.question} ({item.trigger}, {item.count} instance(s))"
        for item in safeguards
    ] + [
        f"architecture review owed: {item.question} ({item.trigger}, {item.count} instance(s))"
        for item in reviews
    ]
    if items:
        return Section(
            "triggers",
            "warn",
            f"{len(safeguards)} safeguard(s), {len(reviews)} architecture review(s) owed",
            tuple(items),
        )
    return Section("triggers", "green", "0 safeguards or architecture reviews owed")


def formal_section(root: Path, binding: str, branch: str) -> Section:
    formal = formal_model.read(binding)
    if formal is None:
        return Section("formal-model", "green", "no formal model declared")
    behind = formal_model.behind(root, branch, formal)
    if behind is not None:
        head = f" (head {behind.commit[:12]})" if behind.commit else ""
        note = (
            f"update the formal model against `{branch}` and advance `- Synced:` in"
            f" {gatelib.BINDING}"
        )
        return Section(
            "formal-model",
            "warn",
            f"synced commit {behind.synced[:12]} is behind {branch}{head}",
            (note,),
        )
    return Section("formal-model", "green", f"synced at {formal.synced[:12]}")


def owed_section(root: Path) -> Section:
    ledger = owed.read(root)
    if ledger.problems:
        return Section(
            "owed",
            "red",
            f"{len(ledger.problems)} ledger problem(s)",
            tuple(ledger.problems),
        )
    comment_entries = owed.comments(root)
    items = [f"{entry.path}: {entry.problem} (since {entry.since})" for entry in ledger.entries]
    if items:
        comment_note = f", {len(comment_entries)} owed comment(s)" if comment_entries else ""
        return Section(
            "owed",
            "warn",
            f"{len(ledger.entries)} entry(ies){comment_note}",
            tuple(items),
        )
    return Section("owed", "green", "0 owed")


def opt_ins_section(root: Path, offered: list[conformance.Finding]) -> Section:
    opt_ins = upgrade.available_opt_ins(root)
    items = [f"opt-in: {title}: {detail}" for title, detail in opt_ins] + [
        f"offered: {finding}" for finding in offered
    ]
    if items:
        return Section(
            "opt-ins",
            "warn",
            f"{len(opt_ins)} available opt-in(s), {len(offered)} offered",
            tuple(items),
        )
    return Section("opt-ins", "green", "none pending")


def parse_thresholds(root: Path) -> tuple[bool, set[str]]:
    text = gatelib.read_text(root / gatelib.THRESHOLDS) or ""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return False, set()
    ceilings = data.get("ceilings")
    if not isinstance(ceilings, dict):
        return False, set()
    loop = ceilings.get("loop")
    has_loop = (
        isinstance(loop, dict)
        and isinstance(loop.get("seconds"), int | float)
        and not isinstance(loop.get("seconds"), bool)
    )
    seconds = ceilings.get("seconds")
    recorded = (
        {
            str(key)
            for key, val in seconds.items()
            if isinstance(val, int | float) and not isinstance(val, bool)
        }
        if isinstance(seconds, dict)
        else set()
    )
    return has_loop, recorded


def budgets_section(root: Path, binding: str) -> Section:
    has_loop, recorded = parse_thresholds(root)
    bound_gates = sorted(
        {row.gate for row in project_bite.gate_rows(binding) if row.bound and row.gate}
    )
    unbounded = [gate for gate in bound_gates if gate not in recorded]
    items: list[str] = []
    if not has_loop:
        items.append(f"`[ceilings.loop]` `seconds` is not set in `{gatelib.THRESHOLDS}`")
    items.extend(route.loop_proposals(root))
    items.extend(route.ceiling_proposals(root))
    items.extend(route.slow_bite_proposals(root))
    share = f"{len(unbounded)}/{len(bound_gates)} bound gate(s) unbounded"
    if items:
        return Section("budgets", "warn", f"{len(items)} budget item(s), {share}", tuple(items))
    return Section("budgets", "green", f"loop budget set, {share}")


def open_disputes(root: Path) -> list[str]:
    folder = root / "docs/disputes"
    if not folder.is_dir():
        return []
    found: list[str] = []
    for path in sorted(folder.glob("*.md")):
        if not path.is_symlink():
            text = gatelib.read_text(path) or ""
            if DISPUTE_OPEN.search(text):
                found.append(f"open dispute: {path.relative_to(root).as_posix()}")
    return found


def life_events_section(root: Path, binding: str) -> Section:
    catalogue = application_types.load()
    declared = sorted(application_types.life_events(binding))
    undeclared = [name for name in catalogue.events if name not in declared]
    declined = route.declined_signs(root)
    items: list[str] = []
    for name, signs in application_types.signs_seen(root).items():
        meaning = catalogue.events[name].meaning
        fresh = [path for sign, path in signs.items() if (name, sign) not in declined]
        if fresh:
            items.append(f"undeclared `{name}` ({meaning}): sign `{fresh[0]}`")
        else:
            items.append(
                f"previously declined `{name}` ({meaning}): sign `{next(iter(signs.values()))}`"
                " still present"
            )
    declared_text = ", ".join(declared) if declared else "none"
    if items:
        return Section(
            "life-events",
            "warn",
            f"declared: {declared_text}; {len(items)} undeclared event sign(s)",
            tuple(items),
        )
    if undeclared:
        return Section(
            "life-events",
            "green",
            f"declared: {declared_text}; {len(undeclared)} undeclared ({', '.join(undeclared)})",
        )
    return Section("life-events", "green", f"declared: {declared_text}; all events declared")


def records_section(root: Path) -> Section:
    stale_items = [
        f"stale pass page: `{page}` cites changed `{cited}`"
        for page, cited in documents.stale(root)
    ]
    items = (
        open_disputes(root)
        + [f"expired flag: {line}" for line in route.expired_switches(root)]
        + stale_items
    )
    if items:
        return Section("records", "warn", f"{len(items)} record item(s)", tuple(items))
    return Section("records", "green", "no open disputes, expired flags or stale pages")


def binding_review_section(binding: str) -> Section:
    rows = project_bite.gate_rows(binding)
    bound = 0
    standing_none: list[str] = []
    later: list[str] = []
    for row in rows:
        if row.bound:
            bound += 1
        elif application_types.LATER.match(row.tool):
            later.append(f"later: {row.question} -> {row.tool}")
        elif application_types.NONE.match(row.tool) and not NOT_A_TYPE.match(row.tool):
            standing_none.append(f"none: {row.question} -> {row.tool}")
    return Section(
        "binding-review",
        "green",
        f"{bound} bound, {len(standing_none)} standing none, {len(later)} later",
        (*standing_none, *later),
    )


def audit(root: Path, plugin: Path) -> list[Section]:
    binding = gatelib.read_text(root / gatelib.BINDING) or ""
    branch = default_branch(root)
    bites = project_bite.registry_check(root)
    with runrecord.read_once():
        found = conformance.findings(root, bites=bites)
        red, held = conformance.owed_split(root, found)
        offered = [finding for finding in held if finding.identity in conformance.OFFERED]
        return [
            conformance_section(red, [str(item) for item in bites.unchecked]),
            plugin_pin_section(binding, plugin),
            hooks_section(root),
            settings_section(root),
            incidents_section(root),
            triggers_section(root),
            life_events_section(root, binding),
            formal_section(root, binding, branch),
            owed_section(root),
            opt_ins_section(root, offered),
            budgets_section(root, binding),
            records_section(root),
            binding_review_section(binding),
        ]


def render_text(sections: list[Section]) -> str:
    reds = sum(section.status == "red" for section in sections)
    warns = sum(section.status == "warn" for section in sections)
    greens = sum(section.status == "green" for section in sections)
    verdict = "red" if reds else "green"
    lines = [f"ratchet-audit: {verdict} ({reds} red, {warns} warn, {greens} green)"]
    for section in sections:
        tag = f"[{section.status}]"
        lines.append(f"{tag:7s} {section.name}: {section.summary}")
        lines.extend(f"  - {item}" for item in section.items)
    return "\n".join(lines) + "\n"


def render_json(sections: list[Section]) -> str:
    reds = sum(section.status == "red" for section in sections)
    warns = sum(section.status == "warn" for section in sections)
    greens = sum(section.status == "green" for section in sections)
    payload = {
        "verdict": "red" if reds else "green",
        "counts": {"red": reds, "warn": warns, "green": greens},
        "sections": [section.as_dict() for section in sections],
    }
    return json.dumps(payload, indent=2) + "\n"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="audit.py",
        description="Run a read-only Ratchet compliance scorecard across the repository.",
    )
    parser.add_argument("--root", default=".")
    parser.add_argument("--plugin", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--json", action="store_true", dest="as_json")
    options = parser.parse_args(argv[1:])
    sections = audit(Path(options.root).resolve(), Path(options.plugin).resolve())
    print(render_json(sections) if options.as_json else render_text(sections), end="")
    return 1 if any(section.status == "red" for section in sections) else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
