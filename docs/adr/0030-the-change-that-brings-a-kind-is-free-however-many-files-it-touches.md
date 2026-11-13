---
status: accepted
---
# The change that brings a kind is free, however many files it touches

ADR 0028 made the first one free and bounded it to one file: a change that brought a kind of code in two files or more was red, "without that bound, a first change could bring forty instances and be green". The owner agreed that bound on 2026-11-13. The README kept promising more: "The change that brings the first of anything is never red for it."

A dogfood run the same day put a scripted owner who does not read code in front of an empty directory, building a read-later web app through the whole route: init, adopt, spec, conjecture, tickets, then the first ticket. The agent wrote the first ticket whole: five source files, the page, a store, a settings module and the tests, 20 files and 683 changed lines as `change_size.py` counts them. The `triggers` gate was red against the base, with two kinds at fault:

- `'source' arrives in 5 files (src/app.ts, src/page.tsx, src/server.ts, src/settings.ts, src/store.ts)`
- `'dependency' arrives in 3 files (src/app.ts, src/page.tsx, src/server.ts)`

Six other kinds arrived in that change in one file each and were green. Neither red kind can arrive in one file in a real first feature: a server, a page and a store each import a library, and each is its own source file. So the one-file bound makes the first feature of almost any application red by construction. The owner was asked to split, dispute or stop, and took the split. The agent committed the settings module alone and parked the rest. After 91 minutes the only merged change was the settings module, and there was no working app, although the parked ticket, built by hand, saved and listed links and survived a restart. Every further piece of the split would have needed a `/ratchet:safeguard` change between it and the next, since each brings another file of a kind whose rows the previous piece made owed. The per-file rule cost the run its first working feature, and it stopped the owner in the middle of an idea, which ADR 0028's principle forbids.

## Decision

**The free arrival is per change, not per file.** A change that takes a kind's count from none at the merge base to any number at head is green for that kind, however many files bring it. The gate notes that the kind has arrived and names the rows the change made owed, as before, and the route makes binding them the next step. A change that adds an instance of a kind that already had instances at the merge base, while a row that kind fires is still `later:`, is red, as before. The rest of ADR 0028 stands.

**What bounds the first change is the change-size ceiling.** The forty instances ADR 0028 feared arrive in one change, and `change_size.py` already holds every change to a ceiling on files and added-plus-deleted lines (`[ceilings.change]` in `quality/thresholds.toml`). That ceiling bounds a first change exactly as it bounds any other, and it is the right instrument: the retrofit cost ADR 0028 bounds is proportional to what one change can bring, and the size gate measures that directly, where a file count measured it badly. The bound is open today. The adoption template ships `[ceilings.change]` commented out, and `change_size.py` is green when no ceiling is recorded, so an adopted repository has no ceiling until its owner records one. Recording one at adoption is a change to the adoption templates, made apart from this decision. Until it lands, a first change has no bound but the agent's own sense of a ticket's size.

Decided 2026-11-13.

## Refutation criterion

This is wrong if the first change of a kind brings so much that binding its safeguard costs more than one change. Read the first five `/ratchet:safeguard` changes made after a free arrival in adopted repositories. It is refuted if any one of them has to change more than five files of the guarded kind to make its gate green (a timeout added at each of many call sites, say), because the arrival piled up instances before the row was bound. It is also refuted if, once adoption records a change-size ceiling, a first feature of an ordinary application (a page, a route, a store) cannot fit under the ceiling the adoption template proposes: the bound would then be one more wall in front of the first feature, and the ceiling, not this rule, has to move.

## Considered options

- **Keep the one-file bound (ADR 0028 as amended on 2026-11-13).** Rejected on the dogfood run: the first feature of a web app was red for two kinds, and the split it forced left no working app after 91 minutes.
- **A higher bound on files, say three or five.** Rejected. Any file count is a guess at what a first feature touches, the run's `source` kind already needed five, and the number of files says little about the retrofit: forty calls in one file cost the same to retrofit as forty in ten.
- **A bound on instances per kind for the first change.** Rejected for the same reason, and because the kinds differ: one `dependency` instance per import line and one `source` instance per file are not comparable counts, so one number cannot suit all kinds.
- **Free per change, bounded by the change-size ceiling.** Chosen. One gate already bounds every change, the dogfood ticket fits under the template's example ceiling (40 files, 1,500 lines) at 20 files and 683 lines, and the rule now says what the README promised.

Supersedes, in part: ADR 0028's bound of the free arrival to one file. The rest of ADR 0028 stands.
