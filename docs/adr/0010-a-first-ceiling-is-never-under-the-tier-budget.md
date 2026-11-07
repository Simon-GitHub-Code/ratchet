---
status: accepted
---
# A first ceiling is never under the tier's budget

Adopt runs `budget --set` once on the skeleton and records the measured time with 1.5 times headroom as each tier's first ceiling, and a ceiling only falls. A trial adoption of a fresh python-uv project measured an empty skeleton and recorded tier 0 at 4 seconds and tier 1 at 14, against the method's own budgets of about a minute and under fifteen minutes. The spec agent of the first feature predicted at once that its tests would cross 14 seconds, so the first real work would have needed an ADR to "loosen" a ceiling sixty times tighter than the budget the method sets, or paid for it with caching and sharding on a suite of seconds. We decided that `budget --set` never records a tier's ceiling under that tier's budget in `tiers.md` (60 seconds for tier 0, 900 for tier 1), so the measured value governs only a repository whose suite is already near its budget; that per-gate keys keep no floor; that an existing ceiling is never raised by the floor, so repositories adopted before this keep theirs; and that the owner may still lower a ceiling by hand, which `ratchet-lowered` reads as the tightening it is. Decided 2026-11-07.

## Refutation criterion

This is wrong if a repository adopted after it lets a tier grow from seconds to near its budget without anyone noticing, because the ceiling sat at the budget while the suite tripled: read `.ratchet/timing.jsonl` across the first ten features of the next two adoptions, and if a tier more than doubles with no pull request naming it, measured ceilings are needed earlier and the floor should give way to a rule that re-measures after the first feature.

## Considered options

- Keep measuring the skeleton: rejected, it turns the first feature's normal growth into ADR paperwork, which teaches that ceilings are noise.
- Leave tier ceilings unset until the first feature merges, then measure: rejected for now, it leaves a window with no ceiling and needs a step someone must remember.
- A `--floor` flag adopt passes: rejected, the budget is the method's number and a script that holds it cannot be called wrongly.
