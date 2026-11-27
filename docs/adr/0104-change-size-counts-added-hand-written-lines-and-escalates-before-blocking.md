---
status: accepted
---
# Change size counts added hand-written lines and escalates before blocking

Amends ADR 0045 and ADR 0076.

Observed in an adopted service repository: a single coherent feature change with roughly 865 lines of hand-written product code repeatedly failed `change-size` at 1,172 to 1,526 lines against the 1,000-line ceiling, burning hundreds of agent steps trimming valid code. Two measurement errors caused the false red:

1. **Generated artefacts were counted as product code.** A regenerated contract schema file alone added 448 lines (`+320 / -128`), almost half the entire product budget, even though no human or agent wrote it by hand. Whole-file recorded outputs declared under `## Recorded outputs` carry the same noise.
2. **Deletions were added to additions.** Counting `added + deleted` doubled the cost of every edited line and penalised deleting dead code or refactoring, inflating the measured size well beyond the new complexity introduced.

At the same time, treating `1,000` product lines as a single hard cliff forced agents either to artificially split coherent features or to golf code when a legitimate slice landed at 1,100–2,000 lines, even though the method already has a structured human-escalation path (`merge: owner`).

## Decision

1. **Count added lines, not `added + deleted`.** `scripts/change_size.py` measures `product_lines` and `test_lines` from added lines only (`git diff --numstat` column 1, plus line counts of untracked files). Deletions no longer consume budget, and replacing one line costs one line instead of two. File counts (`product_files`) still count every non-exempt touched product file, including binaries.
2. **Exclude declared generated artefacts and whole-file recorded outputs.** Alongside method records, lockfiles, snapshots, and the owed ledger, `scripts/change_size.py` exempts:
   - optional `- Generated:` globs declared under `## Test paths` in `docs/process/binding.md`, validated against overly broad patterns (`*`, `**`, `**/*`, `.`, absolute paths, and `..`), and
   - whole-file rows (`Pattern` empty) of `## Recorded outputs`.
   Both are read strictly from the merge base, so a branch cannot exempt its own product code.
3. **Two-tier ceilings: autonomous ceiling (`merge: owner`) and hard runaway ceiling (red gate).** `[ceilings.change]` in `quality/thresholds.toml` gains optional `product_files_hard` and `product_lines_hard` keys (template defaults: `product_files = 25`, `product_files_hard = 60`, `product_lines = 1000`, `product_lines_hard = 2500`, `test_lines = 3000`):
   - **Within `product_files` / `product_lines`:** `change-size` is green and `merge-ready` passes (`merge: agent`, subject to all other rules).
   - **Above `product_files` / `product_lines` and within `product_files_hard` / `product_lines_hard`:** `change-size` passes with a warning naming the autonomous ceiling, the pipeline and preflight let the coherent feature finish instead of aborting it, and `merge_ready.py` returns `merge: owner` with reason `size` so the owner signs off on the larger slice.
   - **Above `product_files_hard` / `product_lines_hard` (or `test_lines`):** `change-size` is red, preflight blocks oversized tickets, and the pipeline stops and splits the ticket.
   - **Backwards-compatible when omitted:** When `product_files_hard` or `product_lines_hard` is absent, `product_files` or `product_lines` acts as the hard ceiling, so existing repositories do not silently loosen their hard gate on upgrade. A hard ceiling lower than its autonomous ceiling is malformed and fails `change-size` red. Because the keys sit under `ceilings.`, `ratchet-lowered` and the merge judge hold them as ratchets once recorded.

## Refutation criterion

This is wrong if:
- an agent auto-merges a change above `product_lines` or `product_files` without owner review (`merge: agent` when `size` should have fired),
- a branch can widen `- Generated:` or raise `product_lines_hard` to bypass `change-size` or `merge-ready` on the same branch, or
- more than a quarter of normal single-slice features over the next twenty merges still exceed the 1,000-line autonomous ceiling once generated files and deletions are excluded (in which case the autonomous ceiling itself needs recalibration).

## Considered options

- **Raise `product_lines` from 1,000 to 2,500 directly.** Rejected. That would allow 2,500-line changes to merge autonomously without human sign-off and would still penalise deletions and generated files.
- **Auto-exempt all `none:` rows of `## Comments`.** Rejected. Repositories routinely mark hand-written data formats (such as `**/*.json`) as `none:` in `## Comments` because the format has no comment syntax; exempting them would silently exclude hand-written configuration and data files.
- **Remove `change-size` completely.** Rejected. Runaway agent diffs degrade review/critique quality, widen blast radius for unguarded arrivals (ADR 0030), and make bisection and rollback harder.
