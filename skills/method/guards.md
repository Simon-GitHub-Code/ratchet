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
exclude = [".claude/worktrees", ".claude/worktrees/**"]
paths = [".claude/**", "guards/**", "~/.claude/settings.json", "~/.claude/settings.local.json"]
```

Groups every binding has: tool configs and thresholds; the command contract and gate scripts; ratchets; CI and the pull-request template; the harness (`edit = "deny"`); local git hooks and config (`edit = "deny"`); fitness tests; home-grown gate tools; formal models and the project-local verification skill; decisions (`docs/adr/**`, `docs/process/**`, `docs/agents/**`, `AGENTS.md`, `CLAUDE.md`, the glossary, `PRODUCT.md`, `DESIGN.md`).

Three things read the manifest, so a guard path is one entry here and nowhere else: the hook, the diff check, and a fitness test that keeps the permission rules in step with it. The template manifest names the groups every binding has; `AGENTS.md`, `GLOSSARY.md` and the glossary map sit in the decisions group.

## The hook

The method's plugin registers a PreToolUse hook on every edit and shell call, in Claude Code, Grok and Codex through the plugin's own `hooks.json`, in pi and OpenCode through the adapters under `adapters/` (ADR 0004; `docs/harness/`). The rules live once, in `hooks/ratchet_guards`. It makes three kinds of decision and fails closed with `ask` when it cannot run:

- **Ask.** An edit to a guard path, naming the group and its reason. Merging a pull request.
- **Deny an edit** in a group marked `edit = "deny"`: the harness and local git, where the platform's own protected-path handling would let an `ask` through. The plugin's own files, every harness's installed plugins and the settings that enable them (`~/.claude/plugins/**` and `settings.json`, `~/.grok/installed-plugins/**`, `~/.grok/hooks/**` and `config.toml`, `~/.codex/plugins/**`, `hooks.json` and `config.toml`, `~/.pi/agent/extensions/**` and `settings.json`, `~/.config/opencode/plugins/**` and `opencode.json`) are always in this set.
- **Deny a command** that bypasses a gate or rewrites history: `--no-verify` and its spellings, `SKIP=`, hook uninstalls and hook-path changes; force pushes in every spelling, pushes to main, hard resets on main, amending a pushed commit, history filters; forge CLI calls (`gh` on GitHub) that change branch protection, rulesets, labels, repository settings, secrets, variables, workflows or runs; nested `claude` invocations given settings or plugin flags; `claude`, `grok`, `codex` and `pi` commands that disable or remove a plugin, package or marketplace; any shell write, move, copy, link or delete of a guard path, including through wrappers, substitutions, `eval`, heredocs and `-c` strings. Reads pass: `gh label list`, a `GET` on a labels endpoint, `gh run list`. One write passes by a record: `gh label create <name>`, on the checkout's own repository, with no `--force`, when `docs/agents/triage-labels.md` at the checkout root lists `<name>` in its "Label in our tracker" column. Creating a label routes nothing by itself, the list is the owner's answer to the mattpocock-skills setup, and `docs/agents/**` is in the decisions group once the repository is adopted.

A verdict covers the whole tool call. Claude Code runs none of a denied command line, so a file write chained before the refused command is lost with it; the deny message says so and names the matched command. Keep a command the guard may refuse (a forge CLI call, a push, anything near a guard path) in a tool call of its own, so an unrelated write is never lost with it.

Without a `guards/manifest.toml` the path rules are off and the command rules still apply. The command rules are on from the first session, before `/ratchet:adopt` has written a binding: anything an agent can set is something an agent can forge, so the guard has no off state and no "not adopted yet" state. `/ratchet:init` is written so that none of its steps provokes a denial; its label step uses the recorded-label path above.

Permission rules in the project's `.claude/settings.json` are a second, independent layer that mirrors the manifest: `deny` for locked groups and bypass commands, `ask` for the rest and for `gh pr merge`.

## The diff check

Some writes are out of the hook's reach: interpreters, `git apply`, merges, variable paths, edits made outside the harness. The `guard-diff` entry point (tier 1) and a CI job list every changed path that matches the manifest at the merge base or at head, with renames split so moving a guard file away is caught. The CI job fails when any guard path changed, as information for the owner's merge decision; it is not a required check.

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
