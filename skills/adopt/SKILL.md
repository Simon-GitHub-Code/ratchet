---
name: adopt
description: "Bring a repository onto the Ratchet method: declare what kind of software it is, choose a current tool for every question that applies, bind each gate against a walking skeleton, and leave no gate that passes with nothing to run."
disable-model-invocation: true
---

# Adopt the Ratchet method

Load the `ratchet:method` skill first; this skill assumes its vocabulary. Templates live in `${CLAUDE_SKILL_DIR}/templates/` and mirror the repository layout; the `justfile` and `.github/` among them are the `just` and GitHub examples, and each template's comments say what its placeholders need.

The method ships the questions, its own gates and three gates for any service, and no tool for any stack: the application types decide which questions apply, and you choose the tool behind each one now, from what is current. [tiers.md](../method/tiers.md) holds the questions, the types, what a recipe must get right, and speed.

## What the owner is asked

Two dialogs, one round each, through the question tool, and nothing else. A question is for a choice that leads to a different product or cannot be taken back; a reversible choice is yours to make and list in the hand-over. What a record says is never asked. An answer you cannot act on goes into the hand-over. A question whose key `## Standing answers` in `AGENTS.md` lists is not asked, and its answer there is taken. Record each answer in one Bash call: `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/questions.py" record --skill adopt --key <key> --kind <kind> --recommended <yes|no> --root .`. The keys are `adopt.types` (`direction`), `adopt.tools` (`tooling`) and `adopt.brief.<n>` (`direction`, or `irreversible` for a decision hard to reverse).

`/ratchet:adopt interactive` asks four dialogs instead, one per topic: the application types, the tools, the assumptions printed under step 4's table, and the design brief, followed by the rounds of `mattpocock-skills:grilling`. Say the count before the first.

**Dialog 1, what this is and what judges it.** Print the table of step 4, then ask two questions. First, the application types: offer the types the tree and the records point to, recommended first, each with its evidence in a clause; the answer decides which questions the method asks of every later change. Second, the tools: accept the proposal, or name the ones you want for any row.

**Dialog 2, the design brief.** Draft the brief from the records and the code, and ask up to four questions no record answers: slice one as checkable items, the out-list, and at most two decisions hard to reverse. A deeper interview is the owner's `/mattpocock-skills:grill-with-docs`; adoption does not wait for it.

## What it costs

Adoption installs every tool, runs every gate, plants each gate's fault in a scratch copy, and runs tier 2 in full once, in step 13: the fuzz run (30 seconds a target by default, so seconds on a skeleton) plus the full mutation run. Tell the owner before step 1; it is not a question.

## Steps

1. **Check the ground.** Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/route.py" --root . --offline` in one Bash call. When it names `/ratchet:init` or `/mattpocock-skills:setup-matt-pocock-skills`, print its lines and stop: both are the owner's keystrokes, and adoption needs what they write. Then check that later steps can run: `git` 2.36 or later; `python3` 3.11 or later; `just`, or the runner the repository uses; the `pre-commit` framework (`pre-commit` or `uvx pre-commit`); with an `origin` on GitHub, `gh` with `gh auth status` green; and `claude plugin list` showing `ratchet`, `mattpocock-skills`, `pstack` and `impeccable`. Commit the tracker setup's uncommitted records, if any, on the default branch as `chore(tracker): record the issue tracker`. Done when all hold, or you have named what is missing and stopped.

2. **Copy the templates.** On a new branch, every commit's subject starts `chore(method): adopt`: the `commits` gate accepts it, and the change-size gate exempts a range made only of such commits and prints the size instead. Copy every file under `templates/`, dotfiles included, to the same path, skipping any that exists, and append to `.gitignore` the lines of the init skill's `templates/gitignore` it lacks. Done when both listings agree and the skipped files are noted for the hand-over.

3. **Recognise the repository.** Read `## Stack` from `PRODUCT.md`, else from `AGENTS.md`, then the tree: the language, the package manager and its lockfile, the task runner, the forge (`git remote`), what the deliverable is and how it starts. Choose the types to recommend and run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/application_types.py" <type>...`: it prints each question the types narrow, with `applies` or the `none: not a <type>` cell to write; every other question applies. Done when you can name the stack, runner, forge, types and the rows they leave out.

4. **Choose a tool for every row that applies.** One Gates row at a time.

   - **What is there wins**, unless you give the reason to replace it.
   - **Look it up, now.** What you remember of a tool is a conjecture. Check its current documentation (Context7 or a web search) that it exists, is maintained and answers the row; say which rows you could not check. Versions come from the registry through the package manager's add command, never from memory, and never from this plugin.
   - **Prefer** one tool for several rows; a tool the package manager locks; a tool that fails when it finds nothing to run; a tool that uses every core and can run one shard of several.
   - **`none` is rare.** For a row that applies, `none` is for a property the project will never have, with the reason and an ADR you write. A row whose tool needs code that does not exist yet is bound now, against the walking skeleton: a row left for later is a guard edit the first feature cannot make.
   - **Already bound.** The method's own gates, and for a service `smoke`, `load-smoke` and `telemetry-e2e`, arrive bound to the plugin's scripts.

   Print one table: each row that applies with its tier, the tool and where you checked it; the rows left out, on one line; and under it what you assume without asking, with reasons: the layers and their direction, the non-negotiables and their gates, the telemetry and flag seams, and who may turn a flag on in production (the owner, unless a record says otherwise). Done when every row that applies has a tool or a reasoned `none`.

5. **Ask dialog 1**, then act on the answers with no further question. Other types: rerun step 3's command and choose tools for the rows that now apply. A named tool that cannot answer its row: bind your proposal and carry the difference into the hand-over. Done when the types and the tools are settled.

6. **Runner and forge.** Fill the binding's Runner and Forge sections, the default branch included. Another runner, another forge, or no `origin`: do what [runners-and-forges.md](runners-and-forges.md) says first. Done when every entry point names a command and the Forge section has no placeholder.

7. **Write the binding and `AGENTS.md`.** In `docs/process/binding.md`: the `- Application types:` line, and every row's tool, threshold source and entry point, or its `none: not a <type>` cell; a row is never deleted. Write the ADR for the tools under the next free number, with the reason for each `none` on a row that applies, which those cells cite. Fill every other section but Speed, which step 11 fills. In `AGENTS.md`, from step 4's assumptions: the layers with their allowed direction (or delete the section), the non-negotiables with the ADR and the gate behind each, the stack rules the method leaves open. Done when no Tool cell is empty and neither file holds an angle-bracket placeholder outside Speed.

8. **Fill the manifest, then generate the permission rules.** Add this stack's tool configs (the dependency manifest and its lockfile among them), fitness tests and gate tools to `guards/manifest.toml`, so no group has an empty `paths`; a path may be listed before its file exists. Bind the three `[[door]]` tables, the one-way doors the merge judge sends to the owner; a door with no path here keeps `paths = []` and says why in `none = "<reason>"`. Run `just settings`, which writes the `deny` and `ask` lists of `.claude/settings.json` from the manifest; never edit them by hand. Done when `just settings-check` is green.

9. **Build the walking skeleton.** With no code yet, write the thinnest deliverable every gate can run against, so the first feature changes no gate: the package with its `core` path; one entry point registered through the telemetry seam, and the test that reads its span from an in-memory exporter; the flag seam with its override and the empty `flags.toml`; one end-to-end test; the contract snapshot beside the tests when the stack publishes one; the container file when the deliverable is an image; and one of each thing a gate is red without: a test, a benchmark, a fuzz target, a documentation example, and where the types ask for them a load scenario and an accessibility check. A repository with code binds the same rows against what exists.

   For a service, fill `boot`, `ready` and `service` at the top of the `justfile`: `smoke`, `load-smoke` and `telemetry-e2e` then need nothing else, beyond a `--url` per route worth overlapping. The boot command builds what it runs and stays in the foreground; raise `boot_timeout` when the build is slow, and write `{port}` where the port goes. The other types bind those rows as the table says, a `none` with the cell step 3 printed; `telemetry_sink.py` wraps any command after `--`. Set `boot`, `ready` and `service` to `"none"` where no row uses them.

   | Type | `smoke` | `load-smoke`, `load` | `telemetry-e2e` |
   | --- | --- | --- | --- |
   | `web-ui` | `boot` serves the built bundle, `ready` is its first page; then as a service | `none` | one page's end-to-end run, the browser exporting over OTLP/HTTP |
   | `app` | the built app starts headless, reaches its first screen and exits 0 | `none` | one end-to-end run of the app |
   | `job` | one run on a small fixture input: exit 0 and the expected output | a burst of inputs (messages, files, rows): errors only, then the Limits' rate and latency | one run of the job |
   | `cli` | `--version` and one real command, each exit 0 with the expected output | `none` | `none` |
   | `library` | `none` | `none` | `none` |

   Done when the skeleton exists and the deliverable runs as its types say.

10. **Bind the entry points, tier 0 first, and show each one bites.** Install each tool through the package manager, add what the tools write to `.gitignore`, and replace each `bind:` body in the `justfile` with the real command; a row bound `none` becomes `@echo "none: <reason>"`. Read [what a recipe must get right](../method/tiers.md#what-a-recipe-must-get-right) before the first recipe.

    **A gate with nothing to run is red.** No test, benchmark, fuzz target or scenario: the recipe fails and says what is missing, even where the tool's own exit code would pass.

    **A bite for every gate you bind.** With each recipe, add its `[[bite]]` to `quality/bites.toml` as its header says: the smallest fault the gate exists to catch, and the words it prints for it; a collection gate gets a second bite that removes the collection. A row whose question requires a proof by name (`[proofs]` in `application-types.toml`: telemetry emitted, changed flags off and on, load smoke) gets a bite that plants that fault and claims it with `proof = "<name>"`. `just bite-one <gate>` proves one gate in a scratch copy; a whole-tree gate's bite narrows it with `args`, and the registry's `setup` holds the build. A verdict other than `bites` is a defect of the recipe or the fault ([tiers.md](../method/tiers.md#every-bound-gate-is-shown-to-bite)): fix it, never loosen `expect`. A gate you cannot plant a fault for is bound `none`, with its reason and ADR.

    **In order.** Run `just snapshots` once the tool configs exist. Bind tier 0 until `just tier0` is green. Then install the commit hooks: run `just hooks-install` in one Bash call. It prints `hooks: green` once a commit here runs tier 0. An `owner:` line instead names a change of git config, which the guard refuses from an agent: give the owner that one command now, not at the hand-over, and go on. Then bind tier 1 until `just tier1` is green, each gate over the change through `{{changed}}`, which judges nothing on the adoption, and each tier 2 gate until it is green alone; a tier 2 recipe red on the skeleton is fixed now. Run `just ratchet` and commit the first thresholds it writes; while the hooks wait for the owner it is red, and the thresholds stay as you wrote them. Done when tier 0 and tier 1 are green, every tier 2 gate is green alone, and no `bind:` body is left.

11. **Speed.** Build it in while nothing is slow ([tiers.md](../method/tiers.md#speed)). Turn the parallel mode of the test runner and of the mutation tool on in the committed configuration. Have each long gate hand `{{shard}}` and `{{shards}}` to its tool. Add a gate to its tier's list at the top of the `justfile` and nowhere else: CI reads the same lists. Run `tier0-tree` and `tier1` three times each; a gate red in parallel and green alone shares state, which tiers.md says how to separate. Fill each `bind:` cache line of the CI templates with this stack's caches, keyed on its lockfiles and tool configs, and keep their shape. Fill the binding's Speed section, one line per concept; leave `- Shards: 1`. Run `just budget tier0 --set` and `just budget tier1 --set` once. Take those readings, and the `perf` and `load` numbers of the Limits, on a quiet machine or the CI runner, and vet each with `pstack:benchmark-checklist`; on a shared machine cap the gates with `RATCHET_JOBS`. Done when the Speed section has no placeholder, three runs of each are green, and `[ceilings.seconds]` has a key per tier.

12. **Records and CI.** Ask dialog 2 and write `docs/design-brief.md` from its answers, dated (`mattpocock-skills:grilling` runs only in `interactive` mode). Run `mattpocock-skills:domain-modeling` for the ADRs and glossary entries the answers crystallised, and replace `<project>` in `GLOSSARY.md`. When the tree has user-interface surfaces, run `impeccable document` for `DESIGN.md`. When it has none, do not create `DESIGN.md`: write under slice one that the first surface is built with `impeccable shape`, and that an agent runs `impeccable document` once it merges.

    Write the plugin's version (from `${CLAUDE_PLUGIN_ROOT}/.claude-plugin/plugin.json`) and its commit (`git -C "${CLAUDE_PLUGIN_ROOT}" rev-parse HEAD` when that is a checkout, else the latest commit of `Simon-GitHub-Code/ratchet` that changed that file) into the binding's `- Ratchet plugin:` line, and the same commit into every `ref: "<ratchet-sha>"` of `ci.yml`, `deep.yml`, `weekly.yml` and `merge.yml`, so CI and every local tier run the plugin's gates at one version, which `just plugin-pin` and the conformance gate hold. Add the toolchain setup, pinned by commit SHA with the version in a trailing comment, so `just pins` stays green, and set the nightly hour in `deep.yml`. Done when the brief is dated, the glossary names the project, the binding names the plugin's version and commit, and the workflows name no placeholder.

13. **Prove it.** Commit first: the change-size gate knows the adoption by its subjects, and in a bite the change is what the tree holds beyond `HEAD`. Run `conformance` and fix what it names. Run `check`, then `check-deep`, once. Its last gate is the proof: the `bite-all` entry point plants each fault of `quality/bites.toml` in a scratch copy and is green only when each gate was green without its fault and red with it. Done when `conformance` and `check-deep` exit 0.

14. **Hand over.** Commit what the proof changed, push and open a pull request (with no forge, the change description at `.scratch/changes/<branch>.md`) with every template section filled and the `tier0` tail under Gates. Under Criticism: every `none` row that applies, every tool you could not check, every assumption of step 4, every answer you could not act on, the adoption's measured size, and the owner's hooks command, if any, which `just ratchet`, the route and the feature preflight wait for. The adoption changes guard paths, so its merge is the owner's; the path rules switch on with it, and from then on the `merge` workflow (with no forge, `just merge <branch>`) merges what the judge passes.

    The last lines are the route: run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/route.py" --root .` in one Bash call and print its lines as they are. Close with `/ratchet:next` on its own line.
