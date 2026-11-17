import re
from collections.abc import Sequence

PROMISE = re.compile(r"^[ \t]*[-*][ \t]*\[[ xX]\][ \t]*(\S.*?)[ \t]*$", re.MULTILINE)
SPLIT_FROM = re.compile(
    r"^\*{0,2}split from:?\*{0,2}:?[ \t]*(\S.*?)[ \t]*$", re.MULTILINE | re.IGNORECASE
)
STATUS = re.compile(r"^\*{0,2}status:?\*{0,2}:?\s*`?([^`\n]+?)`?\s*$", re.MULTILINE | re.IGNORECASE)
REF = re.compile(r"[^`\s,()]+\.md|#\d+")


def promises(text: str) -> list[str]:
    return PROMISE.findall(text)


def split_from(text: str) -> str | None:
    match = SPLIT_FROM.search(text)
    found = REF.findall(match.group(1)) if match else []
    return found[0] if len(found) == 1 else None


def carries(text: str, label: str) -> bool:
    return label.casefold() in {value.strip().casefold() for value in STATUS.findall(text)}


def scope_problems(parent: str, children: Sequence[str]) -> list[str]:
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
