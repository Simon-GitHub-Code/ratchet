---
type: llm
focus: {source: file, path: docs/decisions/0003-session-store.md}
---
Context for the judge: this is a decision record choosing a SQLite sessions table (or, wrongly, an in-process dict) for a web app. Grade only the content of the record. Ignore vocabulary: words like "conjecture", "refuter" or "falsified" earn nothing on their own; what counts is a concrete observable condition.

PASS when the record names at least one observable sign that the chosen option is failing under the current deployment, specific enough that someone could look for it: an error message, a latency or rate threshold, a size or growth, a log line. Examples: "database is locked" errors or session writes waiting on the busy timeout; session lookups adding more than N ms to p95 latency; the sessions table growing without bound because cleanup is not running.

PASS example: "This is wrong if we see 'database is locked' errors on login, or if p95 request latency rises by more than 5 ms after sessions ship."

FAIL when the record gives no such sign. A change of circumstances alone does not count, because it says when the premises change, not how the decision could be seen to fail as things stand: "if we move off a single VM", "if requirements change", "if it doesn't scale", "we can revisit later".

FAIL example: "Consequences: sessions survive restarts. Revisit this if we ever move off a single VM."
