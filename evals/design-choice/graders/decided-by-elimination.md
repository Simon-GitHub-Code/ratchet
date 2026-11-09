---
type: llm
focus: {source: file, path: docs/decisions/0003-session-store.md}
---
Context for the judge: this is a decision record choosing a session store. Facts available in the repository (docs/ops.md): production runs `gunicorn -w 4` with no sticky routing, deploys restart the workers several times a day, the database is SQLite in WAL mode at about 20 requests a second with one write in ten, and no new infrastructure services are allowed. A colleague preferred option A (in-process dict) as simplest and fastest. Grade only the content of the record. Ignore vocabulary: words like "conjecture", "refuter", "criticism" or "elimination" earn nothing on their own.

PASS when the record decides by elimination, which needs both:
1. A concrete criticism that kills the rejected option, tied to a fact of this deployment: four worker processes do not share memory so a session made on one worker is unknown to the others, and/or every deploy restart drops all sessions. Generic remarks ("doesn't scale", "not persistent") without the mechanism do not count.
2. The strongest criticism of the chosen option stated and answered: for example write contention or "database is locked" under four writers, the cost of a database read on every request, or expired rows piling up, each with why it does not kill the option here (WAL, the measured load, a cleanup job, an index).

PASS example: "A fails: nginx spreads requests over four gunicorn workers with no stickiness, so a login stored in worker 1's dict is unknown to worker 2, and every deploy wipes all four. B's weakest point is writes: each login and expiry is a write to the same file. At 2 writes a second in WAL mode with a 5 s busy timeout that is far from contention."

FAIL when any of these holds:
- The rejected option is dismissed without a mechanism tied to this deployment.
- The chosen option's weaknesses are not mentioned, or are listed without any answer.
- The choice rests on preference, convention or confidence ("B is the standard approach", "B feels more robust").

FAIL example: "We chose SQLite because it is persistent and more robust than an in-memory dict, and we already use it."
