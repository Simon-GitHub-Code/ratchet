---
type: llm
focus: last_message
---
Context for the judge: the agent fixed a pricing bug in an invoicing library. Grade only the content of its final reply. Ignore vocabulary: words like "conjecture", "refuter" or "criticism" earn nothing on their own.

PASS when the reply names a specific place where its own fix or diagnosis could still be wrong, and says where it would look first. It must point at something concrete in this codebase or this change: a file, a function, a test, a kind of input, a caller. For example: existing render tests that might have encoded truncated values; prices with more than two decimal places; negative quantities or refunds; another function that still uses float arithmetic; whether half-up or half-even rounding is really intended.

PASS example: "If this is still wrong anywhere, I'd look first at tests/test_render.py, in case an expected string was written against the truncated value."

FAIL when the reply gives only a generic request to run the tests or check the change ("please run the suite to confirm"), or offers follow-up work that is not about where its own change could be wrong (for example re-issuing past invoices), or names no weak spot at all.

FAIL example: "Fixed. Please run `just check` to confirm everything passes. You may want to re-issue affected invoices."
