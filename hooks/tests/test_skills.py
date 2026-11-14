"""The skills' text: what the owner-free run promises, pinned so an edit cannot drift it."""

import re
from pathlib import Path

import pytest

import commits
import feature_preflight as preflight
import snapshots
from tests.repos import commit, repository, write

SKILLS = Path(__file__).resolve().parents[2] / "skills"
INIT = (SKILLS / "init/SKILL.md").read_text(encoding="utf-8")
ADOPT = (SKILLS / "adopt/SKILL.md").read_text(encoding="utf-8")


def test_adopt_asks_its_product_questions_first_and_chooses_tools_itself() -> None:
    # Why: ADRs 0017 and 0034. The owner is asked about the product at step 3, in the first
    # minutes, and the tools are the agent's; a tool the plugin ships is a regression.
    step_3 = ADOPT.split("3. **Recognise the repository.**", 1)[1].split("\n4. ", 1)[0]
    assert "nothing after this step asks the owner anything" in step_3
    # Why: ADR 0049. The architecture and its tools are asked in the same round, not assumed.
    assert "**Assume** `adopt.tools`" not in ADOPT
    assert "**Ask** `architecture.system`" in ADOPT
    assert "**Ask** `architecture.tools`" in ADOPT
    assert "**Assume** `init.stack`" not in INIT
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
    assert "On an empty repository none of the three is asked." in ADOPT


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
    section = page.split("\n### The Spec role's copy\n", 1)[1].split("\n## ", 1)[0]
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


def test_the_conjecture_says_what_each_thing_the_owner_said_became() -> None:
    # Why: ADR 0034. The owner cannot read code, so the conjecture is where he sees that
    # nothing he said was dropped and nothing was invented; adoption keeps his words for it.
    conjecture = (SKILLS / "conjecture/SKILL.md").read_text(encoding="utf-8")
    assert "*What you asked for*" in conjecture
    for became in ("**built**", "**assumed**", "**left out**", "**added**"):
        assert became in conjecture, became
    assert "`## In the owner's words` in `docs/design-brief.md`" in conjecture
    brief = (SKILLS / "adopt/templates/docs/design-brief.md").read_text(encoding="utf-8")
    assert "## In the owner's words" in brief
    assert "under `## In the owner's words` each question the owner answered here" in ADOPT


def test_init_asks_about_the_product_in_one_message() -> None:
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
    for name in ("init", "adopt", "feature", "safeguard", "architecture"):
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


def test_the_architecture_skill_asks_once_and_only_when_the_architecture_changes() -> None:
    # Why: ADR 0052. A decision that holds is an assumption; a change is the owner's, asked once.
    text = (SKILLS / "architecture/SKILL.md").read_text(encoding="utf-8")
    review = text.split("\n## Review the architecture\n", 1)[1]
    assert "**Assume** `architecture.holds`" in review
    assert review.count("**Ask**") == 1
    assert "**Ask** `architecture.change`" in review
    assert 'scripts/triggers.py" --root . --reviews' in review
    for line in ("- Architecture review:", "- Outcome:", "- Decided: YYYY-MM-DD"):
        assert f"`{line}" in review, line
    assert commits.SUBJECT.match("docs(adr): architecture review for stored data")
    assert "`docs(adr): architecture review for <what>`" in review


def test_the_safeguard_skill_guards_a_new_config_on_its_own_branch() -> None:
    # Why: the hook and the merge judge read the manifest as the default branch holds it, so the
    # agent adds the path on its branch and the owner merges it, and the settings are a fixed list
    # no tool's config changes (ADR 0033).
    safeguard = (SKILLS / "safeguard/SKILL.md").read_text(encoding="utf-8")
    assert "Add the tool's config path to the `tool-config` paths" in safeguard
    assert "`.claude/settings.json` needs no change" in safeguard
    assert "just settings" not in safeguard
    guards = (SKILLS / "method/guards.md").read_text(encoding="utf-8")
    assert "the agent adds it to `guards/manifest.toml` on its branch" in guards


UNLISTED = '[[group]]\nname = "tool-config"\nreason = "Rule sets."\npaths = ["lint.toml"]\n'


def test_a_config_the_manifest_does_not_list_is_said_to_be_unguarded(tmp_path: Path) -> None:
    # Why: safeguard's hand-over told the owner that until he listed a new tool's config, the
    # snapshot check held it, so loosening it still showed. The check copies only the files the
    # `tool-config` group lists: a loosened unlisted config passes it, as the verifier showed.
    root = repository(
        tmp_path,
        {"guards/manifest.toml": UNLISTED, "lint.toml": "a = 1\n", ".semgrep.yml": "rules: [x]\n"},
    )
    assert snapshots.write(root, "tool-config") == []
    write(root, ".semgrep.yml", "rules: []\n")
    commit(root, "chore: loosen the unlisted config")
    assert snapshots.check(root, "tool-config") == [], "an unlisted config is not checked"
    safeguard = flat("safeguard/SKILL.md")
    hand_over = safeguard.split("6. **Hand over.**", 1)[1]
    assert "holds the tool's effective configuration" not in safeguard
    # Since ADR 0033 the agent lists the config itself on its branch, so the hand-over names the
    # path it listed instead of asking the owner to list it.
    assert "name the path you added to the manifest" in hand_over
    for page in ("method/SKILL.md", "method/ratchet.md"):
        text = flat(page)
        assert "a config the group does not list is neither guarded nor recorded" in text, page
        assert "effective configuration is snapshotted" not in text.lower(), page
        assert "as the tool resolves it" not in text, page
    readme = " ".join((SKILLS.parent / "README.md").read_text(encoding="utf-8").split())
    assert "each gate's effective configuration is snapshotted" not in readme


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


RULE_PARTS = (
    (
        "what becomes of something of the user's (kept, deleted, merged, duplicated, overwritten,"
        " shared), who may see or do it, or what the user can do at all"
    ),
    "nothing the owner said or answered settles it",
    (
        "A choice an agent wrote down earlier (a spec, a conjecture, an **Assumed** line) does not"
        " settle it"
    ),
    (
        "anything a later change reverses without loss to the user: order, wording, layout, timing,"
        " limits, and, within the architecture the owner chose, tools and how it is built"
    ),
    # Why: ADR 0049. The architecture and its tools are the one choice of how it is built that
    # reaches the owner.
    "/ratchet:architecture",
)
PLACES = ["init/templates/AGENTS.md", "method/SKILL.md", "method/records.md", "method/pipeline.md"]


@pytest.mark.parametrize("page", PLACES)
def test_the_rule_on_asking_has_one_meaning_in_each_place_an_agent_reads_it(page: str) -> None:
    # Why: ADR 0034. "What this should do" covered every behaviour, so a rerun's spec decided what
    # a re-pasted link becomes, and its conjecture called that settled because a spec said so.
    text = " ".join((SKILLS / page).read_text(encoding="utf-8").split())
    for part in RULE_PARTS:
        assert part in text, (page, part)


def test_the_conjecture_asks_every_open_fork_and_cites_what_it_built() -> None:
    # Why: the rerun's table marked two rows "(left open)" as assumed and said no question was
    # needed; two "built" rows named no criterion.
    conjecture = " ".join((SKILLS / "conjecture/SKILL.md").read_text(encoding="utf-8").split())
    assert "one thing at most" not in conjecture
    assert "Ask every such fork before you write anything, all in one message" in conjecture
    assert "Never write that no question was needed while a row says `(left open)`" in conjecture
    assert "a **built** row with no criterion or story to name is not built" in conjecture
    assert "A user story an agent wrote is not the owner's words unless" in conjecture
    assert "correct the criterion to follow their words" in conjecture


def table_problems(table: str) -> list[str]:
    """Each row of a *What you asked for* table that slips a choice past the owner."""
    problems: list[str] = []
    for line in table.splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) != 3 or cells[0] in ("You said", "---"):
            continue
        said, became = cells[0], re.match(r"\*\*([a-z ]+)\*\*", cells[1])
        kind = became.group(1) if became else ""
        if not said.startswith(('"', "(not asked)", "(left open)")):
            problems.append(f"{said}: *You said* quotes the owner or says `(not asked)`")
        elif said.startswith("(left open)"):
            problems.append(f"{said}: a fork left open is asked, never written as {kind}")
        elif said.startswith("(not asked)") and kind not in ("assumed", "added"):
            problems.append(f"{said}: nothing the owner said can be {kind}")
        elif kind == "added" and not said.startswith("(not asked)"):
            problems.append(f"{said}: an addition is something the owner never mentioned")
    return problems


def test_the_conjecture_table_never_slips_a_product_choice_in_as_added() -> None:
    # Why: in three of three sampled runs the table wrote "(left open)" under the owner's column
    # and **added** under the outcome; one made a product choice (Undo after re-pasting the same
    # address is refused) without the owner. The skill's own example must hold the rule.
    verifier = (
        "| (left open) a double click | **added**: changes nothing | stories 14, 20 |\n"
        "| (left open) Undo after re-pasting the same address | **added**: refused | Notes |\n"
    )
    assert len(table_problems(verifier)) == 2
    conjecture = (SKILLS / "conjecture/SKILL.md").read_text(encoding="utf-8")
    unindented = re.sub(r"^ +", "", conjecture, flags=re.MULTILINE)
    example = unindented.split("```markdown\n| You said | Became | Where |\n", 1)[1]
    example = example.split("```", 1)[0]
    assert table_problems(example) == []
    kinds = set(re.findall(r"\| \*\*([a-z ]+)\*\*", example))
    assert kinds == {"built", "assumed", "added", "left out"}
    assert "(not asked)" in example
    text = " ".join(conjecture.split())
    assert "A `(left open)` row is never marked **added** or **assumed**" in text
    assert "the table you write holds none" in text
    assert "An addition that changes what the user sees, keeps or can do is not **added**" in text


GIVES_YOU = re.compile(r"\bthe test gives (?:to )?you\b")
ANSWERED = re.compile(r"\banswer(?:ed)?\b.*\*You said\*|\*You said\*.*\banswer(?:ed)?\b")


def fork_rule_problems(step: str) -> list[str]:
    """Each way a conjecture step's own rule sentences let a fork past the owner.

    The step must say how a fork the test gives the agent is written and how an answered fork's
    row is written, and say it the right way: ``(not asked)`` and **assumed**, and the answer
    quoted and **built**.
    """
    problems: list[str] = []
    rules = re.split(r"(?<=[.?!])\s+", step)
    yours = [rule for rule in rules if GIVES_YOU.search(rule)]
    answered = [rule for rule in rules if ANSWERED.search(rule)]
    if not yours:
        problems.append("no sentence says how a fork the test gives you is written")
    if not answered:
        problems.append("no sentence says how an answered fork's row is written")
    problems += [
        f"a fork the test gives you is `(not asked)` and **assumed**: {rule}"
        for rule in yours
        if "`(not asked)`" not in rule or "**assumed**" not in rule or "(left open)" in rule
    ]
    problems += [
        f"an answered fork quotes the answer and is **built**: {rule}"
        for rule in answered
        if "**built**" not in rule or re.search(r"\*\*(?:added|assumed)\*\*", rule)
    ]
    return problems


def test_the_conjecture_steps_say_how_each_fork_is_written() -> None:
    # Why: the verifier wrote that a fork the test gives the agent is `(left open)` and **added**,
    # and that an answered fork's row is **added**, and every test stayed green: only the example
    # table was held to the rule, not the sentences that state it.
    page = (SKILLS / "conjecture/SKILL.md").read_text(encoding="utf-8")
    text = re.sub(r"^```.*?^```", "", page, flags=re.MULTILINE | re.DOTALL)
    conjecture = " ".join(text.split())
    step_2 = conjecture.split("2. **Draft from the record", 1)[1].split(" 3. **", 1)[0]
    step_3 = conjecture.split("3. **One conjecture", 1)[1].split(" 4. **", 1)[0]
    assert fork_rule_problems(step_2) == []
    assert fork_rule_problems(step_3) == []
    assert "A `(left open)` row is never marked **added** or **assumed**" in step_2
    assert "The third row was a fork" in step_2
    planted = step_3.replace("is written `(not asked)` and **assumed**", "is written `(left open)`")
    assert len(fork_rule_problems(planted)) == 1
    planted = step_3.replace("with the answer quoted under *You said*, marked **built**", "")
    assert fork_rule_problems(planted) == ["no sentence says how an answered fork's row is written"]


def test_adopt_asks_a_repository_with_code_whether_others_reach_it() -> None:
    # Why: ADR 0028. The route's sign patterns miss a deploy job in release.yml and a production
    # address in .env.example, so a deployed project with neither was asked nothing.
    asked = ADOPT.split("## What the owner is asked", 1)[1].split("\n## ", 1)[0]
    flat = " ".join(asked.split())
    assert "On a repository that already has code, always ask the first" in flat
    assert 'recommend "no" only when the tree shows none' in flat
    assert "On an empty repository none of the three is asked." in flat
    assert flat.index("always ask the first") < flat.index("**Ask** `event.deployed`")


def test_init_and_adopt_take_the_owners_preferences_before_any_default() -> None:
    # Why: ADR 0034 decision 7. With no interactive mode, a careful owner names a tool up front.
    for name, text in (("init", INIT), ("adopt", ADOPT)):
        assert "**What the owner asked for wins.**" in text, name
        assert 'as "you asked for"' in text, name


def flat(page: str) -> str:
    return " ".join((SKILLS / page).read_text(encoding="utf-8").split())


PREFERENCE_RULE = "## A preference typed after a command"
PREFERENCE_LINK = "(../method/records.md#a-preference-typed-after-a-command)"


def test_a_typed_preference_chooses_among_allowed_options_and_never_loosens_a_safeguard() -> None:
    # Why: a preference "wins over the defaults", and nothing said a preference could not turn a
    # floor row to none or deny a life event; in the verifier's run only the agent's own judgment
    # refused. The rule is written once, and every skill that takes a preference points at it.
    records = flat("method/records.md")
    assert PREFERENCE_RULE in records
    rule = records.split(PREFERENCE_RULE, 1)[1].split(" ## ", 1)[0]
    for part in (
        "A preference chooses among the options a row allows",
        (
            "never turns a floor row to `none`, lowers a threshold, skips a safeguard, or declares"
            " or denies a life event"
        ),
        "answered in plain words with the route that exists",
        "listed under **Assumed** as not applied",
    ):
        assert part in rule, part
    takers = sorted(
        page.parent.name
        for page in SKILLS.glob("*/SKILL.md")
        if "is a preference" in page.read_text(encoding="utf-8")
    )
    assert takers == ["adopt", "init"]
    for name in takers:
        text = flat(f"{name}/SKILL.md")
        assert PREFERENCE_LINK in text, name
        assert "never loosens a safeguard" in text, name
    adr_page = next((SKILLS.parent / "docs/adr").glob("0034-*.md"))
    adr = " ".join(adr_page.read_text(encoding="utf-8").split())
    decision = adr.split("7. **Preferences come first, in words.**", 1)[1].split(" 8. ", 1)[0]
    assert "chooses among the options a row allows" in decision
    assert "declares or denies a life event" in decision


KIND_OF_SOFTWARE = (
    "The kind of software is what the tree shows, not an option: a preference picks it only where"
    " the tree leaves it open, and one that contradicts the tree is listed as not applied, with"
    " what the tree shows"
)


def test_a_preference_never_declares_a_kind_of_software_the_tree_contradicts() -> None:
    # Why: the records listed the kind of software among the options a preference chooses, and
    # nothing checks the declared types against the tree, so a preference that called a server a
    # command-line tool could drop every service row with no decision record.
    rule = flat("method/records.md").split(PREFERENCE_RULE, 1)[1].split(" ## ", 1)[0]
    options = rule.split("A preference chooses among the options a row allows:", 1)[1]
    assert "kind of software" not in options.split(".", 1)[0]
    assert KIND_OF_SOFTWARE in rule
    adr_page = next((SKILLS.parent / "docs/adr").glob("0034-*.md"))
    adr = " ".join(adr_page.read_text(encoding="utf-8").split())
    decision = adr.split("7. **Preferences come first, in words.**", 1)[1].split(" 8. ", 1)[0]
    assert KIND_OF_SOFTWARE in decision
    residue = adr.split("Known residue.", 1)[1].split(" ## ", 1)[0]
    assert "No script checks the declared application types against the tree" in residue


PREFERENCE_LIMITS = [
    ("method/records.md", "a ceiling raised is a threshold lowered"),
    ("method/records.md", "with the route that exists, and the default stands"),
    ("method/records.md", "Nothing a preference says skips that."),
    ("method/records.md", "An event once declared stays."),
    ("adopt/SKILL.md", "It chooses among the options a row allows and never loosens a safeguard"),
    ("init/SKILL.md", "It chooses among the options a choice allows and never loosens a safeguard"),
    ("adopt/SKILL.md", "except a life event's: the owner's answer to that question is the only"),
    ("adopt/SKILL.md", 'as "you asked for", or as not applied with the route that exists'),
    ("init/SKILL.md", 'as "you asked for", or as not applied with the route that exists'),
    ("adopt/SKILL.md", "says: read it before you act on one"),
    ("init/SKILL.md", "says: read it before you act on one"),
]


@pytest.mark.parametrize(("page", "part"), PREFERENCE_LIMITS)
def test_each_limit_on_a_preference_is_stated_where_it_is_taken(page: str, part: str) -> None:
    # Why: the verifier deleted each of these and every test stayed green. Each is what keeps a
    # typed preference from loosening a bar or declaring a life event without the owner's answer.
    text = flat(page)
    if page == "method/records.md":
        text = text.split(PREFERENCE_RULE, 1)[1].split(" ## ", 1)[0]
    else:
        text = text.split("**What the owner asked for wins.**", 1)[1]
        text = text.split("**Everything else is assumed.**", 1)[0]
    assert part in text


def test_a_preference_not_applied_is_named_in_the_hand_over() -> None:
    # Why: the preference paragraphs list a refused preference under **Assumed** as not applied,
    # but the hand-over steps that build that block named only "you asked for", so the owner
    # never saw that what he typed was not done.
    hand_overs = {
        "adopt": flat("adopt/SKILL.md").split("13. **Hand over.**", 1)[1],
        "init": flat("init/SKILL.md").split("9. **Hand over.**", 1)[1].split(" ## ", 1)[0],
    }
    for name, step in hand_overs.items():
        assert "as not applied, with what was asked for, why not and the route that exists" in (
            step
        ), name


def test_an_overturned_assumption_has_a_route_and_a_stated_cost() -> None:
    # Why: an overturn rerun told the owner to edit the binding by hand to declare a life event.
    records = " ".join((SKILLS / "method/records.md").read_text(encoding="utf-8").split())
    procedure = records.split("## Overturning an assumption", 1)[1].split(" ## ", 1)[0]
    assert "the owner types `/ratchet:safeguard <event>`" in procedure
    assert "Never tell the owner to edit the binding by hand" in procedure
    assert "`/ratchet:conjecture` on it again" in procedure
    for page in ("init/templates/AGENTS.md", "method/SKILL.md", "method/records.md"):
        assert "what changing it later costs" in (SKILLS / page).read_text(encoding="utf-8"), page


COST_PARTS = "(what changes, roughly how much work, and whether anything built is redone)"
SWITCHES_A_TOOL = ("lock file", "the merge is yours")


def example_costs(page: str) -> list[str]:
    """Each example **Assumed** line a page gives, in double backticks after "such as"."""
    return re.findall(r"such as ``(.+?)``", flat(page))


def test_every_cost_line_the_owner_reads_says_what_is_redone() -> None:
    # Why: three example cost lines understated or were false. "Free until the first feature is
    # built" is false once adoption has bound tools to the stack, and "two protected files you
    # approve" left out the package list, its lock file, the tool's settings, the list of checks,
    # the planted mistakes, the saved copies, and the owner's own edit and `just settings`.
    for page in ("init/templates/AGENTS.md", "method/SKILL.md", "method/records.md"):
        assert COST_PARTS in flat(page), page
    examples = {page: example_costs(f"{page}/SKILL.md") for page in ("init", "adopt", "safeguard")}
    for page, spans in examples.items():
        assert spans, page
        for span in spans:
            assert "free until" not in span, (page, span)
            assert re.search(r"\bbuilt\b", span), (page, span)
    for page in ("adopt", "safeguard"):
        for part in SWITCHES_A_TOOL:
            assert any(part in span for span in examples[page]), (page, part)
    # Why: ADR 0049. Init no longer assumes the stack; adoption puts it to the owner.
    assert any("adoption then offers ways to build it" in span for span in examples["init"])
    records = flat("method/records.md")
    route = records.split("## Overturning an assumption", 1)[1].split(" ## ", 1)[0]
    assert "its lockfile" in route
    assert "the old tool's config removed" in route
    assert "nothing yet binds the gates again for another stack" in route


def test_the_cost_check_names_the_stack_and_the_tool_route_names_what_is_redone() -> None:
    # Why: the verifier deleted the sentence that says what a stack changed after adoption costs,
    # and the tool route's "Nothing built is redone", and every test stayed green. An agent
    # checks a cost line against these before the owner reads it. Since ADR 0049 the change is
    # recorded by `/ratchet:architecture`, and each tool is still replaced on its own.
    records = flat("method/records.md")
    assumed = records.split("Assume, and list under **Assumed**", 1)[1]
    assumed = assumed.split(" The pipeline's", 1)[0]
    assert (
        "a stack changed after adoption, which `/ratchet:architecture` records and whose tools are"
        " then replaced one change per tool"
    ) in assumed
    route = records.split("## Overturning an assumption", 1)[1].split(" ## ", 1)[0]
    tool = route.split("- *A tool*:", 1)[1].split(" - *", 1)[0]
    assert tool.endswith("Its merge is the owner's. Nothing built is redone.")


def test_adopt_hands_over_its_assumptions_without_question_keys() -> None:
    # Why: the rerun's hand-over printed `adopt.types` and its siblings to the owner.
    step_13 = ADOPT.split("13. **Hand over.**", 1)[1]
    assert re.search(r"`(?:adopt|init|event|safeguard)\.[a-z-]+`", step_13) is None
    assert "no question key, type name or other method term in it" in step_13
