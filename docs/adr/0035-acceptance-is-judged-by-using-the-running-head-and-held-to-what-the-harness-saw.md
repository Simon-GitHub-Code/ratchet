---
status: accepted
---
# Acceptance is judged by using the running head, and held to what the harness saw

The method promises an owner who does not read code that what merges does what the ticket says. Until now nothing used the product before a merge. The gates read the code and run the tests the building agent wrote. In a trial run, the pipeline built a small read-later app and reported that it passed every check. The app had no way to start, and once started by hand it saved blank links and `javascript:` links. An owner finds both in the first minute of use. A role that uses the product the way its owner would can find them too. Its report is worth something only if nobody has to read the role's work to trust it.

## Decision

**The tester gets the ticket and one address.** `scripts/acceptance.py up` boots the work tree at `HEAD` through `with_service.py`, as the project's justfile says (`boot`, `ready`, `boot_timeout`, and `flags_on`, so every flag due for removal is on). A logging proxy, `acceptance_proxy.py`, stands in front of the product, on a port of its own. `up` prints that proxy's address and the directory the tester writes in, then returns while the product runs. `down` stops everything the session started and waits until nothing it started still answers. A second `up` or `down` changes nothing. The harness tries a `service` and a `web-ui`, and refuses every other application type before it starts anything.

**A verdict rests on requests the harness logged.** The proxy gives each request an id, and logs its method, path, status and the digests of the request and answer, which it keeps as sent. When it stops it seals the log: it records how many lines it wrote and their SHA-256. `check` holds the tester's record to the ticket, the log, the seal, the stored answers and the screenshots on disk, and prints one sentence for each rule the record breaks. Every checklist line gets exactly one verdict from `pass`, `fail`, `unsure` and `not-observable`. A `pass` cites a request this session logged, with the method, path and status the log holds. Every text it says the answer showed is in the stored answer. On a `web-ui` it also cites a screenshot. A `fail` and every finding carry the steps that reproduce them. A blocker or major finding cites what it saw. No text names a file of the product's code. A log that does not match its seal is refused, and so is a record for another session or another commit.

**What is kept is data.** A `pass` leaves a kept check, or says why it cannot be kept. A kept check is a list of HTTP steps or browser steps on paths, never hosts. `{BASE_URL}` and `{unique}` are filled in on each run, so a check carries nothing of the machine it was written on. `replay` runs each kept check against a fresh boot. `replay --control` runs each one against a product that does nothing, and a check that holds there proves nothing and is red. Browser steps drive any Chromium over the DevTools pipe (`cdp.py`), and a page under test can resolve no host but the loopback one.

**The harness writes the record when the product cannot start.** When `boot` or `ready` is `"none"` or still the template's text, or the boot exits or never answers, `up` writes a blocker in plain words. The blocker says what was run and what it printed. Every line of the ticket is then `unsure`, and nothing can pass.

**Evidence stays out of the tree.** Sessions live under `<git common dir>/ratchet/acceptance/<slug>/<head>/<session>/`, and a session that ended more than 14 days ago is removed. `summary` writes `docs/features/<slug>/acceptance.json` and `acceptance-checks.json` from an accepted record. It writes the same bytes on every run, and the orchestrator commits them. `audit` reads the tester's transcript and names every tool call that named a file of the product, or the harness's log beside the tester's directory.

Decided 2026-11-13.

## Refutation criterion

This is wrong if a record `check` accepted is shown, by the owner using the product, to pass a checklist line the product does not keep in the way the line states. Look at the first ten pipeline runs that have a tester. One such record means a verdict can rest on something the log does not show, and the rules above are too weak. It is also wrong if one of those records cites a request the tester did not make and `check` accepts it, so the log and seal can be forged unseen. Last, it is wrong if replaying the kept checks takes more than a quarter of a run's wall clock, because a check nobody can afford to replay will not stay kept.

## Considered options

- **The tester's own evidence files as proof** (screenshots, saved command output, a checker that reads them). Rejected. A file the tester writes proves only that the tester wrote it. The proxy's log is the one record the tester did not write.
- **A tester with the code checked out, told not to read it.** Rejected as the only defence. Nothing holds an instruction. The transcript audit sees the files a tool call names, and the proxy keeps the verdict tied to use whatever the tester read.
- **Kept checks as scripts the tester writes.** Rejected. A script carries the machine it was written on (paths, ports, a browser's location), can do anything when it runs, and cannot be shown to prove something without running it. Data can be read, filled in and run against a do-nothing product.
- **A browser automation library as a dependency.** Rejected. The plugin adds no dependency, and every Chromium speaks the DevTools protocol over a pipe with no library.
