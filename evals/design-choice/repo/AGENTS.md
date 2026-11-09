# pinboard

Load the `ratchet:method` skill before any work. It is the contract this file assumes: tiered deterministic gates, records, disputes and the merge rule. This file holds only what the method leaves to the project.

## What this is

pinboard is a small team noticeboard: people post notes and pin them. It is a plain WSGI app over one SQLite database.

## Read before any design work

- `docs/ops.md`: how production runs.
- `docs/decisions/`: decisions with lasting consequences, one file each.
- `docs/process/binding.md`: the runner and the tool behind each gate.

## Non-negotiables

- No new infrastructure services. Production has the app servers and their disk, nothing else.
