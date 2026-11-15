---
status: proposed
---
# A pull request proves the gates its change could affect, and the merge proves them all

Amends ADR 0020, point 6: what the tier 1 `bite` proves when a change touches a path that defines a gate. Settles issue #168.

The tier 1 `bite` (`project_bite.py --changed`) proves every tier 0 and tier 1 gate whenever a change touches a path that defines one. In one adopted project that is 32 faults over 28 gates, each in a cold scratch copy, and it took 867 to 1137 seconds when green. A change to one gate's recipe paid the whole proof in each of the five `check` runs of its pipeline, and every upgrade pays it, since an upgrade touches the binding. Under load the proof went red at 27 to 31 of 32 with verdicts that read as gates unable to fail (`NOT PROVEN`, `DOES NOT BITE`, `RED BEFORE THE FAULT`), when the cause was time. After two merges, the proof was green with 32 of 32, in 1205 and 1055 seconds, but the tier 1 time budget of 900 seconds turned the default branch red. A check that takes a quarter of an hour stops being run, and a proof that goes red on load teaches its reader to distrust it. The owner chose on 2026-11-15 that a pull request and the local check prove only what the change could affect, and that the full proof runs after the merge.

## Decision

**Tier 1 proves the gates the change could affect.** The tier 1 `bite` proves each gate the change could make unable to fail, and no other. Where the script cannot tell what a changed path affects, it proves every tier 0 and tier 1 gate, as today. The same rule holds on a pull request, in the local `check`, and on the default branch's own tier 1 run, where the change is the merge against its first parent. A change that touches no path defining a gate proves nothing, as today.

**What "could affect" means, per kind of path.** The rule is exact where the script can name the gates from text it already parses, and every tier 0 and tier 1 gate where it cannot.

- **The bites registry, `quality/bites.toml`.** The base and head registries are compared bite by bite. A gate is affected when one of its bites was added, removed or changed. A changed bite counts under both its old and its new `gate`. A change to `setup` or `include` changes the copy every gate runs in, so every gate is affected.
- **A bite's patch, under `quality/bites/`.** The gates whose bite names that patch, in the base or the head registry. A patch no bite names affects no gate.
- **The binding.** The gate rows of base and head are compared. A row added, removed, or changed in its command or tier affects its own gate. A change outside the rows affects every gate, since a gate may read the binding elsewhere (the operability lines, the application types). A change to the plugin pin line alone is open item 4, below. An upgrade that also changes rows, recipes or other gate paths is judged by those changes.
- **The recipe file in `gate-commands`.** `just` is the method's own runner, so the script reads the recipes at base and head (`just --dump --dump-format json`). A changed recipe body affects every gate whose row command reaches that recipe, through its dependencies and through `just <recipe>` calls in recipe bodies. A change to a top-level assignment, a `set` line, an import or a module affects every gate.
- **Any other `gate-commands` path**, such as a script or the commit-hook config. Nothing records which recipe runs which script, and a scan of recipe text for the path would be a guess, so every gate is affected.
- **`tool-config`, `gate-tools`, `fitness` and `formal`.** These groups name no gate, and which config file a tool reads is a fact of the stack that ADR 0042 keeps out of the plugin. Every gate is affected.
- **No merge base.** Every gate is affected, as today.

The proof prints, for each gate in its set, why it is there: `lint: bite x changed`, or `every gate: scripts/check.sh changed, and no record says which gate runs it`. The owner then sees when a run fell back to every gate.

**This record stays proposed until the measurement.** The last two kinds of path fall back to every gate, and the adopted project's two red merges touched scripts. If the measurement shows that most changes still fall back, the targeted proof buys little there. The exact route would then be an optional line on each gate row naming the paths its gate reads, with a missing line counted as every gate. That is a new binding field and the owner's question, so it is asked then, not decided here. What tier 1 does when it falls back is open item 3, below.

**The full proof is tier 2's, with a ceiling of its own.** `bite-all` proves every gate, on the default branch and nightly, as tier 2 runs today. It is held to its own key under `[ceilings.seconds]`, never to the tier 1 ceiling. Adopt sets that key from the first measured run with headroom (`budget --set`). In a project adopted before this record, the upgrade writes the missing key to the owed list (ADR 0037), and the first measured run sets it. A red `bite-all` is a red tier 2, with what that stops (below).

**A green run says how close it came.** The `budget` gate prints, on a green run, its wall clock as a share of its ceiling. At 75 percent or more it raises a warning on the run's page as it does for a red on a pull request, so the owner sees a tier drifting toward its ceiling before runner variance turns it red.

**Each verdict says how long it took and why it went red.** The proof prints each fault's wall time and lane on its verdict line, so every run measures itself. A verdict caused by the gate's time limit is `TIMED OUT`, in its own words, and never `NOT PROVEN`, `DOES NOT BITE` or `RED BEFORE THE FAULT`. `TIMED OUT` is red, never a pass. One run of a gate is limited to the gate's own `[ceilings.seconds]` key where it has one, and else to its tier's key, since one run never needs longer than the gate is allowed in CI. With neither key set, the limit is 900 seconds, the tier 1 budget of the tier table. `--limit` overrides it for one proof.

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

- `scripts/project_bite.py`: `--changed` reads the change's paths and proves the gates the per-path rule names, or every tier 0 and tier 1 gate where it cannot tell, and prints why each gate is in its set. Each verdict line gains the fault's wall time and lane. A gate stopped by its time limit is `TIMED OUT`.
- `scripts/budget.py`: a green run prints its share of the ceiling and warns at 75 percent or more.
- `skills/adopt/templates/quality/thresholds.toml`: a `[ceilings.seconds]` key for `bite-all`. The upgrade owes it where it is missing.
- `skills/method/tiers.md`, the bite section: tier 1 proves what the change could affect; `bite-all` has a ceiling of its own; the `TIMED OUT` verdict.
- ADR 0020, point 6: a pointer to this record.

### Consequences

- A one-recipe change proves one gate, in the local check and on its pull request.
- An upgrade that touches the binding's rows proves the gates of those rows, not all of them.
- The default branch runs the full proof after each merge that touches a gate-defining path, under a budget sized for it.

## Settled by the owner, 2026-11-15

- **1. Which runs own the full proof.** Both the default branch and the nightly run, as tier 2 runs today. The default branch finds a gate a merge broke on the merge that broke it, and the nightly run finds a gate a tool's own change broke with no merge at all. Cost: one full proof per merge that touches a gate-defining path.
- **2. What a red full proof stops.** What any red tier 2 stops. It is fixed before anything else merges, and while it is red the merge judge leaves every merge to the owner. A gate that cannot fail lets every later change through unjudged on what it was for. Cost: a `TIMED OUT` on a loaded runner stops merges until someone runs the proof again, which the 75 percent warning makes rarer.

## Open for the owner

- **3. What tier 1 does when it cannot target.** In every fallback case above, tier 1 would run the same 15 to 25 minute proof under the tier 1 ceiling, and go red on the budget the way the two merges did. It is asked once the measurement says how often the fallback fires, and what the full proof costs with one shared build.
  - (a) Prove every gate under the tier 1 ceiling, as today. Honest, slow, and red on runner variance.
  - (b) Prove every gate, held to the full proof's own ceiling, not the tier 1 ceiling. Slow, and green when the gates bite.
  - (c) Prove nothing it cannot target, and say so in one line. The full proof after the merge catches it, and a red there stops every further merge (item 2).

  Default taken until the owner answers: (b). It merges nothing unproven, and it ends the red on variance. Cost: a pull request or local check that falls back still takes a quarter of an hour, which is what the owner asked to end.
- **4. A change to the plugin pin line alone.** Default taken until the owner answers: it affects no gate, so tier 1 proves nothing for it. The pin names the plugin version, this proof covers the gates the repository bound to its own tools, and the plugin's release proves the plugin's own gates. The full proof after the merge is the catch. Risk: the plugin's release proves its gates on its own fixtures, not on this project's configuration, so an upgrade under which a project's gate stops biting merges, and it is found by the full proof on the default branch.
