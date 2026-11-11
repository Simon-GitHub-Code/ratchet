"""The Claude Code SessionStart and SubagentStart hook: the method in every agent's context.

A directory that is not a git repository gets nothing. A repository on the method (its
``AGENTS.md`` or ``CLAUDE.md`` names ``ratchet:method`` and ``docs/process/binding.md``
exists) gets the stance: the section of the method skill that says how to work by the merge
rule, so the philosophy is in context before the first tool call, in the main session and in
every subagent. The two states in between get one short message, so an agent starting in a
bare repository learns that the gates do not exist yet before it writes code nothing checks.

Every message ends with the route's next step: ``scripts/route.py`` decides from the checkout
alone (offline, so the hook stays fast) which command the owner types next, and the hook
prints it so a session never opens without one.

``SessionStart`` reads plain stdout as context; ``SubagentStart`` reads the JSON field
``hookSpecificOutput.additionalContext``. ``render`` picks the form from the event name.
Antigravity reads neither: its ``SessionStart`` takes ``injectSteps``, a system message.
"""

import json
import subprocess
import sys
from collections.abc import Callable
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
ROUTE_SCRIPT = Path("scripts/route.py")
ROUTE_TIMEOUT = 5
NEXT = "Next, for the owner to type: {command}. Why: {why}. Then: {then}."
Runner = Callable[[list[str]], str | None]


def run_route(argv: list[str]) -> str | None:
    """The route script's stdout, or ``None`` when it fails, hangs or is missing."""
    try:
        completed = subprocess.run(  # noqa: S603 # -- the plugin's own script, a fixed argv
            argv, capture_output=True, text=True, check=False, timeout=ROUTE_TIMEOUT
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout if completed.returncode == 0 else None


def next_step(root: Path, plugin_root: Path = PLUGIN_ROOT, run: Runner = run_route) -> str:
    """The route's next command for ``root`` as one sentence, or empty when it cannot be had."""
    script = plugin_root / ROUTE_SCRIPT
    if not script.is_file():
        return ""
    output = run([sys.executable, str(script), "--root", str(root), "--offline", "--json"])
    if output is None:
        return ""
    try:
        step = json.loads(output)
    except ValueError:
        return ""
    if not isinstance(step, dict) or not all(
        isinstance(step.get(k), str) for k in ("command", "why", "then")
    ):
        return ""
    return NEXT.format(command=step["command"], why=step["why"], then=step["then"])


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


def message(cwd: Path, plugin_root: Path = PLUGIN_ROOT, run: Runner = run_route) -> str | None:
    """The context to add for the checkout holding ``cwd``, if any."""
    root = repository_root(cwd)
    if not (root / ".git").exists():
        return None
    if not names_method(root):
        parts = [NO_STANZA.format(root=root)]
    elif not (root / BINDING).is_file():
        parts = [NO_BINDING.format(root=root)]
    else:
        parts = [ADOPTED.format(root=root), stance(plugin_root)]
    parts.append(next_step(root, plugin_root, run))
    return "\n\n".join(part for part in parts if part)


def render(event: str, text: str, *, antigravity: bool = False) -> str:
    """``text`` in the form the hook event reads: JSON for a subagent, plain text otherwise."""
    if antigravity:
        return json.dumps({"injectSteps": [{"systemMessage": {"systemMessage": text}}]})
    if event == SUBAGENT_EVENT:
        output = {"hookEventName": event, "additionalContext": text}
        return json.dumps({"hookSpecificOutput": output})
    return text


def main(root: Path, event: str, *, antigravity: bool = False) -> int:
    """Print the context for ``root`` in the form ``event`` reads, or nothing."""
    text = message(root)
    if text:
        print(render(event, text, antigravity=antigravity))
    return 0
