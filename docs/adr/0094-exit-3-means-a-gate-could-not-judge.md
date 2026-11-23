---
status: accepted
---
# Exit 3 means a gate could not judge

Settles the remainder of issue #139. Amends ADR 0084 point 3 and `skills/method/tiers.md`.

ADR 0084 kept two gate outcomes, green (`0`) and red (`1`), because `check` and the bite proof read any non-zero exit code alike. On the older unheld path of the `graceful-stop` gate, that left two flaws. A load command that finished before `--signal-after` exited `1` as a failure even when the service never dropped anything, and a run where every post-signal reset came from an unaccepted connection exited `0` (`load-smoke: green`) even when zero accepted requests were in flight at the stop signal.

## Decision

**1. Exit code `3` (`gatelib.UNJUDGED`) means `could not judge`.** It is the method's third gate outcome, distinct from green (`0`) and red (`1`). `gatelib.report(..., exit_code=gatelib.UNJUDGED)` prints `<name>: could not judge` and returns `3`.

**2. The tier runner never passes exit `3` silently.** `scripts/parallel.py` treats `exit_code == gatelib.UNJUDGED` as `Standing.UNJUDGED` (`"could not judge"`). For that gate it prints `parallel: <name>: could not judge (exit 3) in Xs` and lists `<name>: could not judge (exit 3)` among the tier's problems. When all problems in the run are unjudged gates, `parallel.py` prints `parallel: could not judge`, exits `3`, and writes the tier timing record with `"status": "not judged"` and `"exit": 3`. When any gate is red, any work ceiling is over, or the loop budget is over, `parallel.py` prints `parallel: red`, exits `1`, and writes `"status": "red"`.

**3. The bite proof never counts exit `3` as biting.** In `scripts/project_bite.py`, a planted fault whose gate exits `3` yields `Verdict.COULD_NOT_JUDGE` (`COULD NOT JUDGE (exit 3)`), even when the expected substring appears in output, and fails the proof. A clean baseline run that exits `3` before planting yields `Verdict.COULD_NOT_JUDGE_BEFORE` (`COULD NOT JUDGE BEFORE THE FAULT (exit 3)`). A gate that could not set up its observation cannot prove a fault bites.

**4. `with_service.py` and `load_smoke.py` exit `3` when the older unheld path cannot judge a stop.**
- In `scripts/with_service.py`, `older()` returns exit `3` when the load command ends before `--signal-after` so no stop signal was sent. If the load command itself exited non-zero and not `3`, or if `with_service.py` hits a red failure (dropped request, non-zero service exit, shutdown timeout, or listening after stop), red wins and `with_service.py` exits red. If the command exited `3` and no red failure occurred, `with_service.py` propagates exit `3` and reports `with_service: could not judge`.
- In `scripts/load_smoke.py`, worker threads record each request that started before `RATCHET_STOPPING` appeared and finished or timed out after it, and `judge()` adds early connections reset by the service rather than taken by the gate. Whenever `judge()` excuses resets (`taken > 0`), it prints `load-smoke: <N> request(s) had been accepted and not yet answered at the stop signal`. On the older unheld path (`not held`), when resets were excused (`taken > 0`), zero accepted requests were in flight at the signal (`accepted_in_flight == 0`), and no red error occurred, `load_smoke.py` reports `load-smoke: could not judge` and exits `3` instead of printing `load-smoke: green`. On the held path (`held`), the held request itself is in flight across the signal by construction, so `load_smoke.py` stays green while still printing the accepted-in-flight count.

## Refutation criterion

This decision is wrong if any gate exiting `3` passes a tier silently (`parallel.py` exits `0`), counts as biting in `project_bite.py`, or turns a real red failure (service crash, dropped request, or failing load command) into exit `3`. It is also wrong if a clean draining server on the held path turns non-zero under `with_service.py --graceful`.

## Considered options

- **Exit `0` for `could not judge`.** Rejected: a gate that could not observe the stop would pass CI and local checks silently, hiding broken setups or lost races on the older path.
- **Keep exit `1` everywhere.** Rejected: `project_bite.py` and `check` could not distinguish a broken observation from a service that dropped requests, and `load_smoke.py` would still exit `0` when all post-signal resets were unaccepted and nothing accepted was in flight.
- **Print the accepted-in-flight count only on the older path.** Rejected: printing the count whenever resets are excused lets operators inspect concurrency at the signal on both paths with one rule.

## Consequences

- `parallel.py` and `with_service.py` treat exit `3` as `could not judge`, so tests that previously used exit `3` as an arbitrary red exit code use another non-zero code such as `2` or `4`.
- On the older unheld `graceful-stop` path, runs that finish before `--signal-after` or excuse resets with zero accepted requests in flight stop passing or failing as red and exit `3` instead.
- `project_bite.py` rejects any bite whose gate exits `3` before or after planting the fault.
