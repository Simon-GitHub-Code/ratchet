"""The pipeline script's verdict helpers, run in node over the agents' reports."""

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import drain
import merge_ready
from tests.repos import branch, commit, repository, write

PIPELINE = Path(__file__).resolve().parents[2] / "workflows/pipeline.js"
MANIFEST_TOML = '[[group]]\nname = "g"\nreason = "Because it is."\npaths = ["lint.toml"]\n'


def _paths(call: str, *arguments: object) -> list[str]:
    """The sorted paths one of the script's helpers returns for ``arguments``."""
    source = PIPELINE.read_text(encoding="utf-8")
    helpers = source[source.index("function packageName") : source.index("function must(")]
    program = (
        f"{helpers}\nconst args = JSON.parse(process.argv[1])\n"
        f"console.log(JSON.stringify([...{call}(...args)].sort()))"
    )
    node = shutil.which("node")
    assert node, "node runs the workflow script"
    run = subprocess.run(  # noqa: S603 # -- a fixed argv, no shell
        [node, "-e", program, json.dumps(arguments)],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(run.stdout)


def _decided(spec: list[dict[str, str]] | None, notes: list[dict[str, object]] | None) -> list[str]:
    return _paths("specDecidedDependencies", spec, notes)


SPEC = [{"name": "Attrs", "reason": "value objects"}]
MANIFEST = {"path": "pyproject.toml", "note": "dependencies added", "packages": ["attrs"]}
LOCK = {"path": "uv.lock", "note": "lockfile", "packages": ["attrs", "ruff"]}


def test_a_dependency_the_spec_named_covers_its_manifest_and_lockfile() -> None:
    assert _decided(SPEC, [MANIFEST, LOCK]) == ["pyproject.toml", "uv.lock"]


def test_an_added_package_the_spec_did_not_name_covers_nothing() -> None:
    extra = {**MANIFEST, "packages": ["attrs", "pytest-nocov"]}
    assert _decided(SPEC, [extra, LOCK]) == []


def test_a_lockfile_alone_is_not_covered() -> None:
    assert _decided(SPEC, [LOCK]) == []
    version_only = {**MANIFEST, "packages": []}
    assert _decided(SPEC, [version_only, LOCK]) == ["pyproject.toml"]


def test_missing_reports_cover_nothing() -> None:
    assert _decided(None, None) == []


def test_a_record_the_change_only_adds_to_is_covered() -> None:
    notes = [
        {"path": "GLOSSARY.md", "additions": ["term Click (avoid: hit, visit)"]},
        {"path": "docs/adr/0004-links-expire.md", "additions": ["ADR 0004 A link expires"]},
        {"path": "src/billing/GLOSSARY.md", "additions": []},
    ]
    assert _paths("additiveRecords", notes) == [
        "GLOSSARY.md",
        "docs/adr/0004-links-expire.md",
        "src/billing/GLOSSARY.md",
    ]


def test_a_record_note_without_a_path_or_a_list_covers_nothing() -> None:
    broken = [None, {"additions": ["term Click"]}, {"path": "GLOSSARY.md"}, {"path": ""}]
    assert _paths("additiveRecords", broken) == []
    assert _paths("additiveRecords", None) == []


def _record(**fields: object) -> dict[str, object]:
    """The verdict record the script's helper builds from ``fields``."""
    source = PIPELINE.read_text(encoding="utf-8")
    helper = source[source.index("function verdictRecord") : source.index("function must(")]
    program = f"{helper}\nconsole.log(JSON.stringify(verdictRecord(JSON.parse(process.argv[1]))))"
    node = shutil.which("node")
    assert node, "node runs the workflow script"
    run = subprocess.run(  # noqa: S603 # -- a fixed argv, no shell
        [node, "-e", program, json.dumps(fields)], capture_output=True, text=True, check=True
    )
    record: dict[str, object] = json.loads(run.stdout)
    return record


def test_the_verdict_record_names_what_is_still_open_as_texts() -> None:
    findings = [
        {"severity": "blocker", "title": " loses the order "},
        {"severity": "major", "title": ""},
        {"severity": "minor", "title": "a typo"},
        None,
    ]
    assumption = {"question": "q", "assumed": "a", "refuter": "r"}
    assert _record(
        slug="thing",
        ticket=4,
        verdict="request_changes",
        forcedBy=["1 unresolved blocker/major critic finding(s)"],
        findings=findings,
        openQuestions=["Which currency?"],
        assumptions=[assumption],
    ) == {
        "slug": "thing",
        "ticket": "4",
        "verdict": "request_changes",
        "forcedBy": ["1 unresolved blocker/major critic finding(s)"],
        "unresolved": ["loses the order", "a finding with no title"],
        "openQuestions": ["Which currency?"],
        "assumptions": [assumption],
    }


def test_a_clean_approval_records_nothing_open() -> None:
    record = _record(slug="thing", ticket="4", verdict="approve")
    assert record == {
        "slug": "thing",
        "ticket": "4",
        "verdict": "approve",
        "forcedBy": [],
        "unresolved": [],
        "openQuestions": [],
        "assumptions": [],
    }


def test_the_judge_passes_the_record_the_script_builds(tmp_path: Path) -> None:
    root = repository(tmp_path, {"guards/manifest.toml": MANIFEST_TOML, "src/a.py": "x = 1\n"})
    branch(root, "feature/4-thing")
    write(root, "src/a.py", "x = 2\n")
    commit(root, "feat(thing): the change")
    record = _record(slug="thing", ticket="4", verdict="approve")
    assert merge_ready.write_record(root, json.dumps(record)) == 0
    commit(root, "docs(thing): the verdict record")
    assert merge_ready.judge(root, "main", "HEAD") == []


# --- the whole script, run with agents that answer from a table ------------------------------

HARNESS = """
const text = require('fs').readFileSync(process.argv[1], 'utf8')
const source = text.replace('export const meta', 'const meta')
const [args, answers] = JSON.parse(process.argv[2])
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor
const labels = []
const prompts = {}
const schemas = {}
const logs = []
const agent = async (prompt, options) => {
  labels.push(options.label)
  prompts[options.label] = prompt
  schemas[options.label] = options.schema
  return answers[options.label] ?? null
}
const run = new AsyncFunction('args', 'agent', 'phase', 'log', source)
run(args, agent, () => {}, line => logs.push(line)).then(
  result => console.log(JSON.stringify({ result, labels, prompts, schemas, logs })),
  error => console.log(JSON.stringify({ error: error.message, labels })),
)
"""


# The promises of the dogfood's ticket 01 (a read-later list), as a reviewer reads them against a
# faithful spec: each one carried by a criterion, each case of the one field decided.
def _covered(promise: str, *criteria: str) -> dict[str, object]:
    return {"promise": promise, "reading": "covered", "criteria": list(criteria)}


NEW_TAB = "Each item's title links to the article and opens it in a new tab"
PROMISES = [
    _covered("The page lists the unread items newest first", "1", "3"),
    _covered("Saving a link answers `303` to the list", "2"),
    _covered("Reloading the page afterwards does not save it again", "4"),
    _covered("Two items saved in the same second keep a stable newest-first order", "5", "11"),
    _covered("The same link saved twice makes two items", "6"),
    _covered(NEW_TAB, "7"),
    _covered("An empty list shows one plain sentence saying nothing is left", "8"),
    _covered("It has no client JavaScript", "15"),
]
LINK_FIELD = {
    "input": "the link field of the save form",
    "empty": "16: a blank or missing link answers 400 and saves nothing",
    "oversized": "17: a link over 2048 characters answers 400 and saves nothing",
    "hostile": "9: markup characters show as text in the title and the href",
}
REVIEW = {
    "verdict": "approve",
    "ticketPromises": PROMISES,
    "acceptedInputs": [LINK_FIELD],
    "issues": [],
    "adrSuggested": "",
    "guardChanges": [],
    "disputeCoverage": [],
    "dependencyNotes": [],
    "recordNotes": [],
    "checkPasses": True,
    "checkOutput": "check: green",
}
IMPLEMENTED = {
    "filesChanged": ["src/links.py"],
    "allNewTestsPass": True,
    "checkPasses": True,
    "checkOutput": "check: green",
    "disputes": [],
    "notes": "",
}
PREFLIGHT = {
    "ok": True,
    "slug": "expiry",
    "branch": "feature/4-expiry",
    "number": "4",
    "title": "T",
}
SPECIFIED = {
    "slug": "expiry",
    "specPath": "docs/features/expiry/spec.md",
    "testFiles": ["tests/test_expiry.py"],
    "layersTouched": [],
    "openQuestions": [],
    "assumptions": [{"question": "q", "assumed": "a", "refuter": "r"}],
    "dependencies": [],
    "guardEdits": [],
    "failingForRightReason": True,
    "failures": [{"testFile": "tests/test_expiry.py", "content": "NameError: expire"}],
    "checkOutput": "1 failed",
}
CRITIQUED = {
    "findings": [],
    "testsAdded": [],
    "guardFilesChanged": [],
    "attempted": ["an expiry in the past"],
    "fitnessSabotageConfirmed": True,
    "fitnessSabotageSummary": "",
}
REJUDGE = {"slug": "expiry", "specPath": "docs/features/expiry/spec.md"}
ASKED = {
    "question": "Does an expired link say so, or send the visitor home?",
    "kind": "direction",
    "options": ["A page that says it expired", "The home page"],
    "recommendation": "The brief says a visitor is never left guessing.",
}


def _run(args: object, **answers: object) -> dict[str, Any]:
    """What the script returns for ``args`` when each agent answers ``answers[its label]``."""
    node = shutil.which("node")
    assert node, "node runs the workflow script"
    run = subprocess.run(  # noqa: S603 # -- a fixed argv, no shell
        [node, "-e", HARNESS, str(PIPELINE), json.dumps([args, answers])],
        capture_output=True,
        text=True,
        check=True,
    )
    ran: dict[str, Any] = json.loads(run.stdout)
    return ran


def _again(rejudge: dict[str, object], **answers: object) -> dict[str, Any]:
    args = {"ref": "4", "branch": "feature/4-expiry", "rejudge": {**REJUDGE, **rejudge}}
    return _run(args, **answers)


def test_a_branch_brought_up_to_date_is_reviewed_again_and_not_built_again() -> None:
    assumption = {"question": "q", "assumed": "a", "refuter": "r"}
    ran = _again({"assumptions": [assumption]}, **{"review-again": REVIEW})
    assert ran["labels"] == ["review-again"], "no preflight, no spec, no implementer, no critic"
    assert "This is a second review." in ran["prompts"]["review-again"]
    result = ran["result"]
    assert (result["rejudged"], result["verdict"], result["filesChanged"]) == (True, "approve", [])
    assert result["verdictRecord"] == {
        "slug": "expiry",
        "ticket": "4",
        "verdict": "approve",
        "forcedBy": [],
        "unresolved": [],
        "openQuestions": [],
        "assumptions": [assumption],
    }


def test_a_conflict_or_a_red_check_brings_the_reconcile_agent_first() -> None:
    reconciled = {**IMPLEMENTED, "filesChanged": ["flags.toml"], "notes": "both flags kept"}
    answers = {"reconcile": reconciled, "review-again": REVIEW}
    ran = _again({"conflicts": ["flags.toml", ""]}, **answers)
    assert ran["labels"] == ["reconcile", "review-again"]
    assert 'stopped on when it was tried: ["flags.toml"]' in ran["prompts"]["reconcile"]
    assert "git rebase --continue" in ran["prompts"]["reconcile"]
    result = ran["result"]
    assert (result["verdict"], result["filesChanged"]) == ("approve", ["flags.toml"])
    assert result["reconcileNotes"] == "both flags kept"
    assert _again({"checkRed": True}, **answers)["labels"] == ["reconcile", "review-again"]
    red = _again(
        {"checkRed": True}, **{**answers, "reconcile": {**reconciled, "checkPasses": False}}
    )
    assert red["result"]["verdict"] == "request_changes"
    assert red["result"]["verdictForcedBy"] == ["the reconcile result reported checkPasses: false"]


def test_the_second_review_is_held_to_the_first_ones_evidence_rules() -> None:
    finding = {"severity": "major", "title": "repeats a term the other change defined"}
    review = {
        **REVIEW,
        "issues": [finding],
        "guardChanges": ["pyproject.toml"],
        "checkPasses": False,
    }
    result = _again({}, **{"review-again": review})["result"]
    assert result["verdict"] == "request_changes"
    assert result["verdictForcedBy"] == [
        (
            "guard paths changed without a dispute record, a spec-named dependency or an additions"
            " note covering them: pyproject.toml"
        ),
        "1 blocker/major review issue(s)",
        "the review reported checkPasses: false",
    ]
    assert result["verdictRecord"]["unresolved"] == ["repeats a term the other change defined"]
    note = {"path": "pyproject.toml", "note": "dependencies added", "packages": ["attrs"]}
    named = {**REVIEW, "guardChanges": ["pyproject.toml"], "dependencyNotes": [note]}
    dependencies = [{"name": "attrs", "reason": "value objects"}]
    covered = _again({"dependencies": dependencies}, **{"review-again": named})["result"]
    assert (covered["verdict"], covered["undisputedGuardChanges"]) == ("approve", [])


def test_a_rejudge_without_what_the_first_run_returned_is_an_error() -> None:
    ran = _run({"ref": "4", "branch": "feature/4-expiry", "rejudge": {"slug": "expiry"}})
    assert ran["error"].startswith(
        "rejudge needs args.branch and args.rejudge = { slug, specPath }"
    )
    assert ran["labels"] == []
    died = _again({}, **{"review-again": None})
    assert died["error"].startswith("review agent returned no result")


def test_a_product_question_stops_the_run_with_its_options() -> None:
    asked = {**SPECIFIED, "openQuestions": [ASKED]}
    ran = _run({"ref": "4"}, preflight=PREFLIGHT, spec=asked)
    assert ran["labels"] == ["preflight", "spec"]
    result = ran["result"]
    assert (result["verdict"], result["openQuestions"]) == ("blocked", [ASKED])
    assert "under `## Answers`" in result["requiredByOrchestrator"][0]
    prompt = ran["prompts"]["spec"]
    assert "Never ask the owner to confirm a choice" in prompt
    assert "When the user could not tell which option was built, you choose." in prompt
    assert "Read `## Answers` and the comments too" in prompt
    assert _run(None)["labels"] == [], "no ticket, no agent"


def test_a_clean_run_spawns_five_agents_and_records_no_question() -> None:
    answers = {"implement": IMPLEMENTED, "critique": CRITIQUED, "review": REVIEW}
    ran = _run({"ref": "4"}, preflight=PREFLIGHT, spec=SPECIFIED, **answers)
    assert ran["labels"] == ["preflight", "spec", "implement", "critique", "review"]
    assert "This is a second review." not in ran["prompts"]["review"]
    result = ran["result"]
    assert (result["verdict"], result["branch"]) == ("approve", "feature/4-expiry")
    assert result["verdictRecord"]["openQuestions"] == []
    assert result["verdictRecord"]["assumptions"] == SPECIFIED["assumptions"]


def test_a_test_the_critic_kept_failing_is_fixed_before_the_review() -> None:
    # Why: a minor finding with a failing test left `check` red, skipped the fix loop and so
    # failed every review.
    finding = {"severity": "minor", "title": "records a HEAD as a GET", "testFails": True}
    kept = {**CRITIQUED, "findings": [finding], "testsAdded": ["tests/test_method.py"]}
    answers = {"implement": IMPLEMENTED, "critique": kept, "review": REVIEW}
    answers |= {"fix-1": IMPLEMENTED, "critique-1": CRITIQUED}
    ran = _run({"ref": "4"}, preflight=PREFLIGHT, spec=SPECIFIED, **answers)
    assert ran["labels"] == [
        "preflight",
        "spec",
        "implement",
        "critique",
        "fix-1",
        "critique-1",
        "review",
    ]
    assert '"title": "records a HEAD as a GET"' in ran["prompts"]["fix-1"].split("MAY resolve")[0]
    assert ran["result"]["verdict"] == "approve"
    passing = {**kept, "findings": [{**finding, "testFails": False}]}
    ran = _run(
        {"ref": "4"}, preflight=PREFLIGHT, spec=SPECIFIED, **{**answers, "critique": passing}
    )
    assert ran["labels"] == ["preflight", "spec", "implement", "critique", "review"]


def test_a_change_with_no_room_under_the_ceiling_stops_before_the_next_stage() -> None:
    # Why: a ticket whose spec tests and code already filled the ceiling ran nine agents and
    # ended red on change-size alone once the critic's tests were added.
    answers = {"implement": IMPLEMENTED, "critique": CRITIQUED, "review": REVIEW}
    heavy = {**SPECIFIED, "changeSize": {"lines": 1001, "ceiling": 1500}}
    ran = _run({"ref": "4"}, preflight=PREFLIGHT, spec=heavy, **answers)
    assert ran["labels"] == ["preflight", "spec"]
    result = ran["result"]
    assert (result["verdict"], result["tooLarge"]["stage"]) == ("blocked", "the spec")
    assert "1001 lines after the spec" in result["verdictForcedBy"][0]
    assert "splits the ticket" in result["requiredByOrchestrator"][1]

    fits = {**SPECIFIED, "changeSize": {"lines": 1000, "ceiling": 1500}}
    built = {**IMPLEMENTED, "changeSize": {"lines": 1200, "ceiling": 1500}}
    ran = _run({"ref": "4"}, preflight=PREFLIGHT, spec=fits, **{**answers, "implement": built})
    assert ran["labels"] == ["preflight", "spec", "implement"]
    assert ran["result"]["tooLarge"]["stage"] == "the implementation"
    assert sorted(ran["result"]["filesChanged"]) == ["src/links.py", "tests/test_expiry.py"]

    unbound = {**SPECIFIED, "changeSize": {"lines": 9, "ceiling": 0}}
    ran = _run({"ref": "4"}, preflight=PREFLIGHT, spec=unbound, **answers)
    assert ran["result"]["verdict"] == "approve"


def test_a_finding_whose_kept_test_passes_starts_no_fix_round() -> None:
    # Why: a major finding the critic's passing test showed the code holds still started a fix
    # round, and the fix agent had nothing to change.
    held = {
        "severity": "major",
        "title": "rejects a past expiry",
        "testFails": False,
        "heldByKeptTest": True,
    }
    tests = ["tests/critique/expiry.core.critique.test.py"]
    kept = {**CRITIQUED, "findings": [held], "testsAdded": tests}
    answers = {"implement": IMPLEMENTED, "critique": kept, "review": REVIEW}
    ran = _run({"ref": "4"}, preflight=PREFLIGHT, spec=SPECIFIED, **answers)
    assert ran["labels"] == ["preflight", "spec", "implement", "critique", "review"]
    result = ran["result"]
    assert (result["verdict"], result["unresolvedBlockingFindings"]) == ("approve", [])
    assert result["criticFindings"] == [held], "the review still reads it"

    open_major = {**held, "heldByKeptTest": False}
    answers |= {"critique": {**kept, "findings": [open_major]}, "fix-1": IMPLEMENTED}
    answers |= {"critique-1": CRITIQUED}
    ran = _run({"ref": "4"}, preflight=PREFLIGHT, spec=SPECIFIED, **answers)
    assert "fix-1" in ran["labels"], "no kept test, still owed"

    failing = {**held, "testFails": True}
    answers |= {"critique": {**kept, "findings": [failing]}}
    ran = _run({"ref": "4"}, preflight=PREFLIGHT, spec=SPECIFIED, **answers)
    assert "fix-1" in ran["labels"], "a failing kept test is owed whatever the flag says"


def test_a_held_finding_is_not_offered_to_the_fix_agent_as_a_minor() -> None:
    held = {"severity": "major", "title": "held", "testFails": False, "heldByKeptTest": True}
    open_major = {"severity": "major", "title": "open", "testFails": False, "heldByKeptTest": False}
    kept = {**CRITIQUED, "findings": [held, open_major]}
    answers = {"implement": IMPLEMENTED, "critique": kept, "review": REVIEW}
    answers |= {"fix-1": IMPLEMENTED, "critique-1": CRITIQUED}
    ran = _run({"ref": "4"}, preflight=PREFLIGHT, spec=SPECIFIED, **answers)
    assert '"title": "open"' in ran["prompts"]["fix-1"]
    assert '"title": "held"' not in ran["prompts"]["fix-1"]


def test_roles_with_a_red_tree_run_tier_zero_and_their_own_tests_only() -> None:
    # Why: one `check` took 70 to 95 seconds and every role ran it several times, the spec and
    # the critic on a tree that is red on purpose.
    answers = {"implement": IMPLEMENTED, "critique": CRITIQUED, "review": REVIEW}
    ran = _run({"ref": "4"}, preflight=PREFLIGHT, spec=SPECIFIED, **answers)
    prompts = ran["prompts"]
    for role in ("spec", "critique"):
        assert "do not run `check`" in prompts[role], role
        assert "run the `tier0`" in prompts[role], role
    for role in ("implement", "review"):
        assert "run `check` from the repository root" in prompts[role], role
        assert "do not run `check`" not in prompts[role], role
    assert "Run `tier0` and your new tests" in prompts["spec"]


def test_the_review_must_see_check_green_on_the_final_tree() -> None:
    answers = {"implement": IMPLEMENTED, "critique": CRITIQUED}
    red = {**REVIEW, "checkPasses": False}
    ran = _run({"ref": "4"}, preflight=PREFLIGHT, spec=SPECIFIED, review=red, **answers)
    assert ran["result"]["verdict"] == "request_changes"
    reason = "the review's run of check on the final tree was not green"
    assert reason in ran["result"]["verdictForcedBy"]


def test_the_critic_is_pointed_at_the_binding_s_critique_directory() -> None:
    # Why: "next to the spec's tests" put critique files in the source tree, one per feature
    # per layer, beside modules they are not unit tests of.
    answers = {"implement": IMPLEMENTED, "critique": CRITIQUED, "review": REVIEW}
    ran = _run({"ref": "4"}, preflight=PREFLIGHT, spec=SPECIFIED, **answers)
    prompt = ran["prompts"]["critique"]
    assert "Critique tests section names" in prompt
    assert "<slug>.<layer>.critique.test.<ext>" in prompt
    assert "next to the spec's tests" not in prompt


def test_a_ticket_whose_whole_change_cannot_fit_is_split_by_the_spec_stage() -> None:
    # Why: the spec, the critic's tests and the dispute records were 1,300 lines before any
    # implementation, and the run ended over the ceiling after nine agents.
    answers = {"implement": IMPLEMENTED, "critique": CRITIQUED, "review": REVIEW}
    split = [{"title": "first", "criteria": ["a"]}, {"title": "second", "criteria": ["b"]}]
    fits = {"implementationLines": 400, "criticLines": 300, "recordLines": 50, "proposedSplit": []}
    sized = {**SPECIFIED, "changeSize": {"lines": 700, "ceiling": 1500}}
    ran = _run({"ref": "4"}, preflight=PREFLIGHT, spec={**sized, "changeEstimate": fits}, **answers)
    assert ran["result"]["verdict"] == "approve", "1,450 of 1,500 fits"

    over = {**fits, "implementationLines": 451, "proposedSplit": split}
    ran = _run({"ref": "4"}, preflight=PREFLIGHT, spec={**sized, "changeEstimate": over}, **answers)
    assert ran["labels"] == ["preflight", "spec"]
    result = ran["result"]
    assert (result["verdict"], result["tooLarge"]["proposedSplit"]) == ("blocked", split)
    assert "estimates 1501 lines" in result["verdictForcedBy"][0]

    bare = {k: v for k, v in over.items() if k != "proposedSplit"}
    ran = _run({"ref": "4"}, preflight=PREFLIGHT, spec={**sized, "changeEstimate": bare}, **answers)
    assert ran["result"]["tooLarge"]["proposedSplit"] == []

    unmeasured = {**SPECIFIED, "changeEstimate": over}
    ran = _run({"ref": "4"}, preflight=PREFLIGHT, spec=unmeasured, **answers)
    assert ran["result"]["verdict"] == "approve", "no change-size gate, no estimate to hold"


# --- the critic waits for a project that is depended-on (ADR 0028) ------------------------------

YOUNG = {
    **PREFLIGHT,
    "critic": False,
    "critic_reason": "the binding declares no `depended-on` life event: nobody relies on it yet",
}


def test_a_project_nobody_depends_on_goes_spec_implement_review() -> None:
    answers = {"implement": IMPLEMENTED, "critique": CRITIQUED, "review": REVIEW}
    ran = _run({"ref": "4"}, preflight=YOUNG, spec=SPECIFIED, **answers)
    assert ran["labels"] == ["preflight", "spec", "implement", "review"]
    assert any(line.startswith("Critique and Fix skipped: the binding") for line in ran["logs"])
    result = ran["result"]
    assert result["verdict"] == "approve"
    assert result["verdictForcedBy"] == []
    assert result["critic"] == {
        "ran": False,
        "reason": f"the critic was not run: {YOUNG['critic_reason']}",
    }
    assert (result["criticFindings"], result["criticAttempted"], result["iterations"]) == (
        [],
        [],
        0,
    )
    assert result["unresolvedBlockingFindings"] == []
    assert any("critic.ran is false" in line for line in result["requiredByOrchestrator"])


def test_a_project_that_is_depended_on_runs_the_critic() -> None:
    answers = {"implement": IMPLEMENTED, "critique": CRITIQUED, "review": REVIEW}
    old = {**PREFLIGHT, "critic": True, "critic_reason": "others rely on it"}
    for preflight in (old, PREFLIGHT):
        ran = _run({"ref": "4"}, preflight=preflight, spec=SPECIFIED, **answers)
        assert ran["labels"] == ["preflight", "spec", "implement", "critique", "review"]
        assert ran["result"]["critic"]["ran"] is True
    assert ran["result"]["critic"]["reason"] == ""


def test_a_silent_critic_is_forced_but_an_absent_one_is_not() -> None:
    silent = {**CRITIQUED, "attempted": []}
    answers = {"implement": IMPLEMENTED, "critique": silent, "review": REVIEW}
    ran = _run({"ref": "4"}, preflight=PREFLIGHT, spec=SPECIFIED, **answers)
    assert ran["result"]["verdictForcedBy"] == [
        "the critic found nothing and lists nothing attempted"
    ]
    absent = _run({"ref": "4"}, preflight=YOUNG, spec=SPECIFIED, **answers)
    assert absent["result"]["verdict"] == "approve"


def test_the_review_does_not_name_findings_that_do_not_exist() -> None:
    answers = {"implement": IMPLEMENTED, "critique": CRITIQUED, "review": REVIEW}
    young = _run({"ref": "4"}, preflight=YOUNG, spec=SPECIFIED, **answers)["prompts"]["review"]
    assert "critic" not in young.split("Inputs you may read")[1].lower()
    assert "The implementer's notes are outside your inputs" in young
    old = _run({"ref": "4"}, preflight=PREFLIGHT, spec=SPECIFIED, **answers)["prompts"]["review"]
    assert "the critic's findings are outside your inputs" in old


def test_what_the_review_opens_is_open_without_a_critic() -> None:
    finding = {"severity": "major", "title": "loses the order"}
    review = {**REVIEW, "issues": [finding], "verdict": "approve"}
    ran = _run({"ref": "4"}, preflight=YOUNG, spec=SPECIFIED, implement=IMPLEMENTED, review=review)
    result = ran["result"]
    assert result["verdict"] == "request_changes"
    assert result["verdictForcedBy"] == ["1 blocker/major review issue(s)"]
    assert result["verdictRecord"]["unresolved"] == ["loses the order"]


def test_the_critics_share_of_the_size_is_not_counted_when_it_does_not_run() -> None:
    sized = {**SPECIFIED, "changeSize": {"lines": 700, "ceiling": 1500}}
    over = {"implementationLines": 451, "criticLines": 300, "recordLines": 50, "proposedSplit": []}
    answers = {"implement": IMPLEMENTED, "review": REVIEW}
    ran = _run({"ref": "4"}, preflight=YOUNG, spec={**sized, "changeEstimate": over}, **answers)
    assert ran["result"]["verdict"] == "approve", "1,201 of 1,500 without the critic's 300"
    assert "plus the critic's tests" not in ran["prompts"]["spec"]
    assert "return criticLines: 0" in ran["prompts"]["spec"]
    heavy = {**SPECIFIED, "changeSize": {"lines": 1001, "ceiling": 1500}}
    ran = _run({"ref": "4"}, preflight=YOUNG, spec=heavy)
    assert ran["result"]["verdictForcedBy"][0].endswith("with the review still to come")
    full = {**over, "implementationLines": 800}
    ran = _run({"ref": "4"}, preflight=YOUNG, spec={**sized, "changeEstimate": full})
    assert "no critic, records 50" in ran["result"]["verdictForcedBy"][0]


OWED_LINE = (
    "triggers: 'outbound-call' gained 1 instance(s) (src/b.py:2) while the base already held 1"
    " and these rows are still `later:`: survives failing dependencies."
)


def test_a_red_triggers_gate_is_left_to_the_safeguard_and_the_drain_reads_it() -> None:
    # Why: ADR 0028. Quieting the gate is the owner's to decide through /ratchet:safeguard, and
    # the drain recognises the parked run by the gate's own line in checkOutput.
    red = {**IMPLEMENTED, "checkPasses": False, "checkOutput": OWED_LINE, "notes": "outbound-call"}
    answers = {
        "implement": red,
        "critique": CRITIQUED,
        "review": {**REVIEW, "checkPasses": False, "checkOutput": "check: red"},
    }
    ran = _run({"ref": "4"}, preflight=PREFLIGHT, spec=SPECIFIED, **answers)
    prompt = ran["prompts"]["implement"]
    assert "red on the `triggers` gate" in prompt
    assert "do not edit the binding, the justfile or quality/" in prompt
    assert "`checkPasses: false`" in prompt
    assert "`/ratchet:safeguard`" in prompt
    assert ran["result"]["checkPasses"] is False
    assert drain.owed_kinds(ran["result"]) == ["outbound-call"]


def test_the_fix_agent_is_told_the_same_about_a_red_triggers_gate() -> None:
    kept = {**CRITIQUED, "findings": [{"severity": "major", "title": "open"}]}
    answers = {"implement": IMPLEMENTED, "critique": kept, "review": REVIEW}
    answers |= {"fix-1": IMPLEMENTED, "critique-1": CRITIQUED}
    ran = _run({"ref": "4"}, preflight=PREFLIGHT, spec=SPECIFIED, **answers)
    assert "red on the `triggers` gate" in ran["prompts"]["fix-1"]


# --- the review reads the ticket the spec was written from ---------------------------------------

KINDS: dict[str, type] = {
    "object": dict,
    "array": list,
    "string": str,
    "boolean": bool,
    "integer": int,
    "null": type(None),
}


def _valid(value: object, schema: dict[str, Any]) -> bool:
    """Whether ``value`` meets ``schema``, in the subset of JSON Schema the workflow uses."""
    if "anyOf" in schema:
        return any(_valid(value, option) for option in schema["anyOf"])
    kinds = schema.get("type", [])
    kinds = [kinds] if isinstance(kinds, str) else kinds
    checks = [
        not kinds or any(type(value) is KINDS[kind] for kind in kinds),
        "enum" not in schema or value in schema["enum"],
        not isinstance(value, str) or re.search(schema.get("pattern", ""), value) is not None,
    ]
    if isinstance(value, list):
        checks.append(len(value) >= schema.get("minItems", 0))
        checks.extend(_valid(item, schema.get("items", {})) for item in value)
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        checks.extend(key in value for key in schema.get("required", []))
        checks.extend(_valid(item, properties.get(key, {})) for key, item in value.items())
        if schema.get("additionalProperties") is False:
            checks.extend(key in properties for key in value)
    return all(checks)


def _built(review: dict[str, object], ref: str = "4") -> dict[str, Any]:
    answers = {"implement": IMPLEMENTED, "critique": CRITIQUED, "review": review}
    return _run({"ref": ref}, preflight=PREFLIGHT, spec=SPECIFIED, **answers)


def _review_schema() -> dict[str, Any]:
    schema: dict[str, Any] = _built(REVIEW)["schemas"]["review"]
    return schema


def _reread(promise: str, reading: dict[str, str]) -> list[dict[str, object]]:
    """The faithful promises, with ``promise`` read as ``reading`` instead."""
    return [p if p["promise"] != promise else {"promise": promise, **reading} for p in PROMISES]


def test_the_review_reads_the_ticket_the_spec_was_written_from() -> None:
    prompt = _built(REVIEW, ".scratch/list/issues/01-save-a-link.md")["prompts"]["review"]
    inputs = prompt.split("Inputs you may read")[1].split("Load the")[0]
    assert "The ticket: the file `.scratch/list/issues/01-save-a-link.md`" in inputs
    assert "`## Answers`" in inputs
    assert "the ticket are outside your inputs" not in prompt
    tracker = "`gh issue view 4 --json number,title,body,labels,comments`"
    assert tracker in _built(REVIEW)["prompts"]["review"]
    assert tracker in _again({}, **{"review-again": REVIEW})["prompts"]["review-again"]


def test_a_review_that_skips_the_ticket_or_the_inputs_is_not_a_review() -> None:
    schema = _review_schema()
    assert _valid(REVIEW, schema)
    for axis in ("ticketPromises", "acceptedInputs"):
        assert not _valid({k: v for k, v in REVIEW.items() if k != axis}, schema), axis
    assert not _valid({**REVIEW, "ticketPromises": []}, schema), "every ticket promises something"
    assert _valid({**REVIEW, "acceptedInputs": []}, schema), "a change may accept nothing"


def test_each_reading_of_a_promise_carries_what_shows_it() -> None:
    schema = _review_schema()

    def holds(entry: dict[str, object]) -> bool:
        return _valid({**REVIEW, "ticketPromises": [entry]}, schema)

    promise = {"promise": NEW_TAB}
    assert not holds({**promise, "reading": "covered", "criteria": []}), "covered by nothing"
    assert not holds({**promise, "reading": "dropped"}), "dropped with no spec line"
    assert not holds({**promise, "reading": "dropped", "specLine": " "})
    assert not holds({**promise, "reading": "bent", "specLine": "7: opens it"}), "bent how"
    assert not holds({**promise, "reading": "assumed", "specLine": "Assumptions: any link"})
    assert not holds({**promise, "reading": "kept", "criteria": ["7"]})
    assert not holds({**promise, "reading": "covered", "criteria": ["7"], "specLine": "7"})
    assert holds({**promise, "reading": "dropped", "specLine": "Acceptance criteria: none"})
    assert holds({**promise, "reading": "bent", "specLine": "7: opens it", "change": "no tab"})
    seen = {"specLine": "Assumptions: any link", "ownerSees": "a blank item"}
    assert holds({**promise, "reading": "assumed", **seen})
    undecided = {k: v for k, v in LINK_FIELD.items() if k != "hostile"}
    assert not _valid({**REVIEW, "acceptedInputs": [undecided]}, schema), "a case left out"
    assert _valid({**REVIEW, "acceptedInputs": [{**LINK_FIELD, "hostile": None}]}, schema)


def test_a_spec_that_drops_a_ticket_promise_is_refused(tmp_path: Path) -> None:
    # Why: the spec is what the implementer builds and what the review judged the build against,
    # so a spec that left out a promise of the ticket was built to the letter, approved and merged.
    dropped = {"reading": "dropped", "specLine": "Acceptance criteria: no criterion names a tab"}
    result = _built({**REVIEW, "ticketPromises": _reread(NEW_TAB, dropped)})["result"]
    assert result["verdict"] == "request_changes"
    assert result["verdictForcedBy"] == ["1 blocker/major review issue(s)"]
    title = f'the spec drops the ticket\'s promise "{NEW_TAB}"'
    assert result["verdictRecord"]["unresolved"] == [title]
    assert [i["location"] for i in result["reviewIssues"]] == [dropped["specLine"]]

    root = repository(tmp_path, {"guards/manifest.toml": MANIFEST_TOML, "src/a.py": "x = 1\n"})
    branch(root, "feature/4-thing")
    write(root, "src/a.py", "x = 2\n")
    commit(root, "feat(thing): the change")
    record = {**result["verdictRecord"], "slug": "thing"}
    assert merge_ready.write_record(root, json.dumps(record)) == 0
    commit(root, "docs(thing): the verdict record")
    refused = " ".join(reason.text for reason in merge_ready.judge(root, "main", "HEAD"))
    assert title in refused


def test_a_bent_promise_blocks_and_an_assumed_one_goes_to_the_owner() -> None:
    bent = {"reading": "bent", "specLine": "7: opens it in place", "change": "no new tab"}
    result = _built({**REVIEW, "ticketPromises": _reread(NEW_TAB, bent)})["result"]
    assert result["verdictRecord"]["unresolved"] == [
        f'the spec bends the ticket\'s promise "{NEW_TAB}"'
    ]
    saved = "Saving a link answers `303` to the list"
    assumed = {
        "reading": "assumed",
        "specLine": "Ticket 01 saves whatever is pasted (no check that it is a web link)",
        "ownerSees": "a blank save makes an item with no title",
    }
    result = _built({**REVIEW, "ticketPromises": _reread(saved, assumed)})["result"]
    assert (result["verdict"], result["verdictRecord"]["unresolved"]) == ("approve", [])
    surfaced = {"question": saved, "assumed": assumed["specLine"], "refuter": assumed["ownerSees"]}
    assert result["assumptions"] == [*SPECIFIED["assumptions"], surfaced]
    assert result["verdictRecord"]["assumptions"] == result["assumptions"]


def test_an_input_case_no_criterion_decides_blocks() -> None:
    undecided = {**LINK_FIELD, "empty": None, "hostile": None}
    result = _built({**REVIEW, "acceptedInputs": [undecided]})["result"]
    assert result["verdict"] == "request_changes"
    assert result["verdictRecord"]["unresolved"] == [
        "no criterion says what happens to an empty value of the link field of the save form",
        "no criterion says what happens to a hostile value of the link field of the save form",
    ]


def test_the_second_review_reads_the_ticket_by_the_same_rules() -> None:
    dropped = {"reading": "dropped", "specLine": "Out of scope: client JavaScript"}
    review = {**REVIEW, "ticketPromises": _reread("It has no client JavaScript", dropped)}
    assumption = {"question": "q", "assumed": "a", "refuter": "r"}
    result = _again({"assumptions": [assumption]}, **{"review-again": review})["result"]
    assert result["verdict"] == "request_changes"
    assert result["verdictRecord"]["unresolved"] == [
        'the spec drops the ticket\'s promise "It has no client JavaScript"'
    ]
    assert result["verdictRecord"]["assumptions"] == [assumption]
