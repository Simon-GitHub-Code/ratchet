# Changelog

## 0.9.0

A change the gates can judge now merges without the owner, and the owner is asked only what shapes the product or cannot be taken back. Ratchet ships the questions the method asks of each application type and no tool for any stack, and an adopted repository proves that each gate it binds can go red. The ADR numbers below are the plugin's own, in `docs/adr/`.

### Merging

- A pull request merges without the owner when its gates are green, the review approves, no blocker or major finding is open, no guard path changed other than by an addition to a record or a tightened threshold, it goes through no one-way door and tier 2 is not red on the default branch. Everything else waits for the owner, with the reasons written on the pull request. (ADR 0021)
- `scripts/merge_ready.py`, behind the new `merge-ready` entry point, is the judge. It reads commits only and prints `merge: agent` or `merge: owner` with one reason per line.
- On GitHub the new `merge.yml` workflow runs after `ci` from the default branch and merges each pull request the judge passes. With no forge, the new `merge <branch>` entry point judges the branch, runs `check` on the merge result and merges it.
- One-way doors are `[[door]]` tables in `guards/manifest.toml`, of three kinds: `change`, `rewrite` and `removal`. A door guards nothing; only the judge reads it.
- The guard hook asks on `git merge`, `git cherry-pick` and a pull of another branch on the default branch, and refuses any command that moves, deletes or re-points the default branch's ref. (ADR 0016, ADR 0021)

### Records and questions

- An agent may add a glossary term, an `_Avoid_` item or a new ADR without asking. Changing or removing what the default branch holds still asks the owner. A guard group marked `edit = "append"` gets this treatment, and `guard-diff --fail-unless-additive` enforces it. The pull request lists every addition. (ADR 0014)
- An open question is `direction` (the options are different products) or `irreversible` (a wrong guess loses data, fixes a public contract, spends money or weakens a security rule). Everything else is assumed, given a refuter and listed in the pull request. Questions are asked after the work, and each answer is written on the ticket under `## Answers`. (ADR 0013, ADR 0022)
- `/ratchet:conjecture` no longer asks the owner to accept its draft.
- `scripts/questions.py` logs each answer in `.ratchet/questions.tsv`. When a question's last three answers took the recommendation, the route proposes a standing answer for `AGENTS.md`. It never proposes one for a `direction` question. (ADR 0022)

### The drain

- `/ratchet:feature` with no ticket builds every agent-ready ticket, each in its own git work tree, branch and pull request, at most three at once by default. Changes merge one at a time: a branch the default branch moved under is rebased and reviewed again. A ticket that cannot go on is parked and the rest continue. The run ends in one report of what merged, what waits and why, and every assumption made. `scripts/drain.py` holds the steps that need no judgement. (ADR 0023)

### Gates

- A gate with nothing to run is red on every stack. `none` is only for a property the project will never have. (ADR 0020)
- `scripts/hooks_installed.py` runs the commit hooks git would run. The feature preflight, the route and the template `ratchet` recipe are red until a commit would run tier 0, and print the one command for the owner. The guard still refuses hook installs from an agent. (ADR 0015)
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
- The weekly gates run: a new `weekly` entry point and `weekly.yml` workflow, where a red run opens one issue or comments on the open one.
- A ceiling reports on a pull request and blocks on the default branch and nightly (`budget.py --over report`), and CI holds each gate's job to its tier's ceiling (`--tier`).
- Local tiers and CI run the plugin at one commit. The binding's `- Ratchet plugin: <version> at <commit>` line is the pin, every workflow `ref` must equal it, and `scripts/plugin_pin.py` is red at the start of each tier when the plugin in use is another commit.

### Guard hardening

- The hook reads a ref name, a repository and a sync the way git does. It refuses a push or fetch that deletes or re-points the default branch under any spelling git resolves, a tag named as a default branch, and a `git` aimed at another repository through `--git-dir`, `GIT_DIR` or their siblings. A sync on the default branch passes only when git resolves its source to the remote's default branch. (ADR 0024)
- The merge workflow counts a `ci` run only when it tested this pull request against the default branch, and tier 2 only from runs on a commit of the default branch; `scripts/forge_runs.py` decides both. A pull request that was retargeted, or whose branch had a pull request against another base, is the owner's to merge.
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

### Upgrading an 0.8 adoption

Update the plugin to 0.9.0 first. The conformance gate then names each item below that is missing. Files in the manifest's `edit = "deny"` groups (`guards/**`, `.claude/settings.json`) are the owner's to edit by hand.

1. In `docs/process/binding.md`, add the rows the template's Entry points table has and yours lacks: `tier0-tree`, `hooks-installed`, `check-fast`, `settings`, `merge-ready`, `merge` and `bite-one`. Every skill and hook of the method calls them by name, and the conformance gate now requires each. Add their recipes, with `bite` and `bite-all`, from the plugin's `skills/adopt/templates/justfile`. Then run `just conformance`.
2. In `docs/process/binding.md`, add `- Application types: <types>` under `## Gates`. Run `python3 <plugin>/scripts/application_types.py <type> [<type> ...]`, which prints `applies` or the Tool cell to write for each row a type can leave out. Write `none: not a <type>` on each row that does not apply. Give every remaining `none` an ADR number that exists in `docs/adr/`, and fill every empty Tool cell. Then run `just conformance`.
3. In the same Gates table, add the nine operability rows with their tiers and entry points, as the template binding has them: `feature flags declared`, `configuration hardening`, `telemetry emitted`, `changed flags off and on`, `load smoke`, `active security scan`, `load`, `telemetry arrives` and `no flag past its removal date`. Add their recipes from the template `justfile`. Add a `## Telemetry` and a `## Feature flags` section to the binding, and commit a `flags.toml` (an empty file declares no flags). Then run `just conformance`.
4. Replace the `every checker bites` row with the template's `every changed gate still bites` and `every gate bites` rows. Copy `quality/bites.toml` from the template, and add one `[[bite]]` for each row bound to a tool you chose, plus a `remove` bite for each gate that judges a collection. Then run `just conformance` and `just bite-all`.
5. Rewrite the binding's `## Speed` section as the six lines of the template (`- Parallel:`, `- One pipeline:`, `- Shards: 1`, `- Cache:`, `- Affected only:`, `- Ceilings:`), each saying how this stack meets it. Then run `just conformance`.
6. In the `justfile`, replace the `tier0`, `tier0-tree`, `pre-commit` and `commit-msg` recipes with the template's, so the hook recipes answer `RATCHET_HOOK_PROBE`, and add the call to `hooks-installed` at the top of `ratchet`. Then run `just conformance`, and `just hooks-installed`, which prints the owner's command if a commit would not run tier 0.
7. In `guards/manifest.toml` (owner), add the three `[[door]]` tables from the template. Bind each to its paths, or say why the repository has none with `none = "<reason>"`. To let agents add records, move `docs/adr/**` and `GLOSSARY.md` from `decisions` into a `records` group with `edit = "append"`, as the template does. Then run `just conformance`.
8. Regenerate `.claude/settings.json` (owner) with `just settings`. 0.9 denies the commands that move the default branch's ref, and an `append` group gets no Edit rule. Then run `just conformance`.
9. Copy `.github/workflows/merge.yml` from the template and replace `<ratchet-sha>` with the commit ADR 0001 names. Replace `ci.yml` and `deep.yml` with the template's too: the 0.8 copies keep the `guard-changes-present` job and do not read the shard count. Then run `just conformance`.
10. In `.github/pull_request_template.md`, add a `## Operability` section: the flag, the telemetry, and what the security and load gates ran. Then run `just conformance`.
11. If `quality/mutation-register.toml` holds stryker entries, run the mutation gate. Each entry in the old identity form is red and names the entries to paste, one per mutant you judge equivalent. Replace them, then run `just mutation`.
12. In `docs/process/binding.md`, add `- Ratchet plugin: <version> at <commit>` and make every workflow `ref` that checks the plugin out equal that commit. Add `plugin-pin` to the `justfile` from the template, as the first dependency of each tier.
13. Add the `weekly` entry point to the binding and the `justfile`, and copy `.github/workflows/weekly.yml` from the template. The template `ci.yml` and `deep.yml` of step 9 already pass `--over` and `--tier` to `budget.py`, and its `merge.yml` already calls `forge_runs.py`.
14. In `quality/bites.toml`, claim each proof the conformance gate names with `proof = "<name>"` on a bite that plants that fault. If the load smoke requests only the readiness URL, give it a real route, or `--health-only` while the service has none.

## Earlier versions

Versions before 0.9.0 have no changelog entries. Their history is in the git log.
