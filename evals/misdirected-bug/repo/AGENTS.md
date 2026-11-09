# tilltab

Load the `ratchet:method` skill before any work. It is the contract this file assumes: tiered deterministic gates, records, disputes and the merge rule. This file holds only what the method leaves to the project.

## What this is

tilltab prices and renders the invoices our shop sends to customers. Amounts are held as integer pence; only `tilltab/money.py` turns pence into text.

## Read before any design work

- `docs/process/binding.md`: the runner and the tool behind each gate.

## Non-negotiables

- Money is integer pence everywhere except at the text boundary.
