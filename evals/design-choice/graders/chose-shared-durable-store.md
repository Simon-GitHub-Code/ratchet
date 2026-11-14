---
type: llm
focus: {source: file, path: docs/decisions/0003-session-store.md}
---
Context for the judge: this is a decision record choosing where a web app keeps login sessions. The app runs as four server worker processes with no sticky routing and is restarted several times a day, so memory private to one worker is not shared between workers and is lost on every restart. The workable choice is a store that every worker reads and that survives a restart; the repository already has one: the app's existing embedded, file-backed database, which is what option B (a sessions table) uses. Grade the concept the record chooses, not the name of a tool. A record that says "the existing database" or "the app's embedded file database" instead of naming it earns the same as one that names it.

PASS when both hold:
1. The record chooses a store that is shared by all worker processes and durable across restarts, in the embedded, file-backed database the app already has (option B), and does not choose the per-process memory option.
2. The record says why it chose it: at least one reason that is a property of the store and this deployment (shared between workers, survives a restart, no new service to run), not only a preference.

FAIL when it chooses the per-process memory option (option A), chooses neither, defers the choice, proposes a new service the deployment forbids, chooses a shared store with no reason given, or the file is missing or empty.
