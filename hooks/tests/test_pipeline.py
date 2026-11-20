import functools
import json
import re
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

import adapter
import drain
import merge_ready
import publish_check
import quotes
from tests.repos import branch, commit, git, repository, write
from tests.support import PLUGIN_ROOT

PIPELINE = Path(__file__).resolve().parents[2] / "workflows/pipeline.js"
MANIFEST_TOML = '[[group]]\nname = "g"\nreason = "Because it is."\npaths = ["lint.toml"]\n'


PLUGIN_PY = "python3 -E -s -B -X pycache_prefix=/nonexistent/ratchet-no-bytecode"
RULE = "A change never loses a user's data."
RECORDS = {"AGENTS.md": [quotes.Document(f"# Rules\n\n{RULE}\n", markdown=True)]}
BREAKS = {"record": "AGENTS.md", "quote": RULE}
NOTHING_CHECKED = json.dumps({"found": [], "not_found": []})


def _quoted(answer: dict[str, Any], key: str = "findings") -> dict[str, Any]:
    listed = [
        {**finding, "breaks": finding.get("breaks", BREAKS)}
        if finding.get("severity") in {"blocker", "major"}
        else finding
        for finding in answer[key]
    ]
    printed = quotes.check(listed, Path(), RECORDS)
    return {**answer, key: listed, "quoteOutput": json.dumps(printed)}


def _paths(call: str, *arguments: object) -> list[str]:
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
NAMED = {"record": "ticket", "quote": "Saving a link answers `303` to the list"}
REVIEWED_LINK = {**LINK_FIELD, "named": NAMED}
CHECKED = {"ticketPromises": PROMISES, "acceptedInputs": [LINK_FIELD]}
REVIEW = {
    "verdict": "approve",
    "ticketPromises": PROMISES,
    "acceptedInputs": [REVIEWED_LINK],
    "issues": [],
    "adrSuggested": "",
    "guardChanges": [],
    "disputeCoverage": [],
    "dependencyNotes": [],
    "recordNotes": [],
    "checkPasses": True,
    "namedGatesRun": [],
    "unstated": [],
    "quoteOutput": NOTHING_CHECKED,
    "checkOutput": "check: green",
}
IMPLEMENTED = {
    "filesChanged": ["src/links.py"],
    "allNewTestsPass": True,
    "checkPasses": True,
    "namedGatesRun": [],
    "checkOutput": "check: green",
    "disputes": [],
    "unstated": [],
    "departures": [],
    "notes": "",
}
PREFLIGHT = {
    "ok": True,
    "slug": "expiry",
    "branch": "feature/4-expiry",
    "number": 4,
    "title": "T",
    "label": "ready-for-agent",
    "failures": [],
    "output": "preflight: green",
}
SPECIFIED = {
    "slug": "expiry",
    "specPath": "docs/features/expiry/spec.md",
    "namedGates": [],
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
    "mutationSummary": "not applicable: the binding names no mutation gate",
    "fuzzSummary": "not applicable: no value type is touched",
    "unstated": [],
    "quoteOutput": NOTHING_CHECKED,
    "checkOutput": "1 passed",
}
REJUDGE = {"slug": "expiry", "specPath": "docs/features/expiry/spec.md"}
ASKED = {
    "question": "Does an expired link say so, or send the visitor home?",
    "kind": "direction",
    "options": ["A page that says it expired", "The home page"],
    "recommendation": "The brief says a visitor is never left guessing.",
}


def _run(args: object, **answers: object) -> dict[str, Any]:
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
    staged = "stages it:\n\n      git add -- 'flags.toml'\n\nDo this"
    assert staged in ran["prompts"]["reconcile"], "one line per path, the empty one dropped"
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
    review = _quoted(
        {**REVIEW, "issues": [finding], "guardChanges": ["pyproject.toml"], "checkPasses": False},
        "issues",
    )
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
    assert "every open question to the owner in one message" in told
    assert "each with your recommended answer first and why" in told
    assert "one at a time" not in told
    prompt = ran["prompts"]["spec"]
    assert "Never ask the owner to confirm a choice" in prompt
    assert "When the user could not tell which option was built, you choose." in prompt
    assert "Read `## Answers` and the comments too" in prompt
    assert _run(None)["labels"] == [], "no ticket, no agent"


def test_a_run_with_no_ticket_is_answered_with_the_command_to_run_and_never_throws() -> None:
    for args in (None, {}, {"ref": "  "}, "  "):
        ran = _run(args)
        assert "error" not in ran, args
        assert ran["labels"] == [], args
        assert ran["result"]["verdict"] == "blocked", args
        assert ran["result"]["preflight"]["failures"][0]["check"] == "ticket", args
        assert "`/ratchet:feature <ticket>`" in ran["result"]["requiredByOrchestrator"][0], args


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


OVER = {"lines": 1001, "ceiling": 1000, "testLines": 2500, "testCeiling": 3000}


def test_a_fix_round_that_takes_the_product_code_over_its_ceiling_stops_the_run() -> None:
    finding = {"severity": "major", "title": "no expiry", "testFails": True}
    kept = _quoted({**CRITIQUED, "findings": [finding], "testsAdded": ["tests/test_method.py"]})
    answers = {"implement": IMPLEMENTED, "critique": kept, "review": REVIEW}
    answers |= {"critique-1": CRITIQUED}
    fits = {"lines": 1000, "ceiling": 1000, "testLines": 3000, "testCeiling": 3000}
    over = {"fix-1": {**IMPLEMENTED, "changeSize": OVER}}
    ran = _run({"ref": "4"}, preflight=PREFLIGHT, spec=SPECIFIED, **answers, **over)
    assert ran["labels"][-1] == "fix-1", "no re-critique and no review follow"
    result = ran["result"]
    assert (result["verdict"], result["tooLarge"]["stage"]) == ("blocked", "fix round 1")
    assert (result["tooLarge"]["kind"], result["tooLarge"]["lines"]) == ("product", 1001)
    assert "1001 product lines after fix round 1" in result["verdictForcedBy"][0]
    assert "drain.py split" in result["requiredByOrchestrator"][1]
    assert "change-size" in ran["prompts"]["fix-1"]
    room = {"fix-1": {**IMPLEMENTED, "changeSize": fits}}
    ran = _run({"ref": "4"}, preflight=PREFLIGHT, spec=SPECIFIED, **answers, **room)
    assert ran["result"]["verdict"] == "approve", "the whole of both ceilings is room"


def test_a_reconcile_that_takes_the_product_code_over_its_ceiling_stops_before_the_review() -> None:
    reconciled = {**IMPLEMENTED, "filesChanged": ["flags.toml"], "changeSize": OVER}
    ran = _again({"conflicts": ["flags.toml"]}, reconcile=reconciled, **{"review-again": REVIEW})
    assert ran["labels"] == ["reconcile"]
    result = ran["result"]
    assert (result["verdict"], result["tooLarge"]["stage"]) == ("blocked", "the reconcile")
    assert result["rejudged"] is True, "a rejudge that stops as too large is still a rejudge"
    assert result["filesChanged"] == ["flags.toml"]
    assert "change-size" in ran["prompts"]["reconcile"]
    fits = {**reconciled, "changeSize": {**OVER, "lines": 1000}}
    ran = _again({"conflicts": ["flags.toml"]}, reconcile=fits, **{"review-again": REVIEW})
    assert ran["labels"] == ["reconcile", "review-again"]


def test_a_finding_whose_kept_test_passes_starts_no_fix_round() -> None:
    held = {
        "severity": "major",
        "title": "rejects a past expiry",
        "testFails": False,
        "heldByKeptTest": True,
    }
    tests = ["tests/critique/expiry.core.critique.test.py"]
    kept = _quoted({**CRITIQUED, "findings": [held], "testsAdded": tests})
    answers = {"implement": IMPLEMENTED, "critique": kept, "review": REVIEW}
    ran = _run({"ref": "4"}, preflight=PREFLIGHT, spec=SPECIFIED, **answers)
    assert ran["labels"] == ["preflight", "spec", "ticket-check", "implement", "critique", "review"]
    result = ran["result"]
    assert (result["verdict"], result["unresolvedBlockingFindings"]) == ("approve", [])
    assert [f["title"] for f in result["criticFindings"]] == [held["title"]], "the review reads it"

    open_major = {**held, "heldByKeptTest": False}
    answers |= {"critique": _quoted({**kept, "findings": [open_major]}), "fix-1": IMPLEMENTED}
    answers |= {"critique-1": CRITIQUED}
    ran = _run({"ref": "4"}, preflight=PREFLIGHT, spec=SPECIFIED, **answers)
    assert "fix-1" in ran["labels"], "no kept test, still owed"

    failing = {**held, "testFails": True}
    answers |= {"critique": _quoted({**kept, "findings": [failing]})}
    ran = _run({"ref": "4"}, preflight=PREFLIGHT, spec=SPECIFIED, **answers)
    assert "fix-1" in ran["labels"], "a failing kept test is owed whatever the flag says"


def test_a_held_finding_is_not_offered_to_the_fix_agent_as_a_minor() -> None:
    held = {"severity": "major", "title": "held", "testFails": False, "heldByKeptTest": True}
    open_major = {"severity": "major", "title": "open", "testFails": False, "heldByKeptTest": False}
    kept = _quoted({**CRITIQUED, "findings": [held, open_major]})
    answers = {"implement": IMPLEMENTED, "critique": kept, "review": REVIEW}
    answers |= {"fix-1": IMPLEMENTED, "critique-1": CRITIQUED}
    ran = _run({"ref": "4"}, preflight=PREFLIGHT, spec=SPECIFIED, **answers)
    assert '"title": "open"' in ran["prompts"]["fix-1"]
    assert '"title": "held"' not in ran["prompts"]["fix-1"]


def test_roles_with_a_red_tree_run_tier_zero_and_their_own_tests_only() -> None:
    answers = {"implement": IMPLEMENTED, "critique": CRITIQUED, "review": REVIEW}
    ran = _run({"ref": "4"}, preflight=PREFLIGHT, spec=SPECIFIED, **answers)
    prompts = ran["prompts"]
    for role in ("spec", "critique"):
        assert "do not run `check`" in prompts[role], role
        assert "run the `tier0` entry point" in prompts[role], role
        assert "with `tests-changed all`" in prompts[role], role
    for role in ("implement", "review"):
        assert "Run `check` from the repository root last, before you return" in prompts[role]
        assert "do not run `check`" not in prompts[role], role
    for role in ("spec", "critique", "implement", "review"):
        assert (
            "While you work, run the `tier0` entry point and `tests-changed all`" in prompts[role]
        )
        assert "Never run a suite whole to see whether your own tests pass" in _said(prompts[role])
    assert "Run `tier0` and your new tests" in prompts["spec"]


def test_the_review_must_see_check_green_on_the_final_tree() -> None:
    answers = {"implement": IMPLEMENTED, "critique": CRITIQUED}
    red = {**REVIEW, "checkPasses": False}
    ran = _run({"ref": "4"}, preflight=PREFLIGHT, spec=SPECIFIED, review=red, **answers)
    assert ran["result"]["verdict"] == "request_changes"
    reason = "the review's run of check on the final tree was not green"
    assert reason in ran["result"]["verdictForcedBy"]


def test_the_critic_is_pointed_at_the_binding_s_critique_directory() -> None:
    answers = {"implement": IMPLEMENTED, "critique": CRITIQUED, "review": REVIEW}
    ran = _run({"ref": "4"}, preflight=PREFLIGHT, spec=SPECIFIED, **answers)
    prompt = ran["prompts"]["critique"]
    assert "Critique tests section names" in prompt
    assert "<slug>.<layer>.critique.test.<ext>" in prompt
    assert "next to the spec's tests" not in prompt


def test_the_critic_and_the_review_grade_from_the_one_severity_list() -> None:
    answers = {"implement": IMPLEMENTED, "critique": CRITIQUED, "review": REVIEW}
    ran = _run({"ref": "4"}, preflight=PREFLIGHT, spec=SPECIFIED, **answers)
    for role in ("critique", "review"):
        prompt = " ".join(ran["prompts"][role].split())
        assert prompt.count("Severity, the one list every grade you apply comes from.") == 1, role
        blocker, major = prompt.split("- Blocker:", 1)[1].split("- Major:", 1)
        assert "a span attribute or log field carrying a secret or a personal identifier" in blocker
        assert "behaviour shipped on with no flag" in major.split("- Minor:")[0], role
        assert "- Design axis: A layer violation is a blocker" in prompt, role
        assert "A finding that carries no axis takes every line." in prompt, role


def test_a_guard_path_an_open_dispute_names_is_no_finding_and_forces_nothing() -> None:
    answers = {"implement": IMPLEMENTED, "critique": CRITIQUED}
    named = {
        **REVIEW,
        "guardChanges": ["justfile"],
        "disputeCoverage": [{"path": "justfile", "disputeFile": "docs/disputes/0001-gate.md"}],
    }
    ran = _run({"ref": "4"}, preflight=PREFLIGHT, spec=SPECIFIED, review=named, **answers)
    assert ran["result"]["verdict"] == "approve"
    assert ran["result"]["verdictForcedBy"] == []
    unnamed = {**REVIEW, "guardChanges": ["justfile"]}
    ran = _run({"ref": "4"}, preflight=PREFLIGHT, spec=SPECIFIED, review=unnamed, **answers)
    assert ran["result"]["verdict"] == "request_changes"
    assert any(
        "guard paths changed" in r and "justfile" in r for r in ran["result"]["verdictForcedBy"]
    )


def test_neither_the_critic_nor_the_review_grades_a_guard_path_a_dispute_names() -> None:
    answers = {"implement": IMPLEMENTED, "critique": CRITIQUED, "review": REVIEW}
    ran = _run({"ref": "4"}, preflight=PREFLIGHT, spec=SPECIFIED, **answers)
    for role in ("critique", "review"):
        prompt = " ".join(ran["prompts"][role].split())
        assert "A path a dispute names exactly is no finding" in prompt, role
        assert "and a major one when one does" not in prompt, role


def test_a_criterion_the_change_does_not_meet_is_major_and_forces_changes() -> None:
    answers = {"implement": IMPLEMENTED, "critique": CRITIQUED, "review": REVIEW}
    ran = _run({"ref": "4"}, preflight=PREFLIGHT, spec=SPECIFIED, **answers)
    line = "a criterion of the ticket the change does not meet is major"
    assert line in " ".join(ran["prompts"]["review"].split())
    unmet = {**BLOCKER, "severity": "major", "title": "criterion 2 is not met"}
    answers["review"] = _quoted({**REVIEW, "verdict": "approve", "issues": [unmet]}, "issues")
    ran = _run({"ref": "4"}, preflight=PREFLIGHT, spec=SPECIFIED, **answers)
    assert ran["result"]["verdict"] == "request_changes"
    assert ran["result"]["verdictForcedBy"] == ["1 blocker/major review issue(s)"]


def test_a_ticket_whose_whole_change_cannot_fit_is_split_by_the_spec_stage() -> None:
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


BLOCKER = {
    "axis": "spec",
    "severity": "blocker",
    "title": "drops a link with exit 0",
    "location": "src/links.py:3",
    "evidence": "saving an empty link answers 200",
}
BLOCKED = _quoted({**REVIEW, "verdict": "request_changes", "issues": [BLOCKER]}, "issues")
YOUNG_LABELS = ["preflight", "spec", "ticket-check", "implement"]


def test_a_young_project_gets_one_fix_pass_on_a_review_blocker_and_one_more_review() -> None:
    answers = {"implement": IMPLEMENTED, "review": BLOCKED, "fix-1": IMPLEMENTED}
    answers |= {"review-1": REVIEW}
    ran = _run({"ref": "4"}, preflight=YOUNG, spec=SPECIFIED, **answers)
    assert ran["labels"] == [*YOUNG_LABELS, "review", "fix-1", "review-1"]
    assert '"title": "drops a link with exit 0"' in ran["prompts"]["fix-1"].split("MAY resolve")[0]
    assert "round 1 of 1" in ran["prompts"]["fix-1"]
    result = ran["result"]
    assert (result["verdict"], result["iterations"], result["critic"]["ran"]) == (
        "approve",
        1,
        False,
    )
    assert result["reviewIssues"] == []


def test_a_review_that_approves_with_a_blocker_of_its_own_still_gets_the_pass() -> None:
    approving = {**BLOCKED, "verdict": "approve"}
    answers = {"implement": IMPLEMENTED, "review": approving, "fix-1": IMPLEMENTED}
    answers |= {"review-1": REVIEW}
    ran = _run({"ref": "4"}, preflight=YOUNG, spec=SPECIFIED, **answers)
    assert ran["labels"] == [*YOUNG_LABELS, "review", "fix-1", "review-1"]
    assert ran["result"]["verdict"] == "approve"


def test_a_review_round_that_sent_a_fix_pass_stays_in_the_result_and_is_checked(
    tmp_path: Path,
) -> None:
    claimed = {"found": [{"index": 0, **INVENTED}], "not_found": []}
    forged = {
        **BLOCKED,
        "issues": [{**BLOCKER, "breaks": INVENTED}],
        "quoteOutput": json.dumps(claimed),
    }
    answers = {"implement": IMPLEMENTED, "review": forged, "fix-1": IMPLEMENTED}
    answers |= {"review-1": REVIEW}
    ran = _run({"ref": "4"}, preflight=YOUNG, spec=SPECIFIED, **answers)
    assert "fix-1" in ran["labels"], "the forged output sent a fix agent on the reviewer's word"
    result = ran["result"]
    assert (result["verdict"], result["reviewIssues"]) == ("approve", [])
    (round_zero,) = result["reviewIssuesByRound"]
    assert round_zero["round"] == 0
    assert [f["breaks"] for f in round_zero["findings"]] == [INVENTED]
    (tmp_path / "AGENTS.md").write_text(f"# Rules\n\n{RULE}\n", encoding="utf-8")
    written = tmp_path / "start.json.result.json"
    written.write_text(json.dumps(result), encoding="utf-8")
    owed_findings = publish_check.read_result(written)
    lines = publish_check.quote_lines(owed_findings, tmp_path, lambda _: None)
    assert len(lines) == 1
    assert f'"{INVENTED["quote"]}"' in lines[0]
    assert "the finding `drops a link with exit 0`" in lines[0]


def test_a_young_project_whose_second_review_still_blocks_is_parked_after_one_pass() -> None:
    answers = {"implement": IMPLEMENTED, "review": BLOCKED, "fix-1": IMPLEMENTED}
    answers |= {"review-1": BLOCKED, "fix-2": IMPLEMENTED, "review-2": REVIEW}
    ran = _run({"ref": "4"}, preflight=YOUNG, spec=SPECIFIED, **answers)
    assert ran["labels"] == [*YOUNG_LABELS, "review", "fix-1", "review-1"]
    assert ran["result"]["verdict"] == "request_changes"
    assert [i["title"] for i in ran["result"]["reviewIssues"]] == [BLOCKER["title"]]


def test_a_young_projects_fix_pass_is_spent_only_on_blockers_in_the_change() -> None:
    lost = {**PROMISES[0], "reading": "dropped", "change": "the list is not newest first"}
    refused = {**BLOCKED, "ticketPromises": [lost, *PROMISES[1:]]}
    minor = {**REVIEW, "verdict": "request_changes", "issues": [{**BLOCKER, "severity": "minor"}]}
    for review in (refused, minor):
        answers = {"implement": IMPLEMENTED, "review": review, "fix-1": IMPLEMENTED}
        ran = _run({"ref": "4"}, preflight=YOUNG, spec=SPECIFIED, **answers)
        assert ran["labels"] == [*YOUNG_LABELS, "review"]
        assert ran["result"]["verdict"] == "request_changes"


def test_a_fix_pass_that_takes_a_young_project_over_the_ceiling_stops_the_run() -> None:
    size = {"lines": 900, "ceiling": 800, "testLines": 10, "testCeiling": 1000}
    answers = {
        "implement": IMPLEMENTED,
        "review": BLOCKED,
        "fix-1": {**IMPLEMENTED, "changeSize": size},
    }
    ran = _run({"ref": "4"}, preflight=YOUNG, spec=SPECIFIED, **answers)
    assert ran["labels"] == [*YOUNG_LABELS, "review", "fix-1"]
    assert ran["result"]["tooLarge"]["stage"] == "fix round 1"


def test_a_depended_on_project_with_a_review_blocker_gets_no_extra_pass() -> None:
    answers = {"implement": IMPLEMENTED, "critique": CRITIQUED, "review": BLOCKED}
    ran = _run({"ref": "4"}, preflight=PREFLIGHT, spec=SPECIFIED, **answers)
    assert ran["labels"] == [*YOUNG_LABELS, "critique", "review"]
    assert ran["result"]["verdict"] == "request_changes"


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
    review = _quoted({**REVIEW, "issues": [finding], "verdict": "approve"}, "issues")
    again = {"fix-1": IMPLEMENTED, "review-1": review}
    ran = _run(
        {"ref": "4"}, preflight=YOUNG, spec=SPECIFIED, implement=IMPLEMENTED, review=review, **again
    )
    result = ran["result"]
    assert result["verdict"] == "request_changes"
    assert result["verdictForcedBy"] == ["1 blocker/major review issue(s)"]
    assert result["verdictRecord"]["unresolved"] == ["loses the order"]


def test_without_the_critic_a_change_may_fill_the_whole_ceiling() -> None:
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
    kept = _quoted({**CRITIQUED, "findings": [{"severity": "major", "title": "open"}]})
    answers = {"implement": IMPLEMENTED, "critique": kept, "review": REVIEW}
    answers |= {"fix-1": IMPLEMENTED, "critique-1": CRITIQUED}
    ran = _run({"ref": "4"}, preflight=PREFLIGHT, spec=SPECIFIED, **answers)
    assert "red on the `triggers` gate" in ran["prompts"]["fix-1"]


KINDS: dict[str, type] = {
    "object": dict,
    "array": list,
    "string": str,
    "boolean": bool,
    "integer": int,
    "null": type(None),
}


def _valid(value: object, schema: dict[str, Any]) -> bool:
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
    return " ".join(prompt.split())


def _reread(promise: str, reading: dict[str, str]) -> list[dict[str, object]]:
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
    answers = {"implement": IMPLEMENTED, "critique": CRITIQUED, "review": REVIEW}
    based = {**PREFLIGHT, "base": "main"}
    ran = _run({"ref": TICKET_FILE}, preflight=based, spec=SPECIFIED, **answers)
    shown = (
        "Read it, from the repository root (the directory holding AGENTS.md), with\n\n"
        f"      git show --end-of-options 'main:{TICKET_FILE}'\n"
    )
    for role in ("spec", "ticket-check", "review"):
        assert shown in ran["prompts"][role], role
        assert "not the copy in the working tree" in ran["prompts"][role], role
    rejudge = {"ref": TICKET_FILE, "base": "main", "branch": "feature/4-expiry", "rejudge": REJUDGE}
    assert shown in _run(rejudge, **{"review-again": REVIEW})["prompts"]["review-again"]
    unbased = _built(REVIEW, TICKET_FILE)["prompts"]["review"]
    inputs = unbased.split("Inputs you may read")[1].split("Load the")[0]
    assert f"It is the file `{TICKET_FILE}` under the repository root" in inputs
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
        assert axis in review["required"], axis
    assert check["properties"]["ticketPromises"] == review["properties"]["ticketPromises"]
    inputs = review["properties"]["acceptedInputs"]["items"]
    assert inputs["required"] == [
        *check["properties"]["acceptedInputs"]["items"]["required"],
        "named",
    ]
    assert {k: v for k, v in inputs["properties"].items() if k != "named"} == (
        check["properties"]["acceptedInputs"]["items"]["properties"]
    )
    prompts = _built(REVIEW)["prompts"]
    for role in ("ticket-check", "review"):
        assert "- `dropped`: no criterion carries it;" in prompts[role], role
        assert "an assumption decides no case." in prompts[role], role
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
    undecided = {k: v for k, v in REVIEWED_LINK.items() if k != "hostile"}
    assert not _valid({**REVIEW, "acceptedInputs": [undecided]}, schema), "a case left out"
    assert _valid({**REVIEW, "acceptedInputs": [{**REVIEWED_LINK, "hostile": None}]}, schema)
    unnamed = {k: v for k, v in REVIEWED_LINK.items() if k != "named"}
    assert not _valid({**REVIEW, "acceptedInputs": [unnamed]}, schema), "an input no record names"
    blank = {**REVIEWED_LINK, "empty": ""}
    assert not _valid({**REVIEW, "acceptedInputs": [blank]}, schema), "a blank decides nothing"


def test_a_gap_the_check_finds_is_amended_once_and_the_run_goes_on() -> None:
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
    ran = _built(REVIEW)
    spec = _said(ran["prompts"]["spec"])
    assert "mattpocock-skills:codebase-design" in spec
    assert "Design: how the feature fits the project's architecture" in spec
    assert "the `## Architecture` table of docs/process/binding.md declares" in spec
    assert "architecture decision record that section" in spec
    assert "AGENTS.md declares" not in spec
    assert "a new layer, a new kind of storage, a new process boundary" in spec
    assert "open question of kind `direction`" in spec.split("A change that implies")[1]
    review = _said(ran["prompts"]["review"])
    assert "mattpocock-skills:codebase-design" in review
    axis = review.split("F. Design.")[1].split("The script files every")[0]
    assert "the `## Architecture` section of docs/process/binding.md" in axis
    assert "shallow wrapper" in axis
    grades = review.split("- Design axis:")[1].split("A defect the spec defers")[0]
    assert "A layer violation is a blocker" in grades
    assert "are minor" in grades
    issue = ran["schemas"]["review"]["properties"]["issues"]["items"]
    assert "design" in issue["properties"]["axis"]["enum"]


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
    evidence = result["ticketFindings"][0]["evidence"]
    assert "the method's own, that every case of an input a record names is decided" in evidence
    assert "and not a promise of the project" in evidence
    assert "the quote shows the input is the record's" in evidence
    assert "the quote check refuses an invented input" in evidence


def test_a_run_after_a_lost_promise_hands_the_spec_role_what_was_lost() -> None:
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
    assert "dependencies?, namedGates?, assumptions?, settled? }" in PIPELINE.read_text(
        encoding="utf-8"
    )


def test_the_owner_reads_each_input_by_its_plain_name() -> None:
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
    worst = {"ticket-check": GAP, "spec-amend": SPECIFIED, "ticket-recheck": CHECKED}
    worst |= {"implement": IMPLEMENTED, "review": REVIEW}
    blocked = {"review": BLOCKED, "fix-1": IMPLEMENTED, "review-1": BLOCKED}
    young = _run({"ref": "4"}, preflight=YOUNG, spec=SPECIFIED, **{**worst, **blocked})["labels"]
    assert len(young) == drain.YOUNG_PIPELINE_AGENTS

    def owed(number: int) -> dict[str, Any]:
        finding = {"severity": "major", "title": f"open {number}"}
        return _quoted({**CRITIQUED, "findings": [finding]})

    rounds = {"critique": owed(0), "fix-1": IMPLEMENTED, "critique-1": owed(1)}
    rounds |= {"fix-2": IMPLEMENTED, "critique-2": owed(2)}
    depended_on = _run({"ref": "4"}, preflight=PREFLIGHT, spec=SPECIFIED, **worst, **rounds)
    assert len(depended_on["labels"]) == drain.PIPELINE_AGENTS
    rebased = _again(
        {"conflicts": ["flags.toml"]}, reconcile=IMPLEMENTED, **{"review-again": REVIEW}
    )
    assert len(rebased["labels"]) == drain.UPDATE_AGENTS


E2E_GATE = {"gate": "e2e-browser", "tier": "2", "criterion": "`e2e-browser` is green"}
SPEC_GATE = "`e2e-api`"
FIXED = {"fix-1": IMPLEMENTED, "critique-1": CRITIQUED}
KEPT_FAILING = {
    **CRITIQUED,
    "findings": [{"severity": "minor", "title": "a held case", "testFails": True}],
}


def _named(spec: dict[str, object], **answers: object) -> dict[str, Any]:
    answers = (
        {"implement": IMPLEMENTED, "critique": KEPT_FAILING, "review": REVIEW} | FIXED | answers
    )
    return _run({"ref": "4", "namedGates": [E2E_GATE]}, preflight=PREFLIGHT, spec=spec, **answers)


def test_the_gates_the_criteria_name_reach_every_role_and_the_specs_add_to_them() -> None:
    ran = _named({**SPECIFIED, "namedGates": [SPEC_GATE]})
    prompts = ran["prompts"]
    assert "`e2e-browser` (tier 2)" in prompts["spec"]
    assert SPEC_GATE not in prompts["spec"], "the spec role has not named it yet"
    for role in ("implement", "critique", "fix-1", "review"):
        assert "`e2e-browser` (tier 2), `e2e-api`" in prompts[role], role
    assert ran["result"]["namedGates"] == [
        {"gate": "e2e-browser", "tier": "2"},
        {"gate": "e2e-api"},
    ]


def test_a_gate_both_lists_hold_is_named_once() -> None:
    ran = _named({**SPECIFIED, "namedGates": ["`e2e-browser`"]})
    assert ran["prompts"]["review"].count("`e2e-browser`") == 1
    assert ran["result"]["namedGates"] == [{"gate": "e2e-browser", "tier": "2"}]


def test_a_run_whose_criteria_name_no_gate_says_so() -> None:
    ran = _built(REVIEW)
    assert "No criterion of the ticket or of spec.md names a gate" in ran["prompts"]["implement"]


def test_a_later_gate_is_run_whole_once_by_the_implementer_and_once_by_each_review() -> None:
    prompts = _named(SPECIFIED)["prompts"]
    assert "run whole, once, by you, after your `check` is green" in _said(prompts["implement"])
    assert "run whole, once, by you, on the tree you judge" in _said(prompts["review"])
    for role in ("spec", "critique", "fix-1"):
        assert "run whole" not in _said(prompts[role]), role
        assert "You run no gate a criterion names that `check` does not" in prompts[role], role


def test_the_schemas_say_which_named_gates_the_spec_added_and_each_role_ran() -> None:
    ran = _named(SPECIFIED)
    spec, implement, review = (ran["schemas"][label] for label in ("spec", "implement", "review"))
    assert _valid({**SPECIFIED, "namedGates": [SPEC_GATE]}, spec)
    assert not _valid({k: v for k, v in SPECIFIED.items() if k != "namedGates"}, spec)
    assert not _valid({**SPECIFIED, "namedGates": [3]}, spec)
    assert _valid({**IMPLEMENTED, "namedGatesRun": ["`e2e-browser`"]}, implement)
    unsaid = {k: v for k, v in IMPLEMENTED.items() if k != "namedGatesRun"}
    assert not _valid(unsaid, implement), "the implementer says what it ran, even if nothing"
    assert _valid({**REVIEW, "namedGatesRun": ["`e2e-browser`"]}, review)
    assert not _valid({k: v for k, v in REVIEW.items() if k != "namedGatesRun"}, review)
    assert "namedGatesRun" not in ran["schemas"]["fix-1"]["properties"]


def test_the_result_says_which_named_gates_each_role_ran() -> None:
    implemented = {**IMPLEMENTED, "namedGatesRun": ["`e2e-browser`"]}
    reviewed = {**REVIEW, "namedGatesRun": ["`e2e-browser`", "`e2e-api`"]}
    ran = _named(SPECIFIED, implement=implemented, review=reviewed)
    assert ran["result"]["namedGatesRun"] == {
        "implement": ["`e2e-browser`"],
        "review": ["`e2e-browser`", "`e2e-api`"],
    }


def test_a_rejudge_reviews_against_the_gates_the_first_run_named() -> None:
    ran = _again({"namedGates": [{"gate": "e2e-browser", "tier": "2"}]}, **{"review-again": REVIEW})
    assert "`e2e-browser` (tier 2)" in ran["prompts"]["review-again"]


def test_the_preflight_role_relays_no_named_gates_and_its_report_changes_nothing() -> None:
    ran = _run(
        {"ref": "4"},
        preflight={**PREFLIGHT, "named_gates": [E2E_GATE]},
        spec=SPECIFIED,
        implement=IMPLEMENTED,
        critique=CRITIQUED,
        review=REVIEW,
    )
    assert "named_gates" not in ran["schemas"]["preflight"]["properties"]
    assert "named_gates" not in ran["prompts"]["preflight"]
    assert "No criterion of the ticket or of spec.md names a gate" in ran["prompts"]["review"]
    assert ran["result"]["namedGates"] == []


def test_the_skill_passes_the_named_gates_of_the_preflight_and_of_the_drain_open() -> None:
    feature = (PIPELINE.parents[1] / "skills/feature/SKILL.md").read_text(encoding="utf-8")
    assert "`critic_reason`, `named_gates`) and exits 1" in feature
    assert '"namedGates": <the preflight\'s named_gates> }' in feature
    assert '"namedGates": <the open\'s named_gates> }' in feature
    assert "`branch`, `worktree`, `base` and `named_gates`" in feature
    assert '"namedGates": <the first result\'s namedGates>' in feature


MAJOR = {
    "severity": "major",
    "title": "loses the order",
    "carries": None,
    "evidence": "two saves in one second came back reversed",
    "suggestedTest": "",
    "testFails": False,
    "heldByKeptTest": False,
}
INVENTED = {"record": "AGENTS.md", "quote": "A change always keeps the saved order."}
DEPTH = {"implement": IMPLEMENTED, "review": REVIEW, "fix-1": IMPLEMENTED, "fix-2": IMPLEMENTED}
CASE_QUESTION = "Does the list show every item or the first fifty?"


def _case(whose: str, **fields: object) -> dict[str, object]:
    return {
        "now": "the list is empty",
        "evidence": "ran the page with a hundred items",
        "question": CASE_QUESTION,
        "kind": "direction",
        "options": ["Every item", "The first fifty"],
        "recommendation": "Nobody said items may be hidden.",
        "refuter": "Wrong if a long list is slow",
        "whose": whose,
        **fields,
    }


def _departure(source: str, whose: str = "role") -> dict[str, object]:
    return {
        **_case(whose),
        "from": source,
        "quote": "1. Given a link, When it is saved, Then the list shows it first.",
        "why": "the store sorts by title",
    }


def _critique(*findings: dict[str, Any], **fields: object) -> dict[str, Any]:
    return _quoted({**CRITIQUED, "findings": list(findings), **fields})


def _built_run(**answers: object) -> dict[str, Any]:
    return _run({"ref": "4"}, preflight=PREFLIGHT, spec=SPECIFIED, **{**DEPTH, **answers})


def test_a_finding_that_quotes_a_sentence_no_record_holds_is_never_fixed_and_never_dropped() -> (
    None
):
    invented = _critique({**MAJOR, "breaks": INVENTED})
    ran = _built_run(critique=invented)
    assert "fix-1" not in ran["labels"], "the critic's authority alone sends no fix agent"
    result = ran["result"]
    assert (result["verdict"], result["stoppedAfter"]) == ("blocked", "review")
    assert result["criticFindingsByRound"][0]["findings"] == []
    (entry,) = result["unstated"]
    assert (entry["of"], entry["whose"], entry["by"]) == ("C0-1", "owner", "review")
    assert "does not hold the quote" in entry["recommendation"]
    assert result["unclassified"] == 1
    (question,) = result["openQuestions"]
    for words in (
        MAJOR["title"],
        MAJOR["evidence"],
        f'AGENTS.md: "{INVENTED["quote"]}"',
    ):
        assert words in question["question"], words
    assert question["options"] == [
        f"The critic's view: {MAJOR['title']}. Change the code as the finding asks",
        "What the code does now. Leave it as it is",
    ]
    prompt = ran["prompts"]["review"]
    assert '"id": "C0-1"' in prompt
    assert "A change always keeps the saved order." in prompt


def test_the_review_gives_a_moved_finding_to_a_role_and_it_lands_in_the_assumptions() -> None:
    answered = {**REVIEW, "unstated": [_case("role", of="C0-1")]}
    ran = _built_run(critique=_critique({**MAJOR, "breaks": INVENTED}), review=answered)
    result = ran["result"]
    assert result["verdict"] == "approve"
    assumed = [a for a in result["assumptions"] if a["question"] == CASE_QUESTION]
    assert assumed == [
        {
            "question": CASE_QUESTION,
            "assumed": "Every item",
            "refuter": "Wrong if a long list is slow",
        }
    ]
    assert [e["whose"] for e in result["unstated"]] == ["role"]


def test_a_blocker_the_review_cannot_quote_is_moved_and_stops_the_run() -> None:
    quoted = _quoted({**REVIEW, "issues": [{**BLOCKER, "breaks": INVENTED}]}, "issues")
    ran = _run({"ref": "4"}, preflight=YOUNG, spec=SPECIFIED, implement=IMPLEMENTED, review=quoted)
    assert ran["labels"] == [*YOUNG_LABELS, "review"], "no fix pass on an unquoted blocker"
    result = ran["result"]
    assert (result["verdict"], result["stoppedAfter"], result["stop"]) == (
        "blocked",
        "review",
        "question",
    )
    assert [e["of"] for e in result["unstated"]] == ["R1"]
    assert (result["unclassified"], result["unquotedReviewIssues"]) == (0, 1)
    assert result["openQuestions"][0]["options"][0].startswith("The reviewer's view:")


def test_a_minor_with_a_failing_test_and_no_quote_is_still_owed_a_fix() -> None:
    minor = {**MAJOR, "severity": "minor", "testFails": True}
    ran = _built_run(critique=_critique(minor), **{"critique-1": CRITIQUED})
    assert "fix-1" in ran["labels"]
    assert ran["result"]["unstated"] == [], "nothing was moved"


def test_a_finding_carried_through_the_round_it_was_owed_ends_the_loop_and_parks() -> None:
    first = _critique(MAJOR)
    again = _critique({**MAJOR, "carries": "C0-1"})
    ran = _built_run(critique=first, **{"critique-1": again})
    assert ran["labels"][-3:] == ["critique", "fix-1", "critique-1"]
    assert "review" not in ran["labels"], "the review does not run"
    assert '"id": "C0-1"' in ran["prompts"]["critique-1"]
    result = ran["result"]
    assert (result["verdict"], result["iterations"]) == ("request_changes", 1)
    assert (result["stop"], result["stoppedAfter"]) == ("carried", "critique-1")
    assert result["carriedStillOwed"] == ["C1-1"]
    assert [f["id"] for f in result["unresolvedBlockingFindings"]] == ["C1-1"]
    assert result["unresolvedBlockingFindings"][0]["breaks"] == BREAKS
    assert (result["reviewIssues"], result["ticketFindings"]) == ([], [])
    assert result["verdictRecord"]["unresolved"] == [MAJOR["title"]]
    assert result["verdictRecord"]["verdict"] == "request_changes"
    assert (result["unclassified"], result["openQuestions"]) == (0, [])


def test_a_parked_run_hands_the_findings_no_role_classified_to_the_owner_as_questions() -> None:
    moved = {**MAJOR, "title": "reverses a saved list", "breaks": INVENTED}
    first = _critique(MAJOR, moved)
    again = _critique({**MAJOR, "carries": "C0-1"})
    ran = _built_run(critique=first, **{"critique-1": again})
    assert "review" not in ran["labels"]
    result = ran["result"]
    assert (result["verdict"], result["stop"]) == ("request_changes", "carried")
    assert result["unclassified"] == 1
    (entry,) = [e for e in result["unstated"] if e["of"] == "C0-2"]
    assert entry["unclassified"] is True
    (question,) = result["openQuestions"]
    assert result["verdictRecord"]["openQuestions"] == [question["question"]]
    assert question["options"][0].startswith("The critic's view: reverses a saved list")
    assert question["options"][1].startswith("What the code does now")


def test_a_finding_relabelled_as_new_carries_when_its_quote_and_title_are_the_same() -> None:
    first = _critique(MAJOR)
    relabelled = _critique({**MAJOR, "carries": None})
    ran = _built_run(critique=first, **{"critique-1": relabelled})
    assert ran["labels"][-3:] == ["critique", "fix-1", "critique-1"]
    result = ran["result"]
    assert (result["verdict"], result["stop"]) == ("request_changes", "carried")
    assert result["carriedStillOwed"] == ["C1-1"]


def test_two_defects_that_break_the_same_sentence_each_get_their_round() -> None:
    first = _critique(MAJOR)
    other = {**MAJOR, "title": "reverses a saved list", "carries": None}
    assert other["title"] != MAJOR["title"]
    ran = _built_run(critique=first, **{"critique-1": _critique(other), "critique-2": CRITIQUED})
    assert ran["labels"][-6:] == [
        "critique",
        "fix-1",
        "critique-1",
        "fix-2",
        "critique-2",
        "review",
    ]
    assert ran["result"]["carriedStillOwed"] == []


def test_no_role_prompt_runs_a_plugin_script_with_a_bare_python3() -> None:
    args = {"ref": "4", "preflight": "/plugin/scripts/feature_preflight.py"}
    ran = _run(args, preflight=PREFLIGHT, spec=SPECIFIED, **DEPTH, critique=CRITIQUED)
    assert ran["prompts"]
    for label, prompt in ran["prompts"].items():
        assert not re.search(r"python3 (?!-E -s )\S*scripts/", prompt), label
        assert not re.search(r"python3 (?!-E -s )", prompt), label
    assert f"{PLUGIN_PY} '/plugin/scripts/feature_preflight.py'" in ran["prompts"]["preflight"]


AWKWARD = '/opt/my plugins/it\'s "$HOME"/scripts/'
CLOSED = {"check": "ticket", "detail": "closed", "produce": "reopen it"}
STOPPED = {"ok": False, "failures": [CLOSED]}


def _commands(prompt: str) -> list[str]:
    return [line for line in prompt.splitlines() if PLUGIN_PY in line]


HOSTILE = [
    "a b",
    "$HOME",
    "$(touch {marker})",
    "`touch {marker}`",
    'x"y',
    "it's",
    "a;b",
    "*",
    "a\\b",
]


def _words(line: str, cwd: Path) -> list[str]:
    shell = shutil.which("bash")
    assert shell, "bash reads a command line as the agent's shell does"
    assert line.lstrip().startswith(PLUGIN_PY), line
    standin = line.replace(PLUGIN_PY, "printf '%s\\0'", 1)
    standin = standin.replace("<that file>", "THAT").replace("<ticket file>", "TICKET")
    (cwd / "a file the shell could glob").touch()
    run = subprocess.run(  # noqa: S603 # -- the line under test is the shell's input
        [shell, "-c", standin], cwd=cwd, capture_output=True, text=True, check=True
    )
    return run.stdout.split("\0")[:-1]


@pytest.mark.parametrize("hostile", HOSTILE)
def test_a_shell_hands_the_preflight_each_value_as_one_word_unchanged(
    hostile: str, tmp_path: Path
) -> None:
    marker = tmp_path / "expanded"
    value = hostile.format(marker=marker)
    args = {
        "ref": f"docs/{value}.md",
        "preflight": f"/opt/{value}/scripts/feature_preflight.py",
        "base": "base/$HOME;it's",
        "branch": 'feature/x"y`touch${IFS}m`',
    }
    [line] = _commands(_run(args, preflight=STOPPED)["prompts"]["preflight"])
    assert _words(line, tmp_path) == [
        args["preflight"],
        args["ref"],
        "--root",
        ".",
        "--base",
        args["base"],
        "--branch",
        args["branch"],
    ], line
    assert not marker.exists(), f"the shell ran a command out of {line}"


@pytest.mark.parametrize("hostile", HOSTILE)
def test_a_shell_hands_the_quote_check_its_script_path_as_one_word_unchanged(
    hostile: str, tmp_path: Path
) -> None:
    marker = tmp_path / "expanded"
    value = hostile.format(marker=marker)
    named = {"ref": "4", "preflight": f"/opt/{value}/scripts/feature_preflight.py"}
    again = {"review-again": REVIEW}
    runs = [
        _run(named, preflight=PREFLIGHT, spec=SPECIFIED, **DEPTH, critique=CRITIQUED),
        _run({**named, "branch": "feature/4-expiry", "rejudge": REJUDGE}, **again),
    ]
    checked = [f"/opt/{value}/scripts/quotes.py", "THAT", "--root", "."]
    lines = [
        (label, line)
        for ran in runs
        for label, prompt in ran["prompts"].items()
        if label != "preflight"
        for line in _commands(prompt)
    ]
    assert [label for label, _ in lines] == ["critique", "review", "review-again"]
    for label, line in lines:
        record = [] if label == "critique" else ["--record", "ticket=TICKET"]
        assert _words(line, tmp_path) == [*checked, *record], f"{label}: {line}"
    assert not marker.exists(), "the shell ran a command out of a quote-check line"


def test_every_command_a_role_is_told_to_run_is_a_line_of_its_own_with_no_backtick() -> None:
    named = {"ref": "4", "preflight": f"{AWKWARD}feature_preflight.py"}
    again = {"review-again": REVIEW}
    runs = [
        _run(named, preflight=PREFLIGHT, spec=SPECIFIED, **DEPTH, critique=CRITIQUED),
        _run({"ref": "4"}, preflight=PREFLIGHT, spec=SPECIFIED, **DEPTH, critique=CRITIQUED),
        _run({**named, "branch": "feature/4-expiry", "rejudge": REJUDGE}, **again),
    ]
    seen = 0
    for ran in runs:
        for label, prompt in ran["prompts"].items():
            for line in _commands(prompt):
                seen += 1
                assert line.lstrip().startswith(PLUGIN_PY), f"{label}: prose around it: {line}"
                assert "`" not in line, f"{label}: a backtick in a command line: {line}"
    assert seen == 7, "preflight, critique and review in two runs, and the second review"


SLOTS = ("base", "branch", "ticket", "preflight", "root", "conflict", "spec")
SENTINELS = {slot: f"V{slot}0" for slot in SLOTS}
GROUPS = {
    "paths": ("ticket", "preflight", "root", "conflict", "spec"),
    "base": ("base",),
    "branch": ("branch",),
}
NAME_SPAN = re.compile(r"`V[a-z]+0`")
PLACEHOLDER = re.compile(r"<[^<>\n]+>")
COMMAND_LINES = 40
OPTION_SHAPED = [
    "--output=pwned",
    "--exec=touch${IFS}pwned",
    "-cimport os; os.mknod('pwned')",
    "-",
    "--",
]
GIT_OR_GH = re.compile(r"\b(git [a-z-]+|gh [a-z]+ [a-z]+)", re.IGNORECASE)
CODE_SPAN = re.compile(r"`([^`\n]+)`")
PUBLISHING = re.compile(r"\b(?:git (?:commit|push)|gh pr)\b")
FAILING_KEPT = {
    **CRITIQUED,
    "findings": [{"severity": "minor", "title": "records a HEAD as a GET", "testFails": True}],
    "testsAdded": ["tests/test_method.py"],
}


def _runs(values: dict[str, str]) -> list[dict[str, Any]]:
    args = {
        "ref": values["ticket"],
        "base": values["base"],
        "branch": values["branch"],
        "root": values["root"],
        "preflight": f"{values['preflight']}/feature_preflight.py",
    }
    spec = {**SPECIFIED, "specPath": values["spec"]}
    built = {**DEPTH, "critique": FAILING_KEPT, "critique-1": CRITIQUED}
    again = {**REJUDGE, "specPath": values["spec"], "conflicts": [values["conflict"]]}
    return [
        _run(args, preflight=PREFLIGHT, spec=spec, **built),
        _run({**args, "rejudge": again}, reconcile=IMPLEMENTED, **{"review-again": REVIEW}),
    ]


def _shown(runs: list[dict[str, Any]], slots: tuple[str, ...], value: str) -> dict[str, str]:
    prompts: dict[str, str] = {}
    for ran in runs:
        if ran["labels"]:
            prompts.update(ran["prompts"])
            continue
        [failure] = ran["result"]["preflight"]["failures"]
        assert failure["check"] in slots, failure
        assert json.dumps(value, ensure_ascii=False)[1:-1] in failure["detail"], failure
    return prompts


@functools.cache
def _every_prompt() -> dict[str, str]:
    return _shown(_runs(SENTINELS), (), "")


def _blocks(prompt: str) -> list[str]:
    found, fenced, previous = [], False, ""
    for line in prompt.splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
        elif fenced or (re.match(r" {4,}\S", line) and (not previous.strip() or previous in found)):
            found.append(line)
        previous = line
    return found


def _role_commands(prompt: str) -> frozenset[str]:
    places = [*_blocks(prompt), *CODE_SPAN.findall(prompt)]
    found = {command.lower() for place in places for command in GIT_OR_GH.findall(place)}
    return frozenset(found | set(PUBLISHING.findall(prompt)))


def _shell_words(line: str, cwd: Path) -> tuple[list[str], int]:
    shell = shutil.which("bash")
    assert shell, "bash reads a command line as the agent's shell does"
    (cwd / "a file the shell could glob").touch()
    standin = "printf '%s\\0' " + PLACEHOLDER.sub("PLACEHOLDER", line.strip())
    run = subprocess.run(  # noqa: S603 # -- the line under test is the shell's input
        [shell, "-c", standin], cwd=cwd, capture_output=True, text=True, check=False
    )
    return run.stdout.split("\0")[:-1], run.returncode


def _bash(line: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    shell = shutil.which("bash")
    assert shell, "bash reads a command line as the agent's shell does"
    return subprocess.run(  # noqa: S603 # -- the line under test is the shell's input
        [shell, "-c", PLACEHOLDER.sub("PLACEHOLDER", line.strip())],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


def _git(root: Path, argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 # -- a fixed argv, no shell
        ["git", *argv],  # noqa: S607 # -- git is resolved from PATH on purpose
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )


def _scratch(root: Path, name: str = "feature/x") -> Path:
    repository(root, {"t.md": "ticket\n"})
    branch(root, name)
    write(root, "src/a.py", "x = 2\n")
    commit(root, "feat(thing): the change")
    git(root, "switch", "-q", "main")
    write(root, "src/b.py", "y = 1\n")
    commit(root, "feat(other): what merged meanwhile")
    git(root, "switch", "-q", name)
    return root


def _state(root: Path) -> tuple[object, ...]:
    files = sorted(str(p.relative_to(root)) for p in root.rglob("*") if ".git" not in p.parts)
    tree = _git(root, ["rev-parse", "HEAD^{tree}"]).stdout
    return files, tree, _git(root, ["status", "--porcelain"]).stdout


def test_a_run_time_value_stands_only_on_a_command_line_or_alone_in_a_code_span() -> None:
    prompts = _every_prompt()
    roles = ["preflight", "spec", "ticket-check", "implement", "critique", "fix-1", "critique-1"]
    assert sorted(prompts) == sorted([*roles, "review", "reconcile", "review-again"])
    stray = []
    for label, prompt in prompts.items():
        lines = set(_blocks(prompt))
        for line in prompt.splitlines():
            rest = "" if line in lines else NAME_SPAN.sub("", line)
            if any(sentinel in rest for sentinel in SENTINELS.values()):
                stray.append(f"{label}: {line}")
    assert not stray, (
        "a run-time value goes on a command line of its own, quoted, or alone in a code span as a"
        " name; prose that tells a role to build a command around such a name is beyond this"
        " check, so give the role the line:\n" + "\n".join(stray)
    )


def test_the_prompts_show_a_known_number_of_command_lines_and_each_slot_reaches_one() -> None:
    lines = [line for prompt in _every_prompt().values() for line in _blocks(prompt)]
    reached = {slot for slot in SLOTS if any(SENTINELS[slot] in line for line in lines)}
    assert reached == set(SLOTS) - {"spec"}, sorted(reached)
    assert len(lines) == COMMAND_LINES, (
        f"the prompts show {len(lines)} command lines, not {COMMAND_LINES}. Each value a new line"
        " carries needs a slot in SLOTS, which the hostile and option-shaped tests then put on"
        " the line; give it one, then set COMMAND_LINES"
    )


@pytest.mark.parametrize("group", GROUPS)
@pytest.mark.parametrize("hostile", HOSTILE)
def test_a_value_reaches_each_command_line_as_one_word_unchanged_or_is_refused(
    hostile: str, group: str, tmp_path: Path
) -> None:
    marker = tmp_path / "expanded"
    value = hostile.format(marker=marker)
    slots = GROUPS[group]
    plain = _every_prompt()
    shown = _shown(_runs({**SENTINELS, **dict.fromkeys(slots, value)}), slots, value)
    for label, prompt in shown.items():
        for template, line in zip(_blocks(plain[label]), _blocks(prompt), strict=True):
            expected, _ = _shell_words(template, tmp_path)
            for slot in slots:
                expected = [word.replace(SENTINELS[slot], value) for word in expected]
            assert _shell_words(line, tmp_path) == (expected, 0), f"{label}: {line}"
    assert not marker.exists(), "the shell ran a command out of a command line"


@pytest.mark.parametrize("slot", SLOTS)
@pytest.mark.parametrize("option", OPTION_SHAPED)
def test_an_option_shaped_value_is_refused_or_changes_nothing_a_command_does(
    option: str, slot: str, tmp_path: Path
) -> None:
    root = tmp_path / "repo"
    ordinary = {
        "base": "main",
        "branch": "feature/x",
        "ticket": "t.md",
        "preflight": str(PLUGIN_ROOT / "scripts"),
        "root": str(root),
        "conflict": "t.md",
        "spec": "docs/features/x/spec.md",
    }
    shown = _shown(_runs({**ordinary, slot: option}), (slot,), option)
    named = _shown(_runs({**ordinary, slot: "no-such-name"}), (), "") if shown else {}
    pairs = [
        (label, line, other)
        for label, prompt in shown.items()
        for line, other in zip(_blocks(prompt), _blocks(named[label]), strict=True)
        if line != other
    ]
    if pairs:
        _scratch(root)
    for label, line, other in pairs:
        before = _state(root)
        ran = _bash(line, root)
        assert _state(root) == before, f"{label}: the line changed the repository: {line}"
        assert ran.returncode == _bash(other, root).returncode, f"{label}: {line} vs {other}"


REF_NAMES = [
    "main",
    "origin/main",
    "feature/4-expiry",
    "a1b2c3d",
    "0123456789abcdef0123456789abcdef01234567",
    "HEAD",
    "it's",
    "a;b|c&d",
    "$HOME",
    'x"y`z`',
    "été",
    "a@b",
    "a.lock.b",
    "-x",
    "--output=x",
    "a b",
    "a\tb",
    "a\x7fb",
    "a~1",
    "a^",
    "a:b",
    "a?",
    "a*",
    "a[",
    "a\\b",
    "a..b",
    "a@{b",
    "@",
    "a/.b",
    ".a",
    "x.lock",
    "x.lock/y",
    "a//b",
    "a/",
    "/a",
    "a.",
]


@pytest.mark.parametrize("name", REF_NAMES)
def test_a_base_or_a_branch_is_refused_exactly_when_git_refuses_it_as_a_ref_name(
    name: str, tmp_path: Path
) -> None:
    accepted = not name.startswith("-") and (
        _git(tmp_path, ["check-ref-format", "--allow-onelevel", name]).returncode == 0
    )
    ran = _run({"ref": "4", "base": name, "branch": name}, preflight=STOPPED)
    assert (ran["labels"] == ["preflight"]) is accepted, ran.get("result")
    if not accepted:
        [failure] = ran["result"]["preflight"]["failures"]
        assert (ran["result"]["verdict"], failure["check"]) == ("blocked", "base")
        assert failure["detail"].startswith(f"the base {json.dumps(name, ensure_ascii=False)} ")


@pytest.mark.parametrize("field", ["base", "branch"])
def test_a_base_or_a_branch_the_preflight_returns_is_held_to_the_same_rule(field: str) -> None:
    ran = _run({"ref": "4"}, preflight={**PREFLIGHT, field: "--output=pwned"}, spec=SPECIFIED)
    assert ran["labels"] == ["preflight"], "no role saw the value"
    [failure] = ran["result"]["preflight"]["failures"]
    assert (ran["result"]["verdict"], failure["check"]) == ("blocked", field)
    assert failure["detail"] == (
        f'the {field} "--output=pwned" starts with "-", so the command it goes into would read'
        " it as an option"
    )


def test_each_git_line_means_with_its_end_of_options_marker_what_it_means_without(
    tmp_path: Path,
) -> None:
    template = _scratch(tmp_path / "template")
    ordinary = {
        "base": "main",
        "branch": "feature/x",
        "ticket": "t.md",
        "preflight": "/opt/p",
        "root": str(template),
        "conflict": "t.md",
        "spec": "docs/features/x/spec.md",
    }
    prompts = _shown(_runs(ordinary), (), "")
    lines = sorted({line.strip() for p in prompts.values() for line in _blocks(p)})
    markers = {"--end-of-options", "--"}
    marked = [line for line in lines if line.startswith("git ") and markers & {*shlex.split(line)}]
    assert len(marked) == 9, marked
    for n, line in enumerate(marked):
        shelled, direct = tmp_path / f"{n}-shell", tmp_path / f"{n}-argv"
        shutil.copytree(template, shelled, symlinks=True)
        shutil.copytree(template, direct, symlinks=True)
        plain = [word for word in shlex.split(line)[1:] if word not in markers]
        ran, bare = _bash(line, shelled), _git(direct, plain)
        assert (ran.returncode, ran.stdout) == (bare.returncode, bare.stdout), line
        assert _state(shelled) == _state(direct), line


def test_git_reads_each_quoted_range_as_the_branch_against_its_base(tmp_path: Path) -> None:
    name = "feature/$HOME;touch${IFS}landed'`touch${IFS}also`"
    root = _scratch(tmp_path / "repo", name)
    args = {"ref": "4", "base": "main", "branch": name, "rejudge": {**REJUDGE, "checkRed": True}}
    prompts = _run(args, reconcile=IMPLEMENTED, **{"review-again": REVIEW})["prompts"]
    expected = {
        "reconcile": [["diff", f"main...{name}"], ["log", "--oneline", f"{name}..main"]],
        "review-again": [["diff", f"main...{name}"]],
    }
    for label, argvs in expected.items():
        lines = [line for line in _blocks(prompts[label]) if ".." in line]
        assert len(lines) == len(argvs), (label, lines)
        for line, argv in zip(lines, argvs, strict=True):
            ran, direct = _bash(line, root), _git(root, argv)
            split = _git(root, [*argv[:-1], *re.split(r"\.\.\.?", argv[-1])])
            assert direct.returncode == 0, argv
            assert direct.stdout != split.stdout, f"the repository tells {argv} from two words"
            assert (ran.returncode, ran.stdout) == (0, direct.stdout), f"{label}: {line}"
    assert not (root / "landed").exists()
    assert not (root / "also").exists()


def test_no_skill_runs_a_plugin_script_with_a_bare_python3() -> None:
    pages = sorted((PLUGIN_ROOT / "skills").glob("*/SKILL.md"))
    assert pages
    runs = 0
    for page in pages:
        for number, line in enumerate(page.read_text(encoding="utf-8").splitlines(), 1):
            assert not re.search(r"python3 (?!-E -s )\S*scripts/", line), (page.name, number)
            assert "PYTHONPATH=" not in line, (page.name, number)
            assert not re.search(r"python3 (?:\S+ )*-m ", line), (page.name, number)
            runs += line.count(f'{PLUGIN_PY} "${{CLAUDE_PLUGIN_ROOT}}/scripts/')
    assert runs


def test_the_plugin_interpreter_the_prompts_use_is_the_one_the_templates_fall_back_to() -> None:
    justfile = (PLUGIN_ROOT / "skills/adopt/templates/justfile").read_text(encoding="utf-8")
    assert f'plugin_py := if plugin_pyc_ok == "" {{ "{PLUGIN_PY}" }}' in justfile
    assert PLUGIN_PY.startswith(adapter.INTERPRETER)


def test_a_new_blocker_after_a_fix_round_keeps_the_second_round() -> None:
    first = _critique(MAJOR)
    fresh = _critique({**MAJOR, "title": "a second defect", "carries": None})
    ran = _built_run(critique=first, **{"critique-1": fresh, "critique-2": CRITIQUED})
    rounds = ["critique", "fix-1", "critique-1", "fix-2", "critique-2", "review"]
    assert ran["labels"][-6:] == rounds
    assert ran["result"]["verdict"] == "approve"
    assert ran["result"]["carriedStillOwed"] == []


def test_a_minor_and_a_held_finding_are_owed_no_fix_round() -> None:
    held = {**MAJOR, "title": "held", "heldByKeptTest": True}
    first = _critique({**MAJOR, "severity": "minor"}, held)
    ran = _built_run(critique=first)
    assert "fix-1" not in ran["labels"]


def test_a_departure_from_a_criterion_stops_before_the_next_role_starts() -> None:
    departed = {**IMPLEMENTED, "departures": [_departure("criterion")]}
    ran = _built_run(implement=departed, critique=CRITIQUED)
    assert ran["labels"][-1] == "implement"
    result = ran["result"]
    assert (result["verdict"], result["stop"], result["stoppedAfter"]) == (
        "blocked",
        "departure",
        "implement",
    )
    assert result["filesChanged"] == ["src/links.py", "tests/test_expiry.py"]
    assert result["criticFindings"] == []
    assert result["testsAdded"] == []
    assert result["openQuestions"][0]["question"] == CASE_QUESTION
    assert result["departures"][0]["quote"].startswith("1. Given a link")


def test_a_departure_from_an_assumption_is_the_amended_assumption_when_the_role_has_it() -> None:
    amended = {**IMPLEMENTED, "departures": [_departure("assumption", "role")]}
    ran = _built_run(implement=amended, critique=CRITIQUED)
    assert ran["labels"][-2:] == ["critique", "review"]
    result = ran["result"]
    assert result["verdict"] == "approve"
    assert any(a["assumed"] == "Every item" for a in result["assumptions"])
    assert "Every item" in ran["prompts"]["critique"]
    assert "Every item" in ran["prompts"]["review"]
    owners = {**IMPLEMENTED, "departures": [_departure("assumption", "owner")]}
    stopped = _built_run(implement=owners, critique=CRITIQUED)["result"]
    assert (stopped["verdict"], stopped["stop"]) == ("blocked", "departure")


def test_a_fixer_or_a_reconcile_that_departs_from_a_criterion_stops_after_it() -> None:
    departed = {**IMPLEMENTED, "departures": [_departure("criterion")]}
    ran = _built_run(critique=_critique(MAJOR), **{"fix-1": departed})
    assert ran["labels"][-1] == "fix-1"
    assert (ran["result"]["verdict"], ran["result"]["stoppedAfter"]) == ("blocked", "fix-1")
    again = _again({"conflicts": ["flags.toml"]}, reconcile=departed, **{"review-again": REVIEW})
    assert again["labels"] == ["reconcile"]
    assert (again["result"]["verdict"], again["result"]["rejudged"]) == ("blocked", True)


def test_an_entry_the_fork_test_gives_the_owner_blocks_after_the_critic_that_returned_it() -> None:
    ran = _built_run(critique=_critique(unstated=[_case("owner")]))
    assert ran["labels"][-1] == "critique", "no fix round and no review"
    result = ran["result"]
    assert (result["verdict"], result["stop"], result["stoppedAfter"]) == (
        "blocked",
        "question",
        "critique",
    )
    assert [q["question"] for q in result["openQuestions"]] == [CASE_QUESTION]
    assert result["departures"] == []


def test_an_entry_the_fork_test_gives_the_owner_blocks_after_the_reviewer_that_returned_it() -> (
    None
):
    asked = {**REVIEW, "unstated": [_case("owner", of=None)]}
    ran = _run({"ref": "4"}, preflight=YOUNG, spec=SPECIFIED, implement=IMPLEMENTED, review=asked)
    assert ran["labels"] == [*YOUNG_LABELS, "review"]
    result = ran["result"]
    assert (result["verdict"], result["stoppedAfter"]) == ("blocked", "review")
    assert result["reviewIssues"] == []


def test_an_entry_the_fork_test_gives_a_role_reaches_the_next_critic_and_the_review() -> None:
    noted = _critique(MAJOR, unstated=[_case("role")])
    ran = _built_run(critique=noted, **{"critique-1": CRITIQUED})
    for label in ("critique-1", "review", "fix-1"):
        assert CASE_QUESTION in ran["prompts"][label], label
    assert [a["question"] for a in ran["result"]["assumptions"]].count(CASE_QUESTION) == 1


def test_an_implementer_entry_for_the_owner_stops_the_run_after_it() -> None:
    asked = {**IMPLEMENTED, "unstated": [_case("owner")]}
    ran = _built_run(implement=asked)
    assert (ran["labels"][-1], ran["result"]["stop"]) == ("implement", "question")


def test_the_scripts_own_ticket_issues_quote_the_promise_and_the_input_they_name() -> None:
    lost = {**PROMISES[0], "reading": "dropped", "specLine": "no criterion"}
    undecided = {**REVIEWED_LINK, "empty": None}
    review = {**REVIEW, "ticketPromises": [lost, *PROMISES[1:]], "acceptedInputs": [undecided]}
    ran = _run({"ref": "4"}, preflight=YOUNG, spec=SPECIFIED, implement=IMPLEMENTED, review=review)
    by_axis = {i["axis"]: i for i in ran["result"]["ticketFindings"]}
    assert by_axis["ticket"]["breaks"] == {"record": "ticket", "quote": lost["promise"]}
    assert by_axis["input"]["breaks"] == NAMED


def test_the_prompts_state_the_fork_test_and_the_quote_rule_for_each_role_that_needs_them() -> None:
    ran = _built_run(critique=CRITIQUED)
    fork = "An undecided point is the owner's in two cases, and yours in every other."
    for role in ("spec", "implement", "critique", "review"):
        assert fork in ran["prompts"][role], role
    copy = "Copy the unit exactly as the record writes it, markup included and its list marker"
    for role in ("critique", "review"):
        prompt = " ".join(ran["prompts"][role].split())
        assert f"{copy} left off" in prompt, role
        assert "Correct every quote it reports not found" in prompt, role
        assert "quotes.py" in prompt, role
        assert "as `quoteOutput`" in prompt, role
    for role in ("implement", "review", "critique"):
        assert "`unstated` entry" in ran["prompts"][role], role
    for role in ("spec", "ticket-check", "critique"):
        assert "`## Neighbouring cases`" in ran["prompts"][role], role
    assert "Roles of this run decided" not in ran["prompts"]["review"]
    assert "anything the spec left open" not in ran["prompts"]["implement"]
    fixed = _built_run(critique=_critique(MAJOR), **{"critique-1": CRITIQUED})
    assert fork in fixed["prompts"]["fix-1"]
    redone = _again({"conflicts": ["a"]}, reconcile=IMPLEMENTED, **{"review-again": REVIEW})
    assert fork in redone["prompts"]["reconcile"]


def test_the_quote_check_runs_beside_the_preflight_script_when_the_skill_names_it() -> None:
    args = {"ref": "4", "preflight": "/plugin/scripts/feature_preflight.py"}
    ran = _run(args, preflight=PREFLIGHT, spec=SPECIFIED, **DEPTH, critique=CRITIQUED)
    quotes_line = f"{PLUGIN_PY} '/plugin/scripts/quotes.py' <that file> --root ."
    assert quotes_line in ran["prompts"]["critique"]
    assert "--record ticket=" in ran["prompts"]["review"]
    assert "--record ticket=" not in ran["prompts"]["critique"]


def test_the_schemas_carry_breaks_unstated_departures_and_the_quote_output() -> None:
    ran = _built_run(critique=_critique(MAJOR), **{"critique-1": CRITIQUED})
    schemas = ran["schemas"]
    finding = schemas["critique"]["properties"]["findings"]["items"]
    assert finding["properties"]["breaks"]["required"] == ["record", "quote"]
    assert "breaks" not in finding["required"], "a minor may omit it"
    assert "carries" in finding["required"]
    assert "breaks" in schemas["review"]["properties"]["issues"]["items"]["properties"]
    for label in ("critique", "review"):
        assert "quoteOutput" in schemas[label]["required"], label
    for label in ("implement", "fix-1"):
        assert {"unstated", "departures"} <= set(schemas[label]["required"]), label
    unstated = schemas["review"]["properties"]["unstated"]["items"]
    asked = schemas["review"]["properties"]["unstated"]["description"]
    assert "one entry for each moved critic finding the prompt lists" in asked
    assert "none is left unclassified" in asked
    named = schemas["review"]["properties"]["acceptedInputs"]["items"]["properties"]["named"]
    assert "the method's own" in named["description"]
    assert "every case of an input a record names is decided" in named["description"]
    assert "not a promise of the project" in named["description"]
    assert "proof that the input is the record's" in named["description"]
    assert "refuses an input that was invented" in named["description"]
    assert unstated["properties"]["whose"]["enum"] == ["owner", "role"]
    assert "of" in unstated["required"]
    assert "of" not in schemas["critique"]["properties"]["unstated"]["items"]["properties"]
    departure = schemas["implement"]["properties"]["departures"]["items"]
    assert departure["properties"]["from"]["enum"] == ["criterion", "assumption"]
    assert not _valid({**_case("owner"), "whose": "team", "of": None}, unstated)
    assert not _valid({**_case("owner"), "options": ["only one"], "of": None}, unstated)
