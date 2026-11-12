---
status: accepted
---
# A repository is operable from the first commit: telemetry, flags, security and load are gates

Two throwaway projects were taken through the whole route on 2026-11-11, a Python service and a TypeScript one. Both ended with every bound gate green and neither had a span, a metric or a request log; neither had a feature flag; the "image scan" row was `trivy fs` over the source tree of a service that ships as a container; the dynamic scan was bound to `none` in both; the performance row was a micro-benchmark that could not fail in one stack and was red for want of a benchmark in the other. `grep` over the method found no mention of telemetry or flags at all. A method that says what must be true before a merge said nothing about whether the merged thing can be watched, turned off, attacked or loaded.

We decided that the method asks these four of every repository, and that each is split across the tiers by cost, so the developer's loop pays for the cheap part only:

- **Telemetry.** Tier 1: every entry point is registered through one instrumented seam and a test reads its span from an in-memory exporter; the signal names are snapshotted beside the tests. Tier 2: the signals arrive at a collector in the end-to-end environment. OpenTelemetry is the default the presets name, since it is the one vendor-neutral API; a stack without it binds its equivalent.
- **Feature flags.** Tier 0: `flags.toml` is the registry and `scripts/flags.py` judges it in milliseconds: each flag declared once with an owner, a default, a reason and a removal date (or `permanent`), a flag the merge base lacks defaulting to off, a key no source file names removed. Tier 1: the suite once per state of each flag the change adds or edits, and none when it touches none. Tier 2: no flag past its removal date, asked there because it goes red by the calendar.
- **Security.** The rows that existed stay (secrets and pins in tier 0; static analysis, dependency audit and artefact scan in tier 1; a dynamic scan in tier 2). Added: configuration hardening of the container and infrastructure files in tier 1, an active scan with contract-driven attacks in tier 2, and the artefact scan reads the built image when the deliverable is one. The critic role attacks every new entry point.
- **Load.** Tier 1: a load smoke of seconds, judged on errors only, since a pull-request runner is no benchmark host. Tier 2: a load test against the Limits' arrival rate, percentile latency and error rate. Weekly: soak and breaking point, advisory.

The conformance gate requires the rows, the binding's Telemetry and Feature flags sections and the registry. A row may be `none` with a reason, as any other; it may not be absent. The spec role names the telemetry and the flag of every feature, the review judges them, and the pull request template has an Operability section. Adopt binds each row against a walking skeleton, so none of them waits for the first feature. Decided 2026-11-11.

ADR 0017 deleted the presets; it says how to read "the default the presets name" here, and this decision stands.

## Refutation criterion

This is wrong if the rows slow the loop or turn into paperwork. Measure on the next two adoptions: tier 0 stays under its minute and the added tier 1 recipes (`telemetry`, `flag-states` with no changed flag, `load-smoke`, `iac`) add under thirty seconds together on the skeleton; if not, the slow one moves a tier down. And read the first five features built after adoption: if more than one binds a row to `none` to get past it, or ships a flag nobody ever turns on or removes, the row is ceremony and is cut or redesigned. Measured so far: `flags.py` is tens of milliseconds; `trivy config` was 3.9 s warm and 12 s cold on the TypeScript trial, which is why it sits in tier 1 and not tier 0.

## Considered options

- Leave them to each project's non-negotiables: rejected, that is the state the trials found, and an agent builds what the gates ask for and nothing else.
- Put every new row in tier 0 or 1 so nothing is deferred: rejected, a load test and an active scan need a running environment and minutes, and a gate that slow in the pull request is the gate that gets skipped.
- A flag service instead of a file registry: rejected as the default, the registry is the part a deterministic gate can read; the service sits behind the seam and the binding names it.
- Judge latency in the tier 1 load smoke: rejected, shared runners make it a coin flip, and the method does not quarantine a test, so it judges errors there and latency in tier 2.
