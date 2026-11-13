"""The agent-ready status on a local ticket: an agent that writes it asks the owner."""

from dataclasses import dataclass
from pathlib import Path

import pytest

from ratchet_guards import hook, tickets
from ratchet_guards.verdict import Decision, Verdict
from tests.support import LABELS_RECORD, ROOT, FakeFilesystem, make_guard

TICKET = "/repo/.scratch/f/issues/02-b.md"
TRIAGED = "# 02: B\n\n**Status:** needs-triage\n\nBody.\n"


def decide(tool: str, tool_input: dict[str, object], existing: str | None = None) -> str | None:
    contents = {"/repo/docs/agents/triage-labels.md": LABELS_RECORD}
    if existing is not None:
        contents[TICKET] = existing
    payload = {"tool_name": tool, "tool_input": tool_input, "cwd": str(ROOT)}
    verdict = hook.decide(payload, make_guard(filesystem=FakeFilesystem(contents=contents)))
    if verdict is None:
        return None
    assert verdict.decision is Decision.ASK
    return verdict.rule


@pytest.mark.parametrize(
    ("tool", "tool_input", "existing"),
    [
        ("Write", {"file_path": TICKET, "content": "# 02: B\n\nStatus: agent:ready\n"}, None),
        ("Write", {"file_path": TICKET, "content": "**Status:** `agent:ready`\n"}, TRIAGED),
        (
            "Edit",
            {"file_path": TICKET, "old_string": "needs-triage", "new_string": "agent:ready"},
            TRIAGED,
        ),
        (
            "Edit",
            {
                "file_path": TICKET,
                "old_string": "needs-triage",
                "new_string": "agent:ready",
                "replace_all": True,
            },
            TRIAGED,
        ),
        (
            "MultiEdit",
            {
                "file_path": TICKET,
                "edits": [
                    {"old_string": "Body.", "new_string": "Body, sharper."},
                    {"old_string": "needs-triage", "new_string": "agent:ready"},
                ],
            },
            TRIAGED,
        ),
    ],
)
def test_giving_a_local_ticket_the_agent_ready_status_asks(
    tool: str, tool_input: dict[str, object], existing: str | None
) -> None:
    assert decide(tool, tool_input, existing) == "agent-ready-label"


@pytest.mark.parametrize(
    ("tool", "tool_input", "existing"),
    [
        ("Write", {"file_path": TICKET, "content": "Status: needs-triage\n"}, None),
        ("Write", {"file_path": "/repo/docs/x.md", "content": "Status: agent:ready\n"}, None),
        (
            "Write",
            {"file_path": "/repo/.scratch/f/x.txt", "content": "Status: agent:ready\n"},
            None,
        ),
        (
            "Edit",
            {"file_path": TICKET, "old_string": "Body.", "new_string": "More."},
            "Status: agent:ready\n\nBody.\n",
        ),
        ("MultiEdit", {"file_path": TICKET, "edits": "not a list"}, TRIAGED),
        ("MultiEdit", {"file_path": TICKET, "edits": ["not an edit"]}, TRIAGED),
    ],
)
def test_other_writes_to_tracker_files_pass(
    tool: str, tool_input: dict[str, object], existing: str | None
) -> None:
    assert decide(tool, tool_input, existing) is None


ISSUES = "/repo/.scratch/f/issues"
ON_DISK = {
    f"{ISSUES}/01-a.md": TRIAGED,
    f"{ISSUES}/02-b.md": TRIAGED,
    f"{ISSUES}/03-c.md": "Status: agent:ready\n",
    "/repo/.scratch/f/notes.txt": "notes\n",
    "/repo/docs/notes.md": "notes\n",
}
SED = "sed -i s/needs-triage/agent:ready/"


def called(tool: str, tool_input: dict[str, object]) -> Verdict | None:
    contents = {"/repo/docs/agents/triage-labels.md": LABELS_RECORD, **ON_DISK}
    filesystem = FakeFilesystem(files=tuple(contents), contents=contents)
    payload = {"tool_name": tool, "tool_input": tool_input, "cwd": str(ROOT)}
    return hook.decide(payload, make_guard(filesystem=filesystem))


def shell(command: str) -> str | None:
    verdict = called("Bash", {"command": command})
    if verdict is None:
        return None
    assert verdict.rule == "agent-ready-label"
    assert verdict.decision is Decision.ASK
    return verdict.detail


def test_one_command_over_the_tickets_of_a_spec_is_one_ask_that_names_each() -> None:
    asked = shell(f"{SED} .scratch/f/issues/*.md")
    assert asked == "01-a.md, 02-b.md get the status `agent:ready`", "03-c.md holds it already"
    assert shell(f"{SED} .scratch/f/issues/*") == asked, "the pattern is read, not its suffix"


@pytest.mark.parametrize(
    ("command", "named"),
    [
        (f"{SED} .scratch/f/issues/01-a.md", "01-a.md gets"),
        (f"cd .scratch/f/issues && {SED} 01-a.md", "01-a.md gets"),
        ("echo 'Status: agent:ready' > .scratch/f/issues/04-d.md", "04-d.md gets"),
        ("printf 'Status: agent:ready' | tee .scratch/f/issues/04-d.md", "04-d.md gets"),
        ("cat > .scratch/f/issues/04-d.md <<'EOF'\nStatus: agent:ready\nEOF", "04-d.md gets"),
        ("perl -pi -e s/needs-triage/agent:ready/ .scratch/f/issues/02-b.md", "02-b.md gets"),
        (
            "python3 -c \"open('.scratch/f/issues/01-a.md', 'w').write('Status: agent:ready')\"",
            "01-a.md gets",
        ),
    ],
)
def test_a_shell_write_that_gives_a_local_ticket_the_status_asks(command: str, named: str) -> None:
    assert shell(command) == f"{named} the status `agent:ready`"


def test_two_commands_on_one_line_are_one_ask_that_names_both_tickets() -> None:
    asked = shell(f"{SED} .scratch/f/issues/01-a.md && {SED} .scratch/f/issues/02-b.md")
    assert asked == ("01-a.md gets the status `agent:ready`; 02-b.md gets the status `agent:ready`")
    forge = shell(f"gh issue edit 4 --add-label agent:ready; {SED} .scratch/f/issues/01-a.md")
    assert forge == "gh issue edit agent:ready; 01-a.md gets the status `agent:ready`"


@pytest.mark.parametrize(
    "command",
    [
        "sed -i s/agent:ready/resolved/ .scratch/f/issues/03-c.md",
        "sed -i s/needs-triage/needs-info/ .scratch/f/issues/01-a.md",
        "grep -l agent:ready .scratch/f/issues/*.md",
        "sed s/needs-triage/agent:ready/ .scratch/f/issues/01-a.md",
        f"{SED} docs/notes.md",
        f"{SED} .scratch/f/notes.txt",
        f"{SED} ''",
    ],
)
def test_a_shell_command_that_gives_no_ticket_the_status_passes(command: str) -> None:
    assert shell(command) is None


def test_a_patch_that_gives_local_tickets_the_status_is_one_ask_that_names_each() -> None:
    patch = (
        "*** Begin Patch\n"
        "*** Add File: .scratch/f/issues/04-d.md\n+Status: agent:ready\n"
        "*** Update File: .scratch/f/issues/01-a.md\n@@\n-needs-triage\n+agent:ready\n"
        "*** Update File: .scratch/f/issues/03-c.md\n@@\n-Status: agent:ready\n+Body.\n"
        "*** End Patch\n"
    )
    verdict = called("apply_patch", {"input": patch})
    assert verdict is not None
    assert verdict.rule == "agent-ready-label"
    assert verdict.detail == "04-d.md, 01-a.md get the status `agent:ready`"
    quiet = patch.replace("agent:ready", "needs-info")
    assert called("apply_patch", {"input": quiet}) is None


def test_without_a_record_the_role_name_is_the_label() -> None:
    guard = make_guard(filesystem=FakeFilesystem(contents={}))
    payload = {
        "tool_name": "Write",
        "tool_input": {"file_path": TICKET, "content": "Status: ready-for-agent\n"},
        "cwd": str(ROOT),
    }
    verdict = hook.decide(payload, guard)
    assert verdict is not None
    assert "02-b.md gets the status `ready-for-agent`" in verdict.detail


def test_statuses_reads_every_spelling_the_preflight_reads() -> None:
    text = "Status: a\n**Status:** b\n**Status**: `c`\nstatus d\n"
    assert tickets.statuses(text) == {"a", "b", "c", "d"}
    assert tickets.after("Write", {}, "x") == ""


def test_the_label_is_the_record_of_the_checkout_holding_the_ticket() -> None:
    sibling = Path("/sibling")
    record = LABELS_RECORD.replace("agent:ready", "go:build")
    filesystem = FakeFilesystem(
        guarded=(ROOT, sibling), contents={"/sibling/docs/agents/triage-labels.md": record}
    )
    guard = make_guard(filesystem=filesystem)
    ticket = "/sibling/.scratch/f/issues/02-b.md"

    def asked(label: str) -> str | None:
        tool_input = {"file_path": ticket, "content": f"Status: {label}\n"}
        payload = {"tool_name": "Write", "tool_input": tool_input, "cwd": str(ROOT)}
        verdict = hook.decide(payload, guard)
        return None if verdict is None else verdict.rule

    assert asked("go:build") == "agent-ready-label"
    assert asked("agent:ready") is None


@pytest.mark.parametrize(
    ("name", "args", "existing"),
    [
        ("write_to_file", {"TargetFile": TICKET, "CodeContent": "Status: agent:ready\n"}, None),
        (
            "replace_file_content",
            {
                "TargetFile": TICKET,
                "TargetContent": "needs-triage",
                "ReplacementContent": "agent:ready",
                "AllowMultiple": True,
            },
            TRIAGED,
        ),
        (
            "multi_replace_file_content",
            {
                "TargetFile": TICKET,
                "ReplacementChunks": [
                    "not a chunk",
                    {"TargetContent": "needs-triage", "ReplacementContent": "agent:ready"},
                ],
            },
            TRIAGED,
        ),
    ],
)
def test_antigravity_edits_that_give_the_status_ask(
    name: str, args: dict[str, object], existing: str | None
) -> None:
    contents = {"/repo/docs/agents/triage-labels.md": LABELS_RECORD}
    if existing is not None:
        contents[TICKET] = existing
    payload = {"toolCall": {"name": name, "args": args}, "cwd": str(ROOT)}
    verdict = hook.decide(payload, make_guard(filesystem=FakeFilesystem(contents=contents)))
    assert verdict is not None
    assert verdict.rule == "agent-ready-label"


@pytest.mark.parametrize(
    "path",
    [
        "/repo/.scratch/f/issues/../issues/02-b.md",
        "/repo/.scratch/f/./issues/02-b.md",
        "/repo/.SCRATCH/f/Issues/02-b.MD",
    ],
)
def test_a_ticket_named_another_way_still_asks(path: str) -> None:
    content = "# 02: B\n\nStatus: agent:ready\n"
    assert decide("Write", {"file_path": path, "content": content}) == "agent-ready-label"


@pytest.mark.parametrize(
    "path",
    [
        "/repo/.scratch/changes/feature-x.md",
        "/repo/.scratch/f/spec.md",
        "/repo/.scratch/f/notes.md",
        "/repo/.scratch/f/issues/old/02-b.md",
        "/repo/.scratch/issues/02-b.md",
    ],
)
def test_a_tracker_file_that_is_no_ticket_does_not_ask(path: str) -> None:
    content = "Status: agent:ready\n\nSplits the spec into tickets, each labelled agent:ready.\n"
    assert decide("Write", {"file_path": path, "content": content}) is None


def test_a_change_description_that_names_the_label_does_not_ask() -> None:
    described = (
        "mkdir -p .scratch/changes && cat > .scratch/changes/tickets.md <<EOF\n"
        "## Summary\nSplits the spec into four tickets, each labelled agent:ready.\nEOF"
    )
    assert shell(described) is None
    assert shell("echo 'Status: agent:ready' > .scratch/f/spec.md") is None


READY = "echo 'Status: agent:ready' >"
TEE = "echo 'Status: agent:ready' | tee"


@pytest.mark.parametrize(
    ("command", "named"),
    [
        (f"{READY} .scratch/f/issue?/04-d.md", "04-d.md gets"),
        (f"{READY} .scratch/*/issues/04-d.md", "04-d.md gets"),
        (f"{READY} .scratch/f/x/../issues/04-d.md", "04-d.md gets"),
        (f"{READY} .SCRATCH/f/ISSUES/04-d.MD", "04-d.MD gets"),
        (f"{READY} /tmp/t.md; mv /tmp/t.md .scratch/f/issues/", "issues/, t.md get"),
        (f"{READY} .scratch/n/01.md; mv .scratch/n .scratch/g/issues", "issues/ gets"),
        (f"{READY} /tmp/t/issues/01.md; cp -r /tmp/t .scratch/g", "g/ gets"),
        (f"{READY} /tmp/s/g/issues/01.md; mv /tmp/s .scratch", ".scratch/, s/ get"),
        (f"cd .scratch/f && {READY}> issues/04-d.md", "04-d.md gets"),
        # Why: brace expansion, a variable or a substitution may spell any part of the path.
        (f"{TEE} .scratch/f/{{issues,none}}/04-d.md", "04-d.md gets"),
        (f"{TEE} .scratch/f/issues/04-d.{{md,txt}}", "04-d.{md,txt} gets"),
        (f"{TEE} .scratch/f/$D/04-d.md", "04-d.md gets"),
        (f"{TEE} .scratch/f/$(echo issues)/04-d.md", "04-d.md gets"),
    ],
)
def test_every_other_way_to_write_a_ticket_with_the_status_asks(command: str, named: str) -> None:
    asked = shell(command)
    assert asked is not None
    assert named in asked


@dataclass
class LinkedFilesystem(FakeFilesystem):
    """``/repo/t`` is a link to the tickets of feature ``f``."""

    def canonical(self, path: Path) -> Path:
        here = super().canonical(path)
        link = Path("/repo/t")
        return Path(ISSUES) / here.relative_to(link) if here.is_relative_to(link) else here


def test_a_ticket_written_through_a_link_asks() -> None:
    guard = make_guard(filesystem=LinkedFilesystem(contents={}))
    command = "echo 'Status: ready-for-agent' > t/04-d.md"
    payload = {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(ROOT)}
    verdict = hook.decide(payload, guard)
    assert verdict is not None
    assert verdict.detail == "04-d.md gets the status `ready-for-agent`"
