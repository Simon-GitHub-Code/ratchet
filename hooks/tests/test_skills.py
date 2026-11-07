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
    assert "the owner types `/mattpocock-skills:setup-matt-pocock-skills` after init" in INIT
    assert "follow it by hand" not in INIT


def test_every_ratchet_skill_ends_with_the_route() -> None:
    # Why: the owner's next command is the method's promise at every stage (ADR 0009); a
    # hand-over that forgets it leaves the owner guessing which plugin's keystroke comes next.
    for name in ("init", "adopt", "feature"):
        text = (SKILLS / name / "SKILL.md").read_text(encoding="utf-8")
        assert 'scripts/route.py" --root .' in text, name


def test_no_method_page_tells_an_agent_to_load_a_user_invoked_skill() -> None:
    # Why: the harness refuses those skills to an agent; a page that says "load pstack:interrogate"
    # sends the agent into a refusal or an imitation. Only the owner's slash form may appear.
    owners = (
        "interrogate",
        "architect",
        "benchmark-checklist",
        "poteto-mode",
        "to-spec",
        "to-tickets",
        "triage",
        "wayfinder",
        "grill-with-docs",
        "setup-matt-pocock-skills",
        "correct",
        "reflect",
    )
    for page in (SKILLS / "method").glob("*.md"):
        text = page.read_text(encoding="utf-8")
        for skill in owners:
            for prefix in ("pstack:", "mattpocock-skills:"):
                bad = f"`{prefix}{skill}`"
                assert bad not in text, f"{page.name} names {bad}; write /{prefix}{skill}"


def test_the_remote_needs_an_accepting_answer() -> None:
    assert "never from the tracker choice alone" in INIT
    assert "--private --source . --remote origin --push" in INIT
