"""The agent-ready label on a local ticket: an agent that writes it asks the owner.

On a local markdown tracker (mattpocock-skills' ``.scratch/<feature>/``) a ticket's label is its
``Status:`` line, so writing the agent-ready label there is the act ``gh issue edit
--add-label`` is on a forge: the owner's agreement that the ticket is ready to build, which the
feature preflight reads as such. An Edit, Write or MultiEdit of a markdown file under
``.scratch/`` that leaves the file with that status where it had none asks. A shell write is not
seen, the same limit the forge rule has with a command other than ``gh``.
"""

import re
from collections.abc import Mapping
from pathlib import Path

from ratchet_guards.context import Context, Guard
from ratchet_guards.gh import ready_label
from ratchet_guards.verdict import Verdict

TRACKER_DIRECTORY = ".scratch"
STATUS_LINE = re.compile(
    r"^\*{0,2}status:?\*{0,2}:?\s*`?([^`\n]+?)`?\s*$", re.IGNORECASE | re.MULTILINE
)


def statuses(text: str) -> set[str]:
    """Every value a ``Status:`` line in ``text`` gives."""
    return {value.strip() for value in STATUS_LINE.findall(text)}


def _replace(text: str, edit: Mapping[str, object]) -> str:
    old, new = str(edit.get("old_string", "")), str(edit.get("new_string", ""))
    return text.replace(old, new) if edit.get("replace_all") is True else text.replace(old, new, 1)


def after(tool: str, tool_input: Mapping[str, object], before: str) -> str:
    """The file's text once the tool call has run."""
    if tool == "Write":
        return str(tool_input.get("content", ""))
    if tool == "Edit":
        return _replace(before, tool_input)
    edits = tool_input.get("edits")
    for edit in edits if isinstance(edits, list) else []:
        if isinstance(edit, Mapping):
            before = _replace(before, edit)
    return before


def check(
    tool: str, tool_input: Mapping[str, object], path: Path, *, context: Context, guard: Guard
) -> Verdict | None:
    """``agent-ready-label`` when the call gives a local ticket the agent-ready status."""
    if path.suffix != ".md" or TRACKER_DIRECTORY not in path.parts:
        return None
    label = ready_label(context, guard)
    before = guard.locator.filesystem.read_text(path) or ""
    if label in statuses(after(tool, tool_input, before)) and label not in statuses(before):
        return Verdict("agent-ready-label", f"{path.name} gets the status `{label}`")
    return None
