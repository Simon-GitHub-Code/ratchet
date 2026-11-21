---
status: accepted
---
# A booted product listens on loopback, and a wider listener is seen and said

Settles issue #54.

`with_service.py` boots a product on a port it picked and exports `PORT`, and nothing about where to listen. A product that listens on every interface by default is then reachable from the network for as long as a gate or an acceptance session holds it up. In an acceptance session that also defeats the log: a request can reach the product on its own port, around the proxy, and change it with no line (ADR 0035).

## Decision

**`{host}` is offered beside `{port}`, and nothing is exported.** `with_service.py` replaces `{host}` in `--boot`, the `--env` values and the command with the host of the ready URL, which is where the gate probes. An IP address stays as written, and an IPv6 literal loses its brackets. A name such as `localhost` stays the name. The product resolves it with the resolver the probe uses, and the probe tries every address the name resolves to, so it reaches whichever one the product binds. An address picked for the product could be of a family it does not speak. A project opts in by writing `{host}` where its product takes its address, in whatever form the product takes it. Nothing is exported, since a variable name is a stack's convention, and a product that already read that name would listen elsewhere or answer differently with no edit by its project. A boot line without `{host}` boots as before. A boot line that already holds the text `{host}` for a purpose of its own now has it replaced, as `{port}` already is. Neither has an escape. `--ready` cannot hold `{host}`. The template's example boot line passes `{host}`, and its `start` recipe gives the ready URL's host as `with_service.py` reads it, without a user name and in lower case.

**Where the product listens is read when it first answers.** `with_service.py` reads the kernel's table of TCP listeners (`/proc/<pid>/net/tcp` and `tcp6`) and the open files of each process of the boot command's process group and its descendants, as the process table shows them at that moment. A listener one of those processes holds is the product's. An IPv4-mapped address is read as the IPv4 address it maps, which Python 3.11 before 3.11.10 does not classify. An IPv4 or IPv6 loopback address is loopback, `0.0.0.0` and `::` are every interface, and any other address is not loopback. While a process of the group holds the ready URL's port, a listener another process holds on that port number is not judged as the product's, and one beyond loopback gets a line that says so. When no process of the group holds the ready URL's port, as when a container's runtime or a forwarding helper outside the group holds it, the listener on that port is judged as the product's, whoever holds it and whatever else the group holds, since it is what answered the ready probe. What else the process outside the group holds is then not checked. A process that is neither in that group nor a descendant of it is not attributed to the product, so a listener it holds on a port other than the ready URL's is not judged. Usually that is a daemon that left the group and whose parent has exited. A listener opened after the first answer is not seen either, and each line says when it looked.

**In a gate, a wider listener is a note.** Each one is one line, `with_service: note: the product listened on <address>:<port>, ...`, naming the cure, and the gate keeps its colour. An upgrade must never turn a project red, and a project may listen wider on purpose.

**Listening wider on purpose is declared.** The justfile's `listens` names each address the product listens on on purpose, separated by spaces, or is `none`, as the template sets it. `--listens` takes the same list and may be given more than once. A listener on exactly a declared address, not a prefix or a range, gets one line that ends "as declared", in a gate and in a session. An empty value is refused, with what to write. The template's `booted` line passes `--listens` when `listens` is not `none`, so a project that declares no longer sees the note.

**In an acceptance session, a wider listener refuses the session.** The harness passes `--loopback-only` and the declaration. A listener beyond loopback that is not declared stops `with_service.py` before it runs the proxy, so no session starts, and `up` writes the harness's blocker, naming the address and both cures. `replay` boots the same way and says why a check was not run. The product `replay --control` boots is the harness's own and declares `127.0.0.1`, where it listens.

**Three facts decide, and one table.** What was seen: each listener judged as the product's, on loopback or wider. What was not checked: a missing, unreadable or malformed table, a process the gate may not look into, and a ready URL's port that no process of the group holds. The scan goes on past each of these. What was declared: `listens`, a list of addresses or `none`.

| | In a gate | In a session (`up`, `replay`) |
| --- | --- | --- |
| Seen wider than loopback, not declared | a note per listener; the gate keeps its colour | refused, naming each listener and both cures |
| Seen, each on loopback or declared | "as declared" per declared listener | starts; "as declared" per declared listener, and one line that each listener of the boot command's process group and its descendants was on loopback or declared, naming what it did not judge |
| Not checked, `listens` declared | one line: not checked here, as declared, with the addresses and why | starts, with the same line |
| Not checked, `listens` is `none` | a line per thing not checked, with why | refused, naming the cure: declare in `listens` each address the product listens on, a loopback address if that is all |

The first row wins. A declaration never waives a listener that was seen and is not declared, whatever else was not checked. Where nothing could be checked, the project's declaration is taken as where the product listens, so a session starts on any system once the project says where its product listens.

## What an existing project sees

A project that changes nothing keeps every gate's colour and exit code. A gate that boots a product listening wider prints one line per wider listener, a note, or "as declared" once the project declares it. Nothing is exported, so a product that reads an address or a host name from its environment behaves as before.

One change can stop something that ran before. An acceptance session, `up` or `replay`, refuses a product that listens wider than loopback on an address `listens` does not name, and a product whose listeners cannot be checked while `listens` is `none`. Today that meets only a person who runs the harness by hand, since no skill drives it yet. A product whose listener a process outside the boot group holds, behind a container's runtime or a forwarder, started a session before this change while it listened on loopback, and is now refused until the project adds one line to its justfile: `listens := "127.0.0.1"`. The cure is one line: pass `{host}` in `boot` where the product takes its address, or name in `listens` each address the product listens on, a loopback address if that is all.

To adopt `{host}`, a project edits its `boot` line. If its `start` recipe came from an earlier template, it also takes the new template's `start` line, since the old one leaves `{host}` as written. To stop the note for a listener that is wide on purpose, it adds `listens` and the template's `booted` line. The upgrade changes none of these.

## Refutation criterion

The scan judges each listener of the boot command's process group and its descendants, and the listener on the ready URL's port when none of them holds that port. This is wrong if, on Linux, an acceptance session starts while a listener the scan judges is beyond loopback and `listens` does not name it, or starts on something not checked while `listens` is `none`. It is also wrong if a project that declares `listens` cannot start a session on a system where nothing could be checked. It is also wrong if a gate turns red, or a boot line without `{host}` boots differently, because of this decision. And it is wrong if, across the first ten projects that upgrade, the note proves to be noise that every one of them silences by declaring an address rather than by binding loopback.

## Considered options

- **Export a variable that holds the loopback address.** Rejected. A variable name is a stack's convention, which the method does not pick, and it changed products that already read that name: one listened where the gate did not probe, and one read it as its host name and answered 400.
- **`{host}` is always `127.0.0.1`.** Rejected. A product told to listen there does not answer a ready URL on IPv6 loopback or on the machine's address.
- **A name in the ready URL becomes the first address it resolves to.** Rejected. A product that speaks only one address family could be handed an address of the other.
- **Infer the address from the boot line**, looking for a flag or `0.0.0.0` in it. Rejected. The plugin names no framework or flag, and a default inside the product never shows in the line.
- **A wider listener is red in a gate.** Rejected. An upgrade must never turn a project red, and a project may serve wider on purpose.
- **Only a note in an acceptance session.** Rejected. A product reachable around the proxy leaves the log unable to say it saw every request, which is what a session's verdict rests on.
- **Judge only the listener on the ready URL's port.** Rejected. A product that opens an admin or debug listener beside its main one would pass while that listener is open to the network.
- **Judge every listener on the ready URL's port, whatever process holds it.** Rejected while a process of the group holds that port. On a shared machine, another process's listener on that port, on another address, refused a session that had nothing to do with it. When none of them holds it, the listener on that port is the only one that can have answered the probe.
- **Start a session on what could not be checked, with a line, whatever `listens` says.** Rejected. A process the gate may not look into could hold an undeclared wider listener, and the session would start beside it with nobody having said where the product listens.
- **Refuse a session on whatever could not be checked.** Rejected. On a system without the table, or where a container's runtime holds the listener, no session could start whatever the project set.
- **Declare wider listening in the binding document.** Rejected for now. The harness reads the product's start-up from the justfile, and `listens` sits next to `boot`, where a change to one is seen beside the other.
