---
status: accepted
---
# The change-size ceiling counts product code, and tests have a ceiling of their own

Across four adopted projects, 23 merged feature changes (records and lockfiles excluded) measured, in lines of product code (what is not a test): 0, 19, 33, 71, 119, 123, 151, 153, 168, 172, 193, 201, 209, 337, 342, 361, 370, 566, 638, 667, 775, 860, 2064. The 2064 was a first feature, built before the pipeline existed. In lines of tests: 65, 73, 141, 390, 448, 499, 506, 525, 562, 579, 634, 648, 659, 671, 721, 728, 826, 896, 951, 1013, 1069, 1110, 2680. One recent feature counted 1,000 lines, of which 80 were product code and 920 tests (503 from the build, 417 from the critic), and it was itself one piece of a ticket already split for size. In that project 85% of the counted lines across recent features were tests.

The method asks for many tests (an empty, an oversized and a hostile case per input, then the critic's) and then counted them against the same ceiling as the product code. The ceiling of 40 files and 1,500 lines (ADR 0032) therefore mostly limited tests and pushed toward fewer of them. ADR 0032's own criterion, "wrong if a ticket that is one vertical slice stops as too large under it", is met.

The ceiling has three reasons, and tests serve none of the first. It bounds how much unguarded code the first change of a new kind can bring (ADR 0030), which scales with product code. The reviewer and critic agents judge better on a change that fits their attention. A smaller merge is a smaller thing to revert or bisect. No human reviews the code.

## Decision

**The ceiling counts product code.** `[ceilings.change]` holds `product_files` and `product_lines`, which bound the files and the added-plus-deleted lines of what is not a test, and `test_lines`, a separate and much looser ceiling that only stops a runaway of tests. Records, lockfiles, the adoption exemption and rename handling are as before.

**Which paths are tests is the binding's, not the plugin's.** The binding's `## Test paths` section holds `- Tests:` with the globs of every test, fixture and test-support file in backticks, or `none: <reason>`. The adopting agent writes it from what it knows of the stack, as it writes the Dependencies section (ADR 0042); the plugin names no stack. The gate reads the section at the merge base, so a change cannot widen what it exempts. Conformance is red on a missing, unfilled or malformed section, and `upgrade.py` adds the template's section, which an agent fills.

**A missing section fails closed to the old counting.** Until the section is filled, every changed line counts as product and the gate prints how to write it. The gate is not red for the missing row alone: the same ceilings apply as before to the same lines, an upgrade does not turn a green repository red, and conformance carries the finding (owed, since an upgrade, as ADR 0037 has it). The alternative, a red gate, would stop every ticket of an upgraded repository until an agent has run, for a fact the pipeline can only be hindered by, not endangered.

**The numbers: 25 product files, 1,000 product lines, 3,000 test lines.** The product ceiling sits above every feature change measured but the pre-pipeline first feature: the 22nd of 23 is 860 lines, 14% under 1,000, and the median is 201. The room kept for what is still to come was two thirds of the ceiling for the critic's tests (ADR 0040). Those are tests now, so product code keeps no margin: the review writes none, and a fix round a few lines. The stop is the ceiling itself. Product files are 25 since a ticket of 1,000 product lines at the 40 lines per file the data suggests is about 25 files; the data holds no file counts, so that number rests on this estimate and on the criterion below. The test ceiling is 3,000: the largest test total but one is 1,110, and 3,000 leaves room for the critic's tests (about two fifths of the spec's own) on top of a spec at the 1,110 end, and it admits the 2,680 first feature. The early stop for tests keeps ADR 0040's two thirds while the critic runs (2,000 of the spec's own), and all of it without. The pipeline's estimate holds product code (implementation and records) to the product ceiling and tests (the spec's and the critic's) to the test ceiling.

**An existing repository moves its keys, and nothing loosens by the move.** `files` and `lines` become `product_files` and `product_lines` at the same values, and `test_lines` is the template's. The change-size gate reads the older keys as the product ceilings until then. Conformance finds them and `upgrade.py --apply` makes the edit itself. The lowered gate (`lowered.py`) reads the move: a removed `ceilings.change.files` or `lines` is judged against the new key that holds it, and is a loosening only when that value is higher, so a project cannot drop the old key and write a larger one. `test_lines` is a key with no earlier value; it is the one place the method loosens, since tests no longer share the old budget, and the upgrade's report says so. Lowering a migrated repository to 25 and 1,000 is the owner's to do with `change_size.py --set`: a ceiling only falls.

This supersedes ADR 0032 on the first ceiling's value and shape (the ceiling is now this triple, and the first slice must fit under the product ceiling with every line counted) and ADR 0040 on the margin: the two-thirds room applies to the test ceiling and the critic's tests only.

## Refutation criterion

The product ceiling is wrong if a ticket that is one vertical slice stops as too large under it, or if on ten changes built after this one none reaches a third of it (a ceiling nothing approaches bounds nothing, and it comes down). The test ceiling is wrong if any change stops on it while its product code is under half the product ceiling: tests then still limit the work, and it rises by an ADR naming it. The file ceiling is wrong if the median of product files per merged feature over the next ten changes is under 5 or over 15, since it was set from the line count alone. The split of paths is wrong if a repository's `## Test paths` section is filled and a changed test file is still counted as product on a gate run, or a product file is counted as a test.

## Considered options

- **Keep one ceiling and raise it.** Rejected. Tests were 85% of it, so it would let product code grow without a bound the first reason needs.
- **Ship a list of test path patterns per language.** Rejected. The plugin names no stack; the binding holds the per-stack rows.
- **A red gate when the row is missing.** Rejected, as above.
- **Treat the old keys as the new ones silently.** Rejected. The gate reads them, but the lowered gate would then see no change, and nothing would record that tests left the budget.
- **No test ceiling at all.** Rejected. A runaway of generated tests is still a change no agent judges well, and it must stop somewhere.
