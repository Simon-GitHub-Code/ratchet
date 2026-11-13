---
status: accepted
---
# A plugin newer than the pin is announced on every run, and only an older one is red

ADR 0025 made the binding's `- Ratchet plugin: <version> at <commit>` line the one home of the plugin version, held every workflow's checkout to it, and made `plugin_pin.py` red at the start of each tier whenever the plugin a run uses is at any other commit or version. It rejected a warning, because a warning in a tier's output is read by nobody, and it named its own refuter: the pin is wrong if harness updates turn local tiers red so often that owners approve the upgrade unread.

An upgrade of an adopted Rust service from a 0.9 commit to 0.10.0, done from the changelog as a stranger would, showed how that plays out under Claude Code. The plugin a local tier uses there is the marketplace clone, `~/.claude/plugins/marketplaces/ratchet`. It moves whenever the plugin updates, and it is a shallow clone: on the build machine its `.git/shallow` names one commit, so the commit it moved from is gone. `plugin-pin` is the first dependency of every tier and of `tier0-tree`, which the pre-commit hook runs. So one update of the plugin turns every tier and every commit red, in every adopted repository on the machine, until the owner edits the pin in each, and the pin is a guard path. The walk saw the mirror image: it pinned 0.10.0 from a checkout while the marketplace clone was still at 0.9.2, and every commit made outside the walk's own environment was red at `plugin-pin`. No update past a pin was watched; that it walls every repository the same way is read from the mechanism. An owner who must edit a line to commit at all approves that edit to get going, which is the unread approval ADR 0025 said would refute it.

We decided that the pin check orders the two versions and treats the directions differently.

- **Older than the pin is red.** A local tier would run older gates than CI, and the older plugin may lack a script the recipes call. The red says the plugin in use is the older one, and to update it or set `RATCHET_PLUGIN` to a checkout of the pin. Updating the plugin is the developer's own act and asks no one.
- **Newer than the pin is green, and says so on every run.** The line names both versions, says that this run judges by the newer plugin's gates and CI by the pinned one's, names `scripts/upgrade.py`, and prints the exact pin line to change. The route offers the same upgrade as an `also:` line, since the route is what the owner reads. The newer plugin's conformance gate, which runs in tier 1 and reads the rules `upgrade.py` prints, is red while the repository lacks something the newer version needs, and each red says what to add. So a newer plugin that needs nothing new costs nothing until the owner chooses to move the pin, and one that needs something is red in tier 1 with the edits named, but never at the commit hook.
- **Order unknown is red.** The same version at another commit, or a version that is not dotted numbers, cannot be ordered without history the shallow clone does not keep. The red gives both ways out: a checkout of the pin, or `upgrade.py` and the new pin line where the plugin in use is the newer.
- **CI stays on the pinned commit.** Nothing here changes the workflows: each checks the plugin out at the pin, and the conformance gate holds every `ref` to it. With a forge, CI on the pinned commit remains what a merge waits for.

A local run is never silently on other rules than CI: when the plugins differ, every tier either stops or prints the difference before its first gate, and the route repeats it.

`upgrade.py` reports the pin first: the line to write when the plugin in use is newer, or "update the plugin and run this report again" when it is older, since the report reads the rules of the plugin it runs from.

Decided 2026-11-12.

Extended by ADR 0029: a newer plugin judges and is announced, but never writes a file CI reads. `just settings` refuses under it, since CI on the pin would reject the rules it wrote, and `upgrade.py --apply` moves the pin, the workflow refs and the rules in one run instead of the line to change by hand.

## What this does not see

A newer plugin can judge a change differently from the pinned one. Where the newer gate is stricter, a local tier is red on something CI would pass, and the developer sees which gate and that the plugin is newer. Where it is laxer, a local tier is green on something CI then fails; with a forge, CI on the pin still decides the merge. With no forge there is no CI: the local `merge` entry point runs `check` on the newer plugin's gates, and the pin then records what the repository was last upgraded to rather than what judged the merge. The line on every run says so, and nothing stops it.

The order is read from the version alone. A plugin release that lowered its version, or a fork that numbers its own versions, would be ordered wrongly. A copy with no `.git` was already compared by version only.

The announcement can be ignored. Nothing forces the owner to move the pin while the newer plugin needs nothing new, and a repository can run months ahead of its pin.

## Refutation criterion

This is wrong if, in the first ten plugin updates an adopted repository goes through, a change green on a local tier under the newer plugin is red in CI on the pinned commit more than once because the two plugin versions judged it differently: local runs would then have to run the pinned plugin, from a checkout the recipes keep, instead of announcing the newer one. It is wrong if adopted repositories keep a pin two or more minor versions behind the plugin in use for over a month: the announcement is then read by nobody, and the red of ADR 0025 should return for at least a minor version change. And it is wrong if, after a routine plugin update with `upgrade.py` naming nothing, a commit hook or a tier goes red at `plugin-pin` in any adopted repository: that is the wall this decision removes.

## Considered options

- Keep red on any difference (ADR 0025 as it was): rejected, every plugin update stops every commit in every adopted repository until a guard edit, and an owner who edits a line to be able to commit approves it unread.
- Red in `plugin_pin.py` whenever `upgrade.py` reports something missing: rejected, the pin check runs before every commit, and the conformance gate in tier 1 already reads the same rules; running them in the commit hook would make each commit wait on a whole conformance run to learn what tier 1 says anyway.
- Run the pinned plugin locally, fetched by commit: rejected for the reason ADR 0025 rejected it, a network fetch and a second plugin copy on every machine. Offline it cannot work under Claude Code, whose marketplace clone is shallow and loses the old commit on update.
- Run the pinned version from the harness's versioned cache (`~/.claude/plugins/cache/<marketplace>/ratchet/<version>/`): rejected, those copies carry no commit, are marked orphaned once the plugin moves on and are removed later, and the path is one harness's layout.
- Pin by version alone and accept any later patch release: rejected, it gives up the commit for every harness to fix one, and a patch release can change a gate.
- Green with a warning in the tier output alone: rejected, ADR 0025's objection holds for that; the route's `also:` line and the conformance gate's reds are what carry the upgrade to the owner and the agent.
