# Guards and disputes

Agents push and drive the forge's CLI as the owner, so the forge cannot tell an agent's change from the owner's. The gates are the main quality control, so the gates themselves are protected: enforced in the harness, which the model cannot override, and detected in the diff for whatever slips past. Anything an agent can set is something an agent can forge, so there is no switch that turns the guard off.

## The manifest

`guards/manifest.toml` is the single list of guard paths, in groups with a reason each:

```toml
[[group]]
name = "ratchets"
reason = "Recorded thresholds, the mutation register and config snapshots only ever rise."
paths = ["quality/**"]

[[group]]
name = "harness"
reason = "Settings, hooks, workflows and this manifest enforce the guards; the owner edits them by hand."
edit = "deny"
paths = [".claude/settings.json", ".claude/hooks/**", "guards/**", "~/.claude/settings.json"]

[[group]]
name = "records"
reason = "An agent adds a term, an Avoid item or a new ADR; what is already there changes by the owner's hand."
edit = "append"
paths = ["docs/adr/**", "GLOSSARY.md"]
```

A group's `edit` says what an edit of its paths gets: `ask` when the key is absent, `deny` for a locked group, `append` for records an agent may add to.

Groups every binding has: tool configs and thresholds; the command contract and gate scripts; ratchets; CI and the pull-request template; the harness (`edit = "deny"`); local git hooks and config (`edit = "deny"`); fitness tests; home-grown gate tools; formal models and the project-local verification skill; records (`docs/adr/**` and `GLOSSARY.md`, `edit = "append"`); decisions (`docs/process/**`, `docs/agents/**`, `AGENTS.md`, `CLAUDE.md`, the glossary map, `PRODUCT.md`, `DESIGN.md`).

Three things read the manifest, so a guard path is one entry here and nowhere else: the hook, the diff check, and the settings check that keeps the permission rules in step with it. The template manifest names the groups every binding has; `AGENTS.md` and the glossary map sit in the decisions group, the glossary and the ADRs in the records group. A group may carry `exclude` patterns its paths do not cover, but a permission rule cannot, so the template lists the harness paths one by one rather than `.claude/**` with the agent worktrees excluded.

The manifest also lists the one-way doors, as `[[door]]` tables: paths whose change cannot be taken back once it ships. A door guards nothing. The hook, `guard-diff` and the permission rules never read it; the merge judge does (below).

## The hook

The method's plugin registers a PreToolUse hook on every edit and shell call, in Claude Code, Grok and Codex through the plugin's own `hooks.json`, in Antigravity through the copy of it the install converts, in pi and OpenCode through the adapters under `adapters/` (ADR 0004; `docs/harness/`). The rules live once, in `hooks/ratchet_guards`. It makes three kinds of decision and fails closed with `ask` when it cannot run:

- **Pass an addition** to a record, in a group marked `edit = "append"` (ADR 0014). The hook works out the file an Edit, Write or MultiEdit would leave and compares it with the file at the merge base of `HEAD` and the default branch, so a branch may rework what it added and may not touch what the owner merged. A glossary passes when everything the merge base holds is still there, in order and under the same heading (each term with the lines that define it, each `_Avoid_` item, each heading, each line outside the entries) and what came in is a term, an `_Avoid_` item or a cluster heading. A decision record passes when the file is new, named `NNNN-<slug>.md` with a number above every one the merge base holds, has a title, carries no `Relaxes:` line and does not speak of superseding. Everything else on those paths asks, with the reason: a changed definition, a renamed or moved term, a dropped `_Avoid_` item, an edited ADR, and anything the hook cannot read (a term defined twice, an indented `_Avoid_` line, a tool whose result it cannot compute, which today is every tool but Claude Code's three).
- **Ask.** An edit to any other guard path, naming the group and its reason. Merging a pull request, and on the default branch a `git merge`, a `git cherry-pick` or a `git pull` that names another repository or branch: a change reaches the default branch through the merge judge or by the owner's hand (ADR 0021). The sync with the remote's own default branch still passes (`git pull`, `git merge --ff-only origin/main`), and so does the `merge` entry point. Applying the agent-ready label to an issue or pull request (`gh issue edit --add-label`, `gh issue create --label` and the `pr` forms, with the label as `docs/agents/triage-labels.md` spells the `ready-for-agent` role), or giving a local ticket under `.scratch/` that status, through Edit, Write or MultiEdit or through a command line or a patch that names the label and writes the ticket, where one call over several tickets is one ask that names each: the label is the owner's agreement that a ticket is ready to build, and the feature preflight reads it as such.
- **Deny an edit** in a group marked `edit = "deny"`: the harness and local git, where the platform's own protected-path handling would let an `ask` through. The plugin's own files, every harness's installed plugins and the settings that enable them (`~/.claude/plugins/**` and `settings.json`, `~/.grok/installed-plugins/**`, `~/.grok/hooks/**` and `config.toml`, `~/.codex/plugins/**`, `hooks.json` and `config.toml`, `~/.pi/agent/extensions/**` and `settings.json`, `~/.config/opencode/plugins/**` and `opencode.json`) are always in this set, and so is the `git-state` group in every checkout, adopted or not: `.git/refs/**`, `packed-refs`, `HEAD`, `info/grafts`, `shallow` and `objects/info/**`, because the default branch's ref is what the path rules are read through (ADR 0016).
- **Deny a command** that bypasses a gate or rewrites history: `--no-verify` and its spellings (`-n`, any prefix git accepts), `git commit-tree`, `SKIP=` and `RATCHET_HOOK_PROBE=` (the answer the template recipes give a hook probe, which skips the gates), hook uninstalls, hook-path changes and a `chmod` of a guard path; force pushes in every spelling, pushes to main, resets and rebases that move main, amending a pushed commit, history filters; any command that moves, deletes or re-points the default branch's ref or a ref or setting it is read through (ADR 0016): `git branch -f`, `-d`, `-m` or `-c` on `main` or `master`, `update-ref`, `symbolic-ref` set or delete, `checkout`/`switch -B`, `-C` or `-b` naming a default branch, a fetch or push refspec ending in a protected ref, `remote add origin`, `set-url`, `set-head`, `rename`, `remove`, a `git config` or `git -c` on `remote.*`, `url.*`, `alias.*` or `include.*`, and `replace`, `fast-import`, `send-pack` and `receive-pack` whole, each name read as git reads it, so a push or a tag that git matches to a default ref, a `git` pointed at another repository (`--git-dir`, `GIT_DIR`), a command git is handed to run (`rebase --exec`, an editor, an alias) and a sync on a default branch that git reads as anything but the remote's default branch are judged the same way (ADR 0024); forge CLI calls (`gh` on GitHub) that change branch protection, rulesets, labels, repository settings, secrets, variables, workflows or runs; nested `claude` invocations given settings or plugin flags; `claude`, `grok`, `codex` and `pi` commands that disable or remove a plugin, package or marketplace; any shell write, move, copy, link or delete of a guard path, including through wrappers, substitutions, `eval`, heredocs and `-c` strings. An interpreter given its program inline is read by its text: `python3` (any `pythonX.Y`) with `-c` or with a program on stdin from a heredoc, a here-string or a pipe, `node -e`, `--eval`, `-p` or a heredoc, `perl -e`, `ruby -e`, `deno eval` and `bun -e` are denied when the program holds a quoted string naming a guard path (resolved against the working directory) and a write call (`open` for writing, `write_text`, `unlink`, `rename`, `shutil`, `subprocess`, `os.system`, `fs.writeFile`, `rm`, `child_process`, `File.write`, `open` with `>`, any `-i`). The deny message sends the agent to the Edit or Write tool, which asks the owner, with one Write for many changes in one file since each Edit asks. A program that only reads a guard path passes; one that reads a guard path and writes an unguarded file is denied all the same, so the read goes in a call of its own. Inside a longer string, a word made only of slashes (`a / b`) is an operator and names no path; a string that is exactly `/` still names the root, so `shutil.rmtree('/')` is denied. Reads pass: `gh label list`, a `GET` on a labels endpoint, `gh run list`. One write passes by a record: `gh label create <name>`, on the checkout's own repository, with no `--force`, when `docs/agents/triage-labels.md` at the checkout root lists `<name>` in its "Label in our tracker" column. Creating a label routes nothing by itself, the list is the owner's answer to the mattpocock-skills setup, and `docs/agents/**` is in the decisions group once the repository is adopted.

A verdict covers the whole tool call. Claude Code runs none of a denied command line, so a file write chained before the refused command is lost with it; the deny message says so and names the matched command. Keep a command the guard may refuse (a forge CLI call, a push, anything near a guard path) in a tool call of its own, so an unrelated write is never lost with it.

The path rules are on only once `guards/manifest.toml` is committed on the default branch (the branch `refs/remotes/origin/HEAD` names, else `refs/remotes/origin/main`, `refs/remotes/origin/master`, `refs/heads/main` or `refs/heads/master`, each read by its full name and skipped when it names no commit). The hook reads that commit from git and judges every path against it, whatever the work tree says: not a flag an agent could set, and not the work tree, where an adoption in progress is still writing the manifest and the files it names. Until the adoption merges, a manifest that is untracked or committed only on a branch guards nothing, and the command rules apply alone; after it merges, the owner's merged manifest governs even if the work tree's copy is edited or deleted (ADR 0005). A group whose `paths` is still empty matches nothing. The command rules are on from the first session, before `/ratchet:adopt` has written a binding: anything an agent can set is something an agent can forge, so the guard has no off state and no "not adopted yet" state. `/ratchet:init` is written so that none of its steps provokes a denial; its label step uses the recorded-label path above.

Permission rules in the project's `.claude/settings.json` are a second, independent layer generated from the manifest, never written by hand: `just settings` turns every pattern into an `Edit` rule, `deny` for a locked group and `ask` for the rest, none for an `append` group, and adds the fixed command rules (the bypasses, forge changes and the plain spellings of a write to the default branch's ref as `deny`, `gh pr merge` as `ask`; `git symbolic-ref` has no rule because it also reads, and the hook is the one layer on its other spellings); `just settings-check` (tier 1) fails on a rule missing or foreign to those two lists. `gh label create` is not in the deny list, because the hook lets a recorded label through and a settings deny would refuse that one too (ADR 0006). An `append` group has no `Edit` rule for the same reason: a rule matches the path and cannot read the edit, so it would prompt for the additions the hook lets through. On those paths the hook is the only layer in the harness, and the diff check is what stands behind it.

## The diff check

Some writes are out of the hook's reach: an interpreter running a script file by path (`python3 tool.py`), a path an interpreter builds at run time (a variable, a concatenation, a mode held in a variable, a decoded or fetched program), `git apply`, merges, variable paths in the shell, edits made outside the harness, and a ref name a variable, a file or a pipe supplies to a `git` command that would move the default branch (ADR 0016). An interpreter given its program inline is not among them (see the hook above). The `guard-diff` entry point (tier 1) lists every changed path that matches the manifest at the merge base or at head, with renames split so moving a guard file away is caught. The merge judge reads the same listing from the commits, and a change it finds a guard path in waits for the owner. Under a dependency manifest, a lockfile or a snapshot of one, the listing adds what the change does: the packages added, or each locked package's old and new versions, when that is all it does, and otherwise why not (another key changed, an existing entry changed or went, a source other than the registry, an artifact changed in place, a version that falls, a format it does not read). The path is still a guard path and still counted. A dependency is a decision the pull request states ([code.md](code.md)); the owner-away pipeline accepts one only when its spec named it and the listing found nothing else in the file (ADR 0011). Under a path in an `append` group the listing runs the hook's own check on the merge base and the work tree and adds `additions:` with each term, `_Avoid_` item and ADR that came in, or `not additive:` with the reason. That path is counted too; `guard-diff --fail-unless-additive` exits 1 when any guard path changed other than by an addition, and the pipeline treats an `additions:` path as no guard change (ADR 0014).

## The merge judge

A change reaches the default branch in one of two ways: the merge judge passes it and it merges itself, or the owner merges it (ADR 0021). The judge is `scripts/merge_ready.py`, behind the `merge-ready` entry point. It prints `merge: agent` or `merge: owner` with every reason, and it reads commits only: the manifest at the default branch's tip, every other file at the merge base and at the head. The owner merges when any of these holds:

- **A guard path changed.** The listing is `guard-diff`'s, and two changes pass: an addition to a record (ADR 0014), and `quality/thresholds.toml` when every number moved the tight way and nothing else in it changed, which is what the `ratchet` entry point does after every feature. A dependency change does not pass, whether the spec named it or not: the spec is an agent's, so the judge prints the dependency note and the owner merges.
- **A one-way door.** A path in a `[[door]]` table of the manifest. `on = "change"` is for migrations, schema and infrastructure: any change shuts the door. `on = "rewrite"` is for fixtures of stored data: a new file passes, a changed or deleted one does not. `on = "removal"` is for contract artefacts and public-surface snapshots: an addition passes, a removed or changed line does not. A door is no guard path, so the hook never asks on it and an agent builds the change. The flag registry is always a door: a flag that turns on by default, becomes permanent, or leaves the registry while off waits for the owner.
- **New surface with no flag.** The change adds to a file behind an `on = "removal"` door (a route, a command or an exported symbol in a contract artefact or public-surface snapshot) and no flag hides it: `flags.toml` gains no flag that is off, and no line the change adds outside `docs/`, the registry and those files names one that is off. The judge prints `unflagged` and the owner sees what ships. It is asked of the application types the method asks the flag questions of, read from the binding on the default branch, so a library is exempt (ADR 0025).
- **A dispute.** The change adds or edits one, or one not yet decided names a path the change touches.
- **No clean approval.** The pipeline's verdict record, `docs/features/<slug>/verdict.json`, is missing, is not an approval, lists an unresolved blocker or major finding or an open question, or certifies another tree. The record holds the tree the review saw, so a commit made after the review sends the merge to the owner. A minor finding does not.
- **The setting.** The pull request targets a branch other than the default; the branch does not hold the default branch's tip, so its gates ran on an older one; tier 2 is red on the default branch.

On a forge the `merge` workflow asks the judge once `ci` is green on the pull request. A run counts as green only when it tested that pull request against the default branch, so one that was retargeted, or whose branch has had a pull request against another base, is the owner's to merge (ADR 0024). It is triggered by `workflow_run`, which takes the workflow file, the judge and the manifest from the default branch, so a pull request cannot change what judges it, and the job reads the pull request's commits without checking them out or running them. It merges what the judge passes with a merge commit and starts tier 2 on the default branch. On everything else it leaves one comment with the reasons, edited in place on each run. With no forge the `merge` entry point (`scripts/merge.py <branch>`) merges the branch in a scratch work tree, asks the judge, runs `check` on the merge result, and moves the default branch only when both pass.

What the judge cannot know: the verdict record is written by an agent, so an agent can forge it. It says the pipeline ran and what it concluded, and every other reason above is read from the diff and holds against a forged record. The review's independence rests on the pipeline's sight lines, which an agent runs. Tests are not guard paths, so a change that weakens its own tests meets only the count, coverage and mutation gates. A flag whose default is set outside `flags.toml` is not seen. `unflagged` shows that a flag was declared or named beside new surface, not that the surface sits behind it, and it sees only the surface the `on = "removal"` doors list: a helper exported in a refactoring reaches the owner, and a new screen no snapshot records does not.

## What only the owner does

Approves an `ask` at the prompt. Edits a locked file outside the harness, in a pull request the diff check will flag. Runs a denied command in their own shell. Merges what the merge judge leaves to them: a guard path changed, a one-way door, new surface with no flag, a dispute, a verdict that is not a clean approval, a red tier 2. Decides disputes. Changes or removes a glossary term, shortens an `_Avoid_` list, edits or supersedes an ADR. Writes or approves the ADR when a ratchet is relaxed. Re-runs the harness probe after an upgrade of the tool, because protected-path handling is undocumented behaviour.

## Disputes

A denied or unanswered call is the signal to open a dispute.

**When.** The gate fails and you believe the gate, not the code, is wrong: a limit the change cannot reasonably meet, a rule that misfires, a fitness test encoding an outdated decision, a flaky check. Fixing the code being hard is a reason to split the function, write the test or narrow the change, and that comes first.

**How.**

1. Stop work on that gate. Leave it, its config, its snapshot and its register exactly as they are.
2. Take the next free number: one above the highest `docs/disputes/NNNN-*.md`, or `0001`. Write `docs/disputes/NNNN-<slug>.md`:

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

3. Report it to whoever started you (a pipeline agent returns it in `disputes`; an agent an owner is driving interactively reports it in its reply), and continue with the work the gate does not block.

Before `/ratchet:adopt` has run there is no `docs/disputes/` yet; create the directory, write the dispute at the same path, and report it to the owner in the reply. Adopt's template skips an existing file, so the dispute survives adoption.

**Resolution.** The owner upholds it (the gate changes by the owner's hand or approval; an ADR follows when the change relaxes a rule or contradicts a decision; status `upheld` with a link) or rejects it (status `rejected` with the reason; the code changes). A dispute names one gate. A changed guard path is covered only by an upheld dispute that names that path, or by the owner's own hand. Records stay as the history of how the gates evolved; they are not guard paths, so any agent can write one.
