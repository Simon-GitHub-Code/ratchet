# The route

Four plugins build software on the method, and most of their skills are the owner's keystrokes. The harness enforces that: a skill marked `disable-model-invocation` is refused to an agent, in the main session and in every subagent, with the instruction not to reproduce it another way. So the method's job at every stage is to say which keystroke comes next, and `scripts/route.py` decides it from the checkout, never from a guess: `/ratchet:next` relays it, every ratchet skill ends with it, and the session hook prints it.

## The invocation rule

- **A user-invoked skill is the owner's.** In mattpocock-skills: `setup-matt-pocock-skills`, `grill-with-docs`, `wayfinder`, `to-spec`, `to-tickets`, `triage`, `implement`, `implement-spec`, `retro`, `handoff`. In pstack: everything except `setup-pstack`, so `poteto-mode`, `how`, `why`, `architect`, `arena`, `swarm`, `interrogate`, `blast-radius`, `no-comments`, `correct`, `reflect`, the verification skills. A page of this method names one of these only as a command for the owner to type, never as a skill an agent loads or a file an agent follows.
- **A model-invoked skill is anyone's.** In mattpocock-skills: `tdd`, `code-review`, `codebase-design`, `domain-modeling`, `diagnosing-bugs`, `grilling`, `prototype`, `research`, `pr`, `wizard`, `writing-for-agents`. `impeccable`, with every sub-command. `pstack:setup-pstack`. An agent calls these through the Skill tool by name, without waiting to be asked; the pipeline's roles use only these. Where the stage table below lists one under "The owner types", the owner may type it for a pass of their own, and an agent building or reviewing the work runs it anyway: an agent that builds a surface without `impeccable` because the table named it as a keystroke has misread the table.
- **Nothing is lost by the rule.** The owner types `/pstack:poteto-mode` once and it reaches `how`, `architect`, `arena`, `interrogate` and the principles on its own, since pstack chains its own skills by reading their files. `/mattpocock-skills:to-tickets` grills and models on its own. What the rule fixes is who presses the first key.

## The stages

Each stage names what the owner types, what exists afterwards, and what the method checks or guards there. Stages 4 to 9 repeat per feature.

| Stage | The owner types | Afterwards | The method's part |
| --- | --- | --- | --- |
| 1 Initialise | `/ratchet:init` | a git repository, `AGENTS.md` naming the four plugins, `CLAUDE.md` linked to it, `PRODUCT.md` from `impeccable init`, the pstack model rule, the first commit, the remote | the hand-over ends with stage 2's command |
| 2 Tracker | `/mattpocock-skills:setup-matt-pocock-skills` | `docs/agents/issue-tracker.md`, `triage-labels.md`, `domain.md`; the labels on the forge | the guard allows `gh label create` for a recorded name on this checkout |
| 3 Adopt | `/ratchet:adopt` | the binding, the gates and their thresholds, the manifest, CI, ADR 0001, the design brief and glossary from `grilling` and `domain-modeling` | the gates bite from the first commit; the path guards switch on when the adoption merges |
| 3b Hooks (whenever missing) | the one command the route prints when a commit would not run tier 0 (`GIT_CONFIG_GLOBAL=/dev/null pre-commit install` where a global `core.hooksPath` makes `pre-commit install` refuse; `git config --unset core.hooksPath` where the repository sets one) | the commit hooks installed, so tier 0 runs on every commit | `hooks_installed.py` proves it by running the hooks; the route, the feature preflight and `just ratchet` are red until then, and the guard refuses the command from an agent |
| 4 Understand and decide | `/mattpocock-skills:grill-with-docs`; `/mattpocock-skills:wayfinder` when the effort is too big for one session; `/pstack:how` and `/pstack:why` on code that exists; `/pstack:architect` at a boundary; `impeccable shape` before a surface | `GLOSSARY.md`, ADRs with their refuters, a design sketch, `DESIGN.md` | conformance checks the records; the records are guard paths |
| 5 Specify | `/mattpocock-skills:to-spec`, then `/mattpocock-skills:to-tickets`; `/mattpocock-skills:triage` for an issue that arrived | the spec issue; tickets with blocking edges, each with the agent-ready label the skill applies | the guard asks once per agent-ready label, since the label is the owner's agreement |
| 6 Conjecture | `/ratchet:conjecture <ticket>` | `## Conjecture` on the ticket, or on the spec its tickets name as parent: the approach, its refuter, the alternatives and the criticism that killed each | the feature preflight refuses a ticket without one |
| 7 Build | `/ratchet:feature <ticket>` when the owner steps away, or `/ratchet:feature` alone for every agent-ready ticket at once; `/pstack:poteto-mode <ticket, goal, done check>` when they stay | code, tests, commits on `feature/<n>-<slug>`; after a drain, the changes the judge passed merged and one report | the preflight before any agent; `check` on every commit; the guards hold whoever is building |
| 8 Criticise and open | `/pstack:interrogate`, `/pstack:blast-radius`, `/pstack:no-comments`, as the change warrants; agents run `impeccable audit`, `critique` and `polish` on a surface, and `code-review`, themselves | findings with the runs that prove them; the pull request with every template section, `check` green | every finding is a criticism the pull request answers; the merge judge decides who merges |
| 9 Keep | `/mattpocock-skills:retro`, `/pstack:correct`, `/pstack:reflect`; an agent runs `impeccable document` itself, and its write of `DESIGN.md`, a guard path, asks the owner | new lints and tests, skill edits, `DESIGN.md` current | a check that `correct` writes is a gate: `/ratchet:adopt` again binds it; `ratchet` raises the thresholds |

Debugging enters at stage 7: `diagnosing-bugs`, which an agent may call, or `/pstack:poteto-mode` with its bug-fix playbook.

A `/ratchet:feature` run that blocks leaves a stash named `<branch>: blocked run`. The route holds that ticket while the default branch has not moved past the stash, and names the questions or guard edits instead, so `/ratchet:next` never offers a rerun that would block the same way. The owner answering the ticket or landing the edits on the default branch releases it; an answer that moves no commit, as an issue comment on a forge, releases it once the owner drops the stash.

With more than one agent-ready ticket that states a conjecture the route names `/ratchet:feature` with no ticket: the drain, which builds them in work trees of their own and merges what the merge judge passes ([pipeline.md](pipeline.md), ADR 0023). A ticket the drain parked is held by its note in `.ratchet/queue/` until the ticket's text changes.

After its three lines the route prints an `also:` line for each question the owner has answered with the recommended option three times running (`scripts/questions.py`, ADR 0022). It proposes a standing answer and changes nothing.

An `AGENTS.md` that still holds the purpose placeholder sends the owner back to `/ratchet:init`, which a dismissed dialog leaves unfinished. A local spec with no tickets beside it is split with `to-tickets` before another spec is written. Two more states the route reads from the local tracker. A ticket whose `feature/<n>-*` branch is merged but which is still open is marked resolved before anything is built again. Open tickets of which none is agent-ready name the first unblocked one for the owner to agree, before a new spec is written.

## Where poteto-mode meets the guard

`/pstack:poteto-mode` is pstack's front door and runs its own playbooks. On a repository on the method they run under the guard, and three of them meet it:

- **Opening a PR** rebases unpushed commits into small ordered ones, which the guard allows; it amends only unpushed commits, since an amend of a pushed one is refused. Its `/deslop` and the control skills come from a Cursor kit that is not installed, and pstack's own adapter says to skip them.
- **Babysit, Shipping, Autopilot-full and Orchestrate** merge, retarget or land pull requests. A merge an agent types asks the owner: the guard turns their "land the stack" into one question per pull request. The merge job has by then merged what the merge judge passes, so what is left to land is the owner's.
- **Autonomy** in poteto-mode proceeds on ticket updates without asking. Applying the agent-ready label is the one update the guard asks on, because the label is the agreement the preflight reads.

Nothing here changes pstack. It says what the owner will see when its playbooks and the guard meet.
