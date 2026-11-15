---
status: proposed
---
# A pull request proves the gates its change could affect, and the merge proves them all

Amends ADR 0020, point 6: what the tier 1 `bite` proves when a change touches a path that defines a gate. Settles issue #168.

The tier 1 `bite` (`project_bite.py --changed`) proves every tier 0 and tier 1 gate whenever a change touches a path that defines one. In one adopted project that is 32 faults over 28 gates, each in a cold scratch copy, and it took 867 to 1137 seconds when green. A change to one gate's recipe paid the whole proof in each of the five `check` runs of its pipeline, and every upgrade pays it, since an upgrade touches the binding. Under load the proof went red at 27 to 31 of 32 with verdicts that read as gates unable to fail (`NOT PROVEN`, `DOES NOT BITE`, `RED BEFORE THE FAULT`), when the cause was time. After two merges, the proof was green with 32 of 32, in 1205 and 1055 seconds, but the tier 1 time budget of 900 seconds turned the default branch red. A check that takes a quarter of an hour stops being run, and a proof that goes red on load teaches its reader to distrust it. The owner chose on 2026-11-15 that a pull request and the local check prove only what the change could affect, and that the full proof runs after the merge.

## Decision

**Tier 1 proves the gates the change could affect.** The tier 1 `bite` proves each gate the change could make unable to fail, and no other. Where the script cannot tell what a changed path affects, it proves every tier 0 and tier 1 gate, as today. The same rule holds on a pull request, in the local `check`, and on the default branch's own tier 1 run, where the change is the merge against its first parent. A change that touches no path defining a gate proves nothing, as today.

**What "could affect" means, per kind of path.** *To be fixed from the measurement of #168, before this record is accepted.* The issue's starting point:

- a recipe file: the gates whose recipe text changed;
- the binding: the gates whose rows changed;
- a tool's config: the gates that read it;
- `quality/bites.toml` or a patch under `quality/bites/`: the gates whose bites changed;
- anything else in the manifest's gate-defining groups: every tier 0 and tier 1 gate.

**The full proof is tier 2's, with a ceiling of its own.** `bite-all` proves every gate, on the default branch and nightly, as tier 2 runs today. It is held to its own key under `[ceilings.seconds]`, never to the tier 1 ceiling. Adopt and the upgrade set that key from the first measured run with headroom (`budget --set`). A red `bite-all` is a red tier 2, with what that stops (below).

**A green run says how close it came.** The `budget` gate prints, on a green run, its wall clock as a share of its ceiling. At 75 percent or more it raises a warning on the run's page as it does for a red on a pull request, so the owner sees a tier drifting toward its ceiling before runner variance turns it red.

**Each verdict says how long it took and why it went red.** The proof prints each fault's wall time and lane on its verdict line, so every run measures itself. A verdict caused by the gate's time limit is `TIMED OUT`, in its own words, and never `NOT PROVEN`, `DOES NOT BITE` or `RED BEFORE THE FAULT`. `TIMED OUT` is red, never a pass.

## Refutation criterion

This is wrong if any of these is seen:

- A gate the targeted proof skipped is shown unable to fail by the next full proof, from a change the targeted proof judged could not affect it. Each one is a hole in the per-path rule, fixed in that rule.
- The targeted proof of a one-recipe change takes more than a fifth of the full proof's time in the same project.
- A green tier 1 on a pull request is followed by a red tier 1 time budget on the default branch for the same change, with no gate worse.

## Considered options

- **The proof leaves tier 1 altogether.** Rejected by the owner. A change that breaks a gate's ability to fail would merge unproven and be found only after the merge.
- **Tier 1 keeps the full proof and is made faster by sharing one build.** Rejected by the owner as the answer, since it shortens the proof without changing what a one-recipe change pays. Sharing a build stays open as a speed-up for the full proof, once the measurement says how much it buys.
- **Raise the tier 1 ceiling.** A loosening, and it would not stop the proof from growing with each gate.

## For the builders

### What changes where

- `scripts/project_bite.py`: `--changed` reads the change's paths and proves the gates the per-path rule names, or every tier 0 and tier 1 gate where it cannot tell. Each verdict line gains the fault's wall time and lane. A gate stopped by its time limit is `TIMED OUT`.
- `scripts/budget.py`: a green run prints its share of the ceiling and warns at 75 percent or more.
- `skills/adopt/templates/quality/thresholds.toml` and the upgrade: a `[ceilings.seconds]` key for `bite-all`.
- `skills/method/tiers.md`, the bite section: tier 1 proves what the change could affect; `bite-all` has a ceiling of its own; the `TIMED OUT` verdict.
- ADR 0020, point 6: a pointer to this record.

### Consequences

- A one-recipe change proves one gate, in the local check and on its pull request.
- An upgrade that touches the binding's rows proves the gates of those rows, not all of them.
- The default branch runs the full proof after each merge that touches a gate-defining path, under a budget sized for it.

## Settled by the owner, 2026-11-15

- **1. Which runs own the full proof.** Both the default branch and the nightly run, as tier 2 runs today. The default branch finds a gate a merge broke on the merge that broke it, and the nightly run finds a gate a tool's own change broke with no merge at all. Cost: one full proof per merge that touches a gate-defining path.
- **2. What a red full proof stops.** What any red tier 2 stops. It is fixed before anything else merges, and while it is red the merge judge leaves every merge to the owner. A gate that cannot fail lets every later change through unjudged on what it was for. Cost: a `TIMED OUT` on a loaded runner stops merges until someone runs the proof again, which the 75 percent warning makes rarer.
