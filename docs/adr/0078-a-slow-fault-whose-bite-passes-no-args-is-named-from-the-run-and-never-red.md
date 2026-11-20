---
status: accepted
---
# A slow fault whose bite passes no args is named from the run, and never red

Tracked by: #294

Amends `skills/method/tiers.md`, "A bite of a whole-tree gate is narrowed", and the bite table of the same page. Builds on ADR 0020 (the proof), ADR 0069 (the fault's wall time on its verdict line) and ADR 0075 (what an upgrade owes the bite gate).

The method says a bite of a whole-tree gate is narrowed by its `args`. Two adopted projects on 0.13.3 had not narrowed their slow gates, and nothing said so. The proof printed each fault's seconds, but no line and no record tied "this fault took 300 s" to "this bite passes no `args`".

- **Project A, tiers 0 and 1, 16 cores, 4 lanes.** Its type-aware lint, test, coverage, changed-line coverage, test-count, format and security-scan gates were proved over the whole tree. The fallback proof took 875 to 1,192 s locally and 1,055 to 1,608 s on the runner, and it was red in four local runs in a row from load. Once the bites of those gates named two or three files each, the proof took 543 s and was green, 39 of 39 faults over 31 gates.
- **Project B, tier 2, a 2-core runner, 2 lanes, 76 faults over 42 gates.** Six faults with no `args` were 60% of the full proof's fault seconds. Three were narrowed: suite order from 637.5 s to 6.1 to 7.7 s, accessibility from 236.3 s to 33.2 to 51.2 s, fuzz from 65.6 s to 16.5 to 18.1 s. The full proof job went from 1,978 to 2,331 s to 1,258 and 1,732 s. A fault of the "nothing to judge" kind, which empties a folder and expects the runner to find no test, was left whole on purpose.

Before this record the proof wrote nothing per fault. `.ratchet/timing.jsonl` held `budget.py`'s record of each timed key and `parallel.py`'s tier and missing-cache records. The proof printed each verdict with the fault's seconds and lane, and nothing kept them.

## Decision

**The proof records every fault as it runs.** As each verdict is printed, `project_bite.py` appends a `fault` record to `.ratchet/timing.jsonl` through `runrecord.py`, with the gate, the bite's name, the seconds, `narrowed` (whether the bite passes `args`), the verdict, the lane and the usual stamp of time, run and tree. Every way the proof runs passes through the one place that prints a verdict, so the whole proof, `--changed`, `--pull-request-gates`, `--gate` and one lane (`--jobs 1`) all record. A shard job of tier 2 runs the whole proof in its own job and uploads its own `timing.jsonl`. The proof does not split itself across shards, so nothing more is needed there. The record goes to the state folder that `budget.py` and `parallel.py` already write and that the init template ignores. The real work tree is still never written by a fault.

**Slow is longer than the project's `tier0` ceiling.** A fault is slow when its seconds, the sum of its gate's runs for that fault, exceed `[ceilings.seconds].tier0` in `quality/thresholds.toml`. Tier 0 is the project's own word for a fast check: it must finish all of tier 0 in that time, and adopt records it from a measured run and never under the tier's budget of about a minute. A single fault that costs more than the whole commit check is slow by the project's own measure, so the fraction is one and the plugin adds no number. A project with no `tier0` key, or a thresholds file that does not parse, gets no line. The plugin does not default it.

**One line per slow fault with no `args`.** After the verdicts and before the total, the proof prints `bite: slow: <gate>: <bite>: <seconds>s with no args, over the tier0 ceiling of <ceiling>s;` and the cure in the method's words: a bite of a whole-tree gate is narrowed, its args name one file, one target or one mutant, or its `whole` says why it cannot be. A bite that passes `args` is never named, however long it took: its narrowing is taken at its word, and whether it narrows to something that still bites is the check below.

**A bite left whole says why, in `whole`.** `whole = "<reason>"` is an optional key of `[[bite]]`, a non-empty string, and never beside `args`: a bite with both is a registry problem, red in the bite gate and in conformance. A slow bite with a reason is still printed by the proof on every run, with `whole on purpose: <reason>` in place of the cure. It is shown, not silent. Three things keep it honest. The registry sits under `quality/`, a guard path, so a reason is the owner's to merge. The reason is printed where its cost is paid. A reason cannot sit beside a narrowing it contradicts. The key does not change what the proof runs, so a change to it alone proves no gate again under `--changed`.

**Conformance prints a note and is never red on it.** After its findings, the not-checked lines and the owed lines, conformance prints `slow: quality/bites.toml: <the same line>` for each bite that the registry still holds with no `args` and no `whole`, whose last `fault` record has `narrowed` false and seconds over the current `tier0` ceiling. A note is the strongest reading that meets all three constraints:

- **Seen, not inferred.** No record of a run, no note. Only a bite's last record counts, so a later quick run clears it. A record of a bite that has since been narrowed, given a reason, renamed or removed is stale and gives no note. A record that says the bite was narrowed gives none either, since it shows no whole run.
- **Deterministic.** The same record, registry and thresholds give the same lines. A test runs conformance twice on one record and compares the two. But the record itself is a time measured under whatever load the run met. Project A's proof went red four times in a row from load alone, and Project B's runner was about 1.4 times slower at every fault from one run to the next. A fault near the ceiling crosses it on a busy machine, so a red finding would turn a merge red on load and not on the change. No reading of one measured time removes that, so the finding is a printed note plus a record, and never red.
- **An upgrade never turns a project red.** `upgrade.py` owes what conformance finds at the moment it applies the pin. At that moment no `fault` record exists, since the proof that writes one is the release being installed. A red finding would appear on the first proof after the upgrade and could not be owed through the ledger of ADR 0075. As a note it needs no entry, and `upgrade.py` is unchanged. The new `whole` key is optional, and no registry an older release accepted holds it, so no existing registry gains a problem.

## Consequences

On the ticket's figures, with a `tier0` ceiling of 60 s, the lowest adopt records:

- **Project B, named:** suite order (637.5 s) and accessibility (236.3 s) at any `tier0` ceiling under those times. Fuzz (65.6 s) only at a ceiling under 65.6 s: at 60 s it is named, at 70 s it is not. The three other unnarrowed faults are not in the ticket one by one. Together they are the rest of the 60%, which by our inference from two lanes and the job's 1,978 to 2,331 s is at most about 1,400 to 1,900 s, or several hundred seconds each. All three are named at any ceiling of the tier's size, the "nothing to judge" fault among them. That fault is the reason for `whole`: with a reason, it is printed as whole on purpose and drops out of conformance's note.
- **Project B, not named:** the three after narrowing. They pass `args`, and all were under 60 s anyway: 6.1 to 7.7 s, 33.2 to 51.2 s and 16.5 to 18.1 s. Any of the other 70 faults that passes `args`, however long it ran, or that ran in less than the ceiling.
- **Project A:** the ticket gives the totals, not each fault. By inference, its 4 lanes over 875 to 1,192 s spent about 3,500 to 4,800 fault seconds before and about 2,200 after, so the narrowed gates' faults carried roughly 1,300 to 2,600 s. A whole-tree lint, test or coverage gate that runs for more than half a minute makes a fault over 60 s and is named. A format gate that runs in a few seconds over the tree is not, and narrowing it buys only those seconds.

What the rule does not see. A bite whose `args` do not narrow anything, such as a flag, is taken as narrowed. A project with no `tier0` ceiling learns nothing beyond the record. Bites of the gates the plugin ships are proved by `bite.py` and are not recorded here. `timing.jsonl` grows by one line per fault per proof, as it already grows by one line per timed key.

## Follow-up: the narrowing checked inside the proof

The rule that a narrowing is checked by breaking the gate is written in `tiers.md` as an instruction. It can be made deterministic, and cheap, inside the proof. A narrowed bite would name a second fault, `blind`: a `replace` or `patch` that breaks the gate the way the fault guards against, such as the rule turned off or the threshold set to zero. The proof would plant both faults and require the gate green, and give a verdict of its own when the narrowed gate is still red, since that bite then proves only the exit code. The extra run is one narrowed run per narrowed bite. That is seconds against the minutes saved, and it is red on a fact the run sees, not on a time. Not built here.

## Refutation criterion

This is wrong if an adopted project's proof still spends most of its fault seconds on bites with no `args` for two releases after this one while the note is printed, which shows in its `timing.jsonl`. It is also wrong if the note names a fault the project cannot narrow and cannot give a `whole` reason for, or if a merge is ever red on this note.

## Considered options

- **The gate's own ceiling, or a fraction of it.** Rejected. It bounds a warm run of that gate, and an unnarrowed bite is two cold runs of the same gate, so it says how near the gate is to its own ceiling, not how much of the proof it costs. Many gates have no ceiling of their own.
- **A fraction of `bite-all`, `tier2` or `bite-run`.** Rejected. `tier2` is unbounded by the method, `bite-run` defaults to the plugin's 900 s, and any fraction of them is a number the plugin invents.
- **A key of its own, such as `bite-slow`.** Rejected while `tier0` fits. Adopt already records `tier0` for every project, and a new key would be one more number nobody sets.
- **A red conformance finding owed by the upgrade.** Rejected. The upgrade cannot see a record that does not exist yet, and a measured time under load would turn merges red at random.
- **A silent exemption, such as an `args` of `[]` read as "whole on purpose".** Rejected. Nothing would say why, and nothing would show it on the next run.
