"""The Claude Code SessionStart and SubagentStart hook: the method in every agent's context.

A directory that is not a git repository gets nothing. A repository on the method (its
``AGENTS.md`` or ``CLAUDE.md`` names ``ratchet:method`` and ``docs/process/binding.md``
exists) gets the stance: the section of the method skill that says how to work by the merge
rule, so the philosophy is in context before the first tool call, in the main session and in
every subagent. The two states in between get one short message, so an agent starting in a
bare repository learns that the gates do not exist yet before it writes code nothing checks.

``SessionStart`` reads plain stdout as context; ``SubagentStart`` reads the JSON field
``hookSpecificOutput.additionalContext``. ``render`` picks the form from the event name.
"""

import json
from pathlib import Path

from ratchet_guards.hook import PLUGIN_ROOT, repository_root

BINDING = Path("docs/process/binding.md")
INSTRUCTION_FILES = ("AGENTS.md", "CLAUDE.md")
STANZA = "ratchet:method"
METHOD_SKILL = Path("skills/method/SKILL.md")
STANCE_HEADING = "## The stance"
SUBAGENT_EVENT = "SubagentStart"

NO_STANZA = (
    "Ratchet: {root} names no method. No deterministic gate runs on code written here, and no "
    "AGENTS.md says which plugins build it. Before building anything, ask the owner to run "
    "/ratchet:init (AGENTS.md naming mattpocock-skills, pstack, impeccable and ratchet) and "
    "/ratchet:adopt "
    "(the gates). Both are owner-invoked; until then, say in your first reply that nothing checks "
    "the code."
)
NO_BINDING = (
    "Ratchet: {root} names the method in AGENTS.md but has no docs/process/binding.md, so no gate "
    "is bound yet. Load ratchet:method and ask the owner to run /ratchet:adopt before building; "
    "until then, say in your first reply that nothing checks the code."
)
ADOPTED = (
    "Ratchet: {root} is on the method. Load the ratchet:method skill before any work; the "
    "binding's `check` entry point is the gate, and a denied edit is a dispute, not a puzzle."
)


def names_method(root: Path) -> bool:
    """Whether ``AGENTS.md`` or ``CLAUDE.md`` at ``root`` tells agents to load the method."""
    for name in INSTRUCTION_FILES:
        path = root / name
        if path.is_file() and STANZA in path.read_text(encoding="utf-8", errors="replace"):
            return True
    return False


def stance(plugin_root: Path = PLUGIN_ROOT) -> str:
    """The stance section of the method skill, heading included, or empty when unavailable."""
    path = plugin_root / METHOD_SKILL
    if not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    start = text.find(STANCE_HEADING)
    if start < 0:
        return ""
    body = text[start:]
    end = body.find("\n## ", len(STANCE_HEADING))
    return (body if end < 0 else body[:end]).strip()


def message(cwd: Path, plugin_root: Path = PLUGIN_ROOT) -> str | None:
    """The context to add for the checkout holding ``cwd``, if any."""
    root = repository_root(cwd)
    if not (root / ".git").exists():
        return None
    if not names_method(root):
        return NO_STANZA.format(root=root)
    if not (root / BINDING).is_file():
        return NO_BINDING.format(root=root)
    return "\n\n".join(part for part in (ADOPTED.format(root=root), stance(plugin_root)) if part)


def render(event: str, text: str) -> str:
    """``text`` in the form the hook event reads: JSON for a subagent, plain text otherwise."""
    if event == SUBAGENT_EVENT:
        output = {"hookEventName": event, "additionalContext": text}
        return json.dumps({"hookSpecificOutput": output})
    return text


def main(root: Path, event: str) -> int:
    """Print the context for ``root`` in the form ``event`` reads, or nothing."""
    text = message(root)
    if text:
        print(render(event, text))
    return 0
