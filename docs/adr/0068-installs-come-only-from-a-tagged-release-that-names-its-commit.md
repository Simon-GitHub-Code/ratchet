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

**`commit_of` reads the file.** Without `.git`, it reads `.claude-plugin/commit`: 40 lowercase hex characters give the commit, a missing file gives `None`, and anything else gives `UNREADABLE`. With `.git`, a clone is a release only while `HEAD` is the release commit itself: it tracks the file, its single parent is the commit the file names, and the work tree is clean. Then the commit is the one the file names. Otherwise it is `HEAD`, as before, so a dev clone is unchanged. A commit says where a copy came from, never what it holds; only the tree id below does.

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
- **The check.** Before any recipe runs anything from the recorded copy, the guarded justfile computes that copy's tree id in memory with the system python3, as git hashes a tree and with no conversion, without `.claude-plugin/commit`, `.git`, `.in_use`, `.orphaned_at` and any `__pycache__`. It runs nothing until the id equals the pin's. The pin holds exactly the pinned commit's tree id, `M^{tree}`, and CI holds the binding to it.
- **On a mismatch,** it tries the sibling folders of the recorded folder, since harnesses keep earlier versions beside the new one, and runs the first that matches. Only a folder read from the record has its siblings tried. A folder `RATCHET_PLUGIN` names is the owner's choice and runs only if it matches.
- **Only when no copy on disk matches** is the tier red, in one line: both ids, the folder, and the two ways out, which are to upgrade this project or install the pinned release:

  ```text
  the plugin at <dir> is tree <have>, not the <want> that docs/process/binding.md pins, and no copy on disk matches: upgrade this project with `python3 <dir>/scripts/upgrade.py --apply`, or install the plugin release the pin names
  ```

  A pin with no tree id, as every pin written before this record, cannot be checked and is red in one line that names the upgrade, which writes it.

So a rewritten record is harmless: it can choose only a copy identical to the pin. Only the pinned release runs. While it is on disk, a newer copy beside it only says the project can upgrade. A newer copy alone is red until the upgrade. The owner chose this on 2026-11-15 (item 2 below).

**What the check does not hold.** The check holds a copy's files at the moment it hashes them. A tier runs the files on disk, not the hashed objects, and Python reads each module when it first imports it, so a file changed after the hash runs. Running a snapshot of the hashed objects only moves the window to the snapshot's folder, which a process of the same user can also write. This window is accepted risk, with a changed `PATH` and the interpreter's path variables. CI takes the plugin from the forge, so it catches what such a process changes.

Everything else a copy holds is checked or left unread:

- Every `just` a tier starts hashes its folder again, so `RATCHET_PLUGIN_CHECKED` passes a folder and vouches for nothing. The hash costs about 0.1 s per child on a real copy of 485 files, measured at 0.38 to 0.47 s before it was computed in memory.
- Every tier reads and writes compiled files only under `<temporary folder>/ratchet-pyc-<uid>/<tree id>`, a folder outside every copy, through `PYTHONPYCACHEPREFIX`. The template justfile creates `ratchet-pyc-<uid>` with mode 0700 and uses it only when it is a real folder (not a link) that this user owns and that no one else can write. Otherwise it points the prefix at a path that cannot exist and sets `PYTHONDONTWRITEBYTECODE=1`, so every script compiles from source, which costs about 1 to 2 s more for a process that imports `conformance` (measured on a loaded machine: 1.7 to 2.4 s cold against 0.4 to 0.6 s cached). Python reads no compiled file from the copy's `__pycache__`, which the hash leaves out, and another user who creates the shared folder first gets none read.
- Once the hash matches, no git command runs in the copy, since its `.git`, which the hash leaves out, can hold a config that names a command `git status` runs.

**A check holds the tag.** `scripts/release_check.py <tag>` runs in the release workflow after the tag is pushed, and anyone can run it later. It is red unless all of these hold:

- C holds `.claude-plugin/commit` naming M.
- M is C's only parent.
- C's tree without that file equals M's tree.
- M is on main.
- The version in M's `plugin.json` is the tag's, and M is the first commit on main's first-parent line where the version became it.

The workflow refuses to tag a version that is not above every tagged version, in one line: `the version X is not above the tagged vY; versions only go up`. So a version never returns to an earlier value, and that first commit is unique.

Main's CI is red when main holds `.claude-plugin/commit`, since a copy on main would be stale from the next commit.

## Refutation criterion

This is wrong if any of these is seen:

- The harness does not install a `github` source that names the marketplace's own repository at a tag. The build checks this first and stops if it fails.
- An installed copy of a tagged release fails to upgrade a project with no clone of the plugin.
- A project pinned to M runs code in CI that differs from the installed copy at `vV`.
- A `v*` tag moves or disappears with `release_check.py` still green.
- A local tier in an adopted project runs a copy whose tree id is not the pin's, or a `just` a tier started reads the record again and runs another copy.
- An upgrade pins a commit that is no release's main commit.
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
- `skills/adopt/templates/justfile`: `ratchet_plugin` reads `RATCHET_PLUGIN`, then that file, with no fallback; plus its conformance shape check and fix. The owner's own recipes that use the plugin's folder where a checked folder cannot reach them (a shebang recipe, a `:=` expression, a function argument, or an unquoted or single-quoted use) are a red conformance finding that lists each by line, for an agent to fix.
- The settings step's deny list: the `ratchet-plugin` file.
- `README.md`, Upgrading: run the command the tier's first line names.
- The README's Upgrading section no longer says to run the upgrade from a clone at the commit a version was published at (#181's instruction): an installed copy of a tagged release names its own commit, and the section names the one command a red tier prints.
- The README's install step and `docs/harness/claude-code.md`: the plugin installs from the latest release's tag, never from the default branch.

### Consequences

- Copies of every version before the first tagged release carry no file and keep refusing an upgrade until the harness moves them to the first tagged release. It does that when the version string changes.
- The first tagged release is the first copy that upgrades a project by itself.
- When the harness removes the recorded copy and no copy on disk has the pinned tree, every local tier is red in one line until the pinned release is installed and a session starts, or `RATCHET_PLUGIN` names it:

  ```text
  the plugin at <dir> is gone, and no copy on disk has the tree <want> that docs/process/binding.md pins: install the plugin release the pin names, then start an agent session here once, or set RATCHET_PLUGIN to its folder
  ```

- A dev clone has `.git`, so nothing changes for it.
- A harness keeps each older copy beside the current one with an `.orphaned_at` timestamp file, so the hash leaves that file out at the root, and a sibling is tried only when it holds `.claude-plugin/plugin.json`.
- A plugin newer than the pin is red, with the upgrade command, since the justfile would not run it and a green line there would say otherwise. `/ratchet:next` still offers the upgrade.
- The session hook says in one line when the copy running the session is not the one the pin names.
- When a newer run of the release workflow cancels a pending one, a version bumped between two quick merges is never tagged and the later version is. The owner releases nothing for the skipped version: it needs no tag, versions only go up, and the first tag the marketplace installs is the later one.
- For about a minute between M's merge and the tag push, a harness that refreshes finds no `vV` and keeps the copy it has.

## Settled by the owner, 2026-11-15

- **1. No `sha` in the entry, and a tag ruleset.** The owner accepted it and set the ruleset on 2026-11-15. No agent has read the ruleset back, since the guard refuses a read of rulesets. The first proof that a tag is held is `release_check.py`'s run against the first real tag. What the owner had first accepted named the tag and its sha. The build names the tag alone, for the reason under Considered options, and a ruleset forbids moving or deleting `v*` tags. The owner sets that ruleset once in the repository's settings, since no agent changes them. Until the ruleset is set, only `release_check.py` notices a moved tag.
- **2. Only the pinned release runs.** The owner chose on 2026-11-15 that the local gates run only a copy whose tree id equals the pin's, trying the sibling folders of the recorded folder on a mismatch, and are red in one line when no copy on disk matches. This replaces the earlier rule that a copy newer than the pin runs and says the project can upgrade.
  The pin holds the tree id of M beside its version and commit: `- Ratchet plugin: V at M tree T`, with `T` the output of `git rev-parse M^{tree}`. CI, where the plugin is at M from the forge, holds the pin's `T` to the checkout's `M^{tree}` and to the hash of its files. The template justfile computes a copy's tree id in memory with the system python3 alone, as `git write-tree` would of the same files, with no filter, no ignore file and no attributes applied, and `.claude-plugin/commit`, `.git`, the harness's `.in_use` and `.orphaned_at` at the root, and `__pycache__` at any depth, left out. A test holds it equal to `git write-tree` on a tree with executable files, symbolic links and nested folders.

  The top-level `just` reads the record once. It hands the folder it checked to every `just` a recipe starts, as `RATCHET_PLUGIN_CHECKED` beside the pin's tree id, and a child that finds the marker with the same tree id takes that folder, reads no record and tries no sibling, and hashes that folder again, so a forged marker naming another folder fails the hash. A record that changes mid-tier, as a session start rewrites it, changes nothing the tier runs. A clone is a release only while `HEAD` is the release commit itself and the work tree is clean, and a commit proves where a copy came from, never what it holds: only the hash of its files does.
