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

FORBIDDEN = (
    (re.compile(r"\bgit\s+push\b"), "pushes"),
    (re.compile(r"\bgit\s+remote\s+(add|set-url)\b"), "adds a remote"),
    (re.compile(r"\bgh\s+(repo|pr|issue|label|release)\s+(create|fork)\b"), "writes to a forge"),
    (re.compile(r"\bglab\s+(repo|mr|issue|label|release)\s+(create|fork)\b"), "writes to a forge"),
    (
        re.compile(r"\bclaude\s+plugins?\s+(install|uninstall|enable|disable|update|marketplace)"),
        "changes the installed plugins",
    ),
    (re.compile(r"\bclaude\s+mcp\s+(add|remove)"), "changes the MCP servers"),
    (re.compile(r"--dangerously-skip-permissions"), "skips permissions"),
)
USER_FILES = re.compile(
    r"(~|\$HOME|\$\{HOME\}|/home/[^/\s]+)/\.claude/(settings[^/\s]*\.json|plugins/(installed_plugins|known_marketplaces)\.json)"
)
WRITES = re.compile(r">|\btee\b|\bsed\s+-i|\brm\b|\bmv\b|\bcp\b|\bjq\b.*>|\bpython3?\b|\bchmod\b")


def refusal(command: str) -> str | None:
    """Why the harness refuses this Bash command, or ``None``."""
    for pattern, why in FORBIDDEN:
        if pattern.search(command):
            return why
    if USER_FILES.search(command) and WRITES.search(command):
        return "may change the user's settings or installed plugins"
    return None


def outside(path: str, root: Path) -> bool:
    """Whether a file a tool writes lies outside the run's directory."""
    try:
        Path(path).resolve().relative_to(root.resolve())
    except ValueError:
        return True
    return False
