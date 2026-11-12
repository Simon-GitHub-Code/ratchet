---
name: init
description: "Lay down the bare bones any repository needs before agents build in it: a git repository, an AGENTS.md that names the four plugins composed here and what each owns, CLAUDE.md as a symlink to it, the pstack and impeccable one-time setups, the first commit and the remote; then the next command for the owner to type. Two owner dialogs by default; `interactive` asks everything."
disable-model-invocation: true
---

# Initialise a repository

The bare bones. `/ratchet:adopt` builds the gates on top later, when the owner is ready. The template is `${CLAUDE_SKILL_DIR}/templates/AGENTS.md`.

The Ratchet guard hook is already on: its command rules apply from the first session, with or without a binding, and nothing in these steps should trip it. If a tool call is denied while you follow this skill, that is a bug in this skill: write the dispute as [Before adopt](#before-adopt) says and tell the owner. Keep any command the guard might refuse in a Bash call of its own; a denied Bash call runs none of its commands, so a file write chained with it is lost.

Every step says when it is already done. A second run on the same directory skips every step and ends with the same hand-over.

## Two dialogs, one round each

The owner answers two dialogs in a default run, both through the question tool, and nothing else asks them anything. Every other choice is a default the evidence supports, printed as one table before the first dialog so the owner sees it before anything is written. Each dialog is one round: an answer is recorded as given, never refined with a follow-up; an answer you cannot act on is carried into the hand-over as an open item. When `AGENTS.md` already lists a question's key under `## Standing answers`, the question is not asked and its answer there is taken. Once the repository exists (after step 2), record each answer in one Bash call: `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/questions.py" record --skill init --key <key> --kind <kind> --recommended <yes|no> --root .`. The keys are `init.purpose`, `init.user`, `init.difference` and `init.interface` (`direction`), and `init.defaults`, `init.stack` and `init.deploy` (`irreversible`: a remote is created from the first, and a stack is rewritten, not relaxed).

**The defaults table.** Print it as the first thing the owner reads, one row per default with its evidence, and leave out a row whose step is already done:

| Default | Chosen when | Otherwise |
| --- | --- | --- |
| Repository name: the directory name | always (the `gh repo view` name when `origin` exists) | |
| Issue tracker: GitHub Issues | `gh auth status` succeeds | GitLab when only `glab auth status` succeeds; local markdown when neither. The tracker's record, its labels and the domain-doc layout are written by `/mattpocock-skills:setup-matt-pocock-skills`, which the owner types after init: the hand-over says so |
| Remote: create it, private, named as above | tracker is GitHub or GitLab and `origin` is missing | local only |
| pstack models: the tiered mix at `medium` | `~/.claude/rules/pstack-models.md` is missing | the existing rule stands |
| Companion plugins: install the missing ones by the harness page's commands | `mattpocock-skills`, `pstack` or `impeccable` is missing | left missing and named in the hand-over |
| Platform: web, with a user interface | no code yet, or the code has a web front end | `ios`, `android` or `adaptive` from the code; no interface when the owner says so in dialog 2 |

**Dialog 1, always: what this is.** Up to four questions in the one dialog, recommended answers first:

1. The one-paragraph purpose. With evidence (a README, a package manifest, existing code) the recommended option is your reading of it; on an empty directory the options are three shapes (a web application, a service or API, a library or command-line tool) and the owner types the paragraph through the free-text option.
2. The primary user, their situation and the job they are doing.
3. What the product makes possible that a neighbour could not truthfully claim.
4. The defaults table: accept it (recommended); accept it but keep the repository local, with no remote and local markdown as the tracker; or run the rest interactively. The remote is created only from the first answer; never from the tracker choice alone, and never from the local one.

Questions 2 and 3 are `impeccable init`'s own first two; asked here, they are passed to it in step 5 so it does not ask again.

**Dialog 2, greenfield only: the stack.** When the directory has no code and no framework after step 1, up to three questions: the stack (your recommendation first, named with its reason; plain static HTML and CSS; a framework the owner names), the deploy target or constraint that bears on it, and whether the product has a user interface (web, recommended; native; none). A repository with code answers these from the code and skips the dialog.

**`interactive`.** `/ratchet:init interactive` turns every default into a question: step 4 asks one dialog; step 5 chains the two setups, three or four dialogs for `pstack` and up to three for `impeccable`. Six to eight dialogs in all. Say that count before the first one. The owner also lands here by declining the defaults table in dialog 1.

## Steps

1. **Check the ground.** Name the harness you run in and read its page, `${CLAUDE_PLUGIN_ROOT}/docs/harness/<harness>.md` (Claude Code, Grok, Codex, Antigravity, pi or OpenCode): it gives the command that lists installed plugins, the one that installs each of the four, and the names this skill uses mapped to that harness. List the plugins; note each one's version and marketplace (`<plugin>@<marketplace>` in Claude Code) for step 5. When `mattpocock-skills`, `pstack` or `impeccable` is missing, the install is a row of the defaults table in dialog 1 (one answer covers the missing ones); run the page's install commands, each in a Bash call of its own; installing is allowed, removing is refused. In Claude Code the ratchet marketplace declares the three as dependencies, so they are normally already there. A plugin the page says has no package for this harness is reported in the hand-over as missing, not installed another way. `gh auth status` (and `glab auth status` when `glab` exists) decides the tracker and remote rows. Look for code: a package manifest, a lockfile, source directories; none means greenfield and dialog 2. Then git:
   - No `.git` here and no enclosing repository (`git rev-parse --show-toplevel` fails): run `git init -b main`. An empty directory is the normal case, not a failure.
   - `.git` is a directory: nothing to do.
   - Ambiguous: `.git` is a file (a worktree or submodule), `git rev-parse --show-toplevel` names a parent directory (a nested checkout), or the directory is a bare repository. Stop and ask the owner which repository they mean.

   Print the defaults table, then run dialog 1 and, on greenfield, dialog 2. Done when the directory is the top level of a git repository, the four plugins are listed (or you have told the owner which is missing) and the dialogs are answered.

2. **Write `AGENTS.md`.** Copy the template to the repository root. If an `AGENTS.md` already exists, insert the template's opening stanza (the four plugins and their questions) above its first heading and keep the rest. If a `CLAUDE.md` with content exists and no `AGENTS.md`, move that content under the stanza. When the stanza is there but a plugin line differs from the template's (an older ratchet wrote it), replace those lines with the template's and name them in the hand-over; the route sends the owner here when they differ. Done, and skipped on a rerun, when `AGENTS.md` opens with the four-plugin stanza and its plugin lines match the template.

3. **Link `CLAUDE.md`.** `ln -s AGENTS.md CLAUDE.md` at the root, replacing a plain `CLAUDE.md` whose content step 2 moved. Done, and skipped, when `CLAUDE.md` is a symlink to `AGENTS.md`. Then append to `.gitignore` (create it if missing) each line of `${CLAUDE_SKILL_DIR}/templates/gitignore` it lacks: the agent worktrees pstack's panels leave under `.claude/worktrees/` and impeccable's caches, which whole-tree gates would otherwise judge. Done, and skipped, when every line is present.

4. **Fill the title and "What this is".** Replace `# <project>` with the repository name from the defaults table and write the purpose paragraph from dialog 1. Then record the two answers a later command would otherwise ask again, since the owner gave them once:
   - **The stack.** When dialog 2 was asked and `impeccable` will not run (the owner answered "no user interface"), add a `## Stack` section after "What this is" with the stack and the deploy target as the owner gave them. Adopt reads `## Stack` to recognise the stack it chooses tools for, from `PRODUCT.md` when impeccable writes one, from here otherwise.
   - **The tracker.** Under the "Agent skills" heading, above its placeholder, add one line naming the tracker the defaults table settled and why, such as `Tracker chosen at init: local markdown, because the owner kept the repository local.` The tracker setup reads this file before it asks.

   The sections below keep their placeholders: "Agent skills" for the tracker setup, the rest for `/ratchet:adopt`. Done, and skipped, when the title and "What this is" have no angle-bracket placeholder.

5. **Run the other plugins' setup once, from the answers already given.** Resolve a plugin's files at `~/.claude/plugins/cache/<marketplace>/<plugin>/<version>/`, with the marketplace and version from step 1; when in doubt, `find ~/.claude/plugins/cache -path '*/<plugin>/*/SKILL.md'`.
   - **`mattpocock-skills`.** Not run here. Its `setup-matt-pocock-skills` skill is user-invoked (`disable-model-invocation: true`): the Skill tool refuses it to an agent and its author's rule is that no skill calls or imitates it, so the owner types `/mattpocock-skills:setup-matt-pocock-skills` after init, and the hand-over names it as the next command. It writes `docs/agents/issue-tracker.md`, `triage-labels.md` and `domain.md`, fills the `## Agent skills` section of `AGENTS.md`, and creates the labels on the forge (the guard allows `gh label create` for a name in the record it just wrote). Init creates the remote first (step 7) so that those labels have somewhere to go. Skipped, and said so in the hand-over, when `docs/agents/issue-tracker.md` already exists. A forge tracker needs a repository, so when the owner declined the remote in dialog 1 the tracker is local markdown, and the hand-over says why. Put its `## Agent skills` block in the template's "Agent skills" section of `AGENTS.md` (edit `AGENTS.md`, the link target, not `CLAUDE.md`). In `interactive` mode its Section A dialog also decides the remote (create it, private or public, under which name) and Section B rides in the same dialog; the draft confirmation is the second.
   - **`pstack`.** Skipped when `~/.claude/rules/pstack-models.md` exists; say so in the hand-over, since the owner may not remember writing it. Otherwise read `<pstack>/docs/harness/claude-code.md` first (it maps the skill's Cursor names and says the rule is written to that path), then follow `<pstack>/skills/setup-pstack/SKILL.md` with the budget answered as `medium` and the roles taken from the tiered mix: `haiku` for read-only exploration, `sonnet` for code-writing and investigation, `opus` for judgment, prose and synthesis, panels mixing `opus` and `sonnet`; every model written must be one the Agent tool lists. Skip its two closing offers (a verification skill, a formal verification skill): `/ratchet:adopt` names them in the binding's formal group when the stack is known. The file is pstack's, not this plugin's; the owner accepted the mix in dialog 1 and can rerun `/pstack:setup-pstack` at any time. In `interactive` mode run its dialogs as written.
   - **`impeccable`.** Skipped when `PRODUCT.md` exists, or when the owner answered "no user interface" in dialog 2. Otherwise invoke the `impeccable` skill with the argument `init` followed by the owner's answers: the purpose paragraph, the user and job, the mechanism (dialog 1), the stack and deploy target (dialog 2, when asked), the platform from the defaults table, and the sentence "the owner gave these through the question tool this session; write PRODUCT.md from them, ask only about a material gap they leave, and record no workflow default". Its Setup runs the `impeccable context` launcher from its `scripts/` folder before the init reference; let it, and do not rerun it after `PRODUCT.md` is written. It records the stack under `## Stack`, which adopt reads. A question it still asks counts in the hand-over's dialog tally.

   Done when each has run, was already done, or the owner declined.

6. **Commit.** `git add -A && git commit -m "chore(method): initialise the repository"`. The pstack rule lives in `~/.claude/rules/`, outside the repository, and is not part of it. Done, and skipped, when `git status --porcelain` prints nothing.

7. **Remote and push.** When dialog 1 accepted the defaults table with a remote row (or `interactive` recorded consent) and `git remote get-url origin` fails, run, in a Bash call of its own, `gh repo create <name> --private --source . --remote origin --push` (`--public` only when chosen interactively; `glab repo create` for GitLab). This is the one outward-facing action in init; it needs the owner's answer and nothing else. Done, and skipped, when `origin` exists. Skipped with "local only" when there was no remote row or the owner declined it.

8. **Labels.** Only on a rerun where `docs/agents/triage-labels.md` exists (the tracker setup wrote it) and the tracker is GitHub or GitLab with `origin` present: `gh label list` (one Bash call), then for each row of the record whose "Label in our tracker" value is missing, `gh label create <name> --description "<meaning>"` in a Bash call of its own, with exactly one name, no `--force` and no `-R`. That is the one `gh label create` the guard allows: the name is in the record the owner just accepted, on this checkout's own repository. A denial here is a bug: see [Before adopt](#before-adopt). Done, and skipped, when every recorded label is listed. Not applicable for a local tracker.

9. **Hand over.** Report, in this order:
   - The dialog tally: how many dialogs the owner answered (two in a default run; one when the directory had code), and any question a setup still asked.
   - Four lines, one per plugin, saying what it now does in this repository; the pstack line says where the rule lives and whether this run wrote it or found it.
   - The commit hash, and the remote URL or "local only" with the reason (no forge login, or the owner declined the remote and so has local markdown).
   - The label state: created (the count), already present, not applicable (local tracker), not yet (the tracker setup creates them), or refused (the dispute's path).
   - The route, as the last lines. Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/route.py" --root . --offline` in one Bash call and print its lines (`next:`, `why:`, `then:`, and any `also:`) as they are. After a default run they name `/mattpocock-skills:setup-matt-pocock-skills` (it asks the tracker, the labels and the domain-doc layout, one to three questions, and writes the records and the labels), then `/ratchet:adopt` (it asks the application types and proposes a tool per question for the `## Stack` that `PRODUCT.md` or `AGENTS.md` records, in one dialog). Close with the bare command on its own line.

   No pasted commands besides that one. Everything init can do, it has done; what it cannot do is the owner's keystroke, and the route names it.

## Before adopt

`/ratchet:adopt` creates `docs/disputes/`, the binding and ADR 0001; during init none exists. A denial is still the dispute signal: create `docs/disputes/`, write `docs/disputes/0001-<slug>.md` in the format `ratchet:method` gives in its guards page, and report it to whoever started you, which during an interactive init is the owner reading your reply. Adopt's template copy skips an existing file, so the record survives adoption.

