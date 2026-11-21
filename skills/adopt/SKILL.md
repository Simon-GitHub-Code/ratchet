---
name: adopt
description: "Bring a repository onto the Ratchet method: declare what kind of software it is, bind the floor and the rows whose code already exists with current tools, and leave every other row waiting for the trigger that will ask it."
disable-model-invocation: true
---

# Adopt the Ratchet method

Adoption binds the floor (`always`) and the rows whose trigger has already fired in this tree, and writes every other row `later: <trigger>`. `/ratchet:safeguard` binds each one when the route says it is owed.

Load the `ratchet:method` skill first: this skill assumes its vocabulary. Templates live in `${CLAUDE_SKILL_DIR}/templates/` and mirror the repository layout; the `justfile` and `.github/` among them are the `just` and GitHub examples, and a placeholder's `bind:` words say what it needs. Read nothing else of the plugin: the templates, the output of the scripts this skill runs and the pages it links hold every rule you need, and a red gate's line names its repair.

The method ships the questions, its own gates and three gates for any service, and no tool for any stack: the application types decide which questions apply, and the tool behind each one is proposed by you now, from what is current, and chosen in the architecture round of step 3 ([architecture.md](../method/architecture.md)).

No skeleton is built: on an empty repository the next thing written is the owner's idea. What that idea will need is ready, though: the test runner, every config the stack rules promise, how the product starts, and the change-size ceiling, so the first feature, built unattended, changes no guard path but the dependencies it adds. Step 12 rehearses the first slice to show it. [tiers.md](../method/tiers.md) holds the questions, the types, the triggers, what a recipe must get right, and speed.

## What the owner is asked

The owner is asked product questions, as the method's rule on asking defines them, and the architecture with its tools. They are asked only at step 3, so that every question comes in the first minutes and the rest of adoption runs while they are away. Put them all in one message, each in the words written here, with your reading of the records as the recommended option, so the owner can accept them all at once.

1. **Ask** `adopt.first`: "What is the first thing you want to be able to do with it, from start to finish?" The options are the first usable part the records point to, as a few things a person can check, in the owner's words.
2. **Ask** `adopt.later`: "Which of these can wait until that first part works?" The options are what the records name beyond that part; several may be chosen.
3. **Ask** `adopt.hard-to-undo`: "<a choice about what happens to someone's things that would be hard to undo later, asked as someone using it would put it>" At most two, only where the first part you recommend meets such a choice and nothing the owner said settles it, such as "When you mark an item read, should it stay in your list as read, or be deleted?". `/ratchet:conjecture` asks the rest before anything is built.

Then the architecture and its tools, drafted as [architecture.md](../method/architecture.md) says: two or three whole systems that fit what the owner said. Each states what it is made of, where it runs, where its data is kept, how its parts talk, its parts, its trade-offs and what changing it later costs. Your recommendation comes first.

- **A repository with code:** the first is "keep what is here", described from the code. Another is offered only when the code shows a reason. A rewrite is never offered by default.

4. **Ask** `architecture.system`: "How should it be built? Each choice says what it is made of, where it runs, where your things are kept, and what changing it later would cost." The options are the systems, recommended first, and "You decide", which takes the recommendation.
5. **Ask** `architecture.tools`: "Which tools should check the work? Each set names one tool for each kind of check, for the design I recommend." The options are the recommended system's set, one concrete current tool for each concept the rows bound now and the tests need, a second set only where two tools are close, and "You decide", which takes the first. When the owner picks another system, that system's own set holds and this answer is set aside.

Whether others reach it, rely on it, or trust it with what cannot be got back is something a machine cannot see reliably, so the owner declares it. On a repository that already has code, always ask the first in the same message, naming any sign the tree shows (a deploy configuration or workflow, a production address, a host's configuration); recommend "no" only when the tree shows none:

- **Ask** `event.deployed`: "Is it now running somewhere that people other than you can reach?"

Ask the other two in the same message when the tree shows a sign of them (data the tree says is kept, users outside the project). When it shows none and the owner answers yes to the first, the other two follow in one more message, the only one this step adds:

- **Ask** `event.irreplaceable-data`: "Does it now hold anything that could not be got back if it were lost?"
- **Ask** `event.depended-on`: "Do people other than you now rely on it?"

On an empty repository none of the three is asked. So a repository with code is asked at most nine questions here, an empty one at most six. Where the harness takes fewer in one call, the rest follow in the next call at once, with nothing done between: it is still one round.

A choice the owner's words already make is not asked. A deeper interview is the owner's `/mattpocock-skills:grill-with-docs`. Adoption does not wait for it.

**What the owner asked for wins.** Anything typed after the command (`/ratchet:adopt no GitHub, keep it on this computer`) is a preference. It chooses among the options a row allows and never loosens a safeguard, as [the rule on preferences](../method/records.md#a-preference-typed-after-a-command) says: read it before you act on one. Within that:

- it settles the choice it names;
- it wins over the defaults below unless it cannot answer a row: then say why, and take the default;
- a question it answers is not asked, except a life event's: the owner's answer to that question is the only declaration.

Each one is listed under **Assumed** as "you asked for", or as not applied with the route that exists.

**Everything else is assumed.** Act on each with no question and list it at the end of the hand-over under **Assumed**, in plain words, with what changing it later costs and what the owner can say to change it:

- **Assume** `adopt.types`: the application types the records and the tree point to (`## Stack`, the code, how it starts), each with its evidence, said as "a web page served by a small server of its own" rather than by type name.
- **Assume** `adopt.events`: on an empty repository, no life event, said as "nobody but you reaches it yet". On one with code, each event the owner answered yes to. The route asks again when a sign appears.
- The tool of a row bound now whose concept the architecture round named no tool for: chosen at step 5, looked up now, with the one you passed over.

## What it costs

Adoption installs the tools of the rows it binds and the test runner, runs those gates, plants each one's fault in a scratch copy, rehearses the first slice in another, and runs `check-deep` once, in step 12. On an empty repository the only rows it binds are those whose trigger is `always`: with the test runner and the rehearsal, about 35 to 45 minutes on an idle machine, and over an hour when adoption first installs the language's tools or they are slow to set up. A repository with code also binds the rows its code has already triggered.

Tell the owner this before step 1. It is not a question.

## Steps

1. **Check the ground.**
   - Run `python3 -E -s -B -X pycache_prefix=/nonexistent/ratchet-no-bytecode "${CLAUDE_PLUGIN_ROOT}/scripts/route.py" --root . --offline` in one Bash call.
   - When it names `/ratchet:init` or `/mattpocock-skills:setup-matt-pocock-skills`, print its lines and stop: both are the owner's keystrokes, and adoption needs what they write.
   - Check that later steps can run:
     - `git` 2.36 or later;
     - `python3` 3.11 or later;
     - `just`, or the runner the repository uses;
     - the `pre-commit` framework (`pre-commit` or `uvx pre-commit`);
     - with an `origin` on GitHub, `gh` with `gh auth status` green;
     - `claude plugin list` showing `ratchet`, `mattpocock-skills`, `pstack` and `impeccable`.
   - Commit the tracker setup's uncommitted records, if any, on the default branch as `chore(tracker): record the issue tracker`.

   Done when the route names neither command, every check holds and no tracker record is left uncommitted; or you have printed the route's lines, or named what is missing, and stopped.

2. **Copy the templates.**
   - Work on a new branch, or on the one an earlier run of this skill left, which the route names this skill to resume while the binding has no plugin pin (the copy skips every file that exists, so it resumes).
   - Every commit's subject starts `chore(method): adopt`: the `commits` gate accepts it, and the change-size gate exempts a range made only of such commits and prints the size instead.
   - Copy every file under `templates/`, dotfiles included, to the same path, skipping any that exists and skipping `.claude/settings.json`. Claude Code applies its `deny` rules the moment the file exists. Step 8 writes it.
   - Append to `.gitignore` the lines of the init skill's `templates/gitignore` it lacks.

   Done when every file under `templates/` but `.claude/settings.json` is at its path, `.gitignore` holds every line of the init skill's `templates/gitignore`, and the skipped files are noted for the hand-over.

3. **Recognise the repository.**
   - Read `## Stack` from `PRODUCT.md`, else from `AGENTS.md`, then the tree: the language, the package manager and its lockfile, the task runner, the forge (`git remote`), what the deliverable is and how it starts.
   - Choose the types to recommend and run `python3 -E -s -B -X pycache_prefix=/nonexistent/ratchet-no-bytecode "${CLAUDE_PLUGIN_ROOT}/scripts/application_types.py" <type>...`. It prints each question the types narrow, with `applies` or the `none: not a <type>` cell to write. Every other question applies.
   - Draft the architecture options and their tool sets as [architecture.md](../method/architecture.md) says, each tool looked up as step 5 says.
   - Draft the design brief from the records.
   - Ask the questions of [What the owner is asked](#what-the-owner-is-asked) as that section says: nothing after this step asks the owner anything.

   Done when you can name the stack, the runner, the forge, the types and the rows they leave out, the design brief is drafted, and each question is answered.

4. **Find what has arrived.** In `docs/process/binding.md`, in this order:
   1. Write the `## Architecture` section from the system chosen at step 3: `- Decision:` with the path the architecture record will take (the next free number in `docs/adr/`, written at step 7), and one row per part, inner to outer, with the globs of its files and the parts it may depend on. Write `- Published interface:` with the globs of the files a library's users read as its contract, or `none: not a library`.
   2. Write the `- Application types:` line, and `- Life events:` as `adopt.events` settles it.
   3. In the Gates table, write every row whose question's trigger in `application-types.toml` is not `always` as `later: <trigger>`, copied exactly, or `none: not a <type>` where step 3 printed that.
   4. Fill `## Life event signs` (the files of this stack that mark `deployed` and `irreplaceable-data`, or `none: <reason>`) and `## Image pins` (the files that name a container image and the pattern that finds the reference, or `none: <reason>`), from what you know of the tools chosen at step 3.
   5. Write `## Documents`: one row per kind of Markdown file the method's kinds do not cover, from the harness and the forge chosen at step 3: the harness's instruction files, the forge's templates, and any page of the project's own. Each is `never` or `pass`. In `## Life event signs`, give the `depended-on` row the forge's files a maintainer writes for strangers, or `none` with the reason.
   6. Fill `## Triggers`: one row per kind of code the declared types are asked about, and one per kind whose arrival reopens the architecture whatever the types (the `conformance` entry point names each kind missing), with this stack's Paths and a Pattern where a path is not enough, or `none: <reason>` for a kind this project will never have. `flag` is `flags.toml` with the pattern of a flag's table, and `layer` is the parts table of the binding's `## Architecture` section, so `dependency direction` is owed now and bound with the rest.
   7. Prove each row with `python3 -E -s -B -X pycache_prefix=/nonexistent/ratchet-no-bytecode "${CLAUDE_PLUGIN_ROOT}/scripts/triggers.py" --count`. A row that finds nothing in a tree that has the kind is wrong. For a kind with no instance yet, write one line of it in a scratch file the Paths match, see it counted, and delete it.
   8. Run `python3 -E -s -B -X pycache_prefix=/nonexistent/ratchet-no-bytecode "${CLAUDE_PLUGIN_ROOT}/scripts/triggers.py" --json`. It lists the rows owed now: those, with the floor, are the rows this adoption binds.

   Done when the `## Architecture` section, the `- Application types:` and `- Life events:` lines, `## Life event signs`, `## Image pins` and `## Documents` are written, every row whose trigger is not `always` reads `later: <trigger>` or `none: not a <type>`, every kind asked has a row, each count is what the tree holds, and you know the rows to bind.

5. **Take the tool for every row bound now, and for the tests.** Go through the floor, the owed rows of step 4 and the tests one at a time, each with the tool the architecture round chose for its concept. Any other `later:` row gets no tool until its trigger fires. A row bound now whose concept the round named no tool for gets one chosen by the rules below, which are also the rules the sets you offered at step 3 were chosen by.

   - **The tests are provisioned, not asked.** The first feature writes its tests before any module exists, so every repository gets its test runner now, and a property-testing library where the runner has none, while their row stays `later: code:source`. Step 7 names them in the binding's Tests section, with one row of its suites table for each suite and the recipe that runs given files of it (`none: <reason>` where it cannot), and step 9 sets `test_runner` in the `justfile`. `/ratchet:safeguard` later binds the row to them and installs nothing.
   - **What is there wins**, unless you give the reason to replace it.
   - **Look it up, now.** What you remember of a tool is a conjecture. Check its current documentation (Context7 or a web search) that it exists, is maintained and answers the row, and say which rows you could not check. Versions come from the registry through the package manager's add command, never from memory, and never from this plugin.
   - **Prefer** one tool for several rows; a tool the package manager locks; a tool that fails when it finds nothing to run; a tool that uses every core and can run one shard of several.
   - **`none` is rare.** For a row that applies, `none` is for a property the project will never have, with the reason and an ADR you write. A row whose code does not exist yet is `later:`, never `none`.
   - **Already bound.** The method's own gates arrive bound to the plugin's scripts. For a service, `smoke`, `load-smoke` and `telemetry-e2e` do too, once their triggers have fired.

   Write one table, for the pull request and the hand-over:
   - each row bound now with its tier, the tool and where you checked it;
   - the test runner and property-testing library on a line marked `provisioned`;
   - the `later:` rows on one line per trigger;
   - the rows left out, on one line;
   - under it, what you assume without asking, with reasons: the non-negotiables and their gates, and, where their rows are bound now, the telemetry and flag seams and who may turn a flag on in production (the owner, unless a record says otherwise).

   Done when every row bound now has a tool or a reasoned `none`, the test runner and, where it has none, a property-testing library are chosen, and the table is written.

6. **Settle what is assumed.** A life event the owner answered yes to: write it on `- Life events:` and bind its owed rows with the rest.

   Done when the types, the life events and the tools are settled.

7. **Write the binding and `AGENTS.md`.**
   - **The binding.** Fill its Runner and Forge sections, the default branch included. Another runner, another forge, or no `origin`: do what [runners-and-forges.md](runners-and-forges.md) says first.
   - **The Gates table.** Every row bound now gets its tool, threshold source and entry point. The rest stay `later:` or `none: not a <type>`, and a row is never deleted.
   - **The architecture record.** Write it under the number step 4 gave it, as [architecture.md](../method/architecture.md) says:
     - the system chosen and who chose it (the owner, or the recommendation because they left it to you);
     - at least two alternatives with why each lost;
     - the reversal cost part by part;
     - the refutation criterion;
     - in its Decision, each tool with where you checked it and the reason for each `none` on a row that applies, which those cells cite.
   - **The other sections.** Fill every other section of the binding but Speed, which step 10 fills. A section that describes only `later:` rows (Telemetry, Feature flags) says so in one line.
   - **`AGENTS.md`.** From step 5's assumptions, write the non-negotiables with the ADR and the gate behind each, and the stack rules the method leaves open. Where `AGENTS.md` already has a `## Layers` section, move its rows into the binding's `## Architecture` table and delete it.
   - **The stack rules.** A stack rule describes the tree as adoption leaves it. Every config it names (an exemption for the one module allowed something, a directory a tool includes, a compiler key a declared type needs) is written now, and no rule leaves a config to "the change that brings" something: that change is the unattended pipeline's, and a config is a guard path it cannot edit. Name the file `boot` runs and, where settings are read, the one module that reads them.

   Done when every entry point names a command, no Tool cell is empty, the architecture record exists, `AGENTS.md` holds the non-negotiables and the stack rules and no `## Layers` section, and neither file holds an angle-bracket placeholder outside Speed.

8. **Fill the manifest, then generate the permission rules.**
   - Fill each empty `paths` of the groups in `guards/manifest.toml` with this stack's globs: `tool-config` takes the config file of every gate tool, the dependency manifest and its lockfile among them, `fitness` the fitness test directories, and `gate-tools` the directories of the gate tools the project writes. A path may be listed before its file exists. Where a project-local verification skill exists, add its directory to `formal`.
   - Bind the three `[[door]]` tables, the one-way doors the merge judge sends to the owner: `one-way` takes the migrations, schema definitions and infrastructure code, `stored-data` the golden fixtures `data-compat` loads, and `contracts` the artefacts `contracts-check` and `api-check` diff. A door with no path here keeps `paths = []` and says why in `none = "<reason>"`.
   - Run `just settings` last, so the permission rules switch on only once every guard file is in place. It creates `.claude/settings.json` and writes its `deny` and `ask` lists. Never edit them by hand.

   Two things stay apart: the guard hook reads its path rules from the manifest on the default branch, so they start with the adoption's merge, while the permission rules come from the work tree's `.claude/settings.json` and start the moment `just settings` writes it.

   Done when no group of `guards/manifest.toml` has an empty `paths`, each `[[door]]` has its paths or its `none` reason, and `just settings-check` is green.

9. **Bind the entry points, tier 0 first, and show each one bites.** Read [what a recipe must get right](../method/tiers.md#what-a-recipe-must-get-right) before the first recipe.

   **A key a declared type needs is never removed to quiet a gate.** When a gate is red on such a key, or on a provisioned package nothing uses yet, fix the other side: install the runtime package the key names when the stack names it, have the configuration that sets a tool up use it (a setup file the test runner loads), or give the gate its narrowest exception. Step 12's rehearsal must leave the fix green.

   **In order.**
   1. Run `just snapshots` once the tool configs exist.
   2. Bind tier 0 until `just tier0` is green.
   3. Install the commit hooks: run `just hooks-install` in one Bash call. It prints `hooks: green` once a commit here runs tier 0. An `owner:` line instead names a repair only the owner may run: give the owner that one command now, not at the hand-over, and go on. A `fix:` line names a repair that is yours: make it, and run `just hooks-install` again.
   4. Bind tier 1 until `just tier1` is green, each gate over the change through `{{changed}}`, which judges nothing on the adoption.
   5. Bind each tier 2 gate bound now until it is green alone.
   6. Run `just ratchet`, with its `bind:` line replaced by the suites and mutation run that are bound, or removed while none is, and commit the first thresholds it writes. While the owner has yet to install the hooks it is red, and the thresholds stay as you wrote them.

   **What binding takes.** Do each part below as you reach the recipe that needs it.

   **Install.** Install each tool through the package manager, the provisioned test tools included, and add what the tools write to `.gitignore`.

   **Recipes.** Replace each `bind:` body in the `justfile`:
   - a bound row with the real command;
   - a `later:` row with `@echo "later: <trigger>"`, the same words as its Tool cell;
   - a row bound `none` with `@echo "none: <reason>"`.

   The two `@echo` bodies exit 0. Give the `flags` recipe `--used-in` with each source directory the Architecture table names, so a flag leaves the registry with its code. Set `test_runner` to the command that runs the test files named after it, and make the recipe of every suite the suites table lists take files the same way. The test runner's configuration includes the test directories and the Critique tests directory.

   **Test paths.** Fill the binding's `## Test paths` section from what you know of this stack: the globs of every file that is a test, a fixture or test support, the Critique tests directory included, so the change-size gate counts product code against the product ceiling and tests against their own. The gate counts every line as product while the section is unfilled.

   **What each gate reads.** For each tier 0 and tier 1 gate you bound, write a bullet under the binding's `## What each gate reads`, keyed by the last word of its entry point: the config files its tool reads besides the code it judges, as repository-relative paths, directories or globs. Take them from the tool's documentation and the recipe, not from guesses. A change to such a file, where the binding's `tool-config` is its only gate group, then proves only the gates that read it. Leave out a gate whose reads you cannot name: with no bullet it is proven on every such change, which is slower and never wrong. A config file that no bullet names proves every gate when it changes.

   **Fill the four stack sections of the binding from the tools you bound.** The method names no package manager, lockfile, linter or directive, so each fact below is yours to state, from what you know of the tools you chose and what the human decided, in plain terms and without copying from this text.
   - `## Install`: say whether a fresh checkout must install the tools before a recipe runs one.
     - **It must:** bind the `install` recipe to an install frozen to the lockfile you name that does nothing once it has run. List every recipe that runs a tool the install provides, make each depend on `install`, and put it after `plugin-pin` in each tier, so a tier installs once before its gates start.
     - **It need not:** write `none` with the reason, and leave the `install` recipe's `@true` body.
   - `## Dependencies`: name the globs of the manifests and of the lockfiles, any registry the lockfiles do not use yet, and write the `dependencies` recipe, which reads the files under the directory it is given as data and prints the records the section defines. Or write `none` with the reason.
   - `## Suppressions`: one row per kind of directive that tells a bound tool to skip a finding, and per marker that narrows a suite, each with its pattern and its rule. Then, for each bound question the catalogue marks `directives`, a line naming the kinds that silence it, or `none` with the reason.
   - `## Comments`: one row per kind of tracked file other than Markdown, `listed` or `none` with the reason, and the `comments-list` recipe, which reads the files it is given as data and prints a record for every line a comment occupies, as the section says. List every directive the files carry in `## Suppressions`, `kept` where it silences no gate. The version note after a workflow's pinned action needs no row: the gate reads it as the pins gate does. Then run `just comments --record`. While the merge base binds no lister, it reads every tracked file a `listed` row matches and records each comment it finds as owed. A comment with no text is deleted instead, since the ledger cannot hold one.

   `just merge` and CI start from a fresh checkout: a recipe that is green here only because you installed by hand is red there. In `quality/bites.toml` the `setup` of a stack with a real install is `just install`, not a second copy of the command.

   **Boot.** Fill `boot`, `ready` and `service` at the top of the `justfile` for every type that boots, whether or not its code has arrived: the command this stack's conventions imply for the file `AGENTS.md` names, and where it answers.
   - The boot command builds what it runs and stays in the foreground. Raise `boot_timeout` when the build is slow, write `{port}` where the port goes (`$PORT` also works in `boot`), and write `{host}`, the host of `ready`, where the deliverable takes the address it listens on. A deliverable that listens wider on purpose names each such address in `listens`, separated by spaces.
   - `ready` is the URL that answers below 400 once the deliverable is up, with `{port}` for its port. A `cli`, `job` or `app` answers no URL and sets `ready` to `"none"`. A `library` sets `boot`, `ready` and `service` to `"none"`.
   - On an empty repository that file does not exist yet, and nothing is red for it: every row that boots is `later:`, so only `just start` and step 12's rehearsal run `boot` before the first entry point arrives.
   - `flags_on` stays `"none"` until the flag seam exists. Then it is the override the seam reads, each key `flags.py --temporary` prints set on, built by the shell on every run, such as `'FLAG_OVERRIDES=$(' + plugin_py + ' "' + ratchet_plugin + '/scripts/flags.py" --temporary | sed "s/$/=true/" | paste -sd, -)'`.
   - Where an entry point has arrived, bind its rows: for a service, `smoke`, `load-smoke` and `telemetry-e2e` need only a `--url` per route worth overlapping. `load-smoke` takes its `--concurrency` and `--seconds` from the binding's Limits; while the one route is the readiness URL, it runs with `--health-only` in place of `--url`. A gate that requests routes from outside boots through `{{probed}}`, every flag due for removal on; `smoke` and the end-to-end suite judge the defaults. The other types bind those rows as the table says, a `none` with the cell step 3 printed. `{{booted}}` waits for a URL and refuses a `ready` of `"none"`, so a `cli`, `job` or `app` writes its `smoke` as a run of `boot` itself, to its exit. `telemetry_sink.py` wraps any command after `--`, and `--require traces` narrows it to the signals the deliverable emits.

   | Type | `smoke` | `load-smoke`, `load` | `telemetry-e2e` |
   | --- | --- | --- | --- |
   | `web-ui` | `boot` serves the built bundle, `ready` is its first page; then as a service | `none` | one page's end-to-end run, the browser exporting over OTLP/HTTP |
   | `app` | `boot` starts the built app headless; it reaches its first screen and exits 0 | `none` | one end-to-end run of the app |
   | `job` | `boot` runs once on a small fixture input: exit 0 and the expected output | a burst of inputs (messages, files, rows): errors only, then the Limits' rate and latency | one run of the job |
   | `cli` | `boot` with `--version` and with one real command, each exit 0 with the expected output | `none` | `none` |
   | `library` | `none` | `none` | `none` |

   **A gate with nothing to run is red.** No test, benchmark, fuzz target or scenario: the recipe fails and says what is missing, even where the tool's own exit code would pass. A row is bound only once its trigger has fired, so what it judges already has a member.

   **A bite for every gate you bind.**
   - With each recipe, add its `[[bite]]` to `quality/bites.toml` in the form [tiers.md](../method/tiers.md#every-bound-gate-is-shown-to-bite) gives: the smallest fault the gate exists to catch, and the words it prints for it. A collection gate gets a second bite that removes the collection.
   - A row whose question requires a proof by name (`[proofs]` in `application-types.toml`: telemetry emitted, changed flags off and on, load smoke) gets a bite that plants that fault and claims it with `proof = "<name>"`.
   - A shipped service gate bound now needs one, since its row names your `boot`: `smoke` a deliverable that exits, `load-smoke` a route that answers with an error, `telemetry-e2e` a deliverable that exports nothing.
   - A `later:` row needs none.
   - `just bite-one <gate>` proves one gate in a scratch copy. A whole-tree gate's bite narrows it with `args`, and the registry's `setup` holds the build. When the stack compiles, `setup` also fills the compiler cache as [tiers.md](../method/tiers.md#speed) says, before the first bite.
   - A verdict other than `bites` is a defect of the recipe or the fault ([tiers.md](../method/tiers.md#every-bound-gate-is-shown-to-bite)): fix it, never loosen `expect`.
   - A gate you cannot plant a fault for is bound `none`, with its reason and ADR.

   Done when tier 0 and tier 1 are green, every tier 2 gate is green alone, `just hooks-install` has printed `hooks: green` or the owner has the command its `owner:` line names, no `bind:` body is left, `test_runner` runs the test files named after it, `## Test paths` and the four stack sections are filled, each tier 0 and tier 1 gate whose reads you can name has its bullet under `## What each gate reads`, `boot`, `ready` and `service` are filled for every type that boots, every gate you bound has its `[[bite]]`, and the first thresholds are committed or stay as you wrote them while the owner's hooks command waits.

10. **Speed.** Build it in while nothing is slow ([tiers.md](../method/tiers.md#speed)).
    - Turn on the parallel mode of the test runner, provisioned or bound, and of the mutation tool where it is bound, in the committed configuration.
    - Keep the template's `[ceilings.change]` in `quality/thresholds.toml` as it is. Never set it from the adoption's own size.
    - Have each long gate hand `{{shard}}` and `{{shards}}` to its tool, and name it in its tier's `tier1_sharded` or `tier2_sharded` list.
    - Add a gate to its tier's list at the top of the `justfile` and nowhere else: CI reads the same lists.
    - Run `tier0-tree` and `tier1` three times each. A gate red in parallel and green alone shares state, which tiers.md says how to separate.
    - Fill each cache of the CI templates with this stack's caches, and keep their shape. Its `path` names, in place of `.cache`, the dependency directory and every tool cache its job writes: in `deep.yml` the mutation cache and the fuzz corpus too, and for a compiled stack the content-addressed compiler cache outside the tree. Its `key` hashes this stack's lockfiles and tool configs in place of `**/*.lock`.
    - Fill the binding's Speed section, one line per concept, saying how this stack will meet it where nothing is bound yet. Leave `- Shards: 1`.
    - Run `just budget tier0 --set` and `just budget tier1 --set` once. Take those readings on a quiet machine or the CI runner, and vet each with `pstack:benchmark-checklist`. On a shared machine, cap the gates with `RATCHET_JOBS`.

    Done when the parallel modes are on, every CI cache names this stack's paths and lockfiles, the Speed section has no placeholder, three runs of each are green, and `[ceilings.seconds]` has a key per tier.

11. **Records and CI.**
    - Write `docs/design-brief.md` from step 3's answers, dated, and under `## In the owner's words` each question the owner answered here with the answer word for word, after init's answers as `AGENTS.md` and `PRODUCT.md` hold them.
    - Run `mattpocock-skills:domain-modeling` for the ADRs and glossary entries the answers crystallised, and replace `<project>` in `GLOSSARY.md`.
    - **The tree has user-interface surfaces:** run `impeccable document` for `DESIGN.md`.
    - **It has none:** do not create `DESIGN.md`. Write under slice one that the first surface is built with `impeccable shape`, and that an agent runs `impeccable document` once it merges.
    - Write the plugin's version (from `${CLAUDE_PLUGIN_ROOT}/.claude-plugin/plugin.json`) and its commit (`git -C "${CLAUDE_PLUGIN_ROOT}" rev-parse HEAD` when that is a checkout, else the latest commit of `Simon-GitHub-Code/ratchet` that changed that file) and the tree id of that commit (`<commit>^{tree}`; for a copy with no history, the id the template justfile computes) into the binding's `- Ratchet plugin: <version> at <commit> tree <tree id>` line.
    - Write the same commit into every `ref: "<ratchet-sha>"` of `ci.yml`, `deep.yml`, `weekly.yml` and `merge.yml`, so CI and every local tier run the plugin's gates at one version.
    - Replace each `bind: toolchain setup` step with the toolchain setup, pinned by commit SHA with the version in a trailing comment and the setup action's own cache on, so `just pins` stays green. A `bind:` step exits 1, so CI is red while one stands.
    - Where a suite's gate sits in a later tier, replace the `bind:` step for later-tier suites in the `tier1` job of `ci.yml` with the setup that suite needs, under `if: matrix.gate == 'tests-changed'`, as `deep.yml` sets up the whole suite. Delete that step where no suite sits in a later tier.
    - Set the nightly hour in `deep.yml` to one when nobody works on the repository. Cron reads it in UTC.

    Done when the brief is dated and holds the owner's words, the ADRs and glossary entries are written and the glossary names the project, with user-interface surfaces `DESIGN.md` exists, with none there is no `DESIGN.md` and slice one says the first surface is built with `impeccable shape` and that an agent runs `impeccable document` once it merges, the binding names the plugin's version and commit, and the workflows name no placeholder and hold no `bind:` step.

12. **Prove it.**
    - Commit first: the change-size gate knows the adoption by its subjects, and in a bite the change is what the tree holds beyond `HEAD`.
    - Run `conformance` and fix what it names.
    - Run `check` in a fresh worktree of that commit, made as the rehearsal's is below: the working tree has what you installed by hand.

    **Rehearse the first slice.** Show that the first feature finds every config it needs, in a throwaway copy that is never committed.
    1. **Choose the slice.** Slice one is the one step 11's design brief names. When the brief names none, rehearse the smallest slice the declared types imply (one module, the file `boot` runs and the two tests) and say so in the hand-over.
    2. **Make the copy.** Make a worktree of the adoption's commit outside the repository (`git worktree add --detach "${TMPDIR:-/tmp}/rehearsal" HEAD`), as `just merge` does: a fresh checkout, holding nothing untracked, no installed tools, environment, cache or `.env` carried or linked in. Run **no** setup, `quality/bites.toml`'s included, by hand.
    3. **Start with a recipe.** The first command there is a recipe (`just check`), which installs what it needs. One that fails with a tool missing is a recipe that lacks its `install` dependency, and that is the adoption's edit, not the rehearsal's setup.
    4. **Write the slice.** Add with the package manager the runtime packages the stack names for slice one. Then write the smallest real instance of slice one, one small file each:
       - every kind of code it brings, in the part the binding's `## Architecture` table places it and in a form its Triggers row counts (`just triggers --count`);
       - the file `boot` runs;
       - one instance of each stack rule that allows something in one place, such as the one module that reads settings;
       - a unit test and a property test written with the provisioned tools.
    5. **Run it.** Run `just snapshots`, `check`, `just test-files` with the two tests, and `boot`: for a type that serves `eval "$(just --evaluate booted)"`, for a `cli`, `job` or `app` the run step 9's table gives its `smoke`.
    6. **Judge it.** It passes when:
       - `check` is red on nothing but `triggers`;
       - the tests fail or pass on their assertions and not on a configuration or an import;
       - the deliverable answers `ready` or its run exits 0;
       - and `python3 -E -s -B -X pycache_prefix=/nonexistent/ratchet-no-bytecode -P -c 'import sys; sys.path.insert(0, sys.argv.pop(1)); from ratchet_guards import diff; sys.exit(diff.main())' "${CLAUDE_PLUGIN_ROOT}/hooks" --root . --base HEAD` lists no guard path but the dependency manifest, its lockfile and their snapshots.
    7. **Fix in the adoption.** Any other edit the rehearsal needed is a config the adoption owes, or a key it removed: make it in the adoption, commit, run `check`, and rehearse once more in a fresh worktree. Rehearse at most twice: an edit the second round still needs is not chased, but named with its file for the hand-over.

    The rehearsal may reuse that compiler cache and nothing else; it still cannot supply a missing config or tool. Remove each worktree when its round ends.

    Run `check-deep` once. Its last gate is the proof: the `bite-all` entry point plants each fault of `quality/bites.toml` in a scratch copy and is green only when each gate was green without its fault and red with it. Then run `just budget bite-all --set`.

    Done when `check` is green in a fresh worktree of the commit, `conformance` and `check-deep` exit 0, no rehearsal worktree is left, and the rehearsal passed, or its second round ended with the edits it still needs named.

13. **Hand over.**
    - Commit what the proof changed, push and open a pull request (with no forge, the change description at `.scratch/changes/<branch>.md`, the branch's `/` written as `-`) with every template section filled, and under Gates the `tier0` tail and the guard paths the rehearsal changed.
    - Say how to start the product and open it: `just start` and the address it prints, and on an empty repository that it starts once the first feature brings the file `boot` runs.
    - Under Criticism: every `none` row that applies, every tool you could not check, every assumption of step 5, every answer you could not act on, a rehearsal with no slice one named and each edit its second round still needed, the adoption's measured size, and the owner's hooks command, if any, which `just ratchet`, the route and the feature preflight wait for.
    - Say in one line which rows wait for which trigger, and that the route names `/ratchet:safeguard` when one fires. Say also that the first code that runs things at once, reads outside input or moves between named states will owe a formal model, which an agent writes and checks against the code, or a record of why there is none.
    - The merge judge reads the adoption by the gates it brings:
      - **No forge:** `just merge <branch>` merges it when the judge passes it and `check-deep` is green on the merge result.
      - **A forge:** the `merge` workflow arrives with the adoption, so no merge job runs on its own pull request and the owner merges it once.

      The path rules switch on with it, and from then on the `merge` workflow (with no forge, `just merge <branch>`) merges what the judge passes.

    Before the route, say under **You chose** what the architecture round settled: the system and its tools, one plain line each, with what changing it later costs from the record's reversal cost, and "I took my recommendation, as you left it to me" where the owner did.

    Then the **Assumed** block the method's rule on asking describes:
    - what kind of software it is;
    - who reaches it;
    - the tools the round named none for;
    - then step 5's assumptions (the rules every change keeps);
    - and each preference the owner typed, as "you asked for" or, where the rule on preferences kept it from applying, as not applied, with what was asked for, why not and the route that exists.

    One plain line each, with what changing it later costs (what changes, roughly how much work, and whether anything built is redone) and what the owner can say to change it, such as ``Secrets check: <the tool you chose>, current and it reads every file here; <the one you passed over> was the other choice. Changing later: a change of its own that an agent makes while you are away (the other tool installed and set up, the check rewired and shown to catch a planted mistake again, a decision record). It touches protected files: the package list and its lock file, the tool's settings and their saved copy, the list of checks, the list of planted mistakes and the record of which tool runs each check. The agent puts the new settings file on the protected list in place of the old one, and the merge is yours. Nothing built is redone. Say "use <the other>" to switch.``, with the real names in place of the brackets. The block is for the owner: no question key, type name or other method term in it. Then the number of questions the owner answered.

    The last lines are the route: run `python3 -E -s -B -X pycache_prefix=/nonexistent/ratchet-no-bytecode "${CLAUDE_PLUGIN_ROOT}/scripts/route.py" --root .` in one Bash call and print its lines as they are. Close with `/ratchet:next` on its own line.

    Done when what the proof changed is committed, the pull request, or with no forge the change description, has every template section filled, with the `tier0` tail and the guard paths the rehearsal changed under Gates and each item above under Criticism, you have said how to start the product and which rows wait for which trigger, and the reply ends with **You chose**, the **Assumed** block, the number of questions the owner answered and the route's lines, then `/ratchet:next`.
