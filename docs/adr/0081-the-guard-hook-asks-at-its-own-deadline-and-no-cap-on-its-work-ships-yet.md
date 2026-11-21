---
status: accepted
---
# The guard hook asks at its own deadline, and no cap on its work ships yet

Read with ADR 0033, whose known limits name the hook that times out. Adds one case to the rule of ADR 0024 for a relative write after a `cd` the guard cannot follow.

The harness gives the guard hook a time limit: 10 s in `hooks/hooks.json`, and 10 s in the adapter for the other harnesses. A hook that passes it is cancelled, and on `PreToolUse` a cancelled hook does not block the call. So the call runs unjudged. A command built to be slow to read was a way past the guard, and so was an ordinary large command on a busy machine.

## What was measured

`hooks/tests/test_guard_work.py` builds 60 families of commands at sizes n, 2n and 4n. It counts the Python calls the guard makes while it judges each one, and asserts the growth per doubling, one doubling at a time, so a family that explodes fails at its first doubling. A count is the same on every run of one interpreter, and seconds are not. Seconds appear here only beside the load they were taken at. Linear work doubles per doubling, and work that grows with the square of the input quadruples.

| Family | n | `main`: work at n and 4n | per doubling | now: work at n and 4n | per doubling |
| --- | ---: | ---: | --- | ---: | --- |
| many assignments | 50 | 25,737 to 179,412 | 2.49, 2.80 | 19,171 to 78,121 | 2.00, 2.03 |
| a directory spelled from many variables | 3; 6 | 38,121 to 999,291 (n = 3) | 7.90, 3.32 | 184,134 to 237,338 (n = 6) | 1.16, 1.11 |
| a chain of wrappers that change to one directory | 10 | 33,820 to 383,800 | 3.21, 3.54 | 13,311 to 49,131 | 1.90, 1.95 |
| unclosed substitutions in a body | 10 | 29,042 to 496,022 | 4.25, 4.02 | 9,073 to 144,628 | 3.88, 4.11 |
| unclosed substitutions on the line | 10 | 28,565 to 463,010 | 4.14, 3.91 | 7,020 to 102,270 | 3.70, 3.94 |
| a generated script | 25 | 271,169 to 1,119,519 | 2.06, 2.00 | 194,944 to 814,619 | 2.09, 2.00 |
| nested substitutions | 10 | 64,233 to 542,433 | 2.92, 2.89 | unchanged | 2.92, 2.89 |
| nested substitutions over lines | 10 | 67,090 to 585,325 | 2.96, 2.95 | unchanged | 2.96, 2.95 |
| nested heredocs | 5 | 21,210 to 232,351 | 3.43, 3.19 | unchanged | 3.43, 3.19 |
| words piped into a shell | 25 | 50,314 to 538,789 | 3.11, 3.45 | unchanged | 3.11, 3.45 |
| a chain of wrappers that change to many directories | 10 | 33,940 to 384,640 | 3.21, 3.53 | unchanged | 3.20, 3.53 |
| many directory changes, up to the 64-state cap | 10 | 162,315 to 1,128,075 | 2.98, 2.33 | unchanged | 2.98, 2.33 |

Every other family grows at most 2.09 per doubling, before and after. The test allows 2.3 for a linear family and 4.5 for the six families still listed as unchanged and the two unclosed ones.

Three costs grew faster than the command, each from one cause:

- **Many assignments.** Each command copied every name assigned so far into the next context and compared them all. The guard reads nine names from the environment it carries: the repository variables, the homes, `PATH` and `CDPATH`. A context now keeps only those.
- **A directory spelled from variables.** `cd $x0$x1…` read every combination of the values each variable might hold. That is 3 to the power of the variables, though the guard keeps 64 directories and asks past them. With 24 variables the guard on `main` did not finish in minutes. The guard now stops reading once it has reached more than 64 directories, which asks as before. It also stops after 256 combinations. Past those it has not read every directory the `cd` may reach. It asks, as for a line that moves between too many directories, and any relative write after the `cd` is denied (`unresolved-directory`), whatever path the write names: a redirect's, a removal's, an interpreter's or a git command's. Below both caps it reads the same directories in the same order. A second family spells one directory in 2 ways per variable, so it never reaches 64 directories, and only the 256 combinations bound it.
- **Wrappers that change directory.** `sudo -D d sudo -D d …` judged the command once per option, not once per directory. The directories are now kept once each.

Two more costs changed by a constant factor and keep their growth:

- **An unclosed substitution.** Its text ended in the newline the reader adds after a command. Reading that text again added another newline, so the same script was a new text at every depth and was read again. The newline is no longer part of the text, and the work fell about 3.3 times. Its growth with depth is unchanged.
- **Finding a path's group.** The path was normalised again for every pattern of every group. It is now normalised once.

One cost was invisible to the count because it ran inside the regular-expression engine. The pattern that finds a branch switch hidden after a `git` word scanned the rest of the text from each `git`. So `echo git git …` cost the square of its length: 0.075, 0.274 and 0.983 s of CPU for 1,000, 2,000 and 4,000 words. The guard now finds the first `git` and looks for a switch after it, which takes 0.019, 0.039 and 0.073 s. The test times this one family instead of counting it, in CPU seconds at 2,000 and 8,000 words, run in turn five times with the least of each kept, and allows 8 over the two doublings. The old pattern grew 12.9 to 16.6 there, and the guard here 3.3 to 5.3. The hook also read the current branch from git at every use, and now reads it once per directory per tool call.

## The bound on work is not shipped

The plan was to set a bound only after the work was near linear. The bound would sit at least ten times above the worst ordinary command, scaled up tenfold, and would be reached well inside the time limit. It cannot be both.

The worst ordinary command in the sets of ordinary large commands is a generated script of 3,000 lines and 200 KB. It costs 25.9 million calls here, down from 35.1 million on `main`. That took 7.4 s of CPU here and 11.0 s on `main`, on a 16-core machine at a load of 15 to 21. The work grows linearly, so ten such scripts cost about 260 million calls. A bound ten times above that is 2.6 billion calls, which is minutes at the 3.2 to 5.5 million calls a second measured here. A bound reached in 5 s is about 17 million calls, which is two thirds of the unscaled script. That bound would ask on an ordinary command that `main` allows when it finishes in time.

So no cap on work ships, and the figures go to the owner for a ruling. A cap that sits below ordinary commands asks on ordinary large commands that `main` allows. A cap that sits above them is above what the guard can do inside the limit.

## Decision: the hook asks at its own deadline

**The hook gives itself 7 s of the harness's 10.** `hook.main` arms a timer before it reads the call and disarms it when it has a verdict. Every verdict the guard finds is kept the moment it is found, in each directory a wrapper or a `cd` leads to, so a deny found before the timer fires stands. When the timer fires, the hook stops judging and answers the strictest of what it has kept and an `ask`. The reason of that ask says that the guard used the 7 s it gives itself before the harness's time limit, on a busy machine or a command slow to read, and that a smaller tool call is read in time. Once the hook has its verdict, a late timer changes nothing. The hook then writes its log line as for any answer, which takes at most 1 s. Of the 10 s, 7 s go to judging and 1 s to the log line, which leaves 2 s for the interpreter to start and exit. A test reads `hooks/hooks.json` and the adapter's limit, and holds that sum under each.

With judging held for 12 s, the hook on `main` answered after 12.07 s with no output, which the harness reads as allow, so the harness would have cancelled it unjudged at 10 s. The hook here answered `ask` after 7.21 s. An independent review ran the hook as a process ten times with judging held for 12 s, at a load of 16.6 to 30.8 on 16 cores: it answered `ask` after at most 7.61 s and exited after at most 7.84 s, 2.16 s inside the limit. The same review measured a hook with nothing to judge at up to 1.02 s from start to exit.

**It answers ask, not allow and not deny.** The guard could not judge the call, so the owner decides, as for every other call the guard cannot read. A deny would refuse ordinary large commands on a busy machine, and an agent would have no way through.

**The limit stays at 10 s.** A longer limit would let more ordinary commands finish on a busy machine. It would also make every stalled hook keep the session waiting longer. Neither effect is measured well enough here to change the limit.

**`"onFailure": "block"` is still not set.** ADR 0033 left it out because the other harnesses that read `hooks.json` were not checked against the key. This change did not check them either.

## What still fails open on time

- The interpreter does not reach `hook.main` within the limit, for example because it does not start or its imports stall on a disk.
- One step runs past the limit and the timer cannot interrupt it. The timer's signal is handled between interpreter instructions, so one regular-expression match or one system call can run past it.
- The platform has no `setitimer`, as on Windows. There the hook has no deadline of its own.
- The hook has answered, but its log line stalls on a disk past the limit.

In each case the harness cancels the hook and the call runs unjudged, as before. Setting `onFailure` to `block` in `hooks.json` would close all four on Claude Code 2.1.295 and later.

## Consequences

On a busy machine, a command the guard cannot judge in 7 s is asked about. Before, it ran unjudged. That includes ordinary commands that took the guard close to 10 s, such as the generated script measured above. A command split into smaller tool calls is judged in time.

When the guard judges in time, its verdict is never looser than the verdict `main` reaches. Both trees judged 15,348 commands: 14,645 built from the repository's tests by the first review of this change, its 175 probes of spelled directories, and 528 spelled `cd`s on both sides of each cap, which a test pins. On 48 of them, a `cd` spelled from 24 variables, `main` did not finish in 10 s, and the guard here asks or denies. On the others, no verdict is looser, and those that differ are stricter, in two cases: 1,203 commands with a capped `cd`, and 4 with an interpreter or git write where the guard cannot tell the directory. A `cd` spelled from variables whose combinations number more than 256, while their directories number 64 or fewer, asks, and any relative write after it is denied (`unresolved-directory`), whatever path it names. On `main` the guard read every combination, so it allowed a write that was no guard path in any directory, and asked about a workflow or instruction file. After a `cd` the guard cannot read at all, the rule of ADR 0024 is unchanged: a relative write is denied when it would be a guard path of a repository rooted at that directory, and any other relative write passes. That rule now also reads the paths an interpreter's program or a git command writes, as it read a redirect's or a removal's.

A verdict that `main` reached in 7 to 10 s is now `ask`, unless a deny came before the deadline. When `main` took longer than 10 s, the harness cancelled it and the call ran unjudged.

## Refutation criterion

This decision is wrong in either of two cases:

- A tool call runs unjudged because the hook passed the harness's limit while `hook.main` was running. A call that ran with no answer and no guard log line shows it.
- The deadline asks on ordinary commands often enough that owners approve its asks without reading them. The guard log shows it.

## Considered options

- **A deterministic cap on the guard's work that asks past it.** Not shipped, for the figures above. It stays the right shape once the families still listed as unchanged are linear and an ordinary command costs far less than the limit allows.
- **A deadline that answers deny.** Rejected. The defect is a guard that answers too loosely when it runs out of time. A guard that refuses ordinary work on a busy machine trades that defect for one an agent cannot get past.
- **A watchdog thread that writes the ask and exits the process.** Rejected. The thread needs the interpreter lock that the stalled step holds, so it acts no earlier than the signal does. It also exits without writing the guard's log or the test coverage data.
- **A longer limit.** Not now, for the reason given under the decision.
