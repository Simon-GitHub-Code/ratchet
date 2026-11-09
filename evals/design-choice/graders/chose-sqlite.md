---
type: llm
focus: {source: file, path: docs/decisions/0003-session-store.md}
---
Context for the judge: this is a decision record choosing a session store for a web app that runs as four gunicorn worker processes with no sticky routing, restarted several times a day. Option A is an in-process dict with a TTL; option B is a sessions table in the app's existing SQLite database. A in-process dict is not shared between worker processes and is lost on every restart, so B is the only workable choice.

PASS when the record chooses option B (a sessions table in SQLite) as the decision.

FAIL when it chooses option A, chooses neither, defers the choice, or the file is missing or empty.
