---
status: proposed
---
# A method page says what to do and why, never what a script does

Amends ADR 0053.

Four reviews compared the rewritten method pages with the scripts they describe. They found about forty places where a page was wrong or incomplete, and each was wrong in the old page too. Thirteen were on the route page and nineteen on the pipeline page. Others were on the feature page's exit codes and on the method page. Commits 41f8d23 and e5c80c0 correct the route and pipeline pages against the code, sentence by sentence. A sentence that restates code drifts, and the fix is not a better sentence.

## Decision

Each sentence of a page under `skills/` is one of three kinds:

- an **instruction**: what an agent or the owner does;
- a **reason**: why a rule holds, which no code says;
- a **restatement**: what a script does, such as the route's order, the pipeline's loop, a gate's verdict words, an exit code, a list held in a constant, or the checks a gate makes.

Instructions and reasons stay. A restatement goes. When an agent must act on it, the script prints it at that moment. When a reader needs an overview of a state machine, its model page under `docs/model/` carries it, generated from the model or held to it by a test. A page that keeps a copy of a list a script holds has a test that compares the two. A defect a review finds in a restatement is fixed by deleting the sentence.

An exit code or a verdict word that a step branches on is an instruction until the script prints the next step. It is not deleted as a restatement before then. A line format a script parses is an instruction, since the agent writes it. Where only a page states it, a test holds the page's text to the parser.

This amends ADR 0053. The route model (`hooks/tests/route_model.py`) is the specification of the route's order, not the route page. ADR 0053 has the model say what the pages say, and keep the pages' promise where it and the rule part.

## Consequences

The feature page keeps its per-exit tables until `scripts/drain.py` and `scripts/split.py` print the next step for each exit. That rewording lands first, and the page loses the tables after it. A list of the sentences kept for this reason is the input that says what each script must print first.

## Refutation criterion

This is wrong if agents in adopted repositories act wrongly for want of a sentence deleted under this rule. That shows in a drain report or an eval run that names the missing fact.

## Considered options

- **Correct each page against its script.** Rejected. Every review found new drift, and the writers kept adding script detail.
- **Generate whole pages from the code.** Rejected. Instructions and reasons are not in the code.
