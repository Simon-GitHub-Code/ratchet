---
status: accepted
---
# An agent installs the repository's own commit hooks, and only the owner changes git config

ADR 0015 made the method red until a commit would run tier 0 and printed the owner's one command while it was not. A walk of the whole route on a fresh Rust service, as the owner's stand-in, ended at that command. The machine sets a global `core.hooksPath` whose hooks run the repository's own, as many do, so `pre-commit install` refused and the gate printed `GIT_CONFIG_GLOBAL=/dev/null pre-commit install`. The guard refuses that from an agent. Until the owner typed it the route, the feature preflight, the drain and `just ratchet` all stopped, and the coverage thresholds stayed at 0 though 100 was measured. The same walk saw the probe answer "not known" at a load average of 60, because the global hook scans the staged tree and took over 20 seconds, and saw `drain.py plan` print a ticket as `buildable` with no reason while the preflight of the same ticket was red on the hooks.

Reading the guard showed what it refuses, and it is not the install. It denies a `GIT_CONFIG_*` variable, a `git config core.hooksPath`, an uninstall, and a write of `.git/hooks/**` by Edit, Write or a shell command. A plain `pre-commit install` passes, and so does `lefthook install`. So on a machine with no hooks path an agent already installed the hooks during adoption, and the owner's step existed only where a global path made the install refuse. It was a confirmation: the command is fixed, and the owner decides nothing by typing it.

We decided that the method's own script runs that install. `scripts/hooks_install.py`, behind the new `hooks-install` entry point, runs the `pre-commit` framework's install in the checkout and then the probe of ADR 0015, which stays the only proof. Where the global or system git config sets a hooks path it hides those scopes from that one command, through its environment. The gate names the entry point as the repair where the repository's own hooks are missing, without the word `owner`, and the preflight, the route, the drain and the `feature` and `next` skills treat such a repair as the agent's.

The script is narrow by construction:

- It writes no hook. The framework writes its own, and what they run comes from `.pre-commit-config.yaml`, a guard path. It passes no flag that overwrites.
- It changes no git config. A local `core.hooksPath`, and a global hook that does not run the repository's own, are printed as the owner's command, as before, and the script installs nothing for the first.
- It writes only under the repository's git directory. A hooks directory outside the repository is never opened for writing. Its test compares the global hooks and `git config --list --show-origin` before and after.
- It moves no hook somebody else wrote. The framework's install would rename such a hook and run it first; where one is there the script stops and prints the owner's command.
- It does nothing when the binding names another mechanism. The gate prints the binding's install command, and the guard judges it like any command: `lefthook install` passes, a `git config` does not.

The probe now waits two minutes for each hook, not 20 seconds. A hook that has not finished is still reported as unknown, and the message says a busy machine is the usual cause and to run it again. A `buildable` ticket in `drain.py plan` carries the checkout's failure in its `why` while the checkout is not fit, so one row tells what the preflight of that ticket would.

This narrows ADR 0015. Its gate, its probe and its three askers stand. Its sentence that the repair is the owner's one command now holds for a change of git config only, and its rejected option "install the hooks from the gate or the hand-over" stays rejected for the gate: the gate still changes nothing.

Decided 2026-11-12.

## What an agent gains

Nothing it did not have. The install is the one it could already run wherever no hooks path is set. The hidden scopes are fixed in the plugin's own file, which the guard locks, so an agent chooses neither the variable nor its value, and the guard still denies `GIT_CONFIG_*` on a command line. The script removes no hook and replaces none but the framework's own, so it cannot be used to weaken what a commit runs. `pre-commit uninstall`, `--no-verify`, `SKIP=` and `RATCHET_HOOK_PROBE=` are denied as before.

Known residue, caught by no rule. The script runs the `pre-commit` it finds on the path, so a stand-in placed there writes whatever hooks it likes, and the probe shows only that they answer. That is the forged hook ADR 0015 names and the script run by path ADR 0024 names: a write of `.git/hooks` no command line shows. A hook installed through `uvx` holds the path of an interpreter `uvx` may later remove; the framework's hook then looks for `pre-commit` on the path and fails the commit if there is none, which the probe reports as a hook that failed. The same walk saw a shell write give a local ticket the agent-ready status with no ask, and three tickets cost three asks. The hook now reads a command line or a patch that names the label and writes a ticket as giving it, and asks once for the call, naming each ticket. What stays out of reach there is ADR 0024's known residue: a script run by path, a file copied into place, a label a variable supplies.

## Refutation criterion

This is wrong if `hooks-install` prints `hooks: green` and a commit in that checkout skips tier 0; seed it with the two cases of ADR 0015. It is wrong if a run changes a git config value, writes outside the repository's git directory, or moves or replaces a hook the framework did not write: the tests assert each, and a counter-example is a bug in the script, not a reason to widen it. It is wrong if the route still stops at the hooks stage on a machine whose global hooks run the repository's own: measure on the next two adoptions. And the two-minute limit is wrong if the probe answers "not known" on a busy machine again; the next step is then one retry before answering.

## Considered options

- Write the hooks as plain scripts that run the `pre-commit` and `commit-msg` recipes, marked as Ratchet's: rejected. The binding names the framework, whose configuration may hold hooks of the owner's beside tier 0; a plain hook runs none of them while every gate reads green. The framework stashes unstaged changes so that tier 0 judges what is committed; a plain hook judges the work tree. And a later `pre-commit install` by the owner would keep the plain hook as a legacy hook and run tier 0 twice.
- Accept a global hook that already dispatches to `.pre-commit-config.yaml` without the repository's hooks: rejected, the probe already accepts any chain that answers, and inferring one from a hook's text is what ADR 0015 rejected.
- Let the guard pass `GIT_CONFIG_GLOBAL=/dev/null` from an agent: rejected. A variable an agent sets is general, and the next value is a file of its choosing; one fixed command in a locked file is not.
- Have the gate install: rejected, the route runs the gate at every session start, and a gate that writes is no gate.
- Run the binding's install command for another mechanism from the script: rejected, that command may be a `git config`, and the guard is the judge of it.
- Ask the owner inside adoption's first dialog: rejected as the general answer, since it is still a confirmation on every clone. It remains for the change of git config, which adopt now gives the owner when it is found and not at the hand-over.
