"""What the harness refuses on top of the session's own permissions and the plugin's guard.

The run must not reach a remote, push, or change the installed plugins and the user's settings.
The session's deny rules say most of it; this check also catches the same commands chained
after another one, which a prefix rule does not see. Every refusal is logged as a finding.
"""

from __future__ import annotations

import re
from pathlib import Path

DENY_RULES = (
    "Bash(git push:*)",
    "Bash(git remote add:*)",
    "Bash(git remote set-url:*)",
    "Bash(gh repo create:*)",
    "Bash(gh repo fork:*)",
    "Bash(gh pr create:*)",
    "Bash(gh issue create:*)",
    "Bash(gh label create:*)",
    "Bash(glab repo create:*)",
    "Bash(glab mr create:*)",
    "Bash(claude plugin install:*)",
    "Bash(claude plugin uninstall:*)",
    "Bash(claude plugin enable:*)",
    "Bash(claude plugin disable:*)",
    "Bash(claude plugin update:*)",
    "Bash(claude plugin marketplace:*)",
    "Bash(claude mcp:*)",
)

# Why: git's global options (-C dir, -c key=value, --git-dir=...) may sit before the subcommand.
GIT_OPTIONS = r"(?:\s+-[cC]\s+\S+|\s+--[\w-]+(?:=\S+)?)*"
FORBIDDEN = (
    (re.compile(rf"\bgit{GIT_OPTIONS}\s+push\b"), "pushes"),
    (re.compile(rf"\bgit{GIT_OPTIONS}\s+remote\s+(add|set-url)\b"), "adds a remote"),
    (re.compile(r"\bremote\.[\w.-]+\.(url|pushurl)\b"), "adds a remote"),
    (re.compile(r"\bgh\s+(repo|pr|issue|label|release)\s+(create|fork)\b"), "writes to a forge"),
    (re.compile(r"\bglab\s+(repo|mr|issue|label|release)\s+(create|fork)\b"), "writes to a forge"),
    (
        re.compile(r"\bclaude\s+plugins?\s+(install|uninstall|enable|disable|update|marketplace)"),
        "changes the installed plugins",
    ),
    (re.compile(r"\bclaude\s+mcp\s+(add|remove)"), "changes the MCP servers"),
    (re.compile(r"--dangerously-skip-permissions"), "skips permissions"),
)
USER_FILES = re.compile(r"(~|\$HOME|\$\{HOME\}|/home/[^/\s]+)/\.claude\b")
# Why: `2>&1` and `> /dev/null` are not writes; a preflight that lists ~/.claude/rules uses both.
WRITES = re.compile(
    r"(?<![\d&])>>?(?!&)(?!\s*/dev/null)|\btee\b|\bsed\s+-i"
    r"|\b(rm|mv|cp|ln|chmod|touch|truncate|install)\b|\bpython3?\b"
)
SEGMENTS = re.compile(r"&&|\|\||[;|\n]")
INTO_USER_FILES = re.compile(rf"^\s*(cd|pushd)\s+[\"']?{USER_FILES.pattern}")


def refusal(command: str) -> str | None:
    """Why the harness refuses this Bash command, or ``None``."""
    for pattern, why in FORBIDDEN:
        if pattern.search(command):
            return why
    inside = False
    for segment in SEGMENTS.split(command):
        inside = inside or bool(INTO_USER_FILES.search(segment))
        if (inside or USER_FILES.search(segment)) and WRITES.search(segment):
            return "may change the user's settings or installed plugins"
    return None


def _under(path: Path, parent: Path) -> bool:
    return path == parent or parent in path.parents


def forbidden_write(path: str, *, root: Path, protected: tuple[Path, ...]) -> bool:
    """Whether the owner declines a write: one into a protected tree, outside the run directory.

    Why not every write outside the run directory: the product puts its own work trees under
    the temp location (the drain's ``/tmp/ratchet-queue-<uid>``), and an owner answering
    Claude Code's "outside allowed working directories" prompt there says yes. The protected
    trees are the user's home, the plugin checkout and the run's pinned clone of it.
    """
    target = Path(path).resolve()
    trees = [p.resolve() for p in protected]
    # Why: a protected tree inside the run directory (the pinned plugin) still wins.
    inside = [p for p in trees if _under(p, root.resolve())]
    if any(_under(target, p) for p in inside):
        return True
    if _under(target, root.resolve()):
        return False
    return any(_under(target, p) for p in trees)
