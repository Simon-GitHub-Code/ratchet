import json
import os
import re
import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest

import commits
import companions
import conformance
import feature_preflight as preflight
import snapshots
from tests.repos import commit, install_hooks, repository, write

SKILLS = Path(__file__).resolve().parents[2] / "skills"
INIT = (SKILLS / "init/SKILL.md").read_text(encoding="utf-8")
ADOPT = (SKILLS / "adopt/SKILL.md").read_text(encoding="utf-8")


def test_adopt_asks_its_product_questions_first_and_chooses_tools_itself() -> None:
    step_3 = ADOPT.split("3. **Recognise the repository.**", 1)[1].split("\n4. ", 1)[0]
    assert "nothing after this step asks the owner anything" in step_3
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
    assert "**A bite for every gate you bind.**" in ADOPT
    assert "the `bite-all` entry point plants each fault of `quality/bites.toml`" in ADOPT


def test_adopt_binds_the_floor_and_what_has_arrived_and_builds_no_skeleton() -> None:
    assert "walking skeleton" not in ADOPT
    assert 'scripts/triggers.py" --count' in ADOPT
    assert 'scripts/triggers.py" --json' in ADOPT
    assert '`@echo "later: <trigger>"`' in ADOPT
    assert "On an empty repository none of the three is asked." in ADOPT


def test_adopt_provisions_what_the_first_slice_needs_and_rehearses_it() -> None:
    assert "**The tests are provisioned, not asked.**" in ADOPT
    assert "**Rehearse the first slice.**" in ADOPT
    assert "**A key a declared type needs is never removed to quiet a gate.**" in ADOPT
    assert 'no rule leaves a config to "the change that brings" something' in ADOPT
    assert "`just start` and the address it prints" in ADOPT
    assert "from ratchet_guards import diff; sys.exit(diff.main())'" in ADOPT
    assert "--root . --base HEAD` lists no guard path" in ADOPT
    assert 'Set the four to "none"' not in ADOPT
    assert "Slice one is the one step 11's design brief names. When the brief names none" in ADOPT
    assert "Rehearse at most twice" in ADOPT
    assert "for a `cli`, `job` or `app` the run step 9's table gives its `smoke`" in ADOPT
    assert "rehearse again" not in ADOPT, "the rehearsal is bounded"


def test_adopt_leaves_the_permission_rules_to_step_8() -> None:
    step_2 = ADOPT.split("2. **Copy the templates.**", 1)[1].split("\n3. ", 1)[0]
    step_8 = ADOPT.split("8. **Fill the manifest", 1)[1].split("\n9. ", 1)[0]
    assert "skipping `.claude/settings.json`" in step_2
    assert "creates `.claude/settings.json`" in step_8
    assert "path rules from the manifest on the default branch" in step_8
    assert "permission rules come from the work tree's" in step_8


def test_the_feature_skill_shortens_a_long_subject_by_a_fixed_rule() -> None:
    feature = (SKILLS / "feature/SKILL.md").read_text(encoding="utf-8")
    assert "drop whole words from the end of the slug until it fits" in feature
    assert "at most 72 characters" in feature
    assert commits.MAX_SUBJECT == 72


FEATURE = " ".join((SKILLS / "feature/SKILL.md").read_text(encoding="utf-8").split())


def test_the_feature_skill_parks_the_findings_of_a_run_that_ran_no_review() -> None:
    assert "`unresolvedBlockingFindings`, and `reviewIssues` when a review ran" in FEATURE
    assert "with the record it breaks and the sentence it quotes" in FEATURE
    assert "A result with `stop` `carried` ran no review" in FEATURE
    assert "with the critic's view as the first option" in FEATURE


def test_the_feature_skill_says_the_critic_did_not_run_in_both_modes() -> None:
    shared = FEATURE.split("## The pull request", 1)[1].split(" ## ", 1)[0]
    assert (
        "When the critic did not run, the pull request says so and why, from the result's"
        " `critic`, in both modes." in shared
    )


def test_the_drain_closes_the_ticket_from_every_commit() -> None:
    step = FEATURE.split("2. **Commit.**", 1)[1].split("3. **Bring it up to date.**", 1)[0]
    assert "The body holds `Closes <ref>`, whether or not the subject was shortened." in step


def test_a_labelled_tickets_answers_are_the_owners_to_merge_with_a_forge_too() -> None:
    asking = FEATURE.split("## Questions for the owner", 1)[1]
    assert (
        "With a forge, the merge job leaves the pull request to the owner and comments why: relay"
        " that comment as it is." in asking
    )


def test_a_red_preflight_on_the_branch_stops_the_build() -> None:
    step_2 = FEATURE.split("2. **The branch.**", 1)[1].split("3. **The pipeline.**", 1)[0]
    assert (
        "**Red with `--branch`:** report `verdict: blocked` and, for each failure, its `check`,"
        " `detail` and `produce`. Spawn nothing. Stop." in step_2
    )


def test_the_drain_reads_a_refusal_with_exit_2_as_no_conflict() -> None:
    assert "When it prints JSON, the JSON decides" in FEATURE
    assert (
        "**Exit 2 from `update`, `close` or `split` with a `drain:` or `merge:` line and no JSON:**"
        " the"
        " command refused before any work on the ticket. Report the line, leave the ticket as it"
        " is, and go on at step 5 (`D plan`)." in FEATURE
    )


def test_the_drain_stops_on_a_start_that_cannot_be_written_whichever_command_says_so() -> None:
    opening = FEATURE.split("3. **Start the pipelines.**", 1)[1].split("4. **Settle", 1)[0]
    refused = FEATURE.split("**Exit 2 from `update`, `close` or `split`", 1)[1].split("\n", 1)[0]
    checked = FEATURE.split("### The publish check", 1)[1].split("## ", 1)[0]
    stop = "report that line to the owner word for word"
    assert all(stop in part.lower() for part in (opening, refused, checked))
    assert all("`cannot be written`" in part for part in (opening, refused, checked))


def test_the_drain_does_not_close_a_ticket_that_update_says_has_merged() -> None:
    step = FEATURE.split("3. **Bring it up to date.**", 1)[1].split("4. **Judge it again.**", 1)[0]
    assert (
        "**Exit 1 with `update: <ref> is merged and is not rebased`:** the ticket has landed. Do"
        " not close it. Go on at step 5 (`D plan`); the report lists it under Merged." in step
    )


def test_a_split_plan_is_checked_before_drain_split_runs_it() -> None:
    too_large = FEATURE.split("## Too large: the agent splits it", 1)[1].split(" ## ", 1)[0]
    checked = (
        (
            "run `python3 -E -s -B -X pycache_prefix=/nonexistent/ratchet-no-bytecode"
            ' "${CLAUDE_PLUGIN_ROOT}/scripts/split.py" check <ticket> --plan <file>'
            " --root .`. It judges the plan as `split` will and files nothing."
        ),
        (
            "run `python3 -E -s -B -X pycache_prefix=/nonexistent/ratchet-no-bytecode"
            ' "${CLAUDE_PLUGIN_ROOT}/scripts/drain.py" split <ticket> --plan <file>'
            " --root .`, only with a plan the check passed."
        ),
    )
    assert all(sentence in too_large for sentence in checked)


def test_the_fork_test_has_one_wording_the_spec_role_and_the_conjecture_skill_share() -> None:
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
    conjecture = (SKILLS / "conjecture/SKILL.md").read_text(encoding="utf-8")
    assert "*What you asked for*" in conjecture
    for became in ("**built**", "**assumed**", "**left out**", "**added**"):
        assert became in conjecture, became
    assert "`## In the owner's words` in `docs/design-brief.md`" in conjecture
    brief = (SKILLS / "adopt/templates/docs/design-brief.md").read_text(encoding="utf-8")
    assert "## In the owner's words" in brief
    assert "under `## In the owner's words` each question the owner answered here" in ADOPT


def test_init_asks_about_the_product_in_one_message() -> None:
    asked = re.findall(r"^\d\. \*\*Ask\*\* `([^`]+)`", INIT, re.MULTILINE)
    assert asked == ["init.purpose", "init.user", "init.difference", "init.place", "init.online"]
    assert "interactive" not in INIT


def test_init_passes_the_owners_answers_to_the_setups() -> None:
    assert "the argument `init` followed by the owner's answers" in INIT
    assert "budget answered as `medium`" in INIT
    assert "the owner types `/mattpocock-skills:setup-matt-pocock-skills` after init" in INIT
    assert "follow it by hand" not in INIT


def test_init_installs_a_missing_companion_plugin_without_asking() -> None:
    step_1 = " ".join(INIT.split("1. **Check the ground.**", 1)[1].split("\n2. ", 1)[0].split())
    assert "is missing, install it without asking and say so in the hand-over" in step_1


def test_init_leaves_the_agent_skills_block_to_the_tracker_setup() -> None:
    bullet = INIT.split("**`mattpocock-skills`: not run here.**", 1)[1].split("**`pstack`", 1)[0]
    assert "Agent skills" not in bullet


def test_every_ratchet_skill_ends_with_the_route() -> None:
    for name in ("init", "adopt", "feature", "safeguard", "architecture"):
        text = (SKILLS / name / "SKILL.md").read_text(encoding="utf-8")
        assert 'scripts/route.py" --root .' in text, name


def test_the_safeguard_skill_asks_nothing_and_commits_as_the_gate_accepts() -> None:
    safeguard = (SKILLS / "safeguard/SKILL.md").read_text(encoding="utf-8")
    assert "**Assume** `safeguard.tools`" in safeguard
    assert "**Ask**" not in safeguard
    assert "`just bite-one <gate>`" in safeguard
    assert commits.SUBJECT.match("chore(method): safeguard outbound calls")
    assert "`chore(method): safeguard <what>`" in safeguard


def test_the_architecture_skill_asks_once_and_only_when_the_architecture_changes() -> None:
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
    safeguard = (SKILLS / "safeguard/SKILL.md").read_text(encoding="utf-8")
    assert "Add the tool's config path to the `tool-config` paths" in safeguard
    assert "`.claude/settings.json` needs no change" in safeguard
    assert "just settings" not in safeguard
    guards = (SKILLS / "method/guards.md").read_text(encoding="utf-8")
    assert "the agent adds it to `guards/manifest.toml` on its branch" in guards


def test_next_says_what_the_upgrade_does_and_bounds_its_own_retries() -> None:
    next_page = flat("next/SKILL.md")
    assert "give no count of what is owed: only the upgrade's run knows it" in next_page
    assert (
        "When it fails a second time, or the route still names it, stop there and relay what the"
        " command printed, as it is." in next_page
    )


def test_fill_names_where_the_template_is_and_how_its_change_lands() -> None:
    fill = flat("fill/SKILL.md")
    assert "`${CLAUDE_PLUGIN_ROOT}/skills/adopt/templates/docs/process/binding.md`" in fill
    assert (
        "With no forge, write the change description at `.scratch/changes/<branch>.md` (the"
        " branch's `/` written as `-`) and run"
        " `just merge <branch>`." in fill
    )


UNLISTED = '[[group]]\nname = "tool-config"\nreason = "Rule sets."\npaths = ["lint.toml"]\n'


def test_a_config_the_manifest_does_not_list_is_said_to_be_unguarded(tmp_path: Path) -> None:
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
    assert "name the path you added to the manifest" in hand_over
    for page in ("method/SKILL.md", "method/ratchet.md"):
        text = flat(page)
        assert "a config the group does not list is neither guarded nor recorded" in text, page
        assert "effective configuration is snapshotted" not in text.lower(), page
        assert "as the tool resolves it" not in text, page
    readme = " ".join((SKILLS.parent / "README.md").read_text(encoding="utf-8").split())
    assert "each gate's effective configuration is snapshotted" not in readme


PLUGIN_ROOT = SKILLS.parent
OWN_FILES = (
    *(PLUGIN_ROOT / name for name in ("skills", "workflows", "scripts")),
    PLUGIN_ROOT / "README.md",
    *(path for path in sorted((PLUGIN_ROOT / "docs").iterdir()) if path.name != "adr"),
)


def own_texts() -> dict[str, str]:
    found: dict[str, str] = {}
    for root in OWN_FILES:
        for path in [root] if root.is_file() else sorted(root.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                try:
                    found[str(path.relative_to(PLUGIN_ROOT))] = path.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    continue
    return found


def companion_references() -> list[tuple[str, int, str, str, bool]]:
    names = "|".join(re.escape(name) for name in conformance.companions())
    token = re.compile(rf"(?<![\w/:.-])(/?)({names}):([a-z][a-z0-9-]*)(?![\w:])")
    return [
        (name, number, match.group(2), match.group(3), match.group(1) == "/")
        for name, text in own_texts().items()
        for number, line in enumerate(text.splitlines(), 1)
        for match in token.finditer(line)
    ]


def test_each_companion_skill_is_named_in_one_form_and_no_pstack_command_appears() -> None:
    forms: dict[str, set[bool]] = {}
    where: dict[str, list[str]] = {}
    for name, number, plugin, skill, slash in companion_references():
        forms.setdefault(f"{plugin}:{skill}", set()).add(slash)
        where.setdefault(f"{plugin}:{skill}", []).append(f"{name}:{number}{'/' if slash else ''}")
    mixed = {skill: where[skill] for skill, used in forms.items() if len(used) > 1}
    assert not mixed, f"named both as a command and as a load: {mixed}"
    commands = [
        f"{name}:{number}"
        for name, text in own_texts().items()
        for number, line in enumerate(text.splitlines(), 1)
        if "/pstack:" in line
    ]
    assert not commands, f"names a pstack command: {commands}"


def test_every_companion_reference_is_registered_and_scripts_hardcode_none() -> None:
    registered = {
        (role.plugin, role.slug): role.kind == companions.OWNER
        for role in companions.roles().values()
    }
    unregistered: list[str] = []
    wrong_form: list[str] = []
    in_scripts: list[str] = []
    for name, number, plugin, skill, slash in companion_references():
        loc = f"{name}:{number} ({'/' if slash else ''}{plugin}:{skill})"
        if name.startswith("scripts/") and name.endswith(".py"):
            in_scripts.append(loc)
        expected = registered.get((plugin, skill))
        if expected is None:
            unregistered.append(loc)
        elif expected != slash:
            wrong_form.append(loc)
    assert not in_scripts, f"scripts must resolve via companions.py: {in_scripts}"
    assert not unregistered, f"unregistered companion skills: {unregistered}"
    assert not wrong_form, f"companion skill form disagrees with registry: {wrong_form}"


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
    "/ratchet:architecture",
)
PLACES = ["init/templates/AGENTS.md", "method/SKILL.md", "method/records.md", "method/pipeline.md"]


@pytest.mark.parametrize("page", PLACES)
def test_the_rule_on_asking_has_one_meaning_in_each_place_an_agent_reads_it(page: str) -> None:
    text = " ".join((SKILLS / page).read_text(encoding="utf-8").split())
    for part in RULE_PARTS:
        assert part in text, (page, part)


def test_the_conjecture_asks_every_open_fork_and_cites_what_it_built() -> None:
    conjecture = " ".join((SKILLS / "conjecture/SKILL.md").read_text(encoding="utf-8").split())
    assert "one thing at most" not in conjecture
    assert "Ask every such fork before you write anything, all in one message" in conjecture
    assert "Never write that no question was needed while a row says `(left open)`" in conjecture
    assert "a **built** row with no criterion or story to name is not built" in conjecture
    assert "A user story an agent wrote is not the owner's words unless" in conjecture
    assert "correct the criterion to follow their words" in conjecture


def table_problems(table: str) -> list[str]:
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


def conjecture_step(number: int) -> str:
    page = flat("conjecture/SKILL.md")
    return page.split(f" {number}. **", 1)[1].split(f" {number + 1}. **", 1)[0]


def test_the_size_estimate_is_written_on_the_ticket_and_never_on_a_spec() -> None:
    step_2 = conjecture_step(2)
    assert (
        "**A ticket:** the block goes on the ticket itself, even when its siblings share a parent"
        " spec, since the preflight reads `## Size` only from the ticket's own body." in step_2
    )
    assert (
        "**A spec that has not been split:** write no `## Size`. Make the estimate all the same,"
        " for the ceiling case below." in step_2
    )


def test_a_ticket_that_lacks_only_its_size_gets_the_size_and_a_large_one_is_written() -> None:
    step_1 = conjecture_step(1)
    assert "and the ticket states no `## Size`:** it was written before sizes were." in step_1
    assert "Write the size alone" in step_1
    step_2 = conjecture_step(2)
    assert "**The estimate passes a ceiling:** write it as it is, and the ticket with it." in step_2
    assert "not written as one" not in step_2
    step_5 = flat("conjecture/SKILL.md").split(" 5. **", 1)[1]
    assert "**Green, or red on `size` alone:** the last line is the next command" in step_5


def test_the_conjecture_is_printed_once_at_the_hand_over() -> None:
    step_3 = conjecture_step(3)
    assert "It is printed once, in the final reply of step 5" in step_3


def test_a_labelled_ticket_is_the_owners_to_merge_on_a_forge_too() -> None:
    step_4 = conjecture_step(4)
    assert "the merge job leaves the pull request to the owner and comments why on it" in step_4


CRITERIA_BODY = (
    "# Save links\n\nStatus: needs-triage\n\n## What to build\nOne list.\n\n"
    "## Acceptance criteria\n- [ ] newest first\n- [ ] a re-pasted link is refused\n\n"
    "## Blocked by\nNone\n"
)


def criteria_digest(command: str, body: str, tmp_path: Path) -> str:
    path = tmp_path / "ticket.md"
    path.write_bytes(body.encode("utf-8"))
    run = command.replace("<file>", str(path)).replace("<heading>", "## Acceptance criteria")
    bash = shutil.which("bash")
    assert bash
    done = subprocess.run(  # noqa: S603 # -- the page's own command, run on a file the test wrote
        [bash, "-c", run],
        env={**os.environ, "CLAUDE_PLUGIN_ROOT": str(SKILLS.parent)},
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout.strip()


def test_a_conjecture_is_drafted_again_when_its_criteria_change_and_only_then(
    tmp_path: Path,
) -> None:
    page = (SKILLS / "conjecture/SKILL.md").read_text(encoding="utf-8")
    reference = page.split("\n## The criteria digest\n", 1)[1]
    found = re.search(r"\*\*The body is a file\*\*.*`([^`]+)`$", reference, re.MULTILINE)
    assert found, "the page gives the command for a body in a file"
    command = found.group(1)
    assert command.endswith(" '<heading>' <file>")
    forge = f"`gh issue view <n> --json body --jq .body | {command.removesuffix(' <file>')}`"
    assert forge in reference
    written = criteria_digest(command, CRITERIA_BODY, tmp_path)
    assert re.fullmatch(r"[0-9a-f]{40}", written)
    same = (
        CRITERIA_BODY.replace("needs-triage", "ready-for-agent")
        + "\n## Answers\n- Refuse a re-pasted link? Yes.\n\n## Conjecture\nOne list.\n"
        + f"- Criteria: `## Acceptance criteria` `{written}`\n",
        CRITERIA_BODY.replace("\n", "\r\n"),
    )
    for body in same:
        assert criteria_digest(command, body, tmp_path) == written
    last = "# Save links\n\n## Acceptance criteria\n\n- A re-pasted link is refused.\n"
    spaced = last.replace("criteria\n\n- ", "criteria\n- ") + "\n\n## Answers\n- Yes.\n"
    assert criteria_digest(command, spaced, tmp_path) == criteria_digest(command, last, tmp_path)
    changed = CRITERIA_BODY.replace("is refused", "is kept twice")
    assert criteria_digest(command, changed, tmp_path) != written
    step_1 = conjecture_step(1)
    assert "**The command prints another digest than the line names, or exits 3:**" in step_1
    assert "Redraft it: run steps 2 to 5" in step_1
    assert "**It exits any other way:** stop" in step_1
    assert "redraft nothing" in step_1


@pytest.mark.parametrize("via", ["file", "stdin"])
def test_a_digest_command_that_cannot_run_is_not_read_as_changed_criteria(
    via: str, tmp_path: Path
) -> None:
    page = (SKILLS / "conjecture/SKILL.md").read_text(encoding="utf-8")
    reference = page.split("\n## The criteria digest\n", 1)[1]
    found = re.search(r"\*\*The body is a file\*\*.*`([^`]+)`$", reference, re.MULTILINE)
    assert found, "the page gives the command for a body in a file"
    path = tmp_path / "ticket.md"
    path.write_text(CRITERIA_BODY, encoding="utf-8")
    command = found.group(1).replace("<heading>", "## Acceptance criteria")
    run = (
        command.replace("<file>", str(path))
        if via == "file"
        else f"cat {path} | {command.removesuffix(' <file>')}"
    )
    bash = shutil.which("bash")
    assert bash
    unset = {name: value for name, value in os.environ.items() if name != "CLAUDE_PLUGIN_ROOT"}
    done = subprocess.run(  # noqa: S603 # -- the page's own command with the plugin root unset
        [bash, "-c", run], env=unset, capture_output=True, text=True, check=False
    )
    assert done.stdout == ""
    assert done.returncode not in (0, 3), done.stderr


def test_a_redraft_keeps_the_answers_and_shows_the_owner_what_changed() -> None:
    step_3 = conjecture_step(3)
    assert "**A redraft:** ask only the forks the changed criteria opened" in step_3
    assert "Every other line under `## Answers` stays as it is" in step_3
    step_4 = conjecture_step(4)
    assert "`## Answers` keeps every earlier line. It lands as below, as the first write" in step_4
    step_5 = conjecture_step(5)
    assert "the old words under *Was* and the new under *Now*" in step_5
    records = flat("method/records.md")
    assert "keeps every line under `## Answers`, asks only the forks the change opened" in records


SPLIT_BINDING = (
    "# Binding\n\n- Life events: depended-on\n\n## Gates\n\n- Application types: service\n"
)
SPLIT_CONJECTURE = (
    "## Conjecture\n\nParse the ticket once in a script; the refuter is a green run on a ticket"
    " without a label. Alternatives: an agent reads it (killed: it spawns first).\n\n"
)
SPLIT_SPEC = (
    f"# Notes\n\n## Solution\n\nX.\n\n{SPLIT_CONJECTURE}## Operability\n\n"
    "- Flag: `notes`, off until the owner switches it on\n- Signal: a span per request\n"
    "- Load: the list route joins the load smoke\n- Security: one read route and no new input\n"
)
SPLIT_TICKET = "Parent: #12\n\n## What to build\n\nThe list.\n\n## Acceptance criteria\n\n- [ ] a\n"
SPLIT_PART = (
    "Split from: #13\nBlocked by: none\n\n## What to build\n\nThe list, part 2.\n\n"
    f"## Acceptance criteria\n\n- [ ] b\n\n{SPLIT_CONJECTURE}"
    "## Size\n\n- Product lines: 40\n- Product files: 2\n"
)


def split_forge(part: str) -> preflight.Runner:
    bodies = {"12": SPLIT_SPEC, "13": SPLIT_TICKET, "21": part}

    def gh(args: Sequence[str]) -> str | None:
        if list(args[:2]) != ["issue", "view"] or args[2] not in bodies:
            return None
        return json.dumps(
            {
                "number": int(args[2]),
                "title": f"Issue {args[2]}",
                "body": bodies[args[2]],
                "labels": [{"name": "ready-for-agent"}],
                "state": "OPEN",
            }
        )

    return gh


def test_a_part_that_names_no_parent_is_given_its_tickets_parent_line_before_nothing_is_left(
    tmp_path: Path,
) -> None:
    root = repository(tmp_path, {"docs/process/binding.md": SPLIT_BINDING})
    install_hooks(root)
    unlinked = preflight.preflight("21", root, base="main", branch=None, gh=split_forge(SPLIT_PART))
    assert [failure.check for failure in unlinked.failures] == ["operability"]
    linked = preflight.preflight(
        "21", root, base="main", branch=None, gh=split_forge(f"Parent: #12\n{SPLIT_PART}")
    )
    assert linked.ok, linked.failures
    step_1 = conjecture_step(1).lower()
    after = step_1.split("there is nothing to write", 1)[1]
    nothing_to_write = after.split("say so and stop", 1)[0]
    assert "a part that still lacks the `parent:` line step 4 gives it" in nothing_to_write
    assert "write that line alone as step 4 says" in nothing_to_write


def test_the_conjecture_names_one_shared_spec_and_no_dead_clause() -> None:
    shared = "the ticket's siblings share a parent spec"
    assert all(shared in conjecture_step(step).lower() for step in (2, 4))
    assert "while a row says `(left open)`." in conjecture_step(3)


def test_adopt_asks_a_repository_with_code_whether_others_reach_it() -> None:
    asked = ADOPT.split("## What the owner is asked", 1)[1].split("\n## ", 1)[0]
    flat = " ".join(asked.split())
    assert "On a repository that already has code, always ask the first" in flat
    assert 'recommend "no" only when the tree shows none' in flat
    assert "On an empty repository none of the three is asked." in flat
    assert flat.index("always ask the first") < flat.index("**Ask** `event.deployed`")


def test_init_and_adopt_take_the_owners_preferences_before_any_default() -> None:
    for name, text in (("init", INIT), ("adopt", ADOPT)):
        assert "**What the owner asked for wins.**" in text, name
        assert 'as "you asked for"' in text, name


def flat(page: str) -> str:
    return " ".join((SKILLS / page).read_text(encoding="utf-8").split())


PREFERENCE_RULE = "## A preference typed after a command"
PREFERENCE_LINK = "(../method/records.md#a-preference-typed-after-a-command)"


def test_a_typed_preference_chooses_among_allowed_options_and_never_loosens_a_safeguard() -> None:
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
    text = flat(page)
    if page == "method/records.md":
        text = text.split(PREFERENCE_RULE, 1)[1].split(" ## ", 1)[0]
    else:
        text = text.split("**What the owner asked for wins.**", 1)[1]
        text = text.split("**Everything else is assumed.**", 1)[0]
    assert part in text


def test_a_preference_not_applied_is_named_in_the_hand_over() -> None:
    hand_overs = {
        "adopt": flat("adopt/SKILL.md").split("13. **Hand over.**", 1)[1],
        "init": flat("init/SKILL.md").split("9. **Hand over.**", 1)[1].split(" ## ", 1)[0],
    }
    for name, step in hand_overs.items():
        assert "as not applied, with what was asked for, why not and the route that exists" in (
            step
        ), name


def test_an_overturned_assumption_has_a_route_and_a_stated_cost() -> None:
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
    return re.findall(r"such as ``(.+?)``", flat(page))


def test_every_cost_line_the_owner_reads_says_what_is_redone() -> None:
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
    assert any("adoption then offers ways to build it" in span for span in examples["init"])
    records = flat("method/records.md")
    route = records.split("## Overturning an assumption", 1)[1].split(" ## ", 1)[0]
    assert "its lockfile" in route
    assert "the old tool's config removed" in route
    assert "nothing yet binds the gates again for another stack" in route


def test_the_cost_check_names_the_stack_and_the_tool_route_names_what_is_redone() -> None:
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
    step_13 = ADOPT.split("13. **Hand over.**", 1)[1]
    assert re.search(r"`(?:adopt|init|event|safeguard)\.[a-z-]+`", step_13) is None
    assert "no question key, type name or other method term in it" in step_13


ARGUMENT_PLACEHOLDER = re.compile(r"\$(?:ARGUMENTS(?:\[\d+\])?|\d+)")


def frontmatter(text: str) -> str:
    return text.split("---\n", 2)[1] if text.startswith("---\n") else ""


def test_no_skill_holds_text_the_harness_replaces_with_its_argument() -> None:
    pages = {
        path.relative_to(SKILLS): path.read_text(encoding="utf-8")
        for path in SKILLS.rglob("SKILL.md")
    }
    replaced = [
        f"{page}:{number}: {match.group(0)}"
        for page, text in sorted(pages.items())
        for number, line in enumerate(text.splitlines(), 1)
        for match in ARGUMENT_PLACEHOLDER.finditer(line)
    ]
    assert not replaced, f"the harness puts the skill's argument in place of: {replaced}"
    named = [
        str(page)
        for page, text in pages.items()
        if re.search(r"^arguments:", frontmatter(text), re.MULTILINE)
    ]
    assert not named, f"each `$name` these declare is replaced by an argument too: {named}"


def test_method_and_adopt_skills_reflect_local_loop_budget() -> None:
    method = flat("method/SKILL.md")
    assert "A tier runs over its ceiling" not in method
    assert (
        "**The loop is over its budget:** narrow the gate the red names to what the change"
        " reaches, or move it to tier 2 where the red offers that; never raise the budget, which"
        " is the owner's, and when the red says the loop was over before this change, report it"
        " as a blocker." in method
    )
    tiers = flat("method/tiers.md")
    assert "[ceilings.loop]" in tiers
    assert "loop.wait" in tiers
    ratchet = flat("method/ratchet.md")
    assert "[ceilings.loop]" in ratchet
    assert "**Ask** `loop.wait`" in ADOPT
    next_page = flat("next/SKILL.md")
    assert "**Ask** `loop.wait`" in next_page


def test_adopt_binding_and_method_docs_cover_expect_tests_and_recorded_outputs() -> None:
    binding = (SKILLS / "adopt/templates/docs/process/binding.md").read_text(encoding="utf-8")
    assert "- Recorded-output facility:" in binding
    assert "## Recorded outputs\n" in binding
    assert "| Paths | Pattern |" in binding
    assert "recorded-output facility" in ADOPT
    assert "## Recorded outputs" in ADOPT
    tiers = flat("method/tiers.md")
    assert (
        "| Does the code do what its tests claim? | unit, property, expect and fitness tests;"
        in tiers
    )
    assert "**An expect test holds outputs, not inputs.**" in tiers
    assert "**An expect test masks varying values.**" in tiers
    assert "**No gate recipe runs update mode.**" in tiers
    pipeline_doc = flat("method/pipeline.md")
    assert "expect test" in pipeline_doc
    assert "recordedOutputs" in pipeline_doc
    assert "expect-output" in pipeline_doc
    guards_doc = flat("method/guards.md")
    assert "expect-output" in guards_doc
    assert "scripts/expect_outputs.py" in guards_doc


def test_the_safeguard_skill_requires_acceptance_criteria_on_filed_tickets() -> None:
    safeguard = flat("safeguard/SKILL.md")
    defect = safeguard.split("**A defect at binding.**", 1)[1].split("4. **Record", 1)[0]
    round_upkeep = safeguard.split("## A round of the formal model's upkeep", 1)[1]
    assert "## Acceptance criteria" in defect
    assert "## Acceptance criteria" in round_upkeep
