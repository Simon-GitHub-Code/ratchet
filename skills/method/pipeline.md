# The feature pipeline

A feature is built by roles with fixed sight lines, because one agent grading its own work is confirmation, not criticism. The `/ratchet:feature` skill runs them through the `pipeline` workflow the plugin ships (`workflows/pipeline.js`) once its preflight is green: once for a ticket, and once for each ticket when it drains the queue ([the drain](#the-drain)).

## Roles

Each role does its work with the other plugins' skills. The method fixes what a role sees, what it returns and when it is done.

- A skill that spawns sub-agents of its own (`code-review`) is followed in-process. The pipeline fixes the agent count, so the role is the single reviewer its skill describes.
- The roles call model-invoked skills only (`tdd`, `code-review`, `impeccable`, `diagnosing-bugs`). A user-invoked one is the owner's keystroke, and the harness refuses it to an agent. [route.md](route.md)

### Spec

- **Works with:** `mattpocock-skills:tdd` for what a good test is and the seams under test; `mattpocock-skills:codebase-design` for placement, seams, and deep modules over shallow wrappers.
- **Sees:** the ticket with its comments, its parent's `## Conjecture`, `## Operability` and `## Answers`, whether the project is deployed as the preflight read it, the glossary, the design brief, the relevant ADRs, the architecture ADR and the declared layers, and code only as far as placing the feature needs.

**What `docs/features/<slug>/spec.md` holds:**

- numbered Given/When/Then criteria: one for every promise the ticket makes, and, for each thing users or other systems send the product, one each for an empty, an oversized and a hostile value;
- glossary terms;
- a Design section: placement tied to the architecture ADR and the declared layers, the seams it adds or changes and why each module behind one is deep, and what was considered and rejected;
- the seams under test;
- the telemetry that shows each criterion working;
- the feature flag that gates the behaviour, or why there is none;
- a test plan mapped to criteria;
- the assumptions made;
- `## Neighbouring cases`: for each set of values a neighbouring test repeats its checks over, the test, the values, and which of them the new behaviour holds for, every value unless an assumption says otherwise;
- out of scope.

**The failing tests.** Each fails for the right reason, and its failure content is quoted in the result and in the spec. The spec runs `tier0` and only its own tests, never `check`.

**What it decides and what it asks.**

- An undecided point is an assumption. The spec takes the conservative, reversible answer, builds on it, and returns it in `assumptions` with its refuter. The pull request lists each one for the owner to counter. An assumption is by definition one that can be taken back, so it does not hold a merge: the owner may read it after the change has merged.
- It decides each case of an input the ticket leaves open with the narrowest safe answer (an empty value: nothing is saved and nothing breaks; an oversized one: refused with a message, saving nothing and keeping what the user typed; a hostile one: kept as plain text that does nothing, shown as text and never run, followed or used as a path), and lists it as an assumption. A case where that answer takes something from the user, such as refusing an empty value that clears a note, is a `direction` question.
- A point is an open question in two cases. It is `irreversible` when a wrong guess cannot be taken back (stored data lost, a public contract fixed, money spent, a security or privacy rule weakened). It is `direction` when the options are different products, which [the fork test](#the-fork-test) decides. A choice the user could not tell apart is the role's own.
- A promise of the ticket it believes cannot or should not be built as written is a question under the fork test too, never one it leaves out.
- A change that implies a change to the architecture (a new layer, a new kind of storage, a new process boundary) is a `direction` question under the fork test, never decided silently or buried in Design. Its options include the one that keeps the architecture as it is, when the feature can be built within it, and the recommended one comes first, with why.
- Each question carries its kind, two to four options with the recommended one first, and why. The role never asks the owner to confirm.
- Open questions stay open.

**The amend.** When the ticket check finds a gap, the same role amends `spec.md` and its tests once, with the findings in hand:

- the smallest change that closes each gap;
- nothing the gaps do not touch: every other criterion keeps its wording exactly;
- nothing another ticket of the feature promises.

**The estimate.** It estimates the whole change (spec, tests, the critic's share, implementation, records) against the change-size ceiling, and proposes a split when the ticket cannot fit. It omits the estimate when the binding binds no change-size gate.

Done when `spec.md` holds every part listed above, the failing tests exist and each fails for the right reason with its failure quoted in the result and in the spec, tier 0 is green and the new tests are red, the whole change is estimated or the binding binds no change-size gate, and, when the ticket check found a gap, the amend has closed each one.

### Ticket check

- **Works with:** no skill. It reads the ticket, its parent's three sections and `spec.md`.
- **Sees:** the ticket as the base branch holds it, with its comments and `## Answers`; its parent's `## Conjecture`, `## Operability` and `## Answers`; whether the project is deployed as the preflight read it; `spec.md`.
- **Never sees:** the code, the tests, every other record.
- **Returns `ticketPromises`:** each promise of the ticket, in its words, read as `covered` by the criteria that carry it, or as `dropped`, `bent` or `assumed` with the spec line. A sentence about how the method treats the work (what a gate charges) is no promise.
- **Returns `acceptedInputs`:** for each thing the change lets users or other systems send the running product, its plain name (what a person who cannot read code calls it, such as "the link you paste") and the criterion that decides an empty, an oversized and a hostile value, or none. A header the change checks to guard itself is no input.

Done when every promise of the ticket has its reading in `ticketPromises`, and every thing the change lets users or other systems send has its entry in `acceptedInputs`.

### Implement

- **Works with:** `mattpocock-skills:tdd`, one slice at a time; `impeccable` (`shape`, then `polish`) for any user interface.
- **Sees:** the spec and its tests, the paths named, and each promise the run kept from the ticket and each input case it assumed after the amend.
- **Never sees:** the ticket, the spec author's reasoning.
- A test that cannot be made to pass is left red.

Done when every listed test passes and `check` exits 0, or the test that cannot be made to pass is left red and the result says so.

### Critic

The critic runs only once the binding's `- Life events:` line declares `depended-on`. Before that people outside do not rely on the project, and an adversary on every ticket costs more than what it would find: a ticket is built by spec, the ticket check, implement and review. The merge rule is unchanged. A finding the review opens is still open, and the review still judges the spec's tests and the change against the records.

- **Works with:** the method's rule of evidence, as the one adversary; `impeccable` `audit` and `critique` for any user interface. `/pstack:interrogate` is the owner's to type on the branch afterwards, never the critic's to imitate.
- **Sees:** the spec, its tests, the changed files, the fitness tests, the fuzz harness.
- **Never sees:** the implementer's notes, the ticket.
- **Attacks:**
  - property and edge-case tests written;
  - changed-code mutation run, or "not applicable" and why where the binding names no mutation gate;
  - fuzzing run, or "not applicable" and why where the binding names no fuzz harness;
  - every new entry point attacked and overloaded, its telemetry read for a secret, its flag flipped;
  - the fitness tests sabotaged in a scratch copy and seen to bite;
  - vocabulary and contracts checked;
  - `guard-diff` run.
- Every test the critic added that passes, and every one that fails because of a real defect, stays, in the one flat critique directory the binding's Critique tests section names, or `tests/critique/` when it names none (`<slug>.<layer>.critique.test.<ext>`), never beside the code.
- The critic runs `tier0` and its own tests, not `check`.

Done when every attack above is done, every finding carries the command and output that proves it, and `attempted` lists every command run, so "nothing found" refutes only the attempts that would have hit.

### Review

- **Works with:** `mattpocock-skills:code-review`, its Spec and Standards axes and smell baseline; `mattpocock-skills:codebase-design` for the design axis.
- **Sees:** the ticket with its comments and `## Answers`, its parent's `## Conjecture`, `## Operability` and `## Answers`, whether the project is deployed as the preflight read it, the readings the ticket check settled, the spec, the uncommitted diff, `AGENTS.md`, the glossary, `docs/adr/`, the binding, `docs/disputes/`, `DESIGN.md`, generated contract artefacts.
- **Never sees:** the implementer's notes, the critic's findings.
- **Judges:**
  - axis A, the spec: criteria narrowed, tests weakened, behaviour added;
  - axis B, the standards, the telemetry and the flag among them;
  - axis C, guard paths;
  - axis D, the ticket, and axis E, the inputs: starting from the readings the ticket check settled, and not arguing them again, whether the change as built still keeps each promise and decides each input case as `spec.md` states, or as the settled reading states where the run kept the promise from the ticket or assumed the case;
  - axis F, the design, with `mattpocock-skills:codebase-design`'s vocabulary: the change against the architecture ADR and the declared layers (a layer or seam violated, a module that bypasses a declared seam, logic in the wrong layer, a new dependency direction, a shallow wrapper, an architecture decision the spec did not raise).
- It approves only with no blocker or major issue, no dropped promise, no undecided input case, no undisputed guard change, and `check` exit 0.

Done when axes A to F are judged and the verdict approves only under that rule.

### The roles that read the ticket

Three roles read the ticket: the spec role, which writes the spec from it; the ticket check, which reads only the ticket, its parent's three sections and `spec.md` before anything is built; and the review, which reads the change against the readings the check settled. Every other role works from the spec alone, so a spec that drops or bends a promise of the ticket would be built to the letter and pass every other check.

- Each of the three reads a local ticket file as the base branch holds it (`git show <base>:<path>`), not the copy in the working tree that an agent of the run could have edited.

### Severity

Every grade the critique and the review apply, blocker, major or minor, is in one list: `SEVERITY` in `workflows/pipeline.js`.

## The loop

The script is a pure function of its arguments and the agents' structured results, so it resumes cleanly.

0. **Preflight.** `scripts/feature_preflight.py` on the ticket checks that:
   - the ticket exists, is open and was not split;
   - `## Conjecture` and `## Operability` are stated;
   - the ticket's own `## Size` passes no product ceiling;
   - the agent-ready label is present, and no ticket it is blocked by is still open;
   - the tree is clean and `HEAD` is on the branch;
   - in an adopted repository, a commit would run tier 0 (`scripts/hooks_installed.py`, which prints the repair when the commit hooks are missing, since the pipeline's commits would otherwise skip tier 0);
   - no owed item an agent writes from the repository alone is still unwritten.

   The skill runs it before the workflow and spawns nothing while it is red. The workflow runs it again as its first stage, through one low-effort agent that types the command and relays its JSON.

   - Red stops the run with `verdict: "blocked"` and, per failure, what produces the missing piece.
   - Red on `size` alone is the exception: the orchestrator splits the ticket instead, as [`/ratchet:feature`](../feature/SKILL.md) step 1 says, and builds the first part that is free.
1. **Spec.**
   - A non-empty `openQuestions` stops the run with `verdict: "blocked"`. The orchestrator asks the owner each one as the registered product question `feature.open-question` ([questions.toml](questions.toml)), writes the answer on the ticket under `## Answers`, and builds the ticket again.
   - A non-empty `guardEdits` stops the run the same way: criteria that need a guard path changed (an import contract, a shared test fixture, a row of the binding's Architecture table, the definition of a term the glossary already has) cannot be met by a run with no owner at the keyboard. The owner lands those edits first, or builds the ticket with `/pstack:poteto-mode` while present and merges the change.
   - A word the glossary lacks is not a guard edit where the manifest marks the glossary `edit = "append"`: the spec role adds the term with its *Avoid* list, the guard lets an addition through, and the pull request lists it.
2. **Ticket check.** A gap the check finds, a promise `dropped` or `bent` or an input case no criterion decides, goes back to the spec role, which amends `spec.md` once while no code exists, and the check reads it again. After that the run decides, and the script asks the owner nothing.
   - **A promise still `dropped`** is kept from the ticket. The readings the script settles say, in the ticket's words, that the app does it. The implementer builds it with a test, and the review judges the build against it. It joins the `assumptions` as "the plan left this out, so it was built as your ticket says", which the owner overrules as he would any assumption. A ticket line about how the change is built, such as a tool to run, is kept the same way.
   - **A case still undecided** takes the safe answer the same way (an empty value: nothing is saved and nothing breaks; an oversized one: refused with a message, saving nothing and keeping what the user typed; a hostile one: kept as plain text that does nothing, shown as text and never run, followed or used as a path), and joins the `assumptions` under the input's plain name.
   - **A promise still read as `bent`** is a reading the two roles do not share. It joins the `assumptions` and holds nothing.
   - **No question comes from the check.** The owner is asked only the spec role's own open questions, such as a ticket that contradicts itself or a promise the role believes cannot or should not be built as written, and those still stop the run before the build.
   - **At the review, a `dropped` promise or an undecided case** means the build lost what the spec had. The script files it as a major issue, the verdict record carries it as unresolved, the merge judge refuses, and the result's `ticketFindings` go to the spec role of the next run as `args.ticketFindings`.
   - **A `bent` or `assumed` reading at either role** joins the `assumptions`, which the pull request lists for the owner to overrule. It holds no merge.
3. **Implement.** The change-size gate counts product code and tests apart: the binding's `## Test paths` section says which files are tests, and each side has a ceiling. The script reads the gate's numbers after the spec, after the implementation, after each fix round and after the Reconcile of a judge-again.
   - Product lines over their ceiling stop the run as `blocked`, too large. Nothing still to come adds much product code, so there is no margin.
   - Test lines over two thirds of the test ceiling stop it while the critic's tests are still to come, and over the whole ceiling before the project is `depended-on`.
   - The spec stage also adds up the whole change before it returns (product: the lines measured, the implementation, the records; tests: the lines measured and the critic's). When either sum passes its ceiling, it stops as `blocked`, too large, with a proposed split.
   - A fix round or a Reconcile over the product ceiling (or the whole test ceiling, the critic's tests being in by then) stops the run the same way, as `tooLarge` with the stage `fix round N` or `the reconcile`, before the re-critique or the review. The drain splits it as for any other too-large stop.
4. **Critique,** once the project is `depended-on`; skipped before.
5. **Fix loop,** once the project is `depended-on`. While a blocker or major finding remains that no kept passing test resolves, or a test the critic kept is failing (whatever the finding's severity), up to two rounds: a *fresh* fix agent (it has not seen the previous implementer's reasoning), then re-critique, each finding saying by `carries` which earlier finding it still is, plus a search for new breakage.
   - A blocker or major carries `breaks`: the record it breaks and the sentence it quotes. The role runs `scripts/quotes.py` over its findings and returns the output. A finding whose quote the output does not show found is no finding: the script moves it to `unstated`, and no fix round starts for it.
   - A finding that is carried into the next critique and is still owed after the round it was owed in starts no further round: the run ends there, refused with it unresolved, and the review does not run. A new blocker or major still gets the second round.
6. **Review.** Before the project is `depended-on`, a review that lists fixable blockers, whatever its verdict says, gets one fix pass: the same fresh fix agent, given those issues, then the review again. A fixable blocker is a blocker or major issue in the change itself; not a promise of the ticket the change lost, not a question for the owner, not a too-large stop. If the second review approves, the run goes on as approved. If it still refuses, the ticket is parked as before.
   - A review with any `dropped` promise or undecided case gets no fix pass at all, even beside fixable blockers.
   - A `dropped` promise or an undecided case at the review, and a `bent` or `assumed` reading at either role, follow the rules under step 2.
7. **Forced verdict.** The script forces `request_changes`, and `verdictForcedBy` says which, when:
   - blocking findings are unresolved;
   - the last implement or fix reported `checkPasses: false`;
   - the review's own run of `check` on the final tree was not green;
   - the spec's tests did not fail for the right reason, or a test file has no quoted failure;
   - the critic ran and found nothing and attempted nothing;
   - a guard path changed without a dispute naming it, unless it is a dependency the spec named or a record whose `guard-diff` note is `additions:`.

8. **Cases no record states.** The critic, the builders and the review return `unstated`: each case where no record says which behaviour is right, put through [the fork test](#the-fork-test).
   - A case the test gives a role is an assumption: it joins `assumptions`, and the next critic and the review judge against it.
   - A case it gives the owner stops the run `blocked` after the role that returned it, with the question in `openQuestions`. The builders also return `departures`: a departure from a criterion of `spec.md` stops the run the same way, and a departure from an assumption goes through the fork test and, where the role has the choice, is the amended assumption.
   - The work of a blocked run is set aside, as a stash by `/ratchet:feature` and as a file under the git directory by the drain, and the ticket is built again from the preflight once the owner has answered.

**Agents.** Each role is one agent. The ticket check adds two when it finds a gap (the amend and the second read), the fix loop two per round (the fix pass on a young project's review is one such round, which re-reviews instead of re-critiquing), and a change judged again behind another merge two more (reconcile and review). `AGENTS_AT_MOST` in the script holds the most a run spawns before and after `depended-on`.

- Agents leave every change uncommitted. The orchestrator commits and publishes.
- An agent blocked by a guard path writes a dispute and returns it. A guard path an open dispute in the change names is not a finding of the critic or the review, so the verdict stands on the rest (ADR 0067).

**A run that dies.** A run in which an agent dies throws and returns no result. The drain resumes it once, and on a second error closes the ticket with the retryable reason code `errored`, which becomes the owner's only after three closes in a row.

## The fork test

It decides whether an undecided point is the owner's. `/ratchet:conjecture` applies it to every behaviour of a spec or ticket before anything is built, and the Spec role's prompt carries a copy (below), so the owner is asked before a drain what a drain would stop on.

The rule, which says what a product question is: ask the owner when a choice decides what becomes of something of the user's (kept, deleted, merged, duplicated, overwritten, shared), who may see or do it, or what the user can do at all, and nothing the owner said or answered settles it.

- Ask all of a step's open questions in the user's words, in one message, each with your recommended answer first and why, so the owner can accept them all at once.
- A choice an agent wrote down earlier (a spec, a conjecture, an **Assumed** line) does not settle it.
- Assume, and list under **Assumed**, anything a later change reverses without loss to the user: order, wording, layout, timing, limits, and, within the architecture the owner chose, tools and how it is built.
- The architecture itself is the owner's, chosen at adoption and at `/ratchet:architecture`.

To apply it, describe the product to one of its users after each option, in the user's words and with no code in the sentence, and read the two descriptions against the rule.

What the owner said or answered is what a record keeps as theirs ([records.md](records.md#questions-assumptions-and-standing-answers)). A user story an agent wrote settles nothing unless it quotes them. Where their words do settle a choice, the spec follows them. A criterion that contradicts them is a defect, and the conjecture says so.

The owner's, for example: "When you paste a link you already saved, should it stay one item or become a second one?" (what becomes of their item: duplicated or kept); "When you paste a link you already marked read, should it come back as unread or stay read?" (what becomes of it); "May anyone with the link close a poll, or only the person who made it?" (who may do it). The agent's: the order of a list, the words on a button, a status code, where a check lives, a limit with one plainly safe value, how long to wait for a slow page.

### The Spec role's copy

The pipeline's Spec role reads the test from its prompt in `workflows/pipeline.js`, which a test holds to the text below. That copy is older and asks more than the rule in one place: it also sends a difference only in what the user sees, such as the expired-link page, to the owner. It never asks less. Bringing the prompt to the rule is a change to the pipeline of its own.

The test: describe the product to one of its users after each option, in the user's words and with no code in the sentence. When the two descriptions differ in what the user sees, what they can do, who may do it or what becomes of something that is theirs, the owner chooses. When the user could not tell which option was built, the agent chooses.

- "Should an expired link show a page that says it expired, or send the visitor to the home page?" The visitor sees a different thing.
- "May anyone with the link close a poll, or only the person who made it?" Who may do it.
- "When a voter answers twice, does the second answer replace the first or is it refused?" What becomes of their answer.
- Which HTTP status answers a reserved code. No user's product changes with it.
- Whether the expiry is checked in the store or in the route. The user cannot tell.
- A limit the ticket leaves unstated where one value is plainly the safe one: take it, and the owner relaxes it in a later change.

## The orchestrator

The orchestrator is the `/ratchet:feature` skill's session. It runs the preflight, creates the branch and runs the workflow. It reads only the ticket, the workflow's structured result and the spec: reading the implementation would anchor it on one agent's reasoning and defeat the independence the pipeline exists for. When the result is not enough to decide, add the missing field to the schema.

1. **Check the result.** Check `verdict === "approve"`, empty `unresolvedBlockingFindings`, and a green tail in `checkOutput`. Otherwise commit nothing. Stash what the agents left with `git stash push -u -m "<branch>: <verdict> run"`, since the route holds a ticket only on a `blocked` stash of that name. Switch back to the base branch, and settle each field the result holds as [`/ratchet:feature`](../feature/SKILL.md) step 4 says. Two of them build the ticket again with the ticket unchanged: `ticketFindings`, passed back as `args.ticketFindings`, and a `triggers` red, after the owner is told to type `/ratchet:safeguard`, or `/ratchet:architecture` for an owed review.
2. **Report every dispute** to the owner in the hand-over, in plain words: what the rule stopped and what changing it would allow. It is a report, not a question. The owner answers in their own words if they want the rule changed, and any gate change is theirs.
3. **Raise the floors.** Run the `ratchet` entry point when it is bound, and confirm `ratchet-lowered` stays green. Commit everything the work tree holds (`git add -A`) with the raised `quality/` files on the branch, with a message naming the slug: the roles' `filesChanged` can miss the spec and a record they added. The drain commits the two apart, as [`/ratchet:feature`](../feature/SKILL.md) says. Without this step a floor recorded at adoption never moves, and coverage can fall back to it with every gate green.
4. **Commit what else the change holds.**
   - If an ADR is suggested, write it under the next free number. One that must supersede another or carry a `Relaxes:` line waits for the owner, in an interactive session where the guard can ask.
   - With no forge (the binding's Forge section names a change template instead of a pull request template), the filled template is `.scratch/changes/<branch>.md` (the branch's `/` written as `-`). Commit it on the branch with the local ticket's `Status:` line set to `resolved`, the local form of `Closes #N`, so the merge resolves it.
5. **Write the verdict record.** Put the result's `verdictRecord` on stdin of the `merge-ready` entry point's `record` form (`just merge-ready record`), and commit the `docs/features/<slug>/verdict.json` it writes alone, as the last commit. The record names the tree the review saw, so any commit after it sends the merge to the owner.
6. **Publish.** Push, and open the pull request with every section of the repository's template filled ([records.md](records.md)), every entry of `recordNotes` (each added term, *Avoid* item and ADR) listed in the Decisions section. When the critic did not run, the pull request says so and why, from the result's `critic`. With no forge nothing is pushed.
7. **Leave the merge to the judge.** The merge judge decides who merges, never the orchestrator ([guards.md](guards.md)). Run the `merge-ready` entry point and report its lines: `merge: agent`, `merge: wait` with every reason, or `merge: owner` with every reason.
   - With no forge, run the `merge` entry point with the branch (`just merge <branch>`).

   Merge nothing by hand.

## The drain

`/ratchet:feature` with no ticket builds every agent-ready ticket. `scripts/drain.py` holds everything about it that needs no judgement, and the skill runs the pipelines.

- **Merging is serial.** One result is settled at a time.
- **The judge merges.** With a forge, the skill opens the pull request and watches it.
- **A ticket that cannot go on is parked.** An open question, a guard edit, a dispute, a verdict that is not an approval or a `merge: owner` closes the ticket with the reasons as given, and the others go on. `merge: wait` closes the ticket as `waits` with its reasons; when every reason is mechanical (such as `unproven`), a later drain retries it up to the retry limit. A too-large result is not parked, though its verdict is `blocked`: the agent splits it, as [Too large](../feature/SKILL.md#too-large-the-agent-splits-it) says, unless the ticket is itself a part.
  - A `triggers` red, another instance of a kind whose safeguard or architecture review is owed, parks it with a reason naming `/ratchet:safeguard` or `/ratchet:architecture`.
  - No role writes the review: it is the owner's next command, and a decision record written in the same change as the instance would not count.
- **One report.** `drain.py report` writes `.ratchet/queue/<date>-<time>.md`. The owner's questions come after it.
- **An interrupted drain.** The next one starts with `drain.py clean --all`.
