---
status: accepted
---
# Installs come only from a tagged release, and the release names its commit

Settles issue #146. Amends ADR 0060 point 5 for what a release holds.

An installed copy of the plugin has no `.git`, so `plugin_pin.commit_of` returns `None` (`scripts/plugin_pin.py:100-113`) and `scripts/upgrade.py` refuses every project (`scripts/upgrade.py:182-187`). An upgrade needs a clone of the plugin kept by hand at the published commit, and a clone left behind pins a project to an older commit. A file in the copy can name the commit, but no commit can name itself, and installs today follow main's head through `"source": "./"`, so the copy matches no single commit. The owner chose on 2026-11-15 that installs come only from published releases.

## Decision

**A release is one commit off main, named by a tag.** For version V merged on main as commit M, the release commit C is M plus one file, `.claude-plugin/commit`, holding M's full 40-character sha and a newline. Nothing else differs. C's only parent is M, C is never on main, and the tag `vV` names it.

**A project pins M.** The pin an upgrade writes is `V at M`. M's tree is C's without the one file, so CI on the project runs the code the installed copy runs.

**A workflow makes the release.** `.github/workflows/release.yml` runs on a push to main that changes `.claude-plugin/plugin.json`. When tag `vV` does not exist, it makes C, pushes the tag and runs the release check. Neither an agent nor the owner makes C by hand. The version PR keeps the form of #121: it bumps the version, writes the changelog and sets the marketplace `ref`.

**Each release has a release page.** After the check passes, the workflow publishes a GitHub release for `vV`. Its notes are the `## V` section of `CHANGELOG.md`, so the page and the changelog say the same thing. The workflow makes no page for a tag whose check is red, and it leaves a page that already exists alone. The owner authorised the pages on 2026-11-15.

**The marketplace installs the tag.** In `.claude-plugin/marketplace.json`, the ratchet entry's `"source": "./"` becomes `{"source": "github", "repo": "Simon-GitHub-Code/ratchet", "ref": "vV"}`. The version PR sets `ref` with the version.

**`commit_of` reads the file when there is no history.** With `.git`, it runs `git rev-parse HEAD` as now. Without `.git`, it reads `.claude-plugin/commit`: 40 lowercase hex characters give the commit, a missing file gives `None`, and anything else gives `UNREADABLE`.

**The local gates run the installed copy, which a session records.** The marketplace's own clone has a `.git` and follows main's head, so a recipe or an upgrade run from it would pin a commit that is no release and run code no release holds. The adopted justfile's `ratchet_plugin` defaulted to that clone, and so did the README's upgrade command. Instead:

- The SessionStart hook (`hooks/session.py`) writes the path of its own copy, read from its own file, to `ratchet-plugin` in the repository's git common directory, beside the guard event log. One file serves every work tree, the drain's included, and no `git status` shows it. Each session start rewrites it, so it follows the installed version.
- Every harness whose adapter runs `hooks/session.py` writes the file. A harness whose adapter does not run it says so on its `docs/harness/` page, and there the owner exports `RATCHET_PLUGIN` as that page says.
- The adopted justfile reads `RATCHET_PLUGIN` first, then that file. The fallback to the marketplace clone goes. With neither, as in a fresh clone where no session has started, `plugin-pin` is red in one line: the file is written when a session starts in the repository, or set `RATCHET_PLUGIN`. CI is unchanged: it sets `RATCHET_PLUGIN` to its checkout of the pin.
- The settings deny list gains the file, as it holds the event log, since a path pointed at a doctored copy would weaken every local gate.
- When the installed copy and the pin are different releases, the copy check below decides which runs: the pinned copy, wherever it is on disk, or none.
- The README's upgrade section says to run the command the tier's red line names, and names no harness folder.
- An adopted project's `ratchet_plugin` line is a template line, so a conformance shape check with a written fix carries the change to it at the upgrade.

**What the record is trusted for.** CI, which checks the plugin out at the pin, is the authority on whether a change passes. The local record is a convenience that picks which copy the local gates run, and any process with a shell can change it. Running any copy's session hook with the repository's path rewrites it to that copy's folder. The guard does not see that write, and the new value lasts into the owner's own runs. No shell-level guard can close this. So the record only picks among copies, and the gates run none that is not the pin's:

- Every tier's first line prints the folder, the version and the commit of the copy it runs.
- **The check.** Before any recipe runs anything from the recorded copy, the guarded justfile computes that copy's tree id with system git, without `.claude-plugin/commit`, in a throwaway object store and with neutralised config. It runs nothing until the id equals the pin's. The pin holds exactly the pinned commit's tree id, `M^{tree}`, and CI holds the binding to it.
- **On a mismatch,** it tries the sibling folders of the recorded folder, since harnesses keep earlier versions beside the new one, and runs the first that matches.
- **Only when no copy on disk matches** is the tier red, in one line: both ids, the folder, and the two ways out, which are to upgrade this project or install the pinned release.

So a rewritten record is harmless: it can choose only a copy identical to the pin. Only the pinned release runs. While it is on disk, a newer copy beside it only says the project can upgrade. A newer copy alone is red until the upgrade. The owner chose this on 2026-11-15 (item 2 below). Its code arrives in the second round of #177.

**A check holds the tag.** `scripts/release_check.py <tag>` runs in the release workflow after the tag is pushed, and anyone can run it later. It is red unless all of these hold:

- C holds `.claude-plugin/commit` naming M.
- M is C's only parent.
- C's tree without that file equals M's tree.
- M is on main.
- The version in M's `plugin.json` is the tag's.

Main's CI is red when main holds `.claude-plugin/commit`, since a copy on main would be stale from the next commit.

## Refutation criterion

This is wrong if any of these is seen:

- The harness does not install a `github` source that names the marketplace's own repository at a tag. The build checks this first and stops if it fails.
- An installed copy of a tagged release fails to upgrade a project with no clone of the plugin.
- A project pinned to M runs code in CI that differs from the installed copy at `vV`.
- A `v*` tag moves or disappears with `release_check.py` still green.
- A local tier or an upgrade in an adopted project runs any copy but the installed one, or `RATCHET_PLUGIN`'s, or pins a commit that is no release's main commit.
- A tag whose check passed has no release page, or its page says other than the changelog's section for its version.

## Considered options

- **A file that names the commit holding it.** Impossible.
- **A file on main naming the last release.** It is stale from the next commit, and a copy would claim a commit whose code it does not run.
- **A `release` branch that installs follow.** A branch moves and gathers merges. A tag per version names the version and stays put.
- **A `sha` beside the `ref` in the marketplace entry.** C exists only after M merges, so each release would need a second commit on main. The tag ruleset and the release check hold the tag instead.
- **The harness's own install record.** It serves one harness, and the plugin names no harness's folders.
- **Keep `./` and add a `ref`.** A relative source takes no `ref`.

## For the builders

### What changes where

- `scripts/plugin_pin.py`: `commit_of` falls back to `.claude-plugin/commit`.
- `scripts/release_check.py`: new, with its tests on a scratch repository.
- `.github/workflows/release.yml`: new. It makes the tag, runs the check and publishes the release page. Adding it is an `ASK` (workflow write), approved at merge.
- Main's CI: one check that `.claude-plugin/commit` is absent.
- `.claude-plugin/marketplace.json`: the ratchet entry's source. It changes in the first tagged release's version PR, since no tag exists before it.
- `hooks/session.py`: writes `ratchet-plugin` in the git common directory.
- `skills/adopt/templates/justfile`: `ratchet_plugin` reads `RATCHET_PLUGIN`, then that file, with no fallback; plus its conformance shape check and fix.
- The settings step's deny list: the `ratchet-plugin` file.
- `README.md`, Upgrading: run the command the tier's first line names.
- The README's Upgrading section: until the first tagged release is installed, run the upgrade from a clone at the published commit. This line ships in the same change.
- The install page: the plugin installs from the latest tag, never from main.

### Consequences

- Copies of every version before the first tagged release carry no file and keep refusing an upgrade until the harness moves them to the first tagged release. It does that when the version string changes.
- The first tagged release is the first copy that upgrades a project by itself.
- When the harness removes an old installed copy before the next session starts, the recorded path points at nothing, and `plugin-pin` is red until a session starts or `RATCHET_PLUGIN` is set.
- A dev clone has `.git`, so nothing changes for it.
- For about a minute between M's merge and the tag push, a harness that refreshes finds no `vV` and keeps the copy it has.

## Settled by the owner, 2026-11-15

- **1. No `sha` in the entry, and a tag ruleset.** The owner accepted it and set the ruleset on 2026-11-15. No agent has read the ruleset back, since the guard refuses a read of rulesets. The first proof that a tag is held is `release_check.py`'s run against the first real tag. What the owner had first accepted named the tag and its sha. The build names the tag alone, for the reason under Considered options, and a ruleset forbids moving or deleting `v*` tags. The owner sets that ruleset once in the repository's settings, since no agent changes them. Until the ruleset is set, only `release_check.py` notices a moved tag.
- **2. Only the pinned release runs.** The owner chose on 2026-11-15 that the local gates run only a copy whose tree id equals the pin's, trying the sibling folders of the recorded folder on a mismatch, and are red in one line when no copy on disk matches. This replaces the earlier rule that a copy newer than the pin runs and says the project can upgrade.
  The pin holds the tree id of M beside its version and commit: `- Ratchet plugin: V at M tree T`, with `T` the output of `git rev-parse M^{tree}`. CI, where the plugin is at M from the forge, holds the pin's `T` to the checkout's `M^{tree}` and to the hash of its files. The template justfile computes a copy's tree id with system git alone: a throwaway git directory, no git configuration, no filters, the copy's own `.gitignore` and `.gitattributes` overruled, and `.claude-plugin/commit`, `.git`, `__pycache__` and the harness's `.in_use` left out.

  The top-level `just` reads the record once. It hands the folder it checked to every `just` a recipe starts, as `RATCHET_PLUGIN_CHECKED` beside the pin's tree id, and a child that finds the marker with the same tree id takes that folder and reads no record. A record that changes mid-tier, as a session start rewrites it, changes nothing the tier runs. A clone is a release only while `HEAD` is the release commit itself and the work tree is clean, and a commit proves where a copy came from, never what it holds: only the hash of its files does.
