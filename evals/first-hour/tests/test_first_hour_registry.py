"""A session reads public registry metadata with curl, and is told what a refusal leaves alone.

The allowed lines are what an eval run's `/ratchet:adopt` ran to look up current tool versions,
cut down and with neutral names. The refused lines are the ways a `curl` could send, sign or leave
the registries.
"""

from pathlib import Path

import pytest

from first_hour import policy, registry, shellpaths
from first_hour.policy import Scope

ROOT = Path("/tmp/fh-run")
SCOPE = Scope(
    root=ROOT,
    cwd=ROOT / "app",
    home=ROOT / "home",
    tmp=ROOT / "tmp",
    writable=(ROOT / "app", ROOT / "home", ROOT / "tmp"),
)

READS = [
    "curl -s https://pypi.org/pypi/ruff/json",
    "curl -sSL --max-time 10 https://registry.npmjs.org/vite | head -c 300",
    (
        "for p in ruff mypy; do curl -s --max-time 10 https://pypi.org/pypi/$p/json |"
        " python3 -c \"\nimport json,sys\nd=json.load(sys.stdin)\nprint(d['info']['version'])\n\";"
        " done"
    ),
    "curl -fsS -H 'Accept: application/json' -A 'tool-check' https://pypi.org/pypi/ruff/json",
    "curl -s -X GET https://crates.io/api/v1/crates/serde",
    "curl -s --request=GET --retry 2 https://rubygems.org/api/v1/gems/rake.json",
    "curl -s -m 5 -o /dev/null -w '%{http_code}' https://index.crates.io/se/rd/serde",
    "curl --silent --compressed https://proxy.golang.org/example.com/mod/@latest",
    "v=$(curl -s https://pypi.org/pypi/ruff/json) && echo done",
    'echo "$(curl -s https://pypi.org/pypi/ruff/json)"',
    "env -i curl -s https://pypi.org/pypi/ruff/json",
    "sh -c 'curl -s https://pypi.org/pypi/ruff/json'",
    "curl -s https://pypi.org/pypi/ruff/json > ruff.json",
    "curl -s https://pypi.org/pypi/ruff/json -o ruff.json",
]


@pytest.mark.parametrize("command", READS)
def test_a_session_may_read_public_registry_metadata(command: str) -> None:
    assert policy.decide("Bash", {"command": command}, SCOPE) == policy.ALLOW, command


SENDS_OR_LEAVES = [
    "curl -s -X POST https://pypi.org/pypi/ruff/json",
    "curl -s -d x=1 https://pypi.org/pypi/ruff/json",
    "curl -s --data-binary @x https://registry.npmjs.org/ruff",
    "curl -s -T x https://registry.npmjs.org/ruff",
    "curl -s -F f=@x https://pypi.org/legacy/",
    "curl -s --user someone https://pypi.org/pypi/ruff/json",
    "curl -s -H 'Authorization: Bearer abc' https://registry.npmjs.org/ruff",
    "curl -s -H 'Cookie: a=b' https://registry.npmjs.org/ruff",
    "curl -s --header 'X-Token: abc' https://registry.npmjs.org/ruff",
    "curl -s -K cfg https://pypi.org/pypi/ruff/json",
    "curl -s --netrc https://pypi.org/pypi/ruff/json",
    "curl -s -X DELETE https://registry.npmjs.org/ruff",
    "curl -s --request=PUT https://registry.npmjs.org/ruff",
    "curl -s https://example.com/",
    "curl -s http://pypi.org/pypi/ruff/json",
    "curl -s https://pypi.org.example.com/pypi/ruff/json",
    "curl -s https://pypi.org@example.com/x",
    "curl -s https://$HOST/x",
    "curl -s http://127.0.0.1:8000/",
    "curl -s -X DELETE http://127.0.0.1:8000/save",
    "curl -s",
    "curl -s -",
    "curl -s -m10 https://pypi.org/pypi/ruff/json",
    "curl -s -m",
    "curl -s --max-time",
    "curl -s --header",
    "curl -s --silent=1 https://pypi.org/pypi/ruff/json",
    "curl -s --no-such-flag https://pypi.org/pypi/ruff/json",
    "curl -s https://pypi.org/pypi/ruff/json https://example.com",
    "curl -s https://pypi.org/pypi/ruff/json -o /home/someone/x",
    "echo use curl",
    "bash -c \"echo 'curl -s https://pypi.org/x'\"",
    'echo "unclosed; curl -s https://pypi.org/x',
]


@pytest.mark.parametrize("command", SENDS_OR_LEAVES)
def test_a_curl_that_could_send_sign_or_leave_the_registries_is_refused(command: str) -> None:
    decision = policy.decide("Bash", {"command": command}, SCOPE)
    assert not decision.allowed, command


def test_the_registries_are_a_short_list_of_https_hosts() -> None:
    assert len(registry.HOSTS) <= 8
    assert not any("github" in host or "gitlab" in host for host in registry.HOSTS)


# Each was refused as a registry publish, a network connection or a path in an eval run: text that
# a command writes into a file, or a loop's own words, read as a command.
NOT_COMMANDS = [
    # a heredoc that edits a document which says `uv` and later `publish`
    (
        "python3 - <<'EOF'\np='doc.md'; s=open(p).read()\n"
        "s=s.replace('<runner>','`just`; every tool runs through `uv run --frozen`, so the owner"
        " can publish')\nEOF"
    ),
    # a heredoc that appends to a file a list of gates and a word like `token`
    (
        "cat >> quality/bites.toml <<'EOF'\n"
        'setup = ["uv sync --frozen --quiet"]\nexpect = "token"\nEOF'
    ),
    # code that writes prose about the modules a project bans, in string literals
    (
        "python3 - <<'EOF'\ns=open('AGENTS.md').read()\n"
        "s=s.replace('x','`lint` refuses `urllib.request`, `http.client` and socket use')\nEOF"
    ),
    (
        "python3 - <<'EOF'\ns=open('t.py').read()\n"
        "s=s.replace('import a\\n', 'import socket\\nimport a\\n')\nEOF"
    ),
    "node -e \"console.log('fetch(x) and https.request are only words here')\"",
    # a sed script after `do`, which is the program the loop runs
    'for g in a b; do sed -n "/==> $g <==/,/done: $g:/p" /tmp/x.log | head -5; done',
    "if true; then sed -n '/a/,/b/p' notes.txt; fi",
]


@pytest.mark.parametrize("command", NOT_COMMANDS)
def test_text_a_command_only_writes_is_not_a_command(command: str) -> None:
    assert policy.decide("Bash", {"command": command}, SCOPE) == policy.ALLOW, command


STILL_REFUSED = [
    ("bash -c 'npm publish'", "publishes"),
    ("bash <<'EOF'\nuv publish\nEOF", "publishes"),
    ("for d in a b; do cargo login; done", "publishes"),
    ('echo "unclosed; npm publish', "publishes"),
    ("python3 - <<'EOF'\nimport socket\nsocket.create_connection(('x', 1))\nEOF", "inline code"),
    ("python3 -c \"__import__('socket').create_connection(('x', 1))\"", "inline code"),
    ("node -e \"require('https').get('https://x')\"", "inline code"),
    ('python3 -c "import urllib.request" "unclosed', "inline code"),
]


@pytest.mark.parametrize(("command", "why"), STILL_REFUSED)
def test_a_registry_publish_and_a_network_script_are_still_refused(command: str, why: str) -> None:
    decision = policy.decide("Bash", {"command": command}, SCOPE)
    assert not decision.allowed, command
    assert why in decision.why


def test_a_path_one_directory_too_far_up_is_a_real_reach() -> None:
    """From a work tree under the run's tmp, five `..` leave the run; four reach its root."""
    cwd = f"cd {ROOT}/tmp/queue/app/feature"
    assert policy.decide("Bash", {"command": f"{cwd} && ls ../../../../plugin"}, SCOPE).allowed
    far = policy.decide("Bash", {"command": f"{cwd} && ls ../../../../../plugin"}, SCOPE)
    assert not far.allowed
    assert "outside the run directory" in far.why


def test_a_file_tool_cannot_write_beside_the_owners_trees_in_the_run() -> None:
    refused = policy.decide("Write", {"file_path": str(ROOT / "tools" / "helper.py")}, SCOPE)
    assert not refused.allowed
    assert "writes outside the owner's trees" in refused.why


def test_scan_reads_the_commands_and_the_code_a_line_runs() -> None:
    found = shellpaths.scan("A=1 xargs -n1 curl -s u; do ls; python3 -c 'print(1)'")
    assert found is not None
    assert [call.name for call in found.calls] == ["curl", "ls", "python3"]
    assert found.code == ("print(1)",)
    assert shellpaths.scan('echo "unclosed') is None


REFUSALS = [
    "git push origin main",
    "gh repo create x",
    "curl -s -X POST https://pypi.org/x",
    "wget https://example.com",
    "npm publish",
    "claude -p x",
    "cat ~/.claude/.credentials.json",
    "cat /home/someone/x",
    "python3 -c 'import socket'",
]


@pytest.mark.parametrize("command", REFUSALS)
def test_a_refused_shell_command_says_what_is_refused_and_what_still_works(command: str) -> None:
    told = policy.decide("Bash", {"command": command}, SCOPE).told
    assert told.startswith("This call is refused: it ")
    assert "Only this call is refused." in told
    assert "Package installs" in told
    assert "public package-registry metadata still work" in told
    assert "curl -s https://pypi.org/pypi/<name>/json" in told


@pytest.mark.parametrize(
    ("tool", "tool_input"),
    [
        ("WebFetch", {"url": "https://pypi.org/pypi/ruff/json"}),
        ("SendMessage", {"to": "somebody-else"}),
        ("Write", {"file_path": "/home/someone/x"}),
        ("Read", {"file_path": "/home/someone/.ssh/id_ed25519"}),
        ("Read", {"file_path": "/home/someone/.claude/.credentials.json"}),
    ],
)
def test_a_refused_tool_call_says_what_still_works(tool: str, tool_input: dict[str, str]) -> None:
    decision = policy.decide(tool, tool_input, SCOPE, agents={"a1"})
    assert not decision.allowed
    assert decision.told == f"This call is refused: it {decision.why}. {policy.STILL_WORKS}"
    assert "registry" in decision.told
