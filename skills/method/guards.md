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
```

Groups every binding has: tool configs and thresholds; the command contract and gate scripts; ratchets; CI and the pull-request template; the harness (`edit = "deny"`); local git hooks and config (`edit = "deny"`); fitness tests; home-grown gate tools; formal models and the project-local verification skill; decisions (`docs/adr/**`, `docs/process/**`, `docs/agents/**`, `AGENTS.md`, `CLAUDE.md`, the glossary, `PRODUCT.md`, `DESIGN.md`).

Three things read the manifest, so a guard path is one entry here and nowhere else: the hook, the diff check, and the settings check that keeps the permission rules in step with it. The template manifest names the groups every binding has; `AGENTS.md`, `GLOSSARY.md` and the glossary map sit in the decisions group. A group may carry `exclude` patterns its paths do not cover, but a permission rule cannot, so the template lists the harness paths one by one rather than `.claude/**` with the agent worktrees excluded.

## The hook

The method's plugin registers a PreToolUse hook on every edit and shell call, in Claude Code, Grok and Codex through the plugin's own `hooks.json`, in pi and OpenCode through the adapters under `adapters/` (ADR 0004; `docs/harness/`). The rules live once, in `hooks/ratchet_guards`. It makes three kinds of decision and fails closed with `ask` when it cannot run:

- **Ask.** An edit to a guard path, naming the group and its reason. Merging a pull request. Applying the agent-ready label to an issue or pull request (`gh issue edit --add-label`, `gh issue create --label` and the `pr` forms, with the label as `docs/agents/triage-labels.md` spells the `ready-for-agent` role), or giving a local ticket under `.scratch/` that status through Edit, Write or MultiEdit: the label is the owner's agreement that a ticket is ready to build, and the feature preflight reads it as such.
- **Deny an edit** in a group marked `edit = "deny"`: the harness and local git, where the platform's own protected-path handling would let an `ask` through. The plugin's own files, every harness's installed plugins and the settings that enable them (`~/.claude/plugins/**` and `settings.json`, `~/.grok/installed-plugins/**`, `~/.grok/hooks/**` and `config.toml`, `~/.codex/plugins/**`, `hooks.json` and `config.toml`, `~/.pi/agent/extensions/**` and `settings.json`, `~/.config/opencode/plugins/**` and `opencode.json`) are always in this set.
- **Deny a command** that bypasses a gate or rewrites history: `--no-verify` and its spellings (`-n`, any prefix git accepts), `git commit-tree`, `SKIP=`, hook uninstalls, hook-path changes and a `chmod` of a guard path; force pushes in every spelling, pushes to main, hard resets on main, amending a pushed commit, history filters; forge CLI calls (`gh` on GitHub) that change branch protection, rulesets, labels, repository settings, secrets, variables, workflows or runs; nested `claude` invocations given settings or plugin flags; `claude`, `grok`, `codex` and `pi` commands that disable or remove a plugin, package or marketplace; any shell write, move, copy, link or delete of a guard path, including through wrappers, substitutions, `eval`, heredocs and `-c` strings. An interpreter given its program inline is read by its text: `python3` (any `pythonX.Y`) with `-c` or with a program on stdin from a heredoc, a here-string or a pipe, `node -e`, `--eval`, `-p` or a heredoc, `perl -e`, `ruby -e`, `deno eval` and `bun -e` are denied when the program holds a quoted string naming a guard path (resolved against the working directory) and a write call (`open` for writing, `write_text`, `unlink`, `rename`, `shutil`, `subprocess`, `os.system`, `fs.writeFile`, `rm`, `child_process`, `File.write`, `open` with `>`, any `-i`). The deny message sends the agent to the Edit or Write tool, which asks the owner, with one Write for many changes in one file since each Edit asks. A program that only reads a guard path passes; one that reads a guard path and writes an unguarded file is denied all the same, so the read goes in a call of its own. Reads pass: `gh label list`, a `GET` on a labels endpoint, `gh run list`. One write passes by a record: `gh label create <name>`, on the checkout's own repository, with no `--force`, when `docs/agents/triage-labels.md` at the checkout root lists `<name>` in its "Label in our tracker" column. Creating a label routes nothing by itself, the list is the owner's answer to the mattpocock-skills setup, and `docs/agents/**` is in the decisions group once the repository is adopted.

A verdict covers the whole tool call. Claude Code runs none of a denied command line, so a file write chained before the refused command is lost with it; the deny message says so and names the matched command. Keep a command the guard may refuse (a forge CLI call, a push, anything near a guard path) in a tool call of its own, so an unrelated write is never lost with it.

The path rules are on only once `guards/manifest.toml` is committed on the default branch (the remote's `HEAD`, else `main` or `master`). The hook reads that commit from git and judges every path against it, whatever the work tree says: not a flag an agent could set, and not the work tree, where an adoption in progress is still writing the manifest and the files it names. Until the adoption merges, a manifest that is untracked or committed only on a branch guards nothing, and the command rules apply alone; after it merges, the owner's merged manifest governs even if the work tree's copy is edited or deleted (ADR 0005). A group whose `paths` is still empty matches nothing. The command rules are on from the first session, before `/ratchet:adopt` has written a binding: anything an agent can set is something an agent can forge, so the guard has no off state and no "not adopted yet" state. `/ratchet:init` is written so that none of its steps provokes a denial; its label step uses the recorded-label path above.

Permission rules in the project's `.claude/settings.json` are a second, independent layer generated from the manifest, never written by hand: `just settings` turns every pattern into an `Edit` rule, `deny` for a locked group and `ask` for the rest, and adds the fixed command rules (the bypasses and forge changes as `deny`, `gh pr merge` as `ask`); `just settings-check` (tier 1) fails on a rule missing or foreign to those two lists. `gh label create` is not in the deny list, because the hook lets a recorded label through and a settings deny would refuse that one too (ADR 0006).

## The diff check

Some writes are out of the hook's reach: an interpreter running a script file by path (`python3 tool.py`), a path an interpreter builds at run time (a variable, a concatenation, a mode held in a variable, a decoded or fetched program), `git apply`, merges, variable paths in the shell, edits made outside the harness. An interpreter given its program inline is not among them (see the hook above). The `guard-diff` entry point (tier 1) and a CI job list every changed path that matches the manifest at the merge base or at head, with renames split so moving a guard file away is caught. The CI job fails when any guard path changed, as information for the owner's merge decision; it is not a required check. Under a dependency manifest, a lockfile or a snapshot of one, the listing adds what the change does: the packages added, or each locked package's old and new versions, when that is all it does, and otherwise why not (another key changed, an existing entry changed or went, a source other than the registry, an artifact changed in place, a version that falls, a format it does not read). The path is still a guard path and still counted. A dependency is a decision the pull request states ([code.md](code.md)); the owner-away pipeline accepts one only when its spec named it and the listing found nothing else in the file (ADR 0011).

## What only the owner does

Approves an `ask` at the prompt. Edits a locked file outside the harness, in a pull request the diff check will flag. Runs a denied command in their own shell. Merges. Decides disputes. Writes or approves the ADR when a ratchet is relaxed. Re-runs the harness probe after an upgrade of the tool, because protected-path handling is undocumented behaviour.

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
