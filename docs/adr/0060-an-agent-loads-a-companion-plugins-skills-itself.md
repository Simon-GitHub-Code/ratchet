---
status: accepted
---
# An agent loads a companion plugin's skills itself: pstack builds, Ratchet verifies and ships

Amends ADR 0009 (the route names the owner's next keystroke). Decided by the owner, 2026-11-15.

**Ratchet's position on its companions.** `mattpocock-skills` makes the upfront decisions: design, product and architecture. `pstack` builds. Ratchet verifies and ships, with deterministic checks where an agent's judgement would otherwise stand. `impeccable` keeps its question: how anything a person sees is designed. This record applies that position to pstack.

**What ADR 0009 rested on.** Every pstack skill but `setup-pstack` sets `disable-model-invocation: true`, so the harness refuses them to an agent. That holds for the upstream build (0.15.36, 57 of 58 skills flagged, `poteto-mode` among them). It does not hold for the build Ratchet declares: the marketplace entry names `pstack@pstack-claude` (`.claude-plugin/marketplace.json:11-15`), the Claude Code port, 0.9.135 to 0.9.137, which ships 67 skills and flags none. Four of the six harness pages install that port (`docs/harness/claude-code.md:9`, `codex.md:11`, `grok.md:9`, `pi.md:8`); Antigravity's leaves it to init and OpenCode's to copied files. The port's session hook sends the main session to `pstack:poteto-mode` and says `AGENTS.md` takes precedence. So what keeps agents from pstack today is the `AGENTS.md` line "since the Skill tool refuses these to an agent", not the harness. ADR 0009's refutation criterion named this case.

**The measured baseline.** Seven recorded first-hour runs, five at `fa9ee31` and two at 0.11.1. Two of the five are void (run in parallel under load). Only three runs started the pipeline, so only three have workflow-role files (26 files).

| Place | `Skill` calls | pstack | Refused |
| --- | --- | --- | --- |
| Main session | 20 | 0 | 0 |
| `Agent` subagents | 0 | 0 | 0 |
| Workflow roles, deduplicated by `tool_use` id | 25 | 0 | 0 |

- Main-session loads were `impeccable:impeccable` at init and `ratchet:method` and `mattpocock-skills:domain-modeling` at adopt.
- Every role load was a skill its brief names, or `ratchet:method`.
- One run spawned an agent of type `pstack:poteto-agent` at adopt. It loaded no pstack skill.
- pstack's hook note reached every run several times. No agent acted on it.

So an agent's pstack load has never been observed. Observing one is a precondition (point 6).

## Decision

**1. One rule, read from the declared build's frontmatter.** An agent loads a companion skill whenever it fits the task, without waiting to be asked. A skill whose `SKILL.md` sets `disable-model-invocation: true` is the owner's command on every harness, and Ratchet names it only as a command the owner types.

- **The flag decides who may start a skill. The description decides only whether it fits.** `reflect` ("Use when the user says reflect"), `automate-me` and `setup-pstack` fit only when the owner asks in words, so they need no rule of their own.
- **The declared build.** The dependency check (point 3) reads the build the harness lists. A companion from a marketplace other than the declared one is reported as not the declared build. Behaviour is then the same wherever the declared dependency is installed.
- **Where the harness does not enforce the flag.** Claude Code and pi remove a flagged skill from the agent's list (`claude-code.md:28`, `pi.md:32`). Codex, Grok and Antigravity tell the agent to "read the `SKILL.md` and follow it" (`codex.md:36`, `grok.md:29`, `antigravity.md:43`), and OpenCode has no flag (`opencode.md:27`). On those four the agent reads the frontmatter itself and follows a flagged skill only when the owner names it. The four rows say so. This is the rule Ratchet already states (rewritten `skills/method/route.md:7-13`) and mattpocock-skills' own rule that no skill calls or imitates its setup (rewritten `skills/init/SKILL.md:82`).
- **A typed command or a role's brief is the playbook for its run.** The agent loads no playbook skill that the command or the brief does not name. The build briefs name `poteto-mode` (point 5).

Why: pstack is built to be entered by the agent. A keystroke that starts what the agent would start anyway costs the owner's attention and controls nothing. The control is the guard and the merge judge, and they hold whatever skill is loaded (point 2).

**2. From "a change is ready on a branch" onward, the work is Ratchet's.** These steps are Ratchet's:

- the pull request body, from the repository's template;
- the gates;
- the merge judge;
- the merge job, or with no forge the `merge` entry point.

pstack's playbooks yield their pull request format, their merges, their landing and their restacking at that point. A red gate or an open review thread on the branch is build work again. pstack may do it by pushing to the feature branch, which the guard allows. Ratchet has no step of its own there: on an open pull request the route says to wait (`scripts/route.py:110`, `:628`).

The guard stays as strict as it is. No skill changes what it holds:

- **Merges.** An agent's merge asks the owner once per pull request (`hooks/ratchet_guards/verdict.py:94-107`), and the ask names the merge job or the `merge` entry point.
- **History.** Force-pushes in every spelling, `--force-with-lease` included, are denied on every branch (`git.py:21`, `:130-131`), and so are pushes to the default branch, resets that move it, amending a pushed commit and history filters (rewritten `skills/method/guards.md:149`). pstack's restack pushes with `--force-with-lease` (pstack 0.9.137, `skills/poteto-mode/references/merge-safety.md:29`) and meets this denial.
- **Files that act at once.** A write to a CI workflow or an agent-instruction file asks the owner on every branch (rewritten `guards.md:91-106`).
- **Locked paths and guard paths** are held as today.
- **The agent-ready label** asks the owner once per call (`verdict.py:113-117`).

Today the force-push and amend-pushed denials carry the default way out, a gate dispute (`verdict.py:53`, `:80`, `:92`). Each gets a way out that names Ratchet's step: bring the branch up to date without rewriting it, and the merge job or `merge` lands it. The decision stays `DENY`.

Why: if pstack's landing raced the merge judge, two judges would decide one merge.

**3. Ratchet names each dependency once and tests it. It does not describe the other plugins or list their skills.**

- **The dependencies.** The marketplace entry is the one list (`marketplace.json:11-15`). Ratchet keeps no list of companion skills.
- **The template.** Each companion line of the `AGENTS.md` template becomes ``- `<plugin>` ``, with no question and no rule. The Ratchet line keeps "load the `ratchet:method` skill before any work", which is Ratchet describing itself; conformance already requires that string (`scripts/conformance.py:503`). Every rule about using a companion moves into the method's pages, and those pages ship with the plugin.
- **A companion skill is named only where a Ratchet step uses it**, in one of two forms: `/<plugin>:<skill>`, a command the owner types, or `<plugin>:<skill>`, a skill an agent loads. No page lists or describes a companion's skills or names a companion's agent types; pstack's own routing picks its skills.
- **What counts as a reference.** Only those two spellings, where `<plugin>` is a dependency the marketplace entry declares, matched as a whole token in any text, backticked or not. Nothing else is a reference. A bare name is a word, backticked or not, and neither check reads it, even when it equals a skill's name. So no judgement decides what a mention means:
  - `scripts/route.py:107` prints "the `pr` skill fills the body", an instruction to load. It becomes `mattpocock-skills:pr`, so the checks see it.
  - `scripts/feature_preflight.py:5` continues `:4`'s list, and `:113` names the same three skills. Both spell each in full command form, `/mattpocock-skills:to-tickets` and `/mattpocock-skills:triage` included.
  - `feature_preflight.py:13` (`to-tickets`) and `route.py:33-34` (`grill-with-docs`, `to-spec`, `to-tickets`) are docstrings that describe. They stay bare and unchecked. The commands they describe are checked where the route prints them (`route.py:100-102`).
  - `docs/model/route.md:63` and `:95` use `triage` as the route model's advice label. It stays, and so do `pr` and `research` as ordinary words.
- **Agent types.** `docs/harness/claude-code.md:30` (on main and in the rewrite) names `pstack:poteto-agent`, an agent type that no build ships as a skill, so the runtime check would report it missing on a correct install. The cell becomes "`Agent`; a plugin's agent types by their full name". With no page naming a companion's agent type, the derivation needs no list of agent types, which only the harness could supply.
- **The suite test.** It derives every companion reference from Ratchet's own files (`skills/`, `workflows/`, `scripts/`, `README.md`, `docs/` outside `docs/adr/`). Each must appear in one form only. No `/pstack:` appears at all.
- **The runtime check.** At init's first step, and in an evaluation that installs the companions, the same derivation is compared with the build the harness lists. An agent load must exist and be unflagged. An owner command must exist, flagged or not. A companion from an undeclared marketplace is reported. A companion's drift can only be caught where it is installed, since CI installs none.
- **Before the first run of either check,** four mentions of flagged skills written without the slash become command form: `scripts/feature_preflight.py:4`, `:113`, `:129` and the comment at `workflows/pipeline.js:10` name `to-spec` and `triage` as loads. None is dropped. `:113` and `:129` are `PRODUCES` texts that each failure carries (`:192`) and the preflight prints (`:693`), as its docstring promises (`:6-7`).

**The stale-line comparison goes, and one shape check migrates the old lines.** `stale_plugin_lines` and its branch in `setup_step` are deleted (`route.py:93-94`, `:638-652`, `:685-691`). Nothing compares a repository's text with the template again.

- **The shape check.** A conformance finding holds that each companion line is exactly ``- `<plugin>` ``. A companion line is a line of the list before the first `##` heading of `AGENTS.md` whose first backticked word is a declared companion's name, so a project's own bullet that names a plugin elsewhere in the file is never rewritten. Its edit writes that shape from the plugin name the line already holds, so no template text is copied. The stanza's opening sentence names no plugin and is left alone.
- **Where it runs.** In an adopted repository the upgrade applies it, like every conformance edit (`scripts/upgrade.py:4-7`). The edit reaches the default branch through the owner's merge (`upgrade.py:12-14`). The upgrade is offered as an `also:` line when the binding pins an older plugin (`route.py:999-1014`), which the owner reads through `/ratchet:next` and the tier output; the session hook drops `also:` lines (`hooks/ratchet_guards/session.py:84`). A repository that ran init but is not adopted has no pin and no upgrade (`route.py:1005-1007`, `upgrade.py:180-181`). There adopt's last step runs conformance and fixes what it names (`skills/adopt/SKILL.md:104` on main), on adopt's own branch (`:55`), where nothing is asked before the adoption merges (`hooks/ratchet_guards/paths.py:208-214`). Init writes the template, which already has the shape. One case asks, once: an offered edit that does not clear its finding falls to an agent (`conformance.py:396-409`), whose edit of `AGENTS.md` is asked on the branch.
- **Why the migration is needed.** Without it the old pstack line stays in every adopted repository, and pstack's hook defers to `AGENTS.md`, so the decision would never take effect there.

**Precondition of the release that merges the prose rewrite of the agent-facing pages.** That rewrite changes all four plugin lines while the comparison still runs. With the comparison, the route names `/ratchet:init` first (`route.py:807`, `:685`), init's edit of `AGENTS.md` on the default branch is denied (rewritten `guards.md:225`, `:109`), and the route names init again. So that release ships only with the comparison deleted and the shape check in. One upgrade across it must finish on an adopted repository: after the owner's merge, conformance reports no shape finding.

**4. The route names the owner's next move, and never a pstack command.**

- **`next:` does not change.** It has never named a pstack command.
- **`why:` and `then:`.** The pstack command appears only there, and becomes words the owner says to the session. The agent that hears them builds and hands over at point 2.
  - **One ticket ready** (`route.py:506`): "or ask this session to build ticket 5 with you here".
  - **The drain** (`:497`): "or ask this session to build one of them with you here".
  - **A ticket the drain parked** (`:552`): `then:` is `/ratchet:feature`. It adds "or ask this session to make the guard edits with you here; their merge is yours" only when `queue_notes.GUARD` is among the hold's kinds.
  - **A stashed blocked run** (`:561`): the same rule. The added words appear only when the run's queue note records `queue_notes.GUARD` among its kinds. A run blocked on questions alone names `/ratchet:feature` after the answers.
- **A ticket built in the session runs the feature preflight first**, the same script `/ratchet:feature` runs. Its exit decides, not the agent. The method already promises this for both routes: "the preflight refuses anything less before an agent exists" (rewritten `skills/method/SKILL.md:160` [`:76`]).

Why: staying at the keyboard is a request made in words, and choosing the skill is the agent's job.

**5. The pipeline stays, and its build roles work pstack's way within the pipeline's limits.**

- **The implementer and fixer briefs** gain one sentence: load `pstack:poteto-mode` if your skill list has it, and build its way within these limits. Today they name only `mattpocock-skills:tdd` and `impeccable:impeccable` (`workflows/pipeline.js:981-984`, `:1015`).
- **The limits.** The role spawns no agent. It does not commit, push or open a pull request. It keeps the brief's `tdd` loop.
- **What holds the limits today.** The prompt alone. `pipeline.js` passes no tool list to any role. `AGENTS_AT_MOST` counts only the script's own `agent()` calls (`pipeline.js:42-48`). Nothing checks after a role that the branch moved. The pipeline model's `Bounded` and `Unpublished` cover the script's calls and the prompts' text (`docs/model/pipeline.md:59`, `:65`), and `Unpublished` is the rule a loaded build playbook threatens, since pstack's playbooks end in a pull request.
- **The check that makes "no commit, push or pull request" deterministic: the publish check.** A Workflow script cannot read a file or run a command (`workflows/pipeline.js:16-19`), so the check is a script the orchestrator runs, as the preflight is. The starting state is recorded by the deterministic preflight the skill runs (`scripts/feature_preflight.py`, its `Report` at `:196-209`), never by the preflight role the workflow runs as an agent (`workflows/pipeline.js:16-19`): an agent does not report the state it is judged against. The report gains the branch head, and with a forge also the remote branch and the open pull request for it. On a rejudge the starting head is the rebased head the drain records. After the run returns, or after it dies, and before a resume, any close and the orchestrator's first commit (rewritten `skills/feature/SKILL.md:68` [`:40`] for one ticket, `:137` [`:70`] in the drain), one script compares the state with that report, and its exit decides. It runs once per run, not once per role, so a role that published is found after the run, and the roles after it still ran.
  - **A reconcile that finishes a rebase is not publishing.** After a rejudge, the branch passes when each commit ahead of the base has the message and author date of a commit that was ahead before the run, in the same count and order, and nothing was pushed or opened. On a conflict the feature skill leaves the branch as it was (rewritten `skills/feature/SKILL.md`, step 4.3), and this rule depends on that.
  - **A branch that moved overrides every verdict.** The ticket is parked for the owner with the reason that a role published, and it is never retried or approved, whatever the review said.
  - That the orchestrator runs it is prose, as for the preflight.
- **The model turns come first.** The pipeline model gets a role answer "published" that the script passes through unseen, and the page's words on what `Bounded` and `Unpublished` cover. The drain model, which has the land and park steps, gets the rule that catches it. Both land before any script change of this point. This record names them and does not specify them.
- **No spawn** stays a prompt rule unless the workflow API takes a tool list (not checked). The role reader of point 6 measures it.
- **The preamble every role reads** gains two sentences. The brief is the role's playbook: load no playbook skill it does not name. The route line in the role's context is the owner's. Every one of the 26 recorded role files carries the hook's "Next, for the owner to type" sentence.
- **The critic's sentence** names no skill: the run's agent count is fixed, so the critic works in-process and loads nothing that spawns agents (`pipeline.js:1083-1085`).

The brief sentences land only after the publish check is in and the precondition run passes. If a role following `poteto-mode` breaks a limit, the build briefs keep their current text, and the rest of this decision goes ahead.

**6. Preconditions, in order.** No method page changes until these hold.

1. **The eval reader counts skill loads.** Today the reducer counts none, and `run.py reduce` rereads only gate logs (`evals/first-hour/run.py:50-55`, `reduce.py:35`).
   - **Main session and `Agent` subagents** are in `transcript.jsonl`. A subagent's messages carry `parent_tool_use_id`. The reader adds a `skill` event from `AssistantMessage` blocks, matched on the JSON text (`transcript.py:3-6`), and a `skill-refused` event when the call's tool result is an error.
   - **Workflow roles** are only in the run home, at `home/.claude/projects/<app>/<session>/subagents/workflows/wf_*/agent-*.jsonl`, in Claude Code's own format. The role reader counts each role's `Skill`, `Agent` and `Read` calls (a read of a pstack `SKILL.md` counts as a load) and its Bash calls that commit, push or open a pull request.
   - **Deduplication.** Replayed request records repeat earlier `tool_use` blocks. The reader counts each `tool_use` id once.
   - **Joining a role to its step.** The file name's agent id is the `agentId` in the progress snapshots of `transcript.jsonl`, and the folder's `wf_*` id joins it to the `Workflow` call. The driver's step comes from that record.
   - **A layout the reader does not find.** Claude Code can move its storage. A run whose transcript shows a workflow and whose home holds no role file is reported unreadable, not as zero loads.
   - **Both readers fold into `reduce`,** so recorded runs recount without a new run. `harness.py:786` gets the same pass.
   - **The recount reproduces the baseline above.** The recorded runs sit in temporary storage, so the reproduction runs before they age out. The reader's tests keep a fixture cut from them with local paths removed: a role `Skill` call, a replayed record, a refused call, and a `SKILL.md` read, which no recorded run has.
2. **One run shows that an agent's pstack load succeeds, and tests the pipeline's limits.**
   - **When.** Not before 0.12.0 is published and the current first-hour series has logged its end line. It runs alone on the machine, under the load rule in force for first-hour runs, and waits while seven-day usage is past 60%.
   - **The run.** The one persona whose recorded run reached a usable merge (`go-todo-cli`), on a branch with three changes: the point 3 template lines, the point 1 rule in `route.md`, and the point 5 brief sentences on top of the publish check. A run of the same persona on the same base without the three changes is its pair.
   - **Which load counts.** A self-started pstack load: a `Skill` call to a `pstack:` skill that the starter's brief, the `Agent` call's prompt or the owner's typed command did not name, by full name or bare name as a whole word.
   - **What it must show.** (a) At least one self-started pstack load, with no `skill-refused` event. (b) In the pipeline run, no `Agent` call by a role, no role Bash call that commits, pushes or opens a pull request, and no publish check fired. (c) No `poteto-mode` load by the main session inside a typed Ratchet command.
   - **If (a) fails,** the pages do not change. **If (b) fails,** point 5's brief sentences are dropped.
   - **The fixer** runs only on a review blocker. If the run has no fix pass, the first run that has one is checked the same way.
   - (a) to (c) are presence checks. Minutes and cost are read only against the pair. The first-hour driver never gives work in words, so the stay-at-the-keyboard words are measured later, by the epistemology cases.

**7. What stands of ADR 0009.** The route and the script that decides it, init stopping before the tracker setup, the two build routes, and the rule that an agent never imitates a refused skill. What changes: the claim that pstack's skills are refused, the form of the stay-at-the-keyboard route, the critic's reason, and what the build roles load.

## Refutation criterion

This is wrong if any of these is seen. Each names a record that exists, or the change that creates it.

- **Loading pstack costs more than it gives.** Across the precondition pair and later pairs on one base, the with-arm's `metrics.json` shows more `minutes.to_first_usable_merge` or session cost, with no fewer questions and no fewer red gates or guard denials. The seven recorded runs are a baseline for load counts only. `minutes.to_first_feature_merge` counts a spec branch's merge too (`reduce.py:373-382`), and only one recorded run reached a usable merge, so neither serves for cost. Void runs enter no baseline.
- **A pstack playbook breaks the pipeline's limits.** Through the point 6 reader: a role spawns an `Agent`, a role commits, pushes or opens a pull request, the publish check fires, or `poteto-mode` is loaded by the main session inside a typed Ratchet command or by a role whose brief does not name it.
- **Work reaches the default branch past the guard.** In the default branch's first-parent history: a merge whose `guard-diff` lists a guard path, or that carries the agent-ready label, and that the merge judge did not leave to the owner. On a forge the pull request's record says who merged. With no forge, the guard event log (Settled by the owner, item 1) shows whether the guard asked or denied on the way.
- **A guard denial leaves an agent stuck between pstack and Ratchet.** In `transcript.jsonl` or the role files: a denial in the shipping stretch whose way out names no Ratchet step, followed by a turn that ends asking the owner.
- **The owner still has to type a pstack command.** A route event in `events.jsonl` whose `why` or `then` holds `/pstack:`, a turn that asks the owner for one, or a hit from the suite test.
- **The flag is misread.** The runtime check reports an agent load as flagged in the declared build, or a `skill-refused` event names a skill that a Ratchet page tells an agent to load.

## Considered options

- **Keep pstack the owner's, as ADR 0009 does.** Rejected: the premise is false for the declared build, the owner decided otherwise, and pstack's own hook asks for the opposite.
- **Keep a hand list of companion skills, and check names against it.** Rejected: nothing checks the list against the companions, and the check reports Ratchet's seven mattpocock-skills owner commands as flagged on a correct install.
- **Treat a description such as "Use for /x" as a second test of who may start a skill.** Rejected: two agents would decide it differently.
- **Make flagged skills anyone's where the harness does not enforce the flag.** Rejected: it breaks mattpocock-skills' no-imitation rule there.
- **Keep the stale-line comparison, as a conformance finding or an `also:` line.** Rejected: it keeps the cause. Each later template change sends every adopted repository through another rewrite.
- **Let pstack restack with `--force-with-lease` off the default branch.** Rejected: that loosens the guard. Ratchet's merge job makes the restack unnecessary.
- **Keep pstack out of every pipeline role.** Rejected: in Ratchet's position the build is pstack's.
- **Run a multi-model adversarial skill inside the critic.** Deferred: the run's agent count does not allow a panel. The critic's rule of evidence already keeps only findings a run proves.

---

## For the builders

Page line numbers are those of the rewritten agent-facing pages, which land before this record's page changes, with `main` at `bcd3e4c` in brackets where it differs. Scripts, hooks and the workflow are the same on both. Eval lines are on `origin/main`. The page changes land after the rewrite, under point 3's precondition.

**Eval reader (precondition 1).** `evals/first-hour/first_hour/transcript.py:72-85`: the `skill` and `skill-refused` events. A role reader over the run home's role files. `reduce.py:35` and `run.py:50-55`: fold both into `reduce`. `harness.py:786`: the same pass live. The fixture under the reader's tests.

**Dependencies, template and shape (point 3).**

- `skills/init/templates/AGENTS.md:5-7` [`:5-7`]: ``- `<plugin>` `` for each companion.
- `scripts/route.py:93-94`, `:638-652`, `:685-691`: delete the comparison. `hooks/tests/test_route.py:719-738` goes with it.
- `scripts/conformance.py:496-507`: the shape finding beside the Asking section's, with its test.
- `skills/init/SKILL.md:60-65` [`:44`]: drop the stale-lines case and "its plugin lines match the template".
- `skills/init/SKILL.md:81` [`:53`]: resolve each plugin from the harness's plugin list. Drop the `find ~/.claude/plugins/cache` fallback, which matches both pstack builds. Run the runtime check here.
- `skills/init/SKILL.md:85-90` [`:55`]: init loads `pstack:setup-pstack` instead of reading and following its file. "rerun `/pstack:setup-pstack`" becomes "ask any session to run pstack's model setup again".
- `scripts/feature_preflight.py:4-5`, `:113`, `:129`, `workflows/pipeline.js:10`: command form, every skill named in full. `scripts/route.py:107`: `mattpocock-skills:pr`.
- `conformance.py:496-507`: the companion line is recognised in the list before the first `##` heading only.
- `hooks/tests/test_skills.py:222-233` (pins pstack's chain words) and `:350-372` (the hand list `owners`): replace both with the suite test.
- `docs/model/route.md:369`: the left-out `also:` lines include the upgrade's offer. The route model needs no change, since it never had the stale state (`hooks/tests/route_model.py:29-41`).

**Shipping (point 2).** `verdict.py:80` and `:92`: a way out that names Ratchet's step, pinned in the verdict tests. `skills/method/route.md:173-179` [`:56-62`] becomes "Where a build hands over to Ratchet": the steps of point 2 and the guard's holds, linking `guards.md`, naming no companion playbook.

**The route.** `route.py:4-9`, `:35-36`, delete `:105` (`POTETO`), and the words at `:497`, `:506`, `:552`, `:561`. `test_route.py:273` asserts the new words name ticket 5. The route model compares `then:` and `why:` only for the safeguard, architecture and hooks steps (`hooks/tests/test_route_model.py:466-479`), and no eval parses them (`steps.py:119-132`).

**The method pages.** Stage rows name Ratchet's steps and the owner's commands only.

- `skills/method/route.md:3`: replace "most of their skills are the owner's keystrokes". `:5-20` [`:5-9`]: the rule of point 1, with no skill list. `:32-37` [`:21-26`]: drop every `/pstack:` command and every agent-loaded skill from the rows; stage 7 gets the words of point 4. `:39` [`:28`]: delete.
- `skills/method/SKILL.md:10-16` [`:10`]: Ratchet's position on its companions. `:70`: drop "Most skills ... are the owner's keystrokes". `:160` [`:76`]: the words and the preflight.
- `skills/method/code.md:9` [`:3`]: delete the `/pstack:architect` mention.
- `docs/model/README.md:43`: the charts are drawn by an agent's load of `pstack:statechart`, not the owner's `/pstack:statechart`.
- `skills/method/pipeline.md:9-10` [`:17`]: the build roles per point 5; drop `diagnosing-bugs`, which no brief names. `:74` [`:12`]: the critic. `:138` [`:28`]: the words.
- `skills/method/tiers.md:106`, `:109`: `pstack:benchmark-checklist`, loaded by the agent that measured, as `skills/adopt/SKILL.md:224` already says. `:113`: the agent loads the formal verification skills; where their output can live is F1.
- `skills/next/SKILL.md:35` [`:26`], `skills/conjecture/SKILL.md:120` [`:53`], `skills/feature/SKILL.md:60` [`:34`], `README.md:103`: the words.

**Workflow (point 5).** First the two model turns, in `docs/model/pipeline.md` and `docs/model/drain.md`. Then the publish check: the starting state in `feature_preflight.py:196-209`, one comparison script, and its run before the first commit at `skills/feature/SKILL.md:68` [`:40`] and `:137` [`:70`]. Then `pipeline.js:98` (`PREAMBLE`), `:981-984` and `:1015` (the brief sentence, after precondition 2), `:1083-1085` (the critic). `docs/model/pipeline.md:59`, `:65`: what `Bounded` and `Unpublished` cover, and the publish check.

**Harness pages.** `claude-code.md:28`: delete the sentence on pstack's adapter. `claude-code.md:30`: name no agent type (point 3). `codex.md:36`, `grok.md:29`, `antigravity.md:43`, `opencode.md:27`: a flagged skill is the owner's, followed when the owner names it. `docs/model/README.md:43`: `/pstack:statechart` becomes `pstack:statechart`.

**Records.** `docs/adr/0009-the-route-names-the-owners-next-keystroke.md`: a pointer line to this record. Editing an ADR is the owner's merge.

### Separate fixes, not part of this decision

- **F1. A verification skill has nowhere it may be written.** `tiers.md:113` says the formal model and its skill are guard paths, written on a branch and merged by the owner. pstack's verification skills write `.claude/skills/<area>/SKILL.md` (pstack 0.9.137, `create-formal-verification-skill/SKILL.md:40`). Three things refuse that today:
  - the `harness` group holds `.claude/skills/**` with `edit = "deny"` and comes before the `formal` group, and the first match wins (`skills/adopt/templates/guards/manifest.toml:51-65`, `:105-111`; `hooks/ratchet_guards/manifest.py:206-212`);
  - the fixed permission deny list holds `Edit(/.claude/skills/**)` (`scripts/settings_rules.py:119`, `:142`), and that list does not read the manifest (`:4`);
  - no regeneration takes a rule out of `deny` (`:23-24`), so every adopted repository keeps it.

  The fix needs three changes. The bound path goes under the `harness` group's `exclude` (`manifest.py:13`, `:170-172`). `.claude/skills/**` leaves the fixed deny list. The owner removes that rule by hand from each adopted repository's `.claude/settings.json`, itself a denied path. After that, a write to the bound path is asked of the owner on a branch (the instruction-file ask) and denied on the default branch with the way to a branch (`guard-path-default`, `verdict.py:57-62`; `hook.py:479-482`). Every other skill directory is then held by the hook's `harness` deny alone, without the settings layer.
- **F2. Init writes the pstack model sheet where pstack's hook does not read it.** Init writes `~/.claude/rules/pstack-models.md` and reads `<pstack>/docs/harness/claude-code.md` (`skills/init/SKILL.md:37`, `:85-86`). The port has no `docs/` folder, and its hook reads `${CLAUDE_CONFIG_DIR:-$HOME/.claude}/pstack-models.md` (pstack 0.9.137 `hooks/session-start.sh:19`).

### Seen in passing, for the guard's owner

- The upgrade writes `AGENTS.md` through a script, which the instruction-file ask does not see, since it judges only paths a command line names (rewritten `guards.md:101`). The edit reaches the default branch only through the owner's merge (`upgrade.py:12-14`). The shape check adds one more such edit.
- An instruction file is judged by its path from the checkout's root (`hooks/ratchet_guards/early.py:40-41`). So a write to user-level skills or rules outside the checkout is not one. pstack skills that write there fit only on the owner's word (point 1). Not checked further.

## Settled by the owner, 2026-11-15

1. **A guard event log is built with this decision.** The guard hook appends one line for each deny or ask, so that "past the guard" can be seen with no forge as well as in evaluated runs. The owner chose to build it now over deferring it. Cost accepted: a new write path in the hook, and its tests.
   - **Where it lives.** In the repository's git common directory, never in the work tree. An untracked file in the work tree would fail the preflight's `clean` check (`feature_preflight.py:594-599`), set the drain's `dirty` (`drain.py:300`), and show as uncommitted to `merge_ready.py record` (`:1410`). The guard's own diff counts untracked files (`hooks/ratchet_guards/diff.py:84-87`), so one denial would turn the next change into the owner's merge. A `.gitignore` line would need a migration in every adopted repository. The common directory never shows in `status`, holds one log for every worktree, and joins the built-in `.git/` guard paths (`manifest.py:104-111`) with no manifest change in adopted repositories.
   - **How it is written.** One `O_APPEND` write per line, since agents in several worktrees append at once. A failed write never changes the verdict.
