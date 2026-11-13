---
status: accepted
---
# Only product questions reach the owner; every other choice is an assumption the hand-over states

Ratchet is for hobbyists and solo founders who are not engineers. The owner's principle: "We must never block the flow of someone's creativity, we are to help them not block them." He wants to be asked about the product he is building and about consequential direction, and about nothing else. The dogfood run of 2026-11-13 took a fresh project through `/ratchet:init`, the tracker setup, `/ratchet:adopt`, a spec and its tickets, and asked 19 questions on the way. Eight were about the product: its purpose, its user, what makes it better, where it is used, the first usable part, what waits, what becomes of a read item, what a duplicate link is. Eleven had an answer a default gives: a table of defaults to accept, the stack, whether it has a screen, the application types, the life events of a new project, the tools, three of the tracker setup's, the test seams, and whether to publish a ticket split. ADR 0022 had already ruled out a confirmation; the rule was prose, and each skill's own text kept its questions.

We decided five things.

1. **A question is a product question or it is not asked.** A product question is about what the thing should do, for whom, what matters, or a direction with real consequences that no experiment settles. ADR 0022's `direction` and `irreversible` are both product questions. Its third kind, `tooling`, goes: the tools question of `/ratchet:adopt` and of `/ratchet:safeguard` is an assumption now. This supersedes ADR 0017's "the owner is asked one question about tools" and ADR 0022's decision 2 where it names the tools question. Product questions go one at a time, in the words of someone using the product, with no method term.

2. **What replaces each removed question.** The agent takes the default the evidence supports, acts, and ends its reply with an **Assumed** block: one plain line per assumption, what it chose and why in a clause, and what the owner can say to change it. That block is the one place the owner reads them, at the end of the step he just ran; the decision itself lands in the record a later agent reads (`AGENTS.md`, `PRODUCT.md`, the binding), so an assumption he overturns later in his own words is overturned where it is kept. Init's defaults table, its stack and its interface; adopt's application types, its life events on a tree with no sign of one, and its tools; safeguard's tools: each is an `[[assume]]` entry of `skills/method/questions.toml`.

3. **The one outward act keeps its question, in plain words.** Init creates a private remote only from an answer to "Should this project be kept on this computer only, or also as a private copy on your GitHub account?", asked only when a forge login exists and no remote does. A wrong guess publishes the owner's work to an account; that is a direction with consequences, and the old defaults table hid it in a confirmation.

4. **The registry and a test make the rule structural.** `skills/method/questions.toml` lists each question a Ratchet skill asks (its key, the skills that ask it, its kind and its words) and each question a default now answers. A skill asks with `**Ask** `<key>`: "<the words>"` and states a default with `**Assume** `<key>``. `hooks/tests/test_owner_questions.py` is red on an unregistered key, a kind other than `product`, words that differ from the registry's, a question no skill asks, an assumption no skill states, a method term in a product question, and a sentence in any skill page that tells an agent to ask, confirm or wait for an answer without a registered key. `/ratchet:init interactive` and `/ratchet:adopt interactive`, which turned every default into a question, are deleted: the owner overturns a default in words.

5. **A conjecture accounts for each thing the owner said.** `/ratchet:conjecture` adds a *What you asked for* table to `## Conjecture`: one row per thing the owner said that the ticket touches, quoted from where the record keeps it, marked **built**, **assumed** or **left out** with the reason, and one row marked **added** for a behaviour nothing he said asked for. Adoption writes his answers word for word under `## In the owner's words` in the design brief, so the table has a source. Adopt also asks its questions at step 3, in the first minutes, rather than at steps 6 and 11, so the owner answers and leaves.

The tracker setup, `to-spec` and `to-tickets` are mattpocock-skills', and ADR 0009's floor stands: the owner types them. Their questions are governed only by the `## Asking the owner` rule in the `AGENTS.md` template, which every agent in the repository reads, and which now says that a tracker or labels already recorded, where tests reach the code, a ticket split and a draft to confirm are the agent's to choose and state. Decided 2026-11-13.

Supersedes, in part: ADR 0017's question about tools, and ADR 0022's `tooling` kind and its logging of init's and adopt's answers. The rest of both stands.

## Refutation criterion

This is wrong in either direction. Too tight: in the first ten first-hour runs after it, the owner overturns more than one assumption in five from the **Assumed** blocks, or says of a removed question that he wanted it asked. Too loose: a run asks the owner a question he says a default would have answered. The first-hour harness counts questions by class; a run that asks more than nine, or any confirmation, refutes the claim that the rule holds in practice and not only on the page.

Known residue. The test reads words, not meaning: a question phrased outside its vocabulary ("get the owner's OK") passes, as does a non-product question someone registers as `product`, which a reviewer reads in the registry's diff. Questions a companion plugin's skill asks are outside the registry. ADR 0022's log of answers now records only the feature pipeline's per-ticket questions, which never repeat under one key, so its `also:` proposal cannot form; ADR 0022's own criterion removes `questions.py` and the `also:` line, in a change to the route.

## Considered options

- Keep the rule as prose in each skill: rejected, that is ADR 0022, and the run asked eleven non-product questions under it.
- Show the assumptions in a file the owner can open: rejected, a file he never opens is not a place he reads. The end of the reply is.
- Keep `interactive` modes for an owner who wants every choice: rejected, they ask non-product questions by design, and a sentence in the owner's words overturns any default.
- Ask nothing at init on an empty directory and infer the product from the first spec: rejected, the purpose, the user and where it is used are product questions no experiment settles, and every later default leans on them.
