---
name: init
description: Lay down the bare bones any repository needs before agents build in it: a git repository, an AGENTS.md that names the four plugins composed here and what each owns, CLAUDE.md as a symlink to it, the other three plugins' one-time setup, the first commit, the remote and the triage labels.
disable-model-invocation: true
---

# Initialise a repository

The bare bones. `/ratchet:adopt` builds the gates on top later, when the owner is ready. The template is `${CLAUDE_SKILL_DIR}/templates/AGENTS.md`.

The Ratchet guard hook is already on: its command rules apply from the first session, with or without a binding, and nothing in these steps should trip it. If a tool call is denied while you follow this skill, that is a bug in this skill: write the dispute as [Before adopt](#before-adopt) says and tell the owner. Keep any command the guard might refuse in a Bash call of its own; a denied Bash call runs none of its commands, so a file write chained with it is lost.

Every step says when it is already done. A second run on the same directory skips every step and ends with the same hand-over.

**Question rounds.** Say this to the owner before the first dialog: step 4 asks one dialog; step 5 chains three interactive setups, two dialogs for `mattpocock-skills` (the remote questions ride in its tracker dialog and add none), three or four for `pstack` and up to three for `impeccable`. Eight to ten dialogs in all. The question tool takes up to four questions per dialog, so batch what one setup asks into one dialog where its skill allows.

## Steps

1. **Check the ground.** `claude plugin list` shows `mattpocock-skills`, `pstack`, `impeccable` and `ratchet`; note each one's version and marketplace (`<plugin>@<marketplace>`) for step 5. `gh auth status` succeeds when the owner may want a GitHub remote. Then git:
   - No `.git` here and no enclosing repository (`git rev-parse --show-toplevel` fails): run `git init -b main`. An empty directory is the normal case, not a failure.
   - `.git` is a directory: nothing to do.
   - Ambiguous: `.git` is a file (a worktree or submodule), `git rev-parse --show-toplevel` names a parent directory (a nested checkout), or the directory is a bare repository. Stop and ask the owner which repository they mean.

   Done when the directory is the top level of a git repository and the four plugins are listed, or you have told the owner which is missing.

2. **Write `AGENTS.md`.** Copy the template to the repository root. If an `AGENTS.md` already exists, insert the template's opening stanza (the four plugins and their questions) above its first heading and keep the rest. If a `CLAUDE.md` with content exists and no `AGENTS.md`, move that content under the stanza. Done, and skipped on a rerun, when `AGENTS.md` opens with the four-plugin stanza.

3. **Link `CLAUDE.md`.** `ln -s AGENTS.md CLAUDE.md` at the root, replacing a plain `CLAUDE.md` whose content step 2 moved. Done, and skipped, when `CLAUDE.md` is a symlink to `AGENTS.md`.

4. **Fill the title and "What this is".** Replace `# <project>` with the repository name (the `gh repo view` name when a GitHub remote exists, else the directory name). One interactive question dialog for the one-paragraph purpose. The sections below keep their placeholders: "Agent skills" for step 5, the rest for `/ratchet:adopt`. Done, and skipped, when the title and that section have no angle-bracket placeholder.

5. **Run the other plugins' setup once.** Resolve a plugin's files at `~/.claude/plugins/cache/<marketplace>/<plugin>/<version>/`, with the marketplace and version from step 1; when in doubt, `find ~/.claude/plugins/cache -path '*/<plugin>/*/SKILL.md'`.
   - **`mattpocock-skills`.** Skipped when `docs/agents/issue-tracker.md` exists. Its `setup-matt-pocock-skills` skill has `disable-model-invocation: true`, so the Skill tool refuses it: read `<mattpocock-skills>/skills/engineering/setup-matt-pocock-skills/SKILL.md` and follow it by hand, with three changes.
     - *Its Section A dialog also decides the remote.* When no `origin` exists, the one dialog asks: the issue tracker (GitHub, GitLab, local markdown, other); whether to create the remote repository now (private, recommended; public; no remote), and the repository name (the directory name, recommended; or `<owner>/<name>` as typed). A forge tracker needs a repository, so an owner who picks GitHub or GitLab and no remote gets local markdown instead, and you say why in the hand-over. The remote is created only from this answer, in step 7; never infer consent from the tracker choice.
     - *Its Section B rides in the same dialog* (keep the default labels?), so Sections A and B are one dialog and the draft confirmation the second.
     - *Its step 4 says to run `gh label create` when Section B ran on GitHub or GitLab.* Do not do it there: the remote does not exist yet. Write `docs/agents/triage-labels.md` as the skill says and leave creation to step 8, which is where that instruction is carried out.
     Put its `## Agent skills` block in the template's "Agent skills" section of `AGENTS.md` (edit `AGENTS.md`, the link target, not `CLAUDE.md`).
   - **`pstack`.** Skipped when `~/.claude/rules/pstack-models.md` exists; say so in the hand-over, since the owner may not remember writing it. In Claude Code, read `<pstack>/docs/harness/claude-code.md` first; it maps the skill's Cursor names and says the rule is written to that path. Then invoke the `pstack:setup-pstack` skill. Its defaults map every non-Claude slug to `inherit-parent`, which on a Fable or Opus session puts every worker on the most expensive model; when it shows the roles, propose a tiered mix for the owner to accept or change: `haiku` for read-only exploration, `sonnet` for code-writing and investigation, `opus` for judgment, prose and synthesis, and panels mixing two families. The owner decides; the file is pstack's, not this plugin's.
   - **`impeccable`.** Skipped when `PRODUCT.md` exists. When the repository has or will have a user interface, invoke the `impeccable` skill with the argument `init`. Its Setup runs the `impeccable context` launcher from its `scripts/` folder before the init reference; let it, and do not rerun it after `PRODUCT.md` is written. Its interview records the stack under `## Stack`, which step 9 reads. Skip when the owner says the project has no user interface.

   Done when each has run, was already done, or the owner declined.

6. **Commit.** `git add -A && git commit -m "chore(method): initialise the repository"`. The pstack rule lives in `~/.claude/rules/`, outside the repository, and is not part of it. Done, and skipped, when `git status --porcelain` prints nothing.

7. **Remote and push.** When step 5 recorded consent to a remote and `git remote get-url origin` fails, run, in a Bash call of its own, `gh repo create <name> --<visibility> --source . --remote origin --push` (`glab repo create` for GitLab), with the name and visibility the owner chose. This is the one outward-facing action in init; it needs the owner's answer from the dialog and nothing else. Done, and skipped, when `origin` exists. Skipped with "local only" when the owner chose no remote.

8. **Labels.** When the tracker is GitHub or GitLab and `origin` exists: `gh label list` (one Bash call), then for each row of `docs/agents/triage-labels.md` whose "Label in our tracker" value is missing, `gh label create <name> --description "<meaning>"` in a Bash call of its own, with exactly one name, no `--force` and no `-R`. That is the one `gh label create` the guard allows: the name is in the record the owner just confirmed, on this checkout's own repository. A denial here is a bug: see [Before adopt](#before-adopt). Done, and skipped, when every recorded label is listed. Not applicable for a local tracker.

9. **Hand over.** Report, in this order:
   - Four lines, one per plugin, saying what it now does in this repository; the pstack line says where the rule lives and whether this run wrote it or found it.
   - The commit hash, and the remote URL or "local only" with the reason (the owner chose no remote, or chose GitHub without one and so has local markdown).
   - The label state: created (the count), already present, not applicable (local tracker), or refused (the dispute's path).
   - The single next step, as the last two lines: one line saying what `/ratchet:adopt` will ask given the stack `PRODUCT.md` recorded under `## Stack` (the runner, the forge, and the tool behind each gate for that stack; without a `## Stack`, say adopt will ask for the stack first), then the bare line `/ratchet:adopt`.

   No pasted commands. Everything init can do, it has done.

## Before adopt

`/ratchet:adopt` creates `docs/disputes/`, the binding and ADR 0001; during init none exists. A denial is still the dispute signal: create `docs/disputes/`, write `docs/disputes/0001-<slug>.md` in the format `ratchet:method` gives in its guards page, and report it to whoever started you, which during an interactive init is the owner reading your reply. Adopt's template copy skips an existing file, so the record survives adoption.

## Design notes

Why the skill reads the way it does, from two runs on a fresh directory with mattpocock-skills 1.3.1, pstack 0.15.36 and impeccable 4.8.0. The first run ended with four untracked files, no remote, no commit, no labels and a hand-over of commands for the owner to paste; the second refinement removed that hand-over.

- **Init commits, creates the remote and creates the labels.** The friction after the first run was the list of commands left for the owner. Each is now a step with a skip condition, so a rerun is idempotent. The remote is the one outward-facing action, so it is created only from an explicit answer in the tracker dialog, never inferred from "GitHub Issues"; and an owner who wants GitHub Issues but no remote gets local markdown, because the tracker's skills call `gh issue` on a repository that would not exist.
- **Labels: the guard allows the recorded ones (path a).** Three sanctioned paths were weighed: (a) the guard allows `gh label create <name>` when the name is in `docs/agents/triage-labels.md`; (b) init writes a `create-labels.sh` for the owner to run; (c) the owner types the commands with the `!` prefix, which the hook does not see (observed: `! git push origin main` ran on `main`, which the push-main rule refuses from an agent). Path (a) was taken because the forgery argument ("anything an agent can set, an agent can forge") can be answered here: creating a label routes nothing, the routing act is applying one, which the guard judges as its own command; the names are those the owner confirmed in the dialog one step earlier, in a record that lands in the first commit; and once adopted, `docs/agents/**` is a guard path in the decisions group, so the list is the owner's. Everything else about labels stays refused: another name, two names, `--force` (an edit), `-R` (another repository). The "written in this session" condition from the brief was dropped: a hook has no session memory, and the record plus the guard path are the deterministic form of the same intent.
- **Reads pass the guard.** `gh label list` was refused on the first run and is now allowed; the labels rule fires on `create` (unrecorded), `edit`, `delete` and `clone`. The deny message says that no part of the call ran and names the matched command, because a chained `cp` and `AGENTS.md` append were lost with a refused `gh label list` and only noticed by checking.
- **`git init` is init's job.** On an empty directory there is one sensible action; the skill stops only when the directory is a worktree, a nested checkout or bare.
- **Adopt is offered, not run.** The binding names the runner, the forge and the tool behind each gate, which an empty repository cannot know. When impeccable's interview has recorded a `## Stack` (the first run recorded a FastAPI backend and a TypeScript frontend), init knows enough to say what adopt will ask, so the hand-over ends on `/ratchet:adopt`. Follow-up for the adopt skill, not done here: read `## Stack` from `PRODUCT.md` to prefill its runner and forge questions.
- **Skill invocation.** `setup-matt-pocock-skills` cannot be invoked through the Skill tool and is followed from its `SKILL.md`, with its label-creation step moved to step 8 because the remote does not exist when it runs; `impeccable init` is an argument to one skill behind a launcher; `setup-pstack` needs the Claude Code adapter read first. The template's skill names (`tdd`, `codebase-design`, `domain-modeling`, `code-review`, `diagnosing-bugs`, `grill-with-docs`, `pr`, `research`) were checked against mattpocock-skills 1.3.1 and all exist.
- **The pstack mix is a recommendation.** The model sheet is pstack's rule file; this skill only asks the agent to propose the tiered mix the owner wanted, not to write it. The second run found the file already written and skipped the setup, which is why the hand-over now says where the rule came from.
- **The template reserves "Agent skills".** The mattpocock setup updates an existing `## Agent skills` section in place, so a placeholder section keeps it from landing after "Stack rules the method leaves open".
