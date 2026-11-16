---
status: accepted
---
# Fuzzing is coverage-guided, and a solver searches the first parser

Amends ADR 0028's catalogue rows for `fuzzing` and `symbolic checking`, and settled item 5 of ADR 0062. Builds on ADR 0062's per-kind rows, which are accepted and not yet built.

**Today** the catalogue asks two questions of input:

- `fuzzing`, on `code:untrusted-input`: "Can random input crash it?", answered by "fuzzing with a kept corpus, each target for a fixed time under a cap on the total; a crash is a bug" (`skills/method/tiers.md:101`, `:204`).
- `symbolic checking`, on `code:formal-model`: "Can a solver find an input that breaks a contract?" (`tiers.md:100`, `:212`; `skills/method/application-types.toml:281-282`).

Two things are wrong with that.

1. **"Random input" asks for too little.** A fuzzer that mutates input blindly finds shallow crashes and stops. A coverage-guided fuzzer keeps each input that reaches new code and mutates from there, and most of them also compare input bytes against the constants the code compares with. It reaches deep into a parser in the same time box. Nothing in the method asks for that, and no bite shows it, so a blind fuzzer binds the row and is green.
2. **The solver waits for a model it does not need.** Symbolic and concolic execution run on the code itself: a solver works out which input takes a branch, and concolic execution does it along real runs. Neither reads a model. ADR 0062 hung `symbolic checking` on `code:formal-model` only because the question speaks of contracts and no kind marks a contract ("A trigger of its own needs a kind for contracts", settled item 5). So the method asks for a solver only once someone has written a formal model, and never of the parser, where a solver pays most.

A solver does not replace the fuzzer. It runs one path in the time a fuzzer runs thousands of inputs, its paths multiply with every branch and loop over the input, and it must model or skip system calls, libraries and floating point. What it does that a fuzzer cannot is take a branch guarded by a narrow relation between input fields, such as a length that must equal a sum, which mutation almost never hits. The published hybrid designs pair the two: the fuzzer explores, and a concolic engine is called when coverage stops growing, to solve the branch the fuzzer is stuck on. Tools for it are mature on some stacks and absent on others.

## Decision

**1. Fuzzing is coverage-guided.** The `fuzzing` question becomes "Can input that follows the code crash it?", answered by "coverage-guided fuzzing with a kept corpus, each target for a fixed time under a cap on the total; a crash is a bug". Trigger, tier, collection and time box stay as they are.

**2. A named proof holds it.** `fuzzing` requires the proof `guarded-crash`: "a crash reached only when the input holds an exact value of four bytes or more that the parser compares against". A blind fuzzer does not reach it in the bite's seconds, and a coverage-guided one does. The repository writes the fault, as for every proof. The bite names its seconds in `args`, as a fuzz bite does today (`tiers.md:253`).

**3. `symbolic checking` moves to the parser.** Its trigger becomes `code:untrusted-input`. The question becomes "Can a solver find an input that crashes it or breaks an assertion in it?", answered by "symbolic or concolic search of the code, alone or as the fuzzer's hybrid mode, each target for a fixed time under a cap on the total". It stays tier 2. A stack with no tool binds it `none` with the reason, which the method already allows.

**4. A named proof holds the solver.** `symbolic checking` requires the proof `related-fields`: "a crash reached only when two fields of the input satisfy an arithmetic relation, such as one equal to the sum of the others". Comparing input bytes with constants does not reach it, and a solver does. A row bound `none` needs no bite.

**5. `code:formal-model` owes `formal verification` alone.** A kept model is checked by `formal verification`. ADR 0062's `formal verification of untrusted input` stays a separate row on the same trigger: it asks for a check that the parser holds its properties, and this row asks for a search that finds an input where it does not.

So a parser owes three tier 2 rows: `fuzzing`, `symbolic checking` and `formal verification of untrusted input`. Each may be bound `none` with a reason, and each bound row carries its proof.

## Refutation criterion

This is wrong if any of these is seen:

- A crash that a coverage-guided fuzzer with a kept corpus finds within its time box is first found by a blind fuzzer that was bound in its place and kept green, in any adopted repository.
- A `guarded-crash` bite goes green or red on its own across ten runs of the same commit, because the fuzzer's luck decides it. The proof's seconds are then wrong, or the proof cannot be held by a time box.
- In the first five repositories that bind `symbolic checking` to a tool, it finds no crash the fuzzer missed over three months. The row then costs more than it finds.

## Considered options

- **Replace the fuzzer with a solver.** Rejected. A solver is slower per input, stalls on loops over the input, and has no tool on many stacks. The fuzzer finds most crashes, and the solver finds the ones behind narrow relations.
- **Fold the solver into the fuzzing row as a hybrid mode.** Rejected as the only form. One row could not say whether the solver ran, and a stack without one would bind the fuzzer and look complete. Point 3 allows the hybrid mode as the tool that binds the solver's row.
- **Keep `symbolic checking` on `code:formal-model`.** Rejected. The model is not what a solver searches, and the method would keep asking for one only where it is least needed.
- **A new kind for contracts.** Rejected for now. The parser is where the solver pays, and `code:untrusted-input` already marks it. A kind for assertions or pre- and postconditions can come with the first stack that writes them.
- **Check that the fuzzer is coverage-guided by reading its configuration.** Rejected. It would name tools, which the method does not (ADR 0042). The proof shows the behaviour on any stack.

## For the builders

### What changes where

- `skills/method/application-types.toml`: two proofs under `[proofs]`; `fuzzing` gains `proofs = ["guarded-crash"]`; `symbolic checking` gets `trigger = "code:untrusted-input"` and `proofs = ["related-fields"]`.
- `skills/method/tiers.md`: rows `:100` and `:101`; row `:204` lists `fuzzing (2), symbolic checking (2)`; row `:212` lists `formal verification (2)` alone; the list of required proofs at `:276` gains these two.
- `skills/adopt/templates/justfile`: `symbolic` takes `each` and `cap` as `fuzz` does. The binding template's `symbolic checking` row names them.
- `skills/adopt/templates/quality/bites.toml`: the header names the two proofs.
- `scripts/conformance.py`: no new check. The existing one is red on a bound gate with no bite for a proof its question requires.
- ADR 0062: settled item 5 points here.

### Consequences

- A repository with a bound `fuzzing` row is red on upgrade until it has a `guarded-crash` bite, as any newly required proof is.
- A repository with a parser owes `symbolic checking` from the upgrade (item 3 below).
- Each parser costs one more tier 2 time box where a tool binds the solver.

## Settled by the owner, 2026-11-16

- **1. The fuzzing proof.** `guarded-crash` is required, as in point 2. Cost: one bite per repository with a fuzz target, whose seconds must be set so it is red every run. The other option was no proof, with the words alone asking for coverage guidance, which lets a blind fuzzer bind the row unseen.
- **2. The solver's row.** Its own row on `code:untrusted-input`, as in point 3. Cost: one more row per parser, often `none` on stacks with no tool. The other options were the hybrid mode inside `fuzzing`, or keeping the row on `code:formal-model`.
- **3. Repositories already adopted.** The upgrade writes `symbolic checking` as `later: code:untrusted-input`, owed at once where a parser exists, as ADR 0062 settled for its per-kind rows. The safeguard binds it, to a tool or to `none`. Cost: one safeguard run and one owner's merge in each such repository. The other option was to leave the row until the next parser arrives.
- **4. The solver's proof.** `related-fields` is required, as in point 4. Cost: one bite per repository that binds a tool. The other option was no proof, with a row bound to a tool trusted on its name.
