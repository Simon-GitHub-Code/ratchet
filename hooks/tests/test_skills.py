"""The skills' text: what the owner-free run promises, pinned so an edit cannot drift it."""

import re
from pathlib import Path

SKILLS = Path(__file__).resolve().parents[2] / "skills"
INIT = (SKILLS / "init/SKILL.md").read_text(encoding="utf-8")


def test_init_asks_two_dialogs_by_default_and_everything_under_interactive() -> None:
    # Why: the brief's target is two owner dialogs on an empty directory; a live run proves
    # it, and this pins the text that run follows so a later edit cannot add a third quietly.
    dialogs = re.findall(r"\*\*Dialog (\d), ([^.]+)\.", INIT)
    assert dialogs == [("1", "always: what this is"), ("2", "greenfield only: the stack")]
    assert "Dialog 3" not in INIT
    assert "`/ratchet:init interactive`" in INIT
    assert "defaults table" in INIT


def test_init_passes_the_owners_answers_to_the_setups() -> None:
    assert "the argument `init` followed by the owner's answers" in INIT
    assert "budget answered as `medium`" in INIT
    assert "Sections A, B and C answered from the defaults table" in INIT


def test_the_remote_needs_an_accepting_answer() -> None:
    assert "never from the tracker choice alone" in INIT
    assert "--private --source . --remote origin --push" in INIT
