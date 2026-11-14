"""The pipeline model's runs played on the real workflow, many runs to one node process.

Each abstract answer stands for one concrete structured answer the workflow's schema allows, built
from the answers ``test_pipeline`` already scripts. The workflow runs in a fresh ``vm`` context
whose only globals are the language's own, with ``agent``, ``phase`` and ``log`` passed in, so an
effect it reached for any other way would end the run in a ``ReferenceError``.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any

from tests.pipeline_model import (
    Action,
    Build,
    Ceiling,
    Check,
    Critique,
    Ending,
    Objection,
    Outcome,
    Preflight,
    Review,
    Role,
    Said,
    Spec,
    Start,
    Stop,
)
from tests.test_pipeline import (
    ASKED,
    CHECKED,
    CRITIQUED,
    GAP,
    IMPLEMENTED,
    NO_SCRIPT,
    PIPELINE,
    PREFLIGHT,
    REJUDGE,
    REVIEW,
    SPECIFIED,
    YOUNG,
)

TICKET_FILE = ".scratch/list/issues/01-save-a-link.md"
BRANCH = "feature/4-expiry"
ARGS: dict[Start, object] = {
    Start.BUILD: {"ref": "4"},
    Start.NO_TICKET: None,
    Start.REJUDGE: {"ref": "4", "branch": BRANCH, "rejudge": REJUDGE},
    Start.REJUDGE_CONFLICT: {
        "ref": "4",
        "branch": BRANCH,
        "rejudge": {**REJUDGE, "conflicts": ["flags.toml"]},
    },
    Start.REJUDGE_RED: {"ref": "4", "branch": BRANCH, "rejudge": {**REJUDGE, "checkRed": True}},
    Start.REJUDGE_MISSING: {"ref": "4", "branch": BRANCH, "rejudge": {"slug": "expiry"}},
}
LOCAL = {"ref": TICKET_FILE, "base": "main"}


def sized(lines: int, tests: int) -> dict[str, int]:
    return {"lines": lines, "ceiling": 1000, "testLines": tests, "testCeiling": 3000}


FITS = sized(100, 100)
PARTS = [
    {"title": "save a link", "criteria": ["1"], "promises": ["Saving a link answers `303`"]},
    {"title": "list them", "criteria": ["2"], "promises": ["The page lists the unread items"]},
]
OPEN = {
    "severity": "major",
    "title": "loses the order",
    "evidence": "two saves in one second came back reversed",
    "suggestedTest": "tests/critique/expiry.core.critique.test.py",
    "testFails": False,
    "heldByKeptTest": False,
}
BLOCKER = {
    "axis": "spec",
    "severity": "blocker",
    "title": "drops an expense with exit 0",
    "location": "src/links.py:9",
    "evidence": "a blank link is saved",
}
SPECS: dict[Said, object] = {
    Spec.ASKS: {**SPECIFIED, "openQuestions": [ASKED]},
    Spec.GUARD_EDIT: {
        **SPECIFIED,
        "guardEdits": [{"path": "guards/manifest.toml", "edit": "add src/links to a group"}],
    },
    Spec.PRODUCT_OVER: {**SPECIFIED, "changeSize": sized(1001, 100)},
    Spec.TESTS_OVER: {**SPECIFIED, "changeSize": sized(100, 3001)},
    Spec.TESTS_MARGIN: {**SPECIFIED, "changeSize": sized(100, 2500)},
    Spec.SPLIT: {
        **SPECIFIED,
        "changeSize": FITS,
        "changeEstimate": {
            "implementationLines": 950,
            "criticLines": 0,
            "recordLines": 0,
            "proposedSplit": PARTS,
        },
    },
    Spec.FITS: {**SPECIFIED, "changeSize": FITS},
    Spec.WRONG_REASON: {**SPECIFIED, "changeSize": FITS, "failingForRightReason": False},
}
BUILDS: dict[Said, object] = {
    Build.PRODUCT_OVER: {**IMPLEMENTED, "changeSize": sized(1001, 100)},
    Build.TESTS_OVER: {**IMPLEMENTED, "changeSize": sized(100, 3001)},
    Build.TESTS_MARGIN: {**IMPLEMENTED, "changeSize": sized(100, 2500)},
    Build.GREEN: {**IMPLEMENTED, "changeSize": FITS},
    Build.RED: {**IMPLEMENTED, "changeSize": FITS, "checkPasses": False},
}
REVIEWS: dict[Said, object] = {
    Review.APPROVES: REVIEW,
    Review.REFUSES: {**REVIEW, "verdict": "request_changes"},
    Review.APPROVES_RED: {**REVIEW, "checkPasses": False},
    Review.APPROVES_ISSUE: {**REVIEW, "issues": [BLOCKER]},
    Review.APPROVES_DROPPED: {**REVIEW, "ticketPromises": GAP["ticketPromises"]},
    Review.APPROVES_GUARD: {**REVIEW, "guardChanges": ["guards/manifest.toml"]},
}
CONCRETE: dict[Said, object] = {
    Preflight.RED: {
        "ok": False,
        "number": None,
        "title": "",
        "slug": "",
        "branch": "",
        "label": "",
        "failures": [{"check": "label", "detail": "unlabelled", "produce": "the owner labels"}],
        "output": "",
    },
    Preflight.NO_SLUG: {**PREFLIGHT, "slug": ""},
    Preflight.YOUNG: YOUNG,
    Preflight.DEPENDED_ON: {**PREFLIGHT, "critic": True, "critic_reason": "others rely on it"},
    **SPECS,
    Check.NO_GAP: CHECKED,
    Check.GAP: GAP,
    **BUILDS,
    Critique.CLEAR: CRITIQUED,
    Critique.SILENT: {**CRITIQUED, "attempted": []},
    Critique.OWED: {**CRITIQUED, "findings": [OPEN], "testsAdded": [OPEN["suggestedTest"]]},
    **REVIEWS,
}


def key(answer: Said) -> str:
    return f"{type(answer).__name__}.{answer.name}"


HARNESS = """
const fs = require('fs')
const vm = require('vm')
const source = fs.readFileSync(process.argv[1], 'utf8').replace('export const meta', 'const meta')
const pipeline = vm.runInContext(
  `(async function (args, agent, phase, log) {\\n${source}\\n})`, vm.createContext({}))
const { catalogue, runs } = JSON.parse(fs.readFileSync(0, 'utf8'))
const COMMAND = /`(git [a-z-]+|gh [a-z]+ [a-z]+)/g
const PUBLISHING = /\\b(?:git (?:commit|push)|gh pr)\\b/g
const commands = {}
const scanned = new Set()
function scan(label, prompt) {
  const seen = `${label} ${prompt.length}`
  if (scanned.has(seen)) return
  scanned.add(seen)
  const role = label.replace(/-\\d+$/, '')
  commands[role] = commands[role] || new Set()
  for (const found of [...prompt.matchAll(COMMAND)].map(m => m[1])) commands[role].add(found)
  for (const found of prompt.match(PUBLISHING) || []) commands[role].add(found)
}
function kept(result) {
  const too = result.tooLarge
  return {
    verdict: result.verdict,
    ref: result.ref ?? null,
    rejudged: result.rejudged === true,
    tooLarge: too
      ? {
          kind: too.kind,
          stage: too.stage,
          parts: (too.proposedSplit || []).length,
          instructions: (result.requiredByOrchestrator || []).join('\\n'),
        }
      : null,
    questions: (result.openQuestions || []).length,
    guardEdits: (result.guardEdits || []).length,
    preflight: Boolean(result.preflight),
    forcedBy: result.verdictForcedBy || [],
    iterations: result.iterations ?? 0,
    unresolved: (result.unresolvedBlockingFindings || []).length,
    ticketFindings: (result.ticketFindings || []).length,
    assumptions: (result.assumptions || []).map(a => a.question),
    record: result.verdictRecord ? result.verdictRecord.verdict : null,
  }
}
async function main() {
  const played = []
  for (const run of runs) {
    const calls = []
    const agent = async (prompt, options) => {
      calls.push([options.label, options.phase])
      scan(options.label, prompt)
      const answer = catalogue[run.answers[options.label]]
      return answer == null ? null : JSON.parse(JSON.stringify(answer))
    }
    try {
      played.push({ calls, result: kept(await pipeline(run.args, agent, () => {}, () => {})) })
    } catch (error) {
      played.push({ calls, error: { name: error.name, message: String(error.message) } })
    }
  }
  const effects = Object.fromEntries(
    Object.entries(commands).map(([role, found]) => [role, [...found].sort()]))
  console.log(JSON.stringify({ played, effects }))
}
main()
"""


@dataclass(frozen=True)
class Played:
    """What the real workflow did on one run: its agent calls with their phases, and its end."""

    calls: tuple[tuple[str, str], ...]
    ending: Ending
    raw: dict[str, Any]


@dataclass(frozen=True)
class Batch:
    played: list[Played]
    effects: dict[Role, frozenset[str]]
    """Per role, every git or forge command any of its prompts named."""


def expected_calls(actions: list[Action]) -> tuple[tuple[str, str], ...]:
    return tuple(
        (action.label, action.phase.value)
        for action in actions
        if action.role is not None and action.phase is not None
    )


def scripted(actions: list[Action], *, local: bool = False) -> dict[str, object]:
    """The workflow's arguments and each agent's answer, by label, for the run ``actions`` take."""
    start, *answers = actions
    assert isinstance(start.answer, Start)
    args = ARGS[start.answer]
    if local and isinstance(args, dict):
        args = {**args, **LOCAL}
    return {"args": args, "answers": {action.label: key(action.answer) for action in answers}}


def play(runs: list[list[Action]], local: list[list[Action]] | None = None) -> Batch:
    """Each run on the real workflow, then each of ``local`` again reading a local ticket file."""
    batch = [scripted(actions) for actions in runs]
    batch += [scripted(actions, local=True) for actions in local or []]
    catalogue = {key(answer): concrete for answer, concrete in CONCRETE.items()}
    node = shutil.which("node")
    assert node, "node runs the workflow script"
    ran = subprocess.run(  # noqa: S603 # -- a fixed argv, no shell
        [node, "-e", HARNESS, str(PIPELINE)],
        input=json.dumps({"catalogue": catalogue, "runs": batch}),
        capture_output=True,
        text=True,
        check=True,
    )
    out = json.loads(ran.stdout)
    played = [
        Played(tuple((label, phase) for label, phase in raw["calls"]), ending(raw), raw)
        for raw in out["played"]
    ]
    effects = {Role(role): frozenset(found) for role, found in out["effects"].items()}
    return Batch(played, effects)


FORCED = {
    "guard paths changed without": Objection.GUARD,
    "unresolved blocker/major critic finding": Objection.OWED,
    "blocker/major review issue": Objection.REVIEW_ISSUE,
    "the last implement/fix result reported checkPasses: false": Objection.BUILDER_RED,
    "the review's run of check on the final tree was not green": Objection.REVIEW_RED,
    "the review reported checkPasses: false": Objection.REVIEW_RED,
    "the reconcile result reported checkPasses: false": Objection.RECONCILE_RED,
    "the spec reported failingForRightReason: false": Objection.SPEC_EVIDENCE,
    "the critic found nothing and lists nothing attempted": Objection.SILENT,
}
STAGES = {
    "the spec": Role.SPEC,
    "the implementation": Role.IMPLEMENT,
    "fix round 1": Role.FIX,
    "fix round 2": Role.FIX,
    "the reconcile": Role.RECONCILE,
}
DIED = re.compile(r"^(.+) agent returned no result")
RESUME = "resume the run"
# Why: the "Too large" section of skills/feature/SKILL.md has the orchestrator split the ticket and
# ask nothing. These are the words of the result that tell it so, with parts named and without.
SPLIT_UNASKED = (
    "the orchestrator splits it and the owner is not asked",
    "with no `proposedSplit`, divide the ticket's promises yourself",
)


def objection(reason: str) -> Objection:
    found = [objection for text, objection in FORCED.items() if text in reason]
    if len(found) != 1:
        message = f"the model knows no objection the script means by {reason!r}"
        raise ValueError(message)
    return found[0]


def failure(error: dict[str, str]) -> str:
    """The agent the script's own error names, ``refused`` for its other errors, else the crash."""
    if error["name"] != "Error":
        return f"crash {error['name']}: {error['message']}"
    died = DIED.match(error["message"])
    return died.group(1) if died else "refused"


def stopped(result: dict[str, Any]) -> Ending:
    too = result["tooLarge"]
    if too:
        ceiling, stage = Ceiling(too["kind"]), STAGES[too["stage"]]
        unasked = all(words in too["instructions"] for words in SPLIT_UNASKED)
        return Ending(
            Outcome.BLOCKED,
            Stop.TOO_LARGE,
            ceiling,
            stage,
            parts=too["parts"] > 0,
            split_unasked=unasked,
            rejudged=result["rejudged"],
        )
    stops = [
        (Stop.QUESTION, result["questions"] > 0),
        (Stop.GUARD_EDIT, result["guardEdits"] > 0),
        (Stop.NO_TICKET, result["preflight"] and result["ref"] is None),
        (Stop.PREFLIGHT, result["preflight"]),
    ]
    return Ending(Outcome.BLOCKED, stop=next(stop for stop, said in stops if said))


def ending(raw: dict[str, Any]) -> Ending:
    """How a real run ended, as the model tells it."""
    if "error" in raw:
        error = raw["error"]
        return Ending(Outcome.ERROR, error=failure(error), resume=RESUME in error["message"])
    result = raw["result"]
    outcome = Outcome(result["verdict"])
    if outcome is Outcome.BLOCKED:
        return stopped(result)
    return Ending(
        outcome,
        objections=frozenset(map(objection, result["forcedBy"])),
        rounds=result["iterations"],
        unresolved=result["unresolved"] > 0,
        lost=result["ticketFindings"] > 0,
        kept=NO_SCRIPT in result["assumptions"],
        record=Outcome(result["record"]),
        rejudged=result["rejudged"],
    )
