# Changelog

## Unreleased

An upgrade is one command (ADR 0029). `scripts/upgrade.py --apply` makes every change the plugin can derive from its templates and the repository's own binding, and the report it then prints is the rest of the upgrade, each item marked for an agent or for the owner. An upgrade changes guard paths, so the merge judge leaves the change to the owner: the owner's one act is the merge.

### `upgrade.py --apply`

- Every conformance finding names who fixes it: `auto` (the script), `agent` (needs judgement; printed with what to do and the template text to start from) or `owner` (a product or risk call; printed as a question with what the repository shows and what each answer leads to). The finding type has no default for it, so a check without one does not type-check. The kind belongs to the finding: a missing recipe is `auto` when it is entirely the plugin's and `agent` when it needs a list only the project can write.
- `--apply` moves the binding's pin line to the plugin in use and writes the `auto` items: every workflow `ref` that checks the plugin out, at the pin's commit; the permission rules the plugin generates in `.claude/settings.json`; the Entry points rows the binding lacks, when every row runs `just`; each missing recipe that is entirely the plugin's and whose variables and dependencies the `justfile` already defines, at the end of the file; `plugin-pin` before each tier and `hooks-installed` before `ratchet`; the `## Asking the owner` section of `AGENTS.md`; a missing `flags.toml` or `quality/bites.toml`; the missing sections of the change template; the `.gitkeep` of `docs/disputes` and `docs/features`; and `merge.yml` and `weekly.yml` where a forge lacks them.
- The dry run is the apply, on a copy. `upgrade.py` copies what git sees to a temporary directory, moves the pin there and runs every `auto` edit until none is left, then prints the plan. `--apply` writes the result to the repository, each file whole through a rename and the binding last. A run stopped at any write, rerun, ends where one whole run ends, and a second run changes nothing. The pin, the refs and the permission rules are written by one run, so CI is never between two versions.
- It never loosens and never guesses. It adds what is missing and replaces only what the plugin owns: the pin line, the refs and the generated permission rules. A permission rule no version of the plugin writes is kept and becomes a question for the owner. A workflow or recipe that holds the project's own lines is left alone and becomes an `agent` item. Nothing under `guards/` is written. New Gates rows, the application types and the Triggers table are `agent` items; `- Life events:` and the one-way doors are questions for the owner.
- It refuses, and writes nothing, when the plugin in use is older than the pin, when the order cannot be told, when the plugin carries no commit to pin, when the repository is not an adopted git work tree, or when a file it would write leads out of the repository through a link. A rule that does not converge on the copy is a bug in the plugin: exit 3, and nothing written.
- Exit status: 0 when nothing is left to write or to do, 1 while edits are unwritten or `agent` or `owner` items remain, 2 on a refusal, 3 on a plugin bug. `--plugin` is gone: the plugin is the one the script runs from, so the pin and the templates it copies are always the same version.

### The report is the whole upgrade

- A binding without `- Application types:` still lists the Gates rows it lacks, a repository without `quality/bites.toml` still lists each gate that needs a bite, and the tier-list variables are asked for wherever `ci.yml` or `deep.yml` exists, not only once it reads them. The questions the application-types catalogue asks are no longer asked a second time by the operability check.
- A `# bind: toolchain setup` line with nothing bound under it is a conformance finding, so a created `weekly.yml` cannot fail its first run unreported.
- What only an answer can show is named in that answer's item: the Triggers rows each application type asks for, the rows each life event makes owed, and the bite a newly bound gate needs.
- After `--apply` the report names only `agent` and `owner` items, the same ones the dry run named.
- `just conformance` says how many of its findings `upgrade.py --apply` makes.

### The permission rules follow the pin

- `just settings` (`settings_rules.py --write`) refuses under a plugin other than the one CI runs: the binding's pin and the commit the workflows check out. Two commits between them, a half-done upgrade, refuse too. Rules written by a newer plugin are certain to be red in CI on the pinned commit, which is how a repository's main went red mid-upgrade. The refusal names `upgrade.py --apply`, which moves the pin, the refs and the rules together, and `RATCHET_PLUGIN` set to a checkout of the pin.
- `--write` keeps a rule no version of the plugin writes, where it used to drop it. `--check` still reports it.

### Upgrading

- From any earlier adoption: update the plugin, then run `python3 <plugin>/scripts/upgrade.py` in the repository to see the plan, and `--apply` to write it. Do the `agent` items it prints, answer the `owner` questions, run `--apply` again (an answer can make more of it decidable), and open one change. The steps under the earlier "Upgrading" sections say what changed between those versions; `upgrade.py` makes or lists each one that a gate checks.

## 0.11.0

A safeguard arrives with the first thing it guards, and the first one is free (ADR 0028). The method is for hobby and solo projects first, and it must never stop the owner in the middle of an idea. Telemetry, flags, security, load and the new reliability questions are no longer asked of every repository from the first commit. Each question carries a trigger, and is asked once that trigger fires.

### When a question is asked

- Every question in `skills/method/application-types.toml` carries a trigger. `always` is the floor: format, lint, strict types, secrets, commit subjects, the guards, the method's own gates and the tests that exist, asked from the first commit. `code:<kind>` is asked at the first instance of a kind of code: an entry point, an outbound call, a listener, a configuration read, a stored format, a user interface and the other kinds the catalogue lists. `life:<event>` is asked once the owner declares `deployed`, `irreplaceable-data` or `depended-on` on the binding's new `- Life events:` line.
- A row whose trigger has not fired is bound `later: <trigger>` and judges nothing. A row is bound only after its trigger has fired, so "a gate with nothing to run is red" now reads "a gate once asked is red with nothing to run".
- The first one is free. The change that brings the first file of a kind is never red for it (however many instances that file holds; instances in two files are red), and names the rows it made owed. A later change that adds another instance while a row is still owed is red. A new tier 1 gate, `scripts/triggers.py`, counts instances at the merge base and at head by the binding's new `## Triggers` table.
- `/ratchet:safeguard` binds an owed row: an agent chooses the tool, writes the recipe and the fault, and binds the row in a change of its own. The route names it before the next feature. It proposes a life event when it sees a sign of one, and declaring it stays the owner's.
- Adoption no longer builds a walking skeleton. It binds the floor and marks every other row with its trigger.
- `skills/method/tiers.md` replaces "Operable from the first commit" with a Triggers section: every question with its tier and its trigger, grouped, and why each group waits for its trigger. Commitment 11 is now "Operable by the time it matters".

### The route, `/ratchet:safeguard` and adoption

- `/ratchet:safeguard` binds every row `triggers.py --json` says is owed. It proposes a current tool per row and asks one question for all of them, writes each recipe in place of its `later:` body, shows each gate bite with `just bite-one`, records the tools in a new ADR, and opens one change, `chore(method): safeguard <what>`. `/ratchet:safeguard <event>` declares the life event first.
- The route names `/ratchet:safeguard` and what is owed after anything that stops work outright and before the records and the tickets. The `why:` line ends with the step it would otherwise name, so the owner can carry on: the first file that brings the kind is free.
- A sign of an undeclared life event (a `signs` glob of the catalogue) is an `also:` line naming the sign and the event's meaning. `route.py --decline <event>` records the signs seen in `.ratchet/events.tsv`; the line comes back only for a new sign.
- `triggers.py --count` prints what each Triggers row finds, so a pattern is proved on the tree before the gate trusts it.
- Adoption writes the Triggers table, binds the floor and the rows already owed, and writes every other row `later: <trigger>` with a recipe that echoes it. Dialog 1 asks the life events, `none` by default.
- The drain counts four agents a ticket before `depended-on` (`6N - 2`) and nine after (`11N - 2`). A run red on the `triggers` gate is parked with a reason that names `/ratchet:safeguard`, and held only while that kind is owed.
- The template's `graceful-stop` runs `load_smoke.py --until-stopped` inside `with_service.py --graceful`, and `prod-probe` runs `load_smoke.py --ready` against the live deployment. `rollback`'s comment names its trigger, `life:deployed`.

### Seven reliability questions

- `rolls back safely` (`life:deployed`, tier 1): the merge base's build runs on the data the head's migrations leave.
- `survives failing dependencies` (`code:outbound-call`, tier 1): every outbound call has a timeout, and a test meets the dependency erroring, slow and gone.
- `refuses bad configuration` (`code:config-read`, tier 1): `with_service.py --refuses NAME` boots with the setting missing and expects a non-zero exit that names it.
- `shuts down gracefully` (`code:listener`, tier 1): `with_service.py --graceful SECONDS` signals it mid-request and expects the request finished and the process gone in time.
- `incidents tighten a gate` (`always`, tier 1): each record under `docs/incidents/` has a `Bite:` line naming a bite of `quality/bites.toml` that reproduces it. `records.md` gains the incident record, blameless, which the agent proposes when the owner reports an incident.
- `backup restores` (`life:irreplaceable-data`, weekly): the latest backup restores into scratch and the deliverable boots on it.
- `production answers` (`life:deployed`, weekly and after a deploy): the load smoke against the live URL.

### What waits for a life event

- A one-way door waits for the owner only once the project is `deployed` or holds `irreplaceable-data`.
- New surface with no flag waits for the owner only once the project is `deployed`.
- The pipeline's critic and its fix loop run only once the project is `depended-on`. Before that a ticket is built by spec, implement and review, and the merge rule is unchanged.
- Guard paths, disputes and the ratchet hold from the first commit. The merge judge reads the life events at the merge base, so a change cannot loosen its own judge.

ADR 0028 supersedes, in part, ADR 0012's "from the first commit" and its walking skeleton, and ADR 0020's red gate with nothing to run before its trigger fires.

### Upgrading a 0.10 adoption

- Update the plugin and its pin line, and run `scripts/upgrade.py` for the edits it names.
- Add `- Life events:` under `## Gates` in `docs/process/binding.md`, and declare what is already true: `deployed` if others can reach the project, `irreplaceable-data` if it holds data you could not recreate, `depended-on` if people outside rely on it. A binding with no `- Life events:` line counts as declaring every event, so nothing is switched off by omission; `none` turns off the owner's review of one-way doors and unflagged surface and the pipeline's critic, so write `none` only for a project that is not live. Leaving one event out turns off what it guards for a project that has it.
- Add the `## Triggers` table from the template, one row per kind your code has, saying what marks an instance in this stack.
- Add the seven new rows to the Gates table. Each one whose trigger the code already fires is owed: bind it with `/ratchet:safeguard` before the next change that adds another instance. The rest are `later: <trigger>`.
- Rows you already bound stay bound. Nothing here unbinds a gate, and the ratchet does not loosen.

## 0.10.1

Fixes for the eleven issues found while dogfooding 0.10.0 (#20 to #31).

### The pipeline

- The spec and critic roles, whose trees are meant to be red, run `tier0` and only the tests they wrote, not `check`. Implement, fix and review still run `check`, and a review whose own `check` is red forces `request_changes`. (#24)
- A blocker or major finding whose kept test passes, because the code already holds, is resolved by that test: no fix round runs for it, and the review still reads it. (#25)
- The critic keeps its tests in one flat directory, apart from the unit tests: the one the binding's new optional `## Critique tests` section names, else `tests/critique/`, as `<slug>.<layer>.critique.test.<ext>`. (#22)
- The spec estimates the whole change, the critic's tests and the records included, against the change-size ceiling, and stops `blocked` with a proposed split when the ticket cannot fit. (#21)

### Gates

- `guard-diff` reads `pnpm-lock.yaml` (lockfile version 9), so a dependency the spec names and `pnpm add` writes is dependency-only. (#21)
- The change-size gate no longer counts the lockfiles `guard-diff` reads, their copies under `quality/snapshots/`, or dispute records under `docs/disputes/`, and counts a moved file by the lines changed in it. (#21, #22)
- `with_service.py` boots once more on a new port when the service exits before answering on a port it picked, and says so. An early exit prints the first and the last lines the service wrote, so its cause stays in view. (#29, #30)

### Guard

- A `cd` to a directory held in a variable set earlier on the same line (`S=/repo; cd $S && …`) is read as that directory. A relative write that could hit a guard path after a `cd` the guard cannot read is asked of the owner. A shell function that redefines `cd` makes the line unreadable. (#31, ADR 0024)

### Skills and route

- `/ratchet:adopt` no longer copies `.claude/settings.json` from the templates; `just settings` writes it last, once the manifest is filled, so its rules no longer lock the manifest the adoption must fill. (#20)
- `drain.py` and the feature preflight resolve a local ticket by the number its file name starts with, and refuse a number two files share, naming both. (#26)
- `/ratchet:feature` shortens a settling commit subject to 72 characters by dropping whole words from the end of the slug. (#27)
- The route sends a local spec that states no conjecture or operability to `/ratchet:conjecture <spec>` before `to-tickets`, so its tickets inherit both and none is red at the preflight. (#28)

### Upgrading a 0.10.0 adoption

Update the plugin and its pin line. Nothing else is required. To place the critic's tests elsewhere than `tests/critique/`, add a `## Critique tests` section to `docs/process/binding.md` from the template, and make sure the test runner includes that directory.

## 0.10.0

A change the gates can judge now merges without the owner, and the owner is asked only what shapes the product or cannot be taken back. Ratchet ships the questions the method asks of each application type and no tool for any stack, and an adopted repository proves that each gate it binds can go red. The ADR numbers below are the plugin's own, in `docs/adr/`.

### Licence

- Ratchet is released under the MIT licence.

### Merging

- A pull request merges without the owner when its gates are green, the review approves, no blocker or major finding is open, no guard path changed other than by an addition to a record or a tightened threshold, it goes through no one-way door and tier 2 is not red on the default branch. Everything else waits for the owner, with the reasons written on the pull request. (ADR 0021)
- `scripts/merge_ready.py`, behind the new `merge-ready` entry point, is the judge. It reads commits only and prints `merge: agent` or `merge: owner` with one reason per line.
- On GitHub the new `merge.yml` workflow runs after `ci` from the default branch and merges each pull request the judge passes. With no forge, the new `merge <branch>` entry point judges the branch, runs `check` on the merge result and merges it. When the judge leaves the merge to the owner, it prints the command the owner types and the `check-deep` to run after it.
- One-way doors are `[[door]]` tables in `guards/manifest.toml`, of three kinds: `change`, `rewrite` and `removal`. A door guards nothing; only the judge reads it.
- The guard hook asks on `git merge`, `git cherry-pick` and a pull of another branch on the default branch, and refuses any command that moves, deletes or re-points the default branch's ref. (ADR 0016, ADR 0021)

### Records and questions

- An agent may add a glossary term, an `_Avoid_` item or a new ADR without asking. Changing or removing what the default branch holds still asks the owner. A guard group marked `edit = "append"` gets this treatment, and `guard-diff --fail-unless-additive` enforces it. The pull request lists every addition. (ADR 0014)
- An open question is `direction` (the options are different products) or `irreversible` (a wrong guess loses data, fixes a public contract, spends money or weakens a security rule). Everything else is assumed, given a refuter and listed in the pull request. Questions are asked after the work, and each answer is written on the ticket under `## Answers`. (ADR 0013, ADR 0022)
- `/ratchet:conjecture` no longer asks the owner to accept its draft.
- `scripts/questions.py` logs each answer in `.ratchet/questions.tsv`. When a question's last three answers took the recommendation, the route proposes a standing answer for `AGENTS.md`. It never proposes one for a `direction` question. (ADR 0022)
- Questions arrive as dialogs. The stance in every session and the `AGENTS.md` template tell agents to ask through the question tool, one decision per question with the recommended option first, whichever plugin's skill is asking. An existing adoption gets the stance from the plugin, and the conformance gate names the `## Asking the owner` section while `AGENTS.md` lacks it.

### The drain

- `/ratchet:feature` with no ticket builds every agent-ready ticket, each in its own git work tree, branch and pull request, at most three at once by default. Changes merge one at a time: a branch the default branch moved under is rebased and reviewed again. A ticket that cannot go on is parked and the rest continue. The run ends in one report of what merged, what waits and why, and every assumption made. `scripts/drain.py` holds the steps that need no judgement. (ADR 0023)
- The fix loop also runs for a finding whose test the critic kept failing, whatever its severity: such a test leaves `check` red, and the review could only refuse it.
- A change with no room left under the change-size ceiling stops early. The spec and implement roles report the gate's two numbers, and a change over two thirds of the ceiling ends `blocked` as too large, for the owner to split, before the stages that could only push it over.
- The orchestrator commits everything an approved run left in the work tree, not only the files the roles listed, and names an added term without quoting its _Avoid_ words.
- The change-size gate no longer counts the method's own records of a change (the spec and verdict record under `docs/features/`, tickets and change descriptions under `.scratch/`), which the pipeline writes after the review.

### Gates

- A gate with nothing to run is red on every stack. `none` is only for a property the project will never have. (ADR 0020)
- `scripts/hooks_installed.py` runs the commit hooks git would run. The feature preflight, the route and the template `ratchet` recipe are red until a commit would run tier 0, and print the repair. The new `hooks-install` entry point (`scripts/hooks_install.py`) installs the repository's own hooks from an agent, under a global `core.hooksPath` too, with no change of git config; a change of git config stays the owner's one command. The probe waits two minutes for a hook, not 20 seconds, and a hook that has not finished says a busy machine is the usual cause. A `buildable` ticket in `drain.py plan` says when the checkout is not fit to build on. (ADR 0015, ADR 0026)
- Every repository is asked about telemetry, feature flags, security testing and load testing, split across the tiers by cost. New rows include `feature flags declared`, `telemetry emitted`, `load smoke`, `configuration hardening`, `active security scan`, `load` and `telemetry arrives`. Flags are declared in `flags.toml`, checked by the shipped `scripts/flags.py`. (ADR 0012)
- Three shipped gates work for any service, in any language: `with_service.py` boots it and is the `smoke` gate, `load_smoke.py` sends concurrent requests and judges errors only (`load-smoke`), and `telemetry_sink.py` is an OTLP/HTTP receiver that is green only when each required signal arrived (`telemetry-e2e`). (ADR 0012)
- Every gate a repository binds to a tool of its own is shown to bite. `quality/bites.toml` holds a fault for each such gate, and an emptied collection for each gate that judges one. `scripts/project_bite.py` plants each fault in a scratch copy and requires the gate green without it and red with it. The tier 1 `bite` runs only when a change touches a gate; the tier 2 `bite-all` runs every bite. (ADR 0020)
- A stryker survivor's identity now ends in its place inside its unit, and a register entry excuses one mutant. An entry in the old form is red and names the entries to paste. (ADR 0018)
- New surface ships behind a flag or goes to the owner. The judge gives the reason `unflagged` when a change adds to a contract artefact or public-surface snapshot and declares no flag that is off, for the application types the flag question is asked of. (ADR 0025)
- A question may require a proof by name. `application-types.toml` lists three (`outside-the-seam` for telemetry, `broken-flag-state` for flags, `erroring-route` for the load smoke), a `[[bite]]` claims one with `proof`, and conformance is red while a bound row lacks one. (ADR 0025)
- `load_smoke.py` is red when every URL it requests is the readiness URL. A skeleton with no other route passes `--health-only`, and the smoke says so on every run.
- `mutation_survivors.py` judges any mutation tool through `--tool NAME --mutants FILE`, one JSON record per mutant. mutmut and Stryker output is still read directly.
- `coverage_groups.py` reads LCOV as well as Cobertura, and counts a Cobertura line once where a report repeats it under `<methods>`. A ratcheted group measured on such a report may read slightly lower on its next run.
- The binding's `- Commit hooks:` line may name a hook mechanism other than the pre-commit framework. The probe of `hooks_installed.py` is still the proof.
- A gate over the change runs through the new `scripts/changed.py`, which hands it the base and judges nothing on the adoption, the change whose base holds no binding; `bite --changed` proves nothing there, and in a scratch copy of the bite proof the change is the planted fault. `mutation_survivors.py` takes the status `unviable`, counts it apart and is red when no mutant was tested. `parallel.py` takes `--alone` for gates that judge elapsed time, `RATCHET_JOBS` caps the gates at once and the bite proof's scratch copies alike, and the runner and the bite proof end what a gate left running. The first scratch copy is set up alone. The drain ends any process whose program is a file of a work tree it removes, and prints its id: a service an agent started by hand is in no gate's process group.
- A gate that requests the deliverable's routes from outside (interface conformance, the scans, the load smoke and the load test) boots it with every flag due for removal on: `flags.py --temporary` prints those keys, and the template's `flags_on` and `probed` hand them to the flag seam's override. `smoke`, the end-to-end suite and the off half of `flag-states` judge the defaults. A scan is fed the contract's routes and its bite plants the fault on one of them. Adopt names the bite each shipped service gate needs.
- The guard asks once for a call that gives local tickets the agent-ready status, and names each: a command line or a patch that names the label and writes a ticket now asks, where a shell write passed unseen. `/ratchet:conjecture` writes an `## Operability` block (flag, signal, load, security surface) and the feature preflight refuses a ticket that leaves out a line its application types are asked. A line counts as unfilled only when it is blank, a bare `none`, the template's own words or nothing but `<...>` placeholders, so real text that quotes angle brackets passes, and the red says which of these it found. The fork test has one wording in `pipeline.md`, which the Spec role's prompt carries and the conjecture skill cites.
- The Antigravity page names `RATCHET_PLUGIN` and the plugin root as the other five do, and every list of harnesses names all six. A service that exits before it answers now reports the command as it ran, where, for how long, and that it printed nothing when it did. A pinned image that fetches at run time is not pinned.
- The weekly gates run: a new `weekly` entry point and `weekly.yml` workflow, where a red run opens one issue or comments on the open one.
- The weekly issue holds the output of each red gate, read from the runner's log by the new `scripts/red_gates.py`, where it held the last 60 lines of the whole log, which the green gates' output could fill. A log it cannot read as a `parallel.py` log still gets those 60 lines.
- A ceiling reports on a pull request and blocks on the default branch and nightly (`budget.py --over report`), and CI holds each gate's job to its tier's ceiling (`--tier`).
- Local tiers and CI run the plugin at one commit. The binding's `- Ratchet plugin: <version> at <commit>` line is the pin, every workflow `ref` must equal it, and `scripts/plugin_pin.py` runs at the start of each tier. It is red when the plugin in use is older than the pin, or another commit of the same version. A newer plugin is green and says on every run that the tier judges by its gates and CI by the pinned one's, with the line to change; the route offers the upgrade, and the newer plugin's conformance gate is red while the repository lacks something it needs. (ADR 0027)
- The conformance gate names a tier list the CI templates read and the `justfile` lacks: `tier1_gates`, `tier1_sharded`, `tier2_gates` or `tier2_sharded`, each with the template's line to paste. Without them the `shape` job fails with "does not contain variable".
- `scripts/upgrade.py` reports an upgrade: what this plugin version needs that the repository lacks, file by file, the pin first, with the text to add and who may edit each file. It reads the conformance gate's rules and changes nothing.
- The conformance gate says what to add. A missing entry point names the template's row, and the recipe when the `justfile` lacks it. A missing pin prints the line for the plugin in use. It now also names the `## Asking the owner` section of `AGENTS.md`, and in a `justfile` the `changed` wrapper, `RATCHET_JOBS`, `--alone`, the temporary-flags boot and `hooks-installed` at the top of `ratchet`.
- Fuzz time follows the code. The template `fuzz` recipe takes `each` (seconds a target, 30) and `cap` (seconds in total, 600) in place of one fixed 600 second box, so tier 2 on a new project takes seconds. An adoption with `fuzz seconds="600"` keeps working; rebind it to the two parameters to get the saving.

### Guard hardening

- The hook reads a ref name, a repository and a sync the way git does. It refuses a push or fetch that deletes or re-points the default branch under any spelling git resolves, a tag named as a default branch, and a `git` aimed at another repository through `--git-dir`, `GIT_DIR` or their siblings. A sync on the default branch passes only when git resolves its source to the remote's default branch. (ADR 0024)
- The merge workflow counts a `ci` run only when it tested this pull request against the default branch, and tier 2 only from runs on a commit of the default branch; `scripts/forge_runs.py` decides both. A pull request that was retargeted, or whose branch had a pull request against another base, is the owner's to merge.
- The merge job writes its `merge: owner` comment on a pull request it leaves alone for a reason the owner must act on, where it wrote only a line in its log: a pull request from the same branch against another base, a base that was changed, a pull request against another branch, or a finished `ci` run that names none. A red or pending `ci` stays a log line, since the pull request's own checks say it. `forge_runs.py ci --report` decides which reasons are the owner's. (ADR 0024)
- The judge gives the reason `file` when a guard, door or record path is a symbolic link or a submodule.
- A glossary addition is refused when its term mixes scripts or looks like an existing term, or when the new text holds a setext underline or raw HTML.
- The drain's queue directory is the user's own, mode 0700, and `clean` removes only registered work trees inside it.

### Application types, no presets

- A binding declares one or more of six application types (`service`, `web-ui`, `app`, `cli`, `job`, `library`) on a `- Application types:` line. `skills/method/application-types.toml` says which questions apply to each type. A row that does not apply says `none: not a <type>`. A row that applies names a tool, or `none` with a reason and an ADR that accepts the gap. A row is never dropped, and an empty Tool cell is red. (ADR 0017)
- Tools are chosen at adoption, by the agent or by the owner when they have an opinion, and checked against their documentation.

### Speed

- The binding's Speed section states six concepts: Parallel, One pipeline, Shards, Cache, Affected only and Ceilings. Tools are named only as examples. (ADR 0019)
- `scripts/parallel.py` runs a tier's gates at once, prints seconds per gate, appends timings to `.ratchet/timing.jsonl` and names every red gate. The template adds `check-fast`, which stops at the first red.
- The feature preflight and the pipeline read the default branch from the checkout and the binding instead of assuming `main`.
- The CI templates read the tier lists from the `justfile` and the shard count from the binding's `- Shards: N` line, and run the long gates as gate by shard. Adding runners is that one number. The per-tier ceilings in seconds still only fall. (ADR 0019)

### Removed

- The stack presets under `skills/adopt/presets/` (`python-uv` and `typescript-pnpm`). What they taught is kept as rules any recipe must keep, in `skills/method/tiers.md`. (ADR 0017)
- The `guard-changes-present` job of the template `ci.yml`. It failed on every guard path, an added glossary term included; the merge judge now reports guard changes with reasons. (ADR 0021)
- The binding row `every checker bites`. It is replaced by `every changed gate still bites` (`bite`, tier 1) and `every gate bites` (`bite-all`, tier 2).

### Upgrading an 0.8 or 0.9 adoption

Update the plugin to 0.10.0 first. Then run `python3 <plugin>/scripts/upgrade.py` in the repository. It changes nothing: it prints each item below that the repository lacks, file by file with the pin first, with the text or template lines to add and who may edit each file, and exits 1 while anything is missing. It reads the conformance gate's rules, so `just conformance` names the same items one line each. Neither names the items under "Worth doing, not checked", nor three items that another gate names. The mutation gate names a stryker register entry in the old form (step 12), the load smoke names a smoke that requests only the readiness URL (step 14), and the feature preflight names a ticket without its `## Operability` lines (step 17). In a repository whose runner is not `just`, the conformance gate reads no recipe, so the `justfile` parts of steps 1, 2, 7, 10, 13, 15 and 16 are yours to check against the template. Files in the manifest's `edit = "deny"` groups (`guards/**`, `.claude/settings.json`) are the owner's to edit by hand.

An adoption made from a commit between two releases already carries some of these steps. Do only the steps whose items `upgrade.py` and the gates name; each item names its file, which maps it to a step below.

1. In `docs/process/binding.md`, under `## Runner`, add the pin line the conformance gate prints for the plugin in use, `- Ratchet plugin: <version> at <commit>`. From now on that line is the one home of the plugin's version: every later step that needs the commit reads it there. An ADR 0001 that names an older version stays as it is: it is an accepted record of the adoption, and the pin line, not the ADR, says what the gates run. Add `plugin-pin` to the `justfile` from the template, as the first dependency of each tier. With Claude Code the `justfile` falls back to the marketplace clone, and when the plugin updates, the clone moves past the pin: the tiers stay green, say so on every run, and print the line to change (ADR 0027).
2. In `docs/process/binding.md`, add the rows the template's Entry points table has and yours lacks: `tier0-tree`, `hooks-installed`, `hooks-install`, `check-fast`, `settings`, `merge-ready`, `merge`, `weekly` and `bite-one`. Every skill and hook of the method calls them by name, and the conformance gate now requires each. Add their recipes, with `bite` and `bite-all`, from the plugin's `skills/adopt/templates/justfile`.
3. In `docs/process/binding.md`, add `- Application types: <types>` under `## Gates`. Run `python3 <plugin>/scripts/application_types.py <type> [<type> ...]`, which prints `applies` or the Tool cell to write for each row a type can leave out. Write `none: not a <type>` on each row that does not apply. Give every remaining `none` an ADR number that exists in `docs/adr/`, and fill every empty Tool cell.
4. In the same Gates table, add the nine operability rows with their tiers and entry points, as the template binding has them: `feature flags declared`, `configuration hardening`, `telemetry emitted`, `changed flags off and on`, `load smoke`, `active security scan`, `load`, `telemetry arrives` and `no flag past its removal date`. Add their recipes from the template `justfile`. Add a `## Telemetry` and a `## Feature flags` section to the binding, and commit a `flags.toml` (an empty file declares no flags).
5. Replace the `every checker bites` row with the template's `every changed gate still bites` and `every gate bites` rows. Copy `quality/bites.toml` from the template, and add one `[[bite]]` for each row bound to a tool you chose, plus a `remove` bite for each gate that judges a collection. Then run `just bite-all`.
6. Rewrite the binding's `## Speed` section as the six lines of the template (`- Parallel:`, `- One pipeline:`, `- Shards: 1`, `- Cache:`, `- Affected only:`, `- Ceilings:`), each saying how this stack meets it.
7. In the `justfile`, replace the `tier0`, `tier0-tree`, `pre-commit` and `commit-msg` recipes with the template's, so the hook recipes answer `RATCHET_HOOK_PROBE`, and add the call to `hooks-installed` at the top of `ratchet`. Then run `just hooks-install`, which installs the commit hooks or prints the owner's command.
8. In `guards/manifest.toml` (owner), add the three `[[door]]` tables from the template. Bind each to its paths, or say why the repository has none with `none = "<reason>"`. To let agents add records, move `docs/adr/**` and `GLOSSARY.md` from `decisions` into a `records` group with `edit = "append"`, as the template does.
9. Regenerate `.claude/settings.json` (owner) with `just settings`. 0.10 denies the commands that move the default branch's ref, and an `append` group gets no Edit rule.
10. With a forge, copy `.github/workflows/merge.yml` and `weekly.yml` from the template and replace each `<ratchet-sha>` with the commit of the binding's `- Ratchet plugin:` line (step 1). Replace `ci.yml` and `deep.yml` with the template's too: the 0.8 copies keep the `guard-changes-present` job and do not read the shard count, and the template's pass `--over` and `--tier` to `budget.py`. Make every other workflow `ref` that checks the plugin out equal that commit. The `shape` job of `ci.yml` and `deep.yml` reads the gates of each tier from the `justfile` with `just --evaluate`, so add the template's `tier1_gates`, `tier1_sharded`, `tier2_gates` and `tier2_sharded` variables. Each `_gates` list names the gates your tier's recipe runs, and each `_sharded` list the gates of that tier whose tool can run one `SHARD` of `SHARDS`, or `""` for none; run the tier from its list, as the template's `tier1` and `tier2` do, so a local run and CI read one list. Without the variables `shape` fails with "does not contain variable", and the conformance gate names each missing one. With no forge, skip this step: the local recipes need only the variables they use.
11. Add a `## Operability` section to the change template: `.github/pull_request_template.md`, or with no forge the file the binding's `- Change template:` line names. It holds the flag, the telemetry, and what the security and load gates ran.
12. If `quality/mutation-register.toml` holds stryker entries, run the mutation gate. Each entry in the old identity form is red and names the entries to paste, one per mutant you judge equivalent. Replace them, then run `just mutation`.
13. Add the `weekly` recipe to the `justfile` from the template, with its four gates.
14. In `quality/bites.toml`, claim each proof the conformance gate names with `proof = "<name>"` on a bite that plants that fault. If the load smoke requests only the readiness URL, give it a real route, or `--health-only` while the service has none.
15. In the `justfile`, copy the template's `changed`, `jobs` and `alone` variables and its `parallel` line. The cap on the gates at once is now read from `RATCHET_JOBS`: a machine that set another variable, such as `JOBS`, sets `RATCHET_JOBS` instead. Run each gate over the change as `{{changed}} -- <command>`, with `{base}` where the command names the commit it compares with, in place of a merge-base variable of your own.
16. For a service, copy the template's `flags_on` and `probed` variables, bind `flags_on` to the override your flag seam reads, and boot `load-smoke`, `api-deep`, `dast`, `pentest`, `load` and `load-soak` through `{{probed}}`. `smoke` and `telemetry-e2e` keep `{{booted}}` and judge the defaults. If your `booted` passes a flag override of its own, such as every flag on, move it into `flags_on` and drop it from `booted`; otherwise `smoke` and `telemetry-e2e` go on judging the flags on while the method says they judge what ships.
17. Run `/ratchet:conjecture <ticket>` on each open agent-ready ticket, or once on their spec: the feature preflight now asks the `## Operability` lines of the application types the binding declares.
18. Add the `## Asking the owner` section of the plugin's `skills/init/templates/AGENTS.md` to `AGENTS.md`.

Then run `just conformance`.

#### Worth doing, not checked

No gate reads these. Each is safe to leave, and each is worth doing.

- Rebind `fuzz seconds="600"` to the template's `fuzz each="30" cap="600"`, so tier 2 on a small project takes seconds.
- When step 16 moves a flag override out of `booted`, rewrite the binding's `## Feature flags` section to say which gates boot with which flags.
- With no forge, have the machine's scheduler run `just weekly` (step 13).

## Earlier versions

Versions before 0.10.0 have no changelog entries. Their history is in the git log.
