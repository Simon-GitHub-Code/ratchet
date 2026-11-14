"""What a run did about the project's architecture, read from its repository and its transcript.

Three readings, none of them a model's judgement:

``asked`` reads the owner's questions: whether the architecture question came in the adoption round
with at least two options and a recommendation, and how many later questions asked the owner to
change the architecture. ``inspect`` reads the files of the default branch: the architecture
decision record and its required sections, the binding's parts table, and each feature's design
section. ``review_axis`` reads the transcript for the review's design axis.

The words that find a question are the ones ``skills/method/questions.toml`` registers for
``architecture.system`` and ``architecture.tools``; an agent that reworded both beyond these is
read as not having asked, which is the finding.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

BINDING = "docs/process/binding.md"
SYSTEM = re.compile(r"how should it be built|what it is made of", re.IGNORECASE)
SYSTEM_HEADER = re.compile(r"^\s*(?:architecture|system|how it is built)\s*$", re.IGNORECASE)
TOOLS = re.compile(r"which tools should check|tools? (?:set|should)", re.IGNORECASE)
CHANGE = re.compile(
    r"\b(?:new (?:layer|part|process|kind of storage|store)|process boundary|"
    r"change (?:the|its|our) (?:architecture|design)|rework (?:the )?(?:architecture|design)|"
    r"re-?architect|replace (?:the )?(?:store|storage|database)|"
    r"move (?:it |them )?to (?:a|another))",
    re.IGNORECASE,
)
ITEM = re.compile(r"^\s*(?:[-*]|\d+[.)])\s+\S", re.MULTILINE)
HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$", re.MULTILINE)
CHOSEN_BY = re.compile(r"^\s*[-*]\s*Chosen by:\s*(\S.*)$", re.MULTILINE | re.IGNORECASE)
DECISION_PATH = re.compile(r"^\s*[-*]\s*Decision:\s*`([^`]+)`", re.MULTILINE)
PARTS_HEADER = re.compile(r"^\s*\|\s*Part\s*\|\s*Path\s*\|\s*May depend on\s*\|\s*$", re.MULTILINE)
DESIGN_AXIS = re.compile(r"""axis\\*["']?\s*:\s*\\*["']design""")
AXIS_F = re.compile(r"\baxis F\b|\bdesign axis\b", re.IGNORECASE)
VERDICT = re.compile(r"""verdict\\*["']?\s*:\s*\\*["'](?:approve|request_changes)""")
ADR_PATH = re.compile(r"^docs/adr/\d{4}-[^/]+\.md$")
SPEC_PATH = re.compile(r"^docs/features/([^/]+)/spec\.md$")


def section(text: str, name: str) -> str | None:
    """The body under the heading called ``name``, up to the next heading at its level or above."""
    found = [(m.start(), m.end(), len(m.group(1)), m.group(2)) for m in HEADING.finditer(text)]
    for index, (_, end, level, title) in enumerate(found):
        if title.strip().casefold() != name.casefold():
            continue
        stop = next((s for s, _, lv, _ in found[index + 1 :] if lv <= level), len(text))
        return text[end:stop].strip()
    return None


def _question_text(question: Mapping[str, object]) -> str:
    return f"{question.get('header') or ''} {question.get('question') or ''}"


def _is_system(question: Mapping[str, object]) -> bool:
    return bool(
        SYSTEM.search(str(question.get("question") or ""))
        or SYSTEM_HEADER.match(str(question.get("header") or ""))
    ) and not TOOLS.search(_question_text(question))


def _options(question: Mapping[str, object]) -> int:
    options = question.get("options")
    return len(options) if isinstance(options, list) else 0


def asked(questions: Sequence[Mapping[str, object]]) -> dict[str, object]:
    """Whether the architecture was asked in the adoption round, and what was asked of it later."""
    dialog = [q for q in questions if q.get("channel") == "dialog"]
    system = next((q for q in dialog if _is_system(q)), None)
    tools = next((q for q in dialog if TOOLS.search(_question_text(q))), None)
    round_step = None if system is None else system.get("step")
    round_size = sum(q.get("step") == round_step for q in dialog) if system else 0
    later = [
        q
        for q in questions
        if (system is None or q.get("step") != round_step) and CHANGE.search(_question_text(q))
    ]
    options = _options(system) if system else 0
    return {
        "asked": system is not None,
        "options": options,
        "recommended": system is not None and system.get("recommended") is not None,
        "enough_options": options >= 2,  # noqa: PLR2004
        "batched": round_size >= 2,  # noqa: PLR2004
        "round_questions": round_size,
        "answer": None if system is None else system.get("answer"),
        "took_recommended": system is not None and bool(system.get("took_recommended")),
        "tools_asked": tools is not None,
        "change_questions": len(later),
        "change_list": [str(q.get("question") or "")[:200] for q in later],
    }


def _count_items(body: str | None) -> int:
    return len(ITEM.findall(body)) if body else 0


def _filled(body: str | None) -> bool:
    return bool(body and body.strip() and not re.fullmatch(r"<[^>]*>", body.strip()))


def decision_record(text: str) -> dict[str, object]:
    """The required sections of an architecture decision record, each present and filled or not."""
    decision = section(text, "Decision")
    chosen = CHOSEN_BY.search(decision or "")
    options = _count_items(section(text, "Considered options"))
    reversal = _filled(section(text, "Reversal cost"))
    refutation = _filled(section(text, "Refutation criterion"))
    record: dict[str, object] = {
        "decision": _filled(decision),
        "chosen_by": chosen.group(1).strip() if chosen else None,
        "options": options,
        "reversal_cost": reversal,
        "refutation_criterion": refutation,
    }
    record["complete"] = bool(
        record["decision"] and chosen and options >= 2 and reversal and refutation  # noqa: PLR2004
    )
    return record


def _parts(body: str) -> list[dict[str, str]]:
    start = PARTS_HEADER.search(body)
    if start is None:
        return []
    rows = []
    for line in body[start.end() :].strip().splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if not line.strip().startswith("|"):
            break
        if len(cells) != 3 or set("".join(cells)) <= set("-: "):  # noqa: PLR2004
            continue
        rows.append({"part": cells[0], "path": cells[1], "depends_on": cells[2]})
    return rows


def binding(files: Mapping[str, str]) -> dict[str, object]:
    """The binding's architecture section: the record it names and the parts it declares."""
    text = files.get(BINDING, "")
    body = section(text, "Architecture")
    if body is None:
        return {"section": False, "decision": None, "decision_exists": False, "parts": []}
    named = DECISION_PATH.search(body)
    path = named.group(1) if named else None
    return {
        "section": True,
        "decision": path,
        "decision_exists": path in files if path else False,
        "parts": _parts(body),
    }


def features(files: Mapping[str, str]) -> dict[str, object]:
    """Each feature's spec, and whether it carries a filled Design section."""
    specs = {m.group(1): files[p] for p in sorted(files) if (m := SPEC_PATH.match(p))}
    found = {slug: _filled(section(text, "Design")) for slug, text in specs.items()}
    return {
        "specs": len(found),
        "with_design": sum(found.values()),
        "without_design": sorted(slug for slug, ok in found.items() if not ok),
    }


def inspect(files: Mapping[str, str]) -> dict[str, object]:
    """The repository's architecture: the record, the parts and the features' design sections.

    The record read is the one the binding names; a binding that names none (or a file that is
    not there) falls back to the first record with a ``## Considered options`` section.
    """
    bound = binding(files)
    adrs = {p: t for p, t in files.items() if ADR_PATH.match(p)}
    path = str(bound["decision"]) if bound["decision_exists"] else None
    if path is None:
        path = next((p for p in sorted(adrs) if section(adrs[p], "Considered options")), None)
    text = files.get(path or "")
    return {
        "adr": {"path": path, "found": text is not None, **(decision_record(text or ""))},
        "binding": bound,
        "features": features(files),
    }


def review_axis(blobs: Iterable[str]) -> dict[str, object]:
    """What the transcript shows of reviews and their design axis.

    A review result is counted by its ``verdict`` field, a design issue by ``axis: design`` and a
    mention by "axis F" or "design axis" in any text. A review that approved with no design issue
    leaves nothing of the axis in the transcript, so ``design_issues`` and ``mentions`` are a floor.
    """
    texts = list(blobs)
    return {
        "results": sum(len(VERDICT.findall(t)) for t in texts),
        "design_issues": sum(len(DESIGN_AXIS.findall(t)) for t in texts),
        "mentions": sum(len(AXIS_F.findall(t)) for t in texts),
    }


def transcript_texts(lines: Iterable[str]) -> list[str]:
    """The message bodies of a transcript, one JSON text each."""
    return [json.dumps(json.loads(line).get("msg"), default=str) for line in lines if line.strip()]
