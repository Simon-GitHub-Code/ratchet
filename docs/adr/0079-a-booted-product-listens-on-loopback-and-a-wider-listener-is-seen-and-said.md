---
status: accepted
---
# A booted product listens on loopback, and a wider listener is seen and said

Tracked by: #54.

`with_service.py` boots a product on a port it picked and exports `PORT`, and nothing about where to listen. A product that listens on every interface by default is then reachable from the network for as long as a gate or an acceptance session holds it up. In an acceptance session that also defeats the log: a request can reach the product on its own port, around the proxy, and change it with no line (ADR 0035).

## Decision

**The boot command is offered the loopback address the way it is offered the port.** `with_service.py` exports `HOST=127.0.0.1` and replaces `{host}` in `--boot`, `--ready`, `--env` values and the command, as it replaces `{port}`. How a product is told its address stays the project's boot line. The template's example boot line passes both, and its `start` recipe gives the same two.

**Where the product listens is read, not inferred.** Once the product answers, `with_service.py` reads the kernel's table of TCP listeners (`/proc/<pid>/net/tcp` and `tcp6`), which `listeners.py` already read for the graceful stop and which held the address it dropped. Every listener is judged: each one on the ready URL's port, whatever process holds it, and each one held by the boot command's process group or a child of it, on any port. An IPv4 or IPv6 loopback address, and an IPv4-mapped loopback address, is loopback. `0.0.0.0` and `::` are every interface; any other address is not loopback.

**In a gate, a wider listener is a note.** Each one is one line, `with_service: note: the product listens on <address>:<port>, ...`, naming the cure, and the gate keeps its colour. An upgrade must never turn a project red, and a project may listen wider on purpose.

**In an acceptance session, a wider listener refuses the session.** The harness passes `--loopback-only`. A listener beyond loopback stops `with_service.py` before it runs the proxy, so no session starts, and `up` writes the harness's blocker. The blocker names the address and both cures: bind to `127.0.0.1`, or declare the address. `replay` boots the same way and says why a check was not run.

**Listening wider on purpose is declared, and the declaration is checked.** A project sets `listens := "<address>"` in its justfile, next to `boot`. The harness reads it as it reads `boot`, refuses to start when it is not an IP address, and passes it as `--listens`. A listener on exactly that address is said in one line and allowed. A listener on any other address beyond loopback still refuses. Silence declares nothing.

**What cannot be read is said to be not checked.** Where the system has no listener table, or no listener is found on the port or among the boot command's processes, the run prints `with_service: where the product listens was not checked: <why>` and carries on. It neither passes in silence nor refuses.

## What an existing project sees

Nothing in its boot line has to change, and the upgrade does not rewrite it. A product that reads `HOST` from its environment now listens on loopback with no edit. A product that listens on every interface shows one note per wider listener in every gate that boots it, and stays green. Its next acceptance session refuses to start until its boot line passes `{host}` or `$HOST`, or its justfile declares `listens`. A project that adds `{host}` to its boot line takes the template's new `start` recipe with it, which the upgrade lists, or `just start` hands the product the literal `{host}`.

## Refutation criterion

This is wrong if an acceptance session starts while its product holds a listener beyond loopback that `listens` does not name, on Linux, or if a gate turns red only because its product listens wider. It is also wrong if, across the first ten projects that upgrade, the note proves to be noise that every one of them silences by declaring an address rather than by binding loopback.

## Considered options

- **Infer the address from the boot line**, looking for a flag or `0.0.0.0` in it. Rejected. The plugin names no framework or flag, and a default inside the product never shows in the line.
- **A wider listener is red in a gate.** Rejected. An upgrade must never turn a project red, and a project may serve wider on purpose.
- **Only a note in an acceptance session.** Rejected. A product reachable around the proxy leaves the log unable to say it saw every request, which is what a session's verdict rests on.
- **Judge only the listener on the ready URL's port.** Rejected. A product that opens an admin or debug listener beside its main one would pass while that listener is open to the network.
- **Declare wider listening in the binding document.** Rejected for now. The harness reads the product's start-up from the justfile, and `listens` sits next to `boot`, where a change to one is seen beside the other.
