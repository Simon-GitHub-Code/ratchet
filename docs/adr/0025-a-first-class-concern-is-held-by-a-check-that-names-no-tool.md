---
status: accepted
---
# A first-class concern is held by a check, and the check names no tool

ADR 0012 made telemetry, feature flags, security testing and load testing first-class, and ADR 0017 said the method ships questions and never a tool. A read of the plugin before its first public release found both claims weaker than they read. A repository could follow every instruction and still ship new behaviour with no flag, run a load smoke that requested only the health route, and keep a telemetry gate whose one planted fault had nothing to do with the instrumented seam, with every gate green. And four tools were required in effect: the mutation gate read two tools' output and nothing else, the coverage gate read one report format, the conformance gate wanted one hook framework's file, and the gates that the method says run weekly had no schedule.

We decided to close each of these with a check a script runs, and to make each check ask for the concept.

1. **New surface ships behind a flag, or the owner sees it.** The flags gate judges the registry, so a change that added a route with no flag met no gate. The merge judge already reads the files that record the surface: the paths behind a door whose `on` is `"removal"`, which are the contract artefacts and public-surface snapshots. When a change adds to one of them and only adds, the judge now asks for a flag. The change passes when `flags.toml` gains a flag that is off, or when a line the change adds names a flag the registry holds off. The lines read are the added ones outside `docs/`, the registry and the surface files themselves, so a spec that names the flag proves nothing and a route that shares a flag's name does not hide itself. Otherwise the judge prints `unflagged` and the owner merges. The second way to pass exists because a feature built over several changes sits behind one flag: without it every change after the first would reach the owner. The judge asks only the application types that `application-types.toml` asks `changed flags off and on` of, read from the binding at the default branch's tip, so a library is exempt; a binding that names no type, or one the catalogue lacks, is asked.

Decided 2026-11-12.

## What these checks do not see

`unflagged` shows that a flag was declared or named beside new surface. It does not show that the surface sits behind the flag: a change can declare a flag nothing reads, which the flags gate catches only where the binding passes `--used-in`. Three false positives are accepted, and each costs the owner one read. A refactoring that exports a helper adds a line to a public-surface snapshot and reaches the owner. So does surface with nothing to turn off, such as a new field on a response that old consumers ignore. So does a change that adds a route behind an existing flag when the code naming the flag was merged by an earlier change. One false negative is known: surface no snapshot records, such as a screen in a user interface with no contract artefact, is not seen, and the review role is still told to look.

## Refutation criterion

This is wrong if the checks nag or miss. Read the first twenty changes the judge marks `unflagged`: if the owner merges more than half of them unread, the rule is too wide, and the first thing to drop is whichever false positive above accounts for most of them. If a change ships behaviour a user meets with no flag, and its contract or surface snapshot gained a line, the rule has a hole that becomes a test in `hooks/tests/test_merge_ready.py`.

## Considered options

- A gate that fails the pull request when new surface has no flag: rejected, some new surface has nothing to turn off, and a red gate has no way to say "the owner decides". The merge judge does.
- Trust the spec's "Feature flag: none" line: rejected, the spec is an agent's word, which is what ADR 0021 refuses to read.
- Require the new flag in the same change every time: rejected, the second change of a feature behind one flag would always reach the owner.
- Ask every type, libraries included: rejected, a library has no runtime to read a flag in, and the catalogue already says so.
