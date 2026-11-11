---
status: accepted
---
# Path guards come from the manifest committed on the default branch

The hook read `guards/manifest.toml` from the work tree, so the path rules switched on the moment `/ratchet:adopt` wrote the file, and every later adopt step that filled a group or wrote a file the manifest named met a permission prompt: an adoption could not run without the owner in the loop, and the "ask" prompts guarded nothing, since the agent asking was the one writing the rules. We decided that the path rules are on only once the manifest is committed on the default branch (the remote's `HEAD`, else `main` or `master`), that the hook reads that commit from git (`ratchet_guards.committed`) and from nothing else, and that it judges every path against the committed manifest whatever the work tree holds. A manifest that is untracked, or committed only on a branch, guards nothing yet; the command rules apply from the first session regardless. A group whose `paths` list is still empty parses and matches nothing, so the template's unbound groups do not fail every tool call closed while adopt fills them; the conformance gate reports any group still empty. Decided 2026-11-07.

Reading the default branch's ref is only as sound as the ref: ADR 0016 has the hook refuse any command that moves it and names the exact refs it is read through.

## Refutation criterion

The decision is wrong if an agent, on a branch, gets a manifest change merged that drops a guard path without the owner noticing, in more than one of the first twenty adopted repositories (the pull-request's Guard paths section and the `guard-diff` job exist to show that change), or if adoption runs still meet a guard prompt before the adopt commit is merged. Check after the first five repositories adopt the method with this version.

## Considered options

- A flag or environment variable that disables the path rules during adoption: rejected, anything an agent can set is something an agent can forge; the guard must have no off switch.
- Read the manifest from `HEAD` rather than from the default branch: rejected, an agent on its own branch commits whatever it likes to `HEAD`, so `HEAD` is as much the agent's as the work tree.
- Keep reading the work tree and have adopt ask the owner to approve each prompt: rejected, the prompts are the cost this decision removes, and they protect nothing while the agent is the author of the rules.
- Make the parser accept empty groups only during adoption: rejected, a group with no patterns is well defined (it matches nothing), and the conformance gate, not the parser, is where "still unbound" belongs.
