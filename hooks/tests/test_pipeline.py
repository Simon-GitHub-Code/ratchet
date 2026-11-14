"""The pipeline script's verdict helpers, run in node over the agents' reports."""

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

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
    "input": "the `link` field of `POST /items`",
    "plainName": "The link you paste",
    "empty": "16: a blank or missing link answers 400 and saves nothing",
    "oversized": "17: a link over 2048 characters answers 400 and saves nothing",
    "hostile": "9: markup characters show as text in the title and the href",
}
CHECKED = {"ticketPromises": PROMISES, "acceptedInputs": [LINK_FIELD]}
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
    """What the script returns for ``args`` when each agent answers ``answers[its label]``.

    The ticket check reads the faithful spec unless a test answers for it.
    """
    answers = {"ticket-check": CHECKED, **answers}
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
    told = result["requiredByOrchestrator"][0]
    assert "under `## Answers`" in told
    # Why: the skills put every open question of a step in one message, and the list a blocked
    # run hands the orchestrator still said "one at a time".
    assert "every open question to the owner in one message" in told
    assert "each with your recommended answer first and why" in told
    assert "one at a time" not in told
    prompt = ran["prompts"]["spec"]
    assert "Never ask the owner to confirm a choice" in prompt
    assert "When the user could not tell which option was built, you choose." in prompt
    assert "Read `## Answers` and the comments too" in prompt
    assert _run(None)["labels"] == [], "no ticket, no agent"


def test_a_clean_run_spawns_six_agents_and_records_no_question() -> None:
    answers = {"implement": IMPLEMENTED, "critique": CRITIQUED, "review": REVIEW}
    ran = _run({"ref": "4"}, preflight=PREFLIGHT, spec=SPECIFIED, **answers)
    assert ran["labels"] == ["preflight", "spec", "ticket-check", "implement", "critique", "review"]
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
        "ticket-check",
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
    assert ran["labels"] == ["preflight", "spec", "ticket-check", "implement", "critique", "review"]


def test_a_change_with_no_room_under_the_ceilings_stops_before_the_next_stage() -> None:
    # Why: a ticket whose spec tests and code already filled the ceiling ran nine agents and
    # ended red on change-size alone once the critic's tests were added. ADR 0045: product lines
    # and test lines have a ceiling each, and only the tests keep a margin for the critic's.
    answers = {"implement": IMPLEMENTED, "critique": CRITIQUED, "review": REVIEW}
    heavy = {
        **SPECIFIED,
        "changeSize": {"lines": 100, "ceiling": 1000, "testLines": 2001, "testCeiling": 3000},
    }
    ran = _run({"ref": "4"}, preflight=PREFLIGHT, spec=heavy, **answers)
    assert ran["labels"] == ["preflight", "spec"]
    result = ran["result"]
    assert (result["verdict"], result["tooLarge"]["stage"]) == ("blocked", "the spec")
    assert result["tooLarge"]["kind"] == "tests"
    assert "2001 test lines after the spec" in result["verdictForcedBy"][0]
    assert "orchestrator splits it" in result["requiredByOrchestrator"][0]
    assert "drain.py split" in result["requiredByOrchestrator"][1]

    fits = {
        **SPECIFIED,
        "changeSize": {"lines": 100, "ceiling": 1000, "testLines": 2000, "testCeiling": 3000},
    }
    built = {
        **IMPLEMENTED,
        "changeSize": {"lines": 1001, "ceiling": 1000, "testLines": 2000, "testCeiling": 3000},
    }
    ran = _run({"ref": "4"}, preflight=PREFLIGHT, spec=fits, **{**answers, "implement": built})
    assert ran["labels"] == ["preflight", "spec", "ticket-check", "implement"]
    assert ran["result"]["tooLarge"]["stage"] == "the implementation"
    assert ran["result"]["tooLarge"]["kind"] == "product"
    assert sorted(ran["result"]["filesChanged"]) == ["src/links.py", "tests/test_expiry.py"]

    full = {
        **SPECIFIED,
        "changeSize": {"lines": 1000, "ceiling": 1000, "testLines": 100, "testCeiling": 3000},
    }
    ran = _run({"ref": "4"}, preflight=PREFLIGHT, spec=full, **answers)
    assert ran["result"]["verdict"] == "approve", "product code may fill its whole ceiling"

    unbound = {
        **SPECIFIED,
        "changeSize": {"lines": 9, "ceiling": 0, "testLines": 9, "testCeiling": 0},
    }
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
    assert ran["labels"] == ["preflight", "spec", "ticket-check", "implement", "critique", "review"]
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
    sized = {
        **SPECIFIED,
        "changeSize": {"lines": 500, "ceiling": 1000, "testLines": 1500, "testCeiling": 3000},
    }
    ran = _run({"ref": "4"}, preflight=PREFLIGHT, spec={**sized, "changeEstimate": fits}, **answers)
    assert ran["result"]["verdict"] == "approve", "950 product and 1,800 test lines fit"

    over = {**fits, "implementationLines": 451, "proposedSplit": split}
    ran = _run({"ref": "4"}, preflight=PREFLIGHT, spec={**sized, "changeEstimate": over}, **answers)
    assert ran["labels"] == ["preflight", "spec"]
    result = ran["result"]
    assert (result["verdict"], result["tooLarge"]["proposedSplit"]) == ("blocked", split)
    assert "estimates 1001 product lines" in result["verdictForcedBy"][0]

    tests = {**fits, "criticLines": 1501, "proposedSplit": split}
    ran = _run(
        {"ref": "4"}, preflight=PREFLIGHT, spec={**sized, "changeEstimate": tests}, **answers
    )
    assert ran["result"]["tooLarge"]["kind"] == "tests"
    assert "estimates 3001 test lines" in ran["result"]["verdictForcedBy"][0]

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
    assert ran["labels"] == ["preflight", "spec", "ticket-check", "implement", "review"]
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
        assert ran["labels"] == [
            "preflight",
            "spec",
            "ticket-check",
            "implement",
            "critique",
            "review",
        ]
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


def test_without_the_critic_a_change_may_fill_the_whole_ceiling() -> None:
    # Why: an eval run's first ticket came back blocked at 1,067 lines of a 1,500 ceiling, "with
    # the review still to come", though no critic ran and the review's records are not counted.
    answers = {"implement": IMPLEMENTED, "review": REVIEW}
    spec = {
        **SPECIFIED,
        "changeSize": {"lines": 100, "ceiling": 1000, "testLines": 2500, "testCeiling": 3000},
    }
    built = {
        **IMPLEMENTED,
        "changeSize": {"lines": 100, "ceiling": 1000, "testLines": 3000, "testCeiling": 3000},
    }
    ran = _run({"ref": "4"}, preflight=YOUNG, spec=spec, **{**answers, "implement": built})
    assert ran["labels"] == ["preflight", "spec", "ticket-check", "implement", "review"]
    assert ran["result"]["verdict"] == "approve"
    assert "over two thirds" not in ran["prompts"]["spec"]
    old = {**PREFLIGHT, "critic": True, "critic_reason": "others rely on it"}
    ran = _run({"ref": "4"}, preflight=old, spec=spec)
    assert ran["result"]["tooLarge"]["stage"] == "the spec", (
        "with the critic two thirds of the test ceiling still holds"
    )
    assert "two thirds of the test ceiling the run stops" in ran["prompts"]["spec"]


def test_the_critics_share_of_the_size_is_not_counted_when_it_does_not_run() -> None:
    sized = {
        **SPECIFIED,
        "changeSize": {"lines": 100, "ceiling": 1000, "testLines": 2900, "testCeiling": 3000},
    }
    over = {"implementationLines": 400, "criticLines": 300, "recordLines": 50, "proposedSplit": []}
    answers = {"implement": IMPLEMENTED, "review": REVIEW}
    ran = _run({"ref": "4"}, preflight=YOUNG, spec={**sized, "changeEstimate": over}, **answers)
    assert ran["result"]["verdict"] == "approve", (
        "2,900 of 3,000 test lines without the critic's 300"
    )
    assert "plus the critic's tests" not in ran["prompts"]["spec"]
    assert "return criticLines: 0" in ran["prompts"]["spec"]
    heavy = {**SPECIFIED, "changeSize": {"lines": 1001, "ceiling": 1000}}
    ran = _run({"ref": "4"}, preflight=YOUNG, spec=heavy)
    assert "over the change-size ceiling of 1000" in ran["result"]["verdictForcedBy"][0]
    full = {**over, "implementationLines": 900}
    ran = _run({"ref": "4"}, preflight=YOUNG, spec={**sized, "changeEstimate": full})
    assert "no critic" not in ran["result"]["verdictForcedBy"][0], "a product stop names no critic"
    assert "records 50" in ran["result"]["verdictForcedBy"][0]


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


# --- the ticket is read against the spec before the build and against the change after it ------

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


def _built(review: dict[str, object], ref: str = "4", **answers: object) -> dict[str, Any]:
    answers = {"implement": IMPLEMENTED, "critique": CRITIQUED, "review": review, **answers}
    return _run({"ref": ref}, preflight=PREFLIGHT, spec=SPECIFIED, **answers)


def _schema(label: str) -> dict[str, Any]:
    schema: dict[str, Any] = _built(REVIEW)["schemas"][label]
    return schema


def _said(prompt: str) -> str:
    """``prompt`` with its line breaks and indents folded, as a reader takes the sentences."""
    return " ".join(prompt.split())


def _reread(promise: str, reading: dict[str, str]) -> list[dict[str, object]]:
    """The faithful promises, with ``promise`` read as ``reading`` instead."""
    return [p if p["promise"] != promise else {"promise": promise, **reading} for p in PROMISES]


NO_SCRIPT = "It has no client JavaScript"
SAVED = "Saving a link answers `303` to the list"
OUT_OF_SCOPE = {"reading": "dropped", "specLine": "Out of scope: client JavaScript"}
GAP = {**CHECKED, "ticketPromises": _reread(NO_SCRIPT, OUT_OF_SCOPE)}
TICKET_FILE = ".scratch/list/issues/01-save-a-link.md"
HEADER_IS_NO_INPUT = (
    "A header the change itself adds or checks to guard the product (an `Origin` check that"
    " refuses cross-site posts) is not one either: no user sends it as content."
)


def test_every_role_that_reads_a_local_ticket_reads_it_as_the_base_holds_it() -> None:
    # Why: an agent of the run can edit the ticket file in the working tree, and the check and the
    # review would then read the ticket it wrote instead of the one the owner agreed.
    answers = {"implement": IMPLEMENTED, "critique": CRITIQUED, "review": REVIEW}
    based = {**PREFLIGHT, "base": "main"}
    ran = _run({"ref": TICKET_FILE}, preflight=based, spec=SPECIFIED, **answers)
    shown = f"(`git show main:{TICKET_FILE}`, run from the repository root"
    for role in ("spec", "ticket-check", "review"):
        assert shown in ran["prompts"][role], role
        assert "not the copy in the working tree" in ran["prompts"][role], role
    rejudge = {"ref": TICKET_FILE, "base": "main", "branch": "feature/4-expiry", "rejudge": REJUDGE}
    assert shown in _run(rejudge, **{"review-again": REVIEW})["prompts"]["review-again"]
    unbased = _built(REVIEW, TICKET_FILE)["prompts"]["review"]
    inputs = unbased.split("Inputs you may read")[1].split("Load the")[0]
    assert f"The ticket: the file `{TICKET_FILE}` under the repository root" in inputs
    assert "`## Answers`" in inputs
    assert "the ticket are outside your inputs" not in unbased
    tracker = "`gh issue view 4 --json number,title,body,labels,comments`"
    for role in ("spec", "ticket-check", "review"):
        assert tracker in _built(REVIEW)["prompts"][role], role
    assert tracker in _again({}, **{"review-again": REVIEW})["prompts"]["review-again"]


def test_the_ticket_check_reads_the_ticket_and_the_spec_and_nothing_else() -> None:
    prompt = _built(REVIEW)["prompts"]["ticket-check"]
    assert "Read these two and nothing else (no code, no tests, no other record)" in prompt
    assert "- The spec: `docs/features/expiry/spec.md`" in prompt
    assert "run `check`" not in prompt
    assert "what a gate charges" in prompt, "a sentence about the method's work is no promise"


def test_the_check_and_the_review_answer_the_same_two_axes() -> None:
    check, review = _schema("ticket-check"), _schema("review")
    assert check["required"] == ["ticketPromises", "acceptedInputs"]
    for axis in check["required"]:
        assert check["properties"][axis] == review["properties"][axis], axis
        assert axis in review["required"], axis
    prompts = _built(REVIEW)["prompts"]
    for role in ("ticket-check", "review"):
        assert "- `dropped`: no criterion carries it;" in prompts[role], role
        assert "an assumption decides no case." in prompts[role], role
        # Why: one of two checks counted a header the plan checked for its own cross-site
        # refusal as an input, and its undecided cases as gaps.
        assert HEADER_IS_NO_INPUT in _said(prompts[role]), role


def test_the_schema_forces_an_answer_on_each_axis_and_cannot_force_a_whole_one() -> None:
    for label, answer in (("ticket-check", CHECKED), ("review", REVIEW)):
        schema = _schema(label)
        assert _valid(answer, schema), label
        for axis in ("ticketPromises", "acceptedInputs"):
            assert not _valid({k: v for k, v in answer.items() if k != axis}, schema), axis
        assert not _valid({**answer, "ticketPromises": []}, schema), (
            "every ticket promises something"
        )
        assert _valid({**answer, "acceptedInputs": []}, schema), "a change may accept nothing"
        assert _valid({**answer, "ticketPromises": PROMISES[:1]}, schema), (
            "it cannot know the ticket"
        )


def test_each_reading_of_a_promise_carries_what_shows_it() -> None:
    schema = _schema("review")

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
    blank = {**LINK_FIELD, "empty": ""}
    assert not _valid({**REVIEW, "acceptedInputs": [blank]}, schema), "a blank decides nothing"


def test_a_gap_the_check_finds_is_amended_once_and_the_run_goes_on() -> None:
    # Why: a gap found by the review after the build blocked a change nothing could repair: no
    # role after the review fixes the spec, and the stash went to an owner who cannot read code.
    tests = ["tests/test_expiry.py", "tests/test_no_script.py"]
    failures = [{"testFile": f, "content": "AssertionError"} for f in tests]
    amended = {**SPECIFIED, "testFiles": tests, "failures": failures}
    gap = {**GAP, "acceptedInputs": [{**LINK_FIELD, "oversized": None}]}
    answers = {"ticket-check": gap, "spec-amend": amended, "ticket-recheck": CHECKED}
    ran = _built(REVIEW, **answers)
    assert ran["labels"] == [
        "preflight",
        "spec",
        "ticket-check",
        "spec-amend",
        "ticket-recheck",
        "implement",
        "critique",
        "review",
    ]
    amend = ran["prompts"]["spec-amend"]
    assert "This is an AMEND." in amend
    assert f'"promise": "{NO_SCRIPT}"' in amend
    assert '"case": "oversized"' in amend
    assert "This is an AMEND." not in ran["prompts"]["spec"]
    assert "tests/test_no_script.py" in ran["prompts"]["implement"], "the amended spec is built"
    result = ran["result"]
    assert (result["verdict"], result["testFiles"]) == ("approve", tests)
    assert result["assumptions"] == SPECIFIED["assumptions"], "the amend closed every gap"


def test_a_promise_the_amend_still_leaves_out_is_kept_from_the_ticket_and_asks_nothing() -> None:
    # Why: the script asked the owner to confirm what his ticket already says, with "build it as
    # the ticket says" as the answer he had to pick, and stopped him once on a promise the plan
    # kept. A promise still left out is built as the ticket says it, and listed for him to overrule.
    left_out = {"promise": NO_SCRIPT, **OUT_OF_SCOPE}
    promises = [left_out if p["promise"] == NO_SCRIPT else p for p in PROMISES]
    gap = {"ticketPromises": promises, "acceptedInputs": [LINK_FIELD]}
    ran = _built(REVIEW, **{"ticket-check": gap, "spec-amend": SPECIFIED, "ticket-recheck": gap})
    assert ran["labels"][-5:] == ["spec-amend", "ticket-recheck", "implement", "critique", "review"]
    result = ran["result"]
    assert (result["verdict"], result["openQuestions"]) == ("approve", [])
    kept = {
        "question": NO_SCRIPT,
        "assumed": "the plan left this out, so it was built as your ticket says",
        "refuter": "Wrong if you no longer want what your ticket says here",
    }
    assert result["assumptions"] == [*SPECIFIED["assumptions"], kept]
    assert result["verdictRecord"]["assumptions"] == result["assumptions"]
    reading = {
        "promise": NO_SCRIPT,
        "reading": "covered",
        "criteria": [f"Kept from the ticket by the run, as no criterion carries it: {NO_SCRIPT}"],
    }
    assert "settled" in result, "the result returns the readings it settled"
    assert reading in result["settled"]["ticketPromises"], "the settled reading, in its words"
    assert left_out not in result["settled"]["ticketPromises"]
    built = _said(ran["prompts"]["implement"])
    assert "the run keeps them as the ticket says them. Build each, with a test that fails" in built
    assert f'"{NO_SCRIPT}"' in built
    judged = _said(ran["prompts"]["review"])
    assert json.dumps(reading["criteria"][0]) in judged
    assert (
        'A promise whose reading starts "Kept from the ticket by the run, as no criterion carries'
        ' it:" is one the run kept from the ticket: the reading carries it, not spec.md, and it is'
        " dropped only when the tests and the code do not do what the ticket says."
    ) in judged
    plain = _built(REVIEW)
    assert "the run keeps them as the ticket says" not in plain["prompts"]["implement"]
    assert plain["result"]["settled"] == CHECKED, "nothing left out, nothing changed"


def test_a_promise_the_spec_role_will_not_build_is_its_question_never_a_silent_drop() -> None:
    raised = (
        "A promise of the ticket you believe cannot or should not be built as it says is an open"
        " question under the fork test below, never one you leave out: the run builds a promise"
        " the plan leaves out as the ticket says it, and asks the owner nothing."
    )
    ran = _built(REVIEW, **{"ticket-check": GAP, "spec-amend": SPECIFIED, "ticket-recheck": GAP})
    assert raised in _said(ran["prompts"]["spec"])
    note = (
        ran["prompts"]["spec-amend"].split("This is an AMEND.")[1].split("Inputs you may read")[0]
    )
    assert raised in _said(note), "the amend's own instructions say it, beside the promise"
    assert "is that what you want" not in json.dumps(ran)


def test_the_spec_role_designs_the_placement_and_the_review_judges_it_on_axis_f() -> None:
    # Why: no role used a design skill, so a change could pass every axis and the gates while
    # bypassing a seam or placing logic in the wrong layer, with nothing the owner reads to show it.
    ran = _built(REVIEW)
    spec = _said(ran["prompts"]["spec"])
    assert "mattpocock-skills:codebase-design" in spec
    assert "Design: how the feature fits the project's architecture" in spec
    assert "architecture ADR" in spec
    assert "the layers AGENTS.md declares" in spec
    assert "a new layer, a new kind of storage, a new process boundary" in spec
    assert "open question of kind `direction`" in spec.split("A change that implies")[1]
    review = _said(ran["prompts"]["review"])
    assert "mattpocock-skills:codebase-design" in review
    axis = review.split("F. Design.")[1].split("The script files every")[0]
    assert "A layer violation is a blocker" in axis
    assert "shallow wrapper" in axis
    assert "are minor" in axis
    issue = ran["schemas"]["review"]["properties"]["issues"]["items"]
    assert "design" in issue["properties"]["axis"]["enum"]


# What the owner reads in the pull request for a case the run assumed: the case in his words, the
# safe answer that was built, and what would show it wrong.
ASSUMED_CASES = {
    "empty": (
        "left empty",
        "nothing is saved and nothing breaks",
        "an empty value is something a user means to send, such as clearing a note",
    ),
    "oversized": (
        "far longer than anyone would type",
        "the app refuses it with a message, saves nothing and keeps what the user typed",
        "a user needs to send a value that long",
    ),
    "hostile": (
        "made to cause harm, such as hidden code",
        (
            "the app keeps it as plain text that does nothing: shown as text, never run,"
            " followed or used as a path"
        ),
        "a user needs the value to run or be followed as sent",
    ),
}


@pytest.mark.parametrize("case", list(ASSUMED_CASES))
def test_an_input_case_the_amend_leaves_undecided_is_built_safe_and_asks_nothing(case: str) -> None:
    # Why: the spec prompt makes a limit with one plainly safe value the agent's to assume, and
    # never asks the owner to confirm a choice; the script asked him anyway.
    words, safe, refuter = ASSUMED_CASES[case]
    gap = {**CHECKED, "acceptedInputs": [{**LINK_FIELD, case: None}]}
    answers = {"ticket-check": gap, "spec-amend": SPECIFIED, "ticket-recheck": gap}
    ran = _built(REVIEW, **answers)
    assert ran["labels"][-3:] == ["implement", "critique", "review"]
    result = ran["result"]
    assert (result["verdict"], result["openQuestions"]) == ("approve", [])
    assumed = {
        "question": f"What the app does when the link you paste is {words}",
        "assumed": safe,
        "refuter": f"Wrong if {refuter}",
    }
    assert result["assumptions"] == [*SPECIFIED["assumptions"], assumed]
    assert result["verdictRecord"]["assumptions"] == result["assumptions"]
    built = _said(ran["prompts"]["implement"])
    assert "the run took the safe answer for each" in built
    assert f'"assumed": "{safe}"' in built
    judged = _said(ran["prompts"]["review"])
    assert f'"{case}": "Assumed by the run, as no criterion decides it: {safe}"' in judged
    assert f'"{case}": null' not in judged
    assert "as that reading states" in judged
    assert "or as the settled reading states where the run assumed it." in judged
    assert (
        "the run took the safe answer for each. Build each as its `assumed` says, with a test that"
        " fails without it"
    ) in built
    plain = _said(_built(REVIEW)["prompts"]["implement"])
    assert "the run took the safe answer" not in plain


def test_an_input_case_that_is_a_product_choice_is_the_spec_roles_question() -> None:
    # Why: the script asks the owner nothing about an input, so a case whose safe answer takes
    # something from the user reaches him only as the spec role's direction question.
    choice = (
        "An input case where the safe answer takes something from the user (refusing an empty"
        " value when an empty one clears a note, or a limit that loses what they typed) is a real"
        " product choice: raise it as a `direction` open question under the fork test below,"
        " since the script asks the owner nothing about inputs."
    )
    ran = _built(REVIEW, **{"ticket-check": GAP, "spec-amend": SPECIFIED, "ticket-recheck": GAP})
    for role in ("spec", "spec-amend"):
        assert choice in _said(ran["prompts"][role]), role


def test_the_amend_closes_each_gap_with_the_smallest_safe_change() -> None:
    # Why: in all eight sampled amends the plan brought a later ticket's rule forward to close
    # a gap, and an amend that rewrites the rest of the plan is a new plan nobody checked.
    ran = _built(REVIEW, **{"ticket-check": GAP, "spec-amend": SPECIFIED, "ticket-recheck": GAP})
    amend = _said(ran["prompts"]["spec-amend"])
    for rule in (
        (
            "Close each gap with the smallest change that makes the plan do what this ticket says,"
            " or that makes an accepted input safe. Change nothing the gaps do not touch: leave"
            " every other criterion's wording exactly as it is, not split, joined or reworded."
        ),
        (
            "An input case: decide it yourself with the safe answer, the narrowest that keeps the"
            " app safe (an empty value: nothing is saved and nothing breaks; an oversized one: the"
            " app refuses it with a message and keeps what the user typed; a hostile one: kept as"
            " plain text that does nothing)"
        ),
        "Never ask the owner to confirm it.",
        (
            "For an empty value the safe answer is nothing saved and nothing broken, with no"
            " message or rule of its own: a message or a rule is a product another ticket may"
            " promise."
        ),
        (
            "Never bring forward what another ticket of this feature promises (its rule, its"
            " check, its message): when the smallest safe answer overlaps another ticket, take the"
            " narrower answer this ticket needs and name that ticket in the assumption"
        ),
    ):
        assert rule in amend, rule
    spec = _said(ran["prompts"]["spec"])
    assert "Close each gap" not in spec
    assert "Decide each such case the ticket leaves open with the safe answer, the" in spec
    assert (
        "Every promise the ticket makes about what the product does has a criterion, a sentence"
        " in its Problem or What to build included. For each thing users or other systems send the"
        " running product (a form field, a request body, a file), criteria say what happens to an"
        " empty, an oversized and a hostile value."
    ) in spec


def test_an_amend_that_asks_the_owner_stops_the_run_there() -> None:
    asked = {**SPECIFIED, "openQuestions": [ASKED]}
    ran = _built(REVIEW, **{"ticket-check": GAP, "spec-amend": asked})
    assert ran["labels"] == ["preflight", "spec", "ticket-check", "spec-amend"]
    assert (ran["result"]["verdict"], ran["result"]["openQuestions"]) == ("blocked", [ASKED])


def test_a_reading_the_check_and_the_spec_role_do_not_share_is_an_assumption() -> None:
    # Why: one of three readings of a faithful spec took a sentence about what a gate charges for
    # a promise of the product the spec bent.
    charge = "Each is the first of its kind and free"
    bent = {
        "promise": charge,
        "reading": "bent",
        "specLine": "Placement: no criterion names a charge",
        "change": "the spec says nothing of what the gate charges",
    }
    read = {**CHECKED, "ticketPromises": [*PROMISES, bent]}
    ran = _built(REVIEW, **{"ticket-check": read, "spec-amend": SPECIFIED, "ticket-recheck": read})
    assert ran["labels"][-5:] == ["spec-amend", "ticket-recheck", "implement", "critique", "review"]
    result = ran["result"]
    assert (result["verdict"], result["verdictRecord"]["unresolved"]) == ("approve", [])
    surfaced = {"question": charge, "assumed": bent["specLine"], "refuter": bent["change"]}
    assert result["assumptions"] == [*SPECIFIED["assumptions"], surfaced]
    assert result["verdictRecord"]["assumptions"] == result["assumptions"]


def test_a_promise_the_spec_decided_for_the_owner_starts_no_amend_and_is_listed_once() -> None:
    assumed = {
        "reading": "assumed",
        "specLine": "Ticket 01 saves whatever is pasted (no check that it is a web link)",
        "ownerSees": "a blank save makes an item with no title",
    }
    read = {**CHECKED, "ticketPromises": _reread(SAVED, assumed)}
    ran = _built({**REVIEW, "ticketPromises": read["ticketPromises"]}, **{"ticket-check": read})
    assert "spec-amend" not in ran["labels"]
    result = ran["result"]
    assert (result["verdict"], result["verdictRecord"]["unresolved"]) == ("approve", [])
    surfaced = {"question": SAVED, "assumed": assumed["specLine"], "refuter": assumed["ownerSees"]}
    assert result["assumptions"] == [*SPECIFIED["assumptions"], surfaced], "check and review, once"
    assert result["verdictRecord"]["assumptions"] == result["assumptions"]


def test_the_review_starts_from_the_readings_the_check_settled() -> None:
    gap = {**GAP, "acceptedInputs": [{**LINK_FIELD, "oversized": None}]}
    settled = {**CHECKED, "acceptedInputs": [{**LINK_FIELD, "oversized": "17: a settled length"}]}
    answers = {"ticket-check": gap, "spec-amend": SPECIFIED, "ticket-recheck": settled}
    prompt = _built(REVIEW, **answers)["prompts"]["review"]
    assert "They are decided: do not argue them again." in prompt
    assert (
        "D. The ticket. Only the spec role and the ticket check read it before you, so a change"
        " that loses what the owner asked for can pass every other axis."
    ) in _said(prompt)
    assert '"oversized": "17: a settled length"' in prompt
    again = _again({}, **{"review-again": REVIEW})["prompts"]["review-again"]
    assert "do not argue them again" not in again


def test_a_change_that_drops_a_ticket_promise_is_refused(tmp_path: Path) -> None:
    # Why: the spec is what the implementer builds and what the review judged the build against,
    # so a promise of the ticket the build lost was approved and merged.
    lost = {"reading": "dropped", "specLine": "Acceptance criteria: no criterion names a tab"}
    result = _built({**REVIEW, "ticketPromises": _reread(NEW_TAB, lost)})["result"]
    assert result["verdict"] == "request_changes"
    assert result["verdictForcedBy"] == ["1 blocker/major review issue(s)"]
    title = f'the change drops the ticket\'s promise "{NEW_TAB}"'
    assert result["verdictRecord"]["unresolved"] == [title]
    assert [i["location"] for i in result["reviewIssues"]] == [lost["specLine"]]
    assert result["ticketFindings"] == result["reviewIssues"]

    root = repository(tmp_path, {"guards/manifest.toml": MANIFEST_TOML, "src/a.py": "x = 1\n"})
    branch(root, "feature/4-thing")
    write(root, "src/a.py", "x = 2\n")
    commit(root, "feat(thing): the change")
    record = {**result["verdictRecord"], "slug": "thing"}
    assert merge_ready.write_record(root, json.dumps(record)) == 0
    commit(root, "docs(thing): the verdict record")
    refused = " ".join(reason.text for reason in merge_ready.judge(root, "main", "HEAD"))
    assert title in refused


def test_a_bent_reading_at_the_review_holds_no_merge() -> None:
    bent = {"reading": "bent", "specLine": "7: opens it in place", "change": "no new tab"}
    result = _built({**REVIEW, "ticketPromises": _reread(NEW_TAB, bent)})["result"]
    assert (result["verdict"], result["verdictRecord"]["unresolved"]) == ("approve", [])
    assert result["ticketFindings"] == []
    surfaced = {"question": NEW_TAB, "assumed": bent["specLine"], "refuter": bent["change"]}
    assert result["assumptions"] == [*SPECIFIED["assumptions"], surfaced]


@pytest.mark.parametrize("case", list(ASSUMED_CASES))
def test_an_input_case_the_change_leaves_undecided_blocks(case: str) -> None:
    words = ASSUMED_CASES[case][0]
    result = _built({**REVIEW, "acceptedInputs": [{**LINK_FIELD, case: None}]})["result"]
    assert result["verdict"] == "request_changes"
    title = f"the change does not say what the app does when the link you paste is {words}"
    assert result["verdictRecord"]["unresolved"] == [title]
    assert [i["title"] for i in result["ticketFindings"]] == [title]


def test_a_run_after_a_lost_promise_hands_the_spec_role_what_was_lost() -> None:
    # Why: a rerun's spec role never saw why the last run was refused, so it wrote the same spec.
    lost = {"reading": "dropped", "specLine": "15: the page holds no script element"}
    first = _built({**REVIEW, "ticketPromises": _reread(NO_SCRIPT, lost)})["result"]
    assert first["verdict"] == "request_changes"
    findings = first["ticketFindings"]
    answers = {"implement": IMPLEMENTED, "critique": CRITIQUED, "review": REVIEW}
    args = {"ref": "4", "ticketFindings": findings}
    again = _run(args, preflight=PREFLIGHT, spec=SPECIFIED, **answers)["prompts"]["spec"]
    assert "its review refused the change because the build lost these" in again
    assert '"location": "15: the page holds no script element"' in again
    assert "its review refused the change" not in _built(REVIEW)["prompts"]["spec"]


def test_the_second_review_reads_the_ticket_by_the_same_rules() -> None:
    # Why: on a rejudge the skill passes back the first run's assumptions, the ones the first
    # review surfaced among them, so the second review's readings must not list one twice.
    first = {"question": NEW_TAB, "assumed": "7: a new tab", "refuter": "a blank item"}
    given = [{"question": "q", "assumed": "a", "refuter": "r"}, first]
    seen = {"reading": "assumed", "specLine": "Assumptions: any text", "ownerSees": "an empty item"}
    again = {"reading": "assumed", "specLine": "7: a new tab", "ownerSees": "a blank item"}
    readings = {NO_SCRIPT: OUT_OF_SCOPE, SAVED: seen, NEW_TAB: again}
    promises = [
        p if p["promise"] not in readings else {"promise": p["promise"], **readings[p["promise"]]}
        for p in PROMISES
    ]
    result = _again(
        {"assumptions": given}, **{"review-again": {**REVIEW, "ticketPromises": promises}}
    )["result"]
    assert result["verdict"] == "request_changes"
    title = f'the change drops the ticket\'s promise "{NO_SCRIPT}"'
    assert result["verdictRecord"]["unresolved"] == [title]
    assert [i["title"] for i in result["reviewIssues"]] == [title]
    surfaced = {"question": SAVED, "assumed": seen["specLine"], "refuter": seen["ownerSees"]}
    assert result["assumptions"] == [*given, surfaced], "each promise once"
    assert result["verdictRecord"]["assumptions"] == result["assumptions"]


def test_a_rejudge_holds_the_review_to_the_readings_the_first_run_settled() -> None:
    # Why: the second review was told nothing of a case the first run assumed, read it as
    # undecided and refused the change in 3 of 3 real runs, and the drain parked a ticket the
    # owner had nothing to answer for.
    gap = {**CHECKED, "acceptedInputs": [{**LINK_FIELD, "empty": None}]}
    answers = {"ticket-check": gap, "spec-amend": SPECIFIED, "ticket-recheck": gap}
    first = _built(REVIEW, **answers)["result"]
    assert "settled" in first, "the first result returns the readings it settled"
    reading = "Assumed by the run, as no criterion decides it: nothing is saved and nothing breaks"
    assert first["settled"]["acceptedInputs"][0]["empty"] == reading
    rejudge = {"settled": first["settled"], "assumptions": first["assumptions"]}
    kept = {**REVIEW, "acceptedInputs": first["settled"]["acceptedInputs"]}
    ran = _again(rejudge, **{"review-again": kept})
    prompt = _said(ran["prompts"]["review-again"])
    assert "They are decided: do not argue them again." in prompt
    assert f'"empty": "{reading}"' in prompt
    assert "or as the settled reading states where the run assumed it." in prompt
    result = ran["result"]
    assert (result["verdict"], result["verdictRecord"]["unresolved"]) == ("approve", [])
    assert result["assumptions"] == first["assumptions"]
    feature = (PIPELINE.parents[1] / "skills/feature/SKILL.md").read_text(encoding="utf-8")
    assert '"settled": <the first result\'s settled>' in feature, "the skill passes them back"
    assert "dependencies?, assumptions?, settled? }" in PIPELINE.read_text(encoding="utf-8")


def test_the_owner_reads_each_input_by_its_plain_name() -> None:
    # Why: an assumption read "What the app does when The `POST /items` request body as a whole
    # (the form post carrying `link`) is far longer than anyone would type".
    asked = (
        "Give each a `plainName`: what a person who cannot read code calls it, in a few everyday"
        " words and no code terms"
    )
    prompts = _built(REVIEW)["prompts"]
    for role in ("ticket-check", "review"):
        assert asked in _said(prompts[role]), role
    schema = _schema("ticket-check")
    unnamed = {k: v for k, v in LINK_FIELD.items() if k != "plainName"}
    coded, blank = {**LINK_FIELD, "plainName": "the `link` field"}, {**LINK_FIELD, "plainName": " "}
    for entry in (unnamed, coded, blank):
        assert not _valid({**CHECKED, "acceptedInputs": [entry]}, schema), entry
    named = {**LINK_FIELD, "plainName": "URL you paste.", "oversized": None}
    gap = {**CHECKED, "acceptedInputs": [named]}
    ran = _built(REVIEW, **{"ticket-check": gap, "spec-amend": SPECIFIED, "ticket-recheck": gap})
    questions = [a["question"] for a in ran["result"]["assumptions"]]
    assert "What the app does when URL you paste is far longer than anyone would type" in questions


def test_the_drain_counts_every_agent_a_run_can_spawn() -> None:
    # Why: the drain states its cost before it starts, from the number the script declares.
    worst = {"ticket-check": GAP, "spec-amend": SPECIFIED, "ticket-recheck": CHECKED}
    worst |= {"implement": IMPLEMENTED, "review": REVIEW}
    young = _run({"ref": "4"}, preflight=YOUNG, spec=SPECIFIED, **worst)["labels"]
    assert len(young) == drain.YOUNG_PIPELINE_AGENTS
    owed = {**CRITIQUED, "findings": [{"severity": "major", "title": "open"}]}
    rounds = {"critique": owed, "fix-1": IMPLEMENTED, "critique-1": owed}
    rounds |= {"fix-2": IMPLEMENTED, "critique-2": owed}
    depended_on = _run({"ref": "4"}, preflight=PREFLIGHT, spec=SPECIFIED, **worst, **rounds)
    assert len(depended_on["labels"]) == drain.PIPELINE_AGENTS
    rebased = _again(
        {"conflicts": ["flags.toml"]}, reconcile=IMPLEMENTED, **{"review-again": REVIEW}
    )
    assert len(rebased["labels"]) == drain.UPDATE_AGENTS
