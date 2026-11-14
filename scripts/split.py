#!/usr/bin/env python3
"""Split a ticket that is too large into parts, filed in the project's tracker (ADR 0046).

A size stop (the feature preflight's ``## Size``, the spec stage's estimate, or the measured
stop) leaves a ticket the pipeline will not build as one change. Splitting it is no product
decision, so the agent does it, and this script is the one place that decides whether a split is
one. ``check`` judges a plan and ``apply`` files its parts; the drain calls ``apply``, and the
label guard and the merge judge read the same rules from ``ratchet_guards.splits``.

The plan is JSON: ``{"parts": [{"title": "...", "promises": ["<a ``- [ ]`` line of the parent,
verbatim>", ...], "summary": "...", "after": [0]}]}``. ``after`` lists the parts a part waits
on, by index; it defaults to the part before. The first part waits on what the parent waited on.

A plan is refused when the parent is not there, does not carry the agent-ready label now (read
from the default branch for a local ticket, from the tracker for an issue), is itself a part of
another ticket, or when its promises are not the parent's: each part's promises must appear in
the parent verbatim and together carry every promise of the parent exactly once. Prose outside
the criteria is free. The refusal names the line.

The parent's text is never edited: it stays as the owner agreed it and stops being buildable
because its parts name it (``Split from:``). The children carry the label the parent holds.

Usage: ``split.py check|apply <ticket> --plan FILE [--root DIR] [--offline]``.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import feature_preflight as preflight
import gatelib

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hooks"))
from ratchet_guards import splits

INHERITED = ("## Parent", "## Conjecture", "## Size", "## Operability")
SLUG = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class Part:
    """One part of a split: its title, the promises it carries and what it waits on."""

    title: str
    promises: tuple[str, ...]
    summary: str
    after: tuple[int, ...]


def parts_of(plan: object) -> list[Part]:
    """The parts of a plan as JSON, with ``after`` defaulted to the part before."""
    raw = plan.get("parts") if isinstance(plan, dict) else None
    found = []
    for index, item in enumerate(raw if isinstance(raw, list) else []):
        if not isinstance(item, dict):
            continue
        promises = item.get("promises")
        after = item.get("after")
        found.append(
            Part(
                str(item.get("title", "")).strip(),
                tuple(str(p).strip() for p in promises) if isinstance(promises, list) else (),
                str(item.get("summary", "")).strip(),
                tuple(i for i in after if isinstance(i, int))
                if isinstance(after, list)
                else ((index - 1,) if index else ()),
            )
        )
    return found


def parent_now(root: Path, ref: str, gh: preflight.Runner) -> preflight.Ticket | None:
    """The parent as the default branch (a local ticket) or the tracker (an issue) shows it.

    Never the work tree's copy, which an agent of the run could have edited.
    """
    if ref.endswith(".md"):
        for name in gatelib.default_branches(root):
            text = gatelib.show(root, name, ref)
            if text is not None:
                return preflight.local_ticket(ref, text)
        return None
    return preflight.fetch(ref, root, gh)


def child_body(parent: preflight.Ticket, part: Part, *, waits: list[str], label: str) -> str:
    """The text of one part: it names its parent, what it waits on and the promises it carries."""
    sections = [
        f"{heading}\n\n{preflight.section(parent.body, heading).strip()}\n"
        for heading in INHERITED
        if preflight.section(parent.body, heading).strip()
    ]
    lines = [
        f"# {part.title}",
        "",
        *([f"**Status:** `{label}`", ""] if parent.ref.endswith(".md") else []),
        f"Split from: {parent.ref}",
        f"Blocked by: {', '.join(waits) if waits else 'none'}",
        "",
        "## What to build",
        "",
        part.summary or part.title,
        "",
        "## Acceptance criteria",
        "",
        *(f"- [ ] {promise}" for promise in part.promises),
        "",
    ]
    return "\n".join(lines) + "\n" + "\n".join(sections)


def problems(
    parent: preflight.Ticket | None, parts: list[Part], label: str, *, holds: bool
) -> list[str]:
    """Why ``parts`` are not a split of ``parent``; empty when they are."""
    if parent is None:
        return ["the ticket is not there"]
    found = []
    if not holds:
        found.append(
            f"the ticket does not carry `{label}` now, so there is no agreement to inherit"
        )
    if splits.split_from(parent.body) is not None:
        found.append(
            "the ticket is itself a part, and a part is not split again: the owner is asked"
        )
    if len(parts) < 2:  # noqa: PLR2004 # -- a split makes two parts at least
        found.append("a split has two parts at least")
    found += [f"part {n} has no title" for n, part in enumerate(parts, 1) if not part.title]
    # Why: only the criteria are judged; a part's inherited sections are prose and are free.
    bodies = ["\n".join(f"- [ ] {promise}" for promise in part.promises) for part in parts]
    found += splits.scope_problems(parent.body, bodies)
    return found


def next_number(folder: Path) -> int:
    numbers = [int(m.group()) for p in folder.glob("*.md") if (m := re.match(r"\d+", p.name))]
    return max(numbers, default=0) + 1


def file_locally(
    root: Path, parent: preflight.Ticket, parts: list[Part], *, label: str
) -> list[str]:
    """Write each part beside the parent; the refs are the file names the others wait on."""
    folder = (root / parent.ref).parent
    number = next_number(folder)
    slugs = [
        SLUG.sub("-", part.title.lower()).strip("-")[:40].strip("-") or "part" for part in parts
    ]
    names = [f"{number + i:02d}-{slug}.md" for i, slug in enumerate(slugs)]
    for index, part in enumerate(parts):
        waits = [names[i] for i in part.after if 0 <= i < index]
        if index == 0:
            waits = [*parent.blockers, *waits]
        (folder / names[index]).write_text(
            child_body(parent, part, waits=waits, label=label), encoding="utf-8"
        )
    return [(folder / name).relative_to(root).as_posix() for name in names]


def file_on_tracker(
    parent: preflight.Ticket, parts: list[Part], *, label: str, root: Path
) -> list[str]:
    """Create each part as an issue carrying the label, each waiting on the earlier ones."""
    made: list[str] = []
    for index, part in enumerate(parts):
        waits = [made[i] for i in part.after if 0 <= i < index]
        if index == 0:
            waits = [*parent.blockers, *waits]
        scratch = root / gatelib.STATE / "split-body.md"
        scratch.parent.mkdir(parents=True, exist_ok=True)
        scratch.write_text(child_body(parent, part, waits=waits, label=label), encoding="utf-8")
        out = preflight.run_gh(
            [
                "issue",
                "create",
                "--title",
                part.title,
                "--body-file",
                str(scratch),
                "--label",
                label,
            ]
        )
        found = re.search(r"/issues/(\d+)", out or "")
        if found is None:
            message = f"the tracker did not file part {index + 1}; filed so far: {', '.join(made)}"
            raise RuntimeError(message)
        made.append(f"#{found.group(1)}")
    return made


def judge(
    root: Path, ref: str, plan: object, *, gh: preflight.Runner
) -> tuple[preflight.Ticket | None, list[Part], str, list[str]]:
    parent = parent_now(root, ref, gh)
    label = preflight.ready_label(root)
    parts = parts_of(plan)
    holds = parent is not None and (splits.carries(parent.body, label) or label in parent.labels)
    return parent, parts, label, problems(parent, parts, label, holds=holds)


def apply(
    root: Path, ref: str, plan: object, *, gh: preflight.Runner
) -> tuple[list[str], list[str]]:
    """The refs of the parts filed, and the refusal when none were."""
    parent, parts, label, refused = judge(root, ref, plan, gh=gh)
    if refused or parent is None:
        return [], refused
    if ref.endswith(".md"):
        return file_locally(root, parent, parts, label=label), []
    return file_on_tracker(parent, parts, label=label, root=root), []


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["check", "apply"])
    parser.add_argument("ref")
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--root", default=".")
    parser.add_argument("--offline", action="store_true")
    options = parser.parse_args(argv[1:])
    root = Path(options.root).resolve()
    gh = (lambda _args: None) if options.offline else preflight.run_gh
    try:
        plan = json.loads(options.plan.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        print(f"split: the plan does not read: {error}")
        return 2
    children: list[str] = []
    if options.action == "check":
        refused = judge(root, options.ref, plan, gh=gh)[3]
    else:
        children, refused = apply(root, options.ref, plan, gh=gh)
    for line in refused:
        print(f"split: refused: {line}")
    if refused:
        return 1
    print(json.dumps({"children": children}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
