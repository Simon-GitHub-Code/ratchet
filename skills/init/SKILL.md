---
name: init
description: "Set up a repository for agents to build in: git, an AGENTS.md naming the four plugins, the pstack and impeccable one-time setups, the first commit and the remote, then the owner's next command. Asks four or five questions about the product, in one message."
disable-model-invocation: true
---

# Initialise a repository

Lay down the bare bones any repository needs before agents build in it, and end with the next command for the owner to type. `/ratchet:adopt` builds the gates on top later, when the owner is ready.

Every step says when it is already done. A second run on the same directory skips every step and ends with the same hand-over.

**The guard is already on.** Its command rules apply from the first session, with or without a binding, and no step here should trip them. Keep any command the guard might refuse in a Bash call of its own: a denied Bash call runs none of its commands, so a file write chained to it is lost. A denied tool call while you follow this skill is a bug in this skill: write the dispute as [Before adopt](#before-adopt) says and tell the owner.

## What the owner is asked

The owner is asked about the product they are making and about nothing else. The questions below go to the owner in one message, in this order, each in the words written here:

- your recommended answer first, where the records give one;
- two to four options where options help, with the free-text answer always open, so the owner can accept them all at once.

Take an answer as given; never refine it with a follow-up. A question whose answer a record already holds (a README, a package manifest, `PRODUCT.md`, an earlier run) is not asked: read the answer, and say in the hand-over where it came from.

1. **Ask** `init.purpose`: "What are you making? Tell me in a sentence or two what it should do."
2. **Ask** `init.user`: "Who is it for, and what are they doing when they reach for it?"
3. **Ask** `init.difference`: "What should make it better for them than what they use today?"
4. **Ask** `init.place`: "Where will people use it: on this computer only, on other devices at home, or from anywhere?"
5. **Ask** `init.online`: "Should this project be kept on this computer only, or also as a private copy on your GitHub account?" Only when `gh auth status` succeeds and `origin` is missing (`glab` and GitLab alike). Otherwise the repository stays local and the hand-over says why. The remote is created only from an answer that asks for it.

**What the owner asked for wins.** Anything typed after the command (`/ratchet:init no GitHub`) is a preference. It chooses among the options a choice allows and never loosens a safeguard, as [the rule on preferences](../method/records.md#a-preference-typed-after-a-command) says: read it before you act on one. Within that, it settles the choice it names and wins over the defaults below. A question it answers is not asked ("no GitHub" answers `init.online`). Each one is listed under **Assumed** as "you asked for", or as not applied with the route that exists.

**Everything else is assumed.** Take each default below from the evidence and act on it. The hand-over lists it under **Assumed** (step 9) with what changing it later costs, where the owner reads it and overturns it in their own words. Leave out a row whose step is already done.

- **Assume** `init.defaults`:
  - The repository name is the directory name, or the `gh repo view` name when `origin` exists.
  - The issue tracker is GitHub Issues when the repository has a GitHub remote or gets one from `init.online`, GitLab's likewise, and local markdown otherwise.
  - pstack's models are the tiered mix at `medium` when `~/.claude/rules/pstack-models.md` is missing.
  - A missing companion plugin is installed by the harness page's commands.
- **Assume** `init.interface`: a web interface that works on a phone and a laptop, unless the purpose names something else (a native app, a command-line tool, a service with no screen) or the code has one already.

**The stack is not chosen here.** How it is built (what it is written in, where it runs, where its data is kept, its parts and the tools that check it) is offered to the owner at `/ratchet:adopt`, as whole systems to choose between, recommendation first. Init records only what the owner said about where it is used, and adoption drafts those options from it.

## Steps

1. **Check the ground.**
   - Name the harness you run in and read its page, `${CLAUDE_PLUGIN_ROOT}/docs/harness/<harness>.md` (Claude Code, Grok, Codex, Antigravity, pi or OpenCode). It gives the command that lists installed plugins, the one that installs each of the four, and the names this skill uses mapped to that harness.
   - List the plugins. Note each one's version and marketplace (`<plugin>@<marketplace>` in Claude Code) for step 5.
   - When `mattpocock-skills`, `pstack` or `impeccable` is missing, install it without asking and say so in the hand-over: run the page's install commands, each in a Bash call of its own (`init.defaults` assumes it). Installing is allowed; removing is refused.
   - A plugin the page says has no package for this harness is reported in the hand-over as missing, not installed another way.
   - `gh auth status`, and `glab auth status` when `glab` exists, decide whether `init.online` is asked.
   - Look for code: a package manifest, a lockfile, source directories. None means greenfield, whose stack adoption asks.
   - Check git:
     - **No `.git` here and no enclosing repository** (`git rev-parse --show-toplevel` fails): run `git init -b main`. An empty directory is the normal case, not a failure.
     - **`.git` is a directory:** nothing to do.
     - **Ambiguous:** `.git` is a file (a worktree or submodule), `git rev-parse --show-toplevel` names a parent directory (a nested checkout), or the directory is a bare repository. Stop and say what you found: the owner runs init again in the directory they mean.
   - Ask the questions of [What the owner is asked](#what-the-owner-is-asked) and settle the assumptions.

   Done when the directory is the top level of a git repository, the four plugins are listed (or the hand-over will name the one missing) and each question that was asked is answered. A question that a record or a typed preference answers, or whose condition does not hold, is not asked.

2. **Write `AGENTS.md`.** The template is `${CLAUDE_SKILL_DIR}/templates/AGENTS.md`. Its opening stanza names the four plugins.
   - **No `AGENTS.md`:** copy the template to the repository root. If a `CLAUDE.md` with content exists, move that content under the stanza.
   - **`AGENTS.md` exists without the stanza:** insert the template's opening stanza above its first heading and keep the rest.

   Done, and skipped on a rerun, when `AGENTS.md` opens with the four-plugin stanza.

3. **Link `CLAUDE.md` and extend `.gitignore`.**
   - Run `ln -s AGENTS.md CLAUDE.md` at the root, replacing a plain `CLAUDE.md` whose content step 2 moved. Skipped when `CLAUDE.md` is a symlink to `AGENTS.md`.
   - Append to `.gitignore` (create it if missing) each line of `${CLAUDE_SKILL_DIR}/templates/gitignore` it lacks: the agent worktrees pstack's panels leave under `.claude/worktrees/` and impeccable's caches, which whole-tree gates would otherwise judge. Skipped when every line is present.

   Done when `CLAUDE.md` is a symlink to `AGENTS.md` and `.gitignore` holds every line of `${CLAUDE_SKILL_DIR}/templates/gitignore`.

4. **Fill the title and "What this is".** Edit `AGENTS.md`, the link target, not `CLAUDE.md`.
   - Replace `# <project>` with the repository name.
   - Write the purpose paragraph from the owner's answers, in their words where they gave them, with where it is used. Adoption drafts the ways to build it from that paragraph.
   - Record the tracker, so the tracker setup does not ask again for what init settled. Under the "Agent skills" heading, above its placeholder, add one line naming the tracker `init.defaults` settled and why, such as `Tracker chosen at init: local markdown, because the repository has no remote.` The tracker setup reads this file and takes it as its answer.
   - Leave the placeholders of the sections below: "Agent skills" for the tracker setup, the rest for `/ratchet:adopt`.

   Skipped when the title and "What this is" have no angle-bracket placeholder. Done when neither has one and "Agent skills" holds the tracker line or the tracker setup's block.

5. **Run the other plugins' setup once, from the answers already given.** Resolve a plugin's files at `~/.claude/plugins/cache/<marketplace>/<plugin>/<version>/`, with the marketplace and version from step 1. When in doubt, run `find ~/.claude/plugins/cache -path '*/<plugin>/*/SKILL.md'`.
   - **`mattpocock-skills`: not run here.** Its `setup-matt-pocock-skills` skill is user-invoked (`disable-model-invocation: true`). The Skill tool refuses it to an agent, and its author's rule is that no skill calls or imitates it. So the owner types `/mattpocock-skills:setup-matt-pocock-skills` after init, and the hand-over names it as the next command.
     - Skipped, and said so in the hand-over, when `docs/agents/issue-tracker.md` already exists.
     - Init creates the remote first (step 7), so that the labels that setup creates have somewhere to go.
   - **`pstack`: skipped when `~/.claude/rules/pstack-models.md` exists.** Say so in the hand-over, since the owner may not remember writing it. Otherwise:
     1. Read `<pstack>/docs/harness/claude-code.md` first. It maps the skill's Cursor names and says the rule is written to that path.
     2. Follow `<pstack>/skills/setup-pstack/SKILL.md` with the budget answered as `medium` and the roles taken from the tiered mix: `haiku` for read-only exploration, `sonnet` for code-writing and investigation, `opus` for judgment, prose and synthesis, panels mixing `opus` and `sonnet`. Every model written must be one the Agent tool lists.
     3. Skip its two closing offers (a verification skill, a formal verification skill). `/ratchet:adopt` names them in the binding's formal group when the stack is known.

     The file is pstack's, not this plugin's. `init.defaults` assumes the mix, and the owner can rerun `/pstack:setup-pstack` at any time.
   - **`impeccable`: skipped when `PRODUCT.md` exists, or when `init.interface` is no user interface.** Otherwise invoke the `impeccable` skill with the argument `init` followed by the owner's answers: the purpose paragraph, the user and job, what makes it better, where it is used, the interface as assumed, and the sentence "the owner gave these answers this session; the platform is an assumption they can overturn, and the stack is not chosen yet: it is chosen with the owner at adoption; write PRODUCT.md from them, keep the owner's own words, and record no workflow default".
     - Its Setup runs the `impeccable context` launcher from its `scripts/` folder before the init reference. Let it, and do not rerun it after `PRODUCT.md` is written.
     - A question it still asks is listed in the hand-over.

   Done when each has run, was already done, or the owner declined.

6. **Commit.** Run `git add -A && git commit -m "chore(method): initialise the repository"`.

   Done, and skipped, when `git status --porcelain` prints nothing.

7. **Remote and push.**
   - **`origin` exists:** skipped.
   - **The answer to `init.online` asked for a private copy and `git remote get-url origin` fails:** run, in a Bash call of its own, `gh repo create <name> --private --source . --remote origin --push` (`glab repo create` for GitLab). This is the one outward-facing action in init. It needs that answer and nothing else, never from the tracker choice alone.
   - **The question was not asked, or the owner kept it on this computer:** skipped, with "local only".

   Done when `origin` exists, or the hand-over will say "local only".

8. **Labels.** Only on a rerun where `docs/agents/triage-labels.md` exists (the tracker setup wrote it) and the tracker is GitHub or GitLab with `origin` present. Not applicable for a local tracker.
   - Run `gh label list` in one Bash call. A label is missing when that list does not show it.
   - For each row of the record whose "Label in our tracker" value is missing, run `gh label create <name> --description "<meaning>"` in a Bash call of its own, with exactly one name, no `--force` and no `-R`.
   - That is the one `gh label create` the guard allows: the name is in the record the owner just accepted, on this checkout's own repository. A denial here is a bug: see [Before adopt](#before-adopt).

   Done, and skipped, when every recorded label is listed.

9. **Hand over.** Report, in this order:
   - **The plugins.** Four lines, one per plugin, saying what it now does in this repository. A line says so when this run installed its plugin, or when the plugin is missing because it has no package for this harness. The `mattpocock-skills` line says so when its setup was skipped because `docs/agents/issue-tracker.md` exists. The pstack line says where the rule lives and whether this run wrote it or found it.
   - **The commit and the remote.** The commit hash, and the remote URL or "local only" with the reason (no forge login, or the owner kept it on this computer, and so local markdown is the tracker).
   - **The labels.** Created (the count), already present, not applicable (local tracker), not yet (the tracker setup creates them), or refused (the dispute's path).
   - **Assumed.** The block the method's rule on asking describes, with no question key or method term in it. One line per assumption this run took (each part of the defaults that applied, the interface) and per preference the owner typed: "you asked for", or, where the rule on preferences kept it from applying, as not applied, with what was asked for, why not and the route that exists. Each line says what was chosen and why in a clause, what changing it later costs (what changes, roughly how much work, and whether anything built is redone), and what the owner can say to change it, such as ``Screen: a web page that works on your phone and your laptop, because you said you will use it on both. Changing it before you run `/ratchet:adopt` is one line in the project notes, and nothing built is redone; adoption then offers ways to build it that fit what you say. Say "make it an app" or "no screen" to change it.``
   - **The questions.** The count of questions the owner answered, where the answer to each question not asked came from, and each one a companion plugin's setup still asked.
   - **The route, as the last lines.** Run `python3 -E -s -B -X pycache_prefix=/nonexistent/ratchet-no-bytecode "${CLAUDE_PLUGIN_ROOT}/scripts/route.py" --root . --offline` in one Bash call and print its lines (`next:`, `why:`, `then:`, and any `also:`) as they are. Close with the bare command on its own line.

   Give the owner no command to paste besides that one.

   Done when the reply holds each part above, in that order, and ends with the bare command.

## Before adopt

`/ratchet:adopt` creates `docs/disputes/`, the binding and ADR 0001; during init none exists. A denial is still the dispute signal:

1. Create `docs/disputes/`.
2. Write `docs/disputes/0001-<slug>.md` in the format `ratchet:method` gives in its guards page.
3. Report it to whoever started you, which during init is the owner reading your reply.

Adopt's template copy skips an existing file, so the record survives adoption.
