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

export const meta = {
  name: 'pipeline',
  description: 'Build one feature from an agent-ready ticket with the Ratchet method: preflight -> spec -> implement -> adversarial critique -> fix loop -> independent review',
  whenToUse: 'Run by the /ratchet:feature skill with args { ref, branch, base?, root?, preflight? } after its own preflight is green; not by hand. With args.rejudge = { slug, specPath, conflicts?, checkRed?, dependencies?, assumptions? } it judges again a branch that was brought up to date behind another merge: Reconcile when the rebase conflicted or check went red, then Review. Never commits, pushes or opens a PR; the orchestrator does that after reading the result.',
  phases: [
    { title: 'Preflight', detail: 'scripts/feature_preflight.py on the ticket: open, `## Conjecture` stated, agent-ready label, clean tree; stops with verdict "blocked" naming what produces each missing piece' },
    { title: 'Spec', detail: 'ticket -> docs/features/<slug>/spec.md + failing tests; stops with verdict "blocked" if the ticket leaves open questions' },
    { title: 'Implement', detail: 'make the new tests pass test-first with tdd; check green' },
    { title: 'Critique', detail: 'one adversary with the rule of evidence: properties, edge cases, mutation, fuzz, fitness sabotage as binding.md names them' },
    { title: 'Fix', detail: 'fresh implementer per blocker/major round (max 2), re-critique after each' },
    { title: 'Reconcile', detail: 'rejudge only: the rebase onto the moved default branch finished and check green again' },
    { title: 'Review', detail: 'independent code-review of the diff on the spec, standards and guard axes; the script forces the verdict from the evidence' },
  ],
}

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
// The branch is the preflight's to derive (feature/<number>-<slug>) unless the skill passed it.
let BRANCH = typeof ARGS.branch === 'string' && ARGS.branch ? ARGS.branch : ''
let SLUG = ''

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

function commonRules(rebasing) {
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
- Before returning, run \`check\` from the repository root and include the LAST ~40 lines of
  its output verbatim in the \`checkOutput\` field of your structured result.
- Your final output is consumed by a script, not a human: return only the structured object.
${GUARD_RULES}`
}

function checkOutputField() {
  return {
    checkOutput: {
      type: 'string',
      description: 'Last ~40 lines of the `check` entry point output, verbatim, run from the repo root before returning',
    },
  }
}

// ---------------------------------------------------------------------------
// Schemas
// ---------------------------------------------------------------------------
const SPEC_SCHEMA = {
  type: 'object',
  properties: {
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
    ...checkOutputField(),
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
        },
        required: ['severity', 'title', 'evidence', 'suggestedTest'],
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
    ...checkOutputField(),
  },
  required: [
    'findings', 'testsAdded', 'attempted', 'mutationSummary', 'fuzzSummary', 'fitnessSabotageConfirmed',
    'fitnessSabotageSummary', 'guardFilesChanged', 'checkOutput',
  ],
}

const REVIEW_SCHEMA = {
  type: 'object',
  properties: {
    verdict: { type: 'string', enum: ['approve', 'request_changes'] },
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
  required: ['verdict', 'issues', 'adrSuggested', 'guardChanges', 'dependencyNotes', 'recordNotes', 'disputeCoverage', 'checkPasses', 'checkOutput'],
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

function ticketHint() {
  return /^#?\d+$/.test(REF)
    ? `issue ${REF.replace('#', '')} on the tracker (\`gh issue view ${REF.replace('#', '')} --json number,title,body,labels,comments\`)`
    : `the file \`${REF}\` under ${ROOT_HINT}`
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
title, slug, branch, base, label }) and a verdict line on stderr. Return that JSON's fields in the
structured result, verbatim, plus the full output. Do not fix, label, edit or commit anything,
and do not re-run with different arguments. If the script cannot be found or Python refuses
it, return ok: false with one failure { check: "script", detail: <what happened>, produce:
"install the ratchet plugin; the skill passes the script path as args.preflight" }.`
}

function specPrompt() {
  return `${PREAMBLE}
You are the SPEC agent for one feature of this repository.
${commonRules()}
Load the \`mattpocock-skills:tdd\` skill: it says what a good test is and how the seams under
test are chosen. The spec names those seams; the owner has agreed the approach through the
ticket's \`## Conjecture\` section and the agent-ready label.

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
   - Acceptance criteria: numbered, each in Given / When / Then form, each testable
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
4. Run \`check\` and capture its tail. It is expected to be red only on your new tests.

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

function implementPrompt(spec) {
  return `${PREAMBLE}
You are the IMPLEMENT agent for feature \`${spec.slug}\` of this repository.
${commonRules()}
Inputs you may read:
- The spec: \`${spec.specPath}\`
- The failing tests: ${JSON.stringify(spec.testFiles)}
- Layers the spec expects you to touch: ${JSON.stringify(spec.layersTouched)}
- AGENTS.md, GLOSSARY.md, docs/process/binding.md, docs/adr/, and the existing code those
  tests exercise
You are deliberately given neither the ticket nor the spec author's reasoning. If
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

Finish when every listed test passes AND \`check\` exits 0. If a gate blocks you and you
believe the gate, not your code, is wrong, write a gate dispute and list it in \`disputes\`.
If you cannot get there, return anyway with \`checkPasses: false\` and explain in \`notes\`.`
}

function fixPrompt(spec, findings, testsAdded, iteration) {
  const blockingFindings = findings.filter(f => f.severity !== 'minor')
  const minors = findings.filter(f => f.severity === 'minor')
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

Findings you MUST resolve (blocker/major):
${JSON.stringify(blockingFindings, null, 2)}

Findings you MAY resolve if cheap (minor):
${JSON.stringify(minors, null, 2)}

Resolve each blocking finding by changing the implementation; the tests are fixed inputs. A
critic test that asserts something the spec contradicts stays red and is explained in
\`notes\`. A finding that can only be resolved by changing a guard path, or a gate you believe
is wrong, becomes a gate dispute listed in \`disputes\`. Finish when the spec tests and the
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
${commonRules()}
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
   the domain has them, very large values, unicode. Put them in the test directory binding.md
   names, next to the spec's tests, clearly named with the slug and the word critique.
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

${SEVERITY} \`check\` is expected to be red only on the failing tests you kept; say so in the
finding evidence.`
}

function reviewPrompt(spec, again) {
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
- The spec: \`${spec.specPath}\`
- The diff: the change is on branch \`${BRANCH}\`, uncommitted unless this is a second review.
  Enumerate it with
  \`git diff ${BASE} --stat\`, \`git diff ${BASE}\` and \`git status --porcelain\` (untracked files are
  new files: read them). If the branch has commits, \`git diff ${BASE}...${BRANCH}\` is equivalent.
- AGENTS.md, GLOSSARY.md, docs/adr/, docs/process/binding.md, docs/disputes/, DESIGN.md
- Any generated contract artefacts that binding.md names
The implementer's notes, the critic's findings and the ticket are outside your inputs:
judge the code against the spec and the standards only.

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

${SEVERITY}

Verdict: \`approve\` only if there are no blocker or major issues on any axis, every changed
guard path is covered by a dispute record, is a dependency the spec decided or is a record the
change only adds to, and \`check\` exits 0; otherwise
\`request_changes\`. The script re-derives the verdict from your \`issues\`, \`guardChanges\`,
\`dependencyNotes\`, \`recordNotes\` and \`disputeCoverage\`, so make them complete.`
}

// ---------------------------------------------------------------------------
// Helpers (pure)
// ---------------------------------------------------------------------------
function blocking(findings) {
  return (findings || []).filter(f => f && (f.severity === 'blocker' || f.severity === 'major'))
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
  const issues = blocking(review.issues)
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
  const review = must(await agent(reviewPrompt(spec, true), { label: 'review-again', phase: 'Review', schema: REVIEW_SCHEMA }), 'review')
  const judged = rejudged({ review, reconcile, dependencies: REJUDGE.dependencies })
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
    reviewIssues: review.issues,
    reviewVerdict: review.verdict,
    adrSuggested: review.adrSuggested,
    guardChanges: review.guardChanges,
    undisputedGuardChanges: judged.undisputed,
    dependencyNotes: review.dependencyNotes ?? [],
    recordNotes: review.recordNotes ?? [],
    specPath: spec.specPath,
    assumptions: REJUDGE.assumptions ?? [],
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
      assumptions: REJUDGE.assumptions,
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
if (!SLUG || !BRANCH) throw new Error(`preflight returned ok without a slug and branch: ${JSON.stringify(preflight)}`)
log(`Preflight green: ticket ${preflight.number || REF} "${preflight.title}" on branch ${BRANCH}`)

// ---------------------------------------------------------------------------
// Phase 1: Spec
// ---------------------------------------------------------------------------
phase('Spec')
log(`Spec: ${REF} on branch ${BRANCH}`)
const spec = must(await agent(specPrompt(), { label: 'spec', phase: 'Spec', schema: SPEC_SCHEMA }), 'spec')
log(`Spec ${spec.slug}: ${spec.testFiles.length} test file(s), layers ${spec.layersTouched.join(', ')}` +
  (spec.failingForRightReason ? '' : ' -- WARNING: tests not failing for the right reason') +
  (spec.openQuestions.length ? `, ${spec.openQuestions.length} open question(s)` : ''))

// An open question is the owner's to answer, not the implementer's to guess, and a guard path
// other than a record the spec only added to is the owner's to edit. Stop here; the owner answers or lands the edits, and the run repeats.
const guardEdits = spec.guardEdits ?? []
if (spec.openQuestions.length > 0 || guardEdits.length > 0) {
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
      'Put each open question to the owner through the question tool, one at a time, its recommended option first; each answer goes on the ticket under `## Answers`, where the next spec agent reads it',
      'Give the owner each guard edit to land on the default branch (by hand, or with an agent at the keyboard where the guard can ask); an owner-away run cannot',
      'When the owner has answered every open question and there is no guard edit, build the ticket again from the preflight in the same turn; with guard edits, the route names the rerun once they land. Do not open a PR from a blocked run',
    ],
  }
}

// ---------------------------------------------------------------------------
// Phase 2: Implement
// ---------------------------------------------------------------------------
phase('Implement')
const impl = must(
  await agent(implementPrompt(spec), { label: 'implement', phase: 'Implement', schema: IMPLEMENT_SCHEMA }),
  'implement',
)
let filesChanged = union(spec.testFiles, impl.filesChanged)
const disputes = [...(impl.disputes || [])]
let lastCheckOutput = impl.checkOutput
let lastCheckPasses = impl.checkPasses === true
log(`Implement: ${impl.filesChanged.length} file(s), tests pass=${impl.allNewTestsPass}, check=${impl.checkPasses}`)

// ---------------------------------------------------------------------------
// Phase 3: Critique
// ---------------------------------------------------------------------------
phase('Critique')
let critique = must(
  await agent(critiquePrompt(spec, filesChanged, null), { label: 'critique', phase: 'Critique', schema: CRITIQUE_SCHEMA }),
  'critique',
)
filesChanged = union(filesChanged, critique.testsAdded)
const allCriticFindings = [{ round: 0, findings: critique.findings }]
log(`Critique: ${critique.findings.length} finding(s), ${blocking(critique.findings).length} blocking; ` +
  `fitness sabotage confirmed=${critique.fitnessSabotageConfirmed}`)

// ---------------------------------------------------------------------------
// Phase 4: Fix loop (fresh implementer per round, then re-critique)
// ---------------------------------------------------------------------------
phase('Fix')
let iterations = 0
while (blocking(critique.findings).length > 0 && iterations < MAX_FIX_ITERATIONS) {
  iterations += 1
  log(`Fix ${iterations}/${MAX_FIX_ITERATIONS}: ${blocking(critique.findings).length} blocking finding(s)`)
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
  log(`Re-critique ${iterations}: ${critique.findings.length} finding(s), ${blocking(critique.findings).length} still blocking`)
}
const unresolved = blocking(critique.findings)
if (unresolved.length) log(`Fix loop exhausted with ${unresolved.length} unresolved blocking finding(s); review runs anyway`)
else if (iterations === 0) log('No blocking findings; fix loop skipped')

// ---------------------------------------------------------------------------
// Phase 5: Review (independent)
// ---------------------------------------------------------------------------
phase('Review')
const review = must(await agent(reviewPrompt(spec), { label: 'review', phase: 'Review', schema: REVIEW_SCHEMA }), 'review')
lastCheckOutput = review.checkOutput
log(`Review: ${review.verdict}, ${review.issues.length} issue(s)` + (review.adrSuggested ? `, ADR suggested: ${review.adrSuggested}` : ''))

// The verdict is derived from the evidence the agents returned, never taken on the reviewer's
// word alone. Pure over the agents' own reports, so it resumes deterministically.
const guardChanges = union(review.guardChanges, critique.guardFilesChanged)
const covered = coveredGuardPaths(disputes, review.disputeCoverage)
const decidedDependencies = specDecidedDependencies(spec.dependencies, review.dependencyNotes)
for (const p of decidedDependencies) covered.add(p)
const addedRecords = additiveRecords(review.recordNotes)
for (const p of addedRecords) covered.add(p)
const undisputedGuardChanges = guardChanges.filter(p => !covered.has(p))
const blockingReviewIssues = blocking(review.issues)

const forcedReasons = []
if (undisputedGuardChanges.length) forcedReasons.push(`guard paths changed without a dispute record, a spec-named dependency or an additions note covering them: ${undisputedGuardChanges.join(', ')}`)
if (unresolved.length) forcedReasons.push(`${unresolved.length} unresolved blocker/major critic finding(s)`)
if (blockingReviewIssues.length) forcedReasons.push(`${blockingReviewIssues.length} blocker/major review issue(s)`)
if (!lastCheckPasses) forcedReasons.push('the last implement/fix result reported checkPasses: false')
if (!spec.failingForRightReason) forcedReasons.push('the spec reported failingForRightReason: false')
const unquoted = (spec.testFiles || []).filter(f => !(spec.failures || []).some(x => x && x.testFile === f && String(x.content || '').trim()))
if (unquoted.length) forcedReasons.push(`spec test files with no quoted failure content: ${unquoted.join(', ')}`)
const lastAttempted = (critique.attempted || []).filter(a => a && String(a).trim())
if (critique.findings.length === 0 && lastAttempted.length === 0) forcedReasons.push('the critic found nothing and lists nothing attempted')
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
  criticFindings: critique.findings,
  criticFindingsByRound: allCriticFindings,
  unresolvedBlockingFindings: unresolved,
  reviewIssues: review.issues,
  reviewVerdict: review.verdict,
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
  assumptions: spec.assumptions ?? [],
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
    assumptions: spec.assumptions,
  }),
  requiredByOrchestrator: [
    'verdict is "approve" (the script forces request_changes when its evidence says otherwise)',
    'unresolvedBlockingFindings is empty and reviewIssues has no blocker or major entry',
    'checkOutput tail shows check green and checkPasses is true',
    'undisputedGuardChanges is empty; every entry in disputes has been relayed to the owner, who decides',
    'adrSuggested, if set, is written in docs/adr/ in the same PR, under the next free number; the guard lets a new ADR through and asks the owner for one that supersedes another or carries a Relaxes: line',
    'every addition in recordNotes (each new term, Avoid item and ADR) is listed in the pull request under Decisions',
    'every entry in assumptions is listed in the pull request under Conjecture, with its refuter, for the owner to counter',
    'after the change is committed, verdictRecord goes on stdin to the `merge-ready` entry point with `record`, and the docs/features/<slug>/verdict.json it writes is committed alone, last: a commit after it sends the merge to the owner',
    'the merge is the merge judge\'s to decide, never the orchestrator\'s: report `merge: agent` or `merge: owner` with the judge\'s reasons, and merge nothing by hand',
  ],
}
