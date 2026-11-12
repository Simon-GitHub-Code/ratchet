"""The session hook: the stance on an adopted checkout, a nudge on a bare one, silence elsewhere."""

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from ratchet_guards import session
from tests.support import PLUGIN_ROOT

STANZA = "This repository follows the method: load the `ratchet:method` skill first.\n"
STANCE = "## The stance\n\nWork as a critical rationalist.\n\n- One.\n- Two.\n"
SKILL = "---\nname: method\n---\n\n# The method\n\nIntro.\n\n" + STANCE + "\n## Working\n\nLater.\n"
STEP = '{"command": "/ratchet:adopt", "why": "no binding", "then": "grill", "stage": "adopt"}'


def silent(_argv: list[str]) -> str | None:
    return None


def checkout(tmp_path: Path, *, stanza: bool, binding: bool, file: str = "AGENTS.md") -> Path:
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    if stanza:
        (root / file).write_text(STANZA, encoding="utf-8")
    if binding:
        (root / session.BINDING).parent.mkdir(parents=True)
        (root / session.BINDING).write_text("# Binding\n", encoding="utf-8")
    return root


def plugin(tmp_path: Path, *, skill: str | None = SKILL, route: bool = False) -> Path:
    root = tmp_path / "plugin"
    (root / session.METHOD_SKILL).parent.mkdir(parents=True, exist_ok=True)
    if skill is not None:
        (root / session.METHOD_SKILL).write_text(skill, encoding="utf-8")
    if route:
        (root / session.ROUTE_SCRIPT).parent.mkdir(parents=True, exist_ok=True)
        (root / session.ROUTE_SCRIPT).write_text("print('{}')\n", encoding="utf-8")
    return root


def test_a_directory_that_is_not_a_repository_gets_nothing(tmp_path: Path) -> None:
    assert session.message(tmp_path / "loose", plugin(tmp_path)) is None


def test_a_bare_repository_is_told_to_init_and_adopt(tmp_path: Path) -> None:
    root = checkout(tmp_path, stanza=False, binding=False)
    line = session.message(root, plugin(tmp_path))
    assert line is not None
    assert line.startswith(f"Ratchet: {root} names no method.")
    assert "/ratchet:init" in line
    assert "/ratchet:adopt" in line
    assert "## The stance" not in line


def test_a_stanza_without_a_binding_is_told_to_adopt(tmp_path: Path) -> None:
    root = checkout(tmp_path, stanza=True, binding=False)
    line = session.message(root, plugin(tmp_path))
    assert line is not None
    assert "no docs/process/binding.md" in line
    assert "/ratchet:adopt" in line
    assert "/ratchet:init" not in line


def test_an_adopted_repository_gets_the_stance(tmp_path: Path) -> None:
    root = checkout(tmp_path, stanza=True, binding=True)
    text = session.message(root, plugin(tmp_path))
    assert text is not None
    assert text.startswith(f"Ratchet: {root} is on the method.")
    assert text.endswith(STANCE.strip())
    assert "## Working" not in text
    assert session.message(root / "src/deep", plugin(tmp_path)) == text


def test_claude_md_alone_counts_as_the_stanza(tmp_path: Path) -> None:
    root = checkout(tmp_path, stanza=True, binding=True, file="CLAUDE.md")
    text = session.message(root, plugin(tmp_path))
    assert text is not None
    assert "is on the method" in text


def test_a_missing_or_headingless_skill_leaves_only_the_one_line(tmp_path: Path) -> None:
    root = checkout(tmp_path, stanza=True, binding=True)
    assert session.stance(plugin(tmp_path, skill=None)) == ""
    assert session.stance(plugin(tmp_path, skill="# No stance here\n")) == ""
    text = session.message(root, plugin(tmp_path, skill=None))
    assert text == session.ADOPTED.format(root=root)


def test_the_real_skill_carries_a_stance() -> None:
    text = session.stance()
    assert text.startswith(session.STANCE_HEADING)
    assert "critical rationalist" in text
    assert "## Working" not in text


def test_render_is_plain_for_a_session_and_json_for_a_subagent() -> None:
    assert session.render("SessionStart", "hello") == "hello"
    assert session.render("", "hello") == "hello"
    assert json.loads(session.render("SubagentStart", "hello")) == {
        "hookSpecificOutput": {"hookEventName": "SubagentStart", "additionalContext": "hello"}
    }


def test_render_injects_a_system_message_for_antigravity() -> None:
    assert json.loads(session.render("SessionStart", "hello", antigravity=True)) == {
        "injectSteps": [{"systemMessage": {"systemMessage": "hello"}}]
    }


def shim(payload: dict[str, object], cwd: Path) -> str:
    result = subprocess.run(
        [sys.executable, str(PLUGIN_ROOT / "hooks/session.py")],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=True,
        cwd=cwd,
    )
    return result.stdout


def test_the_shim_answers_each_event_in_its_form(tmp_path: Path) -> None:
    bare = checkout(tmp_path, stanza=False, binding=False)
    nudge = shim({"hook_event_name": "SessionStart", "cwd": str(bare)}, tmp_path)
    assert nudge.startswith("Ratchet:")
    adopted = checkout(tmp_path / "other", stanza=True, binding=True)
    text = shim({"hook_event_name": "SessionStart", "cwd": str(adopted)}, tmp_path)
    assert "## The stance" in text
    output = json.loads(shim({"hook_event_name": "SubagentStart", "cwd": str(adopted)}, tmp_path))
    assert "## The stance" in output["hookSpecificOutput"]["additionalContext"]
    assert shim({"cwd": str(tmp_path / "loose")}, tmp_path) == ""


def test_the_shim_answers_antigravity_with_inject_steps(tmp_path: Path) -> None:
    adopted = checkout(tmp_path, stanza=True, binding=True)
    start = {"hookEventName": "SessionStart", "conversationId": "c", "cwd": str(adopted)}
    output = json.loads(shim(start, tmp_path))
    (step,) = output["injectSteps"]
    assert "## The stance" in step["systemMessage"]["systemMessage"]
    assert shim({**start, "cwd": str(tmp_path / "loose")}, tmp_path) == ""


def test_the_next_step_closes_every_message(tmp_path: Path) -> None:
    root = checkout(tmp_path, stanza=True, binding=False)
    plugin_root = plugin(tmp_path, route=True)
    calls: list[list[str]] = []

    def run(argv: list[str]) -> str | None:
        calls.append(argv)
        return STEP

    text = session.message(root, plugin_root, run)
    assert text is not None
    assert text.endswith(
        "Next, for the owner to type: /ratchet:adopt. Why: no binding. Then: grill."
    )
    assert calls[0][1:] == [
        str(plugin_root / session.ROUTE_SCRIPT),
        "--root",
        str(root),
        "--offline",
        "--json",
    ]
    adopted = session.message(checkout(tmp_path / "b", stanza=True, binding=True), plugin_root, run)
    assert adopted is not None
    assert "## The stance" in adopted
    assert adopted.endswith("Then: grill.")


def test_a_route_that_cannot_answer_adds_nothing(tmp_path: Path) -> None:
    root = checkout(tmp_path, stanza=True, binding=True)
    plugin_root = plugin(tmp_path, route=True)
    for output in (None, "not json", "[]", '{"command": 1}'):
        assert session.next_step(root, plugin_root, lambda _a, o=output: o) == ""
    assert session.next_step(root, plugin(tmp_path / "noroute"), silent) == ""
    assert session.message(root, plugin_root, silent) == session.message(root, plugin(tmp_path))


def test_run_route_survives_a_missing_or_failing_interpreter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Why: the hook's own limit is five seconds; starting an interpreter on a loaded machine can
    # take longer, and this case is about the output, not about the limit.
    monkeypatch.setattr(session, "ROUTE_TIMEOUT", 120)
    assert session.run_route(["/nonexistent/python"]) is None
    assert session.run_route([sys.executable, "-c", "raise SystemExit(1)"]) is None
    assert session.run_route([sys.executable, "-c", "print('ok')"]) == "ok\n"


def test_run_route_gives_up_on_a_route_that_outlasts_the_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(session, "ROUTE_TIMEOUT", 0.2)
    assert session.run_route([sys.executable, "-c", "import time; time.sleep(30)"]) is None


def test_the_shipped_stance_links_nothing_relative() -> None:
    """The stance is read in the user's repository, where a link relative to the skill is dead."""
    text = session.stance(PLUGIN_ROOT)
    assert text.startswith(session.STANCE_HEADING)
    assert re.findall(r"\]\((?!https?://)[^)]*\)", text) == []
