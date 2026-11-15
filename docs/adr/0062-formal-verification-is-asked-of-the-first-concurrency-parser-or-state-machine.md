---
status: proposed
---
# Formal verification is asked of the first concurrency, parser or state machine, one row per kind

Amends ADR 0028's trigger for formal verification. Builds on ADR 0060 (proposed): pstack builds, Ratchet verifies and ships with deterministic checks. Ratchet has no formal verification of its own: pstack's two skills write and keep the model, and Ratchet holds the result as a gate.

**Today** the row is asked only on `code:formal-model` (`skills/method/application-types.toml:284-286`, `tiers.md:212`), once a model exists, so the method never suggests writing one.

## Decision

**1. One row per kind, no new grammar.** `formal verification` keeps `code:formal-model` and its meaning. Three questions join it, one kind each: `formal verification of concurrency` (`code:concurrency`), `formal verification of untrusted input` (`code:untrusted-input`) and `formal verification of state` (`code:state-machine`). All four are tier 2 and name `just formal`, as two template rows already share `just triggers`.

- Each row is owed by today's rules: free on the first instance, red on the next while `later:` (`scripts/triggers.py:27-36`).
- A row bound for one kind, to a model or to `none`, answers for that kind alone. A single `or` row would not, since `owed_rows` skips a row that is not `later:` (`:266-269`).
- Every reader of one kind per row stays as it is: `owed_rows` (`:271-273`), the drain's red line (`scripts/drain.py:172`), the route's grouping (`scripts/route.py:904-917`), `not_running` (`scripts/conformance.py:2436-2441`) and the safeguard world (`hooks/tests/safeguard_world.py:203`).

**2. The new kind, `state-machine`.** Catalogue text: "a fixed set of named states that something moves between: a status, a phase, a lifecycle, the steps of a protocol".

- **What is counted** is what the row's Pattern matches, one instance per line (`triggers.py:17-19`). "The code changes which state is current" guides whoever writes the row, since a per-line pattern cannot test it. A matched read-only label set is counted, and that false positive is accepted. A machine the row misses, such as a table, arrives unseen until the row is rewritten.
- **It reopens no architecture** (`application-types.toml:58-68`).
- **The fill writes its Triggers row.** The finding "no row for kind" (`conformance.py:967-978`) moves to a check of its own, `trigger_kinds`, which `owed.FILL_FIRST` gains. Not `triggers`: the set is keyed by rule (`scripts/owed.py:73`, `:140-142`; `conformance.py:2341-2343`), and `triggers` also raises the missing `- Life events:` line, the owner's question (`conformance.py:909-920`). `skills/fill/SKILL.md:3`, `:15` gain the row; fill's closing `upgrade.py --apply` then derives the Gates row (`conformance.py:822-828`).

**3. Adoption says it is coming.** The hand-over line on waiting rows (`skills/adopt/SKILL.md:110`) gains one sentence: the first code that runs at once, reads outside input or moves between named states will owe a formal model an agent writes and checks against the code, or a record of why there is none.

**4. What the create skill is asked.** Both pstack skills are agent loads under ADR 0060: the declared build (`pstack@pstack-claude` 0.9.135 to 0.9.137) flags neither; upstream 0.15.36 flags both. Pages write `pstack:create-formal-verification-skill` and `pstack:maintain-formal-verification-skill`. See Open item 1.

- The safeguard's request names the area, the instances that arrived (pstack 0.9.137, `create-formal-verification-skill/SKILL.md:20`), and one requirement: **the check's one command catches a break planted in that code, with the model and the properties unchanged.**
- The skill already asks for a check seen to fail on a scratch break in the real code (`:31`, `:46`). The request adds that the command catches it by itself. A model of the design, the skill's default for ordering questions (`:26`), does not. So the request asks for a check that reads the code: a verifier run on it, or the model joined to it by a check that runs the code against the model. The skill chooses the tool. The owner is asked nothing (ADR 0034). The target and the tool go under **Assumed**.

**5. What the safeguard binds, and when it is done.**

- The model, harness and skill are guard paths in the `formal` group; the owner merges.
- After step 3 the safeguard reads `triggers.py --json` again. Its model is the first `formal-model` instance, so `formal verification` and `symbolic checking` are owed now (`application-types.toml:280-286`; `triggers.py:290-298`). The run binds both, the first to `just formal`, the second to a tool or `none` with a reason, in the same record. See Open item 5.
- **Done** when the read prints `[]`, or only rows a defect ticket of this run names (point 6). With both `formal-model` rows bound, a second model is never red.
- **`none`** on a per-kind row needs its record (`tiers.md:3`, `:20`) and covers that kind only.
- **Design only.** When no check here can read the code, the per-kind row is bound `none` with a record that says so, and a kept design model is bound under `formal verification`, without point 7's proof. See Open item 6.

**6. A defect found at binding.** The create skill reports a property the code breaks (`:32`). The safeguard cannot bind a red gate and changes no product code, so it leaves this row `later:` and makes two branches.

- **Its change** binds the other rows and merges as any safeguard change.
- **The kept model** goes on `formal/kept-<kind>`, which does not merge. A change that brings the model owes `formal verification` and `symbolic checking` (point 5), and the first would be bound to a red `just formal`.
- **The ticket** names the row, carries the counterexample in the code's words, and holds the line `Formal model: defect` with the row and the kept branch (point 8, Signs). Under it the safeguard writes what a fix carries: a fix that adds an instance of the kind merges `formal/kept-<kind>` and binds the row in the same change.
- While the ticket is open the route names it as work, not `/ratchet:safeguard` for that row. The next instance of the kind stays red, as an owed row means.
- Once the fix lands, the safeguard binds from the kept model with the gate green.
- A fix that must add an instance carries the kept model and the bound row: a change that binds its row is green (`triggers.py:35`), and is the owner's merge. For `concurrency`, whose arrival reopens the architecture (ADR 0052), that change is red while the review is owed (`triggers.py:594`, `application-types.toml:65`): the review's record lands first, on its own, and the route already names the review ahead of the safeguard. See Open item 7.
- **The drain may build the ticket.** A build that adds an instance without the kept branch is red at `triggers` and parked owing the kind (`drain.py:172`, `:1050`). The route tells that hold by the parked ticket's `Formal model: defect` line: its row's kind is among the kinds the hold owes. A defect ticket parked for another kind it happens to add keeps the safeguard step. For that hold the route names the step to change the ticket to meet what the drain parked it for (`route.py:474`), not `/ratchet:safeguard` (`:472-473`), and the ticket's own text says what to carry. The hold lifts as any parked ticket's does, once the ticket's text changes (`scripts/queue_notes.py:173-178`). That parked-ticket path is the only way it stalls.
- Nothing is weakened or quarantined (`tiers.md:148`).

**7. What the model mirrors, and the proof.** The binding gains `## Formal verification`, with three lines: `- Model:` (globs of the models, harness and skill), `- Mirrors:` (globs of the code they describe) and `- Synced: <commit>`.

- `[proofs]` gains `code-departs-from-model`: "a change to the code the model mirrors that breaks a property the model holds, with the model left as it was". The three per-kind questions require it.
- `project_bite.unproven` checks only the name (`scripts/project_bite.py:784-790`). Conformance adds, per bound per-kind row, a bite of its gate that claims the proof, with every fault path matching a `- Mirrors:` glob and that kind's Triggers Paths, and no `- Model:` glob. That ties each kind to its own code, and `- Mirrors:` to what the proof breaks.
- Conformance is also red on a glob that matches no tracked file, on a `- Model:` path the default branch's `formal` group does not hold, and on a `- Synced:` commit that is not an ancestor of `HEAD`.
- A `- Synced:` commit the clone does not hold is red, and the finding names the fetch that brings it. The templates check out full history (`skills/adopt/templates/.github/workflows/ci.yml:22`, `merge.yml:41`, `deep.yml:58`), so their runners hold it.

**8. Upkeep.** The model is **behind** when a first-parent commit on the default branch that `- Synced:` cannot reach touches a `- Mirrors:` path. Renames are split (`--no-renames`, as `scripts/gatelib.py:125`; `touched` gains it, `triggers.py:247`), so a move out of a mirrored path shows its old path. Merge order and dates play no part. The route reads it where it reads owed rows (`route.py:823-828`), with one path-limited log that stops at its first hit. A clone without the `- Synced:` commit reads as behind and says why. `- Synced:` is the default-branch commit the round checked, its branch's fork point, so it stays an ancestor whatever merge the forge makes.

| State | When | The route names |
| --- | --- | --- |
| in sync | no such commit | nothing for it |
| behind | such a commit, and neither state below | `/ratchet:safeguard` beside the build, after a review, an owed row or architecture; never red |
| ticket open | an open ticket's `Formal model:` line says `round` | the ticket, as work |
| round waiting | a `formal/upkeep-` branch the default branch does not hold, and no ticket open | that change, waiting for the owner's merge |

- **Signs.** The route reads these facts from a ticket line and a branch name, which the safeguard writes. See Open item 8.
  - Every ticket the safeguard files holds one line, read as a ticket's `Status:` and `Blocked by:` lines are (`scripts/feature_preflight.py:99-109`): ``Formal model: defect `<row>`, kept on `<branch>` `` for point 6, and `Formal model: round` for a round, with ``, kept on `<branch>` `` when the round kept an update.
  - Every branch the safeguard makes for a model is under `formal/`, and `<commit>` is the checked default-branch commit: `formal/upkeep-<commit>` for a round's change, `formal/kept-<kind>` for a model kept at binding, `formal/kept-upkeep-<commit>` for a round's update that found a bug.
  - Round waiting is read as the route reads an unmerged `records/` branch (`route.py:770-778`). A `formal/kept-` branch never waits for a merge.
- **A round** is the safeguard's upkeep step, which loads the maintain skill (outcomes at `maintain-formal-verification-skill/SKILL.md:20-27`). In sync or updated: the upkeep change, on `formal/upkeep-<commit>`, sets `- Synced:` to the checked commit, with any model change, and the state is round waiting. Bug: the update goes on `formal/kept-upkeep-<commit>` and a ticket carries the counterexample and names that branch. Blocked: a ticket carries the reason and `- Synced:` stays (`:51`). Both lead to ticket open.
- **Ticket open wins over round waiting.** A bug round leaves an unmerged update beside its ticket. The update is kept, not offered: its `just formal` is red until the fix lands, and no pull-request run shows that, since tier 2 runs only on the default branch (`skills/adopt/templates/.github/workflows/deep.yml:2-8`).
- **Leaving.** The owner's merge leads to in sync, or to behind when mirrored code landed meanwhile. A change closed unmerged, with its `formal/upkeep-` branch deleted, or a closed ticket, leads to behind. The round after a closed ticket starts from the kept update on its branch, so finding the same bug again costs a rerun and not a rewrite.
- **No loop.** Every round ends waiting on a merge or a ticket.
- **Cost.** Every round, no-effect included, edits guard paths (`- Synced:`, and the skill's sync commit, `:51`), so each is one owner's merge per batch of mirrored changes. See Open items 3 and 4.
- **Red.** A broken property is red on the next tier 2 run after the merge, not at once (`skills/adopt/templates/justfile:76`; `project_bite.py:122-127`). The merge judge then leaves every merge to the owner (`tiers.md:28`). That merge touched mirrored code, so the model is behind and the route names the upkeep. Its round reads the red through the maintain skill: drift lands as a model change, a bug as a ticket. A red with nothing behind, such as a tool version, is fixed as any tier 2 red.

**9. Tier and cost.** The rows are tier 2 (`tiers.md:28`).

- **Bounds live in the model.** The recipe runs it whole and a time-out is red, since a cut-off check proved nothing (`create…/SKILL.md:33`). The ceiling is the only clock.
- **Its own `[ceilings.seconds]` key.** One more state can multiply the time, and each raise is a `Relaxes:` record and an owner's merge (`tiers.md:144`).
- **The default branch runs it whole** (`:138`, `:146`). No pull-request run: `changed.py` filters no paths (`scripts/changed.py:38-65`).
- **Each proof bite runs the gate three times** in `bite-all` (`project_bite.py:64-69`).

**10. ADR 0060 and its F1 land first.** The `harness` group and the fixed permission list deny `.claude/skills/**`, matched before `formal` (`skills/adopt/templates/guards/manifest.toml:51-65`, `:105-111`). F1 excludes the bound skill path of each harness. `- Model:` names wherever the harness's skill landed.

## Refutation criterion

This is wrong if any of these is seen:

- **The early ask buys nothing.** In the first ten repositories owed a per-kind row, more than half of those rows are bound `none` with no model written. A `none` whose record says the match was not a state machine is left out here and counted under "the kind is misread".
- **Code-reading is too dear.** Among the per-kind rows where a model was written, more than half are bound `none` as design-only.
- **It costs more than it finds.** In `.ratchet/timing.jsonl`, `formal` is the slowest tier 2 gate in most nightly runs (three runs per proof bite counted), or its time-out turns tier 2 red more than once a month, while no ticket names a bug it found.
- **Upkeep fails.** A maintain report says an old pass was on a stale description (`SKILL.md:63`) for commits the route never read as behind, or the route names two rounds with no mirrored commit, merge or closed ticket between.
- **F1 was skipped.** A transcript or the guard event log shows a denial on the bound skill path.
- **The kind is misread.** In the first ten adoptions, `triggers --count` shows the row counting every enumeration, or none where a status changes in place; or more than half of the `state-machine` rows are bound `none` because the match was not a state machine; or a defect ticket names a state machine the row missed.

## Considered options

- **One `or` row, with a review on each further kind's first arrival.** Rejected: a grammar change (`application_types.py:57-65`), five readers changed, and a prose review where a row is a gate.
- **Ask at adoption.** Rejected: a model written before its code describes a design the first feature changes.
- **Keep `code:formal-model` alone.** Rejected: it fires only once a model exists.
- **"Behind" from the order of commits.** Rejected: a late owner's merge clears drift it never read.
- **Drop the proof for design models.** Rejected: the row would claim a mirror no gate checks.
- **Owner commands for the pstack skills.** Rejected under ADR 0060.

---

## For the builders

**The models turn first** (`docs/model/README.md`). This record names them and does not specify them.

- **Safeguard model** (`docs/model/safeguard.md`). Scope stays one kind, with one case the model's author settles either way: a binding that brings the first instance of another kind, whose rows are then owed in the same run (point 5), is a second kind in scope or a line under Left out. It gains "binding found a defect": the row stays owed, the safeguard's change merges and the kept model does not, and a change binding the row with the fix is green once no review is owed.
- **Route model** (`docs/model/route.md`). It gains behind, round waiting, ticket open ahead of round waiting, and a defect ticket ahead of the safeguard, parked by the drain or not. "In sync, or no model" joins the states at rest.

**Scripts.** `conformance.py`: `trigger_kinds`, the section's checks, the absent `- Synced:` commit, the proof tie. `owed.py:73`. `triggers.py`: `--no-renames`, the behind reading. `route.py`: the upkeep step, the defect ticket, the `Formal model:` line and the `formal/` branches.

**Catalogue and pages.** `application-types.toml`: the kind, the proof, three questions. `tiers.md`: `:113` without slashes, with the proof and the red reading; rows `:204`, `:208` and a new `code:state-machine` row; `:276` four proofs. The binding template's rows and section, `quality/bites.toml:35-40`, `skills/safeguard/SKILL.md` (request, reread, defect ticket and its line, the two branches, upkeep step), `skills/fill/SKILL.md`, `skills/adopt/SKILL.md:110`, the manifest hint at `:110`.

**Tests.** `hooks/tests/test_application_types.py:60-64`; behind across a rename and a late merge; the proof tie; `trigger_kinds` fill-first, a missing `- Life events:` line not; a bite fixture planting a departure in mirrored code.

## Open for the owner

Each item is a default taken overnight, built to its recommendation. Item 2 was settled by the owner (below).

- **1. The slash form.** Default taken overnight: an agent load. Recommendation: keep it; the hand-over says plainly that a model was written. Cost of the slash: an exception to ADR 0060, for a keystroke that starts what the agent starts anyway.
- **3. Drift: proposal or red.** Default taken overnight: a proposal. Recommendation: keep it. Cost: one owner's merge per round; red costs one per second change to mirrored code.
- **4. No-effect rounds without the owner.** Default taken overnight: no; `- Synced:` lives in the binding. Recommendation: keep it until ten repositories show how often rounds find no effect. The alternative, a merge-judge rule passing a change that only moves `- Synced:` forward, costs one exception in the judge and lets a wrong "no effect" stand until the next mirrored change.
- **5. `symbolic checking` on the first model.** Default taken overnight: bound in the same run. Recommendation: keep it. Cost: one more row, usually `none` with a reason, wherever a model is written. A trigger of its own needs a kind for contracts.
- **6. Design-only models.** Default taken overnight: the per-kind row `none`, the model under `formal verification`. Recommendation: keep it. Cost: the design model runs, and nothing claims the code mirrors it.
- **7. A fix that needs another instance.** Default taken overnight: it carries the kept model and the bound row. Recommendation: accept. Cost: one change mixes fix and verification, against the maintain skill's advice (`:65`), and it is the owner's merge. When the drain's build ignores the ticket's own instruction and is parked, the owner is told to change a ticket whose text is already right, and the hold lifts only on a text change: one owner's edit to release a ticket the agent misbuilt.
- **8. The signs the route reads.** Default taken overnight: the `Formal model:` ticket line and the `formal/` branch names of point 8. Recommendation: accept; they follow the ticket lines and the `records/` branches the route already reads. Cost: a ticket whose line is edited away reads as ordinary work, and a kept branch renamed by hand is not found by the next round.

## Settled by the owner, 2026-11-15

- **Rows the upgrade adds for a kind already here are owed at once** (open item 2). When an upgrade adds a per-kind row for a kind the repository already has, the row is owed immediately: the route names the safeguard, and the next instance is red until the row is bound. The upgrade writes `later: <trigger>` for a countable trigger even once fired. This amends `dropped_row` (`conformance.py:791-792`, `:812-814`): an owed `later:` row is red on the next instance, not green. Cost: `state-machine` is in nearly every repository, so nearly every upgrade costs one safeguard run and one owner's merge, and the next status set is red until then. Many of those first runs bind `none` because the match was a label set and not a state machine (point 2). Default taken overnight for the refutation: the first criterion leaves those out, and "the kind is misread" counts them. Otherwise those rows sit in the ledger with no step naming them.
