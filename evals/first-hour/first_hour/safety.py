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
WRITES = re.compile(r">|\btee\b|\bsed\s+-i|\brm\b|\bmv\b|\bcp\b|\bjq\b.*>|\bpython3?\b|\bchmod\b")


def refusal(command: str) -> str | None:
    """Why the harness refuses this Bash command, or ``None``."""
    for pattern, why in FORBIDDEN:
        if pattern.search(command):
            return why
    if USER_FILES.search(command) and WRITES.search(command):
        return "may change the user's settings or installed plugins"
    return None


def _under(path: Path, parent: Path) -> bool:
    return path == parent or parent in path.parents


def forbidden_write(path: str, *, root: Path, protected: tuple[Path, ...]) -> bool:
    """Whether the owner declines a write: one into a protected tree, outside the run directory.

    Why not every write outside the run directory: the product puts its own work trees under
    the temp location (the drain's ``/tmp/ratchet-queue-<uid>``), and an owner answering
    Claude Code's "outside allowed working directories" prompt there says yes. The protected
    trees are the user's home and the plugin checkout under test.
    """
    target = Path(path).resolve()
    if _under(target, root.resolve()):
        return False
    return any(_under(target, p.resolve()) for p in protected)
