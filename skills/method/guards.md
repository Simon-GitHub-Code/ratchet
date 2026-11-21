# Guards and disputes

Agents push and drive the forge's CLI as the owner, so the forge cannot tell an agent's change from the owner's, and the gates, the main quality control, are protected themselves: enforced in the harness, which the model cannot override, and detected in the diff for whatever slips past. Read [The hook](#the-hook) when an edit or a command is denied or asked, [The merge judge](#the-merge-judge) when a merge is left to the owner, and [Disputes](#disputes) when you believe a gate is wrong.

## The manifest

`guards/manifest.toml` is the single list of guard paths, in groups with a reason each. Three things read it, so a guard path is one entry here and nowhere else: the hook and the merge judge, as the default branch holds it, and the diff check.

```toml
[[group]]
name = "ratchets"
reason = "Recorded thresholds, the mutation register and config snapshots only ever rise."
paths = ["quality/**"]

[[group]]
name = "harness"
reason = "Settings, hooks, workflows, agents and skills act in any session started in the checkout that holds them, before a merge; the owner edits them by hand."
edit = "deny"
paths = [".claude/settings.json", ".claude/hooks/**", ".claude/skills/**", "~/.claude/settings.json"]

[[group]]
name = "records"
reason = "An agent adds a term, an Avoid item or a new ADR; what is already there changes by the owner's hand."
edit = "append"
paths = ["docs/adr/**", "GLOSSARY.md"]
```

A group's `edit` says what a change to its paths gets:

- **No `edit` key, or `ask` (its old name):** an agent changes the path on a branch with no prompt. The change reaches the default branch only through a merge, and the merge judge sends it to the owner. Where the commit would land on the default branch, the hook denies the change and says to make it on a branch.
- **`deny`:** locks a group the owner edits by hand.
- **`append`:** marks records an agent may add to, on the default branch as well.

A group may carry `exclude` patterns its paths do not cover.

Edit-time control stays for exactly the files whose effect comes before the merge, and everything else is judged at merge. Beside the locked groups, two kinds of file act the moment they are written: CI workflows (the files directly under `.github/workflows/`) and agent-instruction files at any depth. The hook asks the owner before each write of either that it reads, on every branch ([A file that acts at once](#a-file-that-acts-at-once-ask)). They need no group of their own.

The manifest also lists the one-way doors, as `[[door]]` tables: paths whose change cannot be taken back once it ships. A door guards nothing.

### Changing the manifest

When a change needs a new guard path (a tool's config, for example), the agent adds it to `guards/manifest.toml` on its branch.

The manifest is locked against loss and open to additions. The hook and the merge judge read it as the default branch holds it, so a branch that edits it changes nothing until the owner merges it, and the judge sends that merge to the owner as a guard path change.

- On a branch, an Edit or Write of `guards/manifest.toml` passes when all it does is add: a path after the others in a group's or a door's `paths`, or whole `[[group]]` or `[[door]]` tables after the ones there.
- The first group that matches a path wins, so a path added to a group that also matches a pattern of a group below it would take paths from that group. Such a path, every other change, and every shell write, move or delete of the file are the owner's by hand.

## The hook

The rules live once, in `hooks/ratchet_guards`. The hook checks an agent's ordinary writes. It is not a boundary against an agent that sets out to get round it, and ADR 0033 lists the writes it does not read.

### A guard path: pass on a branch, deny on the default branch

An Edit, Write, MultiEdit, NotebookEdit, patch or shell write of a path in a group that is not locked gets no verdict when the commit it would go into lands on a branch other than the default one. Such a change reaches the default branch only through a merge, and the merge judge sends a guard path change to the owner with its reasons, so a prompt at the edit would have asked the owner the same question earlier, with less to go on.

- **A detached `HEAD`** is a branch here: nothing reaches the default branch from it but a push or a merge, which the rules below judge.

### An addition to a record: pass

An addition to a record, in a group marked `edit = "append"`, passes on the default branch as well. The hook works out the file an Edit, Write or MultiEdit would leave and compares it with the file at the merge base of `HEAD` and the default branch, so a branch may rework what it added and may not touch what the owner merged.

- **A glossary** passes when everything the merge base holds is still there, in order and under the same heading (each term with the lines that define it, each `_Avoid_` item, each heading, each line outside the entries), and what came in is a term, an `_Avoid_` item or a cluster heading.
- **A decision record** passes when the file is new, named `NNNN-<slug>.md` with a number above every one the merge base holds, has a title, carries no `Relaxes:` line and does not speak of superseding.
- **Everything else on those paths** is denied on the default branch, with the reason: a changed definition, a renamed or moved term, a dropped `_Avoid_` item, an edited ADR, and anything the hook cannot read (a term defined twice, an indented `_Avoid_` line, a tool whose result it cannot compute, which today is every tool but Claude Code's three). On a branch it passes, and the merge judge reads it.

### A file that acts at once: ask

Two kinds of file act before any merge the judge could read:

- **CI workflows.** GitHub runs a branch's own CI workflows, the files directly under `.github/workflows/`, on a push or a pull request, with the repository's token and secrets.
- **Agent-instruction files.** Claude Code loads an agent-instruction file, `CLAUDE.md`, `CLAUDE.local.md`, `AGENTS.md`, `GEMINI.md` or anything under a `.claude/` directory, at any depth, and follows it. It loads a nested `CLAUDE.md` once an agent reads a file beside it, so an agent could write one, let the critic and reviewer run under it, and delete it before the commit.

`.claude/worktrees/` is not an instruction directory, since each worktree under it is a checkout judged from its own root. Like every path rule this is on only once the manifest is on the default branch, so `/ratchet:adopt` writes its workflows and instructions without a prompt.

### A merge an agent types: deny once adopted

A change reaches the default branch through the merge judge or by the owner's hand, and what merges by itself already runs: the merge job on a forge, the `merge` entry point with no forge. So once the manifest is on the default branch ([When the path rules are on](#when-the-path-rules-are-on)) and the `justfile` there defines both the `merge-ready` and the `merge` recipe, the hook denies a merge an agent types: `gh pr merge`, the forge's merge, branch-merge and auto-merge API, and, on the default branch, `git merge`, `pull`, `cherry-pick`, `am` and `read-tree`. The deny names what merges by itself and `merge-ready --head <branch>`, which prints each reason a change waits. A merge the judge leaves to the owner is the owner's to make, in the forge's interface or their own terminal.

- **Before adoption, or where the default branch lacks either recipe,** there is no way out to name, so the hook asks the owner, with the same words as before. A recipe removed only in the work tree or on another branch still leaves the deny.
- **A sync** passes in either case: a merge or a pull on the default branch whose every operand is the default branch's own remote-tracking ref, named or as its upstream, with or without `--ff-only`, and a resume (`--abort`, `--continue`, `--quit`, `--skip`). An operand git reads as anything else (`FETCH_HEAD`, another remote's branch, a local branch or tag of the same name, an upstream moved to another branch), a `-c` setting on the same `git` other than a `color.*` or `advice.*` one, which change only what git prints, or an earlier command on the line that may have changed what a name points to makes it a merge.
- **A merge tried out locally** runs on a detached `HEAD` or a scratch branch, where no merge rule fires. Switch to it in a tool call of its own, so the guard reads where the merge lands.

### The agent-ready label: ask

The prompt is a plain sentence the owner can answer without knowing the guard. The label is the owner's agreement that a ticket is ready to build, and the feature preflight reads it as such.

### A locked file: deny

An edit in a group marked `edit = "deny"` is denied: the harness and local git. Their files act in a session started in the checkout that holds them, before any merge, so no merge judge reads a change to them. `.claude/settings.json` is in the locked harness group, so an agent cannot write it once the project is adopted. Two sets are always locked:

- the plugin's own files, every harness's installed plugins and the settings that enable them: `~/.claude/plugins/**` and `settings.json`, `~/.grok/installed-plugins/**`, `~/.grok/hooks/**` and `config.toml`, `~/.codex/plugins/**`, `hooks.json` and `config.toml`, `~/.pi/agent/extensions/**` and `settings.json`, `~/.config/opencode/plugins/**` and `opencode.json`
- the `git-state` group in every checkout, adopted or not: `.git/refs/**`, `packed-refs`, `HEAD`, `info/grafts`, `shallow` and `objects/info/**`, because the default branch's ref is what the path rules are read through

### A command that bypasses a gate or rewrites history: deny

An interpreter given its program inline is read by its text. `python3` (any `pythonX.Y`) with `-c` or with a program on stdin from a heredoc, a here-string or a pipe, `node -e`, `--eval`, `-p` or a heredoc, `perl -e`, `ruby -e`, `deno eval` and `bun -e` are denied when the program holds a quoted string naming a guard path (resolved against the working directory) and a write call (`open` for writing, `write_text`, `unlink`, `rename`, `shutil`, `subprocess`, `os.system`, `fs.writeFile`, `rm`, `child_process`, `File.write`, `open` with `>`, any `-i`).

- Where a write of that path would be denied, one that reads it and writes an unguarded file is denied all the same, so the read goes in a call of its own.

**A recorded label passes.** One write passes by a record: `gh label create <name>`, on the checkout's own repository, with no `--force`, when `docs/agents/triage-labels.md` at the checkout root lists `<name>` in its "Label in our tracker" column. Creating a label routes nothing by itself, the list is the owner's answer to the mattpocock-skills setup, and `docs/agents/**` is in the decisions group once the repository is adopted.

### What a deny says

A verdict covers the whole tool call. Claude Code runs none of a denied command line, so a file write chained before the refused command is lost with it. Keep a command the guard may refuse (a forge CLI call, a push, anything near a guard path) in a tool call of its own, so an unrelated write is never lost with it.

### The guard's log

The guard writes one line for each tool call it denies or asks about to `ratchet-guard-events.jsonl` in the repository's git directory, shared by every worktree. Each line holds the time, the decision, the tool, the rule, what the guard matched and the branch. The file is local, never committed and never shown by `git status`, and the hook denies every edit of it. The settings deny list holds it too, as `Edit(/.git/ratchet-guard-events.jsonl)`, so the harness refuses an edit even where the hook does not run. That rule reaches the main checkout only: in a linked worktree `.git` is a file, and the hook alone guards the log there. With no forge it shows whether work reached the default branch past the guard.

The session hook records the folder of the plugin copy it runs from in `ratchet-plugin`, in the same git directory, and the local gates run the scripts it names (ADR 0068). A path pointed at a doctored copy would weaken every local gate, so the hook denies every edit of it in a group of its own, `plugin-record`, and the settings deny list holds `Edit(/.git/ratchet-plugin)`, which reaches as far as the log's rule.

The hook writes its answer first and the line after, so the log never changes a verdict. A hook that cannot finish judging in 7 s, inside the harness's 10 s limit, asks, and its reason says it ran out of time. A deny it reached before then stands instead. Either answer gets its line like any other. Three cases write no line: the ask the guard falls back to when it cannot load, a hook the harness cancels at its own limit, and a harness's own prompt. A deny on a path in another checkout is logged in the repository the session works in.

### When the path rules are on

The path rules are on only once `guards/manifest.toml` is committed on the default branch: the branch `refs/remotes/origin/HEAD` names, else `refs/remotes/origin/main`, `refs/remotes/origin/master`, `refs/heads/main` or `refs/heads/master`, each read by its full name and skipped when it names no commit. The hook reads that commit from git and judges every path against it, whatever the work tree says. It is not a flag an agent could set, and not the work tree, where an adoption in progress is still writing the manifest and the files it names.

- **Until the adoption merges,** a manifest that is untracked or committed only on a branch guards nothing, and the command rules apply alone.
- **After it merges,** the owner's merged manifest governs even if the work tree's copy is edited or deleted.

The command rules are on from the first session, before `/ratchet:adopt` has written a binding. Anything an agent can set is something an agent can forge, so the guard has no off state. The one command rule that reads the adoption is a merge an agent types, and it reads it from the same committed manifest, with the committed `justfile`'s `merge-ready` and `merge` recipes: before both are there, the hook asks the owner where after it denies. `/ratchet:init` is written so that none of its steps provokes a denial. Its label step uses the [recorded label](#a-command-that-bypasses-a-gate-or-rewrites-history-deny) path.

### The permission rules

The permission rules in the project's `.claude/settings.json` are a second, independent layer, the same in every repository and never written by hand. `just settings` writes them, and `just settings-check` (tier 1) fails on a rule missing from or foreign to the lists. `just settings`:

- keeps a rule no version of the plugin writes, which the check still reports
- writes nothing under a plugin other than the one CI checks out, whose check would reject what it wrote

Of the fixed command rules in `deny`:

- `git symbolic-ref` has no rule because it also reads, and the hook is the one layer on its other spellings.
- `gh label create` is not in the deny list, because the hook lets a recorded label through and a settings deny would refuse that one too.

The `ask` list is empty. A rule matches a path or a command and cannot tell a branch from the default one, or read an edit, so the hook is the one layer that asks, and its prompt carries the reasons. On every guard path outside the locked groups the hook is the only layer in the harness, and the merge judge is what stands behind it.

## The diff check

Some writes are out of the hook's reach: an interpreter running a script file by path (`python3 tool.py`), a path an interpreter builds at run time (a variable, a concatenation, a mode held in a variable, a decoded or fetched program), `git apply`, merges, variable paths in the shell, edits made outside the harness, and a ref name a variable, a file or a pipe supplies to a `git` command that would move the default branch.

The `guard-diff` entry point (tier 1) lists every changed path that matches the manifest at the merge base or at head, with renames split so moving a guard file away is caught. The merge judge reads the same listing from the commits, and a change it finds a guard path in waits for the owner.

- **Under a dependency manifest, a lockfile or a snapshot of one,** the listing adds what the change does: the packages added, or each locked package's old and new versions, when that is all it does, and otherwise why not (another key changed, an existing entry changed or went, a source other than the registry, an artifact changed in place, a version that falls, a format it does not read). The path is still a guard path and still counted. A dependency is a decision the pull request states ([code.md](code.md)). The owner-away pipeline accepts one only when its spec named it and the listing found nothing else in the file.

## The merge judge

A change reaches the default branch in one of two ways: the merge judge passes it and it merges itself, or the owner merges it. The judge is `scripts/merge_ready.py`, behind the `merge-ready` entry point.

The owner merges when any of these holds, each under its own heading below: a guard path changed, a one-way door once the project is `deployed` or holds `irreplaceable-data`, new surface with no flag once it is `deployed`, a dispute, no clean approval, or the setting.

### A guard path changed

The listing is `guard-diff`'s. Three changes pass:

- an addition to a record
- `quality/thresholds.toml` when every number moved the tight way and nothing else in it changed, which is what the `ratchet` entry point does after every feature
- `quality/owed.toml` when entries only go and the plugin pin does not move

A dependency change does not pass, whether the spec named it or not: the spec is an agent's, so the judge prints the dependency note and the owner merges.

An instruction file is a guard path wherever it sits, whatever the manifest says: `AGENTS.md`, `CLAUDE.md`, `CLAUDE.local.md` or `GEMINI.md` in any letter case and at any depth, and any path under a `.claude/` directory.

### A one-way door

A door sends the merge to the owner only once the project is `deployed` or holds `irreplaceable-data`. Before either is declared, nothing is lost by walking back through a door.

A door is a path in a `[[door]]` table of the manifest:

- `on = "change"` is for migrations, schema and infrastructure: any change shuts the door.
- `on = "rewrite"` is for fixtures of stored data: a new file passes, a changed or deleted one does not.
- `on = "removal"` is for contract artefacts and public-surface snapshots: an addition passes, a removed or changed line does not.

A door is no guard path, so the hook never asks on it and an agent builds the change. The flag registry is always a door: a flag that turns on by default, becomes permanent, or leaves the registry while off waits for the owner.

### New surface with no flag

New surface sends the merge to the owner only once the project is `deployed`. Before that nobody meets the surface.

The change adds to a file behind an `on = "removal"` door (a route, a command or an exported symbol in a contract artefact or public-surface snapshot) and no flag hides it: `flags.toml` gains no flag that is off, and no line the change adds outside `docs/`, the registry and those files names one that is off. The judge prints `unflagged` and the owner sees what ships. It is asked of the application types the method asks the flag questions of, read from the binding on the default branch, so a library is exempt.

### Before a life event is declared

When a door or new surface is let through because no event is declared yet, the judge prints a `note:` line saying what it let through and why, so the owner sees it. The judge reads the binding's `- Life events:` line at the merge base and at the default branch's tip, never at the head, so a change that declares an event, or drops one, is not judged by its own line. The binding is a guard path, so declaring an event is itself a change the owner merges. Guard paths, disputes and the clean-approval rule below hold from the first commit whatever the events.

### A dispute

The change adds or edits a dispute, or a dispute not yet decided names a path the change touches.

### No clean approval

The pipeline's verdict record, `docs/features/<slug>/verdict.json`:

- is missing
- is not an approval
- lists an unresolved blocker or major finding or an open question
- or certifies another tree

The record holds the tree the review saw, so a commit made after the review sends the merge to the owner. A minor finding does not.

**Records alone** are asked for no verdict record. A record is a file in the whole shape a skill writes it:

- a ticket `.scratch/<feature>/issues/<n>-<slug>.md`
- a spec `.scratch/<feature>/spec.md`
- a change description `.scratch/changes/<branch>.md` (the branch's `/` written as `-`)
- a term added to `GLOSSARY.md`
- an ADR `docs/adr/<nnnn>-<slug>.md`

By the default branch's manifest no guard group holds the first three and only `append` groups hold the last two, and no door holds any. No instruction file is a record, at any depth. Any other name, under `.scratch/` or not, makes the change more than records.

Among records alone the owner merges:

- a ticket change that gives the agent-ready label (other than to a part split from a labelled ticket that keeps to its promises), takes the number of another ticket beside it, or closes a ticket
- any change to a labelled ticket, other than its `Parent:` and `Blocked by:` lines rewritten to name the same files
- any change to the spec a labelled ticket inherits from
- any change to whether a ticket a labelled ticket is blocked by is open
- an ADR, which the judge names by its title for the owner to agree
- a record that changed on the default branch after the change forked
- a deleted record, one with a twin in another letter case, one that is not a plain file of mode `100644`, or one that is not text

### The setting

- The pull request targets a branch other than the default.
- The branch does not hold the default branch's tip, so its gates ran on an older one.
- Tier 2 is red on the default branch.

### The adoption

The adoption is the change that brings the manifest and the binding to a default branch whose tip and merge base hold neither. It is judged by the manifest it brings. No guard path, door, unflagged surface or missing verdict record sends it to the owner, since nothing before it was judged. Two things do:

- an instruction file at any depth, other than the root `AGENTS.md` and `.claude/settings.json` that `/ratchet:adopt` writes
- a ticket that carries the agent-ready label

Its manifest must instead guard at least what the template's does: every template group in order, with its rule, its exclusions and its paths. It may add paths to a group that is not `append`, and it may add groups after the template's. The setting, disputes, the flag registry and the file modes still apply, and the judge prints a `note:` naming the adoption.

### How the merge happens

**On a forge** the `merge` workflow asks the judge once `ci` is green on the pull request.

- A run counts as green only when it tested that pull request against the default branch, so one that was retargeted, or whose branch has had a pull request against another base, is the owner's to merge.
- The workflow is triggered by `workflow_run`, which takes the workflow file, the judge and the manifest from the default branch, so a pull request cannot change what judges it. The job reads the pull request's commits without checking them out or running them.

**With no forge** the `merge` entry point (`scripts/merge.py <branch>`) merges the branch in a scratch work tree, asks the judge, runs `check` on the merge result, and moves the default branch only when both pass. For the adoption it runs `check-deep` there instead. `merge.py --records` lands the records a skill left uncommitted on the default branch: it commits them on a branch of their own, `records/<time>`, and merges that branch the same way, so nothing is committed on the default branch itself.

### What the judge cannot know

- The verdict record is written by an agent, so an agent can forge it. It says the pipeline ran and what it concluded, and every other reason above is read from the diff and holds against a forged record. What records alone are refused is not: a change that carries a verdict record is judged as the pipeline's, so the label, a labelled ticket, a closed ticket and an ADR in it meet only the guard rules and the hook's ask on the label.
- The review's independence rests on the pipeline's sight lines, which an agent runs.
- Tests are not guard paths, so a change that weakens its own tests meets only the count, coverage and mutation gates.
- A flag whose default is set outside `flags.toml` is not seen.
- `unflagged` shows that a flag was declared or named beside new surface, not that the surface sits behind it. It sees only the surface the `on = "removal"` doors list: a helper exported in a refactoring reaches the owner, and a new screen no snapshot records does not.

## What only the owner does

- Approves an `ask` at the prompt: the agent-ready label, and, before adoption or without the two merge recipes, a merge an agent types.
- Declares a life event on the binding's `- Life events:` line.
- Edits a locked file outside the harness, in a pull request the diff check will flag.
- Runs a denied command in their own shell.
- Merges what the merge judge leaves to them:
  - a guard path changed
  - a one-way door, once the project is `deployed` or holds `irreplaceable-data`
  - new surface with no flag, once it is `deployed`
  - a dispute
  - a verdict that is not a clean approval
  - records that give a ticket the agent-ready label
  - records that change a labelled ticket beyond the paths of its links
  - records that change what a labelled ticket reads
  - records that close a ticket
  - records that record a decision
  - a red tier 2
- Decides disputes.
- Changes or removes a glossary term, shortens an `_Avoid_` list, edits or supersedes an ADR.
- Writes or approves the ADR when a ratchet is relaxed.
- Re-runs the harness probe after an upgrade of the tool, because protected-path handling is undocumented behaviour.

## Disputes

A denied or unanswered call is the signal to open a dispute. Open one when the gate fails and you believe the gate, not the code, is wrong: a limit the change cannot reasonably meet, a rule that misfires, a fitness test encoding an outdated decision, a flaky check. When the code is only hard to fix, split the function, write the test or narrow the change: that comes first.

1. **Stop work on that gate.** Leave it, its config, its snapshot and its register exactly as they are.

   Done when the gate, its config, its snapshot and its register are as they were when it failed.

2. **Write the dispute.** Take the next free number: one above the highest `docs/disputes/NNNN-*.md`, or `0001`. Write `docs/disputes/NNNN-<slug>.md`:

   ```markdown
   # NNNN: <one-line title>

   - Status: open
   - Raised by: <role and branch>
   - Date: <YYYY-MM-DD>

   ## Gate
   Its question as the binding lists it, its entry point, and the guard path(s) that define it.

   ## Exact failure
   The command and its output, verbatim, trimmed to the relevant lines.

   ## Why the gate is wrong
   The argument with evidence: the ADR, measurement or case it contradicts or cannot express.

   ## Options
   Each option with its trade-offs, including "keep the gate and change the code" and its cost.

   ## Recommendation
   The option you recommend, and the change to which guard path.
   ```

   Done when the file is at the next free number and every section of it is filled.

3. **Report it.** Report it to whoever started you: a pipeline agent returns it in `disputes`, and an agent an owner is driving interactively reports it in its reply. Then continue with the work the gate does not block.

   Done when the dispute is in your reply or in the `disputes` you return, and no work the gate leaves open is undone.

Before `/ratchet:adopt` has run there is no `docs/disputes/` yet. Create the directory, write the dispute at the same path, and report it to the owner in the reply. Adopt's template skips an existing file, so the dispute survives adoption.

**Resolution.** The owner decides:

- **Upheld:** the gate changes by the owner's hand or approval. An ADR follows when the change relaxes a rule or contradicts a decision. The status becomes `upheld`, with a link.
- **Rejected:** the status becomes `rejected`, with the reason, and the code changes.

A dispute names one gate. A changed guard path is covered only by an upheld dispute that names that path, or by the owner's own hand. Dispute records stay as the history of how the gates evolved. They are not guard paths, so any agent can write one.
