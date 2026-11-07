"""The session hook: the stance on an adopted checkout, a nudge on a bare one, silence elsewhere."""

import json
import subprocess
import sys
from pathlib import Path

from ratchet_guards import session
from tests.support import PLUGIN_ROOT

STANZA = "This repository follows the method: load the `ratchet:method` skill first.\n"
STANCE = "## The stance\n\nWork as a critical rationalist.\n\n- One.\n- Two.\n"
SKILL = "---\nname: method\n---\n\n# The method\n\nIntro.\n\n" + STANCE + "\n## Working\n\nLater.\n"


def checkout(tmp_path: Path, *, stanza: bool, binding: bool, file: str = "AGENTS.md") -> Path:
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    if stanza:
        (root / file).write_text(STANZA, encoding="utf-8")
    if binding:
        (root / session.BINDING).parent.mkdir(parents=True)
        (root / session.BINDING).write_text("# Binding\n", encoding="utf-8")
    return root


def plugin(tmp_path: Path, *, skill: str | None = SKILL) -> Path:
    root = tmp_path / "plugin"
    (root / session.METHOD_SKILL).parent.mkdir(parents=True, exist_ok=True)
    if skill is not None:
        (root / session.METHOD_SKILL).write_text(skill, encoding="utf-8")
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
