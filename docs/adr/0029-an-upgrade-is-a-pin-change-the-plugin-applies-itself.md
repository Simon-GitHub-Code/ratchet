---
status: accepted
---
# An upgrade is a pin change the plugin applies itself, and the merge judge is its trust boundary

When this was written, on 2026-11-13, the owner's three adopted repositories were pinned to 0.8.2, 0.9.2 and 0.5.0. The fixes for most of their daily pain had shipped in 0.10 and 0.11, and none of them had upgraded. The documented upgrade from 0.8 or 0.9 was eighteen manual steps in the changelog. `scripts/upgrade.py` printed what was missing and changed nothing. The one attempt left a repository's main red; the 0.9.2 repository's main was then upgraded to 0.11.0 by hand the same day.

A hand upgrade of two of them, on scratch clones, measured the work. The 0.9.2 one had 56 items: 39 edits, of which 11 were deterministic, 24 needed judgement and 4 were the owner's. The 0.8.2 one had 86 items in 12 files: 58 deterministic (30 of them permission rules that one regeneration writes), 24 judgement, 4 the owner's. It also found seven ways the upgrade goes wrong:

1. The report did not converge. Fixing items revealed new ones: replacing `ci.yml` with the template's added four tier-list variables, and the seven 0.11 Gates rows never appeared while the binding lacked `- Application types:`, because that line's absence hid every row finding.
2. Copying a workflow template dropped a project's browser install step from `deep.yml`, and no gate noticed.
3. Replacing an old `bite` recipe with the template's dropped the project's own bite command, a silent loosening.
4. Writing `later:` for a trigger that has already fired is green, so a safeguard can be deferred quietly.
5. `- Life events:` is required, and the suggested `none` turns off the owner's review of one-way doors. It is the one real owner question in the upgrade.
6. The red main came from `just settings` run on a plugin newer than the pin. The regenerated `.claude/settings.json` held rules only the newer plugin writes, and CI, on the pinned commit, rejected thirty of them. A pin bump and a regenerated settings file in separate commits always break CI.
7. Edits by anchor were error-prone (a template comment repeats the variable it describes), and a partial run could not be repeated.

`upgrade.py`'s docstring said a script that wrote the edits "would be a way round the guard". The hand upgrade showed that a script already passes the guard, and ADR 0021 lists a script file run by path among what the hook cannot reach. The guard was never what made an upgrade safe.

## Decision

**Every conformance finding names who fixes it.** A finding carries a fix, and the type has no default, so a check cannot yield a finding without one and `mypy --strict` is red on a check that tries:

- `auto`: plugin code makes it. Nothing about it needs a judgement. Its text comes from the plugin's templates or the repository's own binding. The edit is built by the check that found the problem from the state it read, so there is no second list of rules to keep in step with the checks.
- `agent`: it needs judgement. The report prints what to do and the template text to start from.
- `owner`: it is a product or risk call. The report prints it as a question with what the repository shows and what each answer leads to.

The kind belongs to the finding, not to the check. A missing Entry points row is `auto` when every row already runs `just` and `agent` under another runner. A missing recipe is `auto` when it is entirely the plugin's and `agent` when it needs a list only the project can write.

**Only the check that found a problem can say an edit fixed it.** A check never yields an `auto` finding: it offers an edit and the kind its finding falls back to. Conformance makes the edit in a copy of what git sees of the repository, runs the same check on that copy, and the finding is `auto` only when that check no longer raises it. An edit that changes nothing, or leaves its finding raised, keeps the fallback kind. A check that yields an `auto` itself is a `TypeError`. The first version let each check test its own edit with a test of its own, and the verifier of this change built a repository with renamed tiers where that test was weaker than the finding: the edit copied the `plugin-pin` recipe, its test saw the script named, and no tier ran it.

**A check is as strong as its finding.** The trial showed where a check passed on less than it claimed, and those checks were made exact:

- every tier, named by the binding's Entry points rows, must run `plugin_pin.py`, through its own lines, a recipe it depends on, or a recipe it calls with `just`. The script named anywhere in the `justfile` was enough before;
- a section is present when a line is its heading. Every reader and writer finds a section the same way, by its heading line with trailing space and a carriage return ignored, and reads every occurrence of it. A heading named in prose, or written as a smaller heading, counted before;
- the `justfile` is read with every file it imports, in the order `just` reads them, so a recipe an import defines is the project's and is never copied again. The copy the checks run on holds every file of the repository the `justfile` imports, an ignored `import? 'local.just'` too, since `just` reads it. An Entry points row that runs a module's recipe (`mod`) is not asked to have a recipe of its own;
- a rule the plugin writes in `ask`, or for a group it writes no rule for (an `append` group), that the project holds in `deny` is stricter than the plugin's. It is not drift, regeneration keeps it in `deny`, and the report names it. No regeneration takes a rule out of `deny`;
- the pin line is a line of the pin's shape, `- Ratchet plugin: <version> at <commit>`. A line that only starts like it, such as a note on where the plugin comes from, is the project's own and is never replaced.

**`upgrade.py --apply` moves the pin and makes every `auto` item.** Each edit reads a file as it is and returns the text the file must hold. It is idempotent, it keeps every line it does not own, and it reads nothing but its file, so the upgrade converges from any starting state: an old version, a half-done upgrade, a run stopped midway. A second run changes nothing. The edits are:

- the binding's `- Ratchet plugin:` pin line, at the plugin in use;
- every workflow `ref` that checks the plugin out, at the pin's commit;
- the permission rules in `.claude/settings.json` that the plugin generates, regenerated from the manifest. A rule no version of the plugin writes is the project's: it is kept and asked about. A rule the project made stricter stays as strict;
- the Entry points rows the binding lacks, as the template writes them, when the runner is `just`;
- each missing recipe that is entirely the plugin's: no `bind:` placeholder, and every variable, dependency and `just` call it names already defined. It goes at the end of the `justfile` itself, never an imported file, in the template's order among the recipes copied there, and `plugin-pin` becomes the first dependency of each tier recipe that does not already run it;
- `hooks-installed` run first by the `ratchet` recipe, as the template runs it;
- the `## Asking the owner` section of `AGENTS.md`, a missing `flags.toml`, a missing `quality/bites.toml`, the missing sections of the change template, the `.gitkeep` of `docs/disputes` and `docs/features`;
- `merge.yml` and `weekly.yml` where they are absent, at the pin's commit.

**The dry run is the apply, on a copy.** `upgrade.py` copies what git sees of the repository to a temporary directory, moves the pin there, and runs the conformance checks and their `auto` edits until none is left. Only then does `--apply` write the changed files to the repository, each one whole through a rename, the binding last. Each file is made in `.ratchet/upgrade`, which git ignores, under a lock, so a run killed between making a file and renaming it leaves nothing git sees, and the next run clears what it left. Without `--apply` it prints the same plan and writes nothing. A rule that does not converge is a bug in the plugin: the run stops with exit 3 and the repository is untouched. `--apply` refuses before writing anything when the plugin in use is older than the pin, when the order cannot be told, when the plugin carries no commit to pin, when a file it would write leads out of the repository through a link (relative or absolute), lies in a submodule, or lands under `guards/` through a link, when a file it would write is one git ignores, when the pin must go into a binding that is not UTF-8 text or a check reads a file that is not, or when `just` cannot read the planned `justfile` though it reads the current one. An ignored file is refused because the copy holds only what git sees: the plan would write over a file it never read, and the change would not carry the write. Git is asked in the repository itself, so every rule it reads counts: an ignore file tracked or not, the repository's own config, the user's. A file that is not UTF-8 is never edited: decoded and written back, its other bytes would be lost, so a trial on it never makes its finding `auto`. A file named outside the tree, such as a change template at `../`, is never written: no trial can clear its finding, so it stays an `agent` item.

**The script never loosens and never guesses.** It adds what is missing. It replaces only the facts the plugin owns: the pin line, the workflow refs and the generated permission rules. A file that mixes the template with the project's own content (a workflow with a setup step, a recipe with an extra command, a tier recipe) is left as it is, and its item is an `agent` item. It never writes under `guards/`: the finding type refuses an edit there, a trial never writes a file that a link leads into `guards/`, and the plan refuses one that reaches it some other way. It never writes a placeholder that no check would flag. New Gates rows are `agent` items: writing `later:` for a trigger needs to know whether the trigger has fired. Each item says so: a row asked from the first commit is to be bound; a row whose trigger has fired, by the instances the Triggers table finds and the life events the binding declares, is to be bound and the item names the instances; any other row is bound, or `later:` only while its trigger has not fired, and the item says what is known of each part of the trigger, including a kind the Triggers table has no row for yet. `- Life events:` and the `[[door]]` tables are `owner` questions.

**The report is complete in one pass.** Everything an `auto` edit would reveal is revealed on the copy before anything prints, so the report after `--apply` is the dry run's, item for item. No check hides a finding behind a fact it does not need: a missing `- Application types:` line no longer hides the Gates rows the binding lacks, a missing `quality/bites.toml` no longer hides the gates that need a bite, and the tier-list variables are asked for wherever a tier workflow exists, not only once it reads them. The questions the catalogue asks are no longer asked twice. A `# bind: toolchain setup` line with nothing bound under it is a finding, so the `weekly.yml` the upgrade creates is not a red first run the report never mentioned. What only an answer can show is named in that answer's item: the Triggers rows each application type asks for, the rows each life event makes owed, and the bite a newly bound gate needs. After `--apply` the report lists only `agent` and `owner` items, and that list is the upgrade's remaining work.

**Regenerating the permission rules refuses under a plugin CI does not run.** `settings_rules.py --write` (`just settings`) compares the plugin it runs from with the one CI checks out: the binding's pin and the commit every workflow `ref` names. When they differ, or name two commits between them, it writes nothing and names both ways out: `upgrade.py --apply`, which moves the pin, the refs and the rules together, or `RATCHET_PLUGIN` set to a checkout of the pinned commit. ADR 0027 keeps a newer plugin green because a run that only judges cannot damage the repository: CI on the pin still decides. A write is different. The file it writes is judged by the pinned plugin in CI, and its newer rules are certain to be rejected there, after the merge. Refusing costs one command; writing costs a red main found later.

## The trust boundary

An upgrade changes guard paths: the binding, the `justfile`, the workflows, `.claude/settings.json`. The guard asks the owner about an agent's edit of each. A script's write passes it, as any script run by path does (ADR 0021). Approving fifty prompts one by one was never a review either: the hand upgrade had thirty edits that asked the owner.

The merge judge is the boundary. It reads the change through git and the manifest at the tip of the default branch, and a change that touches a guard path is `merge: owner` whatever wrote it. An upgrade is therefore one change the owner merges: `--apply`'s writes, the agent's edits and the owner's answers together. The owner's one act is the merge, and the judge cannot be talked out of asking for it.

## What follows, not built here

A release must never turn an adopted repository red. What a release newly asks should arrive as owed rows that `/ratchet:safeguard` binds, which is ADR 0028's principle applied to the method's own releases. A `/ratchet:upgrade` skill should wrap the command: run `--apply`, do the `agent` items, ask the `owner` questions, and open the one change. Later changes build both.

Decided 2026-11-13.

## What this does not see

The rules recognise template text, not intent. A recipe that is the template's word for word but which the project meant to change stays as the template has it. A project that renamed a method recipe gets a second copy under the template's name.

A gap the conformance gate does not check is not an item, so no rule makes it. The changelog keeps a "Worth doing, not checked" list for those.

The refusal in `settings_rules.py --write` knows the plugin CI runs only from the pin or the workflow refs. A repository with neither has no CI to disagree with, and the write proceeds. The other scripts that write a file CI reads (`snapshots.py --write`, `coverage_groups.py --write`, `budget.py --set`) do not refuse yet.

The copy holds what git sees. A check that read an ignored file would judge the copy and the repository differently; none does today, and the tests compare the plan with the result of applying it. A file git ignores that the plan would write is refused, not written.

Two readers outside this change split the binding on `"\n## Gates\n"` and `"\n## Triggers\n"`: `project_bite.py` and `triggers.py`. On a binding whose every line ends in a carriage return they find neither table, so the bite findings for bound Gates rows are not raised there. Every other reader finds the sections; those two are to move to the one way of finding a section.

`just` aliases (`alias x := y`) are not read as recipes, so an Entry points row that runs an alias is asked for as a missing recipe. When the alias has a template recipe's name, the planned `justfile` defines that name twice, `just` cannot read it, and the plan is refused with `just`'s own error; the project's alias is never overwritten, but the upgrade cannot proceed until the alias is a recipe.

An import that leads out of the tree by a relative path (`import? '../shared.just'`) is not in the copy, so a recipe it defines can be copied again. When the import is not optional, `just` cannot read the planned `justfile` in the copy and the plan is refused.

The temporary files and the lock live in `.ratchet/upgrade`. When `.ratchet` is a link out of the repository, they are made where it leads. When that is another file system, the first rename fails, the run stops with an error, and nothing is written.

The pinned commit is the plugin's own, so it must be one CI can fetch. A plugin run from an unpushed local checkout pins a commit CI cannot check out, and the first CI run says so.

## Refutation criterion

This is wrong if `--apply` drops project content or loosens a gate. Read the diff of the first five upgrades made with it: one removed line that was the project's own, or one rule or check that is weaker after it, refutes the rule that wrote it, and that rule becomes an `agent` item. It is wrong if the report after `--apply` is not the remaining work: an upgrade whose change is green locally and red in CI on the new pin, for an item the report did not list, refutes the one-pass claim, and the check that hid the item is fixed before the next upgrade. And it is wrong if the owner still cannot upgrade: if any of the three adopted repositories has not moved to the current release within a month of this change, the cost was not where this decision put it.

## Considered options

- **Keep the report and let an agent make every edit.** Rejected: the hand upgrade took 39 edits, 30 of them asked the owner, and a stopped run could not be repeated. Nobody upgraded.
- **Ship a patch per version pair (0.8 to 0.11, 0.9 to 0.11).** Rejected: the adopted repositories are at commits between releases and half-done states, and a patch applies to one starting state. An ensure rule applies to any.
- **Replace each method file with the template's.** Rejected: it dropped a browser install step and a project's bite command in the hand upgrade. A file the project shares with the template is merged by an agent or left alone.
- **A table of ensure rules beside the checks, keyed by finding.** Rejected: two lists that must agree finding by finding, and an agent that rewords a check breaks the key without a sound. The fix kind also depends on the state, which a table keyed by check cannot say.
- **Merge each template file three ways against the template at the pinned commit.** Rejected: the marketplace clone is shallow (ADR 0027), so the base is missing where the upgrade runs; the binding and the `justfile` differ from the template on nearly every line; and a clean textual merge of a workflow can still be wrong in a way no gate notices.
- **Apply in place, and print the first pass as the dry run.** Rejected: every cascade (the pin, then the refs; a created workflow, then its ref) would be missing from the dry run, and a rule that does not converge would be found after half the repository was written.
- **Apply everything, judgement included, with defaults (`later:` for new rows, `none` for life events).** Rejected: `later:` for a fired trigger defers a safeguard quietly, and `none` switches off the owner's review of one-way doors. Both are loosenings a default would hide.
- **Keep the guard as the boundary and make the script ask per edit.** Rejected: a script's writes do not reach the hook, and per-edit approval is what the owner already skips. The merge judge reads the whole change and cannot be bypassed by the change.
- **Make `just settings` warn instead of refusing under a plugin CI does not run.** Rejected: the warning scrolls past in an agent's session, and the file it writes is certain to be red in CI.
