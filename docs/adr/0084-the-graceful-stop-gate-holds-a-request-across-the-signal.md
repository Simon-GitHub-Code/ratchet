---
status: accepted
---
# The graceful-stop gate holds a request across the signal

Part of issue #139. Read with ADR 0080, whose point 5 lets `--graceful S` stand, and ADR 0081, which asks where the guard cannot judge. Amends the graceful-stop row of `skills/method/tiers.md`.

**What was asked.** The gate is meant to give the same verdict on a busy machine as on a quiet one: a service that drops a request it had taken when the stop signal came is red, one that finishes it is green, and neither verdict comes by chance. An upgrade turns no green project red, and a project doing nothing wrong can always be green. It did not. It signalled a fixed time after the load command began, and judged by the load command's own requests. Under load, the 8 connections of the planted fault, a server that exits on SIGTERM without draining, were all counted as never accepted, and the fault passed. A clean service whose first request came late had nothing in flight at the signal, and was red on the request floor. A load command that failed was red with no word of what failed.

## Decision

**1. Where the gate can see the service take it, the gate holds one request across the signal.** It opens a connection to the first route the load command names that takes a body, and sends the whole request but the body's last byte. The request's head is then whole, so the server has a request it is bound to answer. It reads that the service accepted it: the service's end of that connection in the kernel's TCP table has a socket inode, which it has only once `accept()` returned, and that inode is among the open files of a process of the boot command's group. A copy held by the gate, or by a process outside that group, does not count. From then the load command sends. After `--signal-after` seconds of load the gate signals SIGTERM, sends the last byte at once, and wants the bound status.

The gate waits for nothing between the signal and the last byte, because nothing there needs to be seen. A service cannot read a byte that has not been sent, so it cannot finish the held request before the signal: at the signal that request is in flight on every machine, however slow. What the service does next is its own: it answers the request, or closes it, or lets `--graceful S` run out. A correct service answers, whenever the byte arrives, since it has a whole head and a body it is waiting for. A service that drops what it holds at a stop has the stop ahead of the byte in every queue it reads in order: the signal is pending before the byte is sent, so a process that takes the signal in the thread that reads the connection acts on the stop before it can read the byte. Waiting for some sign that the service acted on the signal would wait on an event a correct service need not produce. The first round waited for the service to stop listening, and a server whose workers keep their inherited listener while they finish the current request never does, so that correct server was red.

**2. Where it cannot, the gate judges as it did before, at once.** It signals `--signal-after` seconds after the load starts, judges by the load command's own requests, and prints one line, `with_service: the gate judged the stop the older way, by the command's own requests, since ...`, with the reason.

| Case | The reason the line gives |
| --- | --- |
| no route the command names takes a body, or the command names no route | no route that takes a body was found among the routes the command named, which it lists, and a byte held back would fall in a request's head, which a server may close at a stop |
| the gate cannot connect or send | the error |
| the service does not accept the held connection within `--timeout` | it was still in the listener's queue |
| a process outside the boot command's group accepted it, as a forwarder does | no process of the service holds it |
| the connection is in no TCP table the gate can read, as in another network namespace or on a system without one | where it looked |
| the gate may not read the open files of a process of the service | the process and the error |
| the service answers or closes the held request before the signal | the route must wait for its whole request, longer than `--signal-after` |

None of these waits out a limit the project did not set: each ends on what the gate sees, or on `--timeout` for the service to accept.

Every way out of the gate's wait for the signal releases the load command, a command that starts sending after `--timeout` included. The smoke waits for that release, and for the gate to say which connections it took, each no longer than the gate's `--timeout`, which the gate exports as `RATCHET_HOLD_WITHIN`, plus a margin of one second, `GATE_MARGIN` in `load_smoke.py`. Past either it is red and says what it waited for. The smoke's own `--timeout` is per request and plays no part: until the fourth round it bounded the wait for the gate's answer, so a project that set it below the time a scan of the processes takes could turn a clean run red. Before the third round the gate gave up on a command that had not started sending without releasing it, so a load smoke that started sending after `--timeout` waited for ever.

**3. Two outcomes, as before, and no third.** Green exits 0 and red exits 1, with the last lines they had. The held request is red when it is reset, closed, answered with another status or not answered within S. The first round added a third outcome, could not judge, with exit 3. It leaves this change: today `check` lists any non-zero exit as red, and the proof counts any non-zero exit as the gate biting, so a third outcome needs both taught, and a later record owns that decision.

**4. No fixed limit on the held path can turn a clean service red.**

| Limit | Before | Now |
| --- | --- | --- |
| `--signal-after`, from the load command creating `RATCHET_SENDING` to SIGTERM | could leave nothing in flight: red on the floor or on a drop counted as never accepted | timed from the service accepting the held request, so a request is in flight however late the load comes |
| `--timeout` for the command to start sending | red | red, as before |
| `--timeout` for the gate to connect and see the service accept the held request | none | judged the older way past it |
| the smoke's wait for the gate to say whether it holds a request | none | the gate's `--timeout` and a margin of one second: red past it |
| the load command ending before the signal | red | red on the older way, as before; on the held path the gate signals at once and the held request judges |
| `load_smoke.py --timeout` per request under `--until-stopped` | a slow answer was an error | applied, and a request that runs past it is not counted once the gate held a request to the signal: a busy machine is slow, and the held request is the one judged; counted as before on the older way |
| `load_smoke.py --min-requests` under `--until-stopped` | red under the floor | not judged once the gate held a request to the signal, which is the request in flight; judged as before on the older way |
| the smoke's wait for the gate to say which connections it took | judged the older way past the smoke's `--timeout` | red past the gate's `--timeout` and a margin of one second; the gate answers while the command runs, and once more when it stops taking connections |
| `--graceful S` | red past S | red past S, the project's own question (ADR 0080, point 5) |

**5. The not-counted rule stays.** After the signal the gate resets the connections still waiting in the service's listener, and the smoke does not count those. Only connections the gate itself took are excused, and the held request was seen accepted, so it is never among them. With the planted fault under 8 connections all 8 of the smoke's requests can still be excused, and the gate is red on the held request.

**6. A failing load request is named.** Each error line of the smoke gives the error's kind and its message, as `ConnectionResetError: [Errno 104] Connection reset by peer`. The held request's line names the kind a dropped request raised, and adds `a ConnectionResetError` when the kind is one, so an adopted project's bite that expects that word still bites.

## What a project sees

The recipe `graceful-stop` keeps its spelling, every flag keeps its name, and the exit codes are those of before. The template's bind text now asks for a route that takes a body and reads all of it before it answers. An upgrade does not rewrite a bound recipe, so an adopted project keeps its route. A project whose bound route takes a body is judged by the held request, and `--signal-after` counts from the service accepting it. A project whose route takes none, or whose acceptor is a forwarder outside the boot command's group, or whose service runs where the gate cannot read its tables or files, is judged as before, and sees one line that says so, why, and what to bind to be judged by a held request. Every stand-in that drains and was green on the old gate is green on this one (Evidence). The one green that can turn red is on a Linux system with no process tables, where the old gate never sent the signal at all; this one sends it and judges the older way.

## Evidence

Observed on a shared 16-core Linux machine, beside other work.

**Where a server has committed to a request.** Each server kind on this machine was sent a request and SIGTERM with the request at three points, and the rest was sent at once and 0.5 s after the signal: the head not yet whole, the head whole with the body pending, and the handler running. One-minute load was 12.6 to 22.3.

| Server | head not whole | body pending | handler running |
| --- | --- | --- | --- |
| a threaded server, a forking server, an event-loop runtime of another language | answered | answered | answered |
| the test stand-ins that drain: one process, workers that keep or close the listener, a handoff to another session, a forwarder, a grandchild, a daemon | answered | not run: their routes took no body | answered |
| a stand-in that leaves its handler thread behind when it exits | answered at once, closed 0.5 s later | not run | answered |

No server here closed a partial head, but servers are known that close, at a stop, a connection whose head has not arrived in full. So the gate holds a byte of the body, never of the head, and `CLOSES_HALF_SENT` in the tests is such a server: green with a body, judged as before without one.

**Never worse than before.** The old gate and this one ran over 30 stand-ins, 21 that drain and 9 that drop, each with a route without a body and one with a body, beside a one-minute load of 14.8 to 21.0.

| Gate | right on the 42 runs that drain | right on the 18 that drop | runs judged by the held request |
| --- | ---: | ---: | ---: |
| the old gate | 35 | 17 | 0 |
| this one | 37 | 18 | 28 |

Every run this gate got wrong the old gate got wrong too, and with the same verdict. Those are a server that stops listening while connections wait in its queue, judged the older way, and two servers that close, at the stop, a connection whose head has not arrived in full. The held request was answered on both, but a load request caught mid-send was closed, and the load's own requests are still judged as before. The old gate was also wrong on a handoff to another session and green on a planted drop.

**The ticket's fault.** A server that exits on SIGTERM without draining, under 8 connections, and its cure, run side by side 30 times, two at a time: the fault was red on the held request in 30 of 30, and the cure green in 30 of 30. One-minute load was 14.0 to 17.1 at the start of each pair. The whole of `test_with_service.py`, four at a time, passed in 30 runs of 30, at a load of 13.0 to 19.7.

**The cases the first round got wrong.** Run on the old gate, the first round and this one, each with both routes:

| Case | old gate | first round | this one |
| --- | --- | --- | --- |
| workers that keep the inherited listener while they finish, two stand-ins | green | red, waiting on a listener that never closes | green: held with a body, the older way without |
| an acceptor outside the boot command's group | green | exit 3 | green, the older way |
| a system with no process tables | green, but the signal was never sent: the thread that sends it died reading the tables | hung | the older way at once |

On that last case this gate signals and judges the older way, which can be red on a request the server reset while it waited in the queue, as the older way always could; the old gate was green there because it judged nothing.

## Known limits, and what #139 still owes

- On the older way the ticket's race is still open: a project judged so is green or red as it was before this change, by timing. That covers every route without a body, a forwarder outside the boot command's group, a container's network namespace, a system without the tables, and a process that hides its open files.
- On the held path the load's own requests are still judged by the older rule. A server that closes, at a stop, a connection whose head has not arrived in full answers the held request, but can close a load request caught mid-send, and is then red, as it was before.
- A route without a body cannot be held where a server has committed to it. Some servers close, at a stop, a connection whose request head has not arrived in full, which is a graceful thing to do, so a byte held in the head could turn such a server red by chance. Holding no byte would leave nothing in flight. A later change could hold such a route by another means.
- A service whose thread that takes the signal is not the one that reads the held connection can read the last byte before it acts on the stop. If it then answers, the gate is green on a request the service did finish, and a drop of the load's requests is judged as before.
- A server whose read timeout is shorter than `--signal-after` answers or closes the held request before the signal, and is judged the older way.
- The third outcome, could not judge with its own exit code, waits for its own record: `check`, the runner and the proof must first learn to read it.

## Refutation criterion

This is wrong if the planted fault is ever green, or its cure ever red, on the held path under load: the measurement is `hooks/tests/test_with_service.py::test_the_tickets_planted_fault_under_eight_connections_is_red_and_its_cure_green` in a loop beside the machine's load average. It is also wrong if any service that finishes the requests it holds at a stop and was green on the old gate is red on this one: the measurement is the differential of Evidence, run against a new kind of server.

## Considered options

- **Keep the fixed signal time and widen the smoke's windows.** Rejected. Any fixed time loses the race on some machine, and the gate cannot tell when it lost.
- **Signal once the smoke reports a request in flight.** Rejected. The smoke's requests may be answered before the signal arrives, and a custom command reports nothing.
- **Send the last byte once the service stops listening.** The first round's choice, rejected: a correct server need not stop listening while it finishes a request, and one whose workers keep the listener waited on the gate while the gate waited on it.
- **Send the last byte once the signal is no longer pending.** Rejected. A service may block the signal and take it only between requests, and the kernel's delivery says nothing about what the service did with it.
- **A third outcome, could not judge.** Left to a later record, for point 3.
- **Retry once.** Rejected: a retry is a second throw at the same race, which ADR 0080 rules out for every gate.
