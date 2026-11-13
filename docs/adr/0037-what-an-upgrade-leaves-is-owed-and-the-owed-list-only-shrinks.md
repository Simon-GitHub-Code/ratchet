---
status: accepted
---
# What an upgrade leaves is owed, not red, and the owed list only shrinks between upgrades

ADR 0029 made an upgrade one command that moves the pin and makes every edit the plugin can derive. On fresh clones of two of the owner's projects, each adopted at an older version, `upgrade.py --apply` made about 30 edits and left 69 and 80 `agent` items and 4 `owner` questions. Every one of those items is a conformance finding. Once the pin moved, conformance in tier 1 was red until an agent had done all of them and the owner had answered all four, so the upgrade could not merge as the one change ADR 0029 promised. A release that turns the owner's project red, or asks him four method questions before he can take it, blocks the work it was meant to help. ADR 0029 named the way out and left it unbuilt: what a release newly asks arrives as owed work, as ADR 0028 has it for the project's own safeguards.

## Decision

**The upgrade makes every edit that needs no judgement.** Beyond ADR 0029's edits, it binds each Gates row whose tool is a script the plugin ships (the incidents gate, the triggers gate, the flag registry), runs that gate in its tier, through a tier recipe that only names its dependencies, copying the template's recipe when it can, and adds it to a tier 1 job that lists its gates by name. A row the catalogue added whose trigger is known not to have fired is written `later: <trigger>`; a trigger whose kind the Triggers table cannot count may have fired, so its row stays owed. It writes the Triggers section with the one row every stack writes alike (`flag`, the tables of `flags.toml`), the tier lists the CI `shape` job reads (from a tier that names its gates, and an empty list of gates to shard), `- Shards: 1`, and the budget flags of ADR 0025 on a budget call an older template wrote. Each edit counts only when the check that raised the finding no longer raises it on the edited tree (ADR 0029). It does not write `- Life events: none`. The merge judge reads a missing line as every event, and `none` would turn the owner's review of one-way doors, of unflagged surface, and the critic off, so that line is the owner's.

**The upgrade writes down what it leaves.** In the change that moves the pin, `upgrade.py --apply` records each finding it cannot settle in `quality/owed.toml`, one entry per finding:

```toml
[[owed]]
rule = "bites"
path = "quality/bites.toml"
problem = "gate 'lint' (lint, near-all rules) is bound to a tool and no [[bite]] plants a fault for it"
since = "0.12.0"
```

`rule` is the conformance check that raised the finding (its function name without `check_`). `path` is the file the finding names. `problem` is what stays the same while the finding stands. `since` is the plugin version whose upgrade wrote the entry down. The ask arrived in a release after the version pinned before that upgrade. The plugin cannot say which release without a table of checks by version, and such a table would drift from the checks. The file is TOML like every other record under `quality/`. The plugin writes it whole and sorts it, so a second write of the same entries is byte-for-byte the same.

**An entry excuses one finding and nothing else.** That finding is the one the check `rule` raises on `path` with the identity `problem`. Two equal entries excuse two equal findings. A finding of another check, another file, or another subject of the same check in the same file (another gate's bite, another Gates row, another recipe) has a different key and is red as before. When two findings cannot be told apart by text, such as two toolchain placeholders in one workflow, the count decides: a third placeholder is red. A finding's identity is its problem, except where the problem says more than what is wrong, and it never holds a path of the checkout or the plugin, so the same finding has the same key wherever either lies. Three kinds of finding carry a separate identity. A toolchain placeholder's problem names its line, which moves when a line is added above it, so its identity names the workflow job instead. A missing recipe's problem lists what the recipe still needs, which shortens as an agent adds the variables. A missing Gates row's problem says whether its trigger has fired, which changes as code arrives. Fixing one owed item, or moving a line, never turns another owed item red. **No entry excuses what another gate judges.** A finding that another tier 0 or tier 1 gate is also red on is never owed: the ledger's own findings (ratchet-lowered), the permission rules (settings-check), the incident records (the incidents gate), a missing `GLOSSARY.md` (prose), a missing `quality/thresholds.toml` (ratchet-lowered) and a missing mutation register (the mutation gates). Conformance would otherwise print it as owed while that gate stays red on it. A Gates row or a permission rule of the project's own, which no version of the plugin writes, stays red and stays the owner's question.

**Conformance counts an owed finding and is not red on it.** It prints each owed finding as `owed: <path>: <problem>`, after the red ones, and the last line gives the count: `conformance: green (0 failures; 69 owed since an upgrade, listed in quality/owed.toml; not running until fixed: ...)`, the last part naming each check that does not run while what is owed stands. An entry that matches no finding is red, because it would excuse the same finding if it came back. The check raises one finding for every such entry and offers one edit that drops them all, and the trial of ADR 0029 shows the edit clears it. So the drop is an `auto` item, and the next `upgrade.py --apply` makes it. No check reads the ledger, so dropping an entry changes no other finding, and the trial judges the drop against the findings already raised without running every check again.

**Between upgrades the ledger only shrinks, and an upgrade owes only what the merge base was red on.** The ratchet-lowered gate (`lowered.py`), which every adopted repository runs in tier 0 and so before every merge, compares the ledger with the merge base. An entry may go. An entry that is new or changed is red unless the same change moves the binding's pin to a newer version, and then it must carry that version as `since`. A binding that pinned nothing, an adoption from before the pin, moves to a pin no older than the plugin judging the change. In a change that moves the pin, the gate also plans the upgrade of the merge base, and an added entry is red unless that plan leaves the same finding red there: a violation the change brought itself is never owed. `upgrade.py` records only that intersection, against the base the gate uses, so the writer and the gate agree. That plan is the pinned plugin's, so a run of another plugin skips it and says so; CI checks the pinned plugin out and asks it. The rule is in `lowered.py` because the ledger is a ratchet like the thresholds, and every adoption already runs that script, so no adopted `justfile` changes. A ledger that is deleted and written again is judged as a whole against the merge base.

**The owner's calls get a stated default, and the upgrade asks him nothing.** Every `owner` finding now carries `assumed`, in plain words: the default that holds until the owner says otherwise. The type requires it, so a check cannot add a question without a default. The default loosens no gate and removes nothing of his. On both projects the four questions were the `- Life events:` line and the three kinds of one-way door:

- The project's stage is left unwritten. The merge judge already reads a missing line as every event declared (ADR 0028), its strictest reading. The triggers gate reads it as none, so a row that waits on an event stays `later:`; the owner's summary names those checks. Writing `none` would switch the owner's review of one-way doors and the critic off, and writing every event would claim a deployment that may not exist and owe safeguards that cannot be bound yet.
- No path is a one-way door. That is how the merge judge treats a manifest with no `[[door]]`: until a door is named, an agent's change to such a part merges without the owner. The upgrade never writes under `guards/` (ADR 0029), and agents may not edit the manifest, so the owner adds a door by hand, with an agent telling him which paths.
- A permission rule that no version of the plugin writes stays. It is the owner's own content.

Each such finding is recorded as owed. The upgrade's report prints the default and the question that would settle it. The owner reads only the plain summary at its end. It gives the two versions, how many edits were made, how many things are owed and that none of them is red, that nothing fixes them by itself and how many of the fixes reach the project through merges he makes, the checks that do not run until they are fixed, each assumption once, and that his one step is the merge.

**`/ratchet:next` says what is owed.** `owed.py` prints one plain line that starts with the count, such as "69 things this project owes since the upgrade to 0.12.0", says nothing fixes them by itself, and names the checks that do not run until they are fixed; it prints nothing when nothing is owed. The `next` skill runs it after the route and relays the line.

## What this does not see

Whether an entry added with a pin move was already red on the merge base is asked only of the plugin the binding pins. A local run of another plugin says it did not ask, and CI, which runs the pinned plugin, asks it. The upgrade change also touches guard paths (the binding, the workflows, the ledger), so the merge judge leaves it to the owner, as ADR 0029 has it for the whole upgrade.

Every owed item on both projects is in a guard path, so each fix reaches the project through a merge the owner makes; one change can carry several. Nothing proposes owed work yet: the route does not, and an agent works on the list when asked.

Writing `later:` for a trigger that has not fired keeps a safeguard off until its kind arrives, exactly as for a fresh adoption: the triggers gate then lets the first instance in and makes the next one red until the row is bound. A row whose kind the Triggers table cannot count yet is not written, because its trigger may have fired; it stays owed and is named among the checks that do not run.

The merge judge sends every change to `quality/owed.toml` to the owner, since `quality/**` is a guard path. A burn-down change that only drops entries also waits for the owner, though only a shrink is possible without a pin move. Teaching the judge that a shrink of the ledger is a tightening, as it already treats the thresholds, belongs to the judge.

An entry's identity is the check's wording at the pinned version. A later release that rewords a check makes its old entries stale and its findings unexcused. That release's upgrade is the change that moves the pin, so it drops the stale entries and records the reworded findings. Between a plugin update and that upgrade, a newer plugin's conformance is red where the pinned one is not, as ADR 0027 already says.

A missing `- Life events:` line still reads as no event for the triggers gate, so an agent working through the owed Gates rows is offered `later: life:<event>` for an event that may have happened. The owed item names the event as undeclared. The project's stage is the owner's to state.

## Refutation criterion

This is wrong if an adopted repository's upgrade change is red in CI on the new pin for a finding the ledger lists. It is wrong if a check the upgraded binding binds to a script the plugin ships does not run in its tier, and is not named as not running. It is wrong if any later change adds to the ledger without moving the pin and merges. It is wrong if, across the owner's projects, the owed count after the first upgrade has not fallen within a month. Owed work that never shrinks is a second backlog nobody reads, and then the release should ask for less.

## Considered options

- **Leave the findings red, and have the upgrade change carry every fix.** Rejected. That was the state after ADR 0029: 69 and 80 items of judgement before the change could merge, and nobody upgraded.
- **Make the new checks of each release warnings.** Rejected. A warning is not counted and never shrinks, and it would weaken the check for repositories adopted after the release too.
- **Match an entry on the file and the check only.** Rejected. One entry would excuse every new violation of that check in that file, such as a gate bound later with no bite.
- **Match on the whole problem text.** Rejected. A line added above a placeholder, or a variable an agent adds while fixing another item, would turn an owed item red.
- **Stale entries silently ignored.** Rejected. An entry for a fixed finding would excuse the same finding when it came back.
- **A separate gate script for the ledger's history.** Rejected. Every adopted `justfile` would need a new recipe and a tier-list entry, and each is an upgrade item of its own. `lowered.py` runs everywhere already.
- **Ask the owner the four questions, in plainer words.** Rejected. Each has a default that loosens nothing, and the owner's one act in an upgrade is the merge (ADR 0029).
- **Write `- Life events: none`, the value adoption starts with.** Rejected. On a project that may already be live it would switch off the owner's review of one-way doors and unflagged surface, and the critic.
- **Leave every catalogue row owed instead of writing `later:`.** Rejected for a trigger known not to have fired: the owed list would hold work no project can do yet, and the triggers gate, which makes the next instance red, would not watch the kind.

Decided 2026-11-13.
