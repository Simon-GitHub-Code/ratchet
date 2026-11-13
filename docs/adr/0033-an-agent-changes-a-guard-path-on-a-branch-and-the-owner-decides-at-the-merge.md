---
status: accepted
---
# An agent changes a guard path on a branch, and the owner decides at the merge

Every change an agent made to a guard path stopped at a permission prompt. In the owner's transcripts of engram and local-automation-co, 174 tool calls raised one. Replayed through the hook and the generated permission rules, 173 still do; the last edited a scratch copy the replay cannot rebuild. Of the 173, 153 change a guard path, almost all of them on a feature branch, 14 apply the agent-ready label and 6 merge a pull request. The owner, on 2026-11-13: "I just say yes to everything so its really performing no function other than to annoy me", and "I don't refuse the file edit prompts I always accept them." A prompt the owner always accepts controls nothing.

It also asked at the wrong moment. A guard path changed on a branch changes nothing on the default branch until the branch merges, and the merge judge (ADR 0021) already sends every change to a guard path to the owner, with its reasons in writing, as one decision over the whole change. The prompt at the edit asked the owner the same question earlier, once per file, with less to go on.

## Decision

The hook judges a change to a guard path by where its commit lands.

- **On a branch, no verdict.** An Edit, Write, MultiEdit, NotebookEdit, patch or shell write of a guard path in a group that is not locked gets no answer from the hook when its commit lands on a branch other than the default one. The hook reads the branch from git: the branch of the checkout that holds the file, or the one a `git switch` or `git checkout` earlier on the same command line moves that checkout to. A detached `HEAD` counts as a branch, since nothing reaches the default branch from it but a push or a merge, which the hook judges. The harness's own permission flow decides, as for any other file.
- **Where the hook cannot tell, the default branch.** A switch it cannot read as one may move any checkout to its default branch: a branch named by a variable or a substitution, or a `git switch` or `git checkout` inside a shell's script, a substitution, `eval`, `xargs`, `find -exec` or a script git runs. A shell write of a guard path after one on the same line is judged as on the default branch. So is a relative path written after a `cd` the hook cannot follow, on any branch, since the shell may then be in any checkout.
- **On the default branch, a deny with the way out.** Where the commit would land on `main`, `master` or the branch the committed manifest is read from, or the hook cannot tell where it lands, the change is denied, and the message says: make the change on a branch and commit it there, where the merge judge reads it. An addition to a record still passes there (ADR 0014). No judge reads a commit made on the default branch, so that is where the hook must hold.
- **A locked group is denied everywhere.** A locked group holds files that act before any merge, in any session started in the checkout that holds them. Claude Code reads the settings, hooks, workflows, agents, skills and commands under `.claude/` from that checkout and reloads them while a session runs; an agent file can set `permissionMode: bypassPermissions`, and a skill can grant tools and register hooks. Git runs `.git/hooks` and reads `.git/config` and the refs the guard trusts (ADR 0016). The plugins and the user's settings act in every session. Removing a directory is judged by the strictest group below it, so `rm -rf .` is a write of a locked path.
- **The template locks the forge's workflows.** GitHub runs a branch's own `.github/workflows/` files on a push or a pull request, with the repository's token and secrets, before any merge. The template moves them out of `ci` into a locked group of their own, `workflows`. The settings layer does not deny them: adoption fills their plugin refs after `just settings` has switched the rules on, and the hook, which reads the manifest the default branch holds, refuses them from the adoption's merge on. Every plugin upgrade that moves those refs becomes an edit the owner makes by hand: 8 of the 153 replayed changes wrote a workflow.
- **The manifest is no longer locked.** The hook and the merge judge read it as the default branch holds it (ADR 0005), so a branch that edits it changes nothing until the owner merges it, and the judge sends that merge to the owner. The template moves `guards/**` from the harness group into a group of its own, `manifest`, and `.claude/rules/**` into `decisions` beside `CLAUDE.md`: a rule is an instruction, which runs nothing. The conformance gate now checks that `guards/**` is in some group, where it checked that it was locked.
- **What still asks.** A merge an agent types (`gh pr merge`, or a `git merge`, `git cherry-pick`, `git am`, `git read-tree` or `git pull` of another branch into the default one), the agent-ready label, and a hook that cannot run. `git am` and `git read-tree` are new to the list: with a guard path free to change on a branch, either would carry that change onto the default branch past the judge. Each is the owner's to give, and no later step judges it. The merge prompt says what the call does, where the reasons are, and to approve only a change the owner wants in now: the judge merges a pull request itself when nothing in it needs the owner, so one still open waits for a reason, which the judge's comment on it gives.
- **The permission rules are fixed.** The `deny` list in `.claude/settings.json` no longer mirrors the manifest. It is the same in every repository: an `Edit` deny on each path that acts before a merge, and the command denies, now with `git config core.hooksPath` and `git -c core.hooksPath`. The `ask` list is empty, `gh pr merge` included: a settings `ask` prompts whatever the hook answers, cannot tell a branch from the default one, and shows the owner a rule where the hook shows reasons. This supersedes ADR 0006 in part.
- **Every deny names the way out.** For a locked path or a bypass, a dispute; for the default branch, a branch; for a relative path written after a `cd` the hook cannot follow, an absolute one. That last case is now denied rather than asked, on a branch too, since an agent can always name the path.

## Measurement

The owner's calls were replayed from the transcripts: 230 calls, the 174 prompts, 27 denies and 29 calls whose hook timed out in the session. Each ran through the hook as Claude Code runs it, and through a simulation of the settings layer, in scratch copies of both repositories built from their default branches. The copies were upgraded as below for the new hook.

| Calls | Before | After, on a branch | After, on the default branch |
| --- | --- | --- | --- |
| Prompts | 173 | 20: 14 labels, 6 merges | 20: the same |
| Guard path changes among them | 153 asked | 153 pass | 153 denied, each naming a branch as the way out |

Hook latency was timed with the two hooks interleaved call by call, so both met the same load (a shared machine at a load average near 67 on 16 cores): the median was 841 ms before and 846 ms after, the 99th percentile 20.6 s before and 3.4 s after, and 22 calls took more than the 10 s Claude Code gives the hook before, none after. The worst cases before were calls the hook never finished: a program whose text held `/** ...`, a JavaScript doc comment, which the hook expanded as a recursive glob from the filesystem root. Claude Code runs a call whose hook times out, so those calls ran unjudged. The hook now expands `**` as a shell without `globstar` does, as `*`. Judging by branch costs two git calls per checkout, not per file: the hook reads each checkout's branch and default branches once per call, so a glob over 2,000 files in one checkout asks git twice.

Each tamper attempt below was typed directly at the hook, on a branch and on the default branch, before and after: `Edit` or `Write` of `.claude/settings.json`, `settings.local.json`, `.claude/hooks/`, `agents/` and `skills/`, `~/.claude/settings.json`, `~/.claude/plugins/`, the plugin's own files, `.git/hooks` and `.git/config`; `sed -i`, `echo >`, `rm`, `mv`, `ln` and an inline `python3 -c` on the same paths; `--no-verify`, `-n`, `SKIP=`, `core.hooksPath` by `git config` and `git -c`, `pre-commit uninstall`; `update-ref`, `branch -f`, `replace` and a push to `main`; a forced push; a nested `claude --settings`, `claude plugin disable`, and a branch protection change. Every one is denied in all four runs. The settings layer now also denies `~/.claude/plugins/**` and both `core.hooksPath` spellings.

## Known limits

- A change made on a branch and carried to the default branch uncommitted (`git switch main` with a dirty work tree, or a stash applied there) is committed there unjudged: the hook judges the write, not the commit. So is one carried by git plumbing the hook does not read as a merge, such as `git --git-dir=<main checkout>/.git --work-tree=. commit -a`, which commits a branch's work tree onto the default branch. Denying `git commit` on the default branch closes both.
- A forge other than GitHub that runs a branch's own CI file (`.gitlab-ci.yml`, for one) is not locked by the template; its CI paths stay in `ci`.
- A script run by path still writes what it likes. On a branch the merge judge sees the guard path it changed; on the default branch nothing does, as before.
- Claude Code runs a call whose hook times out. Hooks can be registered with `"onFailure": "block"` from Claude Code 2.1.295; the plugin does not yet, since the other harnesses that read `hooks.json` were not checked against the key.
- The merge prompt names where the reasons are, not the reasons.
- An owner-invoked skill that changes a guard path on the default branch, such as `/ratchet:init` run again, is now denied there, and runs on a branch.

## Upgrading an adopted repository

1. The owner moves `guards/**` in `guards/manifest.toml` out of the `harness` group into a group of its own with no `edit` key, adds a locked `workflows` group with `.github/workflows/**` before `ci`, and may move `.claude/rules/**` into `decisions`. The manifest stays locked until this merges, so the owner makes the edit.
2. The owner runs `just settings` and commits `.claude/settings.json`. Until then `settings-check` is red and names each `ask` rule and each missing deny.

Without the first step the manifest stays locked, and the hook denies an agent's edit of it as before. Without the second, the old `ask` rules keep prompting for the paths they name.

## Refutation criterion

This is wrong if it lets a guard path change past the owner, or if the prompts come back. Re-run the replay on the owner's next two weeks of transcripts across adopted repositories. It is refuted if a commit on a default branch changes a guard path and reached it by neither a merge the owner made nor one the judge passed; if the owner meets more than five prompts on a working day; or if an agent meets the same default-branch denial three times in a session without making a branch, which would mean the way out does not work.

## Considered options

- **Keep the prompts and ask the owner to read them.** Rejected: the owner said they accept every one, and a control that depends on reading 150 prompts is not one.
- **Answer `allow` on a branch.** Rejected: `allow` skips the harness's own permission flow, and in Antigravity its prompt. The guard never answers `allow`.
- **Ask only on the default branch.** Rejected: an ask there is the same rubber stamp, and no judge reads that commit afterwards. A deny with the way out costs the agent one `git switch -c`.
- **Unlock the agents, skills, commands and workflows under `.claude/` too.** Rejected on the evidence above: they act in a session started in the branch's checkout before any merge.
- **Keep settings `ask` rules for the paths that are not locked.** Rejected: a settings `ask` prompts even when the hook is silent, and cannot read the branch.
