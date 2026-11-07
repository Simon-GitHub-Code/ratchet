---
status: accepted
---
# This repository is built and verified under the Ratchet method

Code here is written mostly by agents, so quality is enforced by machines rather than trusted. We decided to adopt the Ratchet method (the `ratchet` plugin, version <X.Y.Z>, at <repository URL>) in full: the command contract and three tiers, the ratchet, harness-enforced guards with gate disputes, the records, the four-role feature pipeline, the code-shape rules and the critical-rationalist stance. `docs/process/binding.md` names the tool behind each gate; `CLAUDE.md` holds what the method leaves to the project. A rule of the method is loosened here only by an ADR that supersedes this one for that rule.

## Refutation criterion

The method is wrong for this repository if, after slice one, a tier takes longer than its budget with no gate that can be moved, or if a dispute upheld by the owner has no gate change that can express it. Review at the end of slice one.

## Considered options

- A hand-written CLAUDE.md with the same rules: rejected because the rules then drift per repository and no gate checks their presence.
- Adopting the tiers without the guards: rejected because an agent under pressure to go green can loosen a threshold and nothing shows who did.
