# The first hour of a non-engineer

This harness measures what Ratchet asks of a solo builder in their first hour: a scripted owner who is not an engineer starts from an empty directory and does what the plugin tells them, until the first feature a person can use is merged and the app starts, or a budget runs out. One command runs it unattended against any plugin checkout, so each change to the first hour is measured against the same yardstick instead of by impression.

## Run it

```bash
just first-hour /path/to/ratchet /tmp/first-hour-1
# the same, without just:
uv run evals/first-hour/run.py run --plugin /path/to/ratchet --dir /tmp/first-hour-1
```

Options: `--minutes` (default 120), `--usd` (default 40), `--persona` (default `read-later`), `--model` (default `opus`, the product session), `--owner-model` (default `haiku`), `--max-steps` (for a smoke run). The directory must be under the system temp location and empty. A run needs:

- `bwrap` (bubblewrap) able to make a sandbox as your user;
- a signed-in `claude` on the path whose sign-in outlives the run by ten minutes (the run gets the current access token and never the refresh token, so it cannot renew it; start any Claude Code session to renew it first, or lower `--minutes`);
- the other three plugins installed: the run copies them in.

`uv` fetches the Agent SDK the first time. `uv run evals/first-hour/run.py probe --plugin ... --dir ...` starts one session with the run's launch, allows it nothing but three printing commands, and prints what it can reach: its tools, MCP servers and plugins, gh's config directory and login, its session id, and whether the model token is visible to its shell. Run it after any change to the launch.

It writes, in that directory:

| File | What it holds |
| --- | --- |
| `metrics.json` | every measurement below, with the run's model, plugin commit and plugin versions |
| `table.md` | the short table a person reads first |
| `events.jsonl` | one event per line: each route instruction, step, question, prompt, refused call, paste, merge, gate run and app check |
| `transcript.jsonl` | every SDK message of the product session, whole |
| `app/` | the owner's project as the run left it |
| `plugin/` | the clone of the plugin checkout the run tested |
| `home/` | the sessions' home: the run's Claude Code configuration, the copied plugins, the sessions' transcripts |
| `tmp/` | the run's temp location, which commands see as `/tmp` |
| `logs/` | each pasted command's output and each app check's log |
| `stderr.log` | the product session's Claude Code error output |
| `bin/` | the shell prefix that puts each of the session's commands in the sandbox, and links to the tools the run uses |

`uv run evals/first-hour/run.py reduce <dir>` rewrites `metrics.json` and `table.md` from `events.jsonl`. `uv run evals/first-hour/run.py oneoff <logs> <out>` reduces the logs of the hand-driven dogfood run of 2026-11-13, as a cross-check of the reducer.

The A/B comparison is two runs, one per checkout, and a diff of their `metrics.json`. Read the noise section before you trust a difference.

## Personas and the matrix

`--persona <stem>` runs one owner; `run.py personas` lists the stems and the stack each insists on. `read-later` leaves the stack to the assistant. The others state a firm language and shape in the owner's plain words, so a run shows whether the method works outside the two ecosystems it was first tried in:

| Persona | The owner insists on | The first slice |
| --- | --- | --- |
| `go-todo-cli` | Go, a command-line tool with its list in a plain file | `cli`: run `add buy oat milk`, then `list`, and find the task in the output |
| `rust-shortener` | Rust, one program that serves its own page | `html-form`: shorten an address, see it in the list |
| `kotlin-recipes` | Kotlin on the JVM, server-made pages | `html-form`: save a recipe name, see it in the list |
| `dotnet-chores` | C# on .NET, server-made pages | `html-form`: save a chore, see it in the list |
| `ruby-plants` | Ruby, as a Rails app with server-made pages | `html-form`: save a plant, see it in the list |

A persona file adds `stack` (a few words, for the matrix table) to the existing fields. The owner model's rule says a question about a language or shape its firm positions name is a product question, and it holds the position. The `cli` slice kind runs the program the way the repository says (the README's run line cut back before its arguments, or a `run` recipe), once per command of the slice, and looks for `expect` in the last output; the web slices are as before. The app check reads run lines for Go, Rust, .NET, Ruby and JVM projects as well as the original set.

One command runs the matrix, the personas that state a stack one after another (never in parallel: each run is a whole session with builds, and two at once move each other's minutes), each in its own directory under `--dir`:

```bash
just first-hour-matrix /path/to/ratchet /tmp/first-hour-matrix
# the same, without just; --personas go-todo-cli,ruby-plants picks some:
uv run evals/first-hour/run.py matrix --plugin /path/to/ratchet --dir /tmp/first-hour-matrix
```

It takes the single run's `--minutes` (default 90 here), `--usd`, `--model` and `--owner-model`, applied to every persona. `matrix.md` and `matrix.json` are rewritten after each run, so a matrix that is stopped (Ctrl-C reaches the run in progress, which writes its measurements) keeps what it finished. The table has one row a persona: stack, why and when it stopped, minutes to the first feature merge (the first merge whose branch is not the adoption's or init's) and to the first usable merge, questions, product against other, guard prompts, automatic merges, the architecture measures on one line, stack leaks, and whether a usable app was merged. A run that fails still gets a row with its exit code. Expect about an hour to ninety minutes a persona, and up to the cost budget each.

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

**The app check.** After every merge, and once at the end, the default branch is checked out into a work tree of its own (the project's `node_modules` or `.venv` linked in) and started the way the repository tells a person to: a run command in a README code block first, then a `start`, `dev`, `serve`, `run` or `app` recipe in the justfile, then a `start`, `dev` or `serve` script in `package.json`. The app gets a free port through `PORT`; one that ignores it is found by the URL it prints. The check requests `/`, fills the first text field of the page's form with a link, sends it and looks for the link on the page. The slice is `works`, `fails`, or `unchecked`: a page with a script may build its form and save in the browser, which this check does not run, so when the plain form does not work on such a page the slice is unchecked rather than failed. "No way to start it" is a finding, not a crash.

**The plugin under test.** The run clones the checkout's HEAD into `plugin/` before it starts, and everything runs from that clone: the session's plugin, the route, and the adopted project's gates, which find it through `RATCHET_PLUGIN` (without it they run from the installed marketplace copy, whatever checkout the run was given). A commit landing on the checkout mid-run changes nothing, which the first baseline needed: a merge to main landed eight minutes into it. Uncommitted changes in the checkout are not under test; `meta.plugin.dirty` records them. The other three plugins are copied from the operator's install into the run's home, at the versions installed there.

## Safety: what a run can reach

A run reaches its own directory, the model and the network, and nothing else of the machine's. What holds that:

**The sessions.** The product session and the owner model are Claude Code processes started with an environment built from nothing (only the locale and terminal settings are kept from the launching process: no `CLAUDE_CODE_*` of the session that started the harness, no token, no agent socket) and `HOME` set to the run's `home/`. So they load none of the operator's settings, rules, memory, plugins, MCP servers or claude.ai connectors: the session reads only project settings and its own `--settings`, takes no MCP server (`--strict-mcp-config`, connectors off by setting and environment), and loads the four plugins from the run. The owner model has no tools at all. The product session has the tools in `policy.TOOLS` and no others; the outward ones (web, remote triggers, notifications, scheduling, messages to other sessions or projects, artifacts) are also refused by name.

**Every tool call** of the product session, its subagents' included, goes through `policy.decide` twice: in a PreToolUse hook, which sees every call and blocks it if the hook itself fails, and in `can_use_tool`, which sees every permission prompt before the owner answers. A tool off the list, a command that reaches a forge or the network (but see registry reads below), publishes, starts Claude Code or reads the session's credential, and a path outside the run directory are refused; each refusal is logged and counted in `harness_denials`. Every refusal the session reads says what is refused and what still works ("This call is refused: it calls a forge. Only this call is refused. Package installs and reads of public package-registry metadata still work ..."), since a bare refusal once led a session to conclude that the whole network was gone and stop adoption.

**Registry reads.** A normal owner's session picks tools by looking up what is current, and `/ratchet:adopt` tells it to ("Look it up, now"). The run allows that with `curl`, in `first_hour/registry.py`: a `curl` is allowed when every flag is on a short list that cannot send or sign (silent, fail, location, timeouts, an output file, `-X GET`, an `Accept` or `User-Agent` header) and every URL is `https` on a named public host: any ecosystem's registry, none favoured. A body (`-d`, `-F`, `-T`), a method other than GET, `-u`, a config or netrc file, any other header, a forge host, an address (a raw IP, loopback, private, link-local) or a local name (`localhost`, `.local`, `.internal`, a name with no dot), `http`, a port, userinfo, a `$VAR` host, and a `curl` the line does not run as a command (in a string, or in a line a shell cannot read) are refused. The rule names no registry, so it favours no language stack. It is a bit weaker than a host list in two ways: a public name may resolve to an address on the machine's network (the check reads the URL, not DNS), and a read may carry data out in its URL. Neither adds to what a package install already reaches: the network is shared, and the sandbox holds nothing to sign a request with and nothing of the operator's to send. `-L` is allowed, so a read can follow a redirect to another host: it is still a GET with nothing to sign it with. `WebFetch` and `WebSearch` stay refused, because they run in the Claude Code process, which holds the model's token and is outside the sandbox, they take any host, and a page's text goes to the model summarised by another model; a `curl` runs in the sandbox with no credential at all, and its URL is checked as text. Library documentation through a connector (Context7) is off with every other connector; a session reads a registry's metadata or the package's own files instead.

The shell check reads a command line as a shell does (`first_hour/shellpaths.py`): a word that starts like a path is judged, and the script of `sed`, `awk` and `grep`, the text of a quoted message and the body of a heredoc that only writes a file are not. A string literal in code a command runs (`python3 -c`, a heredoc to `node`) is judged unless its first directory is not one the machine's root has, as with a route. A token that cannot be told from a path, such as `/proc/x` or a `..` after a `cd` to a directory the line cannot name, is refused. The commands a line runs (`shellpaths.scan`) are read the same way, after `do` and `then`, `env` and `xargs`, in `sh -c` and in `$(...)`: the network, registry and inline-code rules judge those, and not the text of a heredoc that writes a file or a string literal of a script, which an eval run's sessions were refused for ("publishes or logs in to a registry" on a document that said `uv` and `publish`, "runs inline code that opens a network connection" on a script that edited a file about `urllib`).

**Every command** of the run (each Bash call and hook of the session, through Claude Code's `CLAUDE_CODE_SHELL_PREFIX`, and each pasted command, route call and app check) runs under `bwrap`: the file system read-only, the run's `app/`, `home/` and `tmp/` writable, every home directory and `/run/user` hidden, `/tmp` replaced by the run's `tmp/`, a PID namespace of its own that dies with its parent, and no model credential in the environment of any process in it, `bwrap`'s own included. Inside, gh has an empty config directory and no token, git has no credential helper and rewrites every push URL to a path that does not exist, and there is no SSH key or agent.

**The Claude Code processes themselves are not in the sandbox**: they hold the model token, which no command may see, and the kernel does not let a sandbox start another inside it. This is the split Claude Code's own sandbox makes. Their file tools (Read, Write, Edit, Glob, Grep) are bounded by the policy alone, which judges a path as that process sees it, links followed.

Which layer stops each of the commands a session must never run:

| Command | The policy | If the policy missed it |
| --- | --- | --- |
| `gh repo create` | refused: calls a forge | gh is not logged in: no token in the environment, its config directory is the run's empty one, and the operator's is not in the sandbox |
| `gh api -X POST user/repos` | refused: calls a forge | the same |
| `git push` | refused: pushes to a remote | every push URL is rewritten to a path that does not exist; no credential helper, key or agent; a push to a path outside the run meets a read-only file system |
| `python3 -c` with an HTTP request | refused: inline code that opens a network connection | the request goes out, but carries no credential: there is none in the environment or readable in the sandbox |
| `curl` with a token | refused: opens a network connection | the same: the only token is the model's, which no command's process holds |
| `curl -X POST` or `-d` to a registry | refused: opens a network connection | the request goes out with nothing to sign it; a registry takes no write without a login |
| `curl -s https://<public host>/<path>` | allowed: a read of public metadata | none needed: an unauthenticated GET to a public host, nothing sent but the URL |
| `npm publish`, `uv publish`, `cargo login` | refused: publishes or logs in to a registry | no registry token exists in the sandbox |

What a run can still reach: the network, from every command and from the app (the model and the package registries need it), with nothing to sign in with. A package install fetches from the registries' file hosts as it always did, and a session reads registry metadata with `curl` as above; the machine's system files, read-only; services listening on the machine, since the network is shared; the file tools' race between the policy's check and the read, if a command swaps a link in between; and two directories at the top of the temp location: the run's runtime directory (`first-hour-<hash>`, where Claude Code puts its messaging socket, removed at the end) and, if Claude Code starts background work, its daemon directory under `cc-daemon-<uid>`, keyed by the run's home. The operator's `~/.claude/projects` is not written: the sessions keep their transcripts in the run's home, and `integrity.claude_projects` names any entry there that matches the run.

**Integrity and stopping.** The harness hashes the operator's settings, `CLAUDE.md`, rules, agents and plugin registry before and after (`integrity` in `metrics.json`, with `changed` naming the files that changed). The run's sessions never open those files, so a change there is the operator's own Claude Code, such as a marketplace auto-update. SIGINT and SIGTERM stop the run the way the budget does: the sessions are closed, every process the run started is ended (`integrity.stopped`, and `integrity.reaped` for any left in the run directory), the drain's leftover work trees are removed, and the measurements are written with the stop reason `interrupted`.

**What this does to the numbers.** Bash is not on the session's allow-list, so Claude Code asks about each Bash command it does not judge read-only, as it asks a new owner on a default install: `prompts.permission` counts those prompts. The operator's own rules, memory and other plugins no longer reach the product session, so two operators' runs differ only by the plugin versions they have installed. The baseline below was recorded before either change.

The session also sees its cost budget, which Claude Code enforces (`--usd`), and plans around it: in a ten-minute run capped at $5, adoption stopped after two of its thirteen steps "because the rest won't fit in this session's remaining budget". Give a measured run a budget well above what it will spend, and read a run that stopped near its budget with that in mind.

## The measurements

| Key in `metrics.json` | What it is |
| --- | --- |
| `minutes.to_first_merge`, `minutes.to_first_usable_merge` | minutes from the start to the first merge on the default branch, and to the first after which the slice works |
| `stop` | why the run ended (`usable-merged`, `wall-clock`, `cost`, `repeat`, `forge-stage`, `max-steps`, `interrupted`, `error`), with the detail and the minute |
| `app` | the last app check: the start command and where the repository says it, whether it started and served `/`, `slice` (`works`, `fails` or `unchecked`), and the finding |
| `minutes.to_first_feature_merge` | minutes to the first merge on the default branch whose branch name has neither `adopt` nor `init` in it |
| `architecture` | the architecture measures, below |
| `stack_leaks` | where the plugin's own text named a language or tool the owner never said, below |
| `questions` | by class, by channel, recommended offered and taken (dialog and `also:` questions only: a prose question has no marked option), and every question with its answer and the owner model's reason |
| `prompts` | prompts the owner answered: `guard` (the Ratchet guard's ask, or the `ask` rule adopt writes for a guard path, which Claude Code reports with no reason), `permission` (Claude Code's own), and how many the owner declined |
| `guard_denials` | tool calls the guard denied the agent outright |
| `harness_denials` | calls the harness's policy refused, with the tool, the reason and where (`hook` or `prompt`): a run that reached for something outside its directory shows here |
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
| `integrity` | whether the operator's watched files are unchanged (`changed` names any that are not), the processes the run ended at its stop (`stopped`) and any found still running in the run directory after that (`reaped`), the drain's work trees removed, and any entry of the operator's `~/.claude/projects` that matches the run (`claude_projects`) |

**Runs before the policy fix undercount the feature stage.** Until the fix that put `StructuredOutput` on the tool list and had the shell check read only what a shell uses as a path, the policy refused two things a normal session needs. A workflow agent with a `schema` returns its result through `StructuredOutput`, so the `/ratchet:feature` pipeline failed at its first step and the owner was told to type the command again. And a slash command named in a command line, a `sed` script such as `s/a/b/p`, a glob in a comment and a route such as `/items` were read as paths outside the run and refused. A run from before that fix shows these in `harness_denials` (the tool `StructuredOutput`, and "reaches outside the run directory" with a reason such as `(/p)`, `(/**)` or `(/ratchet:safeguard)`). Its feature-stage minutes, cost, prompts and merges describe the harness as much as the plugin: do not compare them with a run after the fix. A path in `harness_denials` that really is outside the run is still refused and still counts.

**Runs before the registry fix undercount adoption.** Until the fix that let a session read registry metadata, `curl -s https://pypi.org/pypi/<name>/json` was refused ("opens a network connection"), and one session that met it concluded that the harness refused every network connection and stopped adoption at about eight minutes. The same run shows false positives in `harness_denials` that the fix removed: "publishes or logs in to a registry" and "runs inline code that opens a network connection" on a heredoc that only edited a file, and "reaches outside the run directory" on a `sed` script in a `for` loop. What stays refused and is correct: a `curl` to the session's own app with `-X POST` or `-X DELETE` (the network is shared with the machine's other services), a file written beside the owner's trees in the run directory, and a `..` one level too many.

### The architecture measures

`architecture` in `metrics.json`, read with no model: the question part from the logged questions, the rest from the default branch's files and the transcript at the end of the run (two events, `architecture` and `stack-leaks`, so `reduce` gives the same numbers again).

| Key | What it is |
| --- | --- |
| `asked`, `options`, `recommended`, `enough_options`, `batched`, `round_questions` | an architecture question ("How should it be built?", the words `questions.toml` registers for `architecture.system`) came by the dialog, how many options it had, whether one was marked recommended, whether there were at least two, and whether it came in one round with at least one other dialog question of the same step |
| `tools_asked`, `answer`, `took_recommended` | the tools question, the owner's answer and whether it was the recommendation |
| `change_questions`, `change_list` | questions outside the adoption round that ask the owner to change the architecture (a new layer, a new kind of storage, a process boundary, a re-architecture); a wording match, so a floor and a lead to read, not a verdict |
| `adr` | the record the binding's `- Decision:` names (or the first with a `## Considered options`): found, `chosen_by`, the count of considered options, whether `## Reversal cost` and `## Refutation criterion` are filled, and `complete` (all of them, at least two options) |
| `binding`, `parts_declared` | the binding has an `## Architecture` section, the record it names exists, and the rows of its `\| Part \| Path \| May depend on \|` table |
| `features` | each `docs/features/<slug>/spec.md` and whether it has a filled `## Design` section |
| `reviews` | in the transcript: review results seen (by their `verdict` field), issues on axis `design`, and mentions of axis F. A review that approved with no design issue leaves nothing of the axis in the transcript, so the last two are floors |

### The stack leak measure

`stack_leaks` reads the transcript for the plugin's own text: hook output, a skill's text, and what a session read from the plugin's files. It looks in that text for the language and tool names in `first_hour/leaks.py` (eval data, so it may name stacks; the plugin may not) and keeps each name the owner never said, whether in the persona or in an answer, once per source with the surrounding words. A name found there is a plugin leak: the plugin put it in the session, and an agent can take it for the owner's wish. Zero is the goal. `python3` and `just` are not names (the plugin's scripts run on one and its contract is the other); a word that is also plain English is matched only in the shape that names the tool. Read the excerpts: a lexicon match is evidence, not proof.

## Tests

The measurement reducer (over a recorded run and over the dogfood run's logs), the stop conditions, the route-to-move table, the owner's classification plumbing, the gate parser, the tool policy, the sandbox and the app check (against a small server that does or does not save the link) run in `just check`, under the repository's 100% line and branch coverage, `ruff` and `mypy --strict`. A whole run is driven against fake models and a fake plugin, including a SIGINT and a SIGTERM in a session turn and in a paste. The sandbox tests run each forbidden command for real inside the sandbox, from a parent that holds a token, and check that it cannot log in to gh, push, send a credential, write outside the run or see the operator's home; they skip where `bwrap` cannot make a sandbox, and coverage holds without them. The other evals keep their scripts outside those gates; this one is inside them because a wrong reducer would move every comparison.

`first_hour/drive.py` is the exception: it needs the Agent SDK and a signed-in `claude`, so coverage omits it and mypy skips it. It holds the SDK glue, and it still decides two things a number depends on: when a turn has ended (the last result once no workflow or agent runs behind it, or an idle limit), which sets every step's minutes, and the session's cost (the highest total the CLI reported for each session). The `probe` subcommand is how its launch is checked. No tier runs a full run: it costs real money and up to two hours.

## Baseline

Ratchet `main` at `a47b55a` (0.11.0), Claude Code 2.1.323, SDK 0.2.181, product model `claude-opus-5-5`, owner model haiku, persona `read-later`, harness `235911f`. Run on 2026-11-13 at a load average near 60 on a shared box.

That harness predates the sandbox. Its session loaded the operator's own settings, rules, memory, plugins and connectors, ran commands unsandboxed with Bash on the allow-list whole, and was allowed to write anywhere under the temp location. Its permission prompt count is therefore low against this harness, and its run may have been steered by the operator's rules. The other measures in the table did not depend on any of that. A new baseline under this harness is parked until a full run is approved.

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

One run is one sample. Three runs reached the adoption merge: two stopped early (one by an owner who refused the drain's work trees, one because main moved under it) and the baseline. They are not replicates: the harness changed between them, and so did the plugin commit between the attempts and the baseline. Read them as how far one run can move, not as a spread. Their first 37 minutes, with the dogfood run of the same day:

| | Load | `/ratchet:adopt` | Cost to there | First merge | Questions to there |
| --- | --- | --- | --- | --- | --- |
| first attempt (`b254a2c`) | 9 to 19 | 18.4 min | $8.0 | 23.4 min, by hand | 20 |
| second attempt (`b254a2c`) | 37 to 73 | 30.6 min | $9.2 | 36.9 min, by hand (38.3 as recorded: that harness timed a merge at its step's end) | 17 |
| baseline (`a47b55a`) | 59 to 63 | 31.3 min | $11.2 | 36.6 min, by hand | 17 |
| dogfood run, by hand | | | | 23.4 min, by hand | |

What moves: wall-clock minutes follow the machine's load more than the plugin (adoption took 18 minutes on a quiet box and 31 on a busy one), so compare minutes only between runs at a similar `meta.load_avg`. Cost per step varies by about a third. Question counts move by a handful, and the owner model answers the same question differently: the first attempt's owner refused init's defaults, which include a GitHub remote, and the second attempt's and the baseline's accepted them, against the persona. In those two runs the project stayed local only because the harness refused `gh repo create`. No count is normalised for how far a run got. A run that reaches the drain has more prompts and questions than one that stops before it: the drain alone asked 17 times for writes into its own work trees. `bite-all`'s drain smoke went red on clean state under load and green on the rerun in two of three runs; the rerun column says which reds to believe.

What held: every run hand-merged the adoption after `just merge` answered `merge: owner`; every app check found no way to start the app; the first owner paste was always the same three commands; the gates that judge versus wait are fixed by the plugin commit and the project's kinds, not by the run.

With one run a side, which measures a difference in means something:

| Measure | One run a side | Why |
| --- | --- | --- |
| Stop reason, and the stage it stopped at | yes | it held across the runs above |
| App: starts, slice | yes | it held, and it is checked the same way every time |
| Gates judged, waiting, not applicable | yes | fixed by the plugin commit and the project's kinds |
| Shell commands pasted, hand merges | yes, if both runs stopped at the same stage | they held, but a run that goes further pastes and merges more |
| Guard and permission prompts | only if both runs stopped at the same stage | they count what the run reached |
| Minutes | no | they follow the machine's load: 18 against 31 minutes for the same step |
| Questions | no | they move by a handful, and the owner answers the same question differently |
| Cost | no | it varies by about a third per step |

Minutes, cost and question counts need two or three runs a side before a difference of less than about a third means anything.

`integrity.unchanged` was false in the baseline: Claude Code's own marketplace auto-update rewrote the plugin registry at 07:35 (impeccable 4.8.2 to 4.9.0) while no command of the run touched it. The session had loaded its plugins at the start, so the run was not affected. The check cannot tell who changed a file: `integrity.changed` names which ones did, and a reader should look at those before blaming the run. Under this harness the sessions never read the operator's registry, so a change to it cannot reach the run at all.
