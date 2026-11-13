# ratchet

Ratchet is a plugin for coding agents that holds every change to the same standard before it merges. It installs a method in your repository: deterministic gates in three tiers that only tighten, paths the agent cannot edit without you, written records of each decision, and a four-role pipeline that builds a ticket while you are away. A change the gates can judge merges without you; one that touches a guarded path, goes through a one-way door or has an open finding waits for you.

It is for one developer, or a small team, who has agents write most of the code and wants the checks a software factory would give them without approving every pull request. It enforces concepts, not a stack: it ships the questions a change must answer and the gates that need no stack, and at adoption the agent chooses a current tool for every other question, on any language.

## What it costs

- **Four plugins.** Ratchet composes with `mattpocock-skills` (how the work is done: `tdd`, `codebase-design`, `domain-modeling`, `code-review`, `diagnosing-bugs`, `grilling`, `pr`, `research`), `pstack` (style and levers: `poteto-mode`, `architect`, `interrogate`, `arena`, `correct`) and `impeccable` (how anything a person sees is designed). In Claude Code they install with it.
- **Tools on the machine.** `git` 2.36 or later, `python3` 3.11 or later, `just` (or another task runner, rebuilt by hand), the `pre-commit` framework, and `gh` signed in when the repository is on GitHub. Each tool your stack's gates use is chosen and installed at adoption.
- **Agents.** A ticket built while you are away takes at most nine agents, and two more when it is judged again behind another merge. A drain of N tickets takes at most 11N - 2, and says the number before it starts.
- **Your keystrokes.** Up to two dialogs at init, one setup command, two dialogs at adoption, and one merge: the adoption's pull request changes guarded paths, so you merge it yourself. After that you write tickets, apply the agent-ready label, answer the questions that shape the product, and merge what the gates leave to you.

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

2. In the repository, empty or not, type `/ratchet:init`. It asks what the product is (and on an empty directory what it is built with), then writes `AGENTS.md` with `CLAUDE.md` linked to it, runs the pstack and impeccable setups, makes the first commit and, with your consent, the remote.
3. Type `/mattpocock-skills:setup-matt-pocock-skills`. It records the issue tracker and its labels.
4. Type `/ratchet:adopt`. It asks what kind of software this is and whether to accept the tools it proposes, then the design brief; it binds every gate against a walking skeleton, shows each gate fail on a planted fault, and opens the adoption pull request for you to merge. It installs the commit hooks itself; if your git configuration sets a `core.hooksPath` whose hooks do not run the repository's own, it names one command for you to run so that every commit runs tier 0.
5. From then on `/ratchet:next` prints the command to type next: a spec and tickets with `/mattpocock-skills:to-spec` and `to-tickets`, `/ratchet:conjecture <ticket>`, the agent-ready label, then `/ratchet:feature` to build every ready ticket.

## The eleven commitments

Every repository on the method keeps all eleven. `skills/method/SKILL.md` states them in full; each has a reference page beside it.

1. **One set of entry points.** The binding's table is the only list of gates, CI runs the same entry points, and a gate with nothing to run is red.
2. **Three tiers, two aggregates.** Tier 0 on every commit, tier 1 on every pull request, tier 2 on the default branch and nightly; `check` and `check-deep`.
3. **The ratchet.** Thresholds only rise, checked against the merge base; each gate's effective configuration is snapshotted; mutation testing gates on zero unexplained survivors.
4. **Guards.** The files that define, run or record a gate are guard paths, enforced by the harness, which the model cannot override.
5. **Disputes.** An agent that believes a gate is wrong writes a dispute and stops on that gate; the owner decides.
6. **Records.** A design brief, a glossary whose _Avoid_ lists are banned identifiers, one ADR per lasting decision with its refutation criterion, a brief per feature.
7. **The pipeline.** A feature is built by four roles with fixed sight lines: spec, implement, critic, review. A change the gates can judge merges without the owner; a guard path, a one-way door or an open finding waits for them.
8. **Verifiable shape.** Logic sits where every verifier reaches it; declared layers, absolute paths at 100%, hard limits.
9. **Speed.** Fast local runs and fast CI are asked from the first commit: the suite on every core, one pipeline that runs the same locally and in CI, the long gates sharded over a count the binding holds, everything cached, only what the change can affect run. Each tier has a ceiling in seconds that only falls.
10. **The merge rule.** A change is merged when no criticism of it is open.
11. **Operable from the first commit.** Telemetry, feature flags, security testing and load testing are asked of every change, each split across the tiers so the pull request pays only for the cheap part.

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

A type decides which questions apply. It never decides the tool. There is no Python preset and no TypeScript one: at adoption the agent recognises the stack, chooses a current tool for each question that applies, checks the choice against the tool's documentation, takes versions from the package registry, and asks the owner one question, accept these or name your own. A tool named in a plugin ages at the plugin's pace; one chosen at adoption is current that day, on any stack (ADR 0017). What earlier bindings taught is kept as rules any recipe must keep, in `skills/method/tiers.md`: above all, that a gate with nothing to run is red.

## The gates that ship

These need no stack, so the plugin ships them as code and proves them in its own `just check`: `scripts/bite.py` runs a gate against a synthetic violation, red with it and green without, `conformance.py --self-test` does the same for the conformance gate, and the tests cover the rest.

| Gate | Tier | The question it answers |
| --- | --- | --- |
| `scripts/commits.py` | 0 | does every commit subject follow the convention? |
| `scripts/markers.py` | 0 | did the count of TODO, FIXME, HACK and XXX markers rise? |
| `scripts/suppressions.py` | 0 | did any kind of suppression rise, or a focus marker appear? |
| `scripts/prose.py` | 0 | do the documents and comments keep to the glossary? |
| `scripts/snapshots.py` | 0 | does each gate's recorded configuration match the one in force? |
| `scripts/lowered.py` | 0 | was a threshold lowered, or a ceiling raised? |
| `scripts/flags.py` | 0 and 2 | is every feature flag declared once, owned and off when new; with `--expired`, is one past its removal date? `--temporary` lists the flags a gate that requests routes boots with on |
| `scripts/pins.py` | 0 | is every action pinned by commit, every image by digest, every workflow least-privileged? |
| `scripts/coverage_groups.py` | 1 | is each coverage group at its ratchet, and each absolute path at 100%? Fed by the suite's Cobertura or LCOV report |
| `scripts/mutation_survivors.py` | 1 and 2 | is every surviving mutant registered, and no entry stale? Fed by any mutation tool: one JSON line per mutant, or the reports of mutmut and Stryker as they are |
| `scripts/change_size.py` | 1 | is the change within its files and lines ceiling? |
| `ratchet_guards.diff` | 1 | which guard paths changed? |
| `scripts/settings_rules.py` | 1 | do the permission rules still mirror the manifest? |
| `scripts/conformance.py` | 1 | does the repository still carry the method: its records, its binding, its application types, its Speed section, its CI shape? `--self-test` shows it bites |
| `scripts/with_service.py` | 1 | does the deliverable start and answer? Boots a service by any command, waits until a URL answers, stops the whole process tree, retrying once on a new port when a service on a port it picked exits before it answers; with a command after `--` it holds the service up for that command |
| `scripts/load_smoke.py` | 1 | does a running service stay correct under seconds of concurrent requests? Judged on errors only, never on latency, and red while it requests nothing but the readiness URL |
| `scripts/telemetry_sink.py` | 2 | does the telemetry arrive? An OTLP/HTTP receiver that is green only when traces, metrics and logs arrived non-empty for the expected `service.name`; no collector to install |
| `scripts/project_bite.py` | 1 and 2 | does every gate the repository bound go red on its fault, and on an empty collection? Plants each fault of `quality/bites.toml` in a scratch copy; with `--changed`, only when the change touches a gate |
| `scripts/merge_ready.py` | after tier 1 | may this change reach the default branch without the owner? The merge judge: guard paths, one-way doors, new surface with no flag, disputes and the pipeline's verdict record, read from the commits |
| `scripts/merge.py` | with no forge | judge a branch, run `check` on the merge result, and merge it when both pass |
| `scripts/budget.py` | every tier | did a tier or gate finish under its ceiling in seconds? `--set` records the first ceiling, `--tier` also holds a CI job to its tier's ceiling, and `--over report` reports without blocking on a pull request |
| `scripts/parallel.py` | every tier | not a gate: runs a tier's gates at once through the repository's own runner, each gate's output whole, every red one named, so local wall clock is the slowest gate and not the sum |
| `scripts/changed.py` | every gate over the change | not a gate: hands such a gate the commit the change is compared with, and runs nothing on the adoption, the change whose base holds no binding |
| `scripts/plugin_pin.py` | every tier | is the plugin this run uses the version and commit the binding pins, the one CI checks out? Red when it is older or cannot be ordered, with both ways out; green when it is newer, saying so on every run with the line to change |
| `scripts/upgrade.py` | after the plugin updates | not a gate: what this plugin version needs that the repository lacks, file by file with the pin first, each with the text to add and who may edit the file; it reads the conformance gate's rules and changes nothing |
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
| `skills/feature/` | `/ratchet:feature <ticket>`: the feature preflight on an agent-ready ticket, the branch, then the pipeline. With no ticket: every agent-ready ticket at once, each in a work tree of its own, merged one at a time, one report |
| `skills/conjecture/` | `/ratchet:conjecture <ticket>`: the method's two additions to a ticket, its conjecture and how it is operated, drafted from the conversation that produced it |
| `skills/next/` | `/ratchet:next`: the command the owner types next, decided from the checkout and the tracker |
| `scripts/route.py` | the route engine behind `/ratchet:next`, every hand-over and the session hook's last line |
| `scripts/drain.py` | the queue behind `/ratchet:feature` with no ticket: the plan, the work trees it makes and removes, the rebase, the local merge and the report |
| `scripts/questions.py` | one line per answer the owner gives a skill, and the proposal of a standing answer once the recommended option was taken three times running |
| `workflows/pipeline.js` | the four-role pipeline (preflight, spec, implement, critic, review) the feature skill runs, and the second review of a change brought up to date behind another merge |
| `scripts/feature_preflight.py` | what must be true of a ticket and the checkout before the pipeline's first agent spawns |
| `scripts/` | the gates that ship, in the table above |
| `hooks/` | the PreToolUse guard hook, the SessionStart and SubagentStart hook that puts the stance in every agent's context on an adopted checkout and names a bare one, and their `ratchet_guards` package (`guard-diff` lives here too) |
| `scripts/adapter.py` | wires the guard into Codex or OpenCode when the harness does not load the plugin's hooks |
| `adapters/` | the pi extension and the OpenCode plugin that translate a tool call into the guard's JSON |
| `docs/harness/` | one page per harness: install commands, how the guard is wired, the names mapped, what was verified |
| `docs/adr/` | the plugin's own decisions; 0004 is the harness decision, made for five and since extended to Antigravity, 0017 the one that removed the presets |
| `docs/research/` | the sources behind the stance |

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
