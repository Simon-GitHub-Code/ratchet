---
name: init
description: Lay down the bare bones any repository needs before agents build in it: a git repository, an AGENTS.md that names the four plugins composed here and what each owns, CLAUDE.md as a symlink to it, and the other three plugins' one-time setup.
disable-model-invocation: true
---

# Initialise a repository

The bare bones. `/ratchet:adopt` builds the gates on top later, when the owner is ready. The template is `${CLAUDE_SKILL_DIR}/templates/AGENTS.md`.

The Ratchet guard hook is already on: its command rules apply from the first session, with or without a binding, and nothing in these steps should trip it. If a tool call is denied while you follow this skill, that is a bug in this skill: write the dispute as [Before adopt](#before-adopt) says and tell the owner. Keep any command the guard might refuse in a tool call of its own; a denied Bash call runs none of its commands, so a file write chained with it is lost.

**Question rounds.** Say this to the owner before the first dialog: step 4 asks one dialog; step 5 chains three interactive setups, roughly two dialogs for `mattpocock-skills`, three or four for `pstack` and up to three for `impeccable`. About eight to ten dialogs in all. The question tool takes up to four questions per dialog, so batch what one setup asks into one dialog where its skill allows.

## Steps

1. **Check the ground.** `claude plugin list` shows `mattpocock-skills`, `pstack`, `impeccable` and `ratchet`; note each one's version and marketplace (`<plugin>@<marketplace>`) for step 5. Then git:
   - No `.git` here and no enclosing repository (`git rev-parse --show-toplevel` fails): run `git init -b main`. An empty directory is the normal case, not a failure.
   - `.git` is a directory: nothing to do.
   - Ambiguous: `.git` is a file (a worktree or submodule), `git rev-parse --show-toplevel` names a parent directory (a nested checkout), or the directory is a bare repository. Stop and ask the owner which repository they mean.

   Done when the directory is the top level of a git repository and the four plugins are listed, or you have told the owner which is missing.

2. **Write `AGENTS.md`.** Copy the template to the repository root. If an `AGENTS.md` already exists, insert the template's opening stanza (the four plugins and their questions) above its first heading and keep the rest. If a `CLAUDE.md` with content exists and no `AGENTS.md`, move that content under the stanza. Done when `AGENTS.md` opens with the four-plugin stanza.

3. **Link `CLAUDE.md`.** `ln -s AGENTS.md CLAUDE.md` at the root, replacing a plain `CLAUDE.md` whose content step 2 moved. Done when `CLAUDE.md` is a symlink to `AGENTS.md`.

4. **Fill the title and "What this is".** Replace `# <project>` with the repository name (the `gh repo view` name when a GitHub remote exists, else the directory name). One interactive question dialog for the one-paragraph purpose. The sections below keep their placeholders: "Agent skills" for step 5, the rest for `/ratchet:adopt`. Done when the title and that section have no angle-bracket placeholder.

5. **Run the other plugins' setup once.** Skip each that has already run (its output file exists). Resolve a plugin's files at `~/.claude/plugins/cache/<marketplace>/<plugin>/<version>/`, with the marketplace and version from step 1; when in doubt, `find ~/.claude/plugins/cache -path '*/<plugin>/*/SKILL.md'`.
   - **`mattpocock-skills`.** Its `setup-matt-pocock-skills` skill has `disable-model-invocation: true`, so the Skill tool refuses it: read `<mattpocock-skills>/skills/engineering/setup-matt-pocock-skills/SKILL.md` and follow it by hand. It asks for the issue tracker, the triage labels and the docs layout, and writes `docs/agents/*.md` and an `## Agent skills` block; put that block in the template's "Agent skills" section of `AGENTS.md` (edit `AGENTS.md`, the link target, not `CLAUDE.md`). **Do not run `gh label create`.** The guard refuses label management, by design: labels route work, and only the owner routes work. Write the labels into `docs/agents/triage-labels.md` as the skill says and list the create commands for the owner in the hand-over.
   - **`pstack`.** In Claude Code, read `<pstack>/docs/harness/claude-code.md` first; it maps the skill's Cursor names and says the rule is written to `~/.claude/rules/pstack-models.md`. Then invoke the `pstack:setup-pstack` skill. Its defaults map every non-Claude slug to `inherit-parent`, which on a Fable or Opus session puts every worker on the most expensive model; when it shows the roles, propose a tiered mix for the owner to accept or change: `haiku` for read-only exploration, `sonnet` for code-writing and investigation, `opus` for judgment, prose and synthesis, and panels mixing two families. The owner decides; the file is pstack's, not this plugin's.
   - **`impeccable`.** When the repository has or will have a user interface, invoke the `impeccable` skill with the argument `init`. Its Setup runs the `impeccable context` launcher from its `scripts/` folder before the init reference; let it, and do not rerun it after `PRODUCT.md` is written. Skip when the owner says the project has no user interface.

   Done when each has run or the owner declined.

6. **Hand over.** Say what each plugin now does in this repository, in four lines, then: nothing is committed yet, with the first commit to make (`git add -A && git commit -m "chore(method): initialise the repository"`; the pstack rule lives in `~/.claude/rules/`, outside the repository); the label commands the owner runs in their own shell, one `gh label create <name>` per label in `docs/agents/triage-labels.md`, which the guard will refuse from an agent; and that `/ratchet:adopt` binds the gates when the owner wants them.

## Before adopt

`/ratchet:adopt` creates `docs/disputes/`, the binding and ADR 0001; during init none exists. A denial is still the dispute signal: create `docs/disputes/`, write `docs/disputes/0001-<slug>.md` in the format `ratchet:method` gives in its guards page, and report it to whoever started you, which during an interactive init is the owner reading your reply. Adopt's template copy skips an existing file, so the record survives adoption.

## Design notes

Why the skill reads the way it does, from the first run on a fresh directory with mattpocock-skills 1.3.1, pstack 0.15.36 and impeccable 4.8.0.

- **Labels are the owner's, so init does not create them.** The guard refuses `gh label create` with or without a binding, and `/ratchet:adopt` owns the gates, so the three options were: init creates labels through a sanctioned path, init lists them for the owner, or the guard allows label management while no binding exists. The guard has no "not adopted yet" state on purpose: anything an agent can set, an agent can forge. So the setup writes the label file and the owner runs the create commands in their own shell, which is where the method puts every denied command.
- **Reads pass the guard.** `gh label list` was refused on the first run and is now allowed; the labels rule fires on `create`, `edit`, `delete` and `clone`. The deny message now says that no part of the call ran and names the matched command, because a chained `cp` and `AGENTS.md` append were lost with a refused `gh label list` and only noticed by checking.
- **`git init` is init's job.** On an empty directory there is one sensible action; the skill stops only when the directory is a worktree, a nested checkout or bare.
- **Skill invocation.** `setup-matt-pocock-skills` cannot be invoked through the Skill tool and is followed from its `SKILL.md`; `impeccable init` is an argument to one skill behind a launcher; `setup-pstack` needs the Claude Code adapter read first. The template's skill names (`tdd`, `codebase-design`, `domain-modeling`, `code-review`, `diagnosing-bugs`, `grill-with-docs`, `pr`, `research`) were checked against mattpocock-skills 1.3.1 and all exist.
- **The pstack mix is a recommendation.** The model sheet is pstack's rule file; this skill only asks the agent to propose the tiered mix the owner wanted, not to write it.
- **The template reserves "Agent skills".** The mattpocock setup updates an existing `## Agent skills` section in place, so a placeholder section keeps it from landing after "Stack rules the method leaves open".
