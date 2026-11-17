---
status: accepted
---
# A document has a kind, and a steering document leaves with its feature

Amends ADR 0031: the no-forge change description is no longer a record file (its row `.scratch/changes/<name>.md`), and a records change may delete a file `expire` lists. Amends ADR 0061: a labelled ticket that is resolved and whose build has merged, and the spec it inherits from, leave the tree without the owner's merge.

A plan written to steer an agent toward a feature is worse context than the code once the feature exists, and it keeps steering the next agent after it has gone stale. The method writes several such plans: the local spec and tickets under `.scratch/<slug>/`, the pipeline's `docs/features/<slug>/spec.md` and the no-forge change description under `.scratch/changes/` (`scripts/route.py:120`, `scripts/merge_ready.py:1066-1071`). Nothing removes them.

## Decision

**Every tracked markdown file has a kind.** The catalogue's new `[documents]` table holds the method's kinds with their paths, lifetime and whether the prune pass may cut them (ADR 0065). The binding's new `## Documents` table adds the project's own, each standing, with `Prune` either `never` or `pass`. The first match wins, the catalogue before the binding. The method's kinds:

| Kind | Paths | Lifetime | Prune |
| --- | --- | --- | --- |
| instructions | `AGENTS.md` at any depth | standing | never |
| binding | `docs/process/**` | standing | never |
| tracker setup | `docs/agents/**` | standing | never |
| glossary | `GLOSSARY.md`, `GLOSSARY-MAP.md` | standing | never |
| decision | `docs/adr/*.md` | standing | never |
| dispute | `docs/disputes/*.md` | standing | never |
| incident | `docs/incidents/*.md` | standing | never |
| product and design | `PRODUCT.md`, `DESIGN.md` | standing | never; their own plugin rewrites them |
| brief | `docs/design-brief.md` | standing | pass, never `## Purpose`, `## In the owner's words` or `## Explicitly out of slice one` |
| readme | `README.md` at any depth | standing | pass |
| local feature | `.scratch/<slug>/spec.md`, `.scratch/<slug>/issues/*.md` | steering | none |
| feature spec | `docs/features/<slug>/spec.md` | steering | none |

The prune pass keeps every heading `conformance` reads in the brief, `## Slice one` and `## Explicitly out` (`scripts/conformance.py:528-530`), so it may cut slice one's spent text but not its heading.

**The catalogue names no harness and no forge.** A harness's instruction files and a forge's templates are kinds the binding writes at adoption, from the harness and the forge already chosen there. The same move takes the forge paths out of the `depended-on` signs (`skills/method/application-types.toml:87`). The binding carries them, written at adoption.

**When a steering document has expired is read from the tree.** A local feature expires when every ticket under it is resolved (`scripts/feature_preflight.py:99`) and no open ticket elsewhere names a file in it as parent or blocker, as the preflight reads those lines (`:103-110`). A feature spec expires at any head that holds its verdict record, `docs/features/<slug>/verdict.json`, beside it (`merge_ready.py:117`). `expire` on a feature's branch also lists that feature's spec, whose verdict record is the change's last commit (`merge_ready.py:55-57`). No front matter, binding row or tracker call decides it.

**The feature's own change deletes it.** After the content commit and before the verdict record, the orchestrator runs `documents.py expire`. It deletes what that lists, in a commit of its own. It appends each `## Answers` line of a deleted ticket or spec, dated, to the brief's `## In the owner's words`. `expire` converges: it runs again after every update of the branch, and a second run changes nothing. The reviewer reads the spec in the work tree, as now.

**Expiry is judged on the change, at the merge.** The merge judge makes the check, comparing the merge base with the head. A unit this change makes expired, not expired at the merge base and expired at the head, and leaves in the tree is red. The judge refuses a branch that is behind (`merge_ready.py:1277-1283`), so whichever of several tickets merges last deletes the unit, in any order. A unit already expired at the merge base is never red, and no ledger entry records it. The route names a records change that deletes it, and the records check allows the deletion of a file `expire` lists, where today a record may not be deleted (`merge_ready.py:1115-1116`). So a change is never red for files it did not touch.

**A spent ticket is outside the owner's agreement.** ADR 0061 holds a labelled ticket and the spec it inherits from to the owner's merge in every change. The judge already refuses a change to one in a records change (`agreement`, `merge_ready.py:941-950`, called at `:1125`), whatever its status, since a ticket labelled at the merge base or the tip counts (`labelled`, `:779-796`). A deletion `expire` lists is not a change that rule protects when the ticket is resolved and its build has merged. The judge checks both facts at the merge base: the ticket's `Status:` line is a closed status, and the verdict record of its slug, found as ADR 0061 finds a ticket, is in the tree. The change that carries a ticket's own verdict record may delete that ticket too, as ADR 0061 lets it close it. A spec may be deleted when every ticket that inherits from it may be. Any other deletion of a labelled ticket or its spec stays the owner's merge.

**A modify/delete conflict in a unit's files resolves to the deletion.** When every conflicted path is in a unit `expire` lists, the drain's update takes the incoming side of each such path, then runs `expire` again, so an `## Answers` line the incoming edit added is carried to the brief before the files go. `expire` compares a line without its date, so a line already carried is not carried twice. A conflict confined to the brief's `## In the owner's words` resolves the same way, in update and not in Reconcile: the section keeps every line either side holds, each once by its text without the date, oldest date first. Otherwise Reconcile gets the rule in its prompt.

**The spec is found in history by its deletion.** A judge-again run takes the commit that deleted the spec, the newest such commit on the branch, and reads the spec from that commit's parent. No commit hash is recorded, so the read survives any number of rebases.

**Nothing reads a ticket file after the landing that could delete it.** Every reader of `.scratch/<slug>/` reads the note written when the ticket opened. Today every `drain.py` command that takes a ticket reads its file through `preflight.fetch`. For a ticket the drain already noted, it reads the note instead. The method has no links check today. One built later allows a target under a steering kind's path to be gone.

**The change description is a marked commit.** With no forge, the description is the message of a commit the branch marks as its description. A later commit may carry a newer one, and the newest marked commit is the description. How a commit is marked, by the change template's first heading or by a trailer, is chosen when it is built. `merge` copies it into the merge commit. Today the judge reads nothing of the file and only admits its path as a record (`merge_ready.py:1070-1071`), and `merge.py` only names it among the records `--records` lands (`scripts/merge.py:30-33`). The route names the marked commit where it names the file today (`route.py:616`). A `--records` change carries no description, and that exemption is written in the judge. On a forge the description is the pull request, as now.

**The checks.** `conformance` is red on a markdown file of no kind. An upgrade owes those files, and writing the project's kinds is a fill-first item. `ratchet-lowered` is red on a change that deletes a ticket or spec and drops one of its `## Answers` lines from the brief.

**What survives a deletion.** The owner's words go to the brief, which the spec role reads. The assumptions are in the verdict record already. The conjecture with its refuter, and the options the spec rejected, are in the change description and so in the merge commit. Git holds every deleted file. That is enough for what no step reads, and not for the owner's words.

**Tickets on a tracker are not files.** Closing one, as the change's `Closes` line does, is its end. A forge ticket is never deleted.

## Consequences

A branch squashed by hand loses the commit that deleted the spec. A judge-again run then falls back on the ticket and the verdict's assumptions.

## Refutation criterion

This is wrong if any of these is seen:

- A later spec contradicts an answer the owner gave on a ticket since deleted.
- A judge-again run reviews without its spec.
- Agents re-derive from history what a deleted spec said more often than once in twenty features.
- A change is red for an expired unit it did not touch.

Look at the drain reports and at the owner's overturned assumptions.

## Considered options

- **The landing deletes it, in `merge` or the merge job.** Rejected. It is a commit on the default branch itself, which the route never asks for (ADR 0031), or a merge that changes content, and a forge's merge job cannot make it.
- **A records change after the merge, for every feature.** Rejected. Every change until then holds an expired document, and a drain's next branch rebases onto it. It stays only as the way out for a unit already expired at the merge base.
- **Never commit the spec, and keep it in the clone's state directory.** Rejected. A built ticket judged again in a later drain finds no spec, and state outside git is cleaned away.
- **Tie a document to its feature by front matter or a binding row.** Rejected. Front matter is forgotten. A binding row is a guard path, so every feature would cost an owner's merge.
- **Delete each ticket when it is resolved.** Rejected. An open sibling that names it as a blocker then reads "was not found" and is blocked (`feature_preflight.py:546-550`).
- **Change descriptions as files that expire at their merge.** Rejected. The file must exist when the merge is judged, so only a later change could remove it.
- **Expiry judged at build only.** Rejected. Tickets of one unit that merge out of build order, or by hand later, leave the unit behind.
- **Record the sha of the commit that held the spec.** Rejected. A rebase rewrites it.

## For the builders

The route and drain models take these turns first: a unit expired with no deleter, and an update across a deletion. They go into `hooks/tests/route_model.py` and `hooks/tests/drain_model.py` before `route.py`, `drain.py` or `merge.py` change.

Tests:

- One ticket waits, a second lands, and the first is merged by hand later.
- A forge merges two changes out of build order.
- Split parts land after the sibling that ran `expire`.
- A records edit to the unit lands between a ticket's `expire` and its update.
- A judge-again run after two updates reads the deleted spec.
- A fix commit after the description, the `expire` commit after it, and a hand squash.

## Settled by the owner, 2026-11-15

- **What the brief is.** `docs/design-brief.md` stays a standing page. The prune pass may cut its spent sections, such as slice one's definition of done, and never `## Purpose`, `## In the owner's words` or the out-list.
- **A spent ticket leaves without the owner's merge** (was open item 4). A deletion `expire` lists, of a ticket that is closed and whose verdict record is in the tree, is outside ADR 0061's agreement rule, and the judge checks both facts at the merge base. A built ticket has nothing left to agree. Cost: a labelled ticket's file leaves the tree without the owner seeing it go. Its `## Answers` lines go to the brief, held by `ratchet-lowered`, and git keeps the file.

## Defaults accepted by the owner, 2026-11-15

Each item was a default taken overnight. The owner accepted every one as written on 2026-11-15.

- **1. The change description becomes a commit message.** The owner said the change description is deleted by the change that lands its feature. Default taken overnight: it becomes a marked commit's message instead. Recommendation: accept. A file must exist when the merge is judged, so only a later change could delete it. Cost: the letter of the owner's decision changes, and a no-forge description is read from history, not from a file.
- **2. Steering kinds belong to the method.** Default taken overnight: a project adds standing kinds only. Recommendation: accept. Expiry is read from the method's own tickets and verdict records, and no project row can say when its own plan is spent. Cost: a project's own plans are standing until it deletes them.
- **3. A changelog.** Default taken overnight: the method gives it no kind. A project lists it in `## Documents` with `Prune` `never`, and the plugin's own changelog is never pruned. Recommendation: accept. It is release history users read. Cost: one row to write at adoption. This differs from the design's default, under which a changelog was never pruned with no row to write.
