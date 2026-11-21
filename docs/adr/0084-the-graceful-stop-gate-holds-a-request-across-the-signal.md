---
status: accepted
---
# The graceful-stop gate holds a request across the signal

Settles issue #139. Read with ADR 0080, whose point 5 lets `--graceful S` stand, and ADR 0081, which asks where the guard cannot judge. Amends the graceful-stop row of `skills/method/tiers.md`.

**What was asked.** The gate is meant to give the same verdict on a busy machine as on a quiet one: a service that drops a request in flight at the stop signal is always red, a clean service is never red because the machine was slow, and when the gate cannot judge it says so. It did not. It signalled a fixed time after the load command began, and judged by the load command's own requests. Under load, the 8 connections of the planted fault, a server that exits on SIGTERM without draining, were all counted as never accepted, and the fault passed. A clean service whose first request came late had nothing in flight at the signal, and was red on the request floor. A load command that failed was red with no word of what failed.

## Decision

**1. The gate holds one request across the signal.** It opens a connection to the first `--url` the load command names, or to the ready URL when a custom command names none, and sends the whole request but its last byte. It reads that the service accepted it: the service's end of that connection in the kernel's TCP table has a socket inode, which it has only once `accept()` returned, and that inode is among the open files of a process of the boot command's group. A copy held by the gate, or by a process outside that group, does not count. From then the load command sends. After `--signal-after` seconds of load the gate signals SIGTERM. It sends the last byte once no process of the service holds a listener on that port in the listening state, and then wants the bound status. So the request was accepted before the signal and completed after it, on every machine, however slow.

**2. Three outcomes.** `with_service.py --graceful` ends in one of three, each with its own exit code and last line.

| Outcome | Exit | Last line | When |
| --- | ---: | --- | --- |
| green | 0 | `with_service: green` | the held request was answered with the bound status, the service exited 0 within S, and the load command passed |
| red | 1 | `with_service: red` | the held request was reset, closed, answered with another status or not answered within S; the service did not exit 0 within S, or still listened when S ran out; the service exited before the signal; or the load command failed for its own requests |
| could not judge | 3 | `with_service: could not judge` | the load command did not start sending within `--timeout` or ended before the signal; the service did not accept the held request within `--timeout`; the gate could not send it; the service answered it before its last byte; or the gate may not read the service's open files, or finds the connection in no table it can read, as in another network namespace |

Each line before the last says why, and what to change. The worst outcome wins, red over could not judge over green. A load command that exits non-zero turns green red, and leaves could not judge as it is, since the gate stops the service once it has judged.

**3. `check` lists could not judge as red, and nothing retries it.** It is a non-zero exit, so `check` and CI are red, with the reason on the gate's own line. A retry would be a second throw at the same race, which ADR 0080 rules out for every gate.

**4. No fixed limit on this path can turn a clean service red.**

| Limit | Before | Now |
| --- | --- | --- |
| `--signal-after`, from the load command creating `RATCHET_SENDING` to SIGTERM | could leave nothing in flight: red on the floor or on a drop counted as never accepted | timed from the service accepting the held request, so a request is in flight however late the load comes |
| `--timeout` for the command to start sending | red | could not judge |
| `--timeout` for the service to accept the held request | none | could not judge |
| `load_smoke.py --timeout` per request under `--until-stopped` | a slow answer was an error | not applied; the gate ends the service once it has judged, so nothing hangs |
| `load_smoke.py --min-requests` under `--until-stopped` | red under the floor | not judged; the held request is the request in flight |
| the smoke's `--timeout` to wait for the gate to say which connections it took | judged the older way past it | removed; the gate answers while the command runs, and once more when it stops taking connections |
| `--graceful S` | red past S | red past S, the project's own question (ADR 0080, point 5) |

**5. The not-counted rule stays.** After the signal the gate resets the connections still waiting in the service's listener, and the smoke does not count those. Only connections the gate itself took are excused, and the held request was seen accepted, so it is never among them. With the planted fault under 8 connections all 8 of the smoke's requests can still be excused, and the gate is red on the held request.

**6. A failing load request is named.** Each error line of the smoke gives the error's kind and its message, as `ConnectionResetError: [Errno 104] Connection reset by peer`. The held request's line names the kind a dropped request raised, and adds `a ConnectionResetError` when the kind is one, so an adopted project's bite that expects that word still bites.

## What a project sees

The recipe `graceful-stop` keeps its spelling, and every flag keeps its name. `--signal-after` now counts seconds from the service accepting the held request, not from the command starting to send. The template's bind text now asks for a route that reads its whole request before it answers. An upgrade does not rewrite a bound recipe, so an adopted project keeps its route. A route that answers before it reads its request, such as a readiness route that ignores the request, ends could not judge with the bind text's advice. A service whose bound route is slow to answer is green as before. A project that boots its service in a container, or in another network namespace, or hides its open files from the gate, now sees could not judge in place of a less exact green.

## Evidence

Observed on a shared 16-core Linux machine, with the gate's tests run four at a time beside other work.

| Run | Fault planted | Cure | One-minute load |
| --- | ---: | ---: | --- |
| the ticket's fault, a server that exits on SIGTERM without draining, under 8 connections | red in 50 of 50 | green in 50 of 50 | 15.7 at the start, 24.5 at the end |

Before the change, all ten new cases failed on the old gate, in one run. The fault with the signal between two requests was green. The clean service whose first request came after the signal, and the one slow to accept, were red. A command that ended first was red as a drop. Under 8 connections the old gate happened to catch the fault in that run, by one reset of 8, the other 7 excused as never accepted. The ticket's third sighting is the run where it caught none.

## Known limits

- A service that closes a connection whose request head has not arrived in full is red. The held request's last byte falls in its head unless the route takes a body. That is a real drop of a request already sent, and a body moves the split into it.
- A service that keeps listening until every request it holds is answered waits for the held request's last byte, which comes only once it stops listening, and is red when S runs out with the line `still listened on port`.
- A server whose read timeout is shorter than `--signal-after` closes the held request before the signal, which is could not judge.
- `bite.py` counts any non-zero exit as a bite, so a fault that ends could not judge counts as bitten.
- A load command other than the smoke is judged by its exit code alone, and the gate prints only what it printed.

## Refutation criterion

This is wrong if the planted fault is ever green, or a clean service ever red, under load. The measurement is `hooks/tests/test_with_service.py::test_the_tickets_planted_fault_under_eight_connections_is_red_and_its_cure_green` in a loop of 50 beside the machine's load average: one green fault, or one red clean service, refutes it. It is also wrong if could not judge turns out common on ordinary services: more than one in twenty graceful-stop runs ending 3 in the first ten adopted projects would reopen how the gate holds its request.

## Considered options

- **Keep the fixed signal time and widen the smoke's windows.** Rejected. Any fixed time loses the race on some machine, and the gate cannot tell when it lost.
- **Signal once the smoke reports a request in flight.** Rejected. The smoke's requests may be answered before the signal arrives, and a custom command reports nothing.
- **Hold the request and send the last byte right after the signal.** Rejected. A fast route answers it before the service has handled the signal, which says nothing about the stop.
- **Retry once on could not judge.** Rejected, for point 3.
