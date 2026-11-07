// What this workflow assumes of the repository that adopts the Ratchet method.
// It is shipped by the ratchet plugin and runs as /ratchet:feature in any repository, so it
// knows nothing about the project's domain. It relies on the method's command contract and
// file layout only. The repository must provide: a just recipe named check that runs the
// tier 0 and tier 1 gates and exits non-zero on any failure; a just recipe named guard-diff
// that prints every guard path the branch has changed, one per line; the guard manifest at
// guards/manifest.toml listing the guard path groups and patterns; a feature brief at
// docs/features/<slug>/brief.md written by the orchestrator; a docs/disputes/ directory for
// gate dispute records; CLAUDE.md at the repository root stating the non-negotiables;
// GLOSSARY.md stating the vocabulary; and docs/process/binding.md stating how the method
// binds to this repository (layers, test frameworks, mutation, fuzz and fitness gates,
// generated contract artefacts). Every agent loads the ratchet:method skill first and takes
// every project-specific rule from those three files, never from this script.

export const meta = {
  name: 'feature',
  description: 'Build one feature from a brief with the Ratchet method: spec -> implement -> adversarial critique -> fix loop -> independent review',
  whenToUse: 'When a feature brief exists at docs/features/<slug>/brief.md in a repository that follows the Ratchet method and a working branch has been created. Never commits, pushes or opens a PR; the orchestrator does that after reading the result.',
  phases: [
    { title: 'Spec', detail: 'brief -> docs/features/<slug>/spec.md + failing tests; stops with verdict "blocked" if the brief leaves open questions' },
    { title: 'Implement', detail: 'make the new tests pass test-first; just check green' },
    { title: 'Critique', detail: 'adversarial: properties, edge cases, mutation, fuzz, fitness sabotage as binding.md names them' },
    { title: 'Fix', detail: 'fresh implementer per blocker/major round (max 2), re-critique after each' },
    { title: 'Review', detail: 'independent spec + standards review of the diff; the script forces the verdict from the evidence' },
  ],
}

// ---------------------------------------------------------------------------
// Arguments. Everything that could vary between runs comes in via args so the
// script is a pure function of (args, agent results) and resumes cleanly.
// ---------------------------------------------------------------------------
if (!args || typeof args.brief !== 'string' || typeof args.branch !== 'string') {
  throw new Error('feature workflow needs args = { brief: "<path to brief .md>", branch: "<git branch>", root?: "<repo root>", base?: "<base branch, default main>" }')
}
const BRIEF = args.brief
const BRANCH = args.branch
const BASE = typeof args.base === 'string' && args.base ? args.base : 'main'
const ROOT = typeof args.root === 'string' && args.root ? args.root : ''
const MAX_FIX_ITERATIONS = 2

const ROOT_HINT = ROOT
  ? `the repository root (${ROOT}, the directory holding CLAUDE.md)`
  : 'the repository root (the directory holding CLAUDE.md)'

// ---------------------------------------------------------------------------
// Shared prompt fragments
// ---------------------------------------------------------------------------
// Every role begins here. The method and the repository's own binding files are the source
// of every project-specific rule; this script states none of them.
const PREAMBLE = 'Load the `ratchet:method` skill (the method this repository follows), then read CLAUDE.md, docs/process/binding.md and GLOSSARY.md. Obey them.'

// Guards and gate disputes. The harness enforces this; the prompt says it so an agent stops
// at the first block instead of probing.
const GUARD_RULES = `
Guard paths and gate disputes:
- The files that define the quality gates are guard paths: the groups and patterns in
  \`guards/manifest.toml\`. Run \`just guard-diff\` to list the ones your branch changed.
- NEVER edit, move, delete or regenerate a guard path. NEVER bypass a gate: no --no-verify,
  no SKIP=, no force push, no lowering a threshold, no skipping, weakening or deleting a test
  to get green. A PreToolUse hook enforces this: an edit to a guard path waits for a human
  approval you cannot get or, for the harness's own files, is denied; shell writes to guard
  paths are denied. A blocked call is a stop sign. Do not look for another route (another
  tool, an interpreter, a copy, a script).
- If you believe a gate is wrong, stop working on it and write a gate dispute at
  \`docs/disputes/NNNN-<slug>.md\` using the method's dispute template: NNNN is one more than
  the highest existing number (0001 for the first); the record opens with Status: open, who
  raised it and the date, then the sections Gate, Exact failure, Why the gate is wrong,
  Options and Recommendation. Name every guard path the dispute covers in its Gate section.
  Then report it in your structured result (the \`disputes\` field where your schema has one,
  otherwise your notes or findings) and carry on with any work the gate does not block. The
  owner decides; you never change the gate.
`

const COMMON_RULES = `
Ground rules for every agent in this pipeline:
- Work in ${ROOT_HINT} on branch \`${BRANCH}\`.
  First run \`git rev-parse --abbrev-ref HEAD\`; if it does not print \`${BRANCH}\`, run
  \`git switch ${BRANCH}\` (or \`git switch -c ${BRANCH} ${BASE}\` if the branch does not exist).
- NEVER run \`git commit\`, \`git push\`, \`git stash\`, \`git reset\`, \`git checkout -- <file>\` or
  anything else that discards or publishes work. Leave all changes uncommitted on the working tree.
- Vocabulary comes from GLOSSARY.md: use its terms in code, tests and docs and avoid the
  words it lists as banned.
- The project's non-negotiables are whatever CLAUDE.md lists; its layers and their allowed
  dependency direction are whatever CLAUDE.md and docs/process/binding.md define. Treat each
  as a hard rule, not a preference.
- Before returning, run \`just check\` from the repository root and include the LAST ~40 lines
  of its output verbatim in the \`checkOutput\` field of your structured result.
- Your final output is consumed by a script, not a human: return only the structured object.
${GUARD_RULES}`

function checkOutputField() {
  return {
    checkOutput: {
      type: 'string',
      description: 'Last ~40 lines of `just check` output, verbatim, run from the repo root before returning',
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
      description: 'kebab-case feature slug; the brief filename stem unless the brief names one',
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
      description: 'Layer names exactly as CLAUDE.md / docs/process/binding.md name them',
    },
    openQuestions: {
      type: 'array',
      items: { type: 'string' },
      description: 'Questions the brief does not answer; each must also be recorded in spec.md. Any entry stops the pipeline before Implement.',
    },
    failingForRightReason: {
      type: 'boolean',
      description: 'true only if every new test fails because the behaviour is missing, not because of import/syntax errors',
    },
    ...checkOutputField(),
  },
  required: ['slug', 'specPath', 'testFiles', 'layersTouched', 'openQuestions', 'failingForRightReason', 'checkOutput'],
}

const DISPUTES_FIELD = {
  disputes: {
    type: 'array',
    description: 'Gate disputes you raised instead of touching a guard path; empty if none',
    items: {
      type: 'object',
      properties: {
        path: { type: 'string', description: 'docs/disputes/NNNN-<slug>.md, repo-relative' },
        gate: { type: 'string', description: 'The gate and its just recipe, e.g. "complexity limits (just check)"' },
        guardPathsCovered: {
          type: 'array',
          items: { type: 'string' },
          description: 'Every guard path (repo-relative, as `just guard-diff` prints it) that this dispute record names in its Gate section; empty if the dispute names none',
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
  description: 'Every path `just guard-diff` lists for the branch (changed guard paths, repo-relative); empty if none',
}

const IMPLEMENT_SCHEMA = {
  type: 'object',
  properties: {
    filesChanged: { type: 'array', items: { type: 'string' }, description: 'Repo-relative paths created or modified' },
    allNewTestsPass: { type: 'boolean' },
    checkPasses: { type: 'boolean', description: 'true only if `just check` exited 0' },
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
    'findings', 'testsAdded', 'mutationSummary', 'fuzzSummary', 'fitnessSabotageConfirmed',
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
    disputeCoverage: {
      type: 'array',
      description: 'For each changed guard path, the dispute record under docs/disputes/ whose text names that path. Report only pairs you verified by reading the dispute file; a changed guard path with no such file is simply absent from this list.',
      items: {
        type: 'object',
        properties: {
          path: { type: 'string', description: 'A changed guard path, repo-relative, exactly as `just guard-diff` prints it' },
          disputeFile: { type: 'string', description: 'docs/disputes/NNNN-<slug>.md that names this path, repo-relative' },
        },
        required: ['path', 'disputeFile'],
      },
    },
    checkPasses: { type: 'boolean', description: 'true only if `just check` exited 0 when you ran it' },
    ...checkOutputField(),
  },
  required: ['verdict', 'issues', 'adrSuggested', 'guardChanges', 'disputeCoverage', 'checkPasses', 'checkOutput'],
}

// ---------------------------------------------------------------------------
// Prompts. Each role receives only the context it is allowed to see.
// ---------------------------------------------------------------------------
function specPrompt() {
  return `${PREAMBLE}
You are the SPEC agent for one feature of this repository.
${COMMON_RULES}
Inputs you may read:
- The feature brief: \`${BRIEF}\`
- CLAUDE.md, GLOSSARY.md, docs/process/binding.md, every ADR in docs/adr/ that the brief touches,
  and any design document CLAUDE.md tells you to read first
- The existing code and tests only as far as needed to place the feature and name test files

Your tasks, in order:
1. Derive the slug: kebab-case of the brief's filename stem (without extension) unless the brief
   explicitly names a slug. Write \`docs/features/<slug>/spec.md\` containing:
   - Problem (one paragraph, from the brief)
   - Acceptance criteria: numbered, each in Given / When / Then form, each testable
   - Glossary terms used (exact GLOSSARY.md names) and any new term you think the glossary needs
   - Layer placement: for each piece of the feature, which layer it lives in (using the layer
     names CLAUDE.md and binding.md define) and why
   - Test plan: the list of test files you will create, mapped to acceptance criteria
   - Open questions the brief does not answer (do not resolve them yourself; record them)
   - Out of scope (copied from the brief)
2. Write the failing tests with the test frameworks and directories binding.md names for each
   layer. Write a property test for every invariant you can state. Follow every CLAUDE.md
   non-negotiable in the tests themselves. Do not write implementation code.
3. Run the new tests and confirm each FAILS FOR THE RIGHT REASON: a missing function, class,
   endpoint or component, or an assertion on behaviour that does not exist yet. An ImportError
   on a module that should already exist, a syntax error, or a fixture error is the WRONG
   reason; fix the test. Set failingForRightReason accordingly.
4. Run \`just check\` and capture its tail. It is expected to be red only on your new tests.

Open questions stop the pipeline: if \`openQuestions\` is non-empty the orchestrator asks the
owner, edits the brief and re-runs. So record a question only when the brief truly does not
decide it; a question the ADRs or GLOSSARY.md answer is not open.

Return the structured result. Do not include your reasoning about the brief; the
implementer will see only spec.md and the test files.`
}

function implementPrompt(spec) {
  return `${PREAMBLE}
You are the IMPLEMENT agent for feature \`${spec.slug}\` of this repository.
${COMMON_RULES}
Inputs you may read:
- The spec: \`${spec.specPath}\`
- The failing tests: ${JSON.stringify(spec.testFiles)}
- Layers the spec expects you to touch: ${JSON.stringify(spec.layersTouched)}
- CLAUDE.md, GLOSSARY.md, docs/process/binding.md, docs/adr/, and the existing code those
  tests exercise
You are deliberately NOT given the feature brief or the spec author's reasoning. If spec.md
is ambiguous, pick the reading that is most consistent with GLOSSARY.md and the ADRs and
record the choice in \`notes\`.

Work test-first: run the listed tests, implement the smallest change that makes one pass,
re-run, repeat. Do not weaken, skip, delete or rewrite the spec's tests; if a test is wrong,
say so in \`notes\` and leave it failing rather than editing it to pass. Keep every layer rule
and every CLAUDE.md non-negotiable, add the contracts binding.md requires where the spec
states pre/postconditions, and regenerate any contract artefacts binding.md names when an
interface changes (with the recipe it names, never by hand).

Finish when every listed test passes AND \`just check\` exits 0. If a gate blocks you and you
believe the gate, not your code, is wrong, do not touch it: write a gate dispute and list it
in \`disputes\`. If you cannot get there, return anyway with \`checkPasses: false\` and explain
in \`notes\`.`
}

function fixPrompt(spec, findings, testsAdded, iteration) {
  const blockingFindings = findings.filter(f => f.severity !== 'minor')
  const minors = findings.filter(f => f.severity === 'minor')
  return `${PREAMBLE}
You are the FIX agent (iteration ${iteration} of ${MAX_FIX_ITERATIONS}) for feature \`${spec.slug}\` of this repository.
${COMMON_RULES}
A previous implementer built this feature and an adversarial critic found problems. You start
fresh: you have not seen the previous implementer's reasoning and must not look for it.

Inputs you may read:
- The spec: \`${spec.specPath}\`
- The spec's tests: ${JSON.stringify(spec.testFiles)}
- Tests the critic added (some currently fail on purpose): ${JSON.stringify(testsAdded)}
- CLAUDE.md, GLOSSARY.md, docs/process/binding.md, docs/adr/, and the code under change

Findings you MUST resolve (blocker/major):
${JSON.stringify(blockingFindings, null, 2)}

Findings you MAY resolve if cheap (minor):
${JSON.stringify(minors, null, 2)}

Resolve each blocking finding by changing the implementation, not by weakening, skipping or
deleting a test. If a critic test asserts something the spec contradicts, leave the test
failing and explain in \`notes\`. If a finding can only be resolved by changing a guard path, or
a gate blocks you and you believe the gate is wrong, write a gate dispute and list it in
\`disputes\` instead. Finish when the spec tests and the critic's tests pass and \`just check\`
exits 0; otherwise return with \`checkPasses: false\` and explain.`
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
Assume the implementation is wrong somewhere and go looking; "nothing found" needs evidence.
${COMMON_RULES}
Inputs you may read:
- The spec: \`${spec.specPath}\`
- The spec's tests: ${JSON.stringify(spec.testFiles)}
- Files the implementer changed: ${JSON.stringify(filesChanged)}
- CLAUDE.md, GLOSSARY.md, docs/process/binding.md, docs/adr/, and the fitness tests, mutation
  config and fuzz harness binding.md names
You are NOT given the implementer's notes or the feature brief.
${previous}
Do all of the following and report evidence for each:
1. Property tests: with the property-testing library binding.md names, add a property for
   every invariant the spec states or implies (round-trips, monotonicity, conservation,
   idempotence, ordering, anything a CLAUDE.md non-negotiable implies for the values touched).
   Add edge-case tests you EXPECT to fail: zero, negative and boundary values, empty
   collections, duplicates, unknown identifiers, mixed units or currencies where the domain
   has them, period and time-zone boundaries, very large numbers, unicode identifiers. Put
   them in the test directory binding.md names for the layer, next to the spec's tests,
   clearly named with the slug and the word critique.
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
   there of a layer rule or non-negotiable from CLAUDE.md, run the fitness tests in that copy
   and confirm they FAIL. Then delete the copy. Set fitnessSabotageConfirmed only if they
   failed, and describe it in \`fitnessSabotageSummary\`. Never sabotage the real working tree.
5. Non-negotiables and vocabulary: check every CLAUDE.md non-negotiable holds in the change,
   GLOSSARY.md's banned words are absent from new identifiers, and any generated contract
   artefact binding.md names matches the code that it describes.
6. Guard paths: run \`just guard-diff\` and put every path it lists in \`guardFilesChanged\`.
   Each one is a blocker finding (title "guard path changed: <path>") unless a gate dispute
   under docs/disputes/ in the change names that path, and even then a major one: the
   pipeline never changes a gate, only the owner does. Your scratch copy for step 4 is outside
   the repository, so sabotage there is not a guard change.

Keep every test you added that PASSES (regression value) and every test that FAILS because of
a genuine defect (list it as \`suggestedTest\` on the finding). Delete tests that fail because
the test itself was wrong. Do not edit non-test source files.

Severity: blocker = wrong result, data loss, a violated CLAUDE.md non-negotiable, a layer or
contract violation; major = unhandled edge case the spec covers, survived mutant on a stated
invariant, missing contract; minor = everything else. \`just check\` is expected to be red
only on the failing tests you kept; say so in the finding evidence.`
}

function reviewPrompt(spec) {
  return `${PREAMBLE}
You are the independent REVIEWER for feature \`${spec.slug}\` of this repository.
${COMMON_RULES}
Inputs you may read, and nothing else:
- The spec: \`${spec.specPath}\`
- The uncommitted diff: the change is uncommitted on branch \`${BRANCH}\`. Enumerate it with
  \`git diff ${BASE} --stat\`, \`git diff ${BASE}\` and \`git status --porcelain\` (untracked files are
  new files: read them). If the branch has commits, \`git diff ${BASE}...${BRANCH}\` is equivalent.
- CLAUDE.md, GLOSSARY.md, docs/adr/, docs/process/binding.md, docs/disputes/
- Any generated contract artefacts that binding.md names
You are deliberately NOT given the implementer's notes, the critic's findings or the feature
brief. Judge the code against the spec and the standards only.

Review on two axes and record every issue with axis, severity, location and evidence:
A. Spec conformance: does each Given/When/Then acceptance criterion have a test and an
   implementation that satisfies it? Any criterion silently narrowed? Any test weakened,
   skipped or deleted? Any behaviour added that the spec does not ask for?
B. Standards conformance:
   - Glossary vocabulary in identifiers, docs and API names; banned words absent
   - Layer rules as CLAUDE.md and binding.md define them: dependency direction, purity of the
     layers they call pure, no forbidden imports
   - Every CLAUDE.md non-negotiable, checked one by one against the diff
   - Contracts: the pre/postcondition mechanism binding.md names, where the spec states them;
     generated contract artefacts match the code they describe
   - Type strictness, lint, test placement and coverage as the gates in binding.md require
   - Does this change make a decision with lasting consequences (new storage, new external
     dependency, new layer, a changed rule that versions over time)? If so set adrSuggested.
C. Guard paths: run \`just guard-diff\` and list every path it prints in \`guardChanges\`. Then
   read every file under docs/disputes/ that the diff adds or changes. For each changed guard
   path, if some dispute record's text names that exact path, add \`{ path, disputeFile }\` to
   \`disputeCoverage\`; do not infer coverage from a dispute that names only the gate. Every
   changed guard path is a blocker issue on the standards axis when uncovered and a major one
   when covered: the owner, not the pipeline, changes a gate.

Verdict: \`approve\` only if there are no blocker or major issues on any axis, every changed
guard path is covered by a dispute record, and \`just check\` exits 0; otherwise
\`request_changes\`. The script re-derives the verdict from your \`issues\`, \`guardChanges\` and
\`disputeCoverage\`, so make them complete.`
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

function must(result, label) {
  if (!result) throw new Error(`${label} agent returned no result (skipped or died); resume the run to retry`)
  return result
}

// ---------------------------------------------------------------------------
// Phase 1: Spec
// ---------------------------------------------------------------------------
phase('Spec')
log(`Spec: ${BRIEF} on branch ${BRANCH}`)
const spec = must(await agent(specPrompt(), { label: 'spec', phase: 'Spec', schema: SPEC_SCHEMA }), 'spec')
log(`Spec ${spec.slug}: ${spec.testFiles.length} test file(s), layers ${spec.layersTouched.join(', ')}` +
  (spec.failingForRightReason ? '' : ' -- WARNING: tests not failing for the right reason') +
  (spec.openQuestions.length ? `, ${spec.openQuestions.length} open question(s)` : ''))

// An open question is the owner's to answer, not the implementer's to guess. Stop here; the
// orchestrator asks, edits the brief and re-runs.
if (spec.openQuestions.length > 0) {
  log(`Blocked before Implement: ${spec.openQuestions.length} open question(s) in the brief`)
  return {
    slug: spec.slug,
    verdict: 'blocked',
    openQuestions: spec.openQuestions,
    specPath: spec.specPath,
    testFiles: spec.testFiles,
    branch: BRANCH,
    checkOutput: spec.checkOutput,
    requiredByOrchestrator: [
      'Put each open question to the owner and record the answers in the brief',
      'Re-run this workflow with the same args after editing the brief; do not open a PR from a blocked run',
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
log(`Implement: ${impl.filesChanged.length} file(s), tests pass=${impl.allNewTestsPass}, just check=${impl.checkPasses}`)

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
const undisputedGuardChanges = guardChanges.filter(p => !covered.has(p))
const blockingReviewIssues = blocking(review.issues)

const forcedReasons = []
if (undisputedGuardChanges.length) forcedReasons.push(`guard paths changed without a dispute record naming them: ${undisputedGuardChanges.join(', ')}`)
if (unresolved.length) forcedReasons.push(`${unresolved.length} unresolved blocker/major critic finding(s)`)
if (blockingReviewIssues.length) forcedReasons.push(`${blockingReviewIssues.length} blocker/major review issue(s)`)
if (!lastCheckPasses) forcedReasons.push('the last implement/fix result reported checkPasses: false')
if (!spec.failingForRightReason) forcedReasons.push('the spec reported failingForRightReason: false')
const verdict = forcedReasons.length ? 'request_changes' : review.verdict
if (verdict !== review.verdict) log(`Verdict forced to request_changes: ${forcedReasons.join('; ')}`)
else if (forcedReasons.length) log(`Reviewer and script agree on request_changes: ${forcedReasons.join('; ')}`)

// ---------------------------------------------------------------------------
// Result. The orchestrator commits / opens the PR; this workflow never does.
// ---------------------------------------------------------------------------
return {
  slug: spec.slug,
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
  undisputedGuardChanges,
  specPath: spec.specPath,
  testFiles: spec.testFiles,
  openQuestions: spec.openQuestions,
  fitnessSabotageConfirmed: critique.fitnessSabotageConfirmed,
  fitnessSabotageSummary: critique.fitnessSabotageSummary,
  checkPasses: lastCheckPasses && review.checkPasses === true,
  checkOutput: lastCheckOutput,
  branch: BRANCH,
  requiredByOrchestrator: [
    'verdict is "approve" (the script forces request_changes when its evidence says otherwise)',
    'unresolvedBlockingFindings is empty and reviewIssues has no blocker or major entry',
    'checkOutput tail shows just check green and checkPasses is true',
    'undisputedGuardChanges is empty; every entry in disputes has been relayed to the owner, who decides',
    'adrSuggested, if set, is written in docs/adr/ in the same PR',
  ],
}
