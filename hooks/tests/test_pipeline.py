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
