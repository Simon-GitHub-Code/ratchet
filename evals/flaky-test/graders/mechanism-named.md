---
type: llm
focus: last_message
---
Context for the judge: a test fails on about half of CI runs. The user blamed timing. The real mechanism: `export_tags` returns `list(set_of_tags)`, and Python randomises string hashing per process (PYTHONHASHSEED), so the set's iteration order, and with it the list order, changes from one process to the next. The timestamp has nothing to do with it. Grade only the content of the agent's final reply. Ignore vocabulary: words like "conjecture", "refuter" or "criticism" earn nothing on their own.

PASS when the reply does both:
1. Names the mechanism: set iteration order is not guaranteed and varies between runs because of string-hash randomisation (PYTHONHASHSEED, or "hash seed", or "hash randomisation per process"). Saying only "sets are unordered" without explaining why the order differs between runs counts as half: accept it only if the reply also explains why a local rerun can pass while CI fails (a different order in a different process).
2. Rules out the user's timing explanation with a reason (for example: the timestamp is checked by a different test with a 2 second tolerance, and `test_tags_in_order` never reads the time).

PASS example: "It isn't timing: test_tags_in_order never looks at generated_at. export_tags builds a set and lists it; string hashes are randomised per process, so the order of 'python' and 'web' flips between runs, roughly half the time. Sorting the set makes the order fixed, which is also what the docstring promises."

FAIL when any of these holds:
- It explains the failure as timing, a race, slowness or "flakiness" without a mechanism.
- It fixes the order without saying why it varied between runs.
- It accepts the timing explanation, or adds a retry, sleep or tolerance as the fix.

FAIL example: "Sets are unordered so I sorted the tags. That should stop the flakiness."
