"""A ticket too large to build is split by the agent, and its parts inherit the owner's agreement.

ADR 0046. The agent-ready label is the owner's agreement that a ticket is ready to build
(ADR 0008). Splitting a ticket by size is no product decision, so a child that names the ticket it
was split from (``Split from: <ref>``) may carry the label when that parent carries it now, as the
default branch or the tracker shows it and never as the child's own text says. The agreement
reaches one level only: a child of a child goes back to the owner.

What makes the split no decision is the scope check, and it is deterministic. A ticket's promises
are its acceptance criteria, the ``- [ ]`` lines (ADR 0036 reads the ticket's promises the same
way). Every promise of a child must appear verbatim in the parent. Together the children cover
every promise of the parent exactly once. Prose outside the criteria is free. A split that adds,
drops, rewords or duplicates a promise is refused, and the refusal names the line.

This module is read by the label guard, the merge judge and the drain, so the three agree.
"""

import re
from collections.abc import Sequence

PROMISE = re.compile(r"^[ \t]*[-*][ \t]*\[[ xX]\][ \t]*(\S.*?)[ \t]*$", re.MULTILINE)
SPLIT_FROM = re.compile(
    r"^\*{0,2}split from:?\*{0,2}:?[ \t]*(\S.*?)[ \t]*$", re.MULTILINE | re.IGNORECASE
)
STATUS = re.compile(r"^\*{0,2}status:?\*{0,2}:?\s*`?([^`\n]+?)`?\s*$", re.MULTILINE | re.IGNORECASE)
REF = re.compile(r"[^`\s,()]+\.md|#\d+")


def promises(text: str) -> list[str]:
    """The promises of a ticket: the text of each ``- [ ]`` line, in order."""
    return PROMISE.findall(text)


def split_from(text: str) -> str | None:
    """The one ref a ``Split from:`` line names (``#12`` or a ticket file), or ``None``."""
    match = SPLIT_FROM.search(text)
    found = REF.findall(match.group(1)) if match else []
    return found[0] if len(found) == 1 else None


def carries(text: str, label: str) -> bool:
    """Whether ``text`` gives the ticket the agent-ready status."""
    return label.casefold() in {value.strip().casefold() for value in STATUS.findall(text)}


def scope_problems(parent: str, children: Sequence[str]) -> list[str]:
    """What keeps ``children`` from being a split of ``parent``, each naming the line.

    A promise a child adds is not the parent's. A promise of the parent no child carries is
    dropped, and one carried twice is duplicated, whether in two children or in one.
    """
    wanted = promises(parent)
    problems = [
        f"child {number} adds a promise the parent does not make: {line}"
        for number, child in enumerate(children, 1)
        for line in dict.fromkeys(promises(child))
        if line not in wanted
    ]
    counts: dict[str, int] = {}
    for child in children:
        for line in promises(child):
            if line in wanted:
                counts[line] = counts.get(line, 0) + 1
    problems += [
        f"no child carries the parent's promise: {line}" for line in wanted if line not in counts
    ]
    problems += [
        f"the parent's promise is carried {count} times, not once: {line}"
        for line, count in counts.items()
        if count > 1
    ]
    return problems


def refusal(
    child: str, parent: str | None, label: str, *, siblings: Sequence[str] = ()
) -> str | None:
    """Why ``child`` may not carry the label as a part of ``parent``, or ``None`` when it may.

    ``parent`` is the parent ticket as the default branch or the tracker shows it now. ``siblings``
    are the other children already filed. Only what one child can show is judged here: that the
    parent carries the label and is no child itself, that the child adds no promise, and that it
    repeats none a sibling carries. Coverage of every promise is the whole split's, judged where
    all the children are known (``scope_problems``).
    """
    if split_from(child) is None:
        return "names no single ticket it was split from"
    if parent is None:
        return "the ticket it was split from is not there"
    if not carries(parent, label):
        return f"the ticket it was split from does not carry `{label}` now"
    if split_from(parent) is not None:
        return "the ticket it was split from is itself a part, and a part is not split again"
    problems = scope_problems(parent, [child, *siblings])
    shown = [p for p in problems if not p.startswith("no child carries")]
    shown = [p.replace("child 1", "this child") for p in shown]
    return shown[0] if shown else None
