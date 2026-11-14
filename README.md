# Ratchet

[![ci](https://github.com/Simon-GitHub-Code/ratchet/actions/workflows/ci.yml/badge.svg)](https://github.com/Simon-GitHub-Code/ratchet/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

When agents write most of your code, approving every pull request is either your bottleneck or a rubber stamp. Ratchet moves that judgement into deterministic checks that only ever tighten.

A change the checks can judge merges without you. Your attention goes to what the product should do, and to the few changes that need you.

Ratchet is a plugin for coding agents. It is built for Claude Code, and its checks and its guard are also wired for Grok, Codex, Antigravity, pi and OpenCode. It names no language and no tool: it ships the questions a change must answer, and the agent chooses a current tool for each one when your project first needs it.

> **Status: pre-1.0.** One person's working method, shared in the open and changing quickly. Read [Limits](#limits) before you rely on it, and the [changelog](CHANGELOG.md) for what each version changed.

## Contents

- [The thesis](#the-thesis)
- [Who it is for](#who-it-is-for)
- [Quick start](#quick-start)
- [Day to day](#day-to-day)
- [What it costs](#what-it-costs)
- [How it works](#how-it-works)
- [Commands](#commands)
- [Harness support](#harness-support)
- [Upgrading](#upgrading)
- [Limits](#limits)
- [Documentation](#documentation)
- [Developing the plugin](#developing-the-plugin)
- [Contributing](#contributing)
- [Credits](#credits)
- [License](#license)

## The thesis

Agents write code faster than a person can review it. That leaves two bad options. Read every pull request, and you are the bottleneck. Approve without reading, and the approval controls nothing.

Ratchet takes a third. Each thing you would look for in a review becomes a check a machine runs: a script that passes or fails the same way every time. The checks decide, and you are kept for the decisions a machine cannot make. Four ideas hold it together.

1. **A change merges when no criticism of it is open.** Every change is a guess that could be wrong. A failing check is a criticism of it. So is a review finding, and so is a question nobody has answered. A change merges once each one has been answered, by fixing the work or by a written reason why the criticism misses. A green run means the change has not been shown wrong yet, never that it was proved right, and the agents are told to report it that way. The rule comes from critical rationalism, the philosophy of Karl Popper and David Deutsch.
2. **Checks only tighten.** A recorded threshold only rises. A time limit only falls. The files that define a check are protected, so an agent cannot loosen the thing that judges it. That is the ratchet.
3. **A safeguard arrives with the first thing it guards.** The first commit runs only the checks that cost seconds. A smoke test arrives with your first route, a timeout with your first outbound call, a restore drill once you hold data you could not recreate. The change that brings the first of anything is never blocked for it. Ratchet must not stop you in the middle of an idea.
4. **You are asked what only you can decide.** What the product should do and what happens to your users' things are yours. So is how it is built, because the foundations are the hardest choice to take back: the agent proposes two or three whole designs, with what each would cost to change later, and you pick one. Everything a later change can undo is decided for you and listed under **Assumed**, with what changing it would cost. You change any line by saying so.

## Who it is for

One person, or a small team, who has agents write most of the code and wants the checks a software team would have without approving every pull request. Hobby projects and solo founders come first. The first hour is designed for, and [measured against](evals/first-hour/README.md), someone who is not an engineer: every step ends by naming the one command to type next.

It is probably not for you if you want to review every change yourself, if you want a fixed toolchain handed to you, or if your team already has a merge process it trusts.

## Quick start

### 1. Check the machine

- `git` 2.36 or later
- `python3` 3.11 or later
- [`just`](https://github.com/casey/just), the command runner the templates are written for
- the [`pre-commit`](https://pre-commit.com) framework
- `gh`, signed in, when the repository is on GitHub

### 2. Install, in Claude Code

```bash
claude plugin marketplace add mattpocock/skills
claude plugin marketplace add michael-denyer/pstack-claude
claude plugin marketplace add pbakaus/impeccable
claude plugin marketplace add Simon-GitHub-Code/ratchet
claude plugin install ratchet@ratchet
```

The last line installs all four plugins: Ratchet declares the other three as dependencies. For another harness, see [Harness support](#harness-support).

### 3. Type four commands

Run these in your repository. It can be empty.

| You type | What happens |
| --- | --- |
| `/ratchet:init` | Asks four or five questions about what you are making and who it is for. Writes `AGENTS.md`, makes the first commit and, if you say so, a private copy on GitHub. |
| `/mattpocock-skills:setup-matt-pocock-skills` | Records where your tickets live and the labels they use. |
| `/ratchet:adopt` | Asks what you want to be able to do first, what can wait, and how it should be built: two or three whole designs, each with the tools that would check it. Then it sets up the first checks, shows each one fail on a planted mistake, and opens one pull request for you to merge. About 25 minutes on an empty repository. |
| `/ratchet:next` | Prints the command to type next, why, and the step after it. |

No sample app is built. After adoption, the next thing written in an empty repository is your idea.

### 4. Follow the route

You never need to remember the order. `/ratchet:next` reads the repository and your tickets and prints three lines:

```text
next: /ratchet:init
why: AGENTS.md does not name the method
then: /mattpocock-skills:setup-matt-pocock-skills
```

Every Ratchet command ends by naming the next one, and so does the start of every session.

## Day to day

A feature goes from an idea to a merge in five steps. You type the first four, and each one is short.

1. **Describe it.** `/mattpocock-skills:to-spec` turns a conversation into a spec.
2. **State the bet.** `/ratchet:conjecture <spec>` writes down how it will be built, what would show that approach wrong, and what each thing you said became. It asks you, in one message, every product choice your words did not settle.
3. **Split it.** `/mattpocock-skills:to-tickets` cuts the spec into tickets and applies the agent-ready label. The label is your agreement that a ticket may be built, so applying it asks you once.
4. **Build it.** `/ratchet:feature` builds every agent-ready ticket while you are away, each in its own work tree, and merges them one at a time. `/ratchet:feature <ticket>` builds one. If you would sooner stay at the keyboard, type `/pstack:poteto-mode <ticket>` instead.
5. **Read the report.** It says what merged, and what waits for you and why. Each change lists what was assumed on your behalf.

### What merges without you

A change merges itself when all of these hold:

- every check is green,
- an independent review approved it with no finding left open,
- it changes none of the protected files.

### What waits for you

- A change to a protected file: a check, its settings, a threshold, the dependency list, a decision record, the instructions agents read.
- A disagreement an agent has written down about a check it believes is wrong.
- A review that did not end in a clean approval.
- Once other people can reach your project: a change that cannot be taken back, such as a data migration, and new behaviour with no switch to turn it off.

### What you are asked

Questions about the product: what it should do, for whom, and what happens to a user's things. And one about its foundations: how it should be built, asked once at adoption.

Ratchet also asks you to say when the project's situation changes, because a machine cannot see it reliably. There are three such events: other people can reach it, it holds data you could not recreate, and people rely on it. Each one switches on the safeguards that only then have something to protect.

### As the project grows

`/ratchet:next` names two more commands when they are due, each before the next feature.

- `/ratchet:safeguard`, when a change has brought the first of a kind of code or you have declared one of the three events. An agent chooses the tool, sets up the check and shows it fail on a planted mistake.
- `/ratchet:architecture`, when the project's shape has changed: the first stored data, the first screen, the first call to an outside system. An agent reads the way it is built against the code. It records that the design still fits, or asks you once to choose between two or three ways to change it.

Carrying on is free in both cases until a change adds another of the same kind.

## What it costs

- **Four plugins.** Ratchet says what must be true before a merge. [`mattpocock-skills`](https://github.com/mattpocock/skills) says how the work is done, [`pstack`](https://github.com/michael-denyer/pstack-claude) in what style, and [`impeccable`](https://github.com/pbakaus/impeccable) how anything a person sees is designed.
- **Time at adoption.** About 25 minutes on an empty repository, an estimate. A repository with code takes longer, because the safeguards its code already needs are set up at once.
- **Agents.** A ticket built while you are away takes five agents, or seven when its plan is sent back once. After you declare that people rely on the project, a critic and its fix loop join and the most is twelve. A ticket judged again behind another merge takes two more. A run over several tickets states its largest possible count before it starts.
- **Your keystrokes.** Four or five questions at init, one setup command, up to six questions at adoption (nine on a repository that already has code), and one merge of the adoption. After that: tickets, the agent-ready label, product questions, and the merges the checks leave to you.

## How it works

This section uses the method's own words. Each links to the page that defines it; the founding document is [`skills/method/SKILL.md`](skills/method/SKILL.md).

**Gates and tiers.** A *gate* is one deterministic check that answers one question. It is red or green. Tier 0 runs on every commit, tier 1 on every pull request, tier 2 on the default branch and nightly. `check` runs tiers 0 and 1; `check-deep` adds tier 2. CI runs the same entry points you run locally. [Tiers](skills/method/tiers.md)

**The binding.** `docs/process/binding.md` is the one list of gates in a repository: a row per question, with the tool that answers it. A question with no tool yet says `later:` and names what will trigger it. A row is never deleted. [Tiers](skills/method/tiers.md)

**The ratchet.** Thresholds live in `quality/thresholds.toml` and only rise, checked against the merge base. Each tier has a ceiling in seconds that only falls. Mutation testing gates on zero unexplained survivors. [The ratchet](skills/method/ratchet.md)

**Every gate is shown to bite.** A gate nobody has seen fail is a gate nobody can trust. `quality/bites.toml` holds a planted fault for each gate, and a gate must go red with its fault and green without it. A gate that has been asked is red when it finds nothing to run. [Tiers](skills/method/tiers.md)

**Guards.** The files that define, run or record a gate are *guard paths*, listed in `guards/manifest.toml`. A hook in the harness reads each file edit and shell command before it runs, and the model cannot override its verdict. An agent may change a guard path on a branch; the change then waits for you at the merge. The hook refuses a change that would land on the default branch, and asks you before a write to a CI workflow or an agent-instruction file, since those act the moment they are written. [Guards](skills/method/guards.md)

**The merge judge.** A shipped script reads the commits and prints `merge: agent` or `merge: owner` with every reason. It reads the rules from the default branch, so a change cannot loosen its own judge. [Guards](skills/method/guards.md)

**Disputes.** An agent that believes a gate is wrong, not the code, writes a dispute and stops work on that gate. You decide. A rule of the method is criticised in writing, never in action. [Guards](skills/method/guards.md)

**Records.** A design brief, a glossary whose _Avoid_ lists are banned identifiers, one decision record (ADR) per lasting decision with the observation that would show it wrong, and a brief per feature. A review judges a change against them. [Records](skills/method/records.md)

**The pipeline.** One agent grading its own work is confirmation, not criticism. A ticket is built by separate agents in fixed roles, each limited in what it may read: one writes the spec and its failing tests, one checks the spec against the ticket, one implements from the spec alone, one reviews without the implementer's notes. Once people rely on the project, a critic attacks the change before the review. [The pipeline](skills/method/pipeline.md)

**The architecture.** At adoption the agent drafts two or three whole systems: what it is written in, where it runs, where its data is kept, how its parts talk, and what changing it later costs. You pick one. The choice is a decision record with its alternatives, its reversal cost and the observation that would show it wrong. The binding declares the parts and what each may depend on, and a gate holds that direction. An arrival that changes the project's shape reopens it. [The architecture](skills/method/architecture.md)

**Triggers.** Every question says when it starts to be asked. `always` is the floor: format, lint, strict types, secrets, commit subjects and the method's own gates. `code:<kind>` fires at the first instance of a kind of code, such as an entry point, an outbound call or a stored format. `life:<event>` fires when you declare `deployed`, `irreplaceable-data` or `depended-on`. The change that brings a kind is green however many files it touches; the next one that adds another is red until the safeguard is set up, which `/ratchet:safeguard` does. [Tiers](skills/method/tiers.md#triggers)

**Application types.** A repository declares what kind of software it is: `service`, `web-ui`, `app`, `cli`, `job`, `library`, or several. The types decide which questions apply. A library is not asked whether it boots; accessibility is asked only of something with a screen. [Tiers](skills/method/tiers.md#application-types)

**No presets.** A type never decides the tool, and the plugin names no language, framework or tool anywhere. The tools that check the work are proposed with the architecture, looked up in their current documentation, with versions taken from the package registry. What a gate needs to know about your stack (which paths are tests, how dependencies are read, which comments silence a check) is written in your repository's binding. A tool named in a plugin ages at the plugin's pace; one chosen on the day it is needed is current that day.

**The stance.** A session hook puts a short text in every agent's context: state what would show a claim wrong before you make it, name the alternative explanation before a fix, report a green run by what it tried and what it did not. [The merge rule](skills/method/epistemology.md)

### What adoption adds to a repository

```text
AGENTS.md                    what the project is and its non-negotiables
GLOSSARY.md                  the project's words, and the words to avoid
docs/process/binding.md      every question, the tool that answers it, when it is asked,
                             and the parts of the architecture
docs/design-brief.md         what is being built first, and what is left out
docs/adr/                    one record per lasting decision
docs/disputes/               an agent's written case that a gate is wrong
docs/features/               a brief, a spec and a verdict per feature
guards/manifest.toml         the protected files, in groups, each with its reason
quality/thresholds.toml      the recorded thresholds and ceilings
quality/bites.toml           the planted fault that shows each gate fail
flags.toml                   the feature flags
justfile                     one entry point per tier and per gate
.claude/settings.json        permission rules, generated from the manifest
.pre-commit-config.yaml      tier 0 on every commit
.github/workflows/           the same entry points in CI, and the merge job
```

## Commands

Each command is yours to type. Claude Code refuses them to an agent, so the order of work stays in your hands.

| Command | What it does |
| --- | --- |
| `/ratchet:init` | Lays down the bare bones of a repository: `AGENTS.md`, the companion plugins' setup, the first commit, the remote. |
| `/ratchet:adopt` | Brings a repository onto the method: the binding, the floor, the guards, CI and the records. |
| `/ratchet:next` | Prints the command to type next, why, and the step after it. |
| `/ratchet:conjecture <ticket or spec>` | Writes the approach, what would refute it, the alternatives it killed, and what each thing you said became. |
| `/ratchet:feature [ticket]` | Builds one agent-ready ticket, or every one when no ticket is given. |
| `/ratchet:safeguard [event]` | Sets up the safeguards the project now owes. With `deployed`, `irreplaceable-data` or `depended-on`, declares that event first. |
| `/ratchet:architecture` | Reviews how the project is built when its shape changes, or lets you revisit the choice. |
| `/ratchet:fill` | After an upgrade, writes what the new version needs that an agent can write from the repository alone. Asks you nothing. |

Agents load one more skill, `ratchet:method`, by themselves. It is the method in full.

## Harness support

The feature pipeline runs on Claude Code's Workflow tool. The method, its gates and its guard are written to run in six harnesses, and each has a page with its install commands and what has been checked.

| Harness | Pipeline | What has been run |
| --- | --- | --- |
| [Claude Code](docs/harness/claude-code.md) | yes | The home harness. Everything is written for it. |
| [Grok](docs/harness/grok.md) | no | The guard denied a forced push and writes to locked files in a live session. |
| [pi](docs/harness/pi.md) | no | The skills load, and the guard blocked a forced push in a live session. |
| [Antigravity](docs/harness/antigravity.md) | no | The hooks install and answer as documented. It has no subagent hook, so subagents do not get the stance. |
| [Codex](docs/harness/codex.md) | no | Checked against its source. The plugin's hooks have not yet been run from a Codex install. |
| [OpenCode](docs/harness/opencode.md) | no | Written from its documentation. Not yet run against a live OpenCode. |

Outside Claude Code, a ticket is built by the roles [the pipeline page](skills/method/pipeline.md) describes, in any harness with subagents.

## Upgrading

When the plugin updates, every tier says so on its first line and `/ratchet:next` offers the upgrade. In the repository, run:

```bash
python3 ~/.claude/plugins/marketplaces/ratchet/scripts/upgrade.py --apply
```

It moves the repository to the plugin version in use in one change, and it adds and never removes. Anything that needs judgement it writes to `quality/owed.toml` instead of failing: each item is counted, none is red, and between upgrades the list only shrinks. Without `--apply` it prints the same plan and writes nothing. The upgrade touches guard paths, so merging it is your one step. `/ratchet:next` then names `/ratchet:fill`, where an agent writes what it can from the repository alone, and no feature starts before that is done. [CHANGELOG.md](CHANGELOG.md) says what each version changed.

## Limits

Ratchet asks agents to say where their work is weakest. The same applies here.

- **The guard is not a security boundary.** It checks an agent's ordinary writes and commands. A write by an archive tool, a path built at run time, or git moving the work tree goes past it. [ADR 0033](docs/adr/0033-an-agent-changes-a-guard-path-on-a-branch-and-the-owner-decides-at-the-merge.md) lists the ways.
- **Part of the merge decision rests on what an agent wrote.** The review's verdict is recorded by an agent, so an agent could forge it. Every other reason the merge judge gives is read from the diff and holds against a forged record. Tests are not guard paths, so a change that weakens its own tests meets only the count, coverage and mutation gates.
- **Green is not proof.** The checks catch what they were built to catch. A defect no gate asks about merges.
- **Stack-neutral is recent.** The plugin names no language or tool, and each stack fact lives in the repository's binding ([ADR 0042](docs/adr/0042-the-plugin-names-no-stack-tool-and-the-binding-holds-each-stack-fact.md)). It was first tried in two ecosystems. The first-hour evaluation has personas for five more, to show whether it holds outside them.
- **The measured effect is modest.** In the plugin's own evaluation the stance improved how agents reported their work (a pooled score gain of 0.16 over four small cases) and changed no task outcome: both arms fixed the same bugs. Runs with the plugin cost 30% to 90% more. [evals/README.md](evals/README.md) has the numbers and what limits them.
- **It spends agents.** Up to twelve per ticket once people rely on the project. That is the price of criticism from roles that cannot see each other's reasoning.
- **Most harnesses are less tested than Claude Code**, as the table above says.

## Documentation

| Read | For |
| --- | --- |
| [`skills/method/SKILL.md`](skills/method/SKILL.md) | The founding document: the words, the eleven commitments every repository keeps, how work runs. |
| [`skills/method/`](skills/method/) | One page per subject: the merge rule, tiers, the ratchet, guards and disputes, the pipeline, records, the route, the architecture, verifiable shape. |
| [`docs/gates.md`](docs/gates.md) | Every gate the plugin ships, its tier and the question it answers. |
| [`docs/layout.md`](docs/layout.md) | What each directory of the plugin holds. |
| [`docs/model/`](docs/model/README.md) | The plugin's state machines as explicit models, each with a page and tests that hold the code to it. |
| [`docs/harness/`](docs/harness/) | Install commands and guard wiring for each harness. |
| [`docs/adr/`](docs/adr/) | The plugin's own decisions, each with the observation that would show it wrong. |
| [`docs/research/critical-rationalism.md`](docs/research/critical-rationalism.md) | The sources behind the stance. |
| [`evals/`](evals/) | The measurements: the stance, and the first hour of a solo builder. |
| [`CHANGELOG.md`](CHANGELOG.md) | What each version changed, and how to upgrade from the one before. |

## Developing the plugin

You need `uv`, `just`, Node 22.13 or later and the `claude` CLI.

```bash
git clone https://github.com/Simon-GitHub-Code/ratchet
cd ratchet
just check
```

`just check` runs lint and strict types, the test suite under 100% line and branch coverage of the gate scripts and the guard, the suite again on Python 3.11, the conformance self-test, every shipped gate against a synthetic violation, a parse of the workflow and the adapters, and `claude plugin validate`. The method says gates are themselves gated; this is where that applies to the method.

To try a checkout as your installed plugin, add it as a local marketplace (`claude plugin marketplace add /path/to/ratchet`). It is read in place, so an edit applies at the next session or `/reload-plugins`.

The guard locks the installed plugin's own files, so an agent cannot edit a checkout that is also the enabled plugin. Disable it for the session (`claude plugin disable ratchet`, then `/reload-plugins`), or install the plugin from a separate clone.

A change to a commitment is a change to every repository on the method. It is recorded as an ADR under `docs/adr/`, the `version` in `.claude-plugin/plugin.json` rises, and each repository's binding says which version it runs.

## Contributing

Ratchet is not accepting contributions yet. It is pre-1.0 and its commitments still move between versions. Pull requests are likely to be closed, and issues may go unanswered. You are welcome to fork it under the licence below. This section will change when the method settles.

## Credits

Ratchet composes with three plugins and depends on their authors' work: [mattpocock-skills](https://github.com/mattpocock/skills), [pstack](https://github.com/michael-denyer/pstack-claude) in its port for Claude Code of the Cursor original, and [impeccable](https://github.com/pbakaus/impeccable).

The merge rule follows Karl Popper and David Deutsch, and its statement as a rule over open criticisms follows Dennis Hackethal's Veritula. The reading of tests as refutation follows Dave Farley. [The research notes](docs/research/critical-rationalism.md) cite each source.

## License

[MIT](LICENSE)
