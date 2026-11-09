# shelfmark

Load the `ratchet:method` skill before any work. It is the contract this file assumes: tiered deterministic gates, records, disputes and the merge rule. This file holds only what the method leaves to the project.

## What this is

shelfmark keeps a user's bookmarks and exports them as JSON for the browser extension to import. The export format is consumed by the extension, so its shape and ordering are a contract.

## Read before any design work

- `docs/process/binding.md`: the runner and the tool behind each gate.

## Non-negotiables

- A red test stays red until the code is fixed; no retries, no skips.
