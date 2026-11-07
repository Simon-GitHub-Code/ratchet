---
status: accepted
---
# This repository is built and verified under the Ratchet method

Code here is written mostly by agents, so quality is enforced by machines rather than trusted. We decided to adopt the Ratchet method (the `ratchet` plugin, version <X.Y.Z>, at <repository URL>) in full: the entry points and three tiers, the ratchet, harness-enforced guards with gate disputes, the records, the four-role feature pipeline, the verifiable code shape and the merge rule. `docs/process/binding.md` names the runner, the forge and the tool behind each gate; `AGENTS.md` holds what the method leaves to the project and names the three plugins the method composes with, `mattpocock-skills`, `pstack` and `impeccable`. A rule of the method is loosened here only by an ADR that supersedes this one for that rule.

## Refutation criterion

The method is wrong for this repository if, after slice one, a tier takes longer than its budget with no gate that can be moved, or if a dispute upheld by the owner has no gate change that can express it. Review at the end of slice one.

## Considered options

- A hand-written AGENTS.md with the same rules: rejected because the rules then drift per repository and no gate checks their presence.
- Adopting the tiers without the guards: rejected because an agent under pressure to go green can loosen a threshold and nothing shows who did.
