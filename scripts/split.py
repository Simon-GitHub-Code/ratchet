#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import documents
import feature_preflight as preflight
import gatelib
import markdown_text

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hooks"))
from ratchet_guards import splits

if TYPE_CHECKING:
    from collections.abc import Callable

INHERITED = ("## Parent", "## Conjecture", "## Operability")
PARENT_TO_LINE_END = re.compile(f"{preflight.PARENT_LINE.pattern}.*", preflight.PARENT_LINE.flags)
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


def written(
    parent: preflight.Ticket, part: Part, *, waits: list[str], label: str, carry: bool
) -> str:
    sections = [
        f"{heading}\n\n{preflight.section(parent.body, heading).strip()}\n"
        for heading in INHERITED
        if preflight.section(parent.body, heading).strip()
    ]
    answers = markdown_text.section(parent.body, documents.ANSWERS).strip() if carry else ""
    if answers:
        sections.append(f"{documents.ANSWERS}\n\n{answers}\n")
    link = PARENT_TO_LINE_END.search(parent.body) if carry else None
    lines = [
        f"# {part.title}",
        "",
        *([f"**Status:** `{label}`", ""] if parent.ref.endswith(".md") else []),
        f"Split from: {parent.ref}",
        *([link.group()] if link else []),
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


def inherited(text: str) -> dict[str, str]:
    link = PARENT_TO_LINE_END.search(text)
    return {
        "`Parent:` line": link.group() if link else "",
        **{f"`{heading}` section": preflight.section(text, heading).strip() for heading in INHERITED},
        f"`{documents.ANSWERS}` section": markdown_text.section(text, documents.ANSWERS).strip(),
    }


def readings(ref: str, text: str) -> dict[str, object]:
    ticket = preflight.local_ticket(ref, text)
    return {
        "title": ticket.title,
        "labels": ticket.labels,
        "blockers": ticket.blockers,
        "formal model line": ticket.formal_model,
        "conjecture": ticket.conjecture and ticket.conjecture.strip(),
        "operability lines": preflight.operability_lines(ticket, None),
        "acceptance criteria": preflight.criteria(text),
        "size": preflight.section(text, preflight.SIZE).strip(),
        "promises the judge counts": splits.promises(text),
        "split from line": splits.split_from(text),
        "status lines": splits.STATUS.findall(text),
    }


def uncarried(parent: preflight.Ticket, plain: str, candidate: str) -> str | None:
    named = preflight.local_ticket(parent.ref, candidate).parent
    if named != parent.parent:
        return f"the part would name the parent {named}, not its ticket's {parent.parent}"
    if documents.answers_of(candidate) != documents.answers_of(parent.body):
        return "the part would read other answers than its ticket"
    was, now = readings(parent.ref, plain), readings(parent.ref, candidate)
    changed = [name for name in was if was[name] != now[name]]
    return f"the copied text would change the part's {', '.join(changed)}" if changed else None


def child_body(
    parent: preflight.Ticket, part: Part, *, waits: list[str], label: str
) -> tuple[str, str | None]:
    plain = written(parent, part, waits=waits, label=label, carry=False)
    candidate = written(parent, part, waits=waits, label=label, carry=True)
    why = None if candidate == plain else uncarried(parent, plain, candidate)
    return (plain, why) if why else (candidate, None)


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
) -> tuple[str, str | None]:
    waits = [names[i] for i in parts[index].after if 0 <= i < index]
    if index == 0:
        waits = [*parent.blockers, *waits]
    return child_body(parent, parts[index], waits=waits, label=label)


def told(
    parent: preflight.Ticket, parts: list[Part], names: list[str], *, index: int, label: str
) -> str:
    body, why = body_of(parent, parts, names, index=index, label=label)
    if why:
        print(
            f"split: part {index + 1} ({parts[index].title}) does not carry its ticket's"
            f" answers and Parent: line, so it is written as before: {why}"
        )
    return body


def number_of(name: str) -> str:
    return re.sub(r"\D.*", "", name, flags=re.DOTALL).lstrip("0")


def free(folder: Path, name: str) -> bool:
    return (folder / name).is_file() or all(
        number_of(p.name) != number_of(name) for p in folder.glob("*.md")
    )


def untouched(
    folder: Path, parent: preflight.Ticket, parts: list[Part], *, filed: list[str], label: str
) -> list[str]:
    names: list[str] = []
    for index in range(len(parts)):
        body = body_of(parent, parts, names, index=index, label=label)[0]
        recorded = Path(filed[index]).name if len(filed) == len(parts) else None
        if recorded is None or gatelib.read_text(folder / recorded) != body:
            return []
        names.append(recorded)
    return names


def file_locally(  # noqa: PLR0913 -- the parts, where they were filed, and who records them
    root: Path,
    parent: preflight.Ticket,
    parts: list[Part],
    *,
    label: str,
    filed: list[str] | None = None,
    noting: Callable[[list[str]], None] = lambda _paths: None,
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
    texts: list[str] = []
    for index, part in enumerate(parts):
        body = told(parent, parts, names, index=index, label=label)
        recorded = Path(filed[index]).name if len(filed) == len(parts) else None
        if (
            recorded is not None
            and free(folder, recorded)
            and all(number_of(n) != number_of(recorded) for n in names)
        ):
            name = recorded
        else:
            slug = SLUG.sub("-", part.title.lower()).strip("-")[:40].strip("-") or "part"
            number = max([next_number(folder), *(int(number_of(n) or 0) + 1 for n in names)])
            name = f"{number:02d}-{slug}.md"
        names.append(name)
        texts.append(body)
    paths = [(folder / name).relative_to(root).as_posix() for name in names]
    noting(paths)
    for name, body in zip(names, texts, strict=True):
        if not (folder / name).is_file():
            (folder / name).write_text(body, encoding="utf-8")
    return paths


def file_on_tracker(
    parent: preflight.Ticket, parts: list[Part], *, label: str, root: Path
) -> list[str]:
    made: list[str] = []
    for index, part in enumerate(parts):
        scratch = root / gatelib.STATE / "split-body.md"
        scratch.parent.mkdir(parents=True, exist_ok=True)
        scratch.write_text(told(parent, parts, made, index=index, label=label), encoding="utf-8")
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
        path: body_of(parent, parts, names, index=index, label=label)[0]
        for index, path in enumerate(filed[: len(parts)])
    }


def apply(  # noqa: PLR0913 -- the plan, where it was filed, and who records it
    root: Path,
    ref: str,
    plan: object,
    *,
    gh: preflight.Runner,
    filed: list[str] | None = None,
    noting: Callable[[list[str]], None] = lambda _paths: None,
) -> tuple[list[str], list[str]]:
    parent, parts, label, refused = judge(root, ref, plan, gh=gh)
    if refused or parent is None:
        return [], refused
    if ref.endswith(".md"):
        return file_locally(root, parent, parts, label=label, filed=filed, noting=noting), []
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
        parent, parts, label, refused = judge(root, options.ref, plan, gh=gh)
        if parent is not None and not refused:
            names = [f"part {number}" for number in range(1, len(parts) + 1)]
            for index in range(len(parts)):
                told(parent, parts, names, index=index, label=label)
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
