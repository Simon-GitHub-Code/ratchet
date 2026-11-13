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
| `prompts` | prompts the owner answered: `guard` (the Ratchet guard's ask), `permission` (Claude Code's own), and how many the owner declined |
| `guard_denials` | tool calls the guard denied the agent outright |
| `pastes` | every shell command the owner had to paste, with its exit code |
| `asks` | work the route named in words that the owner asked the assistant to do |
| `merges`, `hand_merges` | each merge with who made it (`hand`, `entry-point` or `agent`) and the owner-facing text at that moment, verbatim |
| `red_gates` | each gate red in a command the owner ran, and whether it was red again on the driver's rerun |
| `session_red_gates` | gates the agent saw red inside the session, by name |
| `gates` | of the widest gate run, the gates that judged and the ones that printed `later:` with their trigger |
| `agents` | agents spawned (Agent tool and workflow agents) and workflow runs |
| `cost_usd` | the product session's cost as the CLI reports it, and the owner model's |
| `steps` | each step's command, action, start minute, minutes and cost |
| `meta` | model, plugin path, commit and version, the other three plugins' versions and commits, Claude Code and SDK versions, the harness commit, the global `core.hooksPath`, load average |

## Tests

The measurement reducer (over a recorded run and over the dogfood run's logs), the stop conditions, the route-to-move table, the owner's classification plumbing, the gate parser, the safety check and the app check (against a small server that does or does not save the link) run in `just check`, under the repository's 100% line and branch coverage, `ruff` and `mypy --strict`. The other evals keep their scripts outside those gates; this one is inside them because a wrong reducer would move every comparison. `first_hour/drive.py` is the one exception: it needs the Agent SDK and a signed-in `claude`, so coverage omits it and mypy skips it, and nothing in it decides a number. No tier runs a full run: it costs real money and up to two hours.

## Noise and signal

One run is one sample. From the runs so far (the dogfood run, a one-step smoke run, and the baseline below), this is what moves between runs without a change to the plugin, and what does not.
