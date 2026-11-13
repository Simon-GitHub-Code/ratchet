"""The pipeline script's verdict helpers, run in node over the agents' reports."""

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

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
const logs = []
const agent = async (prompt, options) => {
  labels.push(options.label)
  prompts[options.label] = prompt
  return answers[options.label] ?? null
}
const run = new AsyncFunction('args', 'agent', 'phase', 'log', source)
run(args, agent, () => {}, line => logs.push(line)).then(
  result => console.log(JSON.stringify({ result, labels, prompts, logs })),
  error => console.log(JSON.stringify({ error: error.message, labels })),
)
"""
REVIEW = {
    "verdict": "approve",
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
