import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from ratchet_guards import events
from ratchet_guards.hook import PLUGIN_ROOT, common_directory, repository_root

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import plugin_pin

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
NOT_PINNED = (
    "Ratchet: this session runs the plugin at {plugin}, which is not the release "
    "docs/process/binding.md pins, so the local gates will not run from it."
)
ROUTE_SCRIPT = Path("scripts/route.py")
ROUTE_TIMEOUT = 5
NEXT = "Next, for the owner to type: {command}. Why: {why}. Then: {then}."
Runner = Callable[[list[str]], str | None]


def run_route(argv: list[str]) -> str | None:
    try:
        completed = subprocess.run(  # noqa: S603 # -- the plugin's own script, a fixed argv
            argv, capture_output=True, text=True, check=False, timeout=ROUTE_TIMEOUT
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout if completed.returncode == 0 else None


def child_interpreter() -> list[str]:
    argv = [sys.executable]
    if sys.flags.ignore_environment:
        argv.append("-E")
    if sys.flags.no_user_site:
        argv.append("-s")
    return argv


def next_step(root: Path, plugin_root: Path = PLUGIN_ROOT, run: Runner = run_route) -> str:
    script = plugin_root / ROUTE_SCRIPT
    if not script.is_file():
        return ""
    output = run([*child_interpreter(), str(script), "--root", str(root), "--offline", "--json"])
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
    for name in INSTRUCTION_FILES:
        path = root / name
        if path.is_file() and STANZA in path.read_text(encoding="utf-8", errors="replace"):
            return True
    return False


def stance(plugin_root: Path = PLUGIN_ROOT) -> str:
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


def unpinned(root: Path, plugin_root: Path) -> str:
    pin = plugin_pin.pinned((root / BINDING).read_text(encoding="utf-8", errors="replace"))
    if pin is None or pin.tree is None or plugin_pin.copy_tree_id(plugin_root) == pin.tree:
        return ""
    return NOT_PINNED.format(plugin=plugin_root)


def message(cwd: Path, plugin_root: Path = PLUGIN_ROOT, run: Runner = run_route) -> str | None:
    root = repository_root(cwd)
    if not (root / ".git").exists():
        return None
    if not names_method(root):
        parts = [NO_STANZA.format(root=root)]
    elif not (root / BINDING).is_file():
        parts = [NO_BINDING.format(root=root)]
    else:
        parts = [ADOPTED.format(root=root), unpinned(root, plugin_root), stance(plugin_root)]
    parts.append(next_step(root, plugin_root, run))
    return "\n\n".join(part for part in parts if part)


def render(event: str, text: str, *, antigravity: bool = False) -> str:
    if antigravity:
        return json.dumps({"injectSteps": [{"systemMessage": {"systemMessage": text}}]})
    if event == SUBAGENT_EVENT:
        output = {"hookEventName": event, "additionalContext": text}
        return json.dumps({"hookSpecificOutput": output})
    return text


def record_plugin(cwd: Path, plugin_root: Path) -> None:
    common = common_directory(cwd)
    if common is None or not (repository_root(cwd) / BINDING).is_file():
        return
    try:
        events.replace(common / events.PLUGIN_RECORD, f"{plugin_root}\n")
    except OSError:
        return


def main(root: Path, event: str, *, antigravity: bool = False) -> int:
    text = message(root)
    if text:
        print(render(event, text, antigravity=antigravity))
    return 0
