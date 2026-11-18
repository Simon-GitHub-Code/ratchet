---
status: accepted
---
# The bite gate excuses the bites an upgrade owes

Amends ADR 0037: a gate that reads the ledger itself may judge a finding that is owed.

A real project upgraded from 0.12.4 to 0.13.1 could not get CI green. `upgrade.py --apply` recorded two gaps in `quality/bites.toml` as owed: the comments gate, newly bound, had no `[[bite]]`, and the fuzz gate had no bite with the `guarded-crash` proof. Conformance was green on them. The tier 1 `bite` gate (`project_bite.py --changed`) raises the same two findings from the same registry check, read no ledger, and was red. This meets ADR 0037's refutation criterion: the upgrade change was red in CI on the new pin for a finding the ledger lists. Every project whose binding gains the comments gate on its upgrade to 0.13 hits it.

ADR 0037 offers one remedy: a finding that another tier 0 or tier 1 gate is also red on is never owed. Applied here, conformance would stay red on the gap and the upgrade could not merge until an agent wrote the bite, which is the state ADR 0037 was written to end.

## Decision

**The bite gate reads the ledger and excuses what it owes.** Before it proves anything, `project_bite.py` matches each registry problem against the `bites` entries of `quality/owed.toml`, by the key conformance builds for the same finding: rule `bites`, path `quality/bites.toml`, and the problem. It prints each problem it excuses as `bite: owed: <path>: <problem>` and the count with the versions they are owed since. It stays red on every problem no entry owes, and on a ledger that does not parse when there is a problem to excuse. The bites the registry does hold are proven as before.

**Conformance keeps the staleness check.** An entry whose gap is gone is reported by conformance, as for every rule outside the comments gate's own, and the next `upgrade.py --apply` drops it. The bite gate does not report it: under `--gate` it leaves the gaps out, and would call every owed gap stale.

## What this does not see

The owed key of a gap is its problem text. A release that rewords the check makes the old entry stale and the gap red in both gates until that release's upgrade records it again, as ADR 0037 already says of every rule.
