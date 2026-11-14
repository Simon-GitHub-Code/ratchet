---
status: accepted
---
# The route is held to a model of what the pages say

`scripts/route.py` names the owner's next command from the checkout. What it should name lived in two places: the prose of `skills/method/route.md` and the order of the questions in `route.decide`. The tests in `hooks/tests/test_route.py` check states that someone thought to build. A change that named a step the owner cannot take, sent the owner round in a loop, or named another step than the pages say could pass them in every state no one built. Writing the route down as a machine found two places where the pages made a promise that the rule they gave for it did not keep, and the route followed the rule. Any change the owner merged released a blocked run, so the route could offer a rerun that blocks the same way. The route was fixed for that while the model was written: the fix turned the model's expected failure for it red, and the model's entry for it was deleted, as the decision below asks. The other was a guard hold that outlived the edits it asked for, so the route named landing them again once they had landed. The drain's note now records the paths a run named as its cause and the default branch's commit when it parked, and landing those paths releases the hold. That fix turned the second expected failure red too, and its entry was deleted. The model has no divergence left.

## Decision

**The route has a reference model, and the model says what the pages say.** `hooks/tests/route_model.py` is pure: a frozen state, the steps that change it, and the graph of every state reachable from an empty directory. Its label in each state is what `route.md` says the route names there, asked in the order `route.decide` asks. Where the pages' promise and their rule part, the model keeps the promise. Its rules: the step named can be taken, following the steps named reaches rest, and each one gets nearer to it. [docs/model/route.md](../model/route.md) is the page for the owner, with the model's states, labels, steps, rules and charts.

**Where the route does something else, the model names it.** A divergence says what the route reads instead, the sentence of the pages it goes against, and the rule that breaks. The tests hold the real route to the model with the divergences applied, so a new difference is red. Each divergence also has a strict expected failure that asks the route for what the pages say, so the fix is red until the divergence is deleted. A model that copied the code would agree with every fault in it.

**The real route is asked in every state of the model.** The sweep walks the graph's shortest-path tree on a real repository, copies it where the tree branches, and asks `route.decide` once in each state. Walks along the advice and long stateful runs take the paths the sweep does not. The rules are proved on the graph in Python. No test runs a model checker or needs Java.

**The charts are drawn from the model, and a test keeps them current.** `hooks/tests/model_tla.py` writes any model's graph and rules as a TLA+ module. The `statechart` command of pstack checks that module with TLC and draws a chart for each view. The page names the SHA-256 of the export it was drawn from, and a test is red when the model changes and the page does not. A change to what the route names starts in the model.

## Refutation criterion

This is wrong if, within the model's scope, a change to `route.py` that names a step the owner cannot take, loops, or names another step than the model gives passes `check`. It is also wrong if the sweep cannot stay inside the test gate's time ceiling without leaving states out.

## Considered options

- **More hand-built state tests.** Rejected as the only check: they cover the states someone thought of, and they hold the route to the rule it was written from. Until the fix, `test_route.py` asserted that an unrelated decision releases a blocked run.
- **Stateful runs alone.** Rejected as the only check: the stateful test, steered toward every label, reaches about a fifth of the states, and says nothing of the others.
- **A model that follows the code, with its conditions narrowed where the code differs.** Rejected: narrowing hides the states where the route names a step that cannot be taken, which is the fault the model is for.
- **Running TLC in the test gate.** Rejected: it needs Java on every machine that runs `check`, and a graph of a few hundred states is checked in Python in a fraction of a second.
