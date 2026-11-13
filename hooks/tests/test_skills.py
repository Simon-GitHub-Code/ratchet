"""The skills' text: what the owner-free run promises, pinned so an edit cannot drift it."""

import re
from pathlib import Path

import commits
import feature_preflight as preflight

SKILLS = Path(__file__).resolve().parents[2] / "skills"
INIT = (SKILLS / "init/SKILL.md").read_text(encoding="utf-8")
ADOPT = (SKILLS / "adopt/SKILL.md").read_text(encoding="utf-8")


def test_adopt_asks_its_product_questions_first_and_chooses_tools_itself() -> None:
    # Why: ADRs 0017 and 0034. The owner is asked about the product at step 3, in the first
    # minutes, and the tools are the agent's; a tool the plugin ships is a regression.
    step_3 = ADOPT.split("3. **Recognise the repository.**", 1)[1].split("\n4. ", 1)[0]
    assert "nothing after this step asks the owner anything" in step_3
    assert "**Assume** `adopt.tools`" in ADOPT
    assert "interactive" not in ADOPT
    assert not (SKILLS / "adopt/presets").exists()
    assert "preset" not in ADOPT
    assert 'scripts/application_types.py" <type>...' in ADOPT
    assert "never from this plugin" in ADOPT
    assert "**A gate with nothing to run is red.**" in ADOPT
    # Why: ADR 0020. The plugin ships no tool, so the adopting agent writes a fault per gate.
    assert "**A bite for every gate you bind.**" in ADOPT
    assert "the `bite-all` entry point plants each fault of `quality/bites.toml`" in ADOPT


def test_adopt_binds_the_floor_and_what_has_arrived_and_builds_no_skeleton() -> None:
    # Why: ADR 0028. A hobby project on its first day owes the floor alone; a skeleton built for
    # the gates stood between the owner and the idea they sat down to write.
    assert "walking skeleton" not in ADOPT
    assert 'scripts/triggers.py" --count' in ADOPT
    assert 'scripts/triggers.py" --json' in ADOPT
    assert '`@echo "later: <trigger>"`' in ADOPT
    assert "no life event on a tree with no sign of one" in ADOPT


def test_adopt_provisions_what_the_first_slice_needs_and_rehearses_it() -> None:
    # Why: ADR 0032. A dogfood's first unattended feature blocked on guarded-file edits adoption
    # could have made: no test runner, a config left to "the change that brings" something, a
    # `boot` of `none`, no change-size ceiling. Adoption makes them and proves the slice runs.
    assert "**The tests are provisioned, not asked.**" in ADOPT
    assert "**Rehearse the first slice.**" in ADOPT
    assert "**A key a declared type needs is never removed to quiet a gate.**" in ADOPT
    assert 'no rule leaves a config to "the change that brings" something' in ADOPT
    assert "`just start` and the address it prints" in ADOPT
    assert "python3 -m ratchet_guards.diff --root . --base HEAD" in ADOPT
    assert 'Set the four to "none"' not in ADOPT
    assert "Slice one is the one step 11's design brief names. When the brief names none" in ADOPT
    assert "Rehearse at most twice" in ADOPT
    assert "for a `cli`, `job` or `app` the run step 9's table gives its `smoke`" in ADOPT
    assert "rehearse again" not in ADOPT, "the rehearsal is bounded"


def test_adopt_leaves_the_permission_rules_to_step_8() -> None:
    # Why: issue 20. Claude Code applies .claude/settings.json the moment it exists, so a copy at
    # step 2 locked guards/** before step 8 filled the manifest. The file is written last.
    step_2 = ADOPT.split("2. **Copy the templates.**", 1)[1].split("\n3. ", 1)[0]
    step_8 = ADOPT.split("8. **Fill the manifest", 1)[1].split("\n9. ", 1)[0]
    assert "skipping `.claude/settings.json`" in step_2
    assert "creates `.claude/settings.json`" in step_8
    assert "path rules from the manifest on the default branch" in step_8
    assert "permission rules come from the work tree's" in step_8


def test_the_feature_skill_shortens_a_long_subject_by_a_fixed_rule() -> None:
    # Why: issue 27. A subject built from a long slug passed 72 characters, which tier 0 refuses,
    # and each orchestrator then invented its own shorter one.
    feature = (SKILLS / "feature/SKILL.md").read_text(encoding="utf-8")
    assert "drop whole words from the end of the slug until it fits" in feature
    assert "at most 72 characters" in feature
    assert commits.MAX_SUBJECT == 72


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


def test_init_asks_about_the_product_one_question_at_a_time() -> None:
    # Why: ADR 0034. The owner is asked four product questions, and a fifth only before a
    # remote would be made; the registry test holds their words, this holds their order.
    asked = re.findall(r"^\d\. \*\*Ask\*\* `([^`]+)`", INIT, re.MULTILINE)
    assert asked == ["init.purpose", "init.user", "init.difference", "init.place", "init.online"]
    assert "interactive" not in INIT


def test_init_passes_the_owners_answers_to_the_setups() -> None:
    assert "the argument `init` followed by the owner's answers" in INIT
    assert "budget answered as `medium`" in INIT
    assert "the owner types `/mattpocock-skills:setup-matt-pocock-skills` after init" in INIT
    assert "follow it by hand" not in INIT


def test_every_ratchet_skill_ends_with_the_route() -> None:
    # Why: the owner's next command is the method's promise at every stage (ADR 0009); a
    # hand-over that forgets it leaves the owner guessing which plugin's keystroke comes next.
    for name in ("init", "adopt", "feature", "safeguard"):
        text = (SKILLS / name / "SKILL.md").read_text(encoding="utf-8")
        assert 'scripts/route.py" --root .' in text, name


def test_the_safeguard_skill_asks_nothing_and_commits_as_the_gate_accepts() -> None:
    # Why: ADRs 0028 and 0034 and the owner's principle: binding what is owed is not a question.
    safeguard = (SKILLS / "safeguard/SKILL.md").read_text(encoding="utf-8")
    assert "**Assume** `safeguard.tools`" in safeguard
    assert "**Ask**" not in safeguard
    assert "`just bite-one <gate>`" in safeguard
    assert commits.SUBJECT.match("chore(method): safeguard outbound calls")
    assert "`chore(method): safeguard <what>`" in safeguard


def test_the_safeguard_skill_leaves_the_manifest_and_settings_to_the_owner() -> None:
    # Why: both are in the manifest's `deny` group, so an agent that tried to write them is
    # refused after adoption; the hand-over says what the owner adds and runs.
    safeguard = (SKILLS / "safeguard/SKILL.md").read_text(encoding="utf-8")
    assert "denied to an agent after adoption" in safeguard
    assert "print the exact lines for the owner to add" in safeguard
    assert "`just settings`" in safeguard
    assert "`snapshots-check` holds the tool's effective configuration" in safeguard
    guards = (SKILLS / "method/guards.md").read_text(encoding="utf-8")
    assert "the owner does both" in guards


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
