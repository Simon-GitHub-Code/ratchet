# ratchet

Ratchet is a plugin for coding agents that holds every change to the same standard before it merges. It installs a method in your repository: deterministic gates in three tiers that only tighten, guard paths whose changes wait for you at the merge, written records of each decision, and a pipeline of agents in fixed roles that builds a ticket while you are away. A change the gates can judge merges without you; one that touches a guarded path or has an open finding waits for you, and once others can reach the project so does one that goes through a one-way door.

It is for one developer, or a small team, who has agents write most of the code and wants the checks a software factory would give them without approving every pull request. Hobby projects and solo founders come first. It enforces concepts, not a stack: it ships the questions a change must answer and the gates that need no stack, and the agent chooses a current tool for every other question when the project first needs it, on any language.

It starts small and never stops you mid-idea. On the first commit only the floor runs: format, lint, strict types, secrets, commit subjects and the method's own gates. Every other safeguard arrives with the first thing it guards: a span and a smoke test with the first route, a timeout with the first outbound call, a restore drill once the project holds data you could not recreate. The change that brings the first of anything is never red for it, however many files it touches; the next step is to bind what it now owes, and only a later change that adds another before that is red.

## What it costs

- **Four plugins.** Ratchet composes with `mattpocock-skills` (how the work is done: `tdd`, `codebase-design`, `domain-modeling`, `code-review`, `diagnosing-bugs`, `grilling`, `pr`, `research`), `pstack` (style and levers: `poteto-mode`, `architect`, `interrogate`, `arena`, `correct`) and `impeccable` (how anything a person sees is designed). In Claude Code they install with it.
- **Tools on the machine.** `git` 2.36 or later, `python3` 3.11 or later, `just` (or another task runner, rebuilt by hand), the `pre-commit` framework, and `gh` signed in when the repository is on GitHub. Each tool your stack's gates use is chosen and installed at adoption.
- **Time at adoption.** Adopting an empty repository took 19.5 minutes in a dogfood run on 2026-11-13. Adoption now also rehearses the first slice, which took 8.5 minutes on a heavily loaded machine and, as an estimate, would take 3 to 5 minutes on an idle one. A repository with code takes longer, since it binds the safeguards its code already needs.
- **Agents.** A ticket built while you are away takes five agents until you declare that people depend on the project, or seven when the check of its plan against the ticket sends the plan back once, and then at most twelve, since a critic and its fix loop join; two more when it is judged again behind another merge. A drain says the number before it starts.
- **Your keystrokes.** Four or five questions about your product at init, one setup command, up to four more at adoption (up to seven on a repository that already has code, which is also asked whether others reach it and rely on it), and one merge. Nothing else is asked: each choice a default answers is made for you and listed at the end of the step under **Assumed**, where you change any line by saying so. The adoption's pull request changes guarded paths, so you merge it yourself. After that you write tickets, apply the agent-ready label, answer the questions that shape the product, declare the project's life events when they happen (deployed, holding irreplaceable data, depended on), and merge what the gates leave to you.
- **Safeguards as you go.** Each one is bound by an agent, in a change of its own, when the project first needs it (`/ratchet:safeguard`, which `/ratchet:next` names). Before you deploy, a one-way door and a route with no flag merge without you.

The feature pipeline runs on Claude Code's Workflow tool. The method, its gates and its guard also run in Grok, Codex, Antigravity, pi and OpenCode, each with a page under `docs/harness/`.

## First run

1. Install, in Claude Code:

   ```bash
   claude plugin marketplace add mattpocock/skills
   claude plugin marketplace add michael-denyer/pstack-claude
   claude plugin marketplace add pbakaus/impeccable
   claude plugin marketplace add Simon-GitHub-Code/ratchet
   claude plugin install ratchet@ratchet
   ```

2. In the repository, empty or not, type `/ratchet:init`. It asks what the product is, then writes `AGENTS.md` with `CLAUDE.md` linked to it, runs the pstack and impeccable setups, makes the first commit and, with your consent, the remote.
3. Type `/mattpocock-skills:setup-matt-pocock-skills`. It records the issue tracker and its labels.
4. Type `/ratchet:adopt`. It asks what kind of software this is and whether to accept the tools it proposes, then the design brief; it binds the floor, marks every other row with the trigger that will ask it, shows each bound gate fail on a planted fault, and opens the adoption pull request for you to merge. No walking skeleton is built: on an empty repository the next thing written is your idea. It installs the commit hooks itself; if your git configuration sets a `core.hooksPath` whose hooks do not run the repository's own, it names one command for you to run so that every commit runs tier 0.
5. From then on `/ratchet:next` prints the command to type next: a spec with `/mattpocock-skills:to-spec`, `/ratchet:conjecture <spec>` (its tickets inherit the conjecture and operability), `to-tickets`, `/ratchet:conjecture <ticket>` where a ticket needs its own, the agent-ready label, then `/ratchet:feature` to build every ready ticket. When a change brings the first of a kind of code, or you declare a life event, it names `/ratchet:safeguard` first, with the step it would otherwise name beside it: carrying on is free until a change adds another of that kind. When the tree shows a sign of a life event you have not declared, such as a deploy file, it says so once: type `/ratchet:safeguard deployed` if it is true, or say not yet and it waits for a new sign.

## The eleven commitments

Every repository on the method keeps all eleven. `skills/method/SKILL.md` states them in full; each has a reference page beside it.

1. **One set of entry points.** The binding's table is the only list of gates, each row with the trigger that asks it, CI runs the same entry points, and a gate once asked is red with nothing to run.
2. **Three tiers, two aggregates.** Tier 0 on every commit, tier 1 on every pull request, tier 2 on the default branch and nightly; `check` and `check-deep`.
3. **The ratchet.** Thresholds only rise, checked against the merge base; each gate config the guard manifest lists is snapshotted, and one it does not list is neither guarded nor recorded; mutation testing gates on zero unexplained survivors.
4. **Guards.** The files that define, run or record a gate are guard paths, checked by a hook in the harness whose verdict the model cannot override. Edit-time control stays for exactly the files whose effect comes before the merge; everything else is judged at merge. An agent changes a guard path on a branch without a prompt, and the change waits for you at the merge. The hook refuses each such change it reads that would land on the default branch, and each change it reads to the files you edit by hand: settings, hooks, git config and the installed plugins. Two kinds of file act the moment they are written, CI workflows and agent-instruction files (`CLAUDE.md`, `AGENTS.md` and the rest, at any depth), so the hook asks you before each write of one it reads, in one plain sentence. The hook checks an agent's ordinary writes. It is not a boundary against an agent that sets out to get round it: a write by an archive tool, a path built at run time, or git moving the work tree goes past it (ADR 0033 lists them).
5. **Disputes.** An agent that believes a gate is wrong writes a dispute and stops on that gate; the owner decides.
6. **Records.** A design brief, a glossary whose _Avoid_ lists are banned identifiers, one ADR per lasting decision with its refutation criterion, a brief per feature.
7. **The pipeline.** A feature is built by roles with fixed sight lines: spec, a check of the spec against the ticket, implement and review, and a critic once people depend on the project. A promise of the ticket the spec still leaves out is built as the ticket says it, and the pull request lists it with the other assumptions for the owner to overrule; the check asks the owner nothing. A change the gates can judge merges without the owner; a guard path or an open finding waits for them, and once the project is deployed so do a one-way door and new surface with no flag.
8. **Verifiable shape.** Logic sits where every verifier reaches it; an architecture the owner chose from whole options, recorded with its alternatives and reversal cost, its parts declared in the binding and held by a gate; absolute paths at 100%, hard limits.
9. **Speed.** Fast local runs and fast CI are asked from the first commit: the suite on every core, one pipeline that runs the same locally and in CI, the long gates sharded over a count the binding holds, everything cached, only what the change can affect run. Each tier has a ceiling in seconds that only falls.
10. **The merge rule.** A change is merged when no criticism of it is open.
11. **Operable by the time it matters.** A safeguard arrives with the first thing it guards: telemetry, flags, security, load and reliability questions are each asked at the first instance of the code they guard, or once you declare the life event that gives them something to judge. The first one is free.

## Application types

What kind of software a repository is decides which questions the method asks of it. A binding declares one or more of six types on one line, `- Application types: service, web-ui`:

| Type | What it is |
| --- | --- |
| `service` | answers requests over a network: an HTTP or gRPC API, a web backend |
| `web-ui` | runs in a browser |
| `app` | an installed client with a user interface: mobile, desktop |
| `cli` | a command a person or a script runs |
| `job` | runs unattended on a schedule, a queue or a stream: a worker, a batch, a pipeline |
| `library` | code other code imports; nothing to boot |

Every question applies to every type unless `skills/method/application-types.toml` narrows it: contract drift is asked only of a service, accessibility only of a web interface or an app, and a library is not asked whether it boots or holds under load. A row that applies names a tool, or `none` with a reason and the ADR that accepts the gap. A row that does not apply says `none: not a service` and needs no ADR. No row is dropped, and the conformance gate checks all of it.

## No presets

A type decides which questions apply. It never decides the tool. There is no preset for any language: at adoption the agent proposes two or three whole systems with the current tools that would check each, checked against their documentation, and the owner picks one in the same round as the product questions (ADR 0049); when a later row is owed, the agent takes the tool the architecture record names or chooses a current one, takes versions from the package registry, and lists each choice at the end of the step, where the owner overturns any of them in words (ADR 0034). A tool named in a plugin ages at the plugin's pace; one chosen when it is needed is current that day, on any stack (ADR 0017). What earlier bindings taught is kept as rules any recipe must keep, in `skills/method/tiers.md`: above all, that a gate once asked is red with nothing to run.

## When each question is asked

Every question carries a trigger in `skills/method/application-types.toml`. `always` is the floor, asked from the first commit. `code:<kind>` is asked at the first instance of a kind of code (an entry point, an outbound call, a listener, a configuration read, a stored format, a user interface), which the binding's Triggers table marks for this stack and `scripts/triggers.py` counts. `life:<event>` is asked once you declare `deployed`, `irreplaceable-data` or `depended-on` on the binding's `- Life events:` line. Until then a row says `later: <trigger>` and judges nothing. `skills/method/tiers.md` lists every question with its trigger and why it waits there (ADR 0028).

## The gates that ship

These need no stack, so the plugin ships them as code and proves them in its own `just check`: `scripts/bite.py` runs a gate against a synthetic violation, red with it and green without, `conformance.py --self-test` does the same for the conformance gate, and the tests cover the rest.

| Gate | Tier | The question it answers |
| --- | --- | --- |
| `scripts/commits.py` | 0 | does every commit subject follow the convention? |
| `scripts/markers.py` | 0 | did the count of TODO, FIXME, HACK and XXX markers rise? |
| `scripts/suppressions.py` | 0 | did any kind of suppression the binding lists rise, or a marker it forbids appear? |
| `scripts/prose.py` | 0 | do the documents and comments keep to the glossary? |
| `scripts/snapshots.py` | 0 | does each gate config the guard manifest lists match its recorded copy? |
| `scripts/lowered.py` | 0 | was a threshold lowered, or a ceiling raised? |
| `scripts/flags.py` | 0 and 2 | is every feature flag declared once, owned and off when new; with `--expired`, is one past its removal date? `--temporary` lists the flags a gate that requests routes boots with on |
| `scripts/pins.py` | 0 | is every action pinned by commit, every image by digest, every workflow least-privileged? |
| `scripts/coverage_groups.py` | 1 | is each coverage group at its ratchet, and each absolute path at 100%? Fed by the suite's Cobertura or LCOV report |
| `scripts/mutation_survivors.py` | 1 and 2 | is every surviving mutant registered, and no entry stale? Fed by any mutation tool through one neutral format: one JSON line per mutant, which the project's mutation recipe writes from its tool's report (ADR 0050) |
| `scripts/change_size.py` | 1 | is the change within its product files and lines ceiling, and its test lines within their own (ADR 0045)? (which paths are tests is the binding's `## Test paths`; records, the lockfiles the binding's Dependencies section names and their snapshots are not counted; a moved file counts by its changed lines) |
| `ratchet_guards.diff` | 1 | which guard paths changed? |
| `scripts/settings_rules.py` | 1 | do the permission rules hold the fixed denies, and ask nothing? `--write` (the `settings` entry point) writes them, and refuses under a plugin other than the one CI runs, whose check would reject what it wrote |
| `scripts/conformance.py` | 1 | does the repository still carry the method: its records, its binding, its application types, its Speed section, its CI shape? `--self-test` shows it bites |
| `scripts/with_service.py` | 1 | does the deliverable start and answer? Boots a service by any command, waits until a URL answers, stops the whole process tree, retrying once on a new port when a service on a port it picked exits before it answers; with a command after `--` it holds the service up for that command. `--refuses NAME` asks whether it exits non-zero, naming the setting, when `NAME` is missing; `--graceful SECONDS` whether, signalled mid-request, it finishes the request and exits in time |
| `scripts/triggers.py` | 1 | did the change add another instance of a kind whose safeguards are still owed? Counts each kind's instances at the merge base and at head by the binding's Triggers table; the change that brings a kind is green, however many files it touches, and names the rows it made owed |
| `scripts/load_smoke.py` | 1 | does a running service stay correct under seconds of concurrent requests? Judged on errors only, never on latency, and red while it requests nothing but the readiness URL |
| `scripts/with_service.py --graceful` and `--refuses` | 1 | does a service stop cleanly when signalled under load, and refuse to start without a required setting, naming it? |
| `scripts/telemetry_sink.py` | 2 | does the telemetry arrive? An OTLP/HTTP receiver that is green only when traces, metrics and logs arrived non-empty for the expected `service.name`; no collector to install |
| `scripts/project_bite.py` | 1 and 2 | does every gate the repository bound go red on its fault, and on an empty collection? Plants each fault of `quality/bites.toml` in a scratch copy; with `--changed`, only when the change touches a gate |
| `scripts/acceptance.py` | before the merge judge | not a gate: the harness an acceptance tester stands behind, for a `service` or a `web-ui`. `up` boots the work tree as it is, committed or not, behind a proxy that logs every request, gives the tester one address and files the evidence under the tree it served; `down` stops it all, seals the log and prints the seal's digest; `check` holds the tester's record to that log, so a pass cites a request the harness saw; `replay` runs the record's kept checks on a fresh boot and, with `--control`, against a product that does nothing, where each must fail; `summary` writes what the owner reads. It holds against a careless tester, not one that can run programs: the tester role it is built for may write only in its own directory and touch the product only through a harness command. That role, and the command it needs, which performs one request or one browser action through the proxy and prints the id the proxy logged, are the next unit's (ADR 0035) |
| `scripts/merge_ready.py` | after tier 1 | may this change reach the default branch without the owner? The merge judge: guard paths, disputes and the pipeline's verdict record always (none asked of records alone or of the adoption), one-way doors and new surface with no flag once the binding declares the life events that make them matter, read from the commits |
| `scripts/merge.py` | with no forge | judge a branch, run `check` on the merge result (`check-deep` for the adoption), and merge it when both pass; `--records` lands records alone from the default branch through a branch of their own |
| `scripts/budget.py` | every tier | did a tier or gate finish under its ceiling in seconds? `--set` records the first ceiling, `--tier` also holds a CI job to its tier's ceiling, and `--over report` reports without blocking on a pull request |
| `scripts/parallel.py` | every tier | not a gate: runs a tier's gates at once through the repository's own runner, each gate's output whole, every red one named, so local wall clock is the slowest gate and not the sum |
| `scripts/changed.py` | every gate over the change | not a gate: hands such a gate the commit the change is compared with, and runs nothing on the adoption, the change whose base holds no binding |
| `scripts/plugin_pin.py` | every tier | is the plugin this run uses the version and commit the binding pins, the one CI checks out? Red when it is older or cannot be ordered, with both ways out; green when it is newer, saying so on every run with the line to change |
| `scripts/upgrade.py` | after the plugin updates | not a gate: what this plugin version needs that the repository lacks, each item marked `auto`, `agent` or `owner`; it reads the conformance gate's rules and plans on a copy of the repository. `--apply` writes the pin, the workflow refs, the permission rules and every other `auto` item in one run, writes down the rest in `quality/owed.toml` as owed, and lists it |
| `scripts/owed.py` | after an upgrade | not a gate: how many things the project owes since an upgrade, in one plain line for `/ratchet:next`. An owed finding is counted and not red; `lowered.py` is red when a change adds to the list without moving the pin, and the merge judge lets a change that only drops entries through (ADR 0037) |
| `scripts/hooks_installed.py` | before a feature | would a commit here run tier 0? Red with the repair when not: the `hooks-install` entry point (`scripts/hooks_install.py`) where the repository's own hooks are missing, the owner's one command where git config must change; asked by the preflight, the route and `just ratchet`, not by CI |

Every other gate is the repository's: a tool it chose, behind an entry point the binding names. The plugin cannot vouch for a tool it did not choose, so the repository does: `quality/bites.toml` holds a fault for each such gate, `just bite-all` plants each one and requires the gate red, and the conformance gate is red on a bound gate with no fault (ADR 0020).

## What is in the plugin

| Path | What it is |
| --- | --- |
| `skills/method/SKILL.md` | the founding document: the words, the eleven commitments, how work runs |
| `skills/method/*.md` | the reference behind each commitment: the merge rule, tiers as questions, the ratchet, guards and disputes, the pipeline, records, verifiable shape |
| `skills/method/application-types.toml` | the six application types and the questions each is asked; `scripts/application_types.py <type>...` prints the rows a set of types leaves out |
| `skills/init/` | `/ratchet:init`: the bare bones for any repository: `git init` where needed, an `AGENTS.md` naming the four plugins with `CLAUDE.md` linked to it, the pstack and impeccable one-time setups, the first commit and the remote; the mattpocock-skills setup is the owner's command after it, and a rerun of init creates the triage labels that setup recorded |
| `skills/adopt/` | `/ratchet:adopt`: the steps and the stack-neutral templates that bind the method to a repository's runner, forge and stack |
| `skills/architecture/` | `/ratchet:architecture`: the architecture an adopted repository owes, or a revisit of it: whole options from the code, one round of questions, the decision record and the binding's `## Architecture` section |
| `skills/feature/` | `/ratchet:feature <ticket>`: the feature preflight on an agent-ready ticket, the branch, then the pipeline. With no ticket: every agent-ready ticket at once, each in a work tree of its own, merged one at a time, one report |
| `skills/safeguard/` | `/ratchet:safeguard`: binds a row the project now owes. An agent chooses the tool, writes the recipe and the fault, and binds the row in a change of its own |
| `skills/conjecture/` | `/ratchet:conjecture <ticket or spec>`: the method's two additions to a ticket (or to the spec it came from), its conjecture and how it is operated, drafted from the conversation that produced it |
| `skills/next/` | `/ratchet:next`: the command the owner types next, decided from the checkout and the tracker |
| `scripts/route.py` | the route engine behind `/ratchet:next`, every hand-over and the session hook's last line |
| `scripts/drain.py` | the queue behind `/ratchet:feature` with no ticket: the plan, the work trees it makes and removes, the rebase, the local merge and the report |
| `scripts/questions.py` | one line per answer the owner gives a skill, and the proposal of a standing answer once the recommended option was taken three times running |
| `workflows/pipeline.js` | the feature pipeline (preflight, spec, ticket check, implement, review, and the critic once the project is depended on) the feature skill runs, and the second review of a change brought up to date behind another merge |
| `scripts/feature_preflight.py` | what must be true of a ticket and the checkout before the pipeline's first agent spawns |
| `scripts/` | the gates that ship, in the table above |
| `hooks/` | the PreToolUse guard hook, the SessionStart and SubagentStart hook that puts the stance in every agent's context on an adopted checkout and names a bare one, and their `ratchet_guards` package (`guard-diff` lives here too) |
| `scripts/adapter.py` | wires the guard into Codex or OpenCode when the harness does not load the plugin's hooks |
| `adapters/` | the pi extension and the OpenCode plugin that translate a tool call into the guard's JSON |
| `docs/harness/` | one page per harness: install commands, how the guard is wired, the names mapped, what was verified |
| `docs/adr/` | the plugin's own decisions; 0004 is the harness decision, made for five and since extended to Antigravity, 0017 the one that removed the presets |
| `docs/research/` | the sources behind the stance |

## Upgrade

When the plugin updates, every tier says so on its first line, and the route offers the upgrade. In the repository, run:

```bash
python3 ~/.claude/plugins/marketplaces/ratchet/scripts/upgrade.py --apply
```

It moves the binding's pin, every workflow `ref` and the permission rules to the plugin in use in one run, and adds what the plugin's templates hold and the repository lacks: entry points, the method's own recipes, records and workflows, the Gates rows of the plugin's own gates, run in their tier and in a tier 1 job that lists its gates, a row whose trigger is known not to have fired as `later:`, and the values adoption writes on day one. It adds and never removes: a workflow or recipe that holds the project's own lines is left as it is, and a permission rule the plugin did not write is kept. What is left, an edit that needs judgement or a call that is yours, it writes down in `quality/owed.toml`: each is counted and none of them is red, and the report names the checks that do not run until they are fixed. Nothing works through the list by itself; an agent does when asked. It makes each fix on a branch with no prompt (ADR 0033), one change can carry them all, and you decide at that one merge; a change that only drops entries from the list passes the merge judge without you (ADR 0037). A call that is yours keeps a default that loosens nothing until you say otherwise, and the report ends with each default in plain words, so the upgrade asks you nothing. Between upgrades the list only shrinks: a change that adds to it without moving the pin is red, and in the change that moves the pin an entry for a finding the merge base was not red on is red too (ADR 0037). Running it again is always safe. Without `--apply` it prints the same plan and writes nothing. The upgrade is then one change. It touches guard paths, so the merge judge leaves it to you, and the merge is your one step (ADR 0029).

## Install notes

The marketplace entry declares `mattpocock-skills`, `pstack` and `impeccable` as dependencies, so the last line installs all four once their marketplaces are known. pstack comes from `michael-denyer/pstack-claude`, the port with Cursor's tool names already translated for Claude Code and the other harnesses, not from `cursor/plugins`, the Cursor original. Grok, Codex, Antigravity, pi and OpenCode each have a page under `docs/harness/` with their commands; none has a dependency field, so `/ratchet:init` installs what is missing with the owner's consent.

In Antigravity's CLI:

```bash
agy plugin install https://github.com/Simon-GitHub-Code/ratchet
agy plugin list
```

`agy plugin list` should show `ratchet` enabled. Antigravity has no `SubagentStart` event, so that hook is skipped and subagents do not get the stance; the guard and the `SessionStart` hook run. Antigravity treats a hook answer without a decision as a deny and `allow` as an auto-approve, so when no rule fires the guard answers `ask`, Antigravity's normal permission prompt. See `docs/harness/antigravity.md`.

Updates arrive when `version` in `.claude-plugin/plugin.json` rises. To develop the plugin, add the checkout as a local marketplace instead (`claude plugin marketplace add /path/to/ratchet`): it is read in place, so edits apply at the next session or `/reload-plugins`.

## Develop the plugin

The guard hook locks the installed plugin's root with or without a manifest, and a directory marketplace is read in place, so an agent cannot edit this checkout while the plugin is enabled. Disable it for the session (`claude plugin disable ratchet`, then `/reload-plugins`) and re-enable it afterwards, or install the plugin from a separate clone.

`just check` here runs the hook's tests, lint and types, the conformance self-test, every shipped gate against its synthetic violation, a parse of the workflow and `claude plugin validate`. The method says gates are themselves gated; this is where that applies to the method.

## Change the method

A change to a commitment is a change to every repository on the method. Record it as an ADR here (`docs/adr/`), raise `version`, and let each repository's binding say which version it runs.
