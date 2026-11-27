export const meta = {
  name: 'pipeline',
  description: 'Build one feature from an agent-ready ticket with the Ratchet method: preflight -> spec -> ticket check (one amend of the spec when it finds a gap) -> implement -> adversarial critique -> fix loop (up to two rounds when the project is depended-on, one pass on a review blocker before that) -> independent review',
  whenToUse: 'Run by the /ratchet:feature skill with args { ref, branch, base?, root?, preflight?, namedGates?, ticketFindings? } after its own preflight is green; not by hand. ticketFindings are the last run\'s, when its review found the build lost a promise of the ticket. With args.rejudge = { slug, specPath, conflicts?, checkRed?, dependencies?, namedGates?, assumptions?, settled?, parent?, deployed?, flag? } it judges again a branch that was brought up to date behind another merge: Reconcile when the rebase conflicted or check went red, then Review. Never commits, pushes or opens a PR; the orchestrator does that after reading the result.',
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

const AGENTS_AT_MOST = {"young": 9, "dependedOn": 12, "rejudge": 2}

const REVIEW_ROUNDS = []
const FINDINGS_SO_FAR = { criticFindingsByRound: [], unresolvedBlockingFindings: [], reviewIssues: [], reviewIssuesByRound: REVIEW_ROUNDS }
const PLUGIN_PY = 'python3 -E -s -B -X pycache_prefix=/nonexistent/ratchet-no-bytecode'
const ARGS = typeof args === 'string' ? { ref: args } : (args && typeof args === 'object' ? args : {})
const REF = typeof ARGS.ref === 'string' ? ARGS.ref.trim() : ''
if (!REF) {
  return {
    verdict: 'blocked',
    ...FINDINGS_SO_FAR,
    preflight: { ok: false, failures: [{ check: 'ticket', detail: 'no ticket ref was passed', produce: 'run the /ratchet:feature skill with the ticket number or path; it runs the preflight and passes args = { ref, branch, base }' }] },
    requiredByOrchestrator: ['Run `/ratchet:feature <ticket>`; the slash form of this workflow passes no args and spawns nothing'],
  }
}
let BASE = typeof ARGS.base === 'string' && ARGS.base ? ARGS.base : ''
const ROOT = typeof ARGS.root === 'string' && ARGS.root ? ARGS.root : ''
const PREFLIGHT_SCRIPT = typeof ARGS.preflight === 'string' && ARGS.preflight ? ARGS.preflight : ''
const MAX_FIX_ITERATIONS = 2
const YOUNG_FIX_ROUNDS = 1
const REJUDGE = ARGS.rejudge && typeof ARGS.rejudge === 'object' ? ARGS.rejudge : null
const SETTLED = REJUDGE && REJUDGE.settled && typeof REJUDGE.settled === 'object' ? REJUDGE.settled : null
const TICKET_FINDINGS = Array.isArray(ARGS.ticketFindings) ? ARGS.ticketFindings.filter(Boolean) : []
let BRANCH = typeof ARGS.branch === 'string' && ARGS.branch ? ARGS.branch : ''
let SLUG = ''
let CRITIC = true
let CRITIC_REASON = ''
let NAMED_GATES = namedGatesOf(REJUDGE ? REJUDGE.namedGates : ARGS.namedGates)
const CONFLICTS = REJUDGE ? (REJUDGE.conflicts || []).filter(Boolean).map(String) : []
let PARENT = REJUDGE && typeof REJUDGE.parent === 'string' ? REJUDGE.parent.trim() : ''
let DEPLOYED = !REJUDGE || REJUDGE.deployed !== false
let FLAG = flagOf(REJUDGE && REJUDGE.flag, DEPLOYED)
const DEFAULT_COMPANIONS = {
  tdd: 'mattpocock-skills:tdd',
  codebase_design: 'mattpocock-skills:codebase-design',
  code_review: 'mattpocock-skills:code-review',
  ui_design: 'impeccable:impeccable',
}
let COMPANIONS = companionsOf(REJUDGE && REJUDGE.companions ? REJUDGE.companions : ARGS.companions)

function companionsOf(raw) {
  const out = { ...DEFAULT_COMPANIONS }
  if (!raw || typeof raw !== 'object') return out
  for (const key of Object.keys(DEFAULT_COMPANIONS)) {
    if (typeof raw[key] === 'string' && raw[key].trim()) out[key] = raw[key].trim()
  }
  return out
}

function flagOf(flag, deployed) {
  const key = flag && typeof flag.key === 'string' ? flag.key : ''
  if (flag && flag.form === 'hidden' && /^[^`\s]{1,80}$/.test(key)) return { form: 'hidden', key }
  if (flag && flag.form === 'appears' && !deployed) return { form: 'appears', key: null }
  return { form: 'unasked', key: null }
}

const AN_OPTION = [/^-/, 'starts with "-", so the command it goes into would read it as an option']
const A_CONTROL = [/[\x00-\x1f\x7f]/, 'holds a control character']
const PATH_RULES = [AN_OPTION, A_CONTROL]
const REF_NAME_RULES = [
  AN_OPTION,
  [/[\x00-\x20\x7f]/, 'holds a space or a control character'],
  [/[~^:?*[\\]/, 'holds one of ~ ^ : ? * [ \\'],
  [/\.\./, 'holds ".."'],
  [/@\{/, 'holds "@{"'],
  [/^@$/, 'is "@" alone'],
  [/^\/|\/$|\/\//, 'starts or ends with "/" or holds "//"'],
  [/(^|\/)\./, 'has a part that starts with "."'],
  [/\.lock(\/|$)/, 'has a part that ends with ".lock"'],
  [/\.$/, 'ends with "."'],
]
const BRANCH_RULES = [
  ...REF_NAME_RULES,
  [/^HEAD$/, 'is "HEAD", which git refuses as a branch name (git check-ref-format --branch), so a role on a detached checkout would not switch'],
]
const ROOT_RULES = [[/^(?!\/)/, 'is not an absolute path, so `cd` could find it through CDPATH'], A_CONTROL]
const REF_NAME_PRODUCE = 'a name git accepts as a ref name (git check-ref-format) that does not start with "-": a branch, a remote-tracking branch or an object id'
const BRANCH_PRODUCE = 'a name git accepts as a branch name (git check-ref-format --branch), with no "@{" and not "@" alone'
const PATH_PRODUCE = 'a value that does not start with "-" (a relative path can start with ./) and holds no control character'
const ROOT_PRODUCE = 'the absolute path of the work tree, with no control character'

function unfitArgument() {
  const slots = [
    ['ticket', REF, PATH_RULES, PATH_PRODUCE],
    ['root', ROOT, ROOT_RULES, ROOT_PRODUCE],
    ['preflight', PREFLIGHT_SCRIPT, PATH_RULES, PATH_PRODUCE],
    ['base', BASE, REF_NAME_RULES, REF_NAME_PRODUCE],
    ['branch', BRANCH, BRANCH_RULES, BRANCH_PRODUCE],
    ['parent', PARENT, PATH_RULES, PATH_PRODUCE],
    ...CONFLICTS.map(path => ['conflict', path, [A_CONTROL], 'a conflicted path with no control character']),
  ]
  for (const [slot, value, rules, produce] of slots) {
    const broken = value ? rules.find(([pattern]) => pattern.test(value)) : null
    if (!broken) continue
    const detail = `the ${slot} ${JSON.stringify(value)} ${broken[1]}`
    return {
      verdict: 'blocked',
      ...FINDINGS_SO_FAR,
      preflight: { ok: false, failures: [{ check: slot, detail, produce }] },
      requiredByOrchestrator: [`Pass ${produce}; the pipeline places the ${slot} into commands its roles run, and refused ${detail}`],
    }
  }
  return null
}

const unfitArgs = unfitArgument()
if (unfitArgs) return unfitArgs

function namedGatesOf(list) {
  const seen = new Map()
  for (const entry of Array.isArray(list) ? list : []) {
    const gate = String(typeof entry === 'string' ? entry : (entry && entry.gate) || '').replace(/`/g, '').trim()
    if (!gate) continue
    const tier = entry && typeof entry === 'object' && entry.tier !== undefined && entry.tier !== '' ? String(entry.tier) : ''
    if (!seen.has(gate) || (tier && !seen.get(gate).tier)) seen.set(gate, tier ? { gate, tier } : { gate })
  }
  return [...seen.values()]
}

function namedGatesList() {
  if (!NAMED_GATES.length) return '- No criterion of the ticket or of spec.md names a gate.'
  const names = NAMED_GATES.map(g => `\`${g.gate}\`${g.tier ? ` (tier ${g.tier})` : ''}`).join(', ')
  return `- The gates the criteria name, as code spans: ${names}. A gate of tier 2 or later, or any other gate \`check\` does not run, is the one the rule below sends to a role.`
}

const ROOT_HINT = 'the repository root (the directory holding AGENTS.md)'

function enterRoot(indent) {
  return ROOT ? ` This line enters it:\n\n${indent}cd ${shellWord(ROOT)}\n` : ''
}

const PREAMBLE = 'Load the `ratchet:method` skill (the method this repository follows), then read AGENTS.md, docs/process/binding.md and GLOSSARY.md (or GLOSSARY-MAP.md). Obey them. The binding\'s Entry points table gives the command behind every entry point named below (`check`, `guard-diff`); "run `check`" means run that command from the repository root.'

const SEVERITY = `Severity, the one list every grade you apply comes from. A finding that carries no
axis takes every line. Where a finding carries an axis, the axis a line names is the axis of
the issue.
- Blocker: wrong result (a wrong output, or a wrong input accepted with exit 0), data loss, a
  violated AGENTS.md non-negotiable, a declared-path or contract violation, a crash the fuzz
  harness finds, a span attribute or log field carrying a secret or a personal identifier, a
  recorded output added-by-recording or altered with no justifying criterion in spec.md.
- Major: unhandled edge case the spec covers, survived mutant on a stated invariant, missing
  contract, behaviour shipped on with no flag where spec.md does not say "none" with a reason.
- Minor: everything else.
- Guard paths: a changed guard path that no gate dispute in the change names is a blocker on
  the standards axis. A path a dispute names exactly is no finding: the owner, not the
  pipeline, changes a gate, and the merge judge leaves that merge to the owner. A dependency
  the spec decided, as the guard rules above define it, is no finding; an added package
  spec.md does not name is a major issue on the spec axis (a decision the spec did not make). A
  record the change only adds to (an \`additions:\` note) is no finding for the path; a new term
  that restates a concept the glossary already names, or a new ADR that contradicts one in
  docs/adr/ without the owner's superseding record, is a major issue on the standards axis.
- Spec axis: a criterion of the ticket the change does not meet is major, as a promise of the
  ticket it drops is.
- Design axis: A layer violation is a blocker, and so is a module that reaches around a seam
  that is a layer boundary; around any other seam it is major. A dependency direction the
  architecture ADR rules out, and an architecture decision made without the owner, are major.
  A shallow wrapper, logic that fits its layer but sits in a worse module, and a placement the
  Design section does not explain are minor, unless the spec's Design section says otherwise
  and the code does something else: that is major.
A defect the spec defers to a later ticket is still graded by what it does to a user now.`

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

const WORKING_RULE = `- While you work, run the \`tier0\` entry point and \`tests-changed all\`. Never run a suite whole
  to see whether your own tests pass.`
const CHECK_RULE = `${WORKING_RULE}
- Run \`check\` from the repository root last, before you return, and again only after an edit: do
  not run it again on a tree you have not changed since a green run. Include the LAST ~40 lines of
  its output verbatim in the \`checkOutput\` field of your structured result.`
const OWN_TESTS_RULE = `${WORKING_RULE}
- Your tree is red on purpose, so do not run \`check\`. Before returning, run the \`tier0\` entry
  point from the repository root, then only the tests you wrote, with \`tests-changed all\`.
  Include the LAST ~40 lines of that output verbatim in the \`checkOutput\` field of your
  structured result. The implementer, the fixer and the reviewer run \`check\` last; the reviewer's
  run on the final tree is the one that counts.`
const NAMED_GATE_RULES = {
  implement: `- A gate outside \`check\` that a criterion names is run whole, once, by you, after your
  \`check\` is green. Return the ones you ran in \`namedGatesRun\`, as code spans.`,
  review: `- A gate outside \`check\` that a criterion names is run whole, once, by you, on the tree you
  judge. Return the ones you ran in \`namedGatesRun\`, as code spans.`,
}
const NO_NAMED_GATE_RULE = `- You run no gate a criterion names that \`check\` does not: the implementer runs each once and
  each review once.`

function namedGatesRules(role) {
  return `${namedGatesList()}
${NAMED_GATE_RULES[role] || NO_NAMED_GATE_RULE}`
}

const FORK_TEST = `An undecided point is the owner's in two cases, and yours in every other.

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
  the owner relaxes it in a later change.`

const UNSTATED_RULE = `- A case you saw or measured where no record says which behaviour is right (the ticket, spec.md,
  AGENTS.md, the binding, a decision record, the glossary, the design brief, the guard manifest) is
  an \`unstated\` entry. Never leave it out because you judged it unlikely, and never settle it
  silently in the code or in \`notes\`. Apply the fork test to it: \`whose\` is \`owner\` when the test
  gives the owner the choice and \`role\` when it gives it to you. Each entry carries the case as the
  owner would read it (\`question\`), what the code does now (\`now\`), the \`evidence\`, its \`kind\`, two
  to four \`options\` with the answer you would give first, one sentence on why (\`recommendation\`),
  and what would show that answer wrong (\`refuter\`).`
const DEPARTURE_RULE = `- A criterion or an assumption of spec.md that your change no longer holds is a \`departures\`
  entry, never a line in \`notes\`: \`from\` says which, \`quote\` is its words in spec.md, \`now\` is what
  the code does instead and \`why\` says why. A departure from a criterion stops the run and goes to
  the owner. A departure from an assumption goes through the fork test: \`whose\` is \`role\` when the
  test gives you the choice, and the entry is then the amended assumption, your answer first in
  \`options\`, which the later roles judge against. Give each the \`question\`, \`kind\`, \`options\`,
  \`recommendation\` and \`refuter\` an \`unstated\` entry has.`
const NEIGHBOURING_RULE = `A neighbouring test is one that drives a screen, command, endpoint or function the change edits, by
name or by its address. For each set of values such a test repeats the same checks over (a list of
sizes, locales, roles, screen shapes, classes of input), write one line under
\`## Neighbouring cases\`: the test, the values, and which of them the new behaviour holds for. The
default is every value the neighbouring test uses. A narrower set is an assumption, with its reason.
It is a question only when the fork test gives it to the owner.`

function shellWord(value) {
  return `'${value.replace(/'/g, `'\\''`)}'`
}

function quoteRule(spec, list, ticketFile) {
  const script = PREFLIGHT_SCRIPT ? shellWord(PREFLIGHT_SCRIPT.replace(/[^/]*$/, 'quotes.py')) : '<quotes.py>'
  const named = ['<that file> its path']
  if (ticketFile) named.push('<ticket file> a file holding the ticket text')
  if (!PREFLIGHT_SCRIPT) named.push(`<quotes.py> the path of the ratchet plugin's \`scripts/quotes.py\` as one
  shell word (beside \`feature_preflight.py\`; \`find\` it, do not write one)`)
  const last = named.pop()
  const placeholders = named.length ? `${named.join(',\n  ')}\n  and ${last}` : last
  const record = ticketFile ? ' --record ticket=<ticket file>' : ''
  return `- Every blocker or major in \`${list}\` carries \`breaks\`: \`record\` names the record it breaks and
  \`quote\` is the promise, criterion, non-negotiable or rule it breaks. The records are the ticket,
  spec.md (name it by its path, \`${spec.specPath}\`), AGENTS.md, \`docs/process/binding.md\`, a decision
  record (its path under \`docs/adr/\`), GLOSSARY.md, DESIGN.md and guards/manifest.toml. Copy the unit
  exactly as the record writes it, markup included and its list marker left off: a whole sentence,
  list item, heading, table cell or line. A blocker or major with no quote is not a finding: return it
  as an \`unstated\` entry.
- Before you return, write \`${list}\` as the JSON list you return, in that order, to a file in a
  \`mktemp -d\` directory outside the repository. Run this, with ${placeholders}:

      ${PLUGIN_PY} ${script} <that file> --root .${record}

  Return what it printed, verbatim, as \`quoteOutput\`. Correct every quote it reports not found, and
  run it again, before you return.`
}

let ROLE_ASSUMPTIONS = []
let PENDING_MOVED = []
const ALL_UNSTATED = []
const ALL_DEPARTURES = []

function roleAssumptionsNote() {
  if (!ROLE_ASSUMPTIONS.length) return ''
  return `
Roles of this run decided these cases under the fork test, the amended assumptions among them. Judge
the change against them as against spec.md's own assumptions, and not against what they replaced:
${JSON.stringify(ROLE_ASSUMPTIONS, null, 2)}
`
}

function movedNote() {
  if (!PENDING_MOVED.length) return ''
  return `
The quote check did not find the quote of these critic findings, so the script moved each to
\`unstated\`. Apply the fork test to each, and return one \`unstated\` entry for it with \`of\` set to its \`id\`:
${JSON.stringify(PENDING_MOVED, null, 2)}
`
}

function commonRules(role, rebasing) {
  const uncommitted = rebasing
    ? `- The branch holds the change as commits. Every edit you make stays uncommitted on the
  working tree; the orchestrator commits it. The git commands you run are read-only, plus the
  \`git switch\` above and the three that carry out the rebase below: \`git rebase\` onto
  \`${BASE}\`, as its line below runs it, \`git add <a file you resolved>\` and
  \`git rebase --continue\`.`
    : `- Every change you make stays uncommitted on the working tree; the orchestrator commits and
  publishes. The git commands you run are read-only, plus the \`git switch\` above.`
  return `
Ground rules for every agent in this pipeline:
- Work in ${ROOT_HINT} on branch \`${BRANCH}\`.${enterRoot('      ')}
  First run \`git rev-parse --abbrev-ref HEAD\`; if it does not print \`${BRANCH}\`, run

      git switch --end-of-options ${shellWord(BRANCH)}

  The orchestrator creates the branch, never you: if it does not exist, stop and return an
  error saying so.
${uncommitted}
- Vocabulary comes from the glossary: use its terms in code, tests and docs; its Avoid lists
  are banned identifiers.
- The project's non-negotiables are whatever AGENTS.md lists; its declared paths and their
  dependency direction are whatever AGENTS.md and docs/process/binding.md define. Each is a
  hard rule.
${role === 'spec' || role === 'critic' ? OWN_TESTS_RULE : CHECK_RULE}
${namedGatesRules(role)}
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

const CHANGE_SIZE_FIELD = {
  changeSize: {
    type: 'object',
    description: 'What the binding\'s change-size entry point prints for the working tree as you leave it: the product lines and the ceiling beside them, the test lines and theirs; omit when the binding binds no such gate',
    properties: {
      lines: { type: 'integer', description: 'The product lines changed' },
      ceiling: { type: 'integer', description: 'The product lines ceiling' },
      testLines: { type: 'integer', description: 'The test lines changed' },
      testCeiling: { type: 'integer', description: 'The test lines ceiling' },
    },
    required: ['lines', 'ceiling'],
  },
}
const CHANGE_SIZE_RULE = 'Run the binding\'s change-size entry point last and return what it prints as `changeSize`: `lines` and `ceiling` are its product lines and the ceiling beside them, `testLines` and `testCeiling` its test lines and theirs (leave the last two out when it prints no test ceiling); omit `changeSize` when the binding binds no such gate.'
const TRIGGERS_RULE = 'If `check` is red on the `triggers` gate (a line `triggers: \'<kind>\' gained N instance(s)`: a second instance of a kind whose safeguards are owed), do not edit the binding, the justfile or quality/ to quiet it and do not write a dispute. Finish the rest, then return with `checkPasses: false`, keep that gate line verbatim in `checkOutput`, and name the kind and `/ratchet:safeguard` in `notes`.'
const OWED_RED = /triggers: '[^']+' gained \d+ instance/
const ROOM_NUMERATOR = 2
const ROOM_DENOMINATOR = 3

function testRoomShare(critic) {
  return critic ? [ROOM_NUMERATOR, ROOM_DENOMINATOR] : [1, 1]
}

const ESTIMATE_FIELD = {
  changeEstimate: {
    type: 'object',
    description: 'What the whole change will weigh against the change-size ceilings, product and tests apart, estimated before you return; omit when the binding binds no change-size gate',
    properties: {
      implementationLines: { type: 'integer', description: 'Product lines the implementation will add or change, by the seams the tests exercise' },
      criticLines: { type: 'integer', description: 'Test lines the critic\'s tests will add: about two fifths of the lines of your own tests, more for a user interface or an endpoint' },
      recordLines: { type: 'integer', description: 'Lines of ADRs and glossary entries the change is likely to add, which count as product; 0 when none' },
      proposedSplit: {
        type: 'array',
        description: 'When the product lines (implementation and records, plus those already measured) would pass the product ceiling, or your tests plus the critic\'s the test ceiling: two or more tickets that each fit, with the acceptance criteria each carries; empty when the change fits',
        items: {
          type: 'object',
          properties: {
            title: { type: 'string' },
            criteria: { type: 'array', items: { type: 'string' } },
            promises: { type: 'array', items: { type: 'string' }, description: 'The ticket\'s own acceptance criteria this part carries, each a `- [ ]` line of the ticket copied verbatim without its checkbox; together the parts carry every such line of the ticket exactly once, and none is reworded' },
          },
          required: ['title', 'criteria', 'promises'],
        },
      },
    },
    required: ['implementationLines', 'criticLines', 'recordLines', 'proposedSplit'],
  },
}

const SPEC_SCHEMA = {
  type: 'object',
  properties: {
    namedGates: {
      type: 'array',
      items: { type: 'string' },
      description: 'The gates of the binding\'s Gates table that spec.md\'s criteria name as a code span and the list you were given does not hold, each as the code span of its name; empty when the criteria add none',
    },
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
  required: ['slug', 'specPath', 'namedGates', 'testFiles', 'layersTouched', 'openQuestions', 'assumptions', 'dependencies', 'guardEdits', 'failingForRightReason', 'failures', 'checkOutput'],
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

const SAID = { type: 'string', pattern: '\\S' }
const BREAKS_FIELD = {
  breaks: {
    type: 'object',
    description: 'The record the finding breaks and the promise, criterion, non-negotiable or rule it breaks, quoted: one whole sentence, list item, heading, table cell or line, copied as the record writes it. Required of a blocker or major; a minor may omit it',
    properties: {
      record: { type: 'string', description: 'The record\'s name: `ticket`, the spec\'s path, `AGENTS.md`, `binding`, `GLOSSARY.md`, `DESIGN.md`, `guards/manifest.toml`, or a path under `docs/adr/`' },
      quote: { type: 'string', description: 'The unit, as the record writes it' },
    },
    required: ['record', 'quote'],
  },
}

const QUOTE_OUTPUT_FIELD = {
  quoteOutput: {
    type: 'string',
    description: 'What the plugin\'s quote check printed over your findings, verbatim, run before returning',
  },
}

const CASE_ENTRY = {
  question: { ...SAID, description: 'The case as the owner would read it, in the words of a user of the product, with no code in it' },
  kind: { type: 'string', enum: ['direction', 'irreversible'], description: 'As the fork test defines the two' },
  options: { type: 'array', minItems: 2, items: SAID, description: 'Two to four answers, the one you would give first' },
  recommendation: { ...SAID, description: 'One sentence: why the first option, from the records' },
  refuter: { ...SAID, description: 'What would show the first option wrong, and what changing it later would cost' },
  whose: { type: 'string', enum: ['owner', 'role'], description: 'The fork test\'s answer: owner when it gives the owner the choice, role when it gives it to you' },
}
const CASE_NAMES = Object.keys(CASE_ENTRY)

function unstatedField(classifies) {
  return {
    unstated: {
      type: 'array',
      description: classifies
        ? 'Every case you saw or measured where no record says which behaviour is right, each put through the fork test; and one entry for each moved critic finding the prompt lists, its `of` set to that finding\'s id, so that none is left unclassified; empty when none'
        : 'Every case you saw or measured where no record says which behaviour is right, each put through the fork test; empty when none',
      items: {
        type: 'object',
        properties: {
          ...(classifies ? { of: { type: ['string', 'null'], description: 'The id of the moved critic finding this entry answers; null for an entry of your own' } } : {}),
          now: { ...SAID, description: 'What the code does now' },
          evidence: { ...SAID, description: 'The command run and the output that shows it' },
          ...CASE_ENTRY,
        },
        required: [...(classifies ? ['of'] : []), 'now', 'evidence', ...CASE_NAMES],
      },
    },
  }
}

const DEPARTURES_FIELD = {
  departures: {
    type: 'array',
    description: 'Each criterion or assumption of spec.md the change no longer holds; empty when none',
    items: {
      type: 'object',
      properties: {
        from: { type: 'string', enum: ['criterion', 'assumption'] },
        quote: { ...SAID, description: 'The criterion or assumption in the words of spec.md' },
        now: { ...SAID, description: 'What the code does instead' },
        why: { ...SAID, description: 'Why it does' },
        ...CASE_ENTRY,
      },
      required: ['from', 'quote', 'now', 'why', ...CASE_NAMES],
    },
  },
}

const RECORDED_OUTPUTS_FIELD = {
  recordedOutputs: {
    type: 'array',
    items: { type: 'string' },
    description: 'Repo-relative paths of recorded outputs you recorded or updated with the recorded-output tool because the expected text was too large to state by hand; empty if none',
  },
}

const IMPLEMENT_SCHEMA = {
  type: 'object',
  properties: {
    filesChanged: { type: 'array', items: { type: 'string' }, description: 'Repo-relative paths created or modified' },
    allNewTestsPass: { type: 'boolean' },
    checkPasses: { type: 'boolean', description: 'true only if `check` exited 0' },
    ...RECORDED_OUTPUTS_FIELD,
    ...CHANGE_SIZE_FIELD,
    notes: { type: 'string', description: 'Design decisions and trade-offs' },
    ...DISPUTES_FIELD,
    ...unstatedField(false),
    ...DEPARTURES_FIELD,
    ...checkOutputField(),
  },
  required: ['filesChanged', 'allNewTestsPass', 'checkPasses', 'recordedOutputs', 'notes', 'disputes', 'unstated', 'departures', 'checkOutput'],
}

const NAMED_GATES_RUN_FIELD = {
  namedGatesRun: {
    type: 'array',
    items: { type: 'string' },
    description: 'The gates a criterion names that you ran whole, each as the code span of its name; empty when none was named or none lies outside `check`',
  },
}

const BUILD_SCHEMA = {
  ...IMPLEMENT_SCHEMA,
  properties: { ...IMPLEMENT_SCHEMA.properties, ...NAMED_GATES_RUN_FIELD },
  required: [...IMPLEMENT_SCHEMA.required, 'namedGatesRun'],
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
          ...BREAKS_FIELD,
          carries: { type: ['string', 'null'], description: 'On a re-critique, the `id` of the earlier finding this one is still open from; null for a finding that is new, and always null on the first critique' },
          evidence: { type: 'string', description: 'Command run and the output that demonstrates the problem' },
          suggestedTest: { type: 'string', description: 'Path of a test you added that exposes it, or a sketch of one' },
          testFails: { type: 'boolean', description: 'true when you kept a test for this finding that fails now, so `check` is red on it' },
          heldByKeptTest: { type: 'boolean', description: 'true when you kept a test for this finding that PASSES because the code already holds; false when no test was kept. Never true with testFails' },
        },
        required: ['severity', 'title', 'carries', 'evidence', 'suggestedTest', 'testFails', 'heldByKeptTest'],
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
    ...unstatedField(false),
    ...QUOTE_OUTPUT_FIELD,
    ...checkOutputField(true),
  },
  required: [
    'findings', 'testsAdded', 'attempted', 'mutationSummary', 'fuzzSummary', 'fitnessSabotageConfirmed',
    'fitnessSabotageSummary', 'guardFilesChanged', 'unstated', 'quoteOutput', 'checkOutput',
  ],
}

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
const RUN_ASSUMED = 'Assumed by the run, as no criterion decides it: '
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

const REVIEW_INPUTS_FIELD = {
  ...ACCEPTED_INPUTS_FIELD,
  items: {
    ...ACCEPTED_INPUTS_FIELD.items,
    properties: { ...ACCEPTED_INPUTS_FIELD.items.properties, named: { ...BREAKS_FIELD.breaks, description: 'The record that names this input, the ticket or spec.md, and the sentence that names it, quoted as for `breaks`. The rule an undecided case breaks is the method\'s own, that every case of an input a record names is decided, and not a promise of the project. `named` is the proof that the input is the record\'s, so the quote check refuses an input that was invented' } },
    required: [...ACCEPTED_INPUTS_FIELD.items.required, 'named'],
  },
}

const REVIEW_SCHEMA = {
  type: 'object',
  properties: {
    verdict: { type: 'string', enum: ['approve', 'request_changes'] },
    ticketPromises: TICKET_PROMISES_FIELD,
    acceptedInputs: REVIEW_INPUTS_FIELD,
    issues: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          axis: { type: 'string', enum: ['spec', 'standards', 'design'] },
          severity: { type: 'string', enum: ['blocker', 'major', 'minor'] },
          title: { type: 'string' },
          ...BREAKS_FIELD,
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
    ...NAMED_GATES_RUN_FIELD,
    ...unstatedField(true),
    ...QUOTE_OUTPUT_FIELD,
    ...checkOutputField(),
  },
  required: ['verdict', 'ticketPromises', 'acceptedInputs', 'issues', 'adrSuggested', 'guardChanges', 'dependencyNotes', 'recordNotes', 'disputeCoverage', 'checkPasses', 'namedGatesRun', 'unstated', 'quoteOutput', 'checkOutput'],
}

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
    parent: { type: ['string', 'null'], description: 'the "parent" field: the record the ticket inherits from, or null' },
    deployed: { type: 'boolean', description: 'the "deployed" field: whether the binding\'s life events say the project is deployed' },
    flag: {
      type: 'object',
      description: 'the "flag" field, verbatim: the form the ticket\'s `Flag:` line takes',
      properties: {
        form: { type: 'string', enum: ['hidden', 'appears', 'unasked'] },
        key: { type: ['string', 'null'] },
      },
      required: ['form', 'key'],
      additionalProperties: false,
    },
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
  required: ['ok', 'number', 'title', 'slug', 'branch', 'label', 'parent', 'deployed', 'flag', 'failures', 'output'],
}

function codeSpan(value) {
  const fence = '`'.repeat(Math.max(0, ...(value.match(/`+/g) || []).map(run => run.length)) + 1)
  const pad = /^`|`$/.test(value) ? ' ' : ''
  return `${fence}${pad}${value}${pad}${fence}`
}

function ticketHint(ref = REF) {
  if (/^#?\d+$/.test(ref)) {
    const number = ref.replace('#', '')
    return `issue ${number} on the tracker (\`gh issue view ${number} --json number,title,body,labels,comments\`).`
  }
  if (!BASE) return `the file ${codeSpan(ref)} under ${ROOT_HINT}.`
  return `the file ${codeSpan(ref)} as \`${BASE}\` holds it, not the copy in the working tree, which an
  agent of this run may have changed. Read it, from ${ROOT_HINT}, with

      git show --end-of-options ${shellWord(`${BASE}:${ref}`)} --
`
}

function parentInput() {
  if (!PARENT) return ''
  return `- The ticket's parent, which it inherits from: read only its \`## Conjecture\`,
  \`## Operability\` and \`## Answers\`, beside the ticket's. It is ${ticketHint(PARENT)}
`
}

function deployedNote() {
  return `The project is ${DEPLOYED ? '' : 'not '}deployed. The preflight read the binding's
\`- Life events:\` line, where a binding with none counts as deployed, so do not read it for this.
`
}

function flagRule() {
  const key = `\`${FLAG.key}\``
  return {
    unasked: {
      spec: `   - Feature flag: the key in \`flags.toml\` that gates the new behaviour, declared there with
     \`default = false\`, and what the system does with it off (the merge base's behaviour); or
     "none" with the reason, when the change adds no behaviour a user could meet (a fix that
     restores the specified behaviour, a refactoring, a change with nothing to turn off). The
     merge judge does not read this line: a change that adds a route, a command or an exported
     symbol to a contract artefact or public-surface snapshot, with no flag that is off declared
     in \`flags.toml\` or named in the code it adds, waits for the owner (\`unflagged\`)`,
      promise: '',
      review: `   - Feature flag: new behaviour is reachable only through the flag spec.md names, read through
     the seam binding.md's Feature flags section names, declared in \`flags.toml\` with
     \`default = false\`, and tested with the flag off. Surface added to a contract
     artefact or public-surface snapshot with no flag reaches the owner whatever spec.md says:
     the merge judge reports it as \`unflagged\`, so say in the review which of the two it is,
     behaviour that should be dark or surface with nothing to turn off`,
    },
    hidden: {
      spec: `   - Feature flag: ${key}, the switch the \`Flag:\` line that applies names: the owner chose to
     keep the behaviour hidden until they switch it on. Declare it in \`flags.toml\` with
     \`default = false\`, a reason that cites that answer and a \`remove-by\` date, never
     \`permanent\`; pick the date and list it under Assumptions. Say what the system does with
     it off (the merge base's behaviour)`,
      promise: `   The \`Flag:\` line that applies, the ticket's or else its parent's, is a promise too, with
   the owner's answer: the behaviour stays hidden behind ${key} until they switch it on. A
   spec.md whose feature flag line names no switch, or another one, reads it \`bent\`.
`,
      review: `   - Feature flag: new behaviour is reachable only through ${key}, the switch the \`Flag:\`
     line that applies names with the owner's answer, read through the seam binding.md's Feature
     flags section names, declared in \`flags.toml\` with \`default = false\` and a \`remove-by\`
     date, and tested with it off. A spec.md that names no switch, or another one, against that
     answer is a blocker`,
    },
    appears: {
      spec: `   - Feature flag: "none", citing the \`Flag:\` line that applies: the owner chose to have the
     behaviour appear as soon as it is finished, while the project is not deployed. Put nothing
     behind a switch for it`,
      promise: `   The \`Flag:\` line that applies, the ticket's or else its parent's, is a promise too, with
   the owner's answer: the behaviour appears as soon as it is finished, with no switch to turn
   it off. A spec.md whose feature flag line names a switch reads it \`bent\`.
`,
      review: `   - Feature flag: the \`Flag:\` line that applies is the owner's answer: the behaviour appears
     as soon as it is finished, while the project is not deployed. spec.md's "none" cites it, and
     a missing switch is no finding`,
    },
  }[FLAG.form]
}

function promiseReadings() {
  return `List in \`ticketPromises\` every promise the ticket makes about what the product does or
   does not do: each checklist item and acceptance criterion, and each promise in its other
   sections ("it needs no account", "the list survives a restart"), in the ticket's words,
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
${flagRule().promise}   What the ticket puts out of scope or leaves to a later ticket is no promise of this one, and an
   answer under \`## Answers\` decides what it answers. A sentence about how the method treats the
   work (what a gate charges, which kind of code arrives first, what the route names next) is no
   promise either: it describes the work, not the product.`
}

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

Read these ${PARENT ? 'three' : 'two'} and nothing else (no code, no tests, no other record), and edit nothing:
- The ticket, with its comments and its \`## Answers\` section: what the owner asked for and
  agreed. It is ${ticketHint()}
${parentInput()}- The spec: \`${spec.specPath}\` under ${ROOT_HINT}.${enterRoot('      ').trimEnd()}

${deployedNote()}
1. ${promiseReadings()}
2. ${INPUT_READINGS}
3. Read each line under \`## Neighbouring cases\` of spec.md as you read an input case. A line that
   holds the behaviour for fewer values than its test uses, with no assumption in spec.md giving
   the reason, leaves a promise bent: read that promise as \`bent\`, with the line as \`specLine\`.

Read what the ticket says, not what you would have built. Your final output is consumed by a
script, not a human: return only the structured object.`
}

function preflightPrompt() {
  const script = PREFLIGHT_SCRIPT ? shellWord(PREFLIGHT_SCRIPT) : '<feature_preflight.py>'
  const found = PREFLIGHT_SCRIPT ? '' : `
In the command below, put in place of <feature_preflight.py> the path of the ratchet plugin's
\`scripts/feature_preflight.py\` as one shell word: the newest version under
\`~/.claude/plugins/cache/*/ratchet/*/scripts/\`, or wherever the plugin is installed. \`find\` it,
do not write one.
`
  const branchFlag = BRANCH ? ` --branch ${shellWord(BRANCH)}` : ''
  const baseFlag = BASE ? ` --base ${shellWord(BASE)}` : ''
  const where = ROOT ? `these two lines: the first enters ${ROOT_HINT}, the second runs the check` : `this line in ${ROOT_HINT}`
  const enter = ROOT ? `    cd ${shellWord(ROOT)}\n` : ''
  return `You run one check and relay its output. You decide nothing.
${found}
Run, in a single Bash call, ${where}:

${enter}    ${PLUGIN_PY} ${script} ${shellWord(REF)} --root .${baseFlag}${branchFlag}

It prints a JSON object on stdout ({ ok, ref, failures: [{ check, detail, produce }], number,
title, slug, branch, base, label, critic, critic_reason, parent, deployed, flag }) and a verdict line on stderr. Return that JSON's fields in the
structured result, verbatim, plus the full output. Do not fix, label, edit or commit anything,
and do not re-run with different arguments. If it prints no JSON object, return ok: false with
one failure { check: "script", detail: <what it printed, verbatim>, produce: <one of these two> }.
When Python cannot open the script itself, produce is "install the ratchet plugin; the skill
passes the script path as args.preflight". When anything else went wrong (a usage error from
the script, a file other than the script that cannot be opened), the command line is at fault,
not the install: produce is "the pipeline built a wrong preflight command; report this command
line and its output against the ratchet plugin".`
}

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

function specPrompt(gaps = []) {
  return `${PREAMBLE}
You are the SPEC agent for one feature of this repository.
${commonRules('spec')}
Load the \`${COMPANIONS.tdd}\` skill: it says what a good test is and how the seams under
test are chosen. The spec names those seams; the owner has agreed the approach through the
ticket's \`## Conjecture\` section and the agent-ready label.
Load the \`${COMPANIONS.codebase_design}\` skill too, and use its vocabulary (module,
interface, depth, seam, adapter) for the Design section: where the feature lives, which seams
it adds or changes, and deep modules over shallow wrappers.
${lastRunNote()}${amendNote(gaps)}
Inputs you may read:
- The ticket: ${ticketHint()}
  Its sections are mattpocock-skills' (Parent, What to build, Acceptance criteria, Blocked by,
  or the spec's Problem Statement, Solution, User Stories, Implementation Decisions, Testing
  Decisions, Out of Scope) plus the method's
  \`## Conjecture\`: the approach the owner agreed, the observation that would refute it,
  the alternatives and the criticism that killed each. Build that approach, not another.
  Read \`## Answers\` and the comments too: the owner's answers to an earlier run's
  questions live there, and each one is decided.
${parentInput()}- AGENTS.md, GLOSSARY.md, docs/process/binding.md, every ADR in docs/adr/ that the ticket
  touches, and any design document AGENTS.md tells you to read first
- The existing code and tests only as far as needed to place the feature and name test files

${deployedNote()}
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
   - Design: how the feature fits the project's architecture, in three short parts. Placement:
     for each piece of the feature, which declared path or part it lives in (using the parts
     the \`## Architecture\` table of docs/process/binding.md declares, or the module when none
     are declared) and why, the reason tied to the architecture decision record that section
     names on its \`- Decision:\` line (the one that records the project's macro and micro
     architecture) and to the parts its table declares, by name. Seams: each
     seam the feature adds or changes, its interface, and why the module behind it is deep
     (much behaviour behind a small interface) rather than a shallow wrapper. Considered: the
     placements and shapes you weighed and rejected, each with the reason. Read the
     architecture decision record and the declared parts before placing anything, and keep the feature
     inside them; where the project records none, say so and place by the existing code's
     shape. Whether this change implies a change to the architecture is the question below
   - Seams under test: the public interfaces the tests observe behaviour through, one line
     each on what it catches and what it misses
   - Telemetry: for each acceptance criterion, the signal that shows it working in production
     (the span, its attributes, the metric, the log field), named as binding.md's Telemetry
     section names them; a criterion with no entry point to observe says so
${flagRule().spec}
   - Test plan: the list of test files you will create, mapped to acceptance criteria; it
     includes the telemetry tests under the directory binding.md names and, when there is a
     flag, a test of the behaviour with the flag off
   - Assumptions: each point the ticket leaves undecided that you answered with a reversible
     default, with the answer and what would show it wrong
   - Open questions: only the points the owner decides (see below), each with its options,
     the recommended one first, and why
   - Neighbouring cases: ${NEIGHBOURING_RULE}
   - Out of scope (copied from the ticket)
2. Write the failing tests at those seams, with the test frameworks and directories binding.md
   names. Where behaviour shows as readable output (a rendered page, CLI output, a structured
   response, a formatted report), write an expect test with the recorded-output facility
   binding.md's \`## Tests\` section names, placing any recorded files beside the tests (never
   under \`quality/**\`) and writing the expected text by hand from the ticket so the test fails
   first for the right reason (never record from a run). Once \`depended-on\` is declared,
   altering an existing recorded output sends the merge to the owner (\`expect-output\`), just as
   new surface without a flag sends it (\`unflagged\`). Write a property test for every invariant
   you can state. Follow every AGENTS.md non-negotiable in the tests themselves. Implementation
   code is the implementer's: you write tests only.
3. Run the new tests and quote each failure's content. The right reason is a missing function,
   class, endpoint or component, or an assertion on behaviour that does not exist yet. An
   ImportError on a module that should already exist, a syntax error or a fixture error means
   the test is wrong: fix the test. Set failingForRightReason true only when every failure is
   the predicted one, and return the quoted content per test file in \`failures\`; the script
   blocks the run when a test file has no quoted failure. Put the same quotes in spec.md's
   test plan, so the reviewer sees what each test refuted.
4. Run \`tier0\` and your new tests, and capture the tail. Tier 0 is expected green and the
   new tests red. Do not run \`check\`: the implementer runs it once the code exists.
5. ${CHANGE_SIZE_RULE} Tests are counted apart from product code, under a ceiling of their own:
   when the tests you wrote alone pass ${CRITIC ? 'two thirds of' : ''} the test ceiling the run stops here, and the
   ticket is split. Write the tests the criteria need, never fewer to fit.
6. Estimate the whole change against the two ceilings and return it as \`changeEstimate\`. The
   product side is the implementation plus any ADRs and glossary entries (\`implementationLines\`,
   \`recordLines\`): it is held to the product ceiling, as is the product code already measured.
   The test side is the tests already measured (spec, fixtures), ${criticEstimate(CRITIC)}
   held to the test ceiling. Be honest and not hopeful: the fix agent cannot cut a change down by
   revising code. When either side passes its ceiling, put the split in \`proposedSplit\`: two or
   more tickets, each of which fits both, each with the acceptance criteria it carries and, in
   \`promises\`, the ticket's own \`- [ ]\` lines it carries, copied verbatim, every such line in
   exactly one part. The run then stops, and the orchestrator files the parts as tickets itself.

${FORK_TEST}

A change that implies a change to the architecture is the owner's too, and never one you take
silently or bury in Design: a new layer, a new kind of storage, a new process boundary. It is
an open question of kind \`direction\`, with the options (including the one that keeps the
architecture as it is, if the feature can be built within it), the one you recommend first, and
why. Place the feature inside the current architecture for everything the question does not
touch. Raise no other question about design: a placement within the declared architecture is
yours to decide, and a seam that is one choice among equals is an assumption.

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
(its own command to add a dependency) followed by \`snapshots\`. \`guard-diff\` still lists
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
${commonRules('implement')}
Inputs you may read:
- The spec: \`${spec.specPath}\`
- The failing tests: ${JSON.stringify(spec.testFiles)}
- Layers the spec expects you to touch: ${JSON.stringify(spec.layersTouched)}
- AGENTS.md, GLOSSARY.md, docs/process/binding.md, docs/adr/, and the existing code those
  tests exercise
${promises}${cases}You are deliberately given neither the ticket nor the spec author's reasoning. If
spec.md is ambiguous, pick the reading that is most consistent with the glossary and the ADRs.
${FORK_TEST}
${UNSTATED_RULE}
${DEPARTURE_RULE}

Load the \`${COMPANIONS.tdd}\` skill and work its loop one slice at a time: run the listed
tests, implement the smallest change that makes one pass, re-run, repeat. If the change touches
a user interface, load \`${COMPANIONS.ui_design}\` as well: \`shape\` before you build the surface,
\`polish\` before you return, and DESIGN.md is the standard you build to. The spec's tests and expected text are fixed inputs: a test you believe wrong stays red and is
named in \`notes\`. Only where an expected output is too large to state by hand may you record it
with the recorded-output tool binding.md's \`## Tests\` section names, placing any recorded files
beside the tests (never under \`quality/**\`) and returning every such path in
\`recordedOutputs\` for the reviewer; never re-accept an existing recorded output silently. Keep
every declared-path rule and every AGENTS.md non-negotiable, add the contracts binding.md requires
where the spec states pre/postconditions, and regenerate any contract artefacts binding.md
names when an interface changes, with the entry point it names.

${CHANGE_SIZE_RULE}

${TRIGGERS_RULE}

Finish when every listed test passes AND \`check\` exits 0. If a gate blocks you and you
believe the gate, not your code, is wrong, write a gate dispute and list it in \`disputes\`.
If you cannot get there, return anyway with \`checkPasses: false\` and explain in \`notes\`.`
}

function fixPrompt(spec, findings, testsAdded, iteration, rounds) {
  const blockingFindings = owed(findings)
  const minors = findings.filter(f => f.severity === 'minor' && !blockingFindings.includes(f))
  return `${PREAMBLE}
You are the FIX agent (round ${iteration} of ${rounds}) for feature \`${spec.slug}\` of this repository.
${commonRules('fix')}
A previous implementer built this feature and a critic or reviewer found problems. You start
fresh, from the spec and the code as they are; the previous implementer's reasoning is not
among your inputs.

Inputs you may read:
- The spec: \`${spec.specPath}\`
- The spec's tests: ${JSON.stringify(spec.testFiles)}
- Tests a critic added, if any (some currently fail on purpose): ${JSON.stringify(testsAdded)}
- AGENTS.md, GLOSSARY.md, docs/process/binding.md, docs/adr/, and the code under change

Load the \`${COMPANIONS.tdd}\` skill and work one slice at a time.

Findings you MUST resolve (blocker, major, or a test the critic kept failing):
${JSON.stringify(blockingFindings, null, 2)}

Findings you MAY resolve if cheap (minor):
${JSON.stringify(minors, null, 2)}

Resolve each blocking finding by changing the implementation; the spec's tests and expected text
are fixed inputs. Only where an expected output is too large to state by hand may you record it
with the recorded-output tool binding.md's \`## Tests\` section names, placing any recorded files
beside the tests (never under \`quality/**\`) and returning every such path in
\`recordedOutputs\` for the reviewer; never re-accept an existing recorded output silently. A
critic test that asserts something the spec contradicts stays red and is explained in
\`notes\`. A finding that can only be resolved by changing a guard path, or a gate you believe
is wrong, becomes a gate dispute listed in \`disputes\`.
${roleAssumptionsNote()}
${FORK_TEST}
${UNSTATED_RULE}
${DEPARTURE_RULE}

${CHANGE_SIZE_RULE}

${TRIGGERS_RULE}

Finish when the spec tests and the
critic's tests pass and \`check\` exits 0; otherwise return with \`checkPasses: false\` and
explain.`
}

function stagingLines(conflicts) {
  if (!conflicts.length) return 'The rebase stopped on no path when it was tried.\n'
  return `The rebase stopped on these paths when it was tried. Once you have resolved one, its line
stages it:

${conflicts.map(path => `      git --literal-pathspecs add -- ${shellWord(path)}`).join('\n')}
`
}

function reconcilePrompt(spec, conflicts) {
  return `${PREAMBLE}
You are the RECONCILE agent for feature \`${spec.slug}\` of this repository. The feature was
built and approved. Then another change merged into \`${BASE}\`, and this branch must hold
that change before it can merge. You bring the two together and change nothing else.
${commonRules('reconcile', true)}
Inputs you may read:
- The spec: \`${spec.specPath}\`
- The branch's own change, the first line below, and what merged meanwhile, the second, and
  its diff:

      git diff --end-of-options ${shellWord(`${BASE}...${BRANCH}`)}
      git log --oneline --end-of-options ${shellWord(`${BRANCH}..${BASE}`)}

- AGENTS.md, GLOSSARY.md, docs/process/binding.md, docs/adr/
${stagingLines(conflicts)}
Do this, in order:
1. Run the first line below. If it fails, run the second.

       git merge-base --is-ancestor --end-of-options ${shellWord(BASE)} HEAD
       git rebase --end-of-options ${shellWord(BASE)}

   At each stop, resolve every conflicted file so that both changes hold: both flags stay in the
   registry, both terms in the glossary, both routes in the application. Where the two cannot
   both hold, \`${BASE}\` stands as it is and this feature's code adapts to it. Then
   \`git --literal-pathspecs add --\` each file you resolved, its path one quoted word, and
   \`git rebase --continue\`.
   Never \`--skip\` a commit and never take one side of a whole file.
2. Run \`check\`. A red gate is answered by changing this feature's code, never the other
   change's and never a gate. A number the other change took first (a decision record, a
   migration) is the other change's: give this feature's file the next free one. A test of
   this feature that the other change made wrong is fixed to assert what spec.md says.
3. Stop when the rebase is finished and \`check\` exits 0. If you cannot get there, run
   \`git rebase --abort\` if a rebase is still in progress, and return with
   \`checkPasses: false\`.

${FORK_TEST}
${UNSTATED_RULE}
${DEPARTURE_RULE}

\`filesChanged\` lists the files you edited after the rebase finished; \`allNewTestsPass\` is
whether the spec's tests pass; \`notes\` names each conflict and how you resolved it, since
the reviewer reads the result and not your reasoning.

${CHANGE_SIZE_RULE}`
}

function critiquePrompt(spec, filesChanged, previousRound) {
  const previous = previousRound
    ? `
This is a RE-CRITIQUE after a fix round. Previous findings:
${JSON.stringify(previousRound.findings, null, 2)}
Tests you added last round: ${JSON.stringify(previousRound.testsAdded)}
Re-check every previous finding first. Each of your findings sets \`carries\` to the \`id\` of the
previous finding it is still open from, and to null when it is new. Then look for NEW ways the fix
may have broken things.`
    : ''
  return `${PREAMBLE}
You are the CRITIC for feature \`${spec.slug}\` of this repository. Your job is to BREAK it.
You are the one adversary of this pipeline (its agent count is fixed, so you do the work
in-process and load nothing that spawns agents). The rule of evidence: a finding survives only when a run proves
it, so each carries the exact command, the output it printed and the output the spec requires;
leave out anything you did not run; "no findings" is a valid result when you name what you ran.
${commonRules('critic')}
Inputs you may read:
- The spec: \`${spec.specPath}\`
- The spec's tests: ${JSON.stringify(spec.testFiles)}
- Files the implementer changed: ${JSON.stringify(filesChanged)}
- AGENTS.md, GLOSSARY.md, docs/process/binding.md, docs/adr/, and the fitness tests, mutation
  config and fuzz harness binding.md names
The implementer's notes and the ticket are outside your inputs.
${previous}${roleAssumptionsNote()}
Do all of the following and report evidence for each:
1. Property tests: with the property-testing library binding.md names, add a property for
   every invariant the spec states or implies (round-trips, monotonicity, conservation,
   idempotence, ordering, anything an AGENTS.md non-negotiable implies for the values touched).
   Add edge-case tests you EXPECT to fail: zero, negative and boundary values, empty
   collections, duplicates, unknown identifiers, unit, period or time-zone boundaries where
   the domain has them, very large values, unicode; where behaviour shows as readable output,
   edge-case tests may be expect tests under the same rule (expected text written by hand from
   the spec, never recorded from a run, with any recorded files beside the critique tests and
   never under \`quality/**\`). Put them in the critique directory
   binding.md's Critique tests section names, flat, one file per layer touched, named
   \`<slug>.<layer>.critique.test.<ext>\` (\`tests/critique/\` when the section names none).
   Never put them beside the code: a critique file is organised by feature, not by module.
2. Mutation testing: if binding.md names a mutation gate, run it targeted at the modules in
   filesChanged with the configured invocation. Report survived mutants as findings with the
   mutant diff as evidence, and add a test that kills each if you can. Otherwise write
   "not applicable" and why in \`mutationSummary\`.
3. Fuzzing: if binding.md names a fuzz harness and filesChanged includes a value type (an
   amount, a date, an identifier, a parser), run the harness against it, time-boxed to a few
   minutes. Report crashes as findings. Otherwise write "not applicable" and why in
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
6. User interface: if the change touches one, load \`${COMPANIONS.ui_design}\` and run its \`audit\`
   and \`critique\` on the surface; each finding they prove is a finding here, judged against
   DESIGN.md.
7. Guard paths: run \`guard-diff\` and put every guard path it lists in \`guardFilesChanged\`.
   Grade each by the Guard paths line of the severity list below; a finding on a changed
   guard path is titled "guard path changed: <path>", and the gate disputes are those under
   docs/disputes/ in the change. For a path whose note is \`additions:\`, read what it added;
   a finding on it quotes the two passages. Your scratch copy for step 4 is outside the
   repository, so sabotage there is not a guard change.

8. Neighbouring cases: a value set that a neighbouring test repeats its checks over, and that
   spec.md does not list under \`## Neighbouring cases\`, is a finding against the spec. ${NEIGHBOURING_RULE}

A case you measured or saw that the spec does not state is a finding when a record you can quote
says which behaviour is right, and an \`unstated\` entry when none does. It is one of the two
whatever you judge its likelihood, and you keep no failing test for an \`unstated\` entry.
${FORK_TEST}
${UNSTATED_RULE}
${quoteRule(spec, 'findings', false)}

Keep every test you added that PASSES (regression value) and every test that FAILS because of
a genuine defect (list it as \`suggestedTest\` on the finding). Delete tests that fail because
the test itself was wrong. You edit test files only.

List every command you ran trying to break the change in \`attempted\`, with its one-line
outcome. Nothing found refutes only the attempts that would have hit; the script blocks a
run whose critic found nothing and attempted nothing.

${SEVERITY}

The suite is expected to be red only on the failing tests you kept; say so in the
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

function reviewPrompt(spec, again, settled, recordedOutputs = []) {
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
top; \`git diff\` against \`${BASE}\`, as the inputs below run it, shows both, and nothing of the
other change.
`
    : ''
  return `${PREAMBLE}
You are the independent REVIEWER for feature \`${spec.slug}\` of this repository.
${commonRules('review')}${second}
Inputs you may read, and nothing else:
- The ticket, with its comments and its \`## Answers\` section: what the owner asked for and
  agreed. Read it; never edit it or comment on it. It is ${ticketHint()}
${parentInput()}- The spec: \`${spec.specPath}\`
- The diff: the change is on branch \`${BRANCH}\`, uncommitted unless this is a second review.
  Enumerate it with these three (untracked files are new files: read them):

      git diff --stat --end-of-options ${shellWord(BASE)}
      git diff --end-of-options ${shellWord(BASE)}
      git status --porcelain

  If the branch has commits, this one is equivalent:

      git diff --end-of-options ${shellWord(`${BASE}...${BRANCH}`)}

- AGENTS.md, GLOSSARY.md, docs/adr/, docs/process/binding.md, docs/disputes/, DESIGN.md
- Any generated contract artefacts that binding.md names
- Recorded outputs recorded by implement/fix with the tool: ${JSON.stringify(recordedOutputs || [])} (plus any recorded output added or altered in the diff, which the plugin's \`scripts/expect_outputs.py\` lists against the base branch)
${outsideInputs(CRITIC)} are outside your inputs:
judge the spec against the ticket, and the code against the spec and the standards.

${deployedNote()}${roleAssumptionsNote()}${movedNote()}
Load the \`${COMPANIONS.code_review}\` skill and apply its two axes in-process, as both of
its sub-agents (the pipeline fixes the agent count): the spec source is spec.md; the standards
sources are AGENTS.md, the glossary, docs/adr/ and the binding, plus the skill's smell
baseline as judgement calls. Load the \`${COMPANIONS.codebase_design}\` skill for axis F and
use its vocabulary. Record every issue with axis, severity, location and evidence:
A. Spec conformance: does each Given/When/Then acceptance criterion have a test and an
   implementation that satisfies it? Any criterion silently narrowed? Any test weakened,
   skipped or deleted? Any behaviour added that the spec does not ask for? Hold each recorded
   output added-by-recording or altered (in \`recordedOutputs\` or the diff) to a justifying
   criterion in spec.md: a recorded output added-by-recording or altered with no justifying
   criterion is a blocker, and once \`depended-on\` is declared, altering an existing recorded
   output waits for the owner (\`expect-output\`) like \`unflagged\` for new surface.
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
     reads it from the in-memory exporter, and no span attribute or log field carries a secret
     or a personal identifier
${flagRule().review}
   - Assumptions: each one spec.md lists is what the tests and the code actually do
   - Does this change make a decision with lasting consequences (new storage, new external
     dependency, new layer, a changed rule that versions over time)? If so set adrSuggested.
C. Guard paths: run \`guard-diff\` and list every guard path it prints in \`guardChanges\`,
   each \`dependencies added:\` or \`lockfile:\` note in \`dependencyNotes\`, and each
   \`additions:\` note in \`recordNotes\`. Then
   read every file under docs/disputes/ that the diff adds or changes. For each changed guard
   path, if some dispute record's text names that exact path, add \`{ path, disputeFile }\` to
   \`disputeCoverage\`; a dispute that names only the gate covers no path. Grade each changed
   guard path by the Guard paths line of the severity list below. The check behind an
   \`additions:\` note reads shape, not meaning, so read each addition against the records.
D. The ticket. Only the spec role and the ticket check read it before you, so a change that loses
   what the owner asked for can pass every other axis. ${readings}${promiseReadings()}
   Read them against spec.md as it now stands and the change: a promise is \`dropped\` when what
   carried it is gone from spec.md, or the tests and the code do not do it.
E. Inputs. ${INPUT_READINGS} A case is null when the change does not decide it as spec.md states,
   or as the settled reading states where the run assumed it.
F. Design. Judge the change against the project's architecture: read the architecture decision
   record the \`## Architecture\` section of docs/process/binding.md names on its \`- Decision:\`
   line (the one that records the macro and micro architecture) and the parts its table declares
   with what each may depend on, and the spec's Design section for what was decided and why. Issues on the \`design\`
   axis are: a layer or seam violated, a module that reaches around the seam the architecture
   declares for what it uses, logic placed in a layer that should not hold it, a dependency
   direction the architecture does not allow, a shallow wrapper (a module whose interface is
   nearly as large as what it hides, that only forwards), and a change that makes an
   architecture decision (a new layer, a new kind of storage, a new process boundary) the spec
   did not raise with the owner. Where the project records no architecture,
   judge only the declared layers and the spec's own Design section.
G. Cases no record states. A case you saw or measured where no record says which behaviour is right
   is no issue: return it as an \`unstated\` entry.
${FORK_TEST}
${UNSTATED_RULE}
${quoteRule(spec, 'issues', true)}
   Each \`acceptedInputs\` entry carries \`named\`: the record that names the input, the ticket or
   spec.md, and the whole sentence that names it, quoted the same way.
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

async function fixRound(findings, testsAdded, round, rounds) {
  log(`Fix ${round}/${rounds}: ${owed(findings).length} finding(s) owed a fix`)
  const fix = must(
    await agent(fixPrompt(spec, findings, testsAdded, round, rounds), {
      label: `fix-${round}`, phase: 'Fix', schema: IMPLEMENT_SCHEMA,
    }),
    `fix-${round}`,
  )
  filesChanged = union(filesChanged, fix.filesChanged)
  recordedOutputs = union(recordedOutputs, fix.recordedOutputs)
  disputes.push(...(fix.disputes || []))
  const stop = noRoom(`fix round ${round}`, fix.changeSize, false)
  lastCheckOutput = fix.checkOutput
  lastCheckPasses = fix.checkPasses === true
  if (stop) return tooLarge(stop, spec, { filesChanged, disputes, checkOutput: fix.checkOutput })
  return haltAfter(`fix-${round}`, fix)
}

function haltAfter(after, result, entries = result.unstated || []) {
  return settleCases(after, entries, result.departures || [], {
    specPath: spec.specPath,
    testFiles: spec.testFiles,
    extra: {
      filesChanged,
      disputes,
      checkOutput: lastCheckOutput,
      criticFindings: critique.findings,
      criticFindingsByRound: allCriticFindings,
      unresolvedBlockingFindings: owed(critique.findings),
      testsAdded: critique.testsAdded,
    },
  })
}

function fixableByReview(review) {
  return blocking(review.issues).length > 0 && ticketIssues(review).length === 0
}

function noRoom(stage, size, critic = true) {
  const lines = size && size.lines
  const ceiling = size && size.ceiling
  if (Number.isInteger(lines) && Number.isInteger(ceiling) && ceiling > 0 && lines > ceiling) {
    return {
      kind: 'product',
      stage,
      lines,
      ceiling,
      reason: `too large: ${lines} product lines after ${stage}, over the change-size ceiling of ${ceiling} (tests are counted apart)`,
    }
  }
  const tests = size && size.testLines
  const testCeiling = size && size.testCeiling
  if (!Number.isInteger(tests) || !Number.isInteger(testCeiling) || testCeiling <= 0) return null
  const [numerator, denominator] = testRoomShare(critic)
  if (tests * denominator <= testCeiling * numerator) return null
  return {
    kind: 'tests',
    stage,
    lines: tests,
    ceiling: testCeiling,
    reason: critic
      ? `too large: ${tests} test lines after ${stage}, over two thirds of the test ceiling of ${testCeiling}, with the critic's tests still to come`
      : `too large: ${tests} test lines after ${stage}, over the test ceiling of ${testCeiling}`,
  }
}

function cannotFit(size, estimate, critic = true) {
  if (!estimate) return null
  const lines = size && size.lines
  const ceiling = size && size.ceiling
  const product = (Number.isInteger(lines) ? lines : 0) + (estimate.implementationLines || 0) + (estimate.recordLines || 0)
  if (Number.isInteger(ceiling) && ceiling > 0 && product > ceiling) {
    return {
      kind: 'product',
      stage: 'the spec',
      lines: product,
      ceiling,
      proposedSplit: estimate.proposedSplit || [],
      reason: `too large: the spec estimates ${product} product lines for the whole change (already measured ${lines || 0}, implementation ${estimate.implementationLines || 0}, records ${estimate.recordLines || 0}), over the change-size ceiling of ${ceiling} (tests are counted apart)`,
    }
  }
  const tests = size && size.testLines
  const testCeiling = size && size.testCeiling
  if (!Number.isInteger(testCeiling) || testCeiling <= 0) return null
  const criticLines = critic ? estimate.criticLines || 0 : 0
  const total = (Number.isInteger(tests) ? tests : 0) + criticLines
  if (total <= testCeiling) return null
  return {
    kind: 'tests',
    stage: 'the spec',
    lines: total,
    ceiling: testCeiling,
    proposedSplit: estimate.proposedSplit || [],
    reason: `too large: the spec estimates ${total} test lines for the whole change (spec and tests ${tests || 0}, ${critic ? `the critic's tests ${criticLines}` : 'no critic'}), over the test ceiling of ${testCeiling}`,
  }
}

function tooLarge(stop, spec, extra) {
  log(`Blocked after ${stop.stage}: ${stop.reason}`)
  return {
    slug: SLUG,
    ref: REF,
    verdict: 'blocked',
    ...FINDINGS_SO_FAR,
    verdictForcedBy: [stop.reason],
    tooLarge: stop,
    openQuestions: [],
    guardEdits: [],
    specPath: spec.specPath,
    testFiles: spec.testFiles,
    branch: BRANCH,
    ...extra,
    requiredByOrchestrator: [
      'Commit nothing and open no PR. The ticket is too large to build as one change; splitting it is no product decision, so the orchestrator splits it and the owner is not asked',
      'Write a plan from `tooLarge.proposedSplit` (each part\'s `title` and `promises`, `summary` from its `criteria`) and run `drain.py split <ticket> --plan <file>`, which files the parts with the parent\'s label, each waiting on the one before, and closes the parent as split with its text untouched. It refuses a plan that adds, drops, rewords or doubles one of the ticket\'s `- [ ]` promises, and names the line; with no `proposedSplit`, divide the ticket\'s promises yourself, by what a person can use, and the spec at `specPath` says which criteria and tests there are to divide',
    ],
  }
}

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
    ...FINDINGS_SO_FAR,
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

function criticEstimate(critic) {
  return critic ? "plus the critic's tests (about two fifths of yours)," : 'and no critic tests (the critic does not run, so return criticLines: 0),'
}

function outsideInputs(critic) {
  return critic ? "The implementer's notes and the critic's findings" : "The implementer's notes"
}

function criticSilent(critic, critique) {
  if (!critic) return false
  const attempted = (critique.attempted || []).filter(a => a && String(a).trim())
  return critique.findings.length === 0 && attempted.length === 0
}

function blocking(findings) {
  return (findings || []).filter(f => f && (f.severity === 'blocker' || f.severity === 'major'))
}

function carries(finding, earlier) {
  if (finding.carries === earlier.id) return true
  const was = earlier.breaks
  const now = finding.breaks
  return Boolean(was && now) && was.record === now.record && was.quote === now.quote &&
    String(finding.title || '').trim() === String(earlier.title || '').trim()
}

function owed(findings) {
  return (findings || []).filter(f => f && (isBlocking(f)
    ? f.quoted === true && (f.testFails === true || f.heldByKeptTest !== true)
    : f.testFails === true))
}

function isBlocking(finding) {
  return Boolean(finding) && (finding.severity === 'blocker' || finding.severity === 'major')
}

function quoteVerdict(output) {
  const text = String(output || '')
  const start = text.indexOf('{')
  if (start < 0) return null
  try {
    const verdict = JSON.parse(text.slice(start))
    return verdict && Array.isArray(verdict.found) && Array.isArray(verdict.not_found) ? verdict : null
  } catch (error) {
    return null
  }
}

function quoteHeld(finding, index, verdict) {
  const breaks = finding && finding.breaks
  if (!verdict || !breaks) return false
  return verdict.found.some(e => e && e.index === index && e.record === breaks.record && e.quote === breaks.quote)
}

function whyMissed(index, verdict) {
  const missed = verdict && verdict.not_found.find(e => e && e.index === index)
  return missed && missed.reason ? missed.reason : 'the quote check output does not show its quote found'
}

function sift(findings, quoteOutput, idOf, from) {
  const verdict = quoteVerdict(quoteOutput)
  const kept = []
  const moved = []
  ;(findings || []).forEach((finding, index) => {
    const id = idOf(index)
    if (!isBlocking(finding)) {
      kept.push({ ...finding, id })
    } else if (quoteHeld(finding, index, verdict)) {
      kept.push({ ...finding, id, quoted: true })
    } else {
      moved.push({ id, from, severity: finding.severity, title: finding.title, detail: finding.location ?? finding.suggestedTest ?? '', evidence: finding.evidence, breaks: finding.breaks ?? null, reason: whyMissed(index, verdict) })
    }
  })
  return { kept, moved }
}

function readCritique(raw, round) {
  const { kept, moved } = sift(raw.findings, raw.quoteOutput, index => `C${round}-${index + 1}`, 'critic')
  PENDING_MOVED.push(...moved)
  return { ...raw, findings: kept }
}

function readReview(raw) {
  const { kept, moved } = sift(raw.issues, raw.quoteOutput, index => `R${index + 1}`, 'review')
  return { ...raw, issues: kept, movedIssues: moved }
}

function ownerEntry(moved) {
  const view = moved.from === 'critic' ? 'The critic\'s view' : 'The reviewer\'s view'
  const quote = moved.breaks ? `${moved.breaks.record}: "${moved.breaks.quote}"` : 'none given'
  return {
    of: moved.id,
    ...(moved.from === 'critic' ? { unclassified: true } : { unquotedReview: true }),
    now: moved.evidence,
    evidence: moved.evidence,
    question: `A ${moved.from} found a case no record states, and nothing classified it: ${moved.title}. Where: ${moved.detail || 'not given'}. Evidence: ${moved.evidence}. The quote that was not found: ${quote}.`,
    whose: 'owner',
    kind: 'direction',
    options: [`${view}: ${moved.title}. Change the code as the finding asks`, 'What the code does now. Leave it as it is'],
    recommendation: `Nothing quoted decides it: ${moved.reason}`,
    refuter: 'Wrong if a record already states which behaviour is right',
  }
}

function unclassifiedCount() {
  return ALL_UNSTATED.filter(entry => entry.unclassified === true).length
}

function unquotedReviewCount() {
  return ALL_UNSTATED.filter(entry => entry.unquotedReview === true).length
}

function assumedFrom(entry) {
  return { question: entry.question, assumed: entry.options[0], refuter: entry.refuter }
}

function questionOf(entry) {
  return {
    question: entry.question,
    kind: entry.kind,
    options: entry.options,
    recommendation: entry.recommendation,
    now: entry.now,
    evidence: entry.evidence ?? entry.why,
    by: entry.by,
  }
}

function reviewEntries(review) {
  const own = (review.unstated || []).filter(Boolean)
  const answered = new Set(own.map(entry => entry.of).filter(Boolean))
  const unanswered = PENDING_MOVED.filter(moved => !answered.has(moved.id)).map(ownerEntry)
  PENDING_MOVED = []
  return [...own, ...unanswered, ...(review.movedIssues || []).map(ownerEntry)]
}

function settleCases(after, entries, departures, context) {
  const tagged = entries.map(entry => ({ ...entry, by: after }))
  const departed = departures.map(departure => ({ ...departure, by: after }))
  ALL_UNSTATED.push(...tagged)
  ALL_DEPARTURES.push(...departed)
  ROLE_ASSUMPTIONS.push(
    ...tagged.filter(entry => entry.whose === 'role').map(assumedFrom),
    ...departed.filter(d => d.from === 'assumption' && d.whose === 'role').map(assumedFrom),
  )
  const asks = [
    ...tagged.filter(entry => entry.whose === 'owner'),
    ...departed.filter(d => d.from === 'criterion' || d.whose === 'owner'),
  ]
  if (!asks.length) return null
  log(`Blocked after ${after}: ${asks.length} question(s) for the owner`)
  return {
    slug: SLUG,
    ref: REF,
    verdict: 'blocked',
    stop: departed.some(d => asks.includes(d)) ? 'departure' : 'question',
    stoppedAfter: after,
    openQuestions: asks.map(questionOf),
    departures: ALL_DEPARTURES,
    unstated: ALL_UNSTATED,
    unclassified: unclassifiedCount(),
    unquotedReviewIssues: unquotedReviewCount(),
    assumptions: ROLE_ASSUMPTIONS,
    specPath: context.specPath,
    testFiles: context.testFiles,
    branch: BRANCH,
    ...FINDINGS_SO_FAR,
    ...context.extra,
    requiredByOrchestrator: [
      'Put every open question to the owner in one message, each with your recommended answer first and why, so the owner can accept them all at once: through the question tool, all in one call where it takes them; each answer goes on the ticket under `## Answers`, where the next spec agent reads it',
      'Commit nothing and open no PR. The run is not approved, so its work is kept as the stash a run that is not approved leaves; once every question is answered, build the ticket again from the preflight, so spec.md holds each answer from its first line',
    ],
  }
}

function union(...lists) {
  const seen = new Set()
  for (const list of lists) for (const item of list || []) if (item) seen.add(item)
  return Array.from(seen).sort()
}

function isDisputeRecord(path) {
  return /^docs\/disputes\/\d{4}-[a-z0-9-]+\.md$/.test(path)
}

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

function additiveRecords(recordNotes) {
  return new Set((recordNotes || []).filter(n => n && n.path && Array.isArray(n.additions)).map(n => n.path))
}

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

function dropped(readings) {
  return (readings.ticketPromises || []).filter(p => p.reading === 'dropped')
}

function undecided(readings) {
  return (readings.acceptedInputs || []).flatMap(input =>
    Object.keys(INPUT_CASES).filter(c => input[c] === null)
      .map(c => ({ input: input.input, plainName: input.plainName, case: c, named: input.named })))
}

function ownerName({ plainName }) {
  const name = String(plainName).trim().replace(/[.;:,]+$/, '')
  return /^[A-Z][a-z ]/.test(name) ? name[0].toLowerCase() + name.slice(1) : name
}

function gaps(readings) {
  const bent = (readings.ticketPromises || []).filter(p => p.reading === 'bent')
  return [...dropped(readings), ...bent, ...undecided(readings)]
}

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

function ticketIssues(review) {
  const promises = dropped(review).map(p => ({
    axis: 'ticket',
    severity: 'major',
    title: `the change drops the ticket's promise "${p.promise}"`,
    location: p.specLine,
    evidence: 'nothing in spec.md, the tests or the code carries it',
    breaks: { record: 'ticket', quote: p.promise },
    quoted: true,
  }))
  const cases = undecided(review).map(entry => ({
    axis: 'input',
    severity: 'major',
    title: `the change does not say what the app does when ${ownerName(entry)} is ${INPUT_CASES[entry.case].owner}`,
    location: entry.input,
    evidence: `the change decides no ${entry.case} value. The rule it breaks is the method's own, that every case of an input a record names is decided, and not a promise of the project; the quote shows the input is the record's, and the quote check refuses an invented input`,
    breaks: entry.named,
    quoted: true,
  }))
  return [...promises, ...cases]
}

function reviewIssues(review) {
  return [...(review.issues || []), ...ticketIssues(review)]
}

function withSurfaced(assumptions, readings) {
  const all = [...(assumptions || [])]
  for (const p of readings.ticketPromises || []) {
    if (p.reading !== 'assumed' && p.reading !== 'bent') continue
    if (all.some(a => a && a.question === p.promise)) continue
    all.push({ question: p.promise, assumed: p.specLine, refuter: p.reading === 'bent' ? p.change : p.ownerSees })
  }
  return all
}

function questionText(question) {
  return String(question && typeof question === 'object' ? question.question || '' : question)
}

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

if (REJUDGE) {
  SLUG = String(REJUDGE.slug || '')
  const spec = { slug: SLUG, specPath: String(REJUDGE.specPath || '') }
  if (!SLUG || !spec.specPath || !BRANCH) {
    throw new Error('rejudge needs args.branch and args.rejudge = { slug, specPath }: pass what the first run returned')
  }
  let reconcile = null
  if (CONFLICTS.length > 0 || REJUDGE.checkRed === true) {
    phase('Reconcile')
    reconcile = must(
      await agent(reconcilePrompt(spec, CONFLICTS), { label: 'reconcile', phase: 'Reconcile', schema: IMPLEMENT_SCHEMA }),
      'reconcile',
    )
    log(`Reconcile: ${CONFLICTS.length} conflicted path(s), check=${reconcile.checkPasses}`)
    const reconcileStop = noRoom('the reconcile', reconcile.changeSize, false)
    if (reconcileStop) return tooLarge(reconcileStop, spec, { rejudged: true, filesChanged: reconcile.filesChanged, disputes: reconcile.disputes || [], checkOutput: reconcile.checkOutput })
  }
  const rejudgeContext = { specPath: spec.specPath, testFiles: [], extra: { rejudged: true, filesChanged: reconcile ? reconcile.filesChanged : [], disputes: reconcile ? reconcile.disputes || [] : [], checkOutput: reconcile ? reconcile.checkOutput : '' } }
  const reconcileHalt = reconcile ? settleCases('reconcile', reconcile.unstated || [], reconcile.departures || [], rejudgeContext) : null
  if (reconcileHalt) return reconcileHalt
  phase('Review')
  const review = readReview(must(await agent(reviewPrompt(spec, true, SETTLED, Array.isArray(REJUDGE.recordedOutputs) ? REJUDGE.recordedOutputs : []), { label: 'review-again', phase: 'Review', schema: REVIEW_SCHEMA }), 'review'))
  const reviewHalt = settleCases('review-again', reviewEntries(review), [], { ...rejudgeContext, extra: { ...rejudgeContext.extra, reviewIssues: reviewIssues(review), checkOutput: review.checkOutput } })
  if (reviewHalt) return reviewHalt
  const judged = rejudged({ review, reconcile, dependencies: REJUDGE.dependencies })
  const assumptions = withSurfaced([...(REJUDGE.assumptions || []), ...ROLE_ASSUMPTIONS], review)
  log(`Review again: ${review.verdict}` + (judged.forced.length ? `; forced to request_changes: ${judged.forced.join('; ')}` : ''))
  return {
    slug: SLUG,
    ref: REF,
    rejudged: true,
    ...FINDINGS_SO_FAR,
    verdict: judged.verdict,
    verdictForcedBy: judged.forced,
    filesChanged: reconcile ? reconcile.filesChanged : [],
    reconcileNotes: reconcile ? reconcile.notes : '',
    disputes: reconcile ? reconcile.disputes || [] : [],
    reviewIssues: reviewIssues(review),
    reviewVerdict: review.verdict,
    ticketPromises: review.ticketPromises,
    acceptedInputs: review.acceptedInputs,
    unstated: ALL_UNSTATED,
    unclassified: unclassifiedCount(),
    unquotedReviewIssues: unquotedReviewCount(),
    departures: ALL_DEPARTURES,
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

phase('Preflight')
const preflight = must(await agent(preflightPrompt(), { label: 'preflight', phase: 'Preflight', effort: 'low', schema: PREFLIGHT_SCHEMA }), 'preflight')
if (!preflight.ok) {
  const failures = preflight.failures || []
  log(`Blocked by the preflight: ${failures.map(f => f.check).join(', ') || 'no reason given'}`)
  return {
    verdict: 'blocked',
    ref: REF,
    ...FINDINGS_SO_FAR,
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
PARENT = typeof preflight.parent === 'string' ? preflight.parent.trim() : ''
DEPLOYED = preflight.deployed !== false
FLAG = flagOf(preflight.flag, DEPLOYED)
if (preflight.companions && typeof preflight.companions === 'object') COMPANIONS = companionsOf(preflight.companions)
const unfitPreflight = unfitArgument()
if (unfitPreflight) return unfitPreflight
CRITIC = preflight.critic !== false
CRITIC_REASON = String(preflight.critic_reason || '')
if (!SLUG || !BRANCH) throw new Error(`preflight returned ok without a slug and branch: ${JSON.stringify(preflight)}`)
log(`Preflight green: ticket ${preflight.number || REF} "${preflight.title}" on branch ${BRANCH}`)

phase('Spec')
log(`Spec: ${REF} on branch ${BRANCH}`)
const written = must(await agent(specPrompt(), { label: 'spec', phase: 'Spec', schema: SPEC_SCHEMA }), 'spec')
log(`Spec ${written.slug}: ${written.testFiles.length} test file(s), layers ${written.layersTouched.join(', ')}` +
  (written.failingForRightReason ? '' : ' -- WARNING: tests not failing for the right reason') +
  (written.openQuestions.length ? `, ${written.openQuestions.length} open question(s)` : ''))
const writtenStop = specStop(written)
if (writtenStop) return writtenStop

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
NAMED_GATES = namedGatesOf([...NAMED_GATES, ...(Array.isArray(spec.namedGates) ? spec.namedGates : [])])
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

phase('Implement')
const impl = must(
  await agent(implementPrompt(spec, kept.map(k => k.question), assumedByRun), { label: 'implement', phase: 'Implement', schema: BUILD_SCHEMA }),
  'implement',
)
let filesChanged = union(spec.testFiles, impl.filesChanged)
let recordedOutputs = union(impl.recordedOutputs)
const disputes = [...(impl.disputes || [])]
const implStop = noRoom('the implementation', impl.changeSize, CRITIC)
if (implStop) return tooLarge(implStop, spec, { filesChanged, disputes, checkOutput: impl.checkOutput })
let lastCheckOutput = impl.checkOutput
let lastCheckPasses = impl.checkPasses === true
log(`Implement: ${impl.filesChanged.length} file(s), tests pass=${impl.allNewTestsPass}, check=${impl.checkPasses}`)

let critique = {
  findings: [], testsAdded: [], attempted: [], guardFilesChanged: [],
  fitnessSabotageConfirmed: false, fitnessSabotageSummary: '',
}
const allCriticFindings = []
let iterations = 0
let carriedStillOwed = []
const implementHalt = haltAfter('implement', impl)
if (implementHalt) return implementHalt
if (CRITIC) {
  phase('Critique')
  critique = readCritique(must(
    await agent(critiquePrompt(spec, filesChanged, null), { label: 'critique', phase: 'Critique', schema: CRITIQUE_SCHEMA }),
    'critique',
  ), 0)
  filesChanged = union(filesChanged, critique.testsAdded)
  allCriticFindings.push({ round: 0, findings: critique.findings })
  log(`Critique: ${critique.findings.length} finding(s), ${owed(critique.findings).length} owed a fix; ` +
    `fitness sabotage confirmed=${critique.fitnessSabotageConfirmed}`)
  const critiqueHalt = haltAfter('critique', critique)
  if (critiqueHalt) return critiqueHalt

  phase('Fix')
  while (owed(critique.findings).length > 0 && iterations < MAX_FIX_ITERATIONS && carriedStillOwed.length === 0) {
    iterations += 1
    const wasOwed = owed(critique.findings)
    const stop = await fixRound(critique.findings, critique.testsAdded, iterations, MAX_FIX_ITERATIONS)
    if (stop) return stop
    critique = readCritique(must(
      await agent(critiquePrompt(spec, filesChanged, critique), {
        label: `critique-${iterations}`, phase: 'Fix', schema: CRITIQUE_SCHEMA,
      }),
      `critique-${iterations}`,
    ), iterations)
    filesChanged = union(filesChanged, critique.testsAdded)
    allCriticFindings.push({ round: iterations, findings: critique.findings })
    log(`Re-critique ${iterations}: ${critique.findings.length} finding(s), ${owed(critique.findings).length} still owed a fix`)
    const reCritiqueHalt = haltAfter(`critique-${iterations}`, critique)
    if (reCritiqueHalt) return reCritiqueHalt
    carriedStillOwed = owed(critique.findings).filter(f => wasOwed.some(earlier => carries(f, earlier))).map(f => f.id)
    if (carriedStillOwed.length) log(`Fix loop stops: ${carriedStillOwed.join(', ')} carried through the round it was owed and is still owed`)
  }
} else {
  log(`Critique and Fix skipped: ${CRITIC_REASON || 'the preflight says the critic does not run'}`)
}
const unresolved = owed(critique.findings)
if (unresolved.length) log(`Fix loop exhausted with ${unresolved.length} unresolved blocking finding(s); review runs anyway`)
else if (CRITIC && iterations === 0) log('No blocking findings; fix loop skipped')

function parkCarried() {
  const left = PENDING_MOVED.map(ownerEntry)
  PENDING_MOVED = []
  ALL_UNSTATED.push(...left.map(entry => ({ ...entry, by: `critique-${iterations}` })))
  const stillOwed = owed(critique.findings)
  const guardChanges = union(critique.guardFilesChanged)
  const covered = coveredGuardPaths(disputes, [])
  const undisputedGuardChanges = guardChanges.filter(path => !covered.has(path))
  const forced = [`${stillOwed.length} unresolved blocker/major critic finding(s)`]
  if (undisputedGuardChanges.length) forced.push(`guard paths changed without a dispute record, a spec-named dependency or an additions note covering them: ${undisputedGuardChanges.join(', ')}`)
  if (!lastCheckPasses) forced.push('the last implement/fix result reported checkPasses: false')
  if (!spec.failingForRightReason) forced.push('the spec reported failingForRightReason: false')
  const questions = left.map(questionOf)
  const assumptions = withSurfaced([...(spec.assumptions || []), ...kept, ...assumedByRun, ...ROLE_ASSUMPTIONS], check)
  log(`Parked after critique-${iterations}: ${carriedStillOwed.join(', ')} carried through the round it was owed and is still owed; the review does not run`)
  return {
    slug: SLUG,
    ref: REF,
    verdict: 'request_changes',
    stop: 'carried',
    stoppedAfter: `critique-${iterations}`,
    verdictForcedBy: forced,
    iterations,
    filesChanged,
    critic: { ran: CRITIC, reason: CRITIC_REASON },
    criticFindings: critique.findings,
    criticFindingsByRound: allCriticFindings,
    unresolvedBlockingFindings: stillOwed,
    carriedStillOwed,
    reviewIssues: [],
    reviewIssuesByRound: REVIEW_ROUNDS,
    ticketFindings: [],
    unstated: ALL_UNSTATED,
    unclassified: unclassifiedCount(),
    unquotedReviewIssues: unquotedReviewCount(),
    departures: ALL_DEPARTURES,
    openQuestions: questions,
    assumptions,
    settled,
    disputes,
    guardChanges,
    undisputedGuardChanges,
    specPath: spec.specPath,
    testFiles: spec.testFiles,
    testsAdded: critique.testsAdded,
    checkPasses: false,
    checkOutput: lastCheckOutput,
    namedGates: NAMED_GATES,
    branch: BRANCH,
    verdictRecord: verdictRecord({ slug: SLUG, ticket: REF, verdict: 'request_changes', forcedBy: forced, findings: stillOwed, openQuestions: questions, assumptions }),
    requiredByOrchestrator: [
      'verdict is "request_changes" and no review ran: a critic finding was still owed after the fix round it was given',
      'Commit nothing and open no PR. Park the ticket with each entry of `unresolvedBlockingFindings`, its `breaks` quote with it, as a refused review\'s issues are parked; `openQuestions` holds the critic findings no role classified, for the owner',
    ],
  }
}

if (carriedStillOwed.length) return parkCarried()

phase('Review')
let review = readReview(must(await agent(reviewPrompt(spec, false, settled, recordedOutputs), { label: 'review', phase: 'Review', schema: REVIEW_SCHEMA }), 'review'))
const reviewHalt = reviewStop('review', review)
if (reviewHalt) return reviewHalt
function reviewStop(after, result) {
  const halted = haltAfter(after, result, reviewEntries(result))
  return halted && { ...halted, reviewIssues: reviewIssues(result), checkOutput: result.checkOutput }
}

if (!CRITIC && fixableByReview(review)) {
  iterations += 1
  REVIEW_ROUNDS.push({ round: iterations - 1, findings: blocking(review.issues) })
  const stop = await fixRound(review.issues, [], iterations, YOUNG_FIX_ROUNDS)
  if (stop) return stop
  phase('Review')
  review = readReview(must(await agent(reviewPrompt(spec, false, settled, recordedOutputs), { label: `review-${iterations}`, phase: 'Review', schema: REVIEW_SCHEMA }), `review-${iterations}`))
  const reReviewHalt = reviewStop(`review-${iterations}`, review)
  if (reReviewHalt) return reReviewHalt
  log(`Re-review after the one fix pass: ${review.verdict}`)
}
const builderOutput = lastCheckOutput
lastCheckOutput = review.checkOutput
if (OWED_RED.test(builderOutput || '') && !OWED_RED.test(lastCheckOutput || '')) lastCheckOutput = `${builderOutput}\n${lastCheckOutput}`
const issues = reviewIssues(review)
log(`Review: ${review.verdict}, ${issues.length} issue(s)` + (review.adrSuggested ? `, ADR suggested: ${review.adrSuggested}` : ''))

const guardChanges = union(review.guardChanges, critique.guardFilesChanged)
const covered = coveredGuardPaths(disputes, review.disputeCoverage)
const decidedDependencies = specDecidedDependencies(spec.dependencies, review.dependencyNotes)
for (const p of decidedDependencies) covered.add(p)
const addedRecords = additiveRecords(review.recordNotes)
for (const p of addedRecords) covered.add(p)
const undisputedGuardChanges = guardChanges.filter(p => !covered.has(p))
const blockingReviewIssues = blocking(issues)
const assumptions = withSurfaced(withSurfaced([...(spec.assumptions || []), ...kept, ...assumedByRun, ...ROLE_ASSUMPTIONS], check), review)

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

return {
  slug: SLUG,
  ref: REF,
  verdict,
  verdictForcedBy: forcedReasons,
  iterations,
  filesChanged,
  recordedOutputs,
  critic: { ran: CRITIC, reason: CRITIC ? CRITIC_REASON : `the critic was not run: ${CRITIC_REASON || 'the preflight says it does not run'}` },
  criticFindings: critique.findings,
  criticFindingsByRound: allCriticFindings,
  unresolvedBlockingFindings: unresolved,
  carriedStillOwed,
  unstated: ALL_UNSTATED,
  unclassified: unclassifiedCount(),
  unquotedReviewIssues: unquotedReviewCount(),
  departures: ALL_DEPARTURES,
  reviewIssues: issues,
  reviewIssuesByRound: REVIEW_ROUNDS,
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
  namedGates: NAMED_GATES,
  namedGatesRun: { implement: impl.namedGatesRun ?? [], review: review.namedGatesRun ?? [] },
  parent: PARENT || null,
  deployed: DEPLOYED,
  flag: FLAG,
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
