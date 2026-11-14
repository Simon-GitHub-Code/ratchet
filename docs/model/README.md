# Models

A model here is a small state machine that says what one part of the plugin should do. The tests hold the real code to it. A change that breaks a rule the model states is red in a test, and the page for that model shows the change on a chart.

| Model | Page | Module | Tests |
| --- | --- | --- | --- |
| The route: the next command for the owner | [route.md](route.md) | `hooks/tests/route_model.py` | `hooks/tests/test_route_model.py` |
| The threshold ratchet: what the default branch can hold | [ratchet.md](ratchet.md) | `hooks/tests/ratchet_model.py` | `hooks/tests/test_ratchet_model.py` |
| The safeguard a project owes as it grows | [safeguard.md](safeguard.md) | `hooks/tests/safeguard_model.py` | `hooks/tests/test_safeguard_model.py` |

## What a model holds

A model module in `hooks/tests` is pure. It reads no file and runs no git. It defines:

- a frozen state, and the actions that change it, each yielding the states it can lead to;
- `graph()`, every state reachable from the start and the edges between them;
- `RULES`, the rules by name, each one a check the tests run on the graph.

A model with a page also defines `export()`, the graph and the rules as a TLA+ module, written by `hooks/tests/model_tla.py`. There each rule is a sentence, and TLA+ text, a predicate on a state, or a predicate on a step.

TLA+ cannot see a step that leaves every variable as it was, and such a step can break a rule: a landing the gate refuses changes nothing. So a rule on a step is checked as an invariant. The module holds each state of the graph once for each set of step rules the step into it broke. Where no such rule breaks, the module has the graph's states and steps and no more.

The statechart tool draws a module of at most 500 states. A model with more also defines `summary()`: its graph seen through the few values the charts show, one state for each set of values and one step for each action between two. Its charts are drawn from the summary, and its rules checked on the whole module. The page names the SHA-256 of each module.

The tests prove each rule on the graph in Python. They do not run a model checker. The export is for the charts, and for anyone who wants to check the rules again with one.

Where the real code does something other than the model says, the model names it as a divergence: what the code does instead, the sentence of the method's pages it goes against, and the rule that breaks. The tests hold the code to the model with its divergences applied. Each divergence also has a test that expects the code to fail and is red the day the code is fixed. Then the divergence is deleted.

## Changing the behaviour

Change the model first, then the code.

1. Change the model so it says what the code should do. The model's own tests say whether every rule still holds.
2. Change the code. The tests that run the real code say where it does something else.
3. Draw the charts again, as below. A test is red until the page names the SHA-256 of the current export.

When a rule breaks on purpose, say why in the change, and change the rule.

## Drawing the charts again

The charts are drawn by the `statechart` command of pstack (`/pstack:statechart`), which runs TLC on the export and draws one Mermaid chart for each view. A view is a small module beside the page, in `docs/model/<model>/`, that gives each state and each step its words.

From the `hooks` directory, write the export to a directory outside the checkout:

```sh
python3 -m tests.model_tla tests.route_model /tmp/route-model
sha256sum /tmp/route-model/Route.tla
```

Then, from the repository root, run the `statechart` command once for each view, each into a new directory:

```sh
statechart /tmp/route-model/Route.tla --config /tmp/route-model/Route.cfg \
  --view docs/model/route/view_work.mjs --out /tmp/route-model/work
```

A model with a summary writes it beside the module, as `RatchetDefault.tla` beside `Ratchet.tla`. Draw its views from the summary, and run the tool once on the whole module for the rules. Above 500 states the report counts the states but not the steps. The last line of `tlc.out`, `N states generated`, counts the start and each step. It is the first run of TLC, which stops at the first rule that breaks, so for the count run the tool again with a config that names only the rules that hold.

Put each `chart.mmd` in its block on the page, the counts from each `report.md` beside it, and each SHA-256 on the line that names it.
