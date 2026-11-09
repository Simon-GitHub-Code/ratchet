# gatekeep

Load the `ratchet:method` skill before any work. It is the contract this file assumes: tiered deterministic gates, records, disputes and the merge rule. This file holds only what the method leaves to the project.

## What this is

gatekeep is the request-limiting library our public API calls before it serves a request. Each behaviour is specified in `docs/spec/`; the spec is the standard a change is reviewed against.

## Read before any design work

- `docs/spec/`: the behaviour each module owes its callers.
- `docs/process/binding.md`: the runner and the tool behind each gate.
