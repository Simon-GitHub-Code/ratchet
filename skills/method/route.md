# The route

Most skills of the four plugins that build software on the method are the owner's keystrokes, so at every stage the method says which one comes next. `scripts/route.py` decides it from the checkout, never from a guess. `/ratchet:next` relays it, every ratchet skill ends with it, and the session hook prints it.

## The invocation rule

The harness refuses a skill marked `disable-model-invocation` to an agent, in the main session and in every subagent, with the instruction not to reproduce it another way.

- **A user-invoked skill is the owner's.**
  - mattpocock-skills: `setup-matt-pocock-skills`, `grill-with-docs`, `wayfinder`, `to-spec`, `to-tickets`, `triage`, `implement`, `implement-spec`, `retro`, `handoff`.
  - pstack: everything except `setup-pstack`, so `poteto-mode`, `how`, `why`, `architect`, `arena`, `swarm`, `interrogate`, `blast-radius`, `no-comments`, `correct`, `reflect`, the verification skills.

  A page of this method names one of these only as a command for the owner to type, never as a skill an agent loads or a file an agent follows.
- **A model-invoked skill is anyone's.**
  - mattpocock-skills: `tdd`, `code-review`, `codebase-design`, `domain-modeling`, `diagnosing-bugs`, `grilling`, `prototype`, `research`, `pr`, `wizard`, `writing-for-agents`.
  - `impeccable`, with every sub-command.
  - `pstack:setup-pstack`.

  An agent calls these through the Skill tool by name, without waiting to be asked. The pipeline's roles use only these. Where the stage table below lists one under "The owner types", the owner may type it for a pass of their own, and an agent building or reviewing the work runs it anyway. So an agent that builds a surface runs `impeccable` even where the table names it as a keystroke.
- **Nothing is lost by the rule.** The owner types `/pstack:poteto-mode` once and it reaches `how`, `architect`, `arena`, `interrogate` and the principles on its own, since pstack chains its own skills by reading their files. `/mattpocock-skills:to-tickets` grills and models on its own.

## The stages

Stages 4 to 9 repeat per feature.

| Stage | The owner types | Afterwards | The method's part |
| --- | --- | --- | --- |
| 1 Initialise | `/ratchet:init` | a git repository, `AGENTS.md` naming the four plugins, `CLAUDE.md` linked to it, `PRODUCT.md` from `impeccable init`, the pstack model rule, the first commit, the remote | the hand-over ends with stage 2's command |
| 2 Tracker | `/mattpocock-skills:setup-matt-pocock-skills` | `docs/agents/issue-tracker.md`, `triage-labels.md`, `domain.md`; the labels on the forge | the guard allows `gh label create` for a recorded name on this checkout |
| 3 Adopt | `/ratchet:adopt` | the binding, the gates and their thresholds, the manifest, CI, ADR 0001, the design brief and glossary from `grilling` and `domain-modeling` | the floor bites from the first commit, and every other row is bound or `later: <trigger>`; the path guards switch on when the adoption merges |
| 3b Hooks (whenever missing) | nothing where the repository's own hooks are missing: the route prints the `hooks-install` entry point and an agent runs it. A change of git config is the owner's command (`git config --unset core.hooksPath` where the repository sets one; `git config core.hooksPath <the repository's hooks>` where a global hook does not chain to them) | the commit hooks installed, so tier 0 runs on every commit | `hooks_installed.py` proves it by running the hooks; the route, the feature preflight and `just ratchet` are red until then, and the guard refuses a change of git config from an agent |
| 4 Understand and decide | `/mattpocock-skills:grill-with-docs`; `/mattpocock-skills:wayfinder` when the effort is too big for one session; `/pstack:how` and `/pstack:why` on code that exists; `/pstack:architect` at a boundary; `impeccable shape` before a surface | `GLOSSARY.md`, ADRs with their refuters, a design sketch, `DESIGN.md` | conformance checks the records; the records are guard paths |
| 5 Specify | `/mattpocock-skills:to-spec`, then `/mattpocock-skills:to-tickets`; `/mattpocock-skills:triage` for an issue that arrived | the spec issue; tickets with blocking edges, each with the agent-ready label the skill applies | the guard asks once per call that applies the agent-ready label, since the label is the owner's agreement; one call over the tickets of a spec (`gh issue edit <n>... --add-label`, one command over the local ticket files) is one ask that names each |
| 6 Conjecture | `/ratchet:conjecture <ticket>` | `## Conjecture` on the ticket, or on the spec its tickets name as parent: the approach, its refuter, the alternatives and the criticism that killed each | the feature preflight refuses a ticket without one |
| 7 Build | `/ratchet:feature <ticket>` when the owner steps away, or `/ratchet:feature` alone for every agent-ready ticket at once; `/pstack:poteto-mode <ticket, goal, done check>` when they stay | code, tests, commits on `feature/<n>-<slug>`; after a drain, the changes the judge passed merged and one report | the preflight before any agent; `check` on every commit; the guards hold whoever is building |
| 8 Criticise and open | `/pstack:interrogate`, `/pstack:blast-radius`, `/pstack:no-comments`, as the change warrants; agents run `impeccable audit`, `critique` and `polish` on a surface, and `code-review`, themselves | findings with the runs that prove them; the pull request with every template section, `check` green | every finding is a criticism the pull request answers; the merge judge decides who merges |
| 9 Keep | `/mattpocock-skills:retro`, `/pstack:correct`, `/pstack:reflect`; an agent runs `impeccable document` itself on a branch, and the owner merges `DESIGN.md`, a guard path | new lints and tests, skill edits, `DESIGN.md` current | a check that `correct` writes is a gate: `/ratchet:adopt` again binds it; `ratchet` raises the thresholds |

Debugging enters at stage 7: `diagnosing-bugs`, which an agent may call, or `/pstack:poteto-mode` with its bug-fix playbook.

## The order the route asks in

`scripts/route.py` asks these in order and names the step of the first that holds:

1. **Setup:** `/ratchet:init`, the tracker setup, `/ratchet:adopt`, then the commit hooks (stages 1 to 3b).
2. **A records run that stopped on its `records/<time>` branch:** `just merge --records` again.
3. **Records left uncommitted on the default branch:** land them.
4. **Any other uncommitted change:** commit it.
5. **A working branch the default branch already holds:** `git switch <default>`.
6. **Any other working branch:** merge it, or with a forge, its pull request.
7. **A `records/<time>` branch waiting for its merge:** what the merge judge says.
8. **Rows an upgrade left owed that an agent writes from the repository alone:** `/ratchet:fill`.
9. **An owed architecture review:** `/ratchet:architecture`.
10. **An owed safeguard:** `/ratchet:safeguard`.
11. **An architecture an upgrade left owed:** `/ratchet:architecture`.
12. **The formal model's upkeep:** a ticket its last round filed, as work; else a `formal/upkeep-` branch waiting for the owner's merge; else, when mirrored code changed since `- Synced:`, `/ratchet:safeguard`.
13. **No glossary or design brief yet:** `/mattpocock-skills:grill-with-docs`.
14. **No agent-ready ticket:** agree an open ticket, then the conjecture of a local spec that lacks one, then split a local spec, then `/mattpocock-skills:to-spec`.
15. **An open ticket whose branch is merged:** mark it resolved.
16. **Every agent-ready ticket waits on an open blocker:** finish the blocker.
17. **Every unblocked agent-ready ticket is held:** release the first of them.
18. **More than one ticket states a conjecture:** the drain. **One:** its build.
19. **Otherwise:** `/ratchet:conjecture` for the first ticket.

Steps 9 to 12 wrap the step beneath them, the first of 13 to 19 that holds. The route names the first of 9 to 12 that is owed, and its `why:` line offers the step beneath: `Or carry on with <command>: <cost>`. Its `then:` names the next of 9 to 12 still owed, else the step beneath.

The formal model's upkeep is never red: a change to mirrored code may leave the model true, and only a round can tell. A round waits for the owner because it moves `- Synced:` and may change the model, both guard paths. A ticket from a round comes first: the update it keeps fails until the fix lands, and no pull-request run would show that, since tier 2 runs only on the default branch.

## States that decide `next:`

**An unfinished init (step 1).** An init stopped before its questions were answered leaves the purpose placeholder in `AGENTS.md`. An `AGENTS.md` that still holds it sends the owner back to `/ratchet:init`.

**An unfinished adoption (step 1).** A binding with no plugin pin is the template's: an adoption stopped after copying the templates. The route names `/ratchet:adopt` to resume it, since the hooks install cannot finish an adoption.

**Records left on the default branch (steps 2, 3 and 7).** A spec, tickets, a conjecture or an added term that a skill leaves uncommitted on the default branch land through the merge judge, never as a commit there.

- **No forge:** the route names `just merge --records`, which an agent runs. It commits them on a branch of their own and merges it when the judge and `check` pass.
- **A forge:** the route names a branch and a pull request.

A change among them is the owner's to merge when it does any of these:

- gives a ticket the agent-ready label;
- changes, for a labelled ticket:
  - the ticket itself, beyond the paths of its links to the same files;
  - the spec it inherits from;
  - whether a ticket it is blocked by is open;
- closes a ticket;
- brings an ADR, which the judge names by its title.

So a local ticket batch that `to-tickets` labelled costs the owner one merge. So do answers or a conjecture written on a labelled ticket, and marking a finished ticket resolved.

The `records/<time>` branch of such a change waits unmerged, and at step 7 the route asks the merge judge about it:

- **The judge leaves it to the owner:** the route names the owner's merge command with the judge's first reason.
- **The judge passes it,** as it does the branch of a run that stopped while `check` ran: the route names `just merge <branch>`, which an agent runs.

A `records/<time>` branch checked out (step 2) is a run that stopped before it switched back. The route names `just merge --records` again, which finishes it.

**A branch already merged (step 5).** A working branch whose every commit the default branch already holds is left, never merged again: the route names `git switch <default>`, then `/ratchet:next`. `just merge` leaves a clean checkout on the default branch itself once it has merged the branch it was on. So this state follows a merge cut off before that switch, or a merge by hand. With no forge, it also follows a branch cut and never built on.

**Rows an upgrade left owed that an agent writes from the repository alone (step 8).** They are the rows `owed.py --fill-first` lists: binding sections an upgrade added that the tree and its tools answer, such as Suppressions, Comments, Documents and Triggers, and a mutation recipe the plugin no longer reads. The route names `/ratchet:fill`, which reads the tree and its tools, proposes each row and writes it. The owner is asked nothing. The feature preflight and the drain refuse to start work while one is owed, and the refusal names the step, so the window after an upgrade is that one agent step.

**An owed architecture review (step 9).** Two things trigger one:

- a merged change brought the first instance of a kind that reopens the architecture (a store, a process that keeps running, a call to an outside system, a screen, a clock, a second person, parallelism);
- the owner declared a life event.

While no decision record names that trigger, the route names `/ratchet:architecture` and what the arrival reopens. An agent re-reads the architecture against the code and records that it holds, or asks the owner once how to change it. Its `then:` names `/ratchet:safeguard` when rows are owed too, since the review decides where the new kind lives and so where its safeguards go.

**An owed safeguard (step 10).** Two things fire a row:

- a merged change brought the first instance of a kind;
- the owner declared a life event.

While a row one of them fires is still `later:`, the route names `/ratchet:safeguard` and what is owed. An agent chooses the tool, writes the recipe and the fault, and binds the row.

- **It sits after steps 1 to 7,** since a safeguard is its own branch off a clean default branch.
- **It sits before steps 13 to 19,** since the next change that adds another instance of an owed kind is red, and binding first keeps that build from stalling.

The change that brought the kind was free, however many files it touched. The next one that adds another instance waits for the safeguard, so carrying on costs nothing until the owner adds another. A declared event is never red.

**An architecture an upgrade left owed (step 11).** A repository adopted before the architecture was asked has no decision record or no declared parts. The upgrade writes that down in `quality/owed.toml` under the `architecture` check, rather than turning anything red. While such an entry stands, the route names `/ratchet:architecture`. An agent drafts the options from the code, keeping what is here first, and the owner answers one round. The record and the binding's `## Architecture` section land in one change whose merge is the owner's. Nothing is red for it.

**No agent-ready ticket (step 14).** With local tickets the route asks three things first, in this order:

1. **Open tickets, none agent-ready:** the route names the first unblocked one for the owner to agree.
2. **A local spec with no tickets beside it that states no conjecture, or does not say how it is operated:** `/ratchet:conjecture <spec>`, since its tickets inherit both. `to-tickets` comes after it.
3. **Any other local spec with no tickets beside it:** split it with `to-tickets`.

With none of these, or when the tickets are a forge's issues, the route names `/mattpocock-skills:to-spec`.

**An open ticket whose branch is merged (step 15).** The ticket is marked resolved: `Status: resolved` on a local ticket, the issue closed on a forge. Its branch is the exact `feature/<n>-<slug>` name the feature preflight gives it from the ticket's current title. The branch counts as merged only as the second parent of a merge on the default branch's first-parent line, as `git merge --no-ff` makes.

**Every agent-ready ticket blocked (step 16).** When every agent-ready ticket waits on an open blocker, the route names `finish ticket <ref> "<title>", then mark it resolved` for the first open blocker it finds. When no blocker the tickets name can be found, it names `/mattpocock-skills:to-spec`, with `to-tickets` and the conjecture after it.

**A held ticket (step 17).** A free, unblocked ticket is built first, so a held one never stalls the queue. The route names a release only when every unblocked ticket is held, and then for the first of them. A blocked run or the drain's park holds a ticket.

**A blocked run.** A `/ratchet:feature` run that blocks leaves a stash named `<branch>: blocked run`. The route holds that ticket until what blocked it changes on the default branch, and names the questions or guard edits instead, so `/ratchet:next` never offers a rerun that would block the same way.

- What counts is the ticket's own text, the spec it inherits, and the paths the drain's note recorded as the cause (a guard path the run needed, a dispute it filed). Any other change, such as another ticket agreed or an unrelated binding edit, leaves it held.
- The owner answering the ticket or landing the edits on the default branch releases it. An answer that moves no commit, such as an issue comment on a forge, releases it once the owner drops the stash.

**A ticket the drain parked.** Its note in `.ratchet/queue/` holds it until one of these:

- the ticket's text changes;
- a path its run named as the cause (a guard edit, a dispute file) changes on the default branch since the note was written;
- the safeguard or review it waited on is bound or landed.

The drain's plan and the route share that one rule (`queue_notes.released`). The route reads that note, not only the stash, and names the step that releases the hold for each kind the note records:

- **Too large for one change:** the agent splits the ticket itself (`drain.py split`), so the route names a split only when that split was refused, as a change to the ticket's criteria.
- **Open questions:** the answers on the ticket.
- **Guard edits:** landing them on the default branch. When the note was written before the drain recorded the default branch's commit, landing frees nothing, so the route names a change to the ticket instead.
- **An owed safeguard:** `/ratchet:safeguard`.
- **Another instance of a kind that reopens the architecture, added while that kind's review was owed (step 9):** `/ratchet:architecture`.
- **Any other park:** a change to the ticket.

It never names a bare `/ratchet:feature` for a held ticket, since the drain would refuse it again. Dropping the stash releases nothing.

**The drain (step 18).** With more than one agent-ready ticket that states a conjecture, the route names `/ratchet:feature` with no ticket: the drain. It builds them in work trees of their own and merges what the merge judge passes. Read the drain section of [pipeline.md](pipeline.md) for how it plans, builds and merges them.

## The `also:` lines

After its three lines the route prints an `also:` line for each of these, in this order.

**The upgrade.** When the plugin in use is newer than the one the binding pins, the line names the upgrade command, which moves the pin, the workflow refs and the permission rules together. Read step 4 of [the next skill](../next/SKILL.md) before you relay it.

**A standing answer.** For each question the owner has answered with the recommended option three times running, the line proposes a standing answer and changes nothing. Read `scripts/questions.py` for which questions count and how an answer is recorded.

**A sign of a life event.** When the tree shows a sign of a life event the binding does not declare, the line names the sign, the event's meaning and `/ratchet:safeguard <event>`. A sign is a glob of the binding's `## Life event signs` table, or a shipped sign of `application-types.toml`, such as the files a maintainer writes for strangers. A README or a licence is no sign: a private project has them too. Declaring it is the owner's. When the owner says not yet, `/ratchet:next` runs `route.py --decline <event>`, which records each sign it saw in `.ratchet/events.tsv`. The line comes back only when a sign appears that was not there then, so an owner who said no is not asked again for the same evidence.

## What the route cannot see

**An incident.** When the owner reports one (something broke for them or a user, data was lost, a deploy went wrong), the agent they told proposes an incident record ([records.md](records.md#incident-record-docsincidentsnnnn-slugmd)).

## Where poteto-mode meets the guard

`/pstack:poteto-mode` is pstack's front door and runs its own playbooks. On a repository on the method they run under the guard, and three of them meet it:

- **Opening a PR** rebases unpushed commits into small ordered ones, which the guard allows. It amends only unpushed commits, since an amend of a pushed one is refused. Its `/deslop` and the control skills come from a Cursor kit that is not installed, and pstack's own adapter says to skip them.
- **Babysit, Shipping, Autopilot-full and Orchestrate** merge, retarget or land pull requests. A merge an agent types asks the owner: the guard turns their "land the stack" into one question per pull request. The merge job has by then merged what the merge judge passes, so what is left to land is the owner's.
- **Autonomy** in poteto-mode proceeds on ticket updates without asking. Applying the agent-ready label is the one update the guard asks on, because the label is the agreement the preflight reads.
