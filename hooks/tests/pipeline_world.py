from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass, replace
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
    CASE_QUESTION,
    CHECKED,
    CRITIQUED,
    GAP,
    IMPLEMENTED,
    INVENTED,
    NO_SCRIPT,
    PIPELINE,
    PREFLIGHT,
    REJUDGE,
    REVIEW,
    SPECIFIED,
    YOUNG,
    _case,
    _departure,
    _quoted,
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
    "carries": None,
    "evidence": "two saves in one second came back reversed",
    "suggestedTest": "tests/critique/expiry.core.critique.test.py",
    "testFails": False,
    "heldByKeptTest": False,
}
PREVIOUS = "@previous"
WAS = "@title"
NOW = "@now"
MOVED = "@moved"
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


def reviewed(**fields: object) -> dict[str, Any]:
    return _quoted({**REVIEW, **fields}, "issues")


BUILDS: dict[Said, object] = {
    Build.DEPARTS_CRITERION: {
        **IMPLEMENTED,
        "changeSize": FITS,
        "departures": [_departure("criterion")],
    },
    Build.DEPARTS_ASSUMPTION_ROLE: {
        **IMPLEMENTED,
        "changeSize": FITS,
        "departures": [_departure("assumption", "role")],
    },
    Build.DEPARTS_ASSUMPTION_OWNER: {
        **IMPLEMENTED,
        "changeSize": FITS,
        "departures": [_departure("assumption", "owner")],
    },
    Build.PRODUCT_OVER: {**IMPLEMENTED, "changeSize": sized(1001, 100)},
    Build.TESTS_OVER: {**IMPLEMENTED, "changeSize": sized(100, 3001)},
    Build.TESTS_MARGIN: {**IMPLEMENTED, "changeSize": sized(100, 2500)},
    Build.GREEN: {**IMPLEMENTED, "changeSize": FITS},
    Build.RED: {**IMPLEMENTED, "changeSize": FITS, "checkPasses": False},
}
REVIEWS: dict[Said, object] = {
    Review.APPROVES: REVIEW,
    Review.REFUSES: {**REVIEW, "verdict": "request_changes"},
    Review.REFUSES_ISSUE: reviewed(verdict="request_changes", issues=[BLOCKER]),
    Review.REFUSES_MINOR: {
        **REVIEW,
        "verdict": "request_changes",
        "issues": [{**BLOCKER, "severity": "minor"}],
    },
    Review.REFUSES_DROPPED: reviewed(
        verdict="request_changes", issues=[BLOCKER], ticketPromises=GAP["ticketPromises"]
    ),
    Review.APPROVES_RED: {**REVIEW, "checkPasses": False},
    Review.APPROVES_ISSUE: reviewed(issues=[BLOCKER]),
    Review.APPROVES_DROPPED: {**REVIEW, "ticketPromises": GAP["ticketPromises"]},
    Review.APPROVES_GUARD: {**REVIEW, "guardChanges": ["guards/manifest.toml"]},
    Review.UNSTATED_OWNER: {**REVIEW, "unstated": [_case("owner", of=None)]},
    Review.ISSUE_NO_QUOTE: reviewed(
        verdict="request_changes", issues=[{**BLOCKER, "breaks": INVENTED}]
    ),
    Review.CLASSIFIES_ROLE: {**REVIEW, "unstated": [_case("role", of=MOVED)]},
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
    Critique.OWED: _quoted(
        {
            **CRITIQUED,
            "findings": [{**OPEN, "title": f"{OPEN['title']} {NOW}"}],
            "testsAdded": [OPEN["suggestedTest"]],
        }
    ),
    Critique.OWED_RELABELED: _quoted(
        {
            **CRITIQUED,
            "findings": [{**OPEN, "title": WAS}],
            "testsAdded": [OPEN["suggestedTest"]],
        }
    ),
    Critique.OWED_CARRIED: _quoted(
        {
            **CRITIQUED,
            "findings": [{**OPEN, "carries": PREVIOUS, "title": WAS}],
            "testsAdded": [OPEN["suggestedTest"]],
        }
    ),
    Critique.NO_QUOTE: _quoted({**CRITIQUED, "findings": [{**OPEN, "breaks": INVENTED}]}),
    Critique.UNSTATED_OWNER: {**CRITIQUED, "unstated": [_case("owner")]},
    Critique.UNSTATED_ROLE: {**CRITIQUED, "unstated": [_case("role")]},
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
const ROUNDS = /\\(round \\d+ of (\\d+)\\)/
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
const CHECKED = new Set([
  'type', 'properties', 'required', 'items', 'enum', 'pattern', 'minItems', 'anyOf',
  'additionalProperties', 'description',
])
function is(type, value) {
  if (type === 'null') return value === null
  if (type === 'integer') return Number.isInteger(value)
  if (type === 'array') return Array.isArray(value)
  if (type === 'object') return value !== null && typeof value === 'object' && !Array.isArray(value)
  return typeof value === type
}
function refused(schema, value, at) {
  const unread = Object.keys(schema).filter(word => !CHECKED.has(word))
  if (unread.length) return [`${at}: the harness does not check ${unread.join(', ')}`]
  if (schema.anyOf) {
    const each = schema.anyOf.map(option => refused(option, value, at))
    if (each.some(found => !found.length)) return []
    return [`${at}: fits no option: ${each.map(found => found[0]).join('; ')}`]
  }
  const types = schema.type === undefined ? [] : [].concat(schema.type)
  const shown = JSON.stringify(value)
  if (types.length && !types.some(type => is(type, value))) {
    return [`${at}: ${shown} is not ${types.join(' or ')}`]
  }
  const found = []
  if (schema.enum && !schema.enum.includes(value)) {
    found.push(`${at}: ${shown} is not one of ${schema.enum.join(', ')}`)
  }
  if (schema.pattern && typeof value === 'string' && !new RegExp(schema.pattern).test(value)) {
    found.push(`${at}: ${shown} does not match ${schema.pattern}`)
  }
  if (Array.isArray(value)) {
    if (value.length < (schema.minItems ?? 0)) found.push(`${at}: fewer than ${schema.minItems}`)
    const items = schema.items
    if (items) value.forEach((item, n) => found.push(...refused(items, item, `${at}[${n}]`)))
  }
  if (is('object', value)) {
    for (const name of schema.required || []) {
      if (!(name in value)) found.push(`${at}: lacks ${name}`)
    }
    for (const [name, inner] of Object.entries(value)) {
      const property = (schema.properties || {})[name]
      if (property) found.push(...refused(property, inner, `${at}.${name}`))
      else if (schema.additionalProperties === false) found.push(`${at}: may not have ${name}`)
    }
  }
  return found
}
const broken = {}
const shapes = {}
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
    stop: result.stop ?? null,
    built: (result.filesChanged || []).length,
    stoppedAfter: result.stoppedAfter ?? null,
    carried: (result.carriedStillOwed || []).length,
    unclassified: result.unclassified ?? 0,
    unquotedReview: result.unquotedReviewIssues ?? 0,
    reviewRounds: (result.reviewIssuesByRound || []).length,
    unstated: (result.unstated || []).filter(e => e.of).length,
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
    const told = []
    let lastTitle = 'loses the order'
    const agent = async (prompt, options) => {
      calls.push([options.label, options.phase])
      const rounds = options.label.startsWith('fix-') && prompt.match(ROUNDS)
      if (rounds) told.push([options.label, Number(rounds[1])])
      scan(options.label, prompt)
      const name = run.answers[options.label]
      const answer = catalogue[name]
      if (answer == null) return null
      const round = Number((options.label.match(/-(\\d+)$/) || [0, 0])[1])
      const moved = (prompt.match(/"id": "(C\\d+-\\d+)"/) || [])[1]
      const text = JSON.stringify(answer)
        .replace(/"@previous"/g, JSON.stringify(`C${round - 1}-1`))
        .replace(/@title/g, lastTitle)
        .replace(/@now/g, `round ${round}`)
        .replace(/"@moved"/g, JSON.stringify(moved || 'no id in the prompt'))
      const found = options.schema ? refused(options.schema, JSON.parse(text), name) : []
      if (found.length) broken[`${options.label} ${name}`] = found
      const parsed = JSON.parse(text)
      const first = (parsed.findings || [])[0]
      if (options.label.startsWith('critique') && first) lastTitle = first.title
      return parsed
    }
    try {
      const full = await pipeline(run.args, agent, () => {}, () => {})
      shapes[Object.keys(full).sort().join(',')] ??= full
      const result = kept(full)
      played.push({ calls, told, result })
    } catch (error) {
      played.push({ calls, told, error: { name: error.name, message: String(error.message) } })
    }
  }
  const effects = Object.fromEntries(
    Object.entries(commands).map(([role, found]) => [role, [...found].sort()]))
  console.log(JSON.stringify({ played, effects, broken, shapes: Object.values(shapes) }))
}
main()
"""


@dataclass(frozen=True)
class Played:
    calls: tuple[tuple[str, str], ...]
    ending: Ending
    raw: dict[str, Any]
    told: tuple[tuple[str, int], ...]


@dataclass(frozen=True)
class Batch:
    played: list[Played]
    effects: dict[Role, frozenset[str]]
    results: list[dict[str, Any]]


def expected_calls(actions: list[Action]) -> tuple[tuple[str, str], ...]:
    return tuple(
        (action.label, action.phase.value)
        for action in actions
        if action.role is not None and action.phase is not None
    )


def scripted(actions: list[Action], *, local: bool = False) -> dict[str, object]:
    start, *answers = actions
    assert isinstance(start.answer, Start)
    args = ARGS[start.answer]
    if local and isinstance(args, dict):
        args = {**args, **LOCAL}
    return {"args": args, "answers": {action.label: key(action.answer) for action in answers}}


def play(runs: list[list[Action]], local: list[list[Action]] | None = None) -> Batch:
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
    refused = [f"{call}: {line}" for call, lines in out["broken"].items() for line in lines]
    assert not refused, "answers the workflow's schema refuses:\n" + "\n".join(refused)
    played = [
        Played(
            tuple((label, phase) for label, phase in raw["calls"]),
            ending(raw),
            raw,
            tuple((label, rounds) for label, rounds in raw["told"]),
        )
        for raw in out["played"]
    ]
    effects = {Role(role): frozenset(found) for role, found in out["effects"].items()}
    return Batch(played, effects, out["shapes"])


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
ROUND = re.compile(r"-(\d+)$")
DIED = re.compile(r"^(.+) agent returned no result")
RESUME = "resume the run"
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
    if result["stop"]:
        return Ending(
            Outcome.BLOCKED,
            stop=Stop.DEPARTURE if result["stop"] == "departure" else Stop.QUESTION_AFTER_BUILD,
            after=Role(ROUND.sub("", result["stoppedAfter"])),
            asks=result["questions"] > 0,
            rejudged=result["rejudged"],
            unclassified=result["unclassified"] > 0,
            unquoted_review=result["unquotedReview"] > 0,
        )
    stops = [
        (Stop.QUESTION, result["questions"] > 0),
        (Stop.GUARD_EDIT, result["guardEdits"] > 0),
        (Stop.NO_TICKET, result["preflight"] and result["ref"] is None),
        (Stop.PREFLIGHT, result["preflight"]),
    ]
    return Ending(Outcome.BLOCKED, stop=next(stop for stop, said in stops if said))


def ending(raw: dict[str, Any]) -> Ending:
    if "error" in raw:
        error = raw["error"]
        return Ending(Outcome.ERROR, error=failure(error), resume=RESUME in error["message"])
    result = raw["result"]
    outcome = Outcome(result["verdict"])
    if outcome is Outcome.BLOCKED:
        return replace(stopped(result), review_rounds=result["reviewRounds"] > 0)
    parked = result["stop"] == "carried"
    return Ending(
        outcome,
        stop=Stop.CARRIED if parked else None,
        after=Role(ROUND.sub("", result["stoppedAfter"])) if parked else None,
        asks=parked and result["questions"] > 0,
        unclassified=result["unclassified"] > 0,
        unquoted_review=result["unquotedReview"] > 0,
        review_rounds=result["reviewRounds"] > 0,
        objections=frozenset(map(objection, result["forcedBy"])),
        rounds=result["iterations"],
        unresolved=result["unresolved"] > 0,
        lost=result["ticketFindings"] > 0,
        kept=NO_SCRIPT in result["assumptions"],
        record=Outcome(result["record"]),
        rejudged=result["rejudged"],
        carried=result["carried"] > 0,
        unstated=result["unstated"] > 0,
        assumes_role=CASE_QUESTION in result["assumptions"],
    )
