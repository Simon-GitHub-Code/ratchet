---
status: accepted
---
# An owner-away run assumes what it can take back, and adoption leaves no gate waiting for code

The owner reported being asked to intervene too often. The trials of 2026-11-11 counted it: 27 and 26 owner actions to reach the first agent-ready ticket (16 dialogs each, 7 of them in adopt), and then the first owner-away `/ratchet:feature` stopped at its spec stage after two agents. It stopped on one open question (whether to reserve the short code `healthz`, a collision of one in 62^7) and five guard edits: a glossary entry, and four gate rows the adoption had bound as "none until the first route exists", whose binding only the owner can change.

We decided three things. First, the spec role decides what can be taken back: an undecided point gets the conservative, reversible answer, the spec and tests are built on it, and it is returned in `assumptions` with its refuter and listed in the pull request's Conjecture section, where the owner counters it at the merge they already make. Only a point where a wrong guess loses data, fixes a public contract, spends money or weakens a security or privacy rule is an open question that stops the run. Second, adoption binds every row against a walking skeleton it writes; `none` is for a property the project will never have, never for code that does not exist yet, and a tier 2 recipe red on the skeleton is fixed before the hand-over. Third, adopt and conjecture ask less: adopt has two dialogs, the binding and the brief, each one round; a conjecture is agreed once per spec and inherited by its tickets; init can be accepted with the repository kept local in the same answer. Decided 2026-11-11.

This moves no decision from the owner to an agent. Every assumption reaches the owner in writing before the merge, and the merge is still theirs; what changes is that they read it once, with the code, instead of answering it alone and re-running. ADR 0021 changes the last part: a change the merge judge passes merges before the owner reads its assumptions, which are the ones that can be taken back, and an open question still stops the run.

## Refutation criterion

This is wrong if assumptions carry decisions the owner would have made differently and finds too late. Read the first ten pull requests built this way: if the owner counters more than one assumption in five, the line between assumption and open question is in the wrong place and the spec prompt's four stopping cases grow. It is also wrong if it changes nothing: rerun the trial's ticket, and if the run still blocks before Implement on anything but the glossary entry, the skeleton rule is not being followed and belongs in the conformance gate. Known residue: a new glossary term is still a guard edit, so a feature that needs a word stops an owner-away run; and a human still merges every pull request, which is the largest remaining count of owner actions and is the owner's own rule to change.

## Considered options

- Let the spec role answer every question: rejected, some guesses cannot be taken back, and those are exactly the ones the owner wants to be asked.
- Keep stopping and make the rerun cheaper (resume from the spec): rejected as the fix, the cost is the owner's round trip, not the agents' tokens.
- Let adoption defer rows and have the pipeline bind them: rejected, a gate's binding is a guard path, and an agent that binds the gate it must pass is grading its own work.
