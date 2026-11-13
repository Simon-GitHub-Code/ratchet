from pathlib import Path

import pytest

from first_hour import policy
from first_hour.policy import Scope

ROOT = Path("/tmp/fh-run")
SCOPE = Scope(
    root=ROOT,
    cwd=ROOT / "app",
    home=ROOT / "home",
    tmp=ROOT / "tmp",
    writable=(ROOT / "app", ROOT / "home", ROOT / "tmp"),
)

# Every kind of tool an operator's own sessions may carry: connectors, remote MCP servers, the web,
# remote triggers, notifications, scheduling and messages to other sessions or projects.
NOT_ON_THE_LIST = [
    "mcp__claude_ai_Brokerage__create_order",
    "mcp__claude_ai_Brokerage__get_positions",
    "mcp__claude_ai_Mail__send_message",
    "mcp__claude_ai_Chat__post_message",
    "mcp__claude_ai_Drive__delete_file",
    "mcp__claude_ai_Calendar__create_event",
    "mcp__claude_ai_Docs__create",
    "mcp__claude_ai_Docs__delete",
    "mcp__claude_ai_Fitness__list_activities",
    "mcp__claude_ai_Library_Docs__query-docs",
    "mcp__playwright__browser_navigate",
    "mcp__playwright__browser_run_code_unsafe",
    "mcp__plugin_productivity_tracker__authenticate",
    "WebFetch",
    "WebSearch",
    "RemoteTrigger",
    "PushNotification",
    "SendToProject",
    "SendToProjectContext",
    "CronCreate",
    "ScheduleWakeup",
    "DesignSync",
    "ReadMcpResourceTool",
    "EnterWorktree",
    "Monitor",
    "ListAgents",
    "SendUserFile",
    "SendFeedback",
    "Artifact",
    "a-tool-nobody-has-heard-of",
]


@pytest.mark.parametrize("tool", NOT_ON_THE_LIST)
def test_a_tool_not_on_the_list_is_denied_whatever_its_input(tool: str) -> None:
    decision = policy.decide(tool, {"url": "https://example.com", "to": "a1"}, SCOPE, agents={"a1"})
    assert not decision.allowed
    assert tool in decision.why


def test_every_tool_the_cli_is_told_to_refuse_is_off_the_list() -> None:
    assert not set(policy.DENIED_TOOLS) & set(policy.TOOLS)


# Each case: the command, and the reason the policy gives. The sandbox and the missing credentials
# stop these too (test_first_hour_sandbox.py runs them); this is the layer that records them.
FORGE_AND_NETWORK = [
    ("gh repo create app --private --source . --push", "calls a forge"),
    ("gh api -X POST user/repos -f name=app", "calls a forge"),
    ("git init && gh api -X POST user/repos -f name=app", "calls a forge"),
    ("GH_CONFIG_DIR=/x /usr/bin/gh pr create", "calls a forge"),
    ("glab repo create app", "calls a forge"),
    ("git push origin main", "pushes to a remote"),
    ("git add . && git -C . push", "pushes to a remote"),
    ("git -c user.name=x remote add origin ../elsewhere", "adds a remote"),
    ("git remote set-url origin git@example.com:a/b.git", "adds a remote"),
    ("git config remote.origin.pushurl x", "adds a remote"),
    ("git clone https://github.com/a/b", "names a forge"),
    ("curl -H 'Authorization: token abc' https://api.example.com/user/repos", "network"),
    ("echo hi | /usr/bin/curl -d @- https://example.com", "network"),
    ("wget -q https://example.com/x", "network"),
    ("ssh git@example.com", "network"),
    ("nc -l 9000", "network"),
    (
        (
            "python3 -c \"import urllib.request as u; u.urlopen(u.Request('https://api.example.com',"
            " data=b'{}'))\""
        ),
        "inline code",
    ),
    ("python -c 'import http.client; http.client.HTTPSConnection(\"x\")'", "inline code"),
    ("node -e \"fetch('https://example.com', {method: 'POST'})\"", "inline code"),
    ("npm publish", "publishes"),
    ("pnpm --filter app publish", "publishes"),
    ("uv publish", "publishes"),
    ("twine upload dist/*", "publishes"),
    ("docker push me/app", "publishes"),
    ("claude -p 'do it' --allowedTools Bash", "Claude Code"),
    ("claude plugin install foo@bar", "Claude Code"),
    ("claude mcp add x", "Claude Code"),
    ("echo x; claude --dangerously-skip-permissions", "Claude Code"),
    ("cat ~/.claude/.credentials.json", "credential"),
    ("cat /proc/1/environ", "credential"),
]


@pytest.mark.parametrize(("command", "why"), FORGE_AND_NETWORK)
def test_a_command_that_reaches_a_forge_or_the_network_is_refused(command: str, why: str) -> None:
    decision = policy.decide("Bash", {"command": command}, SCOPE)
    assert not decision.allowed, command
    assert why in decision.why


OUTSIDE = [
    "echo '{}' > /home/someone/.claude/settings.json",
    "cat notes.txt | tee /home/someone/.bashrc",
    "cp notes.txt /home/someone/notes.txt",
    'python3 -c \'open("/home/someone/x", "w").write("x")\'',
    "git -C /home/someone/src/other commit -am x",
    "cd ../.. && rm -rf x",
    "ls ../../..",
    "sed -i s/a/b/ /var/lib/x",
    "cat /home/someone/.config/gh/hosts.yml",
    "echo x >/srv/x",
    "touch /run/user/1001/x",
]


@pytest.mark.parametrize("command", OUTSIDE)
def test_a_path_outside_the_run_directory_is_refused(command: str) -> None:
    decision = policy.decide("Bash", {"command": command}, SCOPE)
    assert not decision.allowed, command
    assert "outside the run directory" in decision.why


ALLOWED = [
    "git init -b main && claude plugin list; gh auth status; command -v glab",
    "glab auth status 2>&1 | head -3",
    "claude --version",
    "git remote -v",
    "git log --oneline | grep push",
    "git commit -m 'save the link on push of the button'",
    "python3 -c 'print(1)' > out.txt",
    "cp ../plugin/skills/init/templates/AGENTS.md AGENTS.md && ln -s AGENTS.md CLAUDE.md",
    "cat ~/.claude/plugins/cache/m/p/1.0/skills/x/SKILL.md",
    'mkdir -p "${TMPDIR:-/tmp}/rehearsal" && git worktree add --detach /tmp/rehearsal HEAD',
    "echo ok > /dev/null 2>&1",
    "ls /usr/lib/node_modules && cat /etc/os-release",
    "pnpm install && pnpm test",
    "just check",
    "uv run pytest -q",
    "npm view vite version",
]


@pytest.mark.parametrize("command", ALLOWED)
def test_ordinary_project_work_is_not_refused(command: str) -> None:
    assert policy.decide("Bash", {"command": command}, SCOPE) == policy.ALLOW, command


@pytest.mark.parametrize(
    ("tool", "tool_input", "allowed"),
    [
        ("Write", {"file_path": str(ROOT / "app" / "src" / "a.ts")}, True),
        ("Edit", {"file_path": "src/a.ts"}, True),
        ("Write", {"file_path": str(ROOT / "tmp" / "ratchet-queue-1" / "t" / "a.ts")}, True),
        ("Write", {"file_path": "/tmp/ratchet-queue-1/t/a.ts"}, False),
        ("Write", {"file_path": str(ROOT / "events.jsonl")}, False),
        ("Write", {"file_path": str(ROOT / "plugin" / "scripts" / "route.py")}, False),
        ("Write", {"file_path": "/home/someone/.claude/settings.json"}, False),
        ("NotebookEdit", {"notebook_path": "/srv/n.ipynb"}, False),
        ("Read", {"file_path": str(ROOT / "plugin" / "skills" / "init" / "SKILL.md")}, True),
        ("Read", {"file_path": "/usr/lib/python3/dist-packages/x.py"}, True),
        ("Read", {"file_path": "/home/someone/.ssh/id_ed25519"}, False),
        ("Read", {"file_path": str(ROOT / "home" / ".claude" / ".credentials.json")}, False),
        ("Glob", {"pattern": "/home/someone/**/*.md"}, False),
        ("Glob", {"pattern": "src/**/*.ts"}, True),
        ("Grep", {"pattern": "TODO", "path": "/home/someone/src"}, False),
        ("Grep", {"pattern": "TODO"}, True),
        ("Workflow", {"scriptPath": str(ROOT / "plugin" / "workflows" / "pipeline.js")}, True),
        ("Workflow", {"scriptPath": "/home/someone/w.js"}, False),
        ("Skill", {"skill": "ratchet:init"}, True),
        ("AskUserQuestion", {"questions": []}, True),
    ],
)
def test_file_tools_reach_the_run_and_write_only_the_owners_trees(
    *, tool: str, tool_input: dict[str, object], allowed: bool
) -> None:
    assert policy.decide(tool, tool_input, SCOPE).allowed is allowed


def test_a_file_tool_is_judged_by_where_a_link_leads(tmp_path: Path) -> None:
    """The file tools run outside the sandbox: a link in the project must not carry them out."""
    root = tmp_path / "run"
    (root / "app").mkdir(parents=True)
    outside = tmp_path / "operator-secret"
    outside.write_text("x")
    (root / "app" / "link").symlink_to(outside)
    (root / "app" / "inside").symlink_to(root / "app")
    scope = Scope(
        root=root, cwd=root / "app", home=root / "home", tmp=root / "tmp", writable=(root / "app",)
    )
    for tool in ("Read", "Write", "Edit"):
        assert not policy.decide(tool, {"file_path": "link"}, scope).allowed
    assert policy.decide("Write", {"file_path": "inside/a.py"}, scope).allowed


def test_a_shell_line_names_the_runs_temp_as_tmp_and_a_file_tool_does_not() -> None:
    assert policy.decide("Bash", {"command": "ls /tmp/ratchet-queue-1"}, SCOPE).allowed
    assert not policy.decide("Read", {"file_path": "/tmp/ratchet-queue-1/a"}, SCOPE).allowed


def test_a_message_goes_only_to_a_subagent_this_session_started() -> None:
    assert policy.decide("SendMessage", {"to": "a80a"}, SCOPE, agents={"a80a"}).allowed
    refused = policy.decide("SendMessage", {"to": "another-session"}, SCOPE, agents={"a80a"})
    assert not refused.allowed
    assert "subagent" in refused.why
