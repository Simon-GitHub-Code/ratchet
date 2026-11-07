---
name: init
description: Lay down the bare bones any repository needs before agents build in it: a git repository, an AGENTS.md that names the four plugins composed here and what each owns, CLAUDE.md as a symlink to it, the other three plugins' one-time setup, the first commit, the remote and the triage labels. Two owner dialogs by default; `interactive` asks everything.
disable-model-invocation: true
---

# Initialise a repository

The bare bones. `/ratchet:adopt` builds the gates on top later, when the owner is ready. The template is `${CLAUDE_SKILL_DIR}/templates/AGENTS.md`.

The Ratchet guard hook is already on: its command rules apply from the first session, with or without a binding, and nothing in these steps should trip it. If a tool call is denied while you follow this skill, that is a bug in this skill: write the dispute as [Before adopt](#before-adopt) says and tell the owner. Keep any command the guard might refuse in a Bash call of its own; a denied Bash call runs none of its commands, so a file write chained with it is lost.

Every step says when it is already done. A second run on the same directory skips every step and ends with the same hand-over.

## Two dialogs, one round each

The owner answers two dialogs in a default run, both through the question tool, and nothing else asks them anything. Every other choice is a default the evidence supports, printed as one table before the first dialog so the owner sees it before anything is written. Each dialog is one round: an answer is recorded as given, never refined with a follow-up; an answer you cannot act on is carried into the hand-over as an open item.

**The defaults table.** Print it as the first thing the owner reads, one row per default with its evidence, and leave out a row whose step is already done:

| Default | Chosen when | Otherwise |
| --- | --- | --- |
| Repository name: the directory name | always (the `gh repo view` name when `origin` exists) | |
| Issue tracker: GitHub Issues | `gh auth status` succeeds | GitLab when only `glab auth status` succeeds; local markdown when neither |
| Remote: create it, private, named as above | tracker is GitHub or GitLab and `origin` is missing | local only |
| Triage labels: the five defaults | the `triage` skill is installed | none |
| Domain docs: single-context | always, including a monorepo (adopt can switch) | |
| pstack models: the tiered mix at `medium` | `~/.claude/rules/pstack-models.md` is missing | the existing rule stands |
| Companion plugins: install the missing ones by the harness page's commands | `mattpocock-skills`, `pstack` or `impeccable` is missing | left missing and named in the hand-over |
| Platform: web, with a user interface | no code yet, or the code has a web front end | `ios`, `android` or `adaptive` from the code; no interface when the owner says so in dialog 2 |

**Dialog 1, always: what this is.** Up to four questions in the one dialog, recommended answers first:

1. The one-paragraph purpose. With evidence (a README, a package manifest, existing code) the recommended option is your reading of it; on an empty directory the options are three shapes (a web application, a service or API, a library or command-line tool) and the owner types the paragraph through the free-text option.
2. The primary user, their situation and the job they are doing.
3. What the product makes possible that a neighbour could not truthfully claim.
4. The defaults table: accept it (recommended), or run the rest interactively. The remote is created only from an accepting answer here; never from the tracker choice alone.

Questions 2 and 3 are `impeccable init`'s own first two; asked here, they are passed to it in step 5 so it does not ask again.

**Dialog 2, greenfield only: the stack.** When the directory has no code and no framework after step 1, up to three questions: the stack (your recommendation first, named with its reason; plain static HTML and CSS; a framework the owner names), the deploy target or constraint that bears on it, and whether the product has a user interface (web, recommended; native; none). A repository with code answers these from the code and skips the dialog.

**`interactive`.** `/ratchet:init interactive` turns every default into a question: step 4 asks one dialog; step 5 chains the three setups, two dialogs for `mattpocock-skills` (the remote questions ride in its tracker dialog), three or four for `pstack` and up to three for `impeccable`. Eight to ten dialogs in all. Say that count before the first one. The owner also lands here by declining the defaults table in dialog 1.

## Steps

1. **Check the ground.** Name the harness you run in and read its page, `${CLAUDE_PLUGIN_ROOT}/docs/harness/<harness>.md` (Claude Code, Grok, Codex, pi or OpenCode): it gives the command that lists installed plugins, the one that installs each of the four, and the names this skill uses mapped to that harness. List the plugins; note each one's version and marketplace (`<plugin>@<marketplace>` in Claude Code) for step 5. When `mattpocock-skills`, `pstack` or `impeccable` is missing, the install is a row of the defaults table in dialog 1 (one answer covers the missing ones); run the page's install commands, each in a Bash call of its own; installing is allowed, removing is refused. In Claude Code the ratchet marketplace declares the three as dependencies, so they are normally already there. A plugin the page says has no package for this harness is reported in the hand-over as missing, not installed another way. `gh auth status` (and `glab auth status` when `glab` exists) decides the tracker and remote rows. Look for code: a package manifest, a lockfile, source directories; none means greenfield and dialog 2. Then git:
   - No `.git` here and no enclosing repository (`git rev-parse --show-toplevel` fails): run `git init -b main`. An empty directory is the normal case, not a failure.
   - `.git` is a directory: nothing to do.
   - Ambiguous: `.git` is a file (a worktree or submodule), `git rev-parse --show-toplevel` names a parent directory (a nested checkout), or the directory is a bare repository. Stop and ask the owner which repository they mean.

   Print the defaults table, then run dialog 1 and, on greenfield, dialog 2. Done when the directory is the top level of a git repository, the four plugins are listed (or you have told the owner which is missing) and the dialogs are answered.

2. **Write `AGENTS.md`.** Copy the template to the repository root. If an `AGENTS.md` already exists, insert the template's opening stanza (the four plugins and their questions) above its first heading and keep the rest. If a `CLAUDE.md` with content exists and no `AGENTS.md`, move that content under the stanza. Done, and skipped on a rerun, when `AGENTS.md` opens with the four-plugin stanza.

3. **Link `CLAUDE.md`.** `ln -s AGENTS.md CLAUDE.md` at the root, replacing a plain `CLAUDE.md` whose content step 2 moved. Done, and skipped, when `CLAUDE.md` is a symlink to `AGENTS.md`.

4. **Fill the title and "What this is".** Replace `# <project>` with the repository name from the defaults table and write the purpose paragraph from dialog 1. The sections below keep their placeholders: "Agent skills" for step 5, the rest for `/ratchet:adopt`. Done, and skipped, when the title and that section have no angle-bracket placeholder.

5. **Run the other plugins' setup once, from the answers already given.** Resolve a plugin's files at `~/.claude/plugins/cache/<marketplace>/<plugin>/<version>/`, with the marketplace and version from step 1; when in doubt, `find ~/.claude/plugins/cache -path '*/<plugin>/*/SKILL.md'`.
   - **`mattpocock-skills`.** Skipped when `docs/agents/issue-tracker.md` exists. Its `setup-matt-pocock-skills` skill has `disable-model-invocation: true`, so the Skill tool refuses it: read `<mattpocock-skills>/skills/engineering/setup-matt-pocock-skills/SKILL.md` and follow it by hand, with its Sections A, B and C answered from the defaults table (tracker, the five default labels, single-context) and its draft confirmation taken as given, so it asks nothing. Its step 4 says to run `gh label create` when Section B ran on GitHub or GitLab: do not do it there, the remote does not exist yet; write `docs/agents/triage-labels.md` as the skill says and leave creation to step 8. A forge tracker needs a repository, so when the owner declined the remote in dialog 1 the tracker is local markdown, and the hand-over says why. Put its `## Agent skills` block in the template's "Agent skills" section of `AGENTS.md` (edit `AGENTS.md`, the link target, not `CLAUDE.md`). In `interactive` mode its Section A dialog also decides the remote (create it, private or public, under which name) and Section B rides in the same dialog; the draft confirmation is the second.
   - **`pstack`.** Skipped when `~/.claude/rules/pstack-models.md` exists; say so in the hand-over, since the owner may not remember writing it. Otherwise read `<pstack>/docs/harness/claude-code.md` first (it maps the skill's Cursor names and says the rule is written to that path), then follow `<pstack>/skills/setup-pstack/SKILL.md` with the budget answered as `medium` and the roles taken from the tiered mix: `haiku` for read-only exploration, `sonnet` for code-writing and investigation, `opus` for judgment, prose and synthesis, panels mixing `opus` and `sonnet`; every model written must be one the Agent tool lists. Skip its two closing offers (a verification skill, a formal verification skill): `/ratchet:adopt` names them in the binding's formal group when the stack is known. The file is pstack's, not this plugin's; the owner accepted the mix in dialog 1 and can rerun `/pstack:setup-pstack` at any time. In `interactive` mode run its dialogs as written.
   - **`impeccable`.** Skipped when `PRODUCT.md` exists, or when the owner answered "no user interface" in dialog 2. Otherwise invoke the `impeccable` skill with the argument `init` followed by the owner's answers: the purpose paragraph, the user and job, the mechanism (dialog 1), the stack and deploy target (dialog 2, when asked), the platform from the defaults table, and the sentence "the owner gave these through the question tool this session; write PRODUCT.md from them, ask only about a material gap they leave, and record no workflow default". Its Setup runs the `impeccable context` launcher from its `scripts/` folder before the init reference; let it, and do not rerun it after `PRODUCT.md` is written. It records the stack under `## Stack`, which adopt reads. A question it still asks counts in the hand-over's dialog tally.

   Done when each has run, was already done, or the owner declined.

6. **Commit.** `git add -A && git commit -m "chore(method): initialise the repository"`. The pstack rule lives in `~/.claude/rules/`, outside the repository, and is not part of it. Done, and skipped, when `git status --porcelain` prints nothing.

7. **Remote and push.** When dialog 1 accepted the defaults table with a remote row (or `interactive` recorded consent) and `git remote get-url origin` fails, run, in a Bash call of its own, `gh repo create <name> --private --source . --remote origin --push` (`--public` only when chosen interactively; `glab repo create` for GitLab). This is the one outward-facing action in init; it needs the owner's answer and nothing else. Done, and skipped, when `origin` exists. Skipped with "local only" when there was no remote row or the owner declined it.

8. **Labels.** When the tracker is GitHub or GitLab and `origin` exists: `gh label list` (one Bash call), then for each row of `docs/agents/triage-labels.md` whose "Label in our tracker" value is missing, `gh label create <name> --description "<meaning>"` in a Bash call of its own, with exactly one name, no `--force` and no `-R`. That is the one `gh label create` the guard allows: the name is in the record the owner just accepted, on this checkout's own repository. A denial here is a bug: see [Before adopt](#before-adopt). Done, and skipped, when every recorded label is listed. Not applicable for a local tracker.

9. **Hand over.** Report, in this order:
   - The dialog tally: how many dialogs the owner answered (two in a default run; one when the directory had code), and any question a setup still asked.
   - Four lines, one per plugin, saying what it now does in this repository; the pstack line says where the rule lives and whether this run wrote it or found it.
   - The commit hash, and the remote URL or "local only" with the reason (no forge login, or the owner declined the remote and so has local markdown).
   - The label state: created (the count), already present, not applicable (local tracker), or refused (the dispute's path).
   - The single next step, as the last two lines: one line saying what `/ratchet:adopt` will ask given the stack `PRODUCT.md` recorded under `## Stack` (it confirms the stack preset's binding in one dialog; without a `## Stack`, say adopt will ask for the stack first), then the bare line `/ratchet:adopt`.

   No pasted commands. Everything init can do, it has done.

## Before adopt

`/ratchet:adopt` creates `docs/disputes/`, the binding and ADR 0001; during init none exists. A denial is still the dispute signal: create `docs/disputes/`, write `docs/disputes/0001-<slug>.md` in the format `ratchet:method` gives in its guards page, and report it to whoever started you, which during an interactive init is the owner reading your reply. Adopt's template copy skips an existing file, so the record survives adoption.

## Design notes

Why the skill reads the way it does, from two runs on a fresh directory with mattpocock-skills 1.3.1, pstack 0.15.36 and impeccable 4.8.0, and a third brief that asked for a run without the owner in the loop. The first run ended with four untracked files, no remote, no commit, no labels and a hand-over of commands for the owner to paste; the second refinement removed that hand-over; the third collapsed the interview.

- **Two dialogs, and defaults the evidence supports.** The second run asked eight to ten dialogs, most of them confirming what the directory already showed: `gh auth status` settles the tracker, the directory settles the name, almost every repository is single-context, and the five default labels are what `triage` expects. Those are now defaults printed as a table, and the owner is asked only what no evidence can supply: what the thing is, and on an empty directory, what it is built with. The table is accepted inside dialog 1 rather than silently, because the remote is outward-facing and the earlier brief's rule stands: consent to a remote is an answer, never an inference. Declining the table is the switch to `interactive`, so no owner is stuck with a default they dislike and no agent gets a switch that turns anything off. One round per dialog: an answer is recorded, not negotiated.
- **The setups are answered, not re-asked.** `impeccable init`'s first two product-truth questions are the natural second and third questions about purpose, so dialog 1 asks them and passes the answers with the invocation; its stack question is dialog 2. `setup-pstack`'s budget and role confirmation are a default row the owner accepts with the table; its closing offers belong to adopt. `setup-matt-pocock-skills`' three sections are default rows. A setup that asks anyway is counted in the hand-over, so the cap is measured, not assumed.
- **Init commits, creates the remote and creates the labels.** The friction after the first run was the list of commands left for the owner. Each is now a step with a skip condition, so a rerun is idempotent. An owner who wants GitHub Issues but declines the remote gets local markdown, because the tracker's skills call `gh issue` on a repository that would not exist.
- **Labels: the guard allows the recorded ones (path a).** Three sanctioned paths were weighed: (a) the guard allows `gh label create <name>` when the name is in `docs/agents/triage-labels.md`; (b) init writes a `create-labels.sh` for the owner to run; (c) the owner types the commands with the `!` prefix, which the hook does not see (observed: `! git push origin main` ran on `main`, which the push-main rule refuses from an agent). Path (a) was taken because the forgery argument ("anything an agent can set, an agent can forge") can be answered here: creating a label routes nothing, the routing act is applying one, which the guard judges as its own command; the names are those the owner accepted in the dialog one step earlier, in a record that lands in the first commit; and once adopted, `docs/agents/**` is a guard path in the decisions group, so the list is the owner's. Everything else about labels stays refused: another name, two names, `--force` (an edit), `-R` (another repository). The "written in this session" condition from the brief was dropped: a hook has no session memory, and the record plus the guard path are the deterministic form of the same intent.
- **Reads pass the guard.** `gh label list` was refused on the first run and is now allowed; the labels rule fires on `create` (unrecorded), `edit`, `delete` and `clone`. The deny message says that no part of the call ran and names the matched command, because a chained `cp` and `AGENTS.md` append were lost with a refused `gh label list` and only noticed by checking.
- **`git init` is init's job.** On an empty directory there is one sensible action; the skill stops only when the directory is a worktree, a nested checkout or bare.
- **Adopt is offered, not run.** The binding names the runner, the forge and the tool behind each gate, which an empty repository cannot know. When `PRODUCT.md` records a `## Stack`, adopt reads it and confirms the matching preset's binding in one dialog, so the hand-over ends on `/ratchet:adopt`.
- **Skill invocation.** `setup-matt-pocock-skills` cannot be invoked through the Skill tool and is followed from its `SKILL.md`, with its label-creation step moved to step 8 because the remote does not exist when it runs; `impeccable init` is an argument to one skill behind a launcher, and takes the owner's answers in the same argument; `setup-pstack` needs the Claude Code adapter read first and is followed from its `SKILL.md`, since the Skill tool refuses most pstack skills. The template's skill names (`tdd`, `codebase-design`, `domain-modeling`, `code-review`, `diagnosing-bugs`, `grilling`, `pr`, `research`) were checked against mattpocock-skills 1.3.1 and all exist.
- **The pstack mix is a recommendation.** The model sheet is pstack's rule file; this skill only proposes the tiered mix the owner wanted, as a default row, and writes it only when the owner accepted the table. The second run found the file already written and skipped the setup, which is why the hand-over says where the rule came from.
- **Init installs the companions, by the harness's own commands.** Claude Code has formal plugin dependencies (the marketplace entry's `dependencies`, cross-marketplace by `allowCrossMarketplaceDependenciesOn`), so installing ratchet installs the three. Grok, Codex, pi and OpenCode have no such field, so step 1 asks once and runs the harness page's commands. Install is allowed by the guard; disable and remove are refused in every harness, because the plugin that is removed may be the one carrying the guard.
- **The template reserves "Agent skills".** The mattpocock setup updates an existing `## Agent skills` section in place, so a placeholder section keeps it from landing after "Stack rules the method leaves open".
