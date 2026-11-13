// What this workflow assumes of the repository that adopts the Ratchet method.
// It is shipped by the ratchet plugin and the /ratchet:feature skill runs it in any repository,
// so it knows nothing about the project's domain, runner or forge. It relies on the method's
// entry points and file layout only. The repository must provide: docs/process/binding.md,
// whose Entry points table names the command for `check` (tier 0 plus tier 1, non-zero on
// any failure) and `guard-diff` (prints every guard path the branch changed, one per line);
// the guard manifest at guards/manifest.toml; a docs/features/ directory for the spec;
// a docs/disputes/ directory; AGENTS.md at the root stating the non-negotiables; and
// GLOSSARY.md (or GLOSSARY-MAP.md) stating the vocabulary. The feature's input is a tracker
// ticket (mattpocock-skills:to-spec, to-tickets or triage wrote it) carrying a `## Conjecture`
// section and the owner's agent-ready label: the plugin's scripts/feature_preflight.py says
// whether it does, and nothing else runs until it says so. Every agent loads the
// ratchet:method skill first and takes every project-specific rule from those files, never
// from this script. Each role works with the skill pipeline.md names for it.
//
// A Workflow script cannot read a file or run a command itself, so the preflight runs twice:
// once in the /ratchet:feature skill, deterministically, before this workflow is invoked, and
// once here as the first stage, through one low-effort agent, so the pipeline cannot be
// entered around the skill.
//
// The critic and the fix loop run only for a project people depend on (ADR 0028): the preflight's
// `critic` field, true when the binding declares the `depended-on` life event. Before that a
// ticket is built by spec, the ticket check, implement and review, and the merge rule is unchanged.

export const meta = {
  name: 'pipeline',
  description: 'Build one feature from an agent-ready ticket with the Ratchet method: preflight -> spec -> ticket check (one amend of the spec when it finds a gap) -> implement -> adversarial critique -> fix loop (only when the project is depended-on) -> independent review',
  whenToUse: 'Run by the /ratchet:feature skill with args { ref, branch, base?, root?, preflight?, ticketFindings? } after its own preflight is green; not by hand. ticketFindings are the last run\'s, when its review found the build lost a promise of the ticket. With args.rejudge = { slug, specPath, conflicts?, checkRed?, dependencies?, assumptions?, settled? } it judges again a branch that was brought up to date behind another merge: Reconcile when the rebase conflicted or check went red, then Review. Never commits, pushes or opens a PR; the orchestrator does that after reading the result.',
  phases: [
    { title: 'Preflight', detail: 'scripts/feature_preflight.py on the ticket: open, `## Conjecture` stated, agent-ready label, clean tree; stops with verdict "blocked" naming what produces each missing piece' },
    { title: 'Spec', detail: 'ticket -> docs/features/<slug>/spec.md + failing tests; stops with verdict "blocked" if the ticket leaves open questions' },
    { title: 'Ticket check', detail: 'one agent reads only the ticket and spec.md: each promise covered, dropped, bent or assumed, each input case decided or not; a gap goes back to the spec role once; a promise the amend still leaves out is built as the ticket says it, and an input case still undecided with the safe answer, each listed with what the run decided; no question to the owner comes from the check' },
    { title: 'Implement', detail: 'make the new tests pass test-first with tdd; check green' },
    { title: 'Critique', detail: 'only when the preflight says critic: true (the binding declares depended-on): one adversary with the rule of evidence: properties, edge cases, mutation, fuzz, fitness sabotage as binding.md names them' },
    { title: 'Fix', detail: 'only with the Critique: fresh implementer per blocker/major round (max 2), re-critique after each' },
    { title: 'Reconcile', detail: 'rejudge only: the rebase onto the moved default branch finished and check green again' },
    { title: 'Review', detail: 'independent code-review of the diff on the spec, standards, guard, ticket and input axes; the script forces the verdict from the evidence' },
  ],
}

// The most agents one run spawns. scripts/drain.py reads this line to state what a drain costs, so
// it stays one JSON object on one line; a test runs the script with every agent at its worst and
// counts them. young: preflight, spec, the ticket check with its one amend and second read,
// implement and review. dependedOn: those, the critic and MAX_FIX_ITERATIONS rounds of fix and
// re-critique. rejudge: the reconcile agent and the second review.
const AGENTS_AT_MOST = {"young": 7, "dependedOn": 12, "rejudge": 2}

// ---------------------------------------------------------------------------
// Arguments. Everything that could vary between runs comes in via args so the
// script is a pure function of (args, agent results) and resumes cleanly.
// ---------------------------------------------------------------------------
// The slash form of a workflow passes no args, so a missing ref is answered, not thrown: the
// result says what to run instead, and no agent has been spawned.
const ARGS = typeof args === 'string' ? { ref: args } : (args && typeof args === 'object' ? args : {})
const REF = typeof ARGS.ref === 'string' ? ARGS.ref.trim() : ''
if (!REF) {
  return {
    verdict: 'blocked',
    preflight: { ok: false, failures: [{ check: 'ticket', detail: 'no ticket ref was passed', produce: 'run the /ratchet:feature skill with the ticket number or path; it runs the preflight and passes args = { ref, branch, base }' }] },
    requiredByOrchestrator: ['Run `/ratchet:feature <ticket>`; the slash form of this workflow passes no args and spawns nothing'],
  }
}
// With no base passed, the preflight reads the default branch and the run takes it from there.
let BASE = typeof ARGS.base === 'string' && ARGS.base ? ARGS.base : ''
const ROOT = typeof ARGS.root === 'string' && ARGS.root ? ARGS.root : ''
const PREFLIGHT_SCRIPT = typeof ARGS.preflight === 'string' && ARGS.preflight ? ARGS.preflight : ''
const MAX_FIX_ITERATIONS = 2
// A branch brought up to date behind another merge is judged again, not built again (ADR 0023):
// the skill passes what the first run returned, and only Reconcile and Review run.
const REJUDGE = ARGS.rejudge && typeof ARGS.rejudge === 'object' ? ARGS.rejudge : null
// The readings the first run's ticket check settled (its result's `settled`): the second review is
// held to them as the first was, so a case the run assumed is not read as open again.
const SETTLED = REJUDGE && REJUDGE.settled && typeof REJUDGE.settled === 'object' ? REJUDGE.settled : null
// What the last run's review found the build lost of the ticket (its result's ticketFindings), so a
// run after a refusal starts from them instead of writing the same spec again.
const TICKET_FINDINGS = Array.isArray(ARGS.ticketFindings) ? ARGS.ticketFindings.filter(Boolean) : []
// The branch is the preflight's to derive (feature/<number>-<slug>) unless the skill passed it.
let BRANCH = typeof ARGS.branch === 'string' && ARGS.branch ? ARGS.branch : ''
let SLUG = ''
// Whether the critic and the fix loop run: the preflight says (ADR 0028). A preflight that does
// not say runs them, since the older script predates the field and more scrutiny is the safe side.
let CRITIC = true
let CRITIC_REASON = ''

const ROOT_HINT = ROOT
  ? `the repository root (${ROOT}, the directory holding AGENTS.md)`
  : 'the repository root (the directory holding AGENTS.md)'

// ---------------------------------------------------------------------------
// Shared prompt fragments
// ---------------------------------------------------------------------------
// Every role begins here. The method and the repository's own binding files are the source
// of every project-specific rule; this script states none of them.
const PREAMBLE = 'Load the `ratchet:method` skill (the method this repository follows), then read AGENTS.md, docs/process/binding.md and GLOSSARY.md (or GLOSSARY-MAP.md). Obey them. The binding\'s Entry points table gives the command behind every entry point named below (`check`, `guard-diff`); "run `check`" means run that command from the repository root.'

// Guards and gate disputes. The harness enforces this; the prompt says it so an agent reads a
// block as the dispute signal the first time.
// Why: the reviewer had no definition and filed a binary that drops an expense with exit 0 as
// minor; critic and reviewer now grade by the same words.
const SEVERITY = 'Severity: blocker = wrong result (a wrong output, or a wrong input accepted with exit 0), data loss, a violated AGENTS.md non-negotiable, a declared-path or contract violation; major = unhandled edge case the spec covers, survived mutant on a stated invariant, missing contract; minor = everything else. A defect the spec defers to a later ticket is still graded by what it does to a user now.'

const GUARD_RULES = `
Guard paths and gate disputes:
- The files that define the quality gates are guard paths: the groups and patterns in
  \`guards/manifest.toml\`. Run \`guard-diff\` to list the ones your branch changed.
- \`guard-diff\` prints one line per changed guard path (group, a tab, the path). Under a
  dependency manifest, a lockfile or a snapshot of one it adds an indented note:
  \`dependencies added:\` with each new package, \`lockfile:\` with each package's versions, or
  \`not dependency-only:\` with the reason. A dependency is added with the package manager,
  never by editing the manifest, and each one is named in spec.md's dependencies. The script
  treats a manifest whose note is \`dependencies added:\` naming only packages spec.md names,
  and a \`lockfile:\` path beside one, as decided by the spec; every other guard path, a
  \`not dependency-only:\` one included, stays a guard change.
- The glossary and the ADRs are records you may add to, where \`guards/manifest.toml\` marks
  their group \`edit = "append"\`: a new term with its \`_Avoid_\` list, a new \`_Avoid_\`
  item, a new ADR under the next free number. The hook lets such an Edit or Write through and
  asks on anything else: changing or removing a term, a definition or an \`_Avoid_\` item,
  editing an ADR the default branch holds, a new ADR that supersedes another or carries a
  \`Relaxes:\` line. Under each record \`guard-diff\` prints \`additions:\` with what came in,
  or \`not additive:\` with the reason. The script treats an \`additions:\` path as no guard
  change; a \`not additive:\` one stays a guard change.
- Guard paths change by the owner's hand only. A PreToolUse hook holds that line: an edit to
  a guard path waits for an approval only the owner can give, and a shell write to one is
  denied. A denied or waiting call is the dispute signal: stop on that gate there.
- A red gate is answered by changing the code: split the unit, write the test, narrow the
  change. Thresholds, suppressions, a test's assertions and a gate's config stay as they are.
- When you believe the gate, not the code, is wrong, write a gate dispute at
  \`docs/disputes/NNNN-<slug>.md\` with the method's template: NNNN is one more than the
  highest existing number (0001 for the first); the record opens with Status: open, who
  raised it and the date, then the sections Gate, Exact failure, Why the gate is wrong,
  Options and Recommendation. Name every guard path the dispute covers in its Gate section.
  Report it in your structured result (the \`disputes\` field where your schema has one,
  otherwise your notes or findings) and carry on with the work the gate leaves open. The
  owner decides.
`

// Why: one `check` took 70 to 95 seconds on the first service built this way and every role ran
// it several times. A role whose tree is red on purpose (the spec's tests fail until the
// implementation exists; the critic keeps tests that fail) cannot get a green `check` anyway, so
// it runs tier 0 and only the tests it wrote. Implement, fix and review run the full `check`,
// and the review's run on the final tree is the one that counts.
const CHECK_RULE = `- Before returning, run \`check\` from the repository root and include the LAST ~40 lines of
  its output verbatim in the \`checkOutput\` field of your structured result.`
const OWN_TESTS_RULE = `- Your tree is red on purpose, so do not run \`check\`. Before returning, run the \`tier0\`
  entry point from the repository root, then only the tests you wrote, with the test runner
  binding.md names. Include the LAST ~40 lines of that output verbatim in the \`checkOutput\`
  field of your structured result. The implementer, the fixer and the reviewer run the full
  \`check\`; the reviewer's run on the final tree is the one that counts.`

function commonRules(rebasing, ownTestsOnly) {
  const uncommitted = rebasing
    ? `- The branch holds the change as commits. Every edit you make stays uncommitted on the
  working tree; the orchestrator commits it. The git commands you run are read-only, plus the
  \`git switch\` above and the three that finish the rebase: \`git rebase ${BASE}\`,
  \`git add <a file you resolved>\` and \`git rebase --continue\`.`
    : `- Every change you make stays uncommitted on the working tree; the orchestrator commits and
  publishes. The git commands you run are read-only, plus the \`git switch\` above.`
  return `
Ground rules for every agent in this pipeline:
- Work in ${ROOT_HINT} on branch \`${BRANCH}\`.
  First run \`git rev-parse --abbrev-ref HEAD\`; if it does not print \`${BRANCH}\`, run
  \`git switch ${BRANCH}\` (or \`git switch -c ${BRANCH} ${BASE}\` if the branch does not exist).
${uncommitted}
- Vocabulary comes from the glossary: use its terms in code, tests and docs; its Avoid lists
  are banned identifiers.
- The project's non-negotiables are whatever AGENTS.md lists; its declared paths and their
  dependency direction are whatever AGENTS.md and docs/process/binding.md define. Each is a
  hard rule.
${ownTestsOnly ? OWN_TESTS_RULE : CHECK_RULE}
- Your final output is consumed by a script, not a human: return only the structured object.
${GUARD_RULES}`
}

function checkOutputField(ownTestsOnly) {
  return {
    checkOutput: {
      type: 'string',
      description: ownTestsOnly
        ? 'Last ~40 lines of the `tier0` entry point and your own tests, verbatim, run from the repo root before returning; not `check`, since your tree is red on purpose'
        : 'Last ~40 lines of the `check` entry point output, verbatim, run from the repo root before returning',
    },
  }
}

// ---------------------------------------------------------------------------
// Schemas
// ---------------------------------------------------------------------------
// Why: a change must leave room under the binding's change-size ceiling for what the later
// stages add. On the first tickets built this way the critic's tests were two fifths of the
// change and the spec's tests most of the rest, so a change that is already over two thirds of
// the ceiling cannot end green, and each stage it still runs is paid for nothing.
const CHANGE_SIZE_FIELD = {
  changeSize: {
    type: 'object',
    description: 'The two numbers the binding\'s change-size entry point prints for the working tree as you leave it; omit when the binding binds no such gate',
    properties: { lines: { type: 'integer' }, ceiling: { type: 'integer' } },
    required: ['lines', 'ceiling'],
  },
}
const CHANGE_SIZE_RULE = 'Run the binding\'s change-size entry point last and return the lines changed and the ceiling it prints as `changeSize`; omit it when the binding binds no such gate.'
// Why: ADR 0028. A red `triggers` gate says a second instance of a kind arrived while its
// safeguards are still owed. Quieting it in the binding, the justfile or quality/ would be the
// implementer loosening the judge; binding the rows is /ratchet:safeguard's, run by the owner. The
// drain parks the run on the gate's own line, so the line must survive in `checkOutput`.
const TRIGGERS_RULE = 'If `check` is red on the `triggers` gate (a line `triggers: \'<kind>\' gained N instance(s)`: a second instance of a kind whose safeguards are owed), do not edit the binding, the justfile or quality/ to quiet it and do not write a dispute. Finish the rest, then return with `checkPasses: false`, keep that gate line verbatim in `checkOutput`, and name the kind and `/ratchet:safeguard` in `notes`.'
// The line the `triggers` gate prints; scripts/drain.py matches the same one.
const OWED_RED = /triggers: '[^']+' gained \d+ instance/
const ROOM_NUMERATOR = 2
const ROOM_DENOMINATOR = 3

// Why: the later stages' lines are known in advance. On one ticket the spec and its tests were
// about 830 lines, the critic's tests about 350 and the dispute records about 140, before any
// implementation. The spec stage adds up the whole change and splits the ticket now, instead of
// after every later agent has run.
const ESTIMATE_FIELD = {
  changeEstimate: {
    type: 'object',
    description: 'What the whole change will weigh against the change-size ceiling, estimated before you return; omit when the binding binds no change-size gate',
    properties: {
      implementationLines: { type: 'integer', description: 'Lines the implementation will add or change, by the seams the tests exercise' },
      criticLines: { type: 'integer', description: 'Lines the critic\'s tests will add: about two fifths of the lines of your own tests, more for a user interface or an endpoint' },
      recordLines: { type: 'integer', description: 'Lines of dispute records, ADRs and glossary entries the change is likely to add; 0 when none' },
      proposedSplit: {
        type: 'array',
        description: 'When the sum of these three and the lines already measured would pass the ceiling: two or more tickets that each fit, with the acceptance criteria each carries; empty when the change fits',
        items: {
          type: 'object',
          properties: {
            title: { type: 'string' },
            criteria: { type: 'array', items: { type: 'string' } },
          },
          required: ['title', 'criteria'],
        },
      },
    },
    required: ['implementationLines', 'criticLines', 'recordLines', 'proposedSplit'],
  },
}

const SPEC_SCHEMA = {
  type: 'object',
  properties: {
    ...CHANGE_SIZE_FIELD,
    ...ESTIMATE_FIELD,
    slug: {
      type: 'string',
      pattern: '^[a-z0-9]+(-[a-z0-9]+)*$',
      description: 'the feature slug the preflight derived, returned unchanged',
    },
    specPath: { type: 'string', description: 'docs/features/<slug>/spec.md, repo-relative' },
    testFiles: {
      type: 'array',
      items: { type: 'string' },
      description: 'Repo-relative paths of the new (currently failing) test files',
    },
    layersTouched: {
      type: 'array',
      items: { type: 'string' },
      description: 'Declared path or layer names exactly as AGENTS.md / docs/process/binding.md name them; empty if the project declares none',
    },
    openQuestions: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          question: { type: 'string', description: 'The question as the owner would read it, in the words of a user of the product, with no code in it' },
          kind: {
            type: 'string',
            enum: ['direction', 'irreversible'],
            description: 'direction: the options are different products for a user. irreversible: a wrong guess cannot be taken back',
          },
          options: {
            type: 'array',
            items: { type: 'string' },
            description: 'Two to four answers the owner can choose between, the one you recommend first; each says what the user gets',
          },
          recommendation: { type: 'string', description: 'One sentence: why the first option, from the ticket, the design brief or the records' },
        },
        required: ['question', 'kind', 'options', 'recommendation'],
      },
      description: 'Only the points the owner decides: the options lead to different products for a user (direction), or a wrong guess would lose data, fix a public contract that cannot be changed back, spend money or open a security hole (irreversible). Each must also be recorded in spec.md. Any entry stops the pipeline before Implement. Everything else is an assumption.',
    },
    assumptions: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          question: { type: 'string', description: 'What the ticket and the records leave undecided' },
          assumed: { type: 'string', description: 'The conservative answer the spec and its tests are built on' },
          refuter: { type: 'string', description: 'What would show the assumption wrong, and what changing it later would cost' },
        },
        required: ['question', 'assumed', 'refuter'],
      },
      description: 'Every undecided point the spec answered with a reversible default instead of stopping; each also recorded in spec.md under Assumptions. The pull request lists them for the owner to counter at the merge. Empty if the ticket decided everything.',
    },
    dependencies: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          name: { type: 'string', description: 'The package name as the registry spells it' },
          reason: { type: 'string', description: 'Why the feature needs it, as spec.md states it' },
        },
        required: ['name', 'reason'],
      },
      description: 'Every package this feature adds, each also named with its reason in spec.md; empty if none. A guard-diff "dependencies added:" note naming a package missing here stays a guard change.',
    },
    guardEdits: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          path: { type: 'string', description: 'Repo-relative guard path, matching a pattern in guards/manifest.toml' },
          edit: { type: 'string', description: 'The change the criteria need there, concrete enough for the owner to make it' },
        },
        required: ['path', 'edit'],
      },
      description: 'Guard paths the criteria cannot be met without changing (an import contract, a test fixture, a Layers row, the definition of a term the glossary already has); this run cannot edit them. A term or an ADR you add where the manifest marks the record `edit = "append"` is not one. Any entry stops the pipeline before Implement.',
    },
    failingForRightReason: {
      type: 'boolean',
      description: 'true only if every new test fails because the behaviour is missing, not because of import/syntax errors',
    },
    failures: {
      type: 'array',
      description: 'One entry per new test file: the failure content it printed (the assertion diff or the missing-name error), quoted, never the assertion line alone',
      items: {
        type: 'object',
        properties: {
          testFile: { type: 'string', description: 'Repo-relative path, one of testFiles' },
          content: { type: 'string', description: 'The quoted failure content' },
        },
        required: ['testFile', 'content'],
      },
    },
    ...checkOutputField(true),
  },
  required: ['slug', 'specPath', 'testFiles', 'layersTouched', 'openQuestions', 'assumptions', 'dependencies', 'guardEdits', 'failingForRightReason', 'failures', 'checkOutput'],
}

const DISPUTES_FIELD = {
  disputes: {
    type: 'array',
    description: 'Gate disputes you raised instead of touching a guard path; empty if none',
    items: {
      type: 'object',
      properties: {
        path: { type: 'string', description: 'docs/disputes/NNNN-<slug>.md, repo-relative' },
        gate: { type: 'string', description: 'The gate question and its entry point, e.g. "limits (check)"' },
        guardPathsCovered: {
          type: 'array',
          items: { type: 'string' },
          description: 'Every guard path (repo-relative, as `guard-diff` prints it) that this dispute record names in its Gate section; empty if the dispute names none',
        },
        recommendation: { type: 'string', description: 'One line: what you recommend the owner decides' },
      },
      required: ['path', 'gate', 'guardPathsCovered', 'recommendation'],
    },
  },
}

const GUARD_PATHS_FIELD = {
  type: 'array',
  items: { type: 'string' },
  description: 'Every guard path `guard-diff` lists for the branch (its unindented lines, the path after the tab, repo-relative); empty if none',
}

const DEPENDENCY_NOTES_FIELD = {
  type: 'array',
  description: 'For each guard path whose guard-diff note starts "dependencies added:" or "lockfile:", the path, which of the two, and the package names the note lists (the word before each parenthesis); empty if none. A "not dependency-only:" note is not listed.',
  items: {
    type: 'object',
    properties: {
      path: { type: 'string', description: 'The guard path, exactly as guard-diff prints it' },
      note: { type: 'string', enum: ['dependencies added', 'lockfile'] },
      packages: { type: 'array', items: { type: 'string' } },
    },
    required: ['path', 'note', 'packages'],
  },
}

const RECORD_NOTES_FIELD = {
  type: 'array',
  description: 'For each guard path whose guard-diff note starts "additions:", the path and each addition the note lists (the parts between its semicolons: "term <name> (avoid: ...)", "avoid <items> (under <term>)", "ADR <number> <title>"); empty if none. A "not additive:" note is not listed.',
  items: {
    type: 'object',
    properties: {
      path: { type: 'string', description: 'The guard path, exactly as guard-diff prints it' },
      additions: { type: 'array', items: { type: 'string' } },
    },
    required: ['path', 'additions'],
  },
}

const IMPLEMENT_SCHEMA = {
  type: 'object',
  properties: {
    filesChanged: { type: 'array', items: { type: 'string' }, description: 'Repo-relative paths created or modified' },
    allNewTestsPass: { type: 'boolean' },
    checkPasses: { type: 'boolean', description: 'true only if `check` exited 0' },
    ...CHANGE_SIZE_FIELD,
    notes: { type: 'string', description: 'Design decisions, trade-offs, anything the spec left open and how you resolved it' },
    ...DISPUTES_FIELD,
    ...checkOutputField(),
  },
  required: ['filesChanged', 'allNewTestsPass', 'checkPasses', 'notes', 'disputes', 'checkOutput'],
}

const CRITIQUE_SCHEMA = {
  type: 'object',
  properties: {
    findings: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          severity: { type: 'string', enum: ['blocker', 'major', 'minor'] },
          title: { type: 'string' },
          evidence: { type: 'string', description: 'Command run and the output that demonstrates the problem' },
          suggestedTest: { type: 'string', description: 'Path of a test you added that exposes it, or a sketch of one' },
          testFails: { type: 'boolean', description: 'true when you kept a test for this finding that fails now, so `check` is red on it' },
          heldByKeptTest: { type: 'boolean', description: 'true when you kept a test for this finding that PASSES because the code already holds; false when no test was kept. Never true with testFails' },
        },
        required: ['severity', 'title', 'evidence', 'suggestedTest', 'testFails', 'heldByKeptTest'],
      },
    },
    testsAdded: { type: 'array', items: { type: 'string' }, description: 'Repo-relative test files you created or extended' },
    attempted: {
      type: 'array',
      items: { type: 'string' },
      description: 'Every command you ran trying to break the change, one per entry, with its one-line outcome. "No findings" is credible only by this list',
    },
    mutationSummary: { type: 'string', description: 'Mutation-testing result for the touched modules, or "not applicable" with the reason (e.g. binding.md names no mutation gate)' },
    fuzzSummary: { type: 'string', description: 'Fuzz harness result for touched value types, or "not applicable" with the reason' },
    fitnessSabotageConfirmed: {
      type: 'boolean',
      description: 'true only if a deliberate violation in a scratch copy made the fitness tests fail; false if the repository has no fitness tests',
    },
    fitnessSabotageSummary: {
      type: 'string',
      description: 'The violation introduced and the failing output, or "not applicable" with the reason',
    },
    guardFilesChanged: GUARD_PATHS_FIELD,
    ...checkOutputField(true),
  },
  required: [
    'findings', 'testsAdded', 'attempted', 'mutationSummary', 'fuzzSummary', 'fitnessSabotageConfirmed',
    'fitnessSabotageSummary', 'guardFilesChanged', 'checkOutput',
  ],
}

// Why: the spec is what the implementer builds and what the review judges the build against, so a
// spec that drops or bends a promise of the ticket passed every check after it. Two roles read the
// ticket against what came of it, the ticket check before Implement and the review after, and both
// return these two axes. The schema makes each answer both and gives each reading the evidence that
// shows it; it cannot make an answer complete, since a list that leaves a promise out fits it as
// well as an empty `acceptedInputs` does.
const SAID = { type: 'string', pattern: '\\S' }
function promiseReading(reading, description, evidence) {
  return {
    type: 'object',
    description,
    properties: {
      promise: { ...SAID, description: 'The promise in the ticket\'s own words' },
      reading: { type: 'string', enum: [reading] },
      ...evidence,
    },
    required: ['promise', 'reading', ...Object.keys(evidence)],
    additionalProperties: false,
  }
}
const SPEC_LINE = { specLine: { ...SAID, description: 'The spec.md line this reading rests on, quoted, or the section a dropped promise is missing from' } }
const TICKET_PROMISES_FIELD = {
  type: 'array',
  minItems: 1,
  description: 'Every promise the ticket makes about what the product does or does not do, one entry each, with its reading',
  items: {
    anyOf: [
      promiseReading('covered', 'spec.md criteria carry it as the ticket says it', {
        criteria: { type: 'array', minItems: 1, items: SAID, description: 'What in spec.md carries it: criterion numbers, or for how the change is built or tested the section entry that states it' },
      }),
      promiseReading('dropped', 'no criterion carries it', SPEC_LINE),
      promiseReading('bent', 'a criterion carries less than the ticket asks, makes it optional or contradicts it', {
        ...SPEC_LINE,
        change: { ...SAID, description: 'What the spec says instead of what the ticket asks' },
      }),
      promiseReading('assumed', 'the ticket leaves part of it open and the spec decided that part for the owner', {
        ...SPEC_LINE,
        ownerSees: { ...SAID, description: 'What the owner would see in the product because of the assumption' },
      }),
    ],
  },
}
// Each case of an input: how the owner reads it after the input's plain name, the safe answer the
// run builds when no criterion decides it, and what would show that answer wrong. The empty one is
// the narrowest: a message or a rule for an empty value is a product the ticket did not ask for,
// and another ticket of the feature may promise it.
const INPUT_CASES = {
  empty: {
    owner: 'left empty',
    safe: 'nothing is saved and nothing breaks',
    refuter: 'an empty value is something a user means to send, such as clearing a note',
  },
  oversized: {
    owner: 'far longer than anyone would type',
    safe: 'the app refuses it with a message, saves nothing and keeps what the user typed',
    refuter: 'a user needs to send a value that long',
  },
  hostile: {
    owner: 'made to cause harm, such as hidden code',
    safe: 'the app keeps it as plain text that does nothing: shown as text, never run, followed or used as a path',
    refuter: 'a user needs the value to run or be followed as sent',
  },
}
// How a settled reading marks a case the run decided because no criterion did.
const RUN_ASSUMED = 'Assumed by the run, as no criterion decides it: '
// How a settled reading marks a promise the plan still left out, which the run keeps as the ticket
// says it.
const RUN_KEPT = 'Kept from the ticket by the run, as no criterion carries it: '
const DECIDED_BY = { type: ['string', 'null'], pattern: '\\S', description: 'The spec.md criterion that says what happens, by its number and words; null when none does' }
const ACCEPTED_INPUTS_FIELD = {
  type: 'array',
  description: 'Each thing the change makes the product accept from outside, with the criterion that decides each case of it; empty only when it accepts nothing',
  items: {
    type: 'object',
    properties: {
      input: { ...SAID, description: 'The input as a user meets it: a form field, a request body, a file, an argument' },
      plainName: {
        type: 'string',
        pattern: '^[^`]*[^\\s`][^`]*$',
        description: 'What a person who cannot read code calls it, in everyday words with no code terms: "the link you paste"',
      },
      ...Object.fromEntries(Object.keys(INPUT_CASES).map(c => [c, DECIDED_BY])),
    },
    required: ['input', 'plainName', ...Object.keys(INPUT_CASES)],
    additionalProperties: false,
  },
}

const TICKET_CHECK_SCHEMA = {
  type: 'object',
  properties: { ticketPromises: TICKET_PROMISES_FIELD, acceptedInputs: ACCEPTED_INPUTS_FIELD },
  required: ['ticketPromises', 'acceptedInputs'],
}

const REVIEW_SCHEMA = {
  type: 'object',
  properties: {
    verdict: { type: 'string', enum: ['approve', 'request_changes'] },
    ticketPromises: TICKET_PROMISES_FIELD,
    acceptedInputs: ACCEPTED_INPUTS_FIELD,
    issues: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          axis: { type: 'string', enum: ['spec', 'standards'] },
          severity: { type: 'string', enum: ['blocker', 'major', 'minor'] },
          title: { type: 'string' },
          location: { type: 'string', description: 'file:line or acceptance-criterion id' },
          evidence: { type: 'string' },
        },
        required: ['axis', 'severity', 'title', 'location', 'evidence'],
      },
    },
    adrSuggested: {
      type: 'string',
      description: 'One-line title of an ADR this change needs, or empty string if none',
    },
    guardChanges: GUARD_PATHS_FIELD,
    dependencyNotes: DEPENDENCY_NOTES_FIELD,
    recordNotes: RECORD_NOTES_FIELD,
    disputeCoverage: {
      type: 'array',
      description: 'For each changed guard path, the dispute record under docs/disputes/ whose text names that path. Report only pairs you verified by reading the dispute file; a changed guard path with no such file is simply absent from this list.',
      items: {
        type: 'object',
        properties: {
          path: { type: 'string', description: 'A changed guard path, repo-relative, exactly as `guard-diff` prints it' },
          disputeFile: { type: 'string', description: 'docs/disputes/NNNN-<slug>.md that names this path, repo-relative' },
        },
        required: ['path', 'disputeFile'],
      },
    },
    checkPasses: { type: 'boolean', description: 'true only if `check` exited 0 when you ran it' },
    ...checkOutputField(),
  },
  required: ['verdict', 'ticketPromises', 'acceptedInputs', 'issues', 'adrSuggested', 'guardChanges', 'dependencyNotes', 'recordNotes', 'disputeCoverage', 'checkPasses', 'checkOutput'],
}

// ---------------------------------------------------------------------------
// Prompts. Each role receives only the context it is allowed to see.
// ---------------------------------------------------------------------------
const PREFLIGHT_SCHEMA = {
  type: 'object',
  properties: {
    ok: { type: 'boolean', description: 'the "ok" field of the script\'s JSON; false when the script could not be found or run' },
    number: { type: ['integer', 'null'], description: 'the "number" field' },
    title: { type: 'string', description: 'the "title" field' },
    slug: { type: 'string', description: 'the "slug" field' },
    branch: { type: 'string', description: 'the "branch" field' },
    base: { type: 'string', description: 'the "base" field: the default branch the feature branches from' },
    label: { type: 'string', description: 'the "label" field: the agent-ready label as this tracker spells it' },
    critic: { type: 'boolean', description: 'the "critic" field: whether the project is depended-on, so the critique and fix phases run' },
    critic_reason: { type: 'string', description: 'the "critic_reason" field, verbatim' },
    failures: {
      type: 'array',
      description: 'the "failures" field, verbatim; when the script could not run, one entry with check "script"',
      items: {
        type: 'object',
        properties: {
          check: { type: 'string' },
          detail: { type: 'string' },
          produce: { type: 'string' },
        },
        required: ['check', 'detail', 'produce'],
      },
    },
    output: { type: 'string', description: 'the script\'s stdout and stderr, verbatim' },
  },
  required: ['ok', 'number', 'title', 'slug', 'branch', 'label', 'failures', 'output'],
}

// Every role that reads the ticket reads it as the owner agreed it: a local ticket file as the base
// branch holds it, since an agent of this run could have changed the copy in the working tree.
function ticketHint() {
  if (/^#?\d+$/.test(REF)) {
    const number = REF.replace('#', '')
    return `issue ${number} on the tracker (\`gh issue view ${number} --json number,title,body,labels,comments\`)`
  }
  if (!BASE) return `the file \`${REF}\` under ${ROOT_HINT}`
  return `the file \`${REF}\` as \`${BASE}\` holds it (\`git show ${BASE}:${REF}\`, run from ${ROOT_HINT}), not the copy in the working tree, which an agent of this run may have changed`
}

// How the ticket check and the review read the ticket. One text, so the two roles mean the same by
// each reading.
const PROMISE_READINGS = `List in \`ticketPromises\` every promise the ticket makes about what the product does or
   does not do: each checklist item and acceptance criterion, and each promise in its other
   sections ("it has no client JavaScript", "the list survives a restart"), in the ticket's words,
   one entry each, with the reading that fits:
   - \`covered\`: spec.md carries it as the ticket says it; name what carries it in \`criteria\`. A
     promise about what the product does is carried by a criterion, or by criteria it follows from,
     never by the Problem section alone. A promise about how the change is built or tested is
     carried by the criterion, Placement, Seams under test or Test plan entry that states it.
   - \`dropped\`: no criterion carries it; \`specLine\` quotes the line that drops it (an Out of
     scope entry, a sentence no criterion checks) or names the section it is missing from.
   - \`bent\`: a criterion carries less than the ticket asks, makes it optional, or contradicts it;
     \`specLine\` quotes the criterion and \`change\` says what differs.
   - \`assumed\`: the ticket leaves part of it open and the spec decided that part for the owner,
     under Assumptions or without saying so; \`specLine\` quotes where, and \`ownerSees\` says what
     the owner would see in the product because of it. Read each entry under the spec's
     Assumptions this way: one that decides part of a promise makes the promise \`assumed\`, and
     one that contradicts the ticket makes it \`bent\`.
   What the ticket puts out of scope or leaves to a later ticket is no promise of this one, and an
   answer under \`## Answers\` decides what it answers. A sentence about how the method treats the
   work (what a gate charges, which kind of code arrives first, what the route names next) is no
   promise either: it describes the work, not the product.`

const INPUT_READINGS = `List in \`acceptedInputs\` each thing the change makes the running product accept from its
   users or from other systems (a form field, a request body, a query parameter, an uploaded file,
   a command-line argument). A setting the operator gives it at start is not one. A header the
   change itself adds or checks to guard the product (an \`Origin\` check that refuses cross-site
   posts) is not one either: no user sends it as content. Give each a \`plainName\`: what a person
   who cannot read code calls it, in a few everyday words and no code terms ("the link you paste",
   not "the \`link\` field of \`POST /items\`"); the owner reads it. For each, quote
   the spec.md criterion that says what happens to an \`empty\` value, an \`oversized\` one and a
   \`hostile\` one, or null where no criterion says. A hostile value is decided only when criteria
   cover each kind that applies to the input: markup where it is shown, a value that runs where it
   is followed, a path that leaves its directory. A criterion that leaves the case to a later
   ticket decides it only if it says what happens until then, and an assumption decides no case.`

function ticketCheckPrompt(spec) {
  return `You are the TICKET CHECK for feature \`${spec.slug}\` of this repository. The spec role has
just written spec.md from the ticket, and nothing has been built. The implementer builds spec.md and
the review judges the build against it, so a promise of the ticket that spec.md drops or bends now
would be built to the letter. What you find goes back to the spec role, which amends spec.md once;
a promise still left out after that is built as the ticket says it, and a case still undecided
with the safe answer.

Read these two and nothing else (no code, no tests, no other record), and edit nothing:
- The ticket: ${ticketHint()}, with its comments and its \`## Answers\` section: what the owner asked
  for and agreed.
- The spec: \`${spec.specPath}\` under ${ROOT_HINT}.

1. ${PROMISE_READINGS}
2. ${INPUT_READINGS}

Read what the ticket says, not what you would have built. Your final output is consumed by a
script, not a human: return only the structured object.`
}

function preflightPrompt() {
  const script = PREFLIGHT_SCRIPT
    ? `\`${PREFLIGHT_SCRIPT}\``
    : 'the ratchet plugin\'s `scripts/feature_preflight.py` (the newest version under `~/.claude/plugins/cache/*/ratchet/*/scripts/`, or wherever the plugin is installed; `find` it, do not write one)'
  const branchFlag = BRANCH ? ` --branch "${BRANCH}"` : ''
  const baseFlag = BASE ? ` --base "${BASE}"` : ''
  return `You run one command and relay its output. You decide nothing.

In ${ROOT_HINT}, run, in a single Bash call:

    python3 ${script} "${REF}" --root .${baseFlag}${branchFlag}

It prints a JSON object on stdout ({ ok, ref, failures: [{ check, detail, produce }], number,
title, slug, branch, base, label, critic, critic_reason }) and a verdict line on stderr. Return that JSON's fields in the
structured result, verbatim, plus the full output. Do not fix, label, edit or commit anything,
and do not re-run with different arguments. If the script cannot be found or Python refuses
it, return ok: false with one failure { check: "script", detail: <what happened>, produce:
"install the ratchet plugin; the skill passes the script path as args.preflight" }.`
}

// Why: an input case or a promise is the spec role's to decide, never the owner's to confirm, and
// the script asks him nothing; the one that is his reaches him as the role's own question. The spec
// role and its amend read the same words.
const SAFE_ANSWER = 'the safe answer, the narrowest that keeps the app safe (an empty value: nothing ' +
  'is saved and nothing breaks; an oversized one: the app refuses it with a message and keeps what ' +
  'the user typed; a hostile one: kept as plain text that does nothing)'
const INPUT_CHOICE = 'An input case where the safe answer takes something from the user (refusing an ' +
  'empty value when an empty one clears a note, or a limit that loses what they typed) is a real ' +
  'product choice: raise it as a `direction` open question under the fork test below, since the ' +
  'script asks the owner nothing about inputs.'
const PROMISE_RAISED = 'A promise of the ticket you believe cannot or should not be built as it says ' +
  'is an open question under the fork test below, never one you leave out: the run builds a ' +
  'promise the plan leaves out as the ticket says it, and asks the owner nothing.'

// The spec role writes spec.md, and amends it once when the ticket check finds `gaps`: the
// promises it read as dropped or bent and the input cases no criterion decides.
function specPrompt(gaps = []) {
  return `${PREAMBLE}
You are the SPEC agent for one feature of this repository.
${commonRules(false, true)}
Load the \`mattpocock-skills:tdd\` skill: it says what a good test is and how the seams under
test are chosen. The spec names those seams; the owner has agreed the approach through the
ticket's \`## Conjecture\` section and the agent-ready label.
${lastRunNote()}${amendNote(gaps)}
Inputs you may read:
- The ticket: ${ticketHint()}. Its sections are mattpocock-skills' (Parent, What to build,
  Acceptance criteria, Blocked by, or the spec's Problem Statement, Solution, User Stories,
  Implementation Decisions, Testing Decisions, Out of Scope) plus the method's
  \`## Conjecture\`: the approach the owner agreed, the observation that would refute it,
  the alternatives and the criticism that killed each. Build that approach, not another.
  Read \`## Answers\` and the comments too: the owner's answers to an earlier run's
  questions live there, and each one is decided.
- AGENTS.md, GLOSSARY.md, docs/process/binding.md, every ADR in docs/adr/ that the ticket
  touches, and any design document AGENTS.md tells you to read first
- The existing code and tests only as far as needed to place the feature and name test files

Your tasks, in order:
1. The slug is \`${SLUG}\`. Write \`docs/features/${SLUG}/spec.md\` containing:
   - Problem (one paragraph, from the ticket)
   - Acceptance criteria: numbered, each in Given / When / Then form, each testable. Every
     promise the ticket makes about what the product does has a criterion, a sentence in its
     Problem or What to build included. For each thing users or other systems send the running
     product (a form field, a request body, a file), criteria say what happens to an empty, an
     oversized and a hostile value. Decide each such case the ticket leaves open with
     ${SAFE_ANSWER}, and list it under Assumptions. ${INPUT_CHOICE} ${PROMISE_RAISED} A ticket
     check reads the ticket against spec.md before anything is built and sends back what it
     misses.
   - Glossary terms used (exact GLOSSARY.md names) and each term you added to the glossary
   - Placement: for each piece of the feature, which declared path or layer it lives in (using
     the names AGENTS.md and binding.md define, or the module when none are declared) and why
   - Seams under test: the public interfaces the tests observe behaviour through, one line
     each on what it catches and what it misses
   - Telemetry: for each acceptance criterion, the signal that shows it working in production
     (the span, its attributes, the metric, the log field), named as binding.md's Telemetry
     section names them; a criterion with no entry point to observe says so
   - Feature flag: the key in \`flags.toml\` that gates the new behaviour, declared there with
     \`default = false\`, and what the system does with it off (the merge base's behaviour); or
     "none" with the reason, when the change adds no behaviour a user could meet (a fix that
     restores the specified behaviour, a refactoring, a change with nothing to turn off). The
     merge judge does not read this line: a change that adds a route, a command or an exported
     symbol to a contract artefact or public-surface snapshot, with no flag that is off declared
     in \`flags.toml\` or named in the code it adds, waits for the owner (\`unflagged\`)
   - Test plan: the list of test files you will create, mapped to acceptance criteria; it
     includes the telemetry tests under the directory binding.md names and, when there is a
     flag, a test of the behaviour with the flag off
   - Assumptions: each point the ticket leaves undecided that you answered with a reversible
     default, with the answer and what would show it wrong
   - Open questions: only the points the owner decides (see below), each with its options,
     the recommended one first, and why
   - Out of scope (copied from the ticket)
2. Write the failing tests at those seams, with the test frameworks and directories binding.md
   names. Write a property test for every invariant you can state. Follow every AGENTS.md
   non-negotiable in the tests themselves. Implementation code is the implementer's: you write
   tests only.
3. Run the new tests and quote each failure's content. The right reason is a missing function,
   class, endpoint or component, or an assertion on behaviour that does not exist yet. An
   ImportError on a module that should already exist, a syntax error or a fixture error means
   the test is wrong: fix the test. Set failingForRightReason true only when every failure is
   the predicted one, and return the quoted content per test file in \`failures\`; the script
   blocks the run when a test file has no quoted failure. Put the same quotes in spec.md's
   test plan, so the reviewer sees what each test refuted.
4. Run \`tier0\` and your new tests, and capture the tail. Tier 0 is expected green and the
   new tests red. Do not run \`check\`: the implementer runs it once the code exists.
5. ${CHANGE_SIZE_RULE} The tests you wrote are most of what the change will
   weigh: when they alone pass two thirds of the ceiling the run stops here, and the owner
   splits the ticket. Write the tests the criteria need, never fewer to fit.
6. Estimate the whole change against that ceiling and return it as \`changeEstimate\`: the
   lines already measured (spec, tests, fixtures), plus the implementation, ${criticEstimate(CRITIC)}
   plus any dispute records and ADRs. Be honest and not
   hopeful: the fix agent cannot cut a change down by revising code. When the sum passes the
   ceiling, put the split in \`proposedSplit\`: two or more tickets, each of which fits, each
   with the acceptance criteria it carries. The run then stops for the owner, who files them.

An undecided point is the owner's in two cases, and yours in every other.

It is an open question of kind \`irreversible\` when a wrong guess cannot be taken back: it
would lose or corrupt stored data, fix a public contract others will depend on, spend money, or
weaken a security or privacy rule.

It is an open question of kind \`direction\` when the options are different products. The
test: describe the product to one of its users after each option, in the user's words and
with no code in the sentence. When the two descriptions differ in what the user sees, what
they can do, who may do it or what becomes of something that is theirs, the owner chooses.
When the user could not tell which option was built, you choose. Three that are direction:
- "Should an expired link show a page that says it expired, or send the visitor to the home
  page?" The visitor sees a different thing.
- "May anyone with the link close a poll, or only the person who made it?" Who may do it.
- "When a voter answers twice, does the second answer replace the first or is it refused?"
  What becomes of their answer.
Three that are not, and are yours to assume:
- Which HTTP status answers a reserved code. No user's product changes with it.
- Whether the expiry is checked in the store or in the route. The user cannot tell.
- A limit the ticket leaves unstated where one value is plainly the safe one: take it, and
  the owner relaxes it in a later change.

Everything else is an assumption. This run has no owner at the keyboard and a stopped run
costs them a round trip, so decide what is yours to decide: take the conservative answer (the
one that refuses, reserves or keeps more, and that a later change can relax), build the spec
and the tests on it, and return it in \`assumptions\` with what would refute it. The pull
request lists every assumption, and the owner counters one that is wrong.

An open question stops the pipeline: the orchestrator parks the ticket and puts the question
to the owner, who answers on the ticket, and the ticket is built again. So ask it well. Each
one carries two to four options the owner can choose between, the one you recommend first,
and a sentence on why. Never ask the owner to confirm a choice: an option you would take
anyway with nothing against it is an assumption. A point the ticket, its \`## Answers\`
section, its comments, the design brief, PRODUCT.md, GLOSSARY.md or an ADR already decides is
neither a question nor an assumption: build what it says.

Guard edits stop it too. This run has no owner at the keyboard, so no agent in it can change a
guard path. Before writing tests, check each criterion against \`guards/manifest.toml\`: when
one cannot be met without changing a guard path (a new import contract, a shared test fixture,
a Layers row, the definition of a term the glossary already has), list it in \`guardEdits\`
with the concrete edit. A word the glossary lacks is not one where the manifest marks the
glossary \`edit = "append"\`: add the entry to GLOSSARY.md yourself before a test uses the
word, in the format of the entries there (the bold term, one to three sentences, its
\`_Avoid_\` list), and name it in spec.md. The hook lets the addition through; if it asks,
the edit changed something that was there, and that is a guard edit. A missing
DESIGN.md is not one: the surface is built against PRODUCT.md and impeccable's \`shape\`, and an
agent runs \`impeccable document\` to write DESIGN.md after the merge. The owner lands the
guard edits first and re-runs; it costs one agent instead of a whole run that ends in
disputes. A dependency the feature needs is not a guard edit: name it, with its reason, in the
spec's placement section and in \`dependencies\`, and add it with the stack's package manager
(\`uv add\`, \`cargo add\`, \`pnpm add\`) followed by \`snapshots\`. \`guard-diff\` still lists
the manifest, the lockfile and their snapshots, with a note of what changed; the script accepts
them when every added package is one \`dependencies\` names. Changing or removing an existing
dependency, or fetching one from git, a path or a URL, is a guard edit.

Return the structured result only; the implementer will see spec.md and the test files, and
nothing of your reasoning about the ticket.`
}

function lastRunNote() {
  if (!TICKET_FINDINGS.length) return ''
  return `
An earlier run built this ticket, and its review refused the change because the build lost these
promises of the ticket or left these cases of an input undecided:
${JSON.stringify(TICKET_FINDINGS, null, 2)}
Give each a criterion, and a failing test the build cannot pass without keeping it.
`
}

function amendNote(gaps) {
  if (!gaps.length) return ''
  return `
This is an AMEND. You wrote spec.md and its tests a moment ago; they are on the working tree, and
nothing has been built. A ticket check that read only the ticket and spec.md found these gaps: a
promise it read as \`dropped\` or \`bent\` is one spec.md does not carry as the ticket says it, and
an \`{ input, plainName, case }\` entry is a case of an input no criterion decides.
${JSON.stringify(gaps, null, 2)}
Close each gap with the smallest change that makes the plan do what this ticket says, or that
makes an accepted input safe. Change nothing the gaps do not touch: leave every other criterion's
wording exactly as it is, not split, joined or reworded.
- A promise: a Given / When / Then criterion that carries it as the ticket says it. ${PROMISE_RAISED}
- An input case: decide it yourself with ${SAFE_ANSWER}: the criterion that says so, and an
  assumption with what would refute it. Never ask the owner to confirm it. For an empty value
  the safe answer is nothing saved and nothing broken, with no message or rule of its own: a
  message or a rule is a product another ticket may promise. ${INPUT_CHOICE}
Each change comes with a failing test whose failure you quote as in task 3. Never bring forward
what another ticket of this feature promises (its rule, its check, its message): when the smallest
safe answer overlaps another ticket, take the narrower answer this ticket needs and name that
ticket in the assumption, even if the check then reads a promise as bent again. Where you read the
ticket otherwise (the sentence is no promise of the product, or a criterion already carries it),
leave spec.md as it is. The check then reads spec.md once more. A promise it still finds dropped
is built as the ticket says it, a case it still finds undecided with the safe answer, and the
owner sees both among what the run decided; a promise it still reads as bent is shown to him as
an assumption. Return the whole result for the spec as it now stands.
`
}

// What the run decided after the amend: `kept` are the promises spec.md still leaves out, in the
// ticket's words, built as the ticket says them; `assumed` are the cases of an input it still
// leaves undecided, built with the safe answer. The owner may overrule either.
function implementPrompt(spec, kept, assumed) {
  const promises = kept.length
    ? `
spec.md leaves out these promises of the ticket, and the run keeps them as the ticket says them.
Build each, with a test that fails without it; a promise about how the change is built (a step or
a tool to run) is done as it says, and \`notes\` says how. The review judges the build against these
words, and the pull request lists them for the owner to overrule.
${JSON.stringify(kept, null, 2)}
`
    : ''
  const cases = assumed.length
    ? `
spec.md leaves these cases of an input undecided, and the run took the safe answer for each.
Build each as its \`assumed\` says, with a test that fails without it: the review judges the build
against these words, and the pull request lists them for the owner to overrule.
${JSON.stringify(assumed, null, 2)}
`
    : ''
  return `${PREAMBLE}
You are the IMPLEMENT agent for feature \`${spec.slug}\` of this repository.
${commonRules()}
Inputs you may read:
- The spec: \`${spec.specPath}\`
- The failing tests: ${JSON.stringify(spec.testFiles)}
- Layers the spec expects you to touch: ${JSON.stringify(spec.layersTouched)}
- AGENTS.md, GLOSSARY.md, docs/process/binding.md, docs/adr/, and the existing code those
  tests exercise
${promises}${cases}You are deliberately given neither the ticket nor the spec author's reasoning. If
spec.md is ambiguous, pick the reading that is most consistent with the glossary and the ADRs
and record the choice in \`notes\`.

Load the \`mattpocock-skills:tdd\` skill and work its loop one slice at a time: run the listed
tests, implement the smallest change that makes one pass, re-run, repeat. If the change touches
a user interface, load \`impeccable:impeccable\` as well: \`shape\` before you build the surface,
\`polish\` before you return, and DESIGN.md is the standard you build to. The spec's tests
are fixed inputs: a test you believe wrong stays red and is named in \`notes\`. Keep every
declared-path rule and every AGENTS.md non-negotiable, add the contracts binding.md requires
where the spec states pre/postconditions, and regenerate any contract artefacts binding.md
names when an interface changes, with the entry point it names.

${CHANGE_SIZE_RULE}

${TRIGGERS_RULE}

Finish when every listed test passes AND \`check\` exits 0. If a gate blocks you and you
believe the gate, not your code, is wrong, write a gate dispute and list it in \`disputes\`.
If you cannot get there, return anyway with \`checkPasses: false\` and explain in \`notes\`.`
}

function fixPrompt(spec, findings, testsAdded, iteration) {
  const blockingFindings = owed(findings)
  const minors = findings.filter(f => f.severity === 'minor' && !blockingFindings.includes(f))
  return `${PREAMBLE}
You are the FIX agent (iteration ${iteration} of ${MAX_FIX_ITERATIONS}) for feature \`${spec.slug}\` of this repository.
${commonRules()}
A previous implementer built this feature and an adversarial critic found problems. You start
fresh, from the spec and the code as they are; the previous implementer's reasoning is not
among your inputs.

Inputs you may read:
- The spec: \`${spec.specPath}\`
- The spec's tests: ${JSON.stringify(spec.testFiles)}
- Tests the critic added (some currently fail on purpose): ${JSON.stringify(testsAdded)}
- AGENTS.md, GLOSSARY.md, docs/process/binding.md, docs/adr/, and the code under change

Load the \`mattpocock-skills:tdd\` skill and work one slice at a time.

Findings you MUST resolve (blocker, major, or a test the critic kept failing):
${JSON.stringify(blockingFindings, null, 2)}

Findings you MAY resolve if cheap (minor):
${JSON.stringify(minors, null, 2)}

Resolve each blocking finding by changing the implementation; the tests are fixed inputs. A
critic test that asserts something the spec contradicts stays red and is explained in
\`notes\`. A finding that can only be resolved by changing a guard path, or a gate you believe
is wrong, becomes a gate dispute listed in \`disputes\`.

${TRIGGERS_RULE}

Finish when the spec tests and the
critic's tests pass and \`check\` exits 0; otherwise return with \`checkPasses: false\` and
explain.`
}

function reconcilePrompt(spec, conflicts) {
  return `${PREAMBLE}
You are the RECONCILE agent for feature \`${spec.slug}\` of this repository. The feature was
built and approved. Then another change merged into \`${BASE}\`, and this branch must hold
that change before it can merge. You bring the two together and change nothing else.
${commonRules(true)}
Inputs you may read:
- The spec: \`${spec.specPath}\`
- The branch's own change: \`git diff ${BASE}...${BRANCH}\`
- What merged meanwhile: \`git log --oneline ${BRANCH}..${BASE}\` and its diff
- AGENTS.md, GLOSSARY.md, docs/process/binding.md, docs/adr/
Paths the rebase stopped on when it was tried: ${JSON.stringify(conflicts)}

Do this, in order:
1. If \`git merge-base --is-ancestor ${BASE} HEAD\` fails, run \`git rebase ${BASE}\`. At each
   stop, resolve every conflicted file so that both changes hold: both flags stay in the
   registry, both terms in the glossary, both routes in the application. Where the two cannot
   both hold, \`${BASE}\` stands as it is and this feature's code adapts to it. Then
   \`git add\` each file you resolved and \`git rebase --continue\`. Never \`--skip\` a commit
   and never take one side of a whole file.
2. Run \`check\`. A red gate is answered by changing this feature's code, never the other
   change's and never a gate. A number the other change took first (a decision record, a
   migration) is the other change's: give this feature's file the next free one. A test of
   this feature that the other change made wrong is fixed to assert what spec.md says.
3. Stop when the rebase is finished and \`check\` exits 0. If you cannot get there, run
   \`git rebase --abort\` if a rebase is still in progress, and return with
   \`checkPasses: false\`.

\`filesChanged\` lists the files you edited after the rebase finished; \`allNewTestsPass\` is
whether the spec's tests pass; \`notes\` names each conflict and how you resolved it, since
the reviewer reads the result and not your reasoning.`
}

function critiquePrompt(spec, filesChanged, previousRound) {
  const previous = previousRound
    ? `
This is a RE-CRITIQUE after a fix round. Previous findings:
${JSON.stringify(previousRound.findings, null, 2)}
Tests you added last round: ${JSON.stringify(previousRound.testsAdded)}
Re-check every previous finding first; any still open goes back into \`findings\` with the same
title. Then look for NEW ways the fix may have broken things.`
    : ''
  return `${PREAMBLE}
You are the CRITIC for feature \`${spec.slug}\` of this repository. Your job is to BREAK it.
You are the one adversary of this pipeline (its agent count is fixed, so you do the work
in-process; the owner may run \`/pstack:interrogate\` on the branch afterwards, which is theirs
to type, not yours to imitate). The rule of evidence: a finding survives only when a run proves
it, so each carries the exact command, the output it printed and the output the spec requires;
leave out anything you did not run; "no findings" is a valid result when you name what you ran.
${commonRules(false, true)}
Inputs you may read:
- The spec: \`${spec.specPath}\`
- The spec's tests: ${JSON.stringify(spec.testFiles)}
- Files the implementer changed: ${JSON.stringify(filesChanged)}
- AGENTS.md, GLOSSARY.md, docs/process/binding.md, docs/adr/, and the fitness tests, mutation
  config and fuzz harness binding.md names
The implementer's notes and the ticket are outside your inputs.
${previous}
Do all of the following and report evidence for each:
1. Property tests: with the property-testing library binding.md names, add a property for
   every invariant the spec states or implies (round-trips, monotonicity, conservation,
   idempotence, ordering, anything an AGENTS.md non-negotiable implies for the values touched).
   Add edge-case tests you EXPECT to fail: zero, negative and boundary values, empty
   collections, duplicates, unknown identifiers, unit, period or time-zone boundaries where
   the domain has them, very large values, unicode. Put them in the critique directory
   binding.md's Critique tests section names, flat, one file per layer touched, named
   \`<slug>.<layer>.critique.test.<ext>\` (\`tests/critique/\` when the section names none).
   Never put them beside the code: a critique file is organised by feature, not by module.
2. Mutation testing: if binding.md names a mutation gate, run it targeted at the modules in
   filesChanged with the configured invocation. Report survived mutants as findings with the
   mutant diff as evidence, and add a test that kills each if you can. Otherwise write
   "not applicable" and why in \`mutationSummary\`.
3. Fuzzing: if binding.md names a fuzz harness and filesChanged includes a value type (an
   amount, a date, an identifier, a parser), run the harness against it, time-boxed to a few
   minutes. Report crashes as blockers. Otherwise write "not applicable" and why in
   \`fuzzSummary\`.
4. Fitness-test sabotage: if the repository has fitness tests (tests that enforce the layer
   rules and non-negotiables), copy the repository to a scratch directory (\`mktemp -d\`,
   excluding virtual environments, package caches and .git), introduce a deliberate violation
   there of a declared-path rule or non-negotiable from AGENTS.md, run the fitness tests in
   that copy and confirm they FAIL. Then delete the copy. Set fitnessSabotageConfirmed only
   if they failed, and describe it in \`fitnessSabotageSummary\`. The sabotage lives in the copy
   and nowhere else.
5. Attack and overload: for every entry point the change adds or alters, try to get in without
   the credential or as the wrong caller, send hostile input (injection strings, oversized and
   malformed bodies, path traversal, a URL that points inward), and call it concurrently
   (twenty callers at once, the same write twice) looking for a lost update, a duplicate or an
   error. Run the \`sast\` and \`load-smoke\` entry points when binding.md binds them. Read
   what the in-memory exporter recorded and report a secret or a personal identifier in any
   span, metric or log. Flip the feature flag spec.md names and show the flag-off behaviour is
   the merge base's. Each attempt goes in \`attempted\`, each success is a finding.
5a. Non-negotiables and vocabulary: check every AGENTS.md non-negotiable holds in the change,
   the glossary's banned words are absent from new identifiers, and any generated contract
   artefact binding.md names matches the code that it describes.
6. User interface: if the change touches one, load \`impeccable:impeccable\` and run its \`audit\`
   and \`critique\` on the surface; each finding they prove is a finding here, judged against
   DESIGN.md.
7. Guard paths: run \`guard-diff\` and put every guard path it lists in \`guardFilesChanged\`.
   Each one is a blocker finding (title "guard path changed: <path>") unless a gate dispute
   under docs/disputes/ in the change names that path, and even then a major one: the
   pipeline never changes a gate, only the owner does. The exception is a dependency the spec
   decided: a path whose note is \`dependencies added:\` naming only packages spec.md's
   dependencies name, and a \`lockfile:\` path beside one, is no finding. An added package
   spec.md does not name is a major finding (a decision the spec did not make). The other
   exception is a record the change only adds to: a path whose note is \`additions:\` is no
   finding by itself. Read what it added: a new term that names a concept the glossary already
   names, or a new ADR that contradicts one in docs/adr/ without the owner's superseding
   record, is a major finding with the two passages quoted. Your scratch copy for step 4 is
   outside the repository, so sabotage there is not a guard change.

Keep every test you added that PASSES (regression value) and every test that FAILS because of
a genuine defect (list it as \`suggestedTest\` on the finding). Delete tests that fail because
the test itself was wrong. You edit test files only.

List every command you ran trying to break the change in \`attempted\`, with its one-line
outcome. Nothing found refutes only the attempts that would have hit; the script blocks a
run whose critic found nothing and attempted nothing.

${SEVERITY} The suite is expected to be red only on the failing tests you kept; say so in the
finding evidence, and set \`testFails: true\` on each such finding. A finding whose test you
kept failing is fixed before the review whatever its severity, since a red \`check\` cannot
be approved: keep a failing test only for a defect the code should lose, and for anything
else say it in the evidence and keep no failing test.

When the test you kept for a finding PASSES because the code already holds (the attack failed,
or showed the code correct), set \`testFails: false\` and \`heldByKeptTest: true\`. The finding is
then resolved by that test: no fix round runs for it, and the reviewer still reads it. A
finding with no test kept, or with a test that proves nothing yet, leaves both false and stays
owed a fix if it is a blocker or major.`
}

// `settled` is what the ticket check left spec.md on before the build: the run's own for the first
// review, and the first run's, passed back by the skill, for a second review; null when none came.
function reviewPrompt(spec, again, settled) {
  const readings = settled
    ? `Before the build a ticket check read the ticket against spec.md, and spec.md was
   settled on these readings:
${JSON.stringify(settled, null, 2)}
   They are decided: do not argue them again. Your part is the change: whether, as built, it still
   keeps each promise and decides each input case as spec.md now states, or, for a case whose
   reading starts "${RUN_ASSUMED.trim()}", as that reading states. A promise whose reading starts
   "${RUN_KEPT.trim()}" is one the run kept from the ticket: the reading carries it, not spec.md,
   and it is dropped only when the tests and the code do not do what the ticket says. Start from
   that list and add any promise it missed. `
    : ''
  const second = again
    ? `
This is a second review. The feature was built and approved; then another change merged into
\`${BASE}\` and this branch was rebased onto it. Judge the change as it stands now, against the
records as they are now: a term, a decision record, a flag or a route the other change added is
part of \`${BASE}\` and a standard here, which this change must neither contradict nor repeat.
The change is committed on the branch, with whatever the reconcile step edited uncommitted on
top; \`git diff ${BASE}\` shows both, and nothing of the other change.
`
    : ''
  return `${PREAMBLE}
You are the independent REVIEWER for feature \`${spec.slug}\` of this repository.
${commonRules()}${second}
Inputs you may read, and nothing else:
- The ticket: ${ticketHint()}, with its comments and its \`## Answers\` section: what the owner
  asked for and agreed. Read it; never edit it or comment on it.
- The spec: \`${spec.specPath}\`
- The diff: the change is on branch \`${BRANCH}\`, uncommitted unless this is a second review.
  Enumerate it with
  \`git diff ${BASE} --stat\`, \`git diff ${BASE}\` and \`git status --porcelain\` (untracked files are
  new files: read them). If the branch has commits, \`git diff ${BASE}...${BRANCH}\` is equivalent.
- AGENTS.md, GLOSSARY.md, docs/adr/, docs/process/binding.md, docs/disputes/, DESIGN.md
- Any generated contract artefacts that binding.md names
${outsideInputs(CRITIC)} are outside your inputs:
judge the spec against the ticket, and the code against the spec and the standards.

Load the \`mattpocock-skills:code-review\` skill and apply its two axes in-process, as both of
its sub-agents (the pipeline fixes the agent count): the spec source is spec.md; the standards
sources are AGENTS.md, the glossary, docs/adr/ and the binding, plus the skill's smell
baseline as judgement calls. Record every issue with axis, severity, location and evidence:
A. Spec conformance: does each Given/When/Then acceptance criterion have a test and an
   implementation that satisfies it? Any criterion silently narrowed? Any test weakened,
   skipped or deleted? Any behaviour added that the spec does not ask for?
B. Standards conformance:
   - Glossary vocabulary in identifiers, docs and API names; banned words absent
   - Declared-path rules as AGENTS.md and binding.md define them: dependency direction, purity
     of the paths they call pure, no forbidden imports
   - Every AGENTS.md non-negotiable, checked one by one against the diff
   - For a user interface, DESIGN.md: tokens, components and patterns used as it records them
   - Contracts: the pre/postcondition mechanism binding.md names, where the spec states them;
     generated contract artefacts match the code they describe
   - Type strictness, lint, test placement and coverage as the gates in binding.md require
   - Telemetry: every entry point the change adds or alters is registered through the seam
     binding.md's Telemetry section names and emits the signal spec.md names, with a test that
     reads it from the in-memory exporter; a span attribute or log field carrying a secret or
     a personal identifier is a blocker
   - Feature flag: new behaviour is reachable only through the flag spec.md names, read through
     the seam binding.md's Feature flags section names, declared in \`flags.toml\` with
     \`default = false\`, and tested with the flag off; behaviour shipped on with no flag, where
     spec.md does not say "none" with a reason, is a major issue. Surface added to a contract
     artefact or public-surface snapshot with no flag reaches the owner whatever spec.md says:
     the merge judge reports it as \`unflagged\`, so say in the review which of the two it is,
     behaviour that should be dark or surface with nothing to turn off
   - Assumptions: each one spec.md lists is what the tests and the code actually do
   - Does this change make a decision with lasting consequences (new storage, new external
     dependency, new layer, a changed rule that versions over time)? If so set adrSuggested.
C. Guard paths: run \`guard-diff\` and list every guard path it prints in \`guardChanges\`,
   each \`dependencies added:\` or \`lockfile:\` note in \`dependencyNotes\`, and each
   \`additions:\` note in \`recordNotes\`. Then
   read every file under docs/disputes/ that the diff adds or changes. For each changed guard
   path, if some dispute record's text names that exact path, add \`{ path, disputeFile }\` to
   \`disputeCoverage\`; a dispute that names only the gate covers no path. Every
   changed guard path is a blocker issue on the standards axis when uncovered and a major one
   when covered: the owner, not the pipeline, changes a gate. The exception is a dependency
   the spec decided, as the guard rules above define it: no issue. An added package spec.md
   does not name is a major issue on the spec axis. The other exception is a record the change
   only adds to (an \`additions:\` note): no issue for the path. The check behind the note
   reads shape, not meaning, so read each addition against the records: a new term that
   restates a concept the glossary already names, or a new ADR that contradicts an existing one,
   is a major issue on the standards axis.
D. The ticket. Only the spec role and the ticket check read it before you, so a change that loses
   what the owner asked for can pass every other axis. ${readings}${PROMISE_READINGS}
   Read them against spec.md as it now stands and the change: a promise is \`dropped\` when what
   carried it is gone from spec.md, or the tests and the code do not do it.
E. Inputs. ${INPUT_READINGS} A case is null when the change does not decide it as spec.md states,
   or as the settled reading states where the run assumed it.
The script files every \`dropped\` promise and every null case as a major issue: the change lost
something the ticket promised. It lists every \`bent\` and \`assumed\` promise with the spec's
assumptions for the owner to overrule, and neither holds the merge. Do not repeat them in
\`issues\`.

${SEVERITY}

Verdict: \`approve\` only if there are no blocker or major issues on any axis, no promise is
dropped, no input case is null, every changed guard path is covered by a dispute
record, is a dependency the spec decided or is a record the change only adds to, and \`check\`
exits 0; otherwise \`request_changes\`. The script re-derives the verdict from your \`issues\`,
\`ticketPromises\`, \`acceptedInputs\`, \`guardChanges\`, \`dependencyNotes\`, \`recordNotes\` and
\`disputeCoverage\`, so make them complete.`
}

// ---------------------------------------------------------------------------
// Helpers (pure)
// ---------------------------------------------------------------------------
// The stop for a change with no room left under the ceiling, or null. \`stage\` is the role
// that just returned.
function noRoom(stage, size, critic = true) {
  const lines = size && size.lines
  const ceiling = size && size.ceiling
  if (!Number.isInteger(lines) || !Number.isInteger(ceiling) || ceiling <= 0) return null
  if (lines * ROOM_DENOMINATOR <= ceiling * ROOM_NUMERATOR) return null
  return {
    stage,
    lines,
    ceiling,
    reason: `too large: ${lines} lines after ${stage}, over two thirds of the change-size ceiling of ${ceiling}, ${critic ? "with the critic's tests and the fixes still to come" : 'with the review still to come'}`,
  }
}

// The stop for a ticket whose whole change cannot fit under the ceiling, or null. The lines
// already measured are the spec's own; the estimate adds what the later stages will.
function cannotFit(size, estimate, critic = true) {
  const lines = size && size.lines
  const ceiling = size && size.ceiling
  if (!estimate || !Number.isInteger(lines) || !Number.isInteger(ceiling) || ceiling <= 0) return null
  const criticLines = critic ? estimate.criticLines || 0 : 0
  const total = lines + (estimate.implementationLines || 0) + criticLines + (estimate.recordLines || 0)
  if (total <= ceiling) return null
  return {
    stage: 'the spec',
    lines: total,
    ceiling,
    proposedSplit: estimate.proposedSplit || [],
    reason: `too large: the spec estimates ${total} lines for the whole change (spec and tests ${lines}, implementation ${estimate.implementationLines || 0}, ${critic ? `the critic's tests ${criticLines}` : 'no critic'}, records ${estimate.recordLines || 0}), over the change-size ceiling of ${ceiling}`,
  }
}

function tooLarge(stop, spec, extra) {
  log(`Blocked after ${stop.stage}: ${stop.reason}`)
  return {
    slug: SLUG,
    ref: REF,
    verdict: 'blocked',
    verdictForcedBy: [stop.reason],
    tooLarge: stop,
    openQuestions: [],
    guardEdits: [],
    specPath: spec.specPath,
    testFiles: spec.testFiles,
    branch: BRANCH,
    ...extra,
    requiredByOrchestrator: [
      'Commit nothing and open no PR. Tell the owner the ticket is too large to build as one change, with the two numbers',
      'The owner splits the ticket into smaller ones (`/mattpocock-skills:to-tickets` on the same spec); the spec this run wrote, at `specPath`, says which criteria and tests there are to divide, and `tooLarge.proposedSplit`, when the spec stage gave one, is where to start',
    ],
  }
}

// What stops the run on what the spec role returned, or null: a change too large to build, an open
// question, which is the owner's to answer and not the implementer's to guess, or a guard path the
// criteria need changed, which is the owner's to edit. The owner answers or lands the edits, and
// the run repeats.
function specStop(spec) {
  const size = noRoom('the spec', spec.changeSize, CRITIC) || cannotFit(spec.changeSize, spec.changeEstimate, CRITIC)
  if (size) return tooLarge(size, spec, { checkOutput: spec.checkOutput })
  const guardEdits = spec.guardEdits ?? []
  if (spec.openQuestions.length === 0 && guardEdits.length === 0) return null
  log(`Blocked before Implement: ${spec.openQuestions.length} open question(s), ${guardEdits.length} guard edit(s)`)
  return {
    slug: SLUG,
    ref: REF,
    verdict: 'blocked',
    openQuestions: spec.openQuestions,
    guardEdits,
    specPath: spec.specPath,
    testFiles: spec.testFiles,
    branch: BRANCH,
    checkOutput: spec.checkOutput,
    requiredByOrchestrator: [
      'Put every open question to the owner in one message, each with your recommended answer first and why, so the owner can accept them all at once: through the question tool, all in one call where it takes them; each answer goes on the ticket under `## Answers`, where the next spec agent reads it',
      'Give the owner each guard edit to land on the default branch (by hand, or with an agent at the keyboard where the guard can ask); an owner-away run cannot',
      'When the owner has answered every open question and there is no guard edit, build the ticket again from the preflight in the same turn; with guard edits, the route names the rerun once they land. Do not open a PR from a blocked run',
    ],
  }
}

// The critic's share of the spec's size estimate, and of the reviewer's inputs it is not given
// (ADR 0028): when the critic does not run, neither refers to findings that do not exist.
function criticEstimate(critic) {
  return critic ? "plus the critic's tests (about two fifths of yours)," : 'and no critic tests (the critic does not run, so return criticLines: 0),'
}

function outsideInputs(critic) {
  return critic ? "The implementer's notes and the critic's findings" : "The implementer's notes"
}

// Whether the critic ran and came back with nothing and no attempt: silence is not a refutation.
// A critic that did not run is not silent; the result says it was not run, and why.
function criticSilent(critic, critique) {
  if (!critic) return false
  const attempted = (critique.attempted || []).filter(a => a && String(a).trim())
  return critique.findings.length === 0 && attempted.length === 0
}

function blocking(findings) {
  return (findings || []).filter(f => f && (f.severity === 'blocker' || f.severity === 'major'))
}

// What the fix loop owes the review: every blocker and major that no kept passing test resolves,
// and every finding whose test the critic kept failing. Such a test leaves \`check\` red, and a red
// \`check\` is never approved, so a minor finding with a failing test would otherwise skip the loop
// and fail the review. A finding whose kept test already passes shows the code holds: a fix round
// would have nothing to change, and the review still reads the finding.
function owed(findings) {
  return (findings || []).filter(f => f && (f.testFails === true || (f.severity !== 'minor' && f.heldByKeptTest !== true)))
}

function union(...lists) {
  const seen = new Set()
  for (const list of lists) for (const item of list || []) if (item) seen.add(item)
  return Array.from(seen).sort()
}

function isDisputeRecord(path) {
  return /^docs\/disputes\/\d{4}-[a-z0-9-]+\.md$/.test(path)
}

// A guard path is disputed only when a dispute record names that path: either an agent that
// wrote the record reported it in guardPathsCovered, or the reviewer read the record and
// reported the pair in disputeCoverage. Nothing is inferred from the mere existence of a record.
function coveredGuardPaths(disputes, disputeCoverage) {
  const covered = new Set()
  for (const d of disputes || []) {
    if (!d || !isDisputeRecord(d.path)) continue
    for (const p of d.guardPathsCovered || []) if (p) covered.add(p)
  }
  for (const c of disputeCoverage || []) {
    if (c && c.path && isDisputeRecord(c.disputeFile)) covered.add(c.path)
  }
  return covered
}

// Why: a dependency the spec named is the spec's decision, not a gate change, but only when
// guard-diff found nothing else in the file (ADR 0011). A lockfile rides on a covered manifest,
// so upgrading a gate tool through the lockfile alone stays the owner's.
function packageName(name) {
  return String(name || '').toLowerCase().replace(/[-_.]+/g, '-')
}

function specDecidedDependencies(specDependencies, dependencyNotes) {
  const named = new Set((specDependencies || []).map(d => d && packageName(d.name)).filter(Boolean))
  const notes = (dependencyNotes || []).filter(n => n && n.path)
  const manifests = notes.filter(n => n.note === 'dependencies added' && (n.packages || []).every(p => named.has(packageName(p))))
  const covered = new Set(manifests.map(n => n.path))
  if (manifests.some(n => (n.packages || []).length)) {
    for (const n of notes) if (n.note === 'lockfile') covered.add(n.path)
  }
  return covered
}

// Why: a record the change only adds to is no gate change (ADR 0014). guard-diff decides that
// from the merge base and the work tree; the reviewer relays its note and the script reads it.
function additiveRecords(recordNotes) {
  return new Set((recordNotes || []).filter(n => n && n.path && Array.isArray(n.additions)).map(n => n.path))
}

// The verdict as the merge judge reads it (scripts/merge_ready.py, ADR 0021): what the script
// decided and what is still open, as texts. The orchestrator pipes this to the `merge-ready`
// entry point's `record` form, which stamps the tree that was reviewed.
function verdictRecord({ slug, ticket, verdict, forcedBy, findings, openQuestions, assumptions }) {
  const titles = (findings || [])
    .filter(f => f && (f.severity === 'blocker' || f.severity === 'major'))
    .map(f => String(f.title || '').trim() || 'a finding with no title')
  return {
    slug,
    ticket: String(ticket),
    verdict,
    forcedBy: forcedBy || [],
    unresolved: titles,
    openQuestions: (openQuestions || []).map(questionText),
    assumptions: assumptions || [],
  }
}

// What a reading of the ticket leaves open, by the role that read it. Before the build a dropped or
// bent promise and an undecided input case go back to the spec role once (gaps). After that amend
// the run decides, and asks the owner nothing: a promise still dropped is kept as the ticket says
// it, and a case still undecided takes the safe answer (settle, keptFromTicket, assumedCases). A
// promise still read as bent is a reading the check and the spec role do not share (withSurfaced).
// The owner sees all three as assumptions. After the build a dropped promise and an undecided case
// are the change losing what the spec had, and the review refuses it (ticketIssues); a bent one is
// an assumption.
function dropped(readings) {
  return (readings.ticketPromises || []).filter(p => p.reading === 'dropped')
}

function undecided(readings) {
  return (readings.acceptedInputs || []).flatMap(input =>
    Object.keys(INPUT_CASES).filter(c => input[c] === null)
      .map(c => ({ input: input.input, plainName: input.plainName, case: c })))
}

// An input as the owner reads it: its plain name, lower case where it starts a clause mid-sentence.
function ownerName({ plainName }) {
  const name = String(plainName).trim().replace(/[.;:,]+$/, '')
  return /^[A-Z][a-z ]/.test(name) ? name[0].toLowerCase() + name.slice(1) : name
}

function gaps(readings) {
  const bent = (readings.ticketPromises || []).filter(p => p.reading === 'bent')
  return [...dropped(readings), ...bent, ...undecided(readings)]
}

// The readings the build is held to: a promise the amend left out reads as kept from the ticket, in
// its words, and a case it left undecided as the safe answer the run built, so the review judges
// the build against that text instead of finding the promise dropped or the case open.
function settle(readings) {
  const ticketPromises = (readings.ticketPromises || []).map(p => p.reading === 'dropped'
    ? { promise: p.promise, reading: 'covered', criteria: [`${RUN_KEPT}${p.promise}`] }
    : p)
  const acceptedInputs = (readings.acceptedInputs || []).map(input => ({
    ...input,
    ...Object.fromEntries(Object.keys(INPUT_CASES).filter(c => input[c] === null)
      .map(c => [c, `${RUN_ASSUMED}${INPUT_CASES[c].safe}`])),
  }))
  return { ticketPromises, acceptedInputs }
}

// What the run decided after the amend, as the pull request lists it for the owner to overrule: the
// promises it kept from the ticket, then the input cases it assumed.
function keptFromTicket(readings) {
  return dropped(readings).map(p => ({
    question: p.promise,
    assumed: 'the plan left this out, so it was built as your ticket says',
    refuter: 'Wrong if you no longer want what your ticket says here',
  }))
}

function assumedCases(readings) {
  return undecided(readings).map(entry => ({
    question: `What the app does when ${ownerName(entry)} is ${INPUT_CASES[entry.case].owner}`,
    assumed: INPUT_CASES[entry.case].safe,
    refuter: `Wrong if ${INPUT_CASES[entry.case].refuter}`,
  }))
}

// A major issue for each promise of the ticket the change drops and each input case it leaves
// undecided. They take the path of any other issue, so the verdict record lists them as unresolved
// and the merge judge refuses; the result returns them as ticketFindings for the next run.
function ticketIssues(review) {
  const promises = dropped(review).map(p => ({
    axis: 'ticket',
    severity: 'major',
    title: `the change drops the ticket's promise "${p.promise}"`,
    location: p.specLine,
    evidence: 'nothing in spec.md, the tests or the code carries it',
  }))
  const cases = undecided(review).map(entry => ({
    axis: 'input',
    severity: 'major',
    title: `the change does not say what the app does when ${ownerName(entry)} is ${INPUT_CASES[entry.case].owner}`,
    location: entry.input,
    evidence: `the change decides no ${entry.case} value`,
  }))
  return [...promises, ...cases]
}

function reviewIssues(review) {
  return [...(review.issues || []), ...ticketIssues(review)]
}

// The assumptions the owner may overrule: the ones given, and once each, every promise a reading
// leaves to the owner: one the spec decided part of (assumed), and one two readers read otherwise
// (bent).
function withSurfaced(assumptions, readings) {
  const all = [...(assumptions || [])]
  for (const p of readings.ticketPromises || []) {
    if (p.reading !== 'assumed' && p.reading !== 'bent') continue
    if (all.some(a => a && a.question === p.promise)) continue
    all.push({ question: p.promise, assumed: p.specLine, refuter: p.reading === 'bent' ? p.change : p.ownerSees })
  }
  return all
}

// The judge reads an open question as a text; the spec role returns it with its options.
function questionText(question) {
  return String(question && typeof question === 'object' ? question.question || '' : question)
}

// What a review of the branch as it now stands forces, when the branch was brought up to date
// behind another merge (ADR 0023): the same evidence rules as the first review, less the
// critic's, whose findings the first run already closed.
function rejudged({ review, reconcile, dependencies }) {
  const covered = coveredGuardPaths(reconcile ? reconcile.disputes : [], review.disputeCoverage)
  for (const p of specDecidedDependencies(dependencies, review.dependencyNotes)) covered.add(p)
  for (const p of additiveRecords(review.recordNotes)) covered.add(p)
  const undisputed = (review.guardChanges || []).filter(p => p && !covered.has(p))
  const issues = blocking(reviewIssues(review))
  const forced = []
  if (undisputed.length) forced.push(`guard paths changed without a dispute record, a spec-named dependency or an additions note covering them: ${undisputed.join(', ')}`)
  if (issues.length) forced.push(`${issues.length} blocker/major review issue(s)`)
  if (reconcile && reconcile.checkPasses !== true) forced.push('the reconcile result reported checkPasses: false')
  if (review.checkPasses !== true) forced.push('the review reported checkPasses: false')
  return { forced, issues, undisputed, verdict: forced.length ? 'request_changes' : review.verdict }
}

function must(result, label) {
  if (!result) throw new Error(`${label} agent returned no result (skipped or died); resume the run to retry`)
  return result
}

// ---------------------------------------------------------------------------
// Rejudge. The branch is built, approved and committed; the default branch moved under it and
// the skill rebased it (scripts/drain.py update). No preflight: the ticket was checked before
// the first run, and the branch may already resolve it. The result's verdictRecord replaces the
// first run's, so the record names a tree a review saw.
// ---------------------------------------------------------------------------
if (REJUDGE) {
  SLUG = String(REJUDGE.slug || '')
  const spec = { slug: SLUG, specPath: String(REJUDGE.specPath || '') }
  if (!SLUG || !spec.specPath || !BRANCH) {
    throw new Error('rejudge needs args.branch and args.rejudge = { slug, specPath }: pass what the first run returned')
  }
  const conflicts = (REJUDGE.conflicts || []).filter(Boolean)
  let reconcile = null
  if (conflicts.length > 0 || REJUDGE.checkRed === true) {
    phase('Reconcile')
    reconcile = must(
      await agent(reconcilePrompt(spec, conflicts), { label: 'reconcile', phase: 'Reconcile', schema: IMPLEMENT_SCHEMA }),
      'reconcile',
    )
    log(`Reconcile: ${conflicts.length} conflicted path(s), check=${reconcile.checkPasses}`)
  }
  phase('Review')
  const review = must(await agent(reviewPrompt(spec, true, SETTLED), { label: 'review-again', phase: 'Review', schema: REVIEW_SCHEMA }), 'review')
  const judged = rejudged({ review, reconcile, dependencies: REJUDGE.dependencies })
  const assumptions = withSurfaced(REJUDGE.assumptions, review)
  log(`Review again: ${review.verdict}` + (judged.forced.length ? `; forced to request_changes: ${judged.forced.join('; ')}` : ''))
  return {
    slug: SLUG,
    ref: REF,
    rejudged: true,
    verdict: judged.verdict,
    verdictForcedBy: judged.forced,
    filesChanged: reconcile ? reconcile.filesChanged : [],
    reconcileNotes: reconcile ? reconcile.notes : '',
    disputes: reconcile ? reconcile.disputes || [] : [],
    reviewIssues: reviewIssues(review),
    reviewVerdict: review.verdict,
    ticketPromises: review.ticketPromises,
    acceptedInputs: review.acceptedInputs,
    adrSuggested: review.adrSuggested,
    guardChanges: review.guardChanges,
    undisputedGuardChanges: judged.undisputed,
    dependencyNotes: review.dependencyNotes ?? [],
    recordNotes: review.recordNotes ?? [],
    specPath: spec.specPath,
    assumptions,
    checkPasses: review.checkPasses === true,
    checkOutput: review.checkOutput,
    branch: BRANCH,
    verdictRecord: verdictRecord({
      slug: SLUG,
      ticket: REF,
      verdict: judged.verdict,
      forcedBy: judged.forced,
      findings: judged.issues,
      openQuestions: [],
      assumptions,
    }),
    requiredByOrchestrator: [
      'verdict is "approve": anything else leaves the branch to the owner, with verdictForcedBy as the reasons',
      'filesChanged, when the reconcile step edited anything, is committed on the branch before the verdict record',
      'verdictRecord goes on stdin to the `merge-ready` entry point with `record`, and the record is committed alone, last',
      'the merge is the merge judge\'s to decide: report its lines and merge nothing by hand',
    ],
  }
}

// ---------------------------------------------------------------------------
// Phase 0: Preflight. One low-effort agent runs the plugin's script and relays its JSON;
// the script decides, the agent types. Red stops the run before the spec agent exists.
// ---------------------------------------------------------------------------
phase('Preflight')
const preflight = must(await agent(preflightPrompt(), { label: 'preflight', phase: 'Preflight', effort: 'low', schema: PREFLIGHT_SCHEMA }), 'preflight')
if (!preflight.ok) {
  const failures = preflight.failures || []
  log(`Blocked by the preflight: ${failures.map(f => f.check).join(', ') || 'no reason given'}`)
  return {
    verdict: 'blocked',
    ref: REF,
    preflight,
    branch: BRANCH || preflight.branch || '',
    requiredByOrchestrator: [
      ...failures.map(f => `${f.check}: ${f.detail} -> ${f.produce}`),
      'Re-run `/ratchet:feature <ticket>` once the ticket carries what is missing; do not open a PR from a blocked run',
    ],
  }
}
SLUG = preflight.slug
BRANCH = BRANCH || preflight.branch
BASE = BASE || preflight.base || ''
CRITIC = preflight.critic !== false
CRITIC_REASON = String(preflight.critic_reason || '')
if (!SLUG || !BRANCH) throw new Error(`preflight returned ok without a slug and branch: ${JSON.stringify(preflight)}`)
log(`Preflight green: ticket ${preflight.number || REF} "${preflight.title}" on branch ${BRANCH}`)

// ---------------------------------------------------------------------------
// Phase 1: Spec
// ---------------------------------------------------------------------------
phase('Spec')
log(`Spec: ${REF} on branch ${BRANCH}`)
const written = must(await agent(specPrompt(), { label: 'spec', phase: 'Spec', schema: SPEC_SCHEMA }), 'spec')
log(`Spec ${written.slug}: ${written.testFiles.length} test file(s), layers ${written.layersTouched.join(', ')}` +
  (written.failingForRightReason ? '' : ' -- WARNING: tests not failing for the right reason') +
  (written.openQuestions.length ? `, ${written.openQuestions.length} open question(s)` : ''))
const writtenStop = specStop(written)
if (writtenStop) return writtenStop

// ---------------------------------------------------------------------------
// Phase 1a: Ticket check. One agent reads the ticket and spec.md, and nothing else. A gap goes back
// to the spec role once, while no code exists to waste, and the check reads the amended spec.
// ---------------------------------------------------------------------------
phase('Ticket check')
const firstCheck = must(
  await agent(ticketCheckPrompt(written), { label: 'ticket-check', phase: 'Ticket check', schema: TICKET_CHECK_SCHEMA }),
  'ticket check',
)
const found = gaps(firstCheck)
if (found.length) log(`Ticket check: ${found.length} gap(s); the spec role amends spec.md once`)
const spec = found.length
  ? must(await agent(specPrompt(found), { label: 'spec-amend', phase: 'Ticket check', schema: SPEC_SCHEMA }), 'spec amend')
  : written
const amendStop = found.length ? specStop(spec) : null
if (amendStop) return amendStop
const check = found.length
  ? must(
    await agent(ticketCheckPrompt(spec), { label: 'ticket-recheck', phase: 'Ticket check', schema: TICKET_CHECK_SCHEMA }),
    'ticket recheck',
  )
  : firstCheck
const settled = settle(check)
const kept = keptFromTicket(check)
const assumedByRun = assumedCases(check)
if (kept.length) log(`Ticket check: ${kept.length} promise(s) still left out of the plan, built as the ticket says`)
if (assumedByRun.length) log(`Ticket check: ${assumedByRun.length} input case(s) still undecided, built with the safe answer`)

// ---------------------------------------------------------------------------
// Phase 2: Implement
// ---------------------------------------------------------------------------
phase('Implement')
const impl = must(
  await agent(implementPrompt(spec, kept.map(k => k.question), assumedByRun), { label: 'implement', phase: 'Implement', schema: IMPLEMENT_SCHEMA }),
  'implement',
)
let filesChanged = union(spec.testFiles, impl.filesChanged)
const disputes = [...(impl.disputes || [])]
const implStop = noRoom('the implementation', impl.changeSize, CRITIC)
if (implStop) return tooLarge(implStop, spec, { filesChanged, disputes, checkOutput: impl.checkOutput })
let lastCheckOutput = impl.checkOutput
let lastCheckPasses = impl.checkPasses === true
log(`Implement: ${impl.filesChanged.length} file(s), tests pass=${impl.allNewTestsPass}, check=${impl.checkPasses}`)

// ---------------------------------------------------------------------------
// Phase 3: Critique, and Phase 4: Fix loop (fresh implementer per round, then re-critique).
// Both run only when the preflight says the project is depended-on (ADR 0028); otherwise the
// critique is an empty one and the review follows the implementation.
// ---------------------------------------------------------------------------
let critique = {
  findings: [], testsAdded: [], attempted: [], guardFilesChanged: [],
  fitnessSabotageConfirmed: false, fitnessSabotageSummary: '',
}
const allCriticFindings = []
let iterations = 0
if (CRITIC) {
  phase('Critique')
  critique = must(
    await agent(critiquePrompt(spec, filesChanged, null), { label: 'critique', phase: 'Critique', schema: CRITIQUE_SCHEMA }),
    'critique',
  )
  filesChanged = union(filesChanged, critique.testsAdded)
  allCriticFindings.push({ round: 0, findings: critique.findings })
  log(`Critique: ${critique.findings.length} finding(s), ${owed(critique.findings).length} owed a fix; ` +
    `fitness sabotage confirmed=${critique.fitnessSabotageConfirmed}`)

  phase('Fix')
  while (owed(critique.findings).length > 0 && iterations < MAX_FIX_ITERATIONS) {
    iterations += 1
    log(`Fix ${iterations}/${MAX_FIX_ITERATIONS}: ${owed(critique.findings).length} finding(s) owed a fix`)
    const fix = must(
      await agent(fixPrompt(spec, critique.findings, critique.testsAdded, iterations), {
        label: `fix-${iterations}`, phase: 'Fix', schema: IMPLEMENT_SCHEMA,
      }),
      `fix-${iterations}`,
    )
    filesChanged = union(filesChanged, fix.filesChanged)
    disputes.push(...(fix.disputes || []))
    lastCheckOutput = fix.checkOutput
    lastCheckPasses = fix.checkPasses === true
    critique = must(
      await agent(critiquePrompt(spec, filesChanged, critique), {
        label: `critique-${iterations}`, phase: 'Fix', schema: CRITIQUE_SCHEMA,
      }),
      `critique-${iterations}`,
    )
    filesChanged = union(filesChanged, critique.testsAdded)
    allCriticFindings.push({ round: iterations, findings: critique.findings })
    log(`Re-critique ${iterations}: ${critique.findings.length} finding(s), ${owed(critique.findings).length} still owed a fix`)
  }
} else {
  log(`Critique and Fix skipped: ${CRITIC_REASON || 'the preflight says the critic does not run'}`)
}
const unresolved = owed(critique.findings)
if (unresolved.length) log(`Fix loop exhausted with ${unresolved.length} unresolved blocking finding(s); review runs anyway`)
else if (CRITIC && iterations === 0) log('No blocking findings; fix loop skipped')

// ---------------------------------------------------------------------------
// Phase 5: Review (independent)
// ---------------------------------------------------------------------------
phase('Review')
const review = must(await agent(reviewPrompt(spec, false, settled), { label: 'review', phase: 'Review', schema: REVIEW_SCHEMA }), 'review')
// Why: the drain reads the owed kind from this output (scripts/drain.py OWED_RED). The reviewer
// reports its own run of check, which may not repeat the line the implementer was stopped on.
const builderOutput = lastCheckOutput
lastCheckOutput = review.checkOutput
if (OWED_RED.test(builderOutput || '') && !OWED_RED.test(lastCheckOutput || '')) lastCheckOutput = `${builderOutput}\n${lastCheckOutput}`
const issues = reviewIssues(review)
log(`Review: ${review.verdict}, ${issues.length} issue(s)` + (review.adrSuggested ? `, ADR suggested: ${review.adrSuggested}` : ''))

// The verdict is derived from the evidence the agents returned, never taken on the reviewer's
// word alone. Pure over the agents' own reports, so it resumes deterministically.
const guardChanges = union(review.guardChanges, critique.guardFilesChanged)
const covered = coveredGuardPaths(disputes, review.disputeCoverage)
const decidedDependencies = specDecidedDependencies(spec.dependencies, review.dependencyNotes)
for (const p of decidedDependencies) covered.add(p)
const addedRecords = additiveRecords(review.recordNotes)
for (const p of addedRecords) covered.add(p)
const undisputedGuardChanges = guardChanges.filter(p => !covered.has(p))
const blockingReviewIssues = blocking(issues)
const assumptions = withSurfaced(withSurfaced([...(spec.assumptions || []), ...kept, ...assumedByRun], check), review)

const forcedReasons = []
if (undisputedGuardChanges.length) forcedReasons.push(`guard paths changed without a dispute record, a spec-named dependency or an additions note covering them: ${undisputedGuardChanges.join(', ')}`)
if (unresolved.length) forcedReasons.push(`${unresolved.length} unresolved blocker/major critic finding(s)`)
if (blockingReviewIssues.length) forcedReasons.push(`${blockingReviewIssues.length} blocker/major review issue(s)`)
if (!lastCheckPasses) forcedReasons.push('the last implement/fix result reported checkPasses: false')
if (review.checkPasses !== true) forcedReasons.push('the review\'s run of check on the final tree was not green')
if (!spec.failingForRightReason) forcedReasons.push('the spec reported failingForRightReason: false')
const unquoted = (spec.testFiles || []).filter(f => !(spec.failures || []).some(x => x && x.testFile === f && String(x.content || '').trim()))
if (unquoted.length) forcedReasons.push(`spec test files with no quoted failure content: ${unquoted.join(', ')}`)
if (criticSilent(CRITIC, critique)) forcedReasons.push('the critic found nothing and lists nothing attempted')
const verdict = forcedReasons.length ? 'request_changes' : review.verdict
if (verdict !== review.verdict) log(`Verdict forced to request_changes: ${forcedReasons.join('; ')}`)
else if (forcedReasons.length) log(`Reviewer and script agree on request_changes: ${forcedReasons.join('; ')}`)

// ---------------------------------------------------------------------------
// Result. The orchestrator commits / opens the PR; this workflow never does.
// ---------------------------------------------------------------------------
return {
  slug: SLUG,
  ref: REF,
  verdict,
  verdictForcedBy: forcedReasons,
  iterations,
  filesChanged,
  critic: { ran: CRITIC, reason: CRITIC ? CRITIC_REASON : `the critic was not run: ${CRITIC_REASON || 'the preflight says it does not run'}` },
  criticFindings: critique.findings,
  criticFindingsByRound: allCriticFindings,
  unresolvedBlockingFindings: unresolved,
  reviewIssues: issues,
  ticketFindings: ticketIssues(review),
  settled,
  reviewVerdict: review.verdict,
  ticketPromises: review.ticketPromises,
  acceptedInputs: review.acceptedInputs,
  adrSuggested: review.adrSuggested,
  disputes,
  guardChanges,
  disputeCoverage: review.disputeCoverage,
  dependencies: spec.dependencies ?? [],
  dependencyNotes: review.dependencyNotes ?? [],
  decidedDependencyPaths: Array.from(decidedDependencies).sort(),
  recordNotes: review.recordNotes ?? [],
  additiveRecordPaths: Array.from(addedRecords).sort(),
  undisputedGuardChanges,
  specPath: spec.specPath,
  testFiles: spec.testFiles,
  openQuestions: spec.openQuestions,
  assumptions,
  fitnessSabotageConfirmed: critique.fitnessSabotageConfirmed,
  fitnessSabotageSummary: critique.fitnessSabotageSummary,
  specFailures: spec.failures,
  criticAttempted: critique.attempted,
  checkPasses: lastCheckPasses && review.checkPasses === true,
  checkOutput: lastCheckOutput,
  branch: BRANCH,
  verdictRecord: verdictRecord({
    slug: SLUG,
    ticket: REF,
    verdict,
    forcedBy: forcedReasons,
    findings: [...unresolved, ...blockingReviewIssues],
    openQuestions: spec.openQuestions,
    assumptions,
  }),
  requiredByOrchestrator: [
    'verdict is "approve" (the script forces request_changes when its evidence says otherwise)',
    'unresolvedBlockingFindings is empty and reviewIssues has no blocker or major entry',
    'when critic.ran is false, the pull request says the critic was not run and gives critic.reason: no adversary attacked this change, and the review is the only independent look',
    'checkOutput tail shows check green and checkPasses is true',
    'undisputedGuardChanges is empty; every entry in disputes has been relayed to the owner, who decides',
    'adrSuggested, if set, is written in docs/adr/ in the same PR, under the next free number; the guard lets a new ADR through and asks the owner for one that supersedes another or carries a Relaxes: line',
    'every addition in recordNotes (each new term, Avoid item and ADR) is listed in the pull request under Decisions',
    'every entry in assumptions is listed in the pull request under Conjecture, with its refuter, for the owner to counter',
    'after the change is committed, verdictRecord goes on stdin to the `merge-ready` entry point with `record`, and the docs/features/<slug>/verdict.json it writes is committed alone, last: a commit after it sends the merge to the owner',
    'the merge is the merge judge\'s to decide, never the orchestrator\'s: report `merge: agent` or `merge: owner` with the judge\'s reasons, and merge nothing by hand',
  ],
}
