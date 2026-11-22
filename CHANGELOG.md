# Changelog

## 0.13.6

This release holds the 8 pull requests merged since 0.13.5. The largest change is to CI. Tier 1 runs as a few jobs instead of one job per gate, and the upgrade rewrites an unedited `ci.yml` to that shape. Some changes are stricter than before, in the guard hook, the feature preflight, conformance and the graceful-stop gate, so read the list of changes that reach the project before you upgrade. Issues #296, #291 and #328 are closed. Issues #139 and #302 each gain a part here and stay open.

Work the drain set aside could turn another ticket's gate red (issue #328). When the drain closed a ticket, or removed the tree of an interrupted run, it kept the uncommitted work as a git stash, which is a commit under `refs/stash`. A gate that reads every commit a ref reaches also read that stash. So one parked ticket's work could turn that gate red for later tickets whose branches never held it.

The proof could run gates side by side that `check` keeps apart (issue #291). `bite` did not read the `--serial` and `--alone` flags that a project's recipes pass `parallel.py`. No lane was told its share of the cores either, so the lanes together could start more workers than the machine has cores.

The graceful-stop gate's verdict rested on timing (part of issue #139). It sent SIGTERM a fixed time after the load began and judged only the load's own requests. Nothing made sure that a request was inside the service at the signal, so a service that drops a request it holds could pass.

A named pull skipped a check that every other sync makes. On the default branch, `git pull origin main` passed on a line the guard cannot read, or after a command that may change what the remote names. A refused merge also said that a merge job merges a pull request, even where the default branch holds no merge workflow.

The pipeline asked for a switch the owner had declined (part of issue #302). Its spec role was told to put every new behaviour behind a flag, whatever the `Flag:` line said, and no role was told whether the project is deployed.

Upgrade from 0.13.5 as the README's Upgrading section says. The upgrade moves the pin to 0.13.6, in the binding's pin line and in the workflows that check the plugin out at it. From an earlier version, the earlier 0.13 notes apply too. These changes reach the project:

- CI runs tier 1 as the jobs that the `justfile`'s new `tier1_jobs` list names (ADR 0090). The upgrade adds that list, computed from the project's tier lists, ceilings, shard count and workflow steps. It also adds the `ci-job` recipe and its Entry points row. A `ci.yml` that is still the unedited template of any release from v0.13.0 to v0.13.5 is rewritten to the new shape, with its plugin checkout line and every other line kept. Tier 1 then runs `bite`, `rest`, a long job for each gate of `tier1_sharded` as before, and a job for each gate that has its own seconds ceiling, is sharded with a `- Shards:` count over 1, or has a workflow step of its own. In CI each gate inside a group job such as `rest` is held to the lower of its own seconds ceiling and the tier's, by its own time, as each gate's own job was in 0.13.5. A group's total time is held only to a ceiling under its job name. A gate whose time cannot be read gets a `not judged` line, and its exit still decides the job. Any other `ci.yml` is kept as it is, and the upgrade owes the change in `quality/owed.toml` with the template's difference. A `tier1_jobs` that is the project's own, such as a list of its own recipes, is kept word for word with its `ci.yml`, and conformance prints one `offered:` line. No owed or offered item turns the project red.
- A rewritten `ci.yml` starts tier 1 without waiting for tier 0. Both tiers must still be green to merge. To wait again, put `tier0` back in tier 1's `needs:`. A gate whose every Gates row says `none:` or `later:`, and whose recipe is only the one `@echo` line the template writes, gets no runner. The shape job prints a line that names each such gate. A gate whose recipe does anything else runs in `rest`. For a list in the plugin's form, conformance holds every tier 1 gate in exactly one job. A gate in a job of its own with no ceiling, shards, sharded list or step to need it is red, and its edit moves the gate into `rest`. To keep it alone, name it with the reason on the binding's new `- Own jobs:` line.
- On the default branch of an adopted project, the guard hook now refuses `git pull origin <default>` in two cases where it passed before. One is a line that holds what the guard cannot read, such as `GIT_DIR=` or `--git-dir`. The other is an earlier command on the line that may change what the remote names, such as `git remote` or `git config`. For this named pull, `git add`, `git commit`, `git log`, `git status` and a plain test run such as `just test` do not count, and neither does a commit message written with `$(…)` or a here-document. An earlier command that sets a variable, runs a command named by a `$` value, names git or `.git` in any other way, or runs another git command such as `git reset` counts. A bare `git pull` or `git merge --ff-only` after any earlier command is refused as before. On the default branch of any other checkout, the hook asks. Run the pull in a tool call of its own. A `gh api graphql` call whose query comes from a file, through `--input` or `-F name=@file`, is asked about. Write the query on the command line, where the guard reads it. Where the default branch holds no `.github/workflows/merge.yml`, a refused merge names `just merge <branch>`, `just merge-ready --head <branch>` and the owner's own merge, and no merge job. A `-c color.*` or `-c advice.*` setting no longer turns a sync into a merge.
- The feature preflight refuses a binding whose `- Life events:` line holds a value it cannot read, such as `Deployed`, `deployed (since March)` or `none` with a reason after it. The check is `life-events`. Write `none`, or one or more of `deployed`, `irreplaceable-data` and `depended-on` joined by commas. The preflight also refuses `Flag:` lines that disagree, and a flag key over 80 characters. A part of a split that lacks an Operability line and names no parent, from a ticket that names one, is now refused with a line that quotes the `Parent:` line to add. The spec role, the ticket check and the review are now told whether the project is deployed, the parent's `## Conjecture`, `## Operability` and `## Answers`, and the owner's answer on the `Flag:` line. Where the owner chose to have the behaviour appear before deployment, no role asks for a switch.
- When the drain closes a ticket or removes the tree of an interrupted run, it sets the uncommitted work aside as one file under the git directory, in `ratchet-kept/<branch>/`, and writes no ref (ADR 0087). Its `kept:` line names the file. `drain.py restore <branch>` switches the checkout to the branch, or to the commit the work was on when the branch has moved, puts the oldest such file back there, and deletes the file once the checkout holds what it held. The restore refuses in one line, and writes nothing, when the checkout holds work of its own, or marks a path of the file assume-unchanged or skip-worktree. That line names `git update-index --no-<mark> -- <path>`. On a later failure it puts back what it wrote, keeps the file, exits 1 and names the command to run again. On each `clean` and `restore`, a stash that an earlier drain made is moved into such a file, when its message and the ticket's note prove the drain made it. Any other stash in the drain's form stays, and a line names `git stash pop --index stash@{<n>}`. A gate that reads files under the git directory still sees `ratchet-kept/`, so exclude that folder from it. A run that `/ratchet:feature` sets aside as `blocked` is still a git stash, so a gate that reads every ref still sees it.
- The graceful-stop gate holds a request across the signal where it can see the service take it (ADR 0084). When the load smoke names a route that takes a body, the gate sends the first such route one request, all but the body's last byte, and waits until it sees the service accept it. `--signal-after` seconds of load later it sends SIGTERM, then the last byte, and the service must answer with the bound status. A service that drops that request is red. While a request is held, the smoke counts no request that runs past `--timeout` and judges no floor. Where the gate cannot hold a request, as with no route that takes a body, it judges as before and prints `with_service: the gate judged the stop the older way, by the command's own requests, since <why>`. The upgrade does not rewrite a bound recipe. To have a request held, bind a route that takes a body and reads all of it before it answers. ADR 0084 lists what #139 still owes.
- `bite` keeps the schedule the recipes give `parallel.py` (ADR 0086). It reads every `--serial`, `--alone` and `--wide` a recipe passes, through the `justfile`'s variables. When it cannot read a recipe's call as text, it runs that recipe's gates with nothing beside them and prints one line that names the recipe and the words. Each proof gate is told its share of the cores in `RATCHET_JOBS`, which is the cores divided by the lanes, at least 1 and at most the cap. A gate that runs alone is told the cap. The proof runs as many lanes as before. A red that another lane could have caused, such as `RED BEFORE THE FAULT` or `TIMED OUT`, names the gates that ran beside it and the cure. The binding's Speed section takes three optional lines, `- Apart:`, `- Alone:` and `- Wide:`. `Apart` and `Alone` add to the recipes' flags in `check` and in the proof, and `Wide` in `check`. They take nothing away. The upgrade writes none of them. A gate that never reads `RATCHET_JOBS` runs as before.

These changes are smaller:

- The guard reads the default branch's ref, and each file it shows from it, once per tool call. Before, it read them again for each merge or cherry-pick on the line.
- `just ci-job NAME` runs one tier 1 job locally as CI runs it. A red group job names each red gate, and `just <gate>` reproduces it.
- One change touches only the plugin's own tests (#353).

## 0.13.5

This release holds the 22 pull requests merged since 0.13.4. Most of what reaches an adopted project is in the guard hook, the merge and the upgrade, and several of those changes are stricter than before. Read the list of changes that reach the project before you upgrade. Tickets #316, #302 and #99 each gain a part here and stay open.

A commit after a green final check was refused (issue #327). When the pipeline's preflight had written a branch's one-ticket start, a green `drain.py check --final` marked the queue note passed and left the start unpassed. The guard then denied a commit in the queue's work tree as `queue-final-check`. A green final check now passes the start too, and `open` and `update` close it first.

The guard did not read a heredoc body whose delimiter is unquoted (issue #46). Bash runs each substitution in such a body, so a body read with `<<EOF` that held `$(git push -f)` ran it, and the guard gave no verdict.

A product a gate boots could listen on every interface, and nothing said so (issue #54). The boot line had no way to name the host the ready URL uses.

Upgrade from 0.13.4 as the README's Upgrading section says. The upgrade moves the pin to 0.13.5, in the binding's pin line and in the workflows that check the plugin out at it. From an earlier version, the earlier 0.13 notes apply too. These changes reach the project:

- The upgrade writes more than the pin. It regenerates `.claude/settings.json`, keeping the project's own rules, and the deny list gains `Edit(/.git/ratchet-deep.jsonl)`. It replaces the step of `.github/workflows/merge.yml` that deletes a merged branch with the template's step, which keeps the branch while an open pull request targets it, or when the forge's answer cannot be read. If that step was edited by hand, the upgrade owes the change in `quality/owed.toml` instead. It also owes the line `.git/ratchet-deep.jsonl` in the `local-git` group of `guards/manifest.toml`. No owed item turns the project red.
- The guard hook refuses a merge an agent types, where it asked before (ADR 0082). This holds in a checkout whose default branch holds `guards/manifest.toml` and a `justfile` that defines `merge-ready` and `merge`, as an adopted project's does. It covers `gh pr merge`, a `gh api` call that merges a pull request, and, on the default branch, `git merge`, `git pull`, `git cherry-pick`, `git am` and `git read-tree`. The refusal names the way out. On a forge, the merge job merges a pull request once its checks pass, and `just merge-ready --head <branch>` prints each reason one waits. With no forge, `just merge <branch>` merges. A merge the judge leaves to the owner is the owner's to make. In any other checkout the hook asks, as before. On the default branch of any checkout, a merge of the branch it tracks, as in `git merge origin/<default>`, no longer needs `--ff-only`. A sync and a resume with `--abort` or `--continue` still pass there. A `git -c ...` sync of the branch it tracks, which passed before, now counts as a merge and is refused or asked about.
- The guard hook gives itself 7 seconds (ADR 0081). A tool call it has not finished judging by then is asked about with "The guard could not check this tool call". A refusal it found before then still stands. Make the tool call smaller. After a `cd` the guard cannot follow, it now also judges a relative path given to `git rm` or another git command, or written by an interpreter such as `python3 -c`, and refuses one that could reach a guard path. After a `cd` with more than 256 spellings, it refuses every relative write. Use an absolute path or `cd` to a literal directory.
- The guard judges each substitution in a heredoc body whose delimiter is unquoted. A command it refuses there is refused with a note to quote the delimiter, as in `<<'EOF'`. A body with an unclosed backtick or `$(` is asked about. Quoting the delimiter keeps the body as text.
- With no forge, the merge reads `.git/ratchet-deep.jsonl`. The new `just merge --deep` writes it. It runs `check-deep` on the tip of the default branch in a work tree of its own and appends a green, red or unrun line. A red that no later green covers holds every later merge for the owner, and the `next:` line says to merge what fixes tier 2, then run `just merge --deep` again. A line that does not parse holds every merge until the file is removed. After a merge, the `next:` line now names `just merge --deep`. `/ratchet:next` runs it in the background when a `next:` line names it, and `/ratchet:feature` runs it on `merge: wait`. With no record, nothing is held. On a forge, `merge --deep` refuses and points at the deep workflow. The merge workflow still passes the judge only the newest finished deep run, as 0.13.4 did, so a deep run still going holds nothing and #316 stays open.
- When a change gives a split part the agent-ready label, the merge judge rebuilds the part from its ticket on the default branch. If the part's `Parent:` line, its `## Parent`, `## Conjecture`, `## Operability` or `## Answers` section, or the spec it reads differs, the merge goes to the owner, and the reason names the piece. Write the part as the split writes it, or the owner merges it. Put an owner's note on a part under a heading of its own. A change that labels a part the 0.13.4 split wrote goes to the owner when the part's ticket has answers or a `Parent:` line, since that split did not carry them.
- A split part carries its ticket's `## Answers` and `Parent:` line, so the preflight reads the spec that line names, as it does for the ticket. A part whose ticket has a `Parent:` line the preflight refuses is refused too. Where carrying them would change how the part reads, `split` writes the part as before and says why.
- With local ticket files and no `origin` remote, `drain.py split` commits the parts on `records/split-<branch>` and lands them through the records merge, which judges the commit and runs `check`. If they cannot land, it exits 4 with a `not landed:` line that names the cause and the command to run again. During a merge or a rebase it files nothing and exits 2. A ticket blocked by a part that has not landed waits while a branch holds the part, and is refused otherwise.
- `drain.py open`, `update` and `check --final` stop when the one-ticket start in the git directory must change and cannot be written. Make that folder writable and run the command again.
- `/ratchet:feature` stops as `blocked` before the spec when a value it places on a command line is one git refuses, such as a base that starts with `-` or a branch named `HEAD`, or when the root is not an absolute path (issue #317).
- `with_service.py` replaces `{host}` in the boot line, its `--env` values and the command with the ready URL's host (ADR 0079). When the product answers on an address that is not loopback, a gate prints a note and its verdict does not change. `acceptance.py up` and `replay` refuse to start a session on such an address unless the justfile's `listens` names it. The upgrade writes none of this. To use it, put `{host}` in the boot line and copy the template's `listens` and `booted` lines. A boot line without `{host}` still boots, and the note and the refusal apply to it. Where the gate cannot see where the product listens, as on a system with no `/proc`, it prints a note, and `acceptance.py up` and `replay` refuse until the justfile's `listens` names each address.
- `bite` and conformance name a slow fault, one whose bite passes no `args` and whose recorded time is over the tier 0 ceiling (issue #294, ADR 0078). No verdict changes. Give the bite `args`, or `whole = "<reason>"` when it cannot narrow. A bite with both is red. `bite` appends one record per fault to `.ratchet/timing.jsonl`.
- An upgrade run on an open branch measures the owed ledger against the branch's merge base, and against `HEAD` on the default branch (issue #287). When the pin moves, it drops the entries the branch brought that one upgrade from that base would not write, and moves the others to the new pin. The plan names the branch to commit it on. `/ratchet:fill` step 6 takes the default branch's ledger only where it conflicts.

These changes are smaller:

- A pull request that targets a branch other than the default branch is held with a reason that says to change its target on the forge's page, which keeps it open (ADR 0083). The verdicts do not change.
- `merge_ready.py --deep` and `forge_runs.py deep --answer` read deep runs of every status, and `forge_runs.py speaks` is new. A `[ceilings.deep] unproven` bound in `quality/thresholds.toml` makes the judge print `merge: wait`, and `drain land` reads the reasons after it. No shipped template sets the bound.
- A forge answer or verdict record that is nested too deep or is not UTF-8 is read as unreadable instead of stopping the script. A `.ratchet/timing.jsonl` that cannot be written is named in a `not recorded:` line, and one that cannot be read no longer stops conformance.
- In an asked project that is not yet deployed, `/ratchet:conjecture` asks the owner whether new behaviour stays hidden until it is switched on, as `conjecture.flag`. It writes the answer under `## Answers` and a `Flag:` line under `## Operability`. The pipeline's roles are not yet given a parent spec's answers, and its spec role does not yet read whether the project is deployed, so #302 stays open.
- The acceptance proxy and the null product stop serving once whoever started the session is gone (part of #99, which stays open).
- ADR 0080 adds a rule to the tiers page, that no tier 0 or tier 1 gate rests on how much work fits in a number of seconds. No code changes. ADR 0088 is added as a record, and nothing builds it yet.
- Three changes touch only the plugin's own CI and tests (#335, #339, #341).

## 0.13.4

Five defects are fixed, and a tier ends sooner. Two of the defects are shared by every release from 0.12.4 to 0.13.3, and a real project's `/ratchet:feature` and `/ratchet:conjecture` runs found them. The upgrade check for this release found a third. From 0.13.0 on, an upgrade could owe a finding the project does not have and leave conformance red. Two of the changes are stricter than before, one in conformance and the `bite` gate and one in the feature preflight, so read the list of changes that reach the project before you upgrade.

`/ratchet:feature` could stop before the spec (issue #311). The pipeline's first step runs the feature preflight, and its command line held the script's path inside Markdown backticks. A shell reads backticks as a command to run first, so an agent that ran the line as written started the preflight with no ticket and then asked Python to open the ticket as a script. The run came back `blocked` on the `script` check, and its advice was to install a plugin that was installed.

`/ratchet:conjecture` recorded a digest of nothing (issue #312). The `- Criteria:` line holds a digest of the criteria a conjecture was drafted against, so that a later run tells from one command whether they changed. The command in the skill spelled a token that an agent harness replaces with the skill's argument. The command an agent ran therefore hashed no text, every line it wrote held `e69de29bb2d1d6434b8b29ae775ad8c2e48c5391`, and a conjecture never read as out of date after its criteria changed.

The upgrade owed a finding the project does not have (found by this release's upgrade check). `upgrade.py` decides what a project owes by running conformance in a copy of the project and at its base. That copy was a fresh repository with none of the project's history, refs or `.git/info/exclude`. In a project whose binding names a `- Synced:` commit, which `/ratchet:safeguard` writes once a formal model is bound, the copy raised "the '- Synced:' commit ... is not in this clone" and the upgrade wrote it into `quality/owed.toml`. In the project the commit resolves, so conformance was red on the entry ("an entry owes ..., which is no longer found"), and the `upgrade.py --apply` it named kept the entry. From 0.13.0 on, each upgrade of a project whose binding already named a `- Synced:` commit met this. The copy now borrows the project's objects and holds its refs, its HEAD, its shallow boundary, its `info/exclude` and the main work tree's HEAD reflog. No file in the project's `.git` changes.

A fault that no longer planted merged green (issue #306). A change could break a fault in `quality/bites.toml` and still merge green: a patch that stops applying, a `replace` whose text is gone, a `remove` glob that matches nothing. A pull request proves only the gates of tiers 0 and 1, so the deep run went red on a later, unrelated merge, and whoever merged next had to find the cause.

The feature preflight misread `## Operability` (issue #178). It took a sentence that begins with a line's name, such as `Flagged rows are red.`, as the `Flag:` line. It let a `none` with no reason through when markup surrounded it, as in `` `none` ``, `**none**` or `- none`. It refused a ticket that left out a line its parent spec states. It named `Parent: ../spec.md` as `.scratch/f/issues/../spec.md`, a path git cannot read.

A tier started its gates in the order it declares them (issue #197). A long gate declared late queued behind short ones, so a tier held to a time ceiling went red when the machine was a little slow.

Upgrade from 0.13.3 as the README's Upgrading section says. The upgrade moves the pin to 0.13.4, in the binding's pin line and in the workflows that check the plugin out at it. From an earlier version, the earlier 0.13 notes apply too. These changes reach the project:

- Conformance and the `bite` gate check that every `patch`, `replace` and `remove` fault in `quality/bites.toml` can still be planted, in the work tree as git sees it. They plant nothing and run no gate. A fault that already cannot be planted is written into `quality/owed.toml` by the upgrade, as `bite '<name>' of gate '<gate>' cannot be planted`, and does not turn the project red. That entry holds while later changes move why the fault fails. A later change that breaks a fault is red on its own pull request. A part of a fault whose file git does not see is not checked only when the registry has a `setup`, which may make that file in the scratch copy, and neither the merge base nor HEAD holds the file. Conformance and `bite` then print a `not checked:` line for it, and the full proof plants it. Without `setup`, a file git does not see is red.
- The feature preflight can refuse an agent-ready ticket it passed before. An operability line now counts only as its name and a colon, as in `Flag:`, `- Flag:`, `- **Flag:**` or `- **Flag**:`, so prose that only begins with the name is a missing line. The preflight refuses a `none` with no reason in any markup: `` `none` ``, `**none**`, `_none_`, `- none`, `none:`, `(none)`, `none: n/a`. A `none` followed by a reason passes, as in `none: the command only reads`. A new `parent` check refuses a ticket whose `Parent:` is absolute or resolves outside the repository. The cure is to state the line, or `none` and why, and to point `Parent:` at a spec inside the repository. A line the ticket leaves out is now read from its parent spec.
- The upgrade plans in a copy that shares the project's history, so a check that reads history answers as it does in the project. An upgrade now lists a true `formal_group` finding (a `- Model:` path outside the default branch's `formal` group) as red and exits 1, where it used to exit 0 and leave the project red. A true `default_branch` finding is now owed. No upgrade newly refuses. A project that upgraded to 0.13.0 through 0.13.3 and holds the wrong "not in this clone" entry has it dropped by this upgrade.
- A tier starts its longest gates first, from the times its last runs recorded. No verdict changes.

A conjecture whose `- Criteria:` line holds `e69de29bb2d1d6434b8b29ae775ad8c2e48c5391` is redrafted once, the next time `/ratchet:conjecture <ticket>` reads it. Whether its criteria changed since it was written cannot be known, so the redraft is the safe path. Every line under `## Answers` stays, and no question is asked unless the criteria now open a fork or contradict an answer. The skill will say the criteria changed since the conjecture was written, which may not be so. On a forge the issue is edited in place. With a local tracker the rewrite lands as any ticket write does, and a ticket that already carries the agent-ready label lands by the owner's merge.

- Every value the pipeline places on a command line a role runs as written is one quoted shell word: the preflight's script path, ticket, base and branch, and the quote check's script path. A space, a `$`, a quote or a backtick in any of them reaches the script unchanged.
- When the preflight prints no result, the failure says which side is at fault. A script Python cannot open is the install. Anything else is the command the pipeline built, and the advice is to report it.
- `scripts/criteria_digest.py` prints the criteria digest, and the conjecture skill runs it in place of the one-line command. Git computes the digest, as the one-line command meant it to, so a line recorded correctly still matches, in a SHA-1 and in a SHA-256 repository.
- The skill reads three outcomes. A digest is compared with the line. No criteria heading in the body means the criteria changed. A command that could not run is reported, and nothing is redrafted.
- The plugin's tests hold both classes. Each command line a role prompt shows is run through a real shell with hostile values, and no skill page may hold text a harness replaces with the skill's argument.
- A fault that cannot be planted is named with its gate, its file, what failed and the cure, as in `bite 'deep fault' of gate 'slow' cannot be planted: quality/bites/p.patch no longer applies: error: patch failed: src/code.txt:1; ...; make the patch again against the file as it is now`. Where a `replace` fault, or a `patch` fault that otherwise applies, changes a file git does not see, the cure says, if the file moved, to point `path` at its new name or make the patch again against it, or else to remove the bite and add one that plants.
- When a change reaches a tier 2 fault, `bite` names that gate and says each of its `patch`, `replace` and `remove` faults was checked to still plant. A `write` fault is planted only by the full proof. `bite-all` and `--gate` still plant each fault and report `THE FAULT WAS NOT PLANTED`, as before.
- A gate's expected time is the median of its last five times in the tier's `--report` file. A gate with no recorded time starts before the recorded ones, and equal times keep the order given, so a missing or unreadable record gives the declared order. `--alone` gates still run last, and under `--fail-fast` the gates start in exactly the order given. In CI the record starts empty, so a CI tier keeps the declared order until the record reaches the job (issue #197 stays open for that).
- The preflight's report names the ticket's parent as a path git reads, and says whether the binding declares the `deployed` life event.

## 0.13.3

A defect of 0.13.0 to 0.13.2 is fixed, which a real project's `/ratchet:fill` found. The fill binds the comments lister, and `just comments --record` writes every comment the project already has into `quality/owed.toml` as owed. On that project it wrote 1,917 entries, 11,493 lines. The tier 1 gate `change-size` counted the ledger as product code, so the fill change was red at 11,937 lines against a ceiling of 1,500. The change cannot be split, since only the change that binds the lister may record. A project with a few hundred comments hits it.

Upgrade from 0.13.2 as the README's Upgrading section says: the upgrade moves the pin to 0.13.3, in the binding's pin line and in the workflows that check the plugin out at it, and changes nothing else in the project. From an earlier version, the earlier 0.13 notes apply too. Land the upgrade before the fill.

If the fill's pull request is open when the upgrade lands, move its ledger onto the new pin, or `ratchet-lowered` is red on every `comments` entry. The fill skill's step 6 now says how: merge the default branch in, take its `quality/owed.toml`, run `just comments --record` again, then run `upgrade.py --apply` again to drop the entries the fill already settles. On a copy of the project, that path left the comments owed since 0.13.3, with `ratchet-lowered` and conformance green and `change-size` at 279 lines.

- `change-size` does not count `quality/owed.toml`, in either direction, as it does not count the method's records and the lockfiles (ADR 0076, amending ADR 0045). The ledger's growth is still held. `ratchet-lowered` is red on an entry a change adds, save on a pin move, in the change that binds the lister, or on a rename. The ledger is a guard path, and the merge judge passes it without the owner only when entries go and the pin does not move.
- A `/ratchet:prune comments` batch is sized by the comments it deletes and what encodes them, not by the ledger lines it drops. An owed comment holds about six ledger lines, so a batch under the ceiling now holds about seven times as many comments.

## 0.13.2

A defect of 0.13.0 and 0.13.1 is fixed, which an upgrade of a real project found in CI. Every upgrade from 0.12.x to either version was red on the tier 1 gate `bite`. The upgrade owes the bite gaps it cannot write, in `quality/owed.toml` under the rule `bites`, and conformance accepts them as owed. But `project_bite.py` read no ledger, so `bite` was red on the same gaps. Every such upgrade hit it, since the `comments` gate is new in 0.13.0 and no project upgrading from 0.12.x has a bite for it.

Upgrade from 0.13.0 or 0.13.1 as the README's Upgrading section says: the upgrade moves the pin to 0.13.2, in the binding's pin line and in the workflows that check the plugin out at it, and changes nothing else in the project. From an earlier version, the 0.13.0 notes apply too.

If a pull request that upgrades to 0.13.0 or 0.13.1 is still open, close it and upgrade the default branch afresh. Running the upgrade again on that open branch turns `ratchet-lowered` red on every entry the first upgrade owed (issue #287, to be fixed in 0.13.3).

- The bite gate excuses a gap that `quality/owed.toml` owes under the key conformance builds for the same finding. It prints each owed gap, then their count and the version they are owed since, such as `bite: 2 gaps owed since 0.13.2, listed in quality/owed.toml`. Then it proves the bites the registry holds, as before.
- It stays red on any gap no entry owes. A ledger it cannot read is red only when there is a gap to excuse.
- ADR 0075 amends ADR 0037, which said a finding that another tier 0 or tier 1 gate is red on is never owed. Held to here, that rule kept every upgrade red, the state ADR 0037 was written to end. The owed list still only shrinks: `ratchet-lowered` and the guard on `quality/owed.toml` hold its growth.

## 0.13.1

A defect of the route is fixed, one 0.12.4 and 0.13.0 share, which the first-hour evaluation of 0.13.0 found. When a parked ticket's guard edits sat on a branch whose merge the merge judge leaves to the owner, the route could not see that branch from the default branch, and the owner's answer on the ticket released the whole park, its guard part with it. So the route named `/ratchet:feature` for a ticket still held, and the owner went round the same hold again. Upgrade from 0.13.0 as the README's Upgrading section says: the upgrade moves the pin to 0.13.1, in the binding's pin line and in the workflows that check the plugin out at it, and changes nothing else in the project. From an earlier version, the 0.13.0 notes apply too.

- From the default branch, the route names the owner's merge of a branch the merge judge leaves to the owner. It names such a branch only when it carries its change description under `.scratch/changes/`, no run is attached to it, no other work tree has it checked out, and it is not a `formal/` branch. The step says the judge's first reason and the command that prints every reason. Deleting the branch instead brings back the step that lands the guard edits.
- Each part of a park is released by its own cause, and the route names only the parts still open. An edit of the ticket, such as the owner's answer, releases its questions and its size. The guard part is released when the guard edits it named land, or when the branch holding them merges, and by a ticket edit only for a park recorded with no base commit. A dispute the run filed counts as a guard hold, and the guard paths it covers as its causes. A park that names no kinds, such as one written from a result that was not JSON, is released as before, by an edit of the ticket or by its causes landing.
- `drain.py close` and `split` refuse a `--result` file that holds no JSON object: they exit 2 and write no note. Such a file used to write a hold with no kinds, which held the ticket with nothing to release it.

The route model took its turn first. It gains a branch that waits for the owner's merge and a ticket parked for both questions and guard edits, and five new rules hold. The route of 0.13.0 breaks them, and its shortest break of the rule that no build is named while a hold's cause is unmet is the evaluation's own run, 16 steps.

The first-hour evaluation's sandbox refuses fewer harmless commands, the plugin's own bytecode setting among them. Nothing the plugin ships changes.

## 0.13.0

Upgrade from any version from 0.10.0 on, as the README's Upgrading section says. The upgrade writes most of what is new and records the rest as owed, so it is green when it lands. Three things follow it:

- **Type `/ratchet:fill` next.** Until it lands, no feature and no drain starts. It gives every Markdown file a kind, fills the Comments table and binds its lister, records the comments the project already has, adds a Triggers row for the new `state-machine` kind, and writes the suites table that `tests-changed` reads.
- **Ask an agent to work what the upgrade left owed.** `quality/owed.toml` lists it: the comments gate's bites, the `- Published interface:` line, the `- Incremental:` line, the shared suite report, and the formal verification rows once their Triggers rows exist. Then `upgrade.py --apply` drops what is settled.
- **Read what the upgrade changed in your justfile and merge workflow.** It adds `comments` to the tier 0 gates, `RATCHET_RUN` to the justfile so a run shares one suite, and `--tier1` to `.github/workflows/merge.yml` so a red tier 1 holds merges.

One gate is loosened on purpose: a project that does not declare `library` has `documentation coverage, ratcheted` rewritten to `none: not a library`, since a doc comment outside a library's published interface is now red (ADR 0063).

Comments are red, and the code is the context (ADR 0063).

- A new tier 0 gate, `no prose comments`, allows a comment only where a tool reads it: a line that a kind of the binding's `## Suppressions` matches (`kept` names a directive that silences nothing), or a pinned action's version note. A doc comment is allowed only on a library's published interface.
- The project writes its lister, `just comments-list`, which prints `path<TAB>line<TAB>kind<TAB>text`. The lister is a guard path.
- The comments a project already has are recorded once, by `just comments --record` in the change that first binds the lister, and owed. They follow renames. A comment with no text cannot be recorded, so it is deleted.
- The plugin writes no comment into a project. The ledger's header is gone, recipes copied from the template carry none, and the templates and the catalogue ship comment-free.
- Outside its CI workflows and the eval sample projects, the plugin itself holds no prose comment or docstring. It keeps the docstrings its code prints or checks and the directives tools read. Each constraint a comment stated is now a test seen to fail when it breaks, or a line of an ADR. The workflows' comments go in a later change.

Every document has a kind, and the ones that steer a feature leave with it (ADR 0064).

- The catalogue names a kind for each Markdown file the method writes. Standing kinds stay. Steering kinds (a feature's spec, a local unit and its tickets) expire. The binding's `## Documents` adds the project's own, and conformance is red on a Markdown file of no kind.
- A local unit expires when its last ticket closes and no open ticket names its files. A feature spec expires once its review verdict exists. The feature step and the drain delete it and carry each `## Answers` line, dated, to the brief's `## In the owner's words`.
- The merge judge holds a change that makes a unit expire and leaves it in the tree. `ratchet-lowered` is red on a change that deletes a spec or ticket and drops one of its answers.
- The first drain after the upgrade deletes the units and specs that are already finished.

`/ratchet:prune` deletes what the code already says (ADR 0065).

- `/ratchet:prune comments` works through the owed comments in batches the change-size ceiling allows. It deletes each one, or first encodes the constraint it claims as a test seen red, a type, a lint rule or a decision record.
- `/ratchet:prune pages` cuts what a page of a `pass` kind repeats of the code, once `- Prune after:` merges (10 by default) have landed and the page cites a path that changed after it. `documents.py cited` is red on a cut whose citation is gone.
- The owner is asked nothing. A fresh review agent reads every removal, and its verdict lets the merge judge pass the change. The route offers both, and neither is ever red.
- The merge judge leaves to the owner any change that removes a heading a page's kind keeps, or deletes such a page whole, and any prune pass that touches a page not of a `pass` kind.

Formal verification is asked of the first concurrent code, parser or state machine (ADR 0062).

- Three tier 2 rows, `formal verification of concurrency`, `of untrusted input` and `of state`, and a new code kind, `state-machine`. Each row's proof is a break planted in the code the model mirrors, with the model unchanged, which the check catches.
- The binding's new `## Formal verification` holds `- Model:`, `- Mirrors:` and `- Synced:`. `/ratchet:safeguard` writes the model with `pstack:create-formal-verification-skill`, and the route offers a round of upkeep with `pstack:maintain-formal-verification-skill` when mirrored code moves past `- Synced:`. Upkeep is never red.
- On the upgrade, a row whose kind the project already has is owed at once.

Fuzzing is coverage-guided, and a solver searches the first parser (ADR 0070).

- A bound `fuzzing` row needs a `guarded-crash` bite: a crash reached only through an exact value of four or more bytes that the parser compares against.
- `symbolic checking` is now asked when the first untrusted input arrives, and its bite is `related-fields`. The upgrade moves a `later: code:formal-model` row to the new trigger and owes both bites.

The suite runs once per check (ADR 0071). The binding's `## Shared reports` names the producer, and `report.py` runs it once per run and tree for every gate that reads it. A local `check` runs the suite once, under coverage, where it ran it four times. A local `test` is slower, since it is now instrumented. CI is unchanged.

A finding quotes what it breaks (ADR 0072). A blocker or major from the critic or the reviewer names a record and quotes it, and only a finding whose quote that record holds is owed a fix. The publish check reads the run's result and is red on an owed finding whose quote is not found. An unfound quote goes to `unstated` and starts no fix round, and a fix loop that is not converging stops.

A check runs the test files a change touches (ADR 0073). The binding's `## Tests` gains a suites table, and the new tier 1 gate `tests-changed` runs the test files a change adds or edits through each suite's own recipe. Each role in the pipeline runs it while it works, and the preflight names the gates a ticket's criteria hold.

A red tier 1 on the default branch holds merges, as a red tier 2 does (ADR 0069). Until a later push to the default branch is green, the merge judge leaves every pull request to the owner. A project with more than 100 runs of a workflow no longer reads tier 2 as red.

Every line of the timing record carries the time, the run and the tree, and a feature run states its time, its `check` runs and how many judged a tree already green. A path the `- Local caches:` line names that does not exist is printed and recorded.

The skills say only what the code cannot (ADR 0066). Each sentence of a skill page is an instruction, a reason or a restatement of what a script does, and the restatements are gone. The pipeline's loop stays whole, since other harnesses run it by hand from the page.

Smaller fixes:

- The upgrade puts the plugin's interpreter on the template's inline Python lines, and carries the jobs lines into a justfile older than `ratchet.jobs`.
- The marketplace entry names the HTTPS address, so an update needs no SSH key.
- Gates that each use every core share a schedule group, and the runner names a group whose chain outlasts every gate beside it. An upgraded justfile keeps its groups. Copy `serial1` from the template to take the new one.
- The binding names the mutation tool's incremental mode, and the recipe takes it locally, never in CI or a bite.
- A drain `check` after an interrupted `update` says to run `update` again, and the guard refuses a commit while an update is under way.
- A one-ticket feature run's commit waits for its publish check.
- Five scripts print their whole description under `--help`.

Decided, with no code: the plugin keeps its own runner, and a container pipeline engine is a project's to bind under its own entry points (ADR 0074).

The models took their turns. The drain model obeys the guard's final-check gate, and found the interrupted-update defect fixed above. The route and drain models let a steering unit leave with its last ticket, a shared unit with the last ticket to land, and carry its answers to the brief as records. The pipeline's charts are drawn from today's model.

## 0.12.4

The first tagged release. Upgrade from any version from 0.10.0 on, as the README's Upgrading section says. The first upgrade to a tagged release starts from a red pin line that names the command to run.

Releases are tagged, and a project pins the plugin by its content (ADR 0068).

- The marketplace entry installs the tag, not the default branch's head.
- The binding's pin line holds the release's version, its commit and the id of its tree. Every tier's first line names the plugin copy it runs, and a copy whose files do not hash to the pinned tree is red before any gate runs. Every `just` a tier starts checks again.
- A session records the plugin folder it runs from in `.git/ratchet-plugin`, and the adopted justfile reads it, or `RATCHET_PLUGIN` first. The guard refuses an edit of that file, and the settings step adds a deny rule for it, so an upgraded project gets one settings change. Where a harness keeps earlier versions beside the recorded one, the justfile looks for the pinned copy among them.
- The plugin's scripts, and the guard and session hooks under every harness, run with Python told to ignore its environment variables and the user's site folder. Their compiled files go to a folder only this user can write. Where that folder is not private, nothing is compiled to disk, and each run says so in one line that names the folder, why, the cost and the way out.
- Conformance names, by line, each recipe that calls a plugin script with the bare interpreter, and the upgrade rewrites it. It also names a recipe that uses the plugin's folder where the checked folder cannot reach it, such as a shebang recipe or a `:=` expression, and a `pre-commit` recipe that is not the template's.
- What the check does not hold is recorded as accepted risk: a file changed between the check and the run, a changed `PATH` or a variable the project's own tools read, and a compiled file this user's own process leaves in the private folder. CI runs the plugin from the forge on a fresh runner, so it catches what any of them changes.

On a pull request, the proof that gates can fail proves only the gates the change could affect (ADR 0069, issue #168).

- The proof narrows on what a record declares and never on what it infers from code. A changed fault, a patch a fault names, a row of the Gates table, a file a fault edits, and a tool-config path under a bullet of the binding's new `## What each gate reads` section each name their gates.
- Anything no record assigns proves every gate: every path in the `gate-commands`, `gate-tools`, `fitness` or `formal` groups, the justfile included; a change to the registry's `setup` or `include`; a file under `quality/bites/` that no fault names; any `.gitignore` or `.gitattributes`; the plugin pin; a tool-config path no bullet names; and a binding change outside its gate rows.
- A change that touches no path that defines a gate and no file a fault edits proves nothing, as before. The full proof runs after the merge and finds a product change that defeats a fault.
- The pull request's proof is held to its own ceiling, `bite`, where one is recorded and never above the full proof's; else to the full proof's, `bite-all`; else to its tier's. Record `bite-all` after the upgrade: until then the pull request's proof is held to the tier 1 ceiling, and the full proof to the tier 2 ceiling where one is recorded, else to none. Conformance asks for the key and says how long the last timed full proof took.

The drain refuses a commit made before its final check (issue #206). In a queue work tree whose start has not passed `drain.py check <ticket> --final`, the guard refuses `git commit`, `am`, `cherry-pick` and `revert`, and names that step. A commit that lands before the final check still parks the ticket, and the line now names both causes: a role committed, or the orchestrator committed before the final check. A one-ticket run of `/ratchet:feature`, which has no drain, names its own check instead.

Records are found by their content, not by a branch's name (issue #191). The route names any unmerged branch that holds only records, whatever it is called, and the merge deletes such a branch once it is merged. When the upgrade drops stale owed entries, it keeps every other byte of `quality/owed.toml`.

Faster:

- An upgrade tries each of conformance's fixes once per file content, and lists the repository's files about a third as often as it did.

Decisions recorded, with their code still to come: fuzzing is coverage-guided and a solver searches the first parser (ADR 0070); a check runs the suite once, and every gate that reads it still runs alone (ADR 0071).

The models took one more turn each. The route model defines a waiting records branch by what it holds, and its walk leaves records on branches of either kind of name. The drain model says where a commit in a queue work tree is refused, and its walk asks the real guard at every state.

## 0.12.3

A defect of 0.12.2 is fixed. Under 0.12.2, the proof that gates can fail ended a slow gate's runs early, so its faults read `TIMED OUT` and tier 1 was red on any change to a path that defines a gate, the upgrade to 0.12.2 included. If you saw that, upgrade: nothing in the project needs to change, and the next proof is judged under the new limit. Upgrade from any version from 0.10.0 on. Run the upgrade as the README's Upgrading section says: until installs come from a tagged release, from a clone at the commit this version was published at.

- 0.12.2 ended one run of a gate, in the proof that gates can fail, after the gate's own time ceiling, else its tier's (issue #193). Those ceilings bound warm runs in the project's work tree, and a proof's run is in a fresh copy with no cache, so a slow gate was ended early and its fault read `TIMED OUT`. A run's limit is now `--limit` where given, else `bite-run` under `[ceilings.seconds]` in `quality/thresholds.toml`, else 900 seconds, and no gate's or tier's ceiling is read for it. The limit only ends a run that hangs, and the proof's first line says which of the three it is.

The upgrade keeps a project's own words (issue #188). Where a project had written rules of its own on a companion line of `AGENTS.md`, making the line the plugin's name alone used to leave deleting them as the shortest way to green. The upgrade now moves each such item whole, wrapped lines and nested bullets with it, word for word to the section `## Moved by the Ratchet upgrade` at the end of the same file. The finding says the text moves and is not deleted, and the upgrade's report lists what it moved. Read that section after the upgrade, move what still applies, and delete it.

The models took two more turns, each ahead of the script change it describes. The drain model has the check without `--final`: a role's commit after it never lands, and `land` names the final check as the way on. The route model has a conjecture gone stale: the real route still names a build for a ticket whose criteria changed after its conjecture was written, and a strict expected failure pins that until the route and the preflight read the digest.

## 0.12.2

Two defects of published versions are fixed, both in the proof that gates can fail. Upgrade from any version from 0.10.0 on. Run the upgrade as the README's Upgrading section says: until installs come from a tagged release, from a clone at the commit this version was published at.

- 0.12.0 and 0.12.1 could delete or overwrite a file in the real work tree while proving that a gate can fail (issue #172). Restoring the scratch copy between runs let git check a path for a link and then delete or write it by path, so a process that `setup` left running could swap a directory of the copy for a link to the real tree in between, and git would remove or write the real file. Restoring now only lists with git, and removes and writes every file through descriptors pinned to the copy; a link is unlinked and never followed. One window remains: a process that `setup` left running and that swaps the copy's `.git` for a link could still make git write the real repository (issue #175).
- From 0.10.0 up to 0.12.1, restoring the scratch copy trusted file timestamps. A gate or a process that `setup` left running, if it rewrote a tracked file in place with content of the same length and put its modification time back, left that content in the copy. The next runs of that gate were then judged on a tree the repository does not hold. Usually this shows as `RED BEFORE THE FAULT` or `NOT PROVEN` on a later bite, or a later bite is reported as not biting when its fault alone would. In the rare worst case, where the gate is red only with the leftover and the fault together, a later bite reads `bites` when its own fault alone would not make the gate red. Restoring now compares every file's content with the snapshot.

The proof that gates can fail says how long each fault took (issue #168). Each verdict line ends with the wall time of its runs and the lane they ran in. A run that passes its time limit is ended and reads `TIMED OUT`: the limit is the gate's own ceiling, else its tier's, else 900 seconds, and the proof's first line names it. `TIMED OUT` covers only that limit; a tool that times out inside a gate still reads by its exit code. A red proof prints the machine's load and core count at its start and end, and a red verdict that took more than twice its gate's fastest green run says so. A green tier that used 75 percent or more of its time ceiling prints a warning. Nothing here makes the proof faster: proving only the gates a change could affect is planned (issue #168).

The drain runs the publish check (ADR 0060). `open`, `update`, `check` and `land` each check that no pipeline role committed, pushed or opened a pull request during its run. `check --final` marks the recorded start as passed, and `land` refuses a ticket whose start is absent or unmarked. A rebase that drops a commit the base already holds is not publishing: each dropped commit must have a twin of its own on the base, with the same message and author date. A single-ticket run of `/ratchet:feature` checks the same after each pipeline run, and stops with the run stashed when a role published.

The pipeline's review:

- A guard path that an open dispute names is no finding (ADR 0067). A gate tightened in the same change as its code can now reach approve.
- A criterion of the ticket that the change does not meet is a major finding on the spec axis.

The `Flag:` line is asked before deployment (ADR 0061). The feature preflight asks it of every declared type, whatever the life events, and the route prints an `also:` line for each switch past its removal date.

Companion plugins are named, never restated (ADR 0060). A companion line in `AGENTS.md` is the plugin's name alone, read from the plugin's own marketplace entry. The route names words for the session and never a companion's command. The guard's refusals of a force-push and of an amend of a pushed commit name Ratchet's own step as the way out. The upgrade rewrites only the plugin's companion lines.

Conformance holds every workflow step and permission to a marker of its shape, so a template fix now reaches an adopted project (issue #166). An adopted project can see up to nine new findings on its first run after the upgrade, each with its fix.

The settings step writes a harness-level deny rule for the guard's event log, beside the guard's own refusal. The rule reaches the main checkout; in a linked work tree the hook alone guards the log.

`/ratchet:conjecture` writes the size alone on a ticket whose conjecture is current but has no `## Size`, and writes a ticket whose size passes a ceiling instead of stopping: `/ratchet:feature` splits it by size before it builds (ADR 0046).

The route model took one more turn: the `Flag:` line is settled in it, a spec's line is read as the preflight reads it, and 18 of its 20 rules hold on the real route. The two that break are pinned to an adoption that stopped after its pin, which the route still tells to build.

Decisions recorded, with their code still to come: installs come only from a tagged release, and a project's local gates run only a copy of the plugin whose content is the pinned release's (ADR 0068).

## 0.12.1

A ticket that waits on a split ticket now waits on its parts (ADR 0046). The drain used to refuse it for good while the split ticket stayed open, and started it early when the owner closed the split ticket by hand. One reader, `preflight.blockers()`, now reads a split blocker as its parts, whether it is open or closed, for the drain and for the one-ticket preflight. The drain model found both breaks before the fix was written.

The guard keeps a log (ADR 0060). Each tool call it denies or asks about appends one line to `ratchet-guard-events.jsonl` in the repository's git common directory, shared by every work tree and never shown by `git status`. The guard's answer is written first and never waits on the log. The log is a guard path of its own.

`/ratchet:conjecture` notices criteria that changed after the conjecture was written. `## Conjecture` ends with a `- Criteria:` line that holds a digest of the acceptance criteria. When the digest no longer matches, the skill drafts the conjecture again, keeps every answer, asks only the forks the change opened and shows what moved. The route and the preflight do not read the digest yet.

The first parts of the publish check are in, and nothing calls them yet (ADR 0060). `scripts/publish_check.py` says whether a pipeline role committed, pushed or opened a pull request during its run, and the feature preflight records the start state the check reads. `scripts/comments.py` judges the comment records a project's lister prints, for the zero-comments rule, and is not wired to a gate.

Gates and proofs:

- The graceful-stop proof no longer races its own stop signal, and `check` shares the machine's cores with other runs.
- A gate that needs a port picks one below the range the system hands out to outgoing connections, so a busy machine no longer takes it first.
- The jobs cap reads git's `ratchet.jobs` setting after `RATCHET_JOBS`.
- A red conformance finding prints what it asks next under it.
- CI cache keys carry the run, so a cache saves again after its first save. Conformance asks an adopted project for the same change and writes the fix.
- The merge job closes the issues that a pull request it merges says it closes.

The pipeline grades from one severity list. The critic and the reviewer both cite `SEVERITY` in `workflows/pipeline.js`. The critic is stricter for it: a secret or a personal identifier in a span attribute or log field is a blocker, and behaviour shipped on with no flag is a major finding.

The models took six more turns, each ahead of the script change it describes: a pipeline role that publishes unseen, the drain's split step and its publish check, the safeguard that binds a row and adds an instance, and the route's `Flag:` line before deployment. Where a script lags its record, a strict expected failure pins the gap and turns red when the script is fixed.

Decisions recorded, with their scripts still to come: formal verification of the first risky code of each kind (ADR 0062), the zero-comments rule (ADRs 0063 to 0066), and installs that come only from a tagged release (ADR 0068). ADR 0059 says a stack that compiles builds once and reuses its cache.

The agent-facing pages under `skills/` and the harness pages are rewritten for the agents that read them. Intent and pinned phrases are unchanged.

## 0.12.0

Version 0.11.1 was an interim cut of this release, published so that an adopted project could pin it while the evaluation runs went on. It has no entry of its own: everything in it is listed here.

The plugin's state machines are written down as models, and the real code is held to them. The route, one pipeline run, the queue drain and the merge, the safeguard a project owes, and the threshold ratchet each have a model under `hooks/tests`: every state it can reach, and the rules each state and step must keep. The tests prove every rule on every state, and play the real scripts against the model, so a change that breaks a rule is red. Each model has a page with its charts under `docs/model`: `route.md`, `pipeline.md`, `drain.md`, `safeguard.md` and `ratchet.md`. Most of the fixes below were found this way.

The queue drain finishes what a killed or concurrent command left, and no longer lands a held ticket or loses track of a merge (ADR 0023).

- `land` refuses a ticket the drain holds. A merge is recorded once: `close` of a merged ticket is refused.
- A rerun after a kill finishes the step. `land` finishes the discard it was cut off in, `update` keeps its rebasing mark until the outdated verdict record is gone, and a refusal is counted once. `update`, `report` and `clean` record a merge that no note says, and `update` no longer rebases a branch that already landed.
- `update` making a work tree again counts against the cap, `split` takes the drain lock, and `open` keeps a parked note the report has not listed yet.
- A landing holds a lock of its own until it ends. `report`, `clean` and `clean --all`, from any drain, leave that ticket's note and work tree alone while it runs, and say so.
- A `report` ends only its own drain's lease. `clean --all` is refused while a program runs in another drain's work tree, whether a lease is held or not.
- A fast-forward cut off half-way is put back before any drain command reads a ticket, so a ticket parked meanwhile is held on its own text. While a git command still runs in that work tree, every drain command is refused and says to run it again.
- `merge.py` removes an empty scratch directory a killed run left, once it has stood a minute.
- A second `land` of a ticket is refused while its landing is still running, and says to run it again once that one ends. It went on with no lock, and could record the merge and remove the work tree under the landing that was alive.
- `clean --all` leaves the work tree and the note of a live landing whose earlier close was cut off before it removed the tree.
- `update`, `close` and `split` of a ticket refuse with exit 3 and change nothing while that ticket's landing runs. Before, a ticket parked during its merge could be merged without the owner.
- A `--take-over` of a drain that is still alive is the owner's call. A landing that drain runs keeps its work tree while it runs, and the next `clean --all` takes it once the landing has ended.
- The drain sees a landing while its `land` or the merge it started is alive. Each `land` holds a private lock the merge probes, and `merge.py` holds its own lock for its whole run, so a `land` killed alone no longer lets ticket commands, `clean` or a second `land` act under a live merge. `merge.py` never removes or creates the file `RATCHET_LANDING` names, and it refuses one outside the repository's git directory (ADR 0058).

A gate's proof can no longer write into the real repository through a link. A setup left running could swap a directory of the scratch copy for a link into the real tree; the snapshot then held the link, and each restore wrote it back. `bite` now refuses any link that setup made or changed whose end lies outside the copy, and names it. It judges the link by its text in the snapshot, through every link on the way, not by the copy as it stands, and refuses a ring of links. A link the commit already holds is the repository's own and stays.

A project that is not yet depended-on gets one fix pass on a review blocker: a fresh fix agent and a second review, keyed on the review's own blockers whatever its verdict (ADR 0056). A lost ticket promise still parks the ticket. Such a run spawns at most nine agents, not seven.

The change size is read after each fix round and after a rejudge's reconcile, not only after the spec and the build (ADR 0055). A round that takes product code over its ceiling stops the run as too large, so the drain splits the ticket instead of parking it on a red check. A pipeline run that errors is resumed once, then closed to be tried again; after three such closes in a row the ticket is held, and the reason says the run errored each time. A guard hold written before the drain recorded its base commit is released by a change to the ticket, and the route names that step.

The README is rewritten for a solo builder. It opens on the thesis, then a quick start, the day to day, what the method costs and how it works. The table of shipped gates moves to `docs/gates.md` and the map of the plugin to `docs/layout.md`. It says that contributions are not accepted yet. The time adoption takes is stated as measured, in the README and in the adopt skill, in place of the estimate of about 25 minutes: 35 and 42 minutes on empty repositories where the language's tools were quick to install and run, 81 minutes where adoption first installed them, and still unfinished after 87 minutes where they were slowest to set up.

The last stack knowledge leaves the plugin's code and pages for the binding (ADR 0050).

- The signs of `deployed` and `irreplaceable-data` are the binding's `## Life event signs` table; the pins gate reads the files that name an image from `## Image pins`; the coverage gate's exclusions are the Suppressions kinds that silence its question; the prose gate scans the comments of every tracked text file, with no extension list.
- The mutation gate reads one format, one JSON line per mutant (`--tool NAME --mutants FILE`). Its readers of two tools' reports are gone; the project's mutation recipe converts its tool's report. A recipe that still passes a report is a `conformance` finding.
- An upgrade owes each missing section (ADR 0037) and the agent fills them in one step, `/ratchet:fill`, which the route names right after the upgrade and which asks the owner nothing. The feature preflight and `drain.py open` refuse to start while such an item is owed, and name the step. Until it is done the route proposes no such event, the pins gate reads no image file and the coverage gate searches no exclusion, and each says so.

The architecture and the tools are asked at adoption, recorded and declared (ADR 0049). The adopting agent drafts two or three whole systems (what it is written in, where it runs, where its data is kept, how its parts talk, its parts, its trade-offs and what changing it later costs), with the tools that would check each, and the owner picks in the same round as the product questions, recommendation first; "you decide" takes the recommendation and the record says so. A repository with code is offered "keep what is here" first, and a rewrite never by default.

- The choice is a decision record with `## Decision` and its `- Chosen by:` line, at least two alternatives under `## Considered options`, `## Reversal cost` and `## Refutation criterion`. The binding's new `## Architecture` section names it and declares the parts, `| Part | Path | May depend on |`, which replaces the `## Layers` section of `AGENTS.md`.
- `conformance` is red on a missing record, fewer than two alternatives, no reversal cost or refuter, no `- Chosen by:`, no declared part, or a part that depends on one the table lacks. It reads structure only.
- An adopted repository gets the template's section from the upgrade, and the rest is owed (ADR 0037). While it is owed the route names the new `/ratchet:architecture`, which also revisits the choice later. When a safeguard is owed too, the route names the safeguard first and its `then:` names `/ratchet:architecture`.
- Init no longer assumes the stack, and `/ratchet:safeguard` takes the tool the record names for a concept. Everything else of ADR 0034 stands.

No shipped page, template, prompt, script or hook names a stack tool beyond a residue that says why, file by file (ADR 0042). The speed paragraphs of `tiers.md`, the docstrings, the skills' examples and the CI templates' cache keys now say the concept, and the cache key hashes `quality/snapshots/` instead of a package manifest. A test searches every shipped file for tool, language, framework and host names and is red on a new one, and on a residue entry that no longer matches.

The architecture is reviewed again when the project's shape changes (ADR 0052). The catalogue's new `[architecture]` table names the triggers that reopen it: the first stored format, listener, outbound call, user interface, scheduled work, access control or concurrency, and the life events `deployed`, `irreplaceable-data` and `depended-on`. When one fires an architecture review is owed beside any rows, and owed the same way: the arrival is free and noted, and a later change that adds another instance of the kind is red until a decision record at the merge base names the trigger on a `- Architecture review:` line, with `- Outcome: holds` or `changes`, `- Decided:`, a decision, a refutation criterion, and two or more options when it changes the architecture. A declared event owes a review and is never red.

- `/ratchet:architecture` does the review: it re-reads the architecture decision and the declared layers against the code, records that the decision holds (an assumption the hand-over states), or asks the owner one question with two or three options and their reversal cost, records the answer as a superseding decision and updates the layers so the gates hold. The route names it before `/ratchet:safeguard`; the drain holds a ticket red for an owed review until the review lands.
- Two kinds join the catalogue, `scheduled-work` and `access-control`, and every Triggers table needs a row for each kind that reopens the architecture, whatever the types. An existing repository owes the missing rows through the upgrade.
- The review is asked by the new Gates row `architecture reviewed when its shape changes`, bound to `scripts/triggers.py`; `upgrade.py --apply` adds it. What a repository held when that row was first bound counts as reviewed, so no adopted repository turns red for what it already had.
- A decision record settles the review only when it landed after the kind first arrived on the default branch. A record written ahead of the code settles nothing, so the instances that follow the arrival are still reviewed.

The spec decides where the change goes, and the review judges it against the architecture (ADR 0051). The spec's Design section ties the placement to the architecture record and the parts it declares, names each seam it adds or changes and why, and says what it rejected. The review gains a design axis: crossing a declared part's boundary is a blocker, a dependency the architecture rules out is major, and a shallow wrapper or a placement the spec does not explain is minor. A change to the architecture itself, such as a new kind of storage or a new process boundary, is a question for the owner, recommendation first.

A drain whose processes are all gone no longer blocks the next `clean --all`. The lease judged a drain alive by any process of its chain that was still running, including the service manager above every session; it now counts only a process below where the chains part, or a program running from a queue work tree, and the two-hour record of commands is gone.

A ticket that is too large is split by the agent, not handed back to the owner (ADR 0046). A part names its ticket (`Split from:`) and inherits the agent-ready label while that ticket carries it on the default branch or the tracker, one level only. A deterministic check holds the scope: each promise (`- [ ]` line) of a part appears in the parent verbatim and the parts carry every promise exactly once; a refusal names the line. `drain.py split --plan` files the parts in order, closes the parent as `split` with its text untouched, and keeps draining.

A part of a split no longer carries the size its parent was too large for. Copied into each part, the parent's `## Size` stopped every part at its preflight, and a part is never split again, so every part was parked as too large. A part now states no size, and the spec stage estimates it.

On a forge, a ticket waiting only on `behind` or `gates` is retried by the drain again, not held for the owner. The judge's pull request comment lists each reason as "- `code`: text", and the drain read the list marker as part of the code.

The first-hour eval reads what the plugin prints today. Its gate parser read only an older wording of the runner's lines, so a run's waiting and not applicable gates were missing from its numbers. Its reducer counted the guard's asks as the harness's own permission prompts. Its scripted owner took only the first command of a merge's `next:` line and skipped the deep check after it. `reduce` now also parses each pasted command's gate run again from the output saved in `logs/`, so a run recorded before the fix reduces to the right numbers, and each reader has a test that runs the real printer into it.

The first-hour eval builds its matrix table from runs already recorded. `run.py table <run dir>...` reads each run's `metrics.json` as it is and prints one row a run, and with `--dir` writes `matrix.md` and `matrix.json`; it launches and reduces nothing, and refuses a run it cannot name and two runs of one persona. The table gains a `Harness` column, the commit of the eval code that drove each run, read from the run's own record. Its "Min to first feature merge" column, and `minutes.to_first_feature_merge` behind it, now take the first merge of a branch named `feature/`, the name the feature step gives the branch it builds. They took the first merge that was not the adoption's or init's, so a spec's merge counted as the first feature; a run recorded before this shows the old value until its event log is reduced again.

The change-size ceiling counts product code, not tests (ADR 0045). Tests were 85% of the lines counted on recent changes, so the ceiling mostly limited them. `[ceilings.change]` is now `product_files = 25`, `product_lines = 1000` and a loose `test_lines = 3000` that only stops a runaway, and the early stops follow: product code has no margin for what is still to come, tests keep two thirds of their ceiling while the critic runs.

- Which paths are tests is the binding's `## Test paths` section, written at adoption by the project's agent: the plugin names no stack. Until it is filled every changed line counts as product, as before, and conformance names the missing section.
- An existing repository's `files` and `lines` become `product_files` and `product_lines` at the same values, by `upgrade.py --apply`; the lowered gate reads the move and refuses a higher value. `test_lines` is new, and the one loosening, which the upgrade says plainly.

The change that brings the first of a kind of code is free however many files it touches, and only once (ADR 0030, ADR 0048). The free arrival was bounded to one file, so a first feature that brought its server, its page and its store together was red. The change-size ceiling now bounds what one change brings. A kind the default branch ever held is returning, not arriving: removing every instance and adding them back is red while its rows are owed.

A `Relaxes:` record loosens a threshold only in the change that adds it (ADR 0047). It was honoured from every decision record in the tree, for good. Its values must now equal the merge base's and the head's, and a key removed and then written back looser is judged against the value it last held.

The drain's recovery reads a kill inside one file's write, and tells two drains under one process apart (ADR 0023). A fast-forward killed while git wrote a file puts that part-written file back, and keeps only what the owner had changed or left untracked before it began. `clean --all` prints the drain's token, and the drain's later commands stamp its lease, so a second drain started under the same long-lived process is refused while the first is active.

The plugin names no language, package manager or stack tool in its gates, and the binding holds each stack fact (ADR 0042, ADR 0043, ADR 0044).

- Three binding sections carry what the gates used to list: `## Install` (whether an install comes first, its lockfile and the recipes that need it), `## Dependencies` (manifest and lockfile globs, accepted registries, and a `just dependencies <dir>` entry point that prints records the plugin compares) and `## Suppressions` (the directive kinds the bound tools read). `conformance` is red when a needed row is missing or malformed, and when a manifest or lockfile the section declares is in no `guards/manifest.toml` group.
- The merge base's entry point and table judge a change, so a change cannot loosen its own judge. The upgrade writes a section only from what the tree states and owes the rest.
- Supersedes ADR 0039 and ADR 0011 in part.

The queue drain and the merge no longer lose work, collide or stop half-way (ADR 0023).

- A work tree is removed only once its uncommitted work is a stash. A stale `index.lock`, a merge left unfinished or a damaged index kept the stash from being made, and the tree was removed anyway; now it stays, nothing running from it is ended, and the note and the command say so.
- One drain runs on a repository at a time. `clean --all` is refused while a process of another drain is alive, every command that changes a work tree runs under a lock the kernel frees when its holder dies, and a built ticket still in its work tree counts against the cap.
- A run killed part-way finishes when it is run again. `update` abandons a rebase it left and starts over, `land` records a merge that landed and puts back what a killed `merge.py` left (its scratch work tree, a fast-forward cut off half-way), and `clean` records a merge no note said.
- A built branch that another landing left behind is rebased and judged again before it lands, so no verdict on a tree no review saw lands. A hold outranks built: a ticket parked after it had commits stays held. A ticket behind a held blocker is refused with the blocker and its reason.

The guards hold whatever the default branch is called (ADR 0041). In a repository with no remote and a default branch named neither `main` nor `master`, every path guard was off. When no remote or fixed name settles it, the default branch is now the one the main work tree has checked out, and every rule that compares with the default branch reads it.

A ticket that fits the change-size ceiling is no longer parked as too large before the critic exists (ADR 0040). The margins are now those of ADR 0045 above: none for product code, and two thirds of the test ceiling while the critic runs.

The route no longer loops a non-engineer on a parked ticket or a half-finished adoption. It reads the drain's note of why a ticket is held and names the step that releases that kind of hold (a size block names the split, never a bare `/ratchet:feature`), and a binding with no plugin pin names `/ratchet:adopt` to resume, ahead of the hooks install. A held ticket or a blocked run is released only by a change to the ticket, by the cause its run recorded landing (the guard edits, a dispute), or, for a safeguard hold, by binding the safeguard. An unrelated change on the default branch no longer offers the same build again.

- After `just merge` of the checked-out branch, the checkout is left on the default branch. For a branch the default branch already holds, the route names `git switch <default>`, not the same merge again.

An adopted project whose tools need an install merges through `just merge` with nothing installed by hand (ADR 0039, ADR 0043).

- The template `justfile` has an `install` recipe. Where the binding's `## Install` section says the stack needs one, the recipe installs exactly what the lockfile it names says, every recipe the section lists depends on it, and each tier runs it after `plugin-pin`, so a fresh checkout works as CI does.
- `conformance` is red when the justfile does not do what the section says, and those findings are never owed. `upgrade.py --apply` writes the section from the justfile's own `install` recipe where it can.
- Step 12 of `/ratchet:adopt` rehearses in a fresh worktree with no setup by hand, where the merge runs.

An upgrade is one command, and what it cannot settle is owed, not red (ADR 0029, ADR 0037).

- `upgrade.py --apply` moves the pin and makes every edit that needs no judgement. Each edit counts only when the check that raised it no longer does on the edited tree, and every conformance finding says who fixes it: `auto`, `agent` or `owner`. It refuses, and writes nothing, when a file it would write is ignored by git, leads out of the repository through a link, lies in a submodule or lands under `guards/`.
- What it leaves is written to `quality/owed.toml`, one entry per finding. `conformance` counts the owed findings, stays green and names the checks that do not run until they are fixed, and `/ratchet:next` relays the list.
- Between upgrades the list only shrinks. A new or changed entry is red unless the same change moves the pin to a newer version and the merge base was red on that finding. A change that only drops entries passes the merge judge without the owner.
- The upgrade asks the owner nothing. Each call that is the owner's keeps a default that loosens nothing, stated in plain words, and the upgrade never writes `- Life events: none`.
- An upgrade no longer reports nothing red for a mutation recipe from before ADR 0050 that hands the output of `mutation_survivors.py --changed` to its tool unfiltered. That list now holds every changed file, so the recipe failed on the first change with no code in it. `conformance` names such a recipe red, never owed, and reads a recipe that starts with a shebang as one script.
- The sections an upgrade adds are written with their tables padded, so a project whose format check pads tables is not red right after the upgrade.

Records land without the owner, and so does the adoption (ADR 0031). The merge judge asks no verdict of a change made only of records: a ticket, a spec, a change description, a glossary addition or an ADR. The owner still merges a ticket change that gives the agent-ready label, takes another's number or closes a ticket, any change to a labelled ticket or the spec it inherits from, and any ADR. `AGENTS.md`, `CLAUDE.md` and the other agent-instruction files stay the owner's at any depth. `just merge --records` lands the records a skill left on the default branch through a branch of their own, and the route never tells an agent to commit on the default branch. The adoption is judged by the manifest it brings, which must guard at least what the template does.

- A ticket edit that changes only its links is the agent's to merge, when each changed link names the same file and the preflight reads the ticket the same; otherwise it is the owner's (ADR 0057). The preflight's `Parent:` line reads a `.md` path before a bare number.

An agent changes a guard path on a branch without a prompt, and the owner decides at the merge (ADR 0033). The merge judge already sends every guard path change to the owner with its reasons. In the owner's replayed sessions this took the prompts from 173 to 39.

- Two kinds of file act the moment they are written, so the hook asks before each write of one, on every branch: a CI workflow, and an agent-instruction file at any depth.
- On the default branch a guard path change is denied, and the message says to make it on a branch. A locked group (settings, hooks, local git, the plugins) is denied on every branch. Every deny ends in its way out.
- `guards/**` has a `manifest` group of its own, and `.claude/rules/**` moves to `decisions`. `.claude/settings.json` holds the same fixed `deny` list in every repository.

Only product questions reach the owner (ADR 0034). Init and adoption ask only what decides what becomes of the user's things, who may see or do them, or what they can do at all, all in one message with a recommended answer each. Every other choice is listed under **Assumed**, with what changing it later costs. `/ratchet:safeguard` asks nothing.

- The `interactive` modes of `/ratchet:init` and `/ratchet:adopt` are gone: type a preference after the command. A preference never turns a check off, lowers a bar or declares a life event.
- `/ratchet:conjecture` shows what each thing the owner said became, asks each choice their words leave open, and corrects a spec that contradicts them.
- `skills/method/questions.toml` lists every question a skill may ask, and a test is red on any other. ADR 0049 later made the architecture and its tools an adoption question.

Before anything is built, a check reads the spec against the ticket (ADR 0036). A plan that leaves out something the ticket promised, or leaves a field's empty, too long or harmful input undecided, is repaired. What it still leaves out is built as the ticket says, and an input still open is built the safe way; the pull request lists both in plain words for the owner to overrule. The check asks the owner nothing. It adds one agent to every run, and two when it finds a gap.

A new acceptance harness, `scripts/acceptance.py`, for a tester to stand behind (ADR 0035). `up` boots the work tree as it is behind a proxy that logs every request and gives the tester one address. `check` holds the tester's record to that log, so a pass cites a request the harness saw. `replay` runs the record's kept checks again, and with `--control` against a product that does nothing, where each must fail. `summary` writes what the owner reads. It is not a gate: the tester role that uses it is still to come. `docs/acceptance.md` is the reference.

Adoption provisions what the first slice needs, and rehearses it (ADR 0032). `/ratchet:adopt` installs a test runner, and a property-testing library where the runner has none, and names them in the binding's new `## Tests` section. It writes every config its stack rules promise, fills `boot`, `ready` and `service` for every type that boots, and ships a live change-size ceiling. The new `just start` runs the product. `/ratchet:safeguard` no longer installs a test runner that adoption provisioned.

A gate's proof is harder to fool, and the summary says what ran (ADR 0020).

- Every run of a gate starts from the copy as restoring leaves it, and each bite is judged against a green run of its own. A gate red again with no fault after a bite leaves that bite `NOT PROVEN`. A fault is planted only in a file git sees in the snapshot, never through a link, in `.git` or in a submodule.
- A new fault kind, `replace = { path, old, new }`, changes the one place an exact text stands in a file. It keeps applying when the lines around it change, where a context patch broke on an unrelated edit beside it. Text found nowhere or twice is `THE FAULT WAS NOT PLANTED`, which is red.
- A gate whose whole output is one `later:` or `none:` line judged nothing, and says so. The last line counts what ran and what waits, as in `parallel: green (26 ran, 39 not run yet)`, where it read as every gate green.
- An interrupt stops `project_bite.py` within a fraction of a second, and the scratch copies are removed once every lane has stopped.

The guard no longer asks about a write that a `cd` did not cause, and reads more of the shell as the shell does.

- A `cd` inside a subshell, in a pipeline stage before the last or in a background job no longer moves the commands after it, on a line the guard models in full. On a line with a heredoc, a `case`, a quote left open, or a command that may move the shell where the line does not show it (`eval`, `source`, an alias, a function, a trap), every `cd` stays in force. A quote or substitution left open asks.
- The guard reads one operator at a time, a heredoc delimiter as a full word, and `${ cmd; }` as a command that runs first, so lines that once hid a write are judged. A command that runs another in a directory it names, such as `env -C` or `sudo -D`, is judged in that directory.
- The ask after a move the guard cannot follow names the path, says it is the owner's, and says what approving and denying each do.
- The agent-ready label is asked about only on a ticket file, so a spec or a change description that names it no longer asks.

The graceful-stop check is no longer red on a service that drains correctly.

- `with_service.py --graceful` holds a copy of each listener of the service from just before the stop signal. A connection still waiting in the accept queue, which the service never took, is named in `RATCHET_STOPPING` and not counted. Every other reset after the signal is a dropped request, and red, whenever its request started. This needs Linux 5.6 or later; on a port the gate cannot hold, the smoke judges as before and names the port and the reason.
- **Breaking:** `--signal-after` counts from when the command creates `RATCHET_SENDING`, not from its start. A `--graceful` command must create that path within `--timeout`, as `load_smoke.py --until-stopped` does, or the check is red.

Smaller fixes.

- `telemetry_sink.py` stops its command at once on an interrupt. The signal could reach its receiver thread, and the command then ran to its end.
- The settings check no longer fails when the owner denies a rule the plugin writes as an ask, and regeneration writes such a rule back once.
- With `--over report`, `budget.py` raises a run over its ceiling as a warning annotation, as well as printing it.
- `conformance` and the upgrade read each justfile once, so an upgrade plan is several times faster.
- `/ratchet:feature` starts the pipeline workflow by its name. On a fresh install the harness refused the script path the skill gave, because the session had not read that file; the path stays as the fallback for a harness that lists no workflow by name.
- The binding template's Install sentence writes its example in a double-backtick span, so a markdown formatter no longer rewrites the sentence and drops its spaces.

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
