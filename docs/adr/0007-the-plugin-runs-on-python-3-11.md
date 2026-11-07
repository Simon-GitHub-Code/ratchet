---
status: accepted
---
# The plugin runs on Python 3.11

The hook and the gate scripts run on whatever `python3` the harness or the CI runner has: the plugin installs no interpreter and ships no dependency. The code used `PurePath.full_match`, which arrived in Python 3.13, so on `ubuntu-latest` (3.12) every manifest-reading gate died with an `AttributeError`, and on a workstation with an older `python3` the hook answered `ask` on every tool call. We decided that the floor is Python 3.11, the oldest version with `tomllib`, which the manifest and thresholds need; that `full_match` is written out once as `ratchet_guards.globs` for the hook and once in `scripts/gatelib.py` for the gates, with a property test holding the two to the same answers and to the standard library's where it exists; and that the floor is stated in one place each for the shim, the adopt skill and the packaging, all saying 3.11. Decided 2026-11-07.

## Refutation criterion

The decision is wrong if a hook or gate fails on a Python between 3.11 and the current release in two of the first twenty adopted repositories, or if the two glob implementations are found to disagree on a pattern a manifest or thresholds file uses. Check after the first five repositories adopt the method with this version.

## Considered options

- Pin Python 3.13 in the CI templates with a setup step and say 3.13 in the skill: rejected, the hook still runs on the workstation's `python3`, which the plugin does not control, and an interpreter install is one more step every harness adapter would have to carry.
- Depend on a glob library: rejected, the hook is standard library only so that it runs before any project has installed anything.
- Import the hook's matcher from the gate scripts: rejected, the scripts run with the plugin's `scripts/` directory alone on the path, and a `sys.path` edit at import time is a workaround the method asks the critic to flag; two copies held equal by a test is the honest shape.
