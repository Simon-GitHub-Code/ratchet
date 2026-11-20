#!/usr/bin/env python3
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

INHERITED = ("## Parent", "## Conjecture", "## Operability")
SLUG = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class Part:
    title: str
    promises: tuple[str, ...]
    summary: str
    after: tuple[int, ...]


def parts_of(plan: object) -> list[Part]:
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
    if ref.endswith(".md"):
        for name in gatelib.default_branches(root):
            text = gatelib.show(root, name, ref)
            if text is not None:
                return preflight.local_ticket(ref, text)
        return None
    return preflight.fetch(ref, root, gh)


def child_body(parent: preflight.Ticket, part: Part, *, waits: list[str], label: str) -> str:
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
    bodies = ["\n".join(f"- [ ] {promise}" for promise in part.promises) for part in parts]
    found += splits.scope_problems(parent.body, bodies)
    return found


def next_number(folder: Path) -> int:
    numbers = [int(m.group()) for p in folder.glob("*.md") if (m := re.match(r"\d+", p.name))]
    return max(numbers, default=0) + 1


def body_of(
    parent: preflight.Ticket, parts: list[Part], names: list[str], *, index: int, label: str
) -> str:
    waits = [names[i] for i in parts[index].after if 0 <= i < index]
    if index == 0:
        waits = [*parent.blockers, *waits]
    return child_body(parent, parts[index], waits=waits, label=label)


def number_of(name: str) -> str:
    return re.sub(r"\D.*", "", name, flags=re.DOTALL).lstrip("0")


def free(folder: Path, name: str) -> bool:
    return (folder / name).is_file() or all(
        number_of(p.name) != number_of(name) for p in folder.glob("*.md")
    )


def matching(folder: Path, body: str) -> str | None:
    return next(
        (p.name for p in sorted(folder.glob("*.md")) if p.read_text(encoding="utf-8") == body),
        None,
    )


def untouched(
    folder: Path, parent: preflight.Ticket, parts: list[Part], *, filed: list[str], label: str
) -> list[str]:
    names: list[str] = []
    for index in range(len(parts)):
        body = body_of(parent, parts, names, index=index, label=label)
        recorded = Path(filed[index]).name if len(filed) == len(parts) else None
        name = recorded if filed else matching(folder, body)
        if name is None or gatelib.read_text(folder / name) != body:
            return []
        names.append(name)
    return names


def file_locally(
    root: Path,
    parent: preflight.Ticket,
    parts: list[Part],
    *,
    label: str,
    filed: list[str] | None = None,
) -> list[str]:
    folder = (root / parent.ref).parent
    filed = filed or []
    mine = untouched(folder, parent, parts, filed=filed, label=label)
    numbers = {number_of(name) for name in mine}
    known = [f":(literal){(folder / name).relative_to(root).as_posix()}" for name in mine]
    if (
        any(
            number_of(path.name) in numbers for path in folder.glob("*.md") if path.name not in mine
        )
        and gatelib.git(root, "ls-files", "--", *known) == ""
    ):
        for name in mine:
            (folder / name).unlink()
        filed = []
    names: list[str] = []
    for index, part in enumerate(parts):
        body = body_of(parent, parts, names, index=index, label=label)
        recorded = Path(filed[index]).name if len(filed) == len(parts) else None
        if recorded is not None and free(folder, recorded):
            name = recorded
        elif not filed and (found := matching(folder, body)) is not None:
            name = found
        else:
            slug = SLUG.sub("-", part.title.lower()).strip("-")[:40].strip("-") or "part"
            name = f"{next_number(folder):02d}-{slug}.md"
        if not (folder / name).is_file():
            (folder / name).write_text(body, encoding="utf-8")
        names.append(name)
    return [(folder / name).relative_to(root).as_posix() for name in names]


def file_on_tracker(
    parent: preflight.Ticket, parts: list[Part], *, label: str, root: Path
) -> list[str]:
    made: list[str] = []
    for index, part in enumerate(parts):
        scratch = root / gatelib.STATE / "split-body.md"
        scratch.parent.mkdir(parents=True, exist_ok=True)
        scratch.write_text(body_of(parent, parts, made, index=index, label=label), encoding="utf-8")
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


def bodies(
    root: Path, ref: str, plan: object, *, filed: list[str], gh: preflight.Runner
) -> dict[str, str]:
    parent, parts, label, refused = judge(root, ref, plan, gh=gh)
    if refused or parent is None:
        return {}
    names = [Path(path).name for path in filed]
    return {
        path: body_of(parent, parts, names, index=index, label=label)
        for index, path in enumerate(filed[: len(parts)])
    }


def apply(
    root: Path, ref: str, plan: object, *, gh: preflight.Runner, filed: list[str] | None = None
) -> tuple[list[str], list[str]]:
    parent, parts, label, refused = judge(root, ref, plan, gh=gh)
    if refused or parent is None:
        return [], refused
    if ref.endswith(".md"):
        return file_locally(root, parent, parts, label=label, filed=filed), []
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
