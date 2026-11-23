---
status: accepted
---
# Expect tests and recorded outputs

Settles issue #300. Builds on ADR 0028 (life events and triggers wait until the project reaches the stage they guard), ADR 0050 (reading declarations at the merge base), and ADR 0073 (per-suite test configuration under `## Tests`).

**What was asked.** Nothing in adoption, the method docs, or the pipeline prompts steered a role toward expect (snapshot/golden) tests where behaviour shows as readable output, and re-accepting an existing expected output passed every gate because test files are not guard paths.

## Decision

**1. Provision and prompt, with no new gate.** `/ratchet:adopt` provisions a recorded-output facility (or notes `built in` where the test runner includes one) beside the test runner and property-testing library, names it in `## Tests`, and declares recorded-output locations in `## Recorded outputs`. Pipeline roles (spec and critic) are steered to write expect tests where behaviour shows as readable output, and tier 1 lists expect tests alongside unit, property, and fitness tests; no gate requires expect tests.

**2. Hand-written expected text at spec time; recording only when too large to state.** The spec role writes expected output text by hand from the ticket so new tests fail first for the right reason, never recording from a run. The spec's expected text is a fixed input to implement and fix. Only where expected output is too large to state by hand may implement or fix record with the recorded-output tool, returning every such path in `recordedOutputs` for the reviewer.

**3. Review holds recorded outputs to criteria; the merge judge holds existing ones once `depended-on`.**
- The review holds every recorded output added-by-recording or altered to a justifying criterion in `spec.md`. A recorded output added-by-recording or altered with no justifying criterion is a `blocker` on the spec axis.
- Once `- Life events:` declares `depended-on`, altering a recorded output that existed at the merge base sends the merge to the owner (`expect-output` in `scripts/merge_ready.py`), like `unflagged` for new surface. Before `depended-on` is declared, the merge judge notes and lets it through.

**4. Open points settled.**
- **What "alters" means:** For a `## Recorded outputs` row with no `Pattern` (empty, `-`, `—`, or `none`), any modification, deletion, or rename of a file matched by `Paths` that existed at the merge base is an alteration; newly added files pass. For a row with a `Pattern` (inline expectations inside a test file), any change to or deletion of a line matching `Pattern` that existed at the merge base in a file matched by `Paths` is an alteration; newly added matching lines pass when no merge-base match was altered.
- **Listing:** A shipped non-gate script `scripts/expect_outputs.py` classifies added and altered recorded outputs against the merge base for both the review role and `scripts/merge_ready.py`.
- **Placement:** Recorded output files live beside the tests, never under `quality/**` (which is a guard path for ratchets and snapshots).
- **Merge-base declaration:** `## Recorded outputs` is read from `docs/process/binding.md` at the merge base so a branch cannot narrow what is watched.

## Refutation criterion

This decision is wrong, and must be revised, if any of these is observed:

- An agent branch alters or removes a recorded output that existed at the merge base after `depended-on` is declared and `merge_ready.py` reports `merge: agent`.
- A branch editing `docs/process/binding.md` to drop or narrow `## Recorded outputs` hides an altered recorded output from `scripts/expect_outputs.py` or `scripts/merge_ready.py`.
- A gate recipe runs the recorded-output tool in update mode or treats `expect_outputs.py` as a failing gate on its own.

## Considered options

- **Put recorded outputs in `guards/manifest.toml` as a guard group or `[[door]]`.** Rejected. Guard groups lock edits at write time or block all additions/edits by role, and doors wait on `deployed` / `irreplaceable-data` rather than `depended-on` and cannot distinguish inline snapshot additions from alterations inside test files.
- **Add a new tier 1 gate demanding expect tests or failing on altered snapshots.** Rejected. Expect tests are appropriate where behaviour shows as readable output, not everywhere, and re-accepting an existing expected output is an owner merge decision rather than a broken build.
- **Treat any edit to a file containing inline snapshots as altering recorded outputs.** Rejected. Adding a new test or editing unrelated lines in a test file would trigger `expect-output`; diffing merge-base lines matching `Pattern` avoids false positives.

## Consequences

- `skills/adopt/templates/docs/process/binding.md` gains a recorded-output facility note under `## Tests` and a `## Recorded outputs` table (`| Paths | Pattern |`).
- `scripts/expect_outputs.py` provides `declaration`, `listing`, `altered`, and a `--json` CLI.
- `scripts/merge_ready.py` adds `EXPECT_EVENTS = frozenset({"depended-on"})` and emits `expect-output` when `depended-on` is declared and `expect_outputs.altered(...)` is non-empty.
- `workflows/pipeline.js` adds `recordedOutputs` to `IMPLEMENT_SCHEMA` (covering implement and fix) and updates spec, implement, fix, critic, and review prompts and `SEVERITY`.
