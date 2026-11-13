# The first hour of a non-engineer

This harness measures what Ratchet asks of a solo builder in their first hour: a scripted owner who is not an engineer starts from an empty directory and does what the plugin tells them, until the first feature a person can use is merged and the app starts, or a budget runs out. One command runs it unattended against any plugin checkout, so each change to the first hour is measured against the same yardstick instead of by impression.

## Run it

```bash
just first-hour /path/to/ratchet /tmp/first-hour-1
# the same, without just:
uv run evals/first-hour/run.py run --plugin /path/to/ratchet --dir /tmp/first-hour-1
```

Options: `--minutes` (default 120), `--usd` (default 40), `--persona` (default `read-later`), `--model` (default `opus`, the product session), `--owner-model` (default `haiku`), `--max-steps` (for a smoke run). The directory must be under the system temp location and empty. A run needs a signed-in `claude` on the path and the other three plugins installed; `uv` fetches the Agent SDK the first time.

It writes, in that directory:

| File | What it holds |
| --- | --- |
| `metrics.json` | every measurement below, with the run's model, plugin commit and plugin versions |
| `table.md` | the short table a person reads first |
| `events.jsonl` | one event per line: each route instruction, step, question, prompt, paste, merge, gate run and app check |
| `transcript.jsonl` | every SDK message of the product session, whole |
| `app/` | the owner's project as the run left it |
| `plugin/` | the clone of the plugin checkout the run tested |
| `logs/` | each pasted command's output and each app check's log |

`uv run evals/first-hour/run.py reduce <dir>` rewrites `metrics.json` and `table.md` from `events.jsonl`. `uv run evals/first-hour/run.py oneoff <logs> <out>` reduces the logs of the hand-driven dogfood run of 2026-11-13, as a cross-check of the reducer.

The A/B comparison is two runs, one per checkout, and a diff of their `metrics.json`. Read the noise section before you trust a difference.

## What the run does

**The owner.** `personas/read-later.toml` says who the owner is, the idea ("a small read-later web app: I paste a link, it saves the title, I can mark it read and see what's left"), their firm positions (local only: no remote, no forge, no accounts) and the first slice a person can use. Every question goes to a small model given the persona and the question. It classifies the question first: `product` (the answer depends on what the owner wants, or on a firm position) or `default-able` (how it is built: tools, tests, git, the method). The rule is applied in code, not trusted to the model: a default-able question takes the option marked recommended whenever there is one. Each answer is logged with its class, whether a recommended option existed and whether it was taken. Questions arrive on three channels: a question dialog, a turn that ends by asking the owner something in prose, and a route `also:` line that proposes a life event. Another idea or application type is another persona file with its own slice; a new kind of slice check is one entry in `appcheck.SLICES`.

**The route decides the steps.** After every step the driver runs the plugin's own `scripts/route.py --json`, the engine behind `/ratchet:next`, and does what its `next:` line names, from the first `/ratchet:init` on:

| The route names | The owner |
| --- | --- |
| a slash command | types it into the session |
| a shell command (the `merge` stage, an owner's hook fix) | pastes it; the run counts it, and pastes the commands the output's `next:` lines name too, such as the hand merge and `just check-deep` |
| work in words (commit this, mark a ticket resolved) | asks the assistant to do it, as a non-engineer would |
| the hooks-install entry point | types `/ratchet:next`, which runs it |
| a forge stage (pull request, review) | nothing: the run stops, since the persona has no forge |

**Stops.** The first merged change after which the app starts and its slice works; the wall-clock budget; the cost budget (product session plus owner model); the same instruction twice on an unchanged checkout, or three times in all.

The owner follows the route and the `next:` lines of what they paste, not commands a turn gives only in prose. When a skill ends by listing `git merge` lines for the owner and the route still names that skill, the run stops on the repeat: that is the route and the session disagreeing, and the baseline below stops there.

**The app check.** After every merge, and once at the end, the default branch is checked out into a work tree of its own (the project's `node_modules` or `.venv` linked in) and started the way the repository tells a person to: a run command in a README code block first, then a `start`, `dev`, `serve`, `run` or `app` recipe in the justfile, then a `start`, `dev` or `serve` script in `package.json`. The app gets a free port through `PORT`; one that ignores it is found by the URL it prints. The check requests `/`, fills the first text field of the page's form with a link, sends it and looks for the link on the page. "No way to start it" is a finding, not a crash.

**The plugin under test.** The run clones the checkout's HEAD into `plugin/` before it starts, and everything runs from that clone: the session's plugin, the route, and the adopted project's gates, which find it through `RATCHET_PLUGIN` (without it they run from the installed marketplace copy, whatever checkout the run was given). A commit landing on the checkout mid-run changes nothing, which the first baseline needed: a merge to main landed eight minutes into it. Uncommitted changes in the checkout are not under test; the run says so and `meta.plugin.dirty` records it.

**Safety.** The run directory must be under the temp location. The product session loads the pinned plugin and disables the installed `ratchet@ratchet` for that session only (`--settings`); tools come through an allow-list with Edit and Write scoped to the owner's project, so the plugin's guard hook and every permission prompt stay in the loop, and `can_use_tool` answers each prompt as the owner would: yes, unless it writes into the home directory, the plugin checkout or its pinned clone. Writes elsewhere under the temp location are allowed because the product keeps its own work trees there (the drain's `/tmp/ratchet-queue-<uid>`); an owner who refused them would break the pipeline, which the first attempt at the baseline did. Deny rules and a PreToolUse hook refuse a push, a remote, a forge write, a plugin or MCP change and `--dangerously-skip-permissions`, chained or not. `gh` runs with an empty config directory and no token. The harness records a hash of the user's settings, `CLAUDE.md`, rules, agents and plugin registry before and after (`integrity` in `metrics.json`), and stops every process left running in the run directory or in the product's work trees.

Two settings bend the numbers, and a reader comparing runs should know them. Bash is on the allow-list whole, so Claude Code prompts for a Bash command only when it judges it sensitive: `prompts.permission` undercounts what a default install would ask a new owner. And the session loads the user's own settings sources, so the operator's `~/.claude` rules, memory and other plugins reach the product session the way they reach a real owner's; two operators' runs differ by that much.

## The measurements

| Key in `metrics.json` | What it is |
| --- | --- |
| `minutes.to_first_merge`, `minutes.to_first_usable_merge` | minutes from the start to the first merge on the default branch, and to the first after which the slice works |
| `app` | the last app check: the start command and where the repository says it, whether it started, served `/` and passed the slice, and the finding |
| `questions` | by class, by channel, recommended offered and taken (dialog and `also:` questions only: a prose question has no marked option), and every question with its answer |
| `prompts` | prompts the owner answered: `guard` (the Ratchet guard's ask, or the `ask` rule adopt writes for a guard path, which Claude Code reports with no reason), `permission` (Claude Code's own), and how many the owner declined |
| `guard_denials` | tool calls the guard denied the agent outright |
| `pastes` | every shell command the owner had to paste, with its exit code |
| `asks` | work the route named in words that the owner asked the assistant to do |
| `merges`, `hand_merges` | each merge with who made it (`hand`, `entry-point` or `agent`) and the owner-facing text at that moment, verbatim |
| `red_gates` | each gate red in a command the owner ran, and whether it was red again on the driver's rerun |
| `session_red_gates` | gates the agent saw red inside the session, by name |
| `gates` | of the widest gate run, the gates that judged, the ones that printed `later:` with their trigger, and the ones that do not apply (`none`) |
| `agents` | agents spawned (Agent tool and workflow agents) and workflow runs |
| `cost_usd` | the product session's cost as the CLI reports it, and the owner model's |
| `steps` | each step's command, action, start minute, minutes and cost |
| `meta` | model, plugin path, commit and version, the other three plugins' versions and commits, Claude Code and SDK versions, the harness commit, the global `core.hooksPath`, load average |

## Tests

The measurement reducer (over a recorded run and over the dogfood run's logs), the stop conditions, the route-to-move table, the owner's classification plumbing, the gate parser, the safety check and the app check (against a small server that does or does not save the link) run in `just check`, under the repository's 100% line and branch coverage, `ruff` and `mypy --strict`. The other evals keep their scripts outside those gates; this one is inside them because a wrong reducer would move every comparison. `first_hour/drive.py` is the one exception: it needs the Agent SDK and a signed-in `claude`, so coverage omits it and mypy skips it, and nothing in it decides a number. No tier runs a full run: it costs real money and up to two hours.

## Baseline

Ratchet `main` at `a47b55a` (0.11.0), Claude Code 2.1.323, SDK 0.2.181, product model `claude-opus-5-5`, owner model haiku, persona `read-later`, harness `235911f`. Run on 2026-11-13 at a load average near 60 on a shared box.

| Measure | Value |
| --- | --- |
| Stopped | repeat at 100.9 min (`/ratchet:safeguard` again: its branches wait for a hand merge) |
| Minutes to first merged change | 36.6 (the adoption branch, by hand) |
| Minutes to first usable merged change | n/a |
| App | no way to start it: no README, and no `dev` or `start` script or recipe |
| Questions (product / default-able) | 24 (11 / 13) |
| Recommended option offered / taken | 16 / 15 |
| Guard approval prompts / other permission prompts | 17 / 30 |
| Guard denials the agent met | 2 |
| Shell commands the owner pasted | 3 |
| Things the owner asked the assistant to do | 1 |
| Merges | 2: the adoption by hand, ticket 01 by the drain |
| Red gates (red again on rerun) | bite-all (no) |
| Gates judged / waiting / not applicable | 26 / 32 / 7 |
| Cost | $30.14 product session, $0.07 owner |

The run's driver parsed the widest gate run with a parser that predated the runner's `judged nothing (later: ...)` line; that one event was parsed again from its saved log (`logs/04-just-merge-chore-adopt-ratchet-paste-3.txt`), and the event log as recorded is kept beside it.

## Noise and signal

One run is one sample. Three runs reached the adoption merge: two stopped early (one by an owner who refused the drain's work trees, one because main moved under it) and the baseline. Their first 37 minutes, with the dogfood run of the same day:

| | Load | `/ratchet:adopt` | Cost to there | First merge | Questions to there |
| --- | --- | --- | --- | --- | --- |
| first attempt (`b254a2c`) | 9 to 19 | 18.4 min | $8.0 | 23.4 min, by hand | 20 |
| second attempt (`b254a2c`) | 37 to 73 | 30.6 min | $9.2 | 36.9 min, by hand (38.3 as recorded: that harness timed a merge at its step's end) | 17 |
| baseline (`a47b55a`) | 59 to 63 | 31.3 min | $11.2 | 36.6 min, by hand | 17 |
| dogfood run, by hand | | | | 23.4 min, by hand | |

What moves: wall-clock minutes follow the machine's load more than the plugin (adoption took 18 minutes on a quiet box and 31 on a busy one), so compare minutes only between runs at a similar `meta.load_avg`. Cost per step varies by about a third. Question counts move by a handful, and the owner model answers the same question differently: init's defaults (which include a GitHub remote) were refused in one run and accepted in the baseline, where the harness's refusal of `gh repo create` kept the project local anyway. Prompt counts depend on how far the run got: the drain alone asked 17 times for writes into its own work trees. `bite-all`'s drain smoke went red on clean state under load and green on the rerun in two of three runs; the rerun column says which reds to believe.

What held: every run hand-merged the adoption after `just merge` answered `merge: owner`; every app check found no way to start the app; the first owner paste was always the same three commands; the gates that judge versus wait are fixed by the plugin commit and the project's kinds, not by the run. Those are the measures to compare across one run each. Minutes, cost and question counts need two or three runs a side before a difference of less than about a third means anything.

`integrity.unchanged` was false in the baseline: Claude Code's own marketplace auto-update rewrote the plugin registry at 07:35 (impeccable 4.8.2 to 4.9.0) while no command of the run touched it. The session had loaded its plugins at the start, so the run was not affected, but the check cannot tell who changed the registry; a reader should look at which entries changed before blaming the run.
