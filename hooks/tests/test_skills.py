"""The skills' text: what the owner-free run promises, pinned so an edit cannot drift it."""

import re
from pathlib import Path

import feature_preflight as preflight

SKILLS = Path(__file__).resolve().parents[2] / "skills"
INIT = (SKILLS / "init/SKILL.md").read_text(encoding="utf-8")
ADOPT = (SKILLS / "adopt/SKILL.md").read_text(encoding="utf-8")


def test_adopt_asks_two_dialogs_and_chooses_tools_itself() -> None:
    # Why: ADR 0017. The owner is asked what the software is and whether to accept the tools,
    # then about the brief; a third dialog, or a tool the plugin ships, is a regression.
    dialogs = re.findall(r"\*\*Dialog (\d), ([^.]+)\.", ADOPT)
    assert dialogs == [("1", "what this is and what judges it"), ("2", "the design brief")]
    assert "Dialog 3" not in ADOPT
    assert "`/ratchet:adopt interactive`" in ADOPT
    assert not (SKILLS / "adopt/presets").exists()
    assert "preset" not in ADOPT
    assert 'scripts/application_types.py" <type>...' in ADOPT
    assert "never from this plugin" in ADOPT
    assert "**A gate with nothing to run is red.**" in ADOPT
    # Why: ADR 0020. The plugin ships no tool, so the adopting agent writes a fault per gate.
    assert "**A bite for every gate you bind.**" in ADOPT
    assert "the `bite-all` entry point plants each fault of `quality/bites.toml`" in ADOPT


def test_adopt_leaves_the_permission_rules_to_step_8() -> None:
    # Why: issue 20. Claude Code applies .claude/settings.json the moment it exists, so a copy at
    # step 2 locked guards/** before step 8 filled the manifest. The file is written last.
    step_2 = ADOPT.split("2. **Copy the templates.**", 1)[1].split("\n3. ", 1)[0]
    step_8 = ADOPT.split("8. **Fill the manifest", 1)[1].split("\n9. ", 1)[0]
    assert "skipping `.claude/settings.json`" in step_2
    assert "creates `.claude/settings.json`" in step_8
    assert "path rules from the manifest on the default branch" in step_8
    assert "permission rules come from the work tree's" in step_8


def test_the_fork_test_has_one_wording_the_spec_role_and_the_conjecture_skill_share() -> None:
    # Why: the conjecture skill asked the owner what the pipeline's Spec role would have assumed,
    # because only the role's prompt held the examples. A prompt cannot follow a link, so the
    # method page is the source and the prompt's copy is held to it here.
    page = (SKILLS / "method/pipeline.md").read_text(encoding="utf-8")
    section = page.split("\n## The fork test\n", 1)[1].split("\n## ", 1)[0]
    shared = [line.removeprefix("- ") for line in section.splitlines() if line.startswith("- ")]
    assert len(shared) == 6
    shared += re.findall(r"^The test: .*the owner chooses\.", section, re.MULTILINE)
    assert len(shared) == 7
    workflow = SKILLS.parent / "workflows/pipeline.js"
    prompt = " ".join(workflow.read_text(encoding="utf-8").split())
    for text in shared:
        assert text in prompt, text
    conjecture = (SKILLS / "conjecture/SKILL.md").read_text(encoding="utf-8")
    assert "[the fork test](../method/pipeline.md#the-fork-test)" in conjecture
    assert "what they can do" not in conjecture, "the skill cites the test and does not restate it"


def test_the_conjecture_skill_writes_the_block_the_preflight_asks() -> None:
    conjecture = (SKILLS / "conjecture/SKILL.md").read_text(encoding="utf-8")
    for name, _, says in preflight.OPERABILITY_LINES:
        assert f"`- {name}:` {says}" in conjecture, name


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
