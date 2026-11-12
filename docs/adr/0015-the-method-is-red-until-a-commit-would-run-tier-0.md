---
status: accepted
---
# The method is red until a commit would run tier 0, proved by running the hooks

Tier 0 runs on every commit through two hooks: `pre-commit` runs `just pre-commit` on the tree, and `commit-msg` runs `just commit-msg` on the message. In two adopted trial projects on the owner's machine no hook was ever installed. The machine's global git config sets `core.hooksPath`, `pre-commit install` refuses while it is set, and the adoption's hand-over left the owner a command they did not run. Tier 0 then ran on no commit of either project, the feature preflight was green, and so was the route. Nothing said so. The guard refuses hook installs and hook-path changes from an agent, correctly, so the repair is the owner's one command, and the method has to stay red until they run it.

We decided that a shipped, deterministic gate answers "would a commit in this checkout run tier 0?" and that three places ask it: the feature preflight (a `hooks` failure whose `produce` is the owner's command), the route (the next step becomes that command in an adopted repository) and the template `justfile`'s `ratchet` recipe. CI and `conformance` do not, since a CI checkout has no hooks and runs tier 0 as a job.

The gate proves the answer. `scripts/hooks_installed.py` runs the hooks git would run for a commit (`git hook run pre-commit`, and `commit-msg` with a message file) with `RATCHET_HOOK_PROBE` naming a scratch file. The template's `pre-commit` and `commit-msg` recipes write their own name to that file and exit before any gate runs; the gate is green only when both names are there. The answer goes through a file because the `pre-commit` framework hides the output of a hook that passes. Every `core.hooksPath` in every scope, and every global hook that chains to the repository's own, is exercised exactly as a commit would exercise it, and no hook script is read. Tier 0 itself never runs. The hooks run one after the other, as git runs them, and a hook that exits non-zero without answering is reported with its exit code and output, not as missing: it ran and failed, and a commit would be refused. `tier0` calls a new `tier0-tree` recipe, so only the two hook entries answer the probe and a stray variable never skips a gate in `check`.

A red names the command: `git config --unset core.hooksPath` for a local path; `GIT_CONFIG_GLOBAL=/dev/null pre-commit install` (or `GIT_CONFIG_SYSTEM`, or `uvx pre-commit install` where `pre-commit` is not on the path) where a global path makes the install refuse; `git config core.hooksPath <the repository's hooks>` where the repository's hooks work and a global hook does not chain to them. A justfile whose recipes cannot answer the probe is red before any hook runs, so a repository adopted earlier is never made to run tier 0 by the probe; the justfile is repaired from the template. `conformance` checks that the recipes answer.

Each forgery meets a guard that already exists, and no mechanism was added for it. Writing a hook script into `.git/hooks` is an edit of the `local-git` group, which is `deny`. Changing the recipes is an edit of `justfile`, a `gate-commands` guard path that `guard-diff` lists. The one hole the probe opens, setting `RATCHET_HOOK_PROBE` on a real commit so that the recipes skip the gates, is closed in the rule that already refuses `SKIP=`.

Decided 2026-11-11.

## Refutation criterion

The decision is wrong if a commit runs without tier 0 while `hooks_installed.py` is green. Seed it: a hook that answers the probe yet skips the gates on a real commit (a chain that runs the repository's hook only for some arguments), and a `commit-msg` hook that answers for the one-line message the probe passes and rejects real ones. If the gate stays green on either, the probe must pass the arguments git passes on a commit. It is also wrong if the probe costs more than about a second on a repository whose hooks are installed, or if it runs a gate for real on a justfile the static check let through. Measure both on the next two adopted repositories.

## Considered options

- Read the hook scripts and the git config and infer the chain: rejected. A global hook may chain through `exec`, a function, an include or a wrapper, and every spelling is a parser to maintain; the probe asks git and the hook what happens, and is right for spellings nobody wrote a case for.
- Print the answer on standard output and read it from the hook run: rejected, `pre-commit` shows the output of a passing hook only with `verbose: true`, which would print every gate's output on every commit.
- Ask `git config core.hooksPath` and require it empty: rejected, a machine may set it globally on purpose (a secret scanner runs from there, for one), and the right answer there is to keep that hook and make sure it chains.
- Ask the gate in CI and in `conformance` too: rejected, a CI checkout has no hooks, so it would be red on every run.
- Run the real `just pre-commit` as the probe: rejected, it is the full tier 0, about a minute, and a session start and a route would pay it.
- Install the hooks from the gate or the hand-over: rejected, the guard refuses hook installs and hook-path changes from an agent, and that refusal is right: a hook is code run on every commit.
