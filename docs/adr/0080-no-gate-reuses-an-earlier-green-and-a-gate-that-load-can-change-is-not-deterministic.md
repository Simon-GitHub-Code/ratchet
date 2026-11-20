---
status: accepted
---
# No gate reuses an earlier green, and a gate that load can change is not deterministic

Settles issue #301. Read with ADR 0071, ADR 0073 and ADR 0074. Amends `skills/method/SKILL.md`, the word **gate**.

ADR 0071 declined to reuse a report from one run to the next, since what a gate reads is wider than the tree (its third considered option). ADR 0073 left reuse to a later record, for a runner that can make a gate's inputs complete. ADR 0074 found no such runner and said what would be one. This record is the later record. It answers a project that built the store anyway.

**What was asked.** An adopted project on 0.13.3 built a store that lets a local tier skip a gate whose inputs match an earlier green run. The key was a digest of every file under the root by a direct walk, git's state for the gates that read it, the environment and the tools' versions. It was taken before and after each run. After four build rounds and four independent reviews, the project's owner parked it. Issue #301 asks for one of two things, decided in a record:

1. The plugin provides verdict reuse, with the key, the classes of gate and the never-store rule owned and tested once.
2. The method says a project should not build it, gives the reason, and says what to do instead where the saving was.

## Decision

**1. The plugin does not provide verdict reuse, and a project does not build its own.** A gate's green comes from a run of that gate on this tree. No gate is skipped because an earlier run on matching inputs was green.

**2. The reason is the project's own figures.** Measured on 0.13.3 at 4 lanes, `check` without the proof:

- On a change to product source, reuse saved nothing, and taking the key cost 4 to 8 seconds. That is the change a `check` exists to judge.
- On an unchanged tree, 97 seconds against 193. The floor was the suite, which the store could never reuse (point 5 below).
- On a documentation-only change, 164 seconds on a loaded machine, perhaps 100 on a quiet one.
- Only 28 of about 50 gates could ever be reused. The 12 that mattered cost 394 seconds fresh, and 145 of those were a build that other gates need anyway.

The price was the one failure the method cannot accept: a green the code did not earn. Each of the four reviews found a new way to one. These are the traps, and they are the test plan for anyone who reopens this record. Each needs a test that is red on a store that falls into it.

1. A gate that reads the base or `HEAD`, keyed without them.
2. A key taken from git's index misses files marked assume-unchanged or skip-worktree, and a racily clean edit inside the same second.
3. Generated files under the build output, left out of the key, which a gate reads.
4. Two runs in one work tree. The first overwrites a report while the second reads it, and the second stores a green judged on the first's content.
5. A gate whose result changes with load (point 5 below). It must never be stored, and that removed the whole suite from the store.
6. A skip set written as a folder, when the gate reads by file extension: a linter reads a script under a documentation folder. A tracer cannot catch it, since it sees only the files that exist when it runs.
7. A change to the index or `HEAD`, made and undone during a run.
8. A digest whose fields are joined by spaces, an environment variable on the inert list that a library reads, and a link that points out of the tree.

**3. What a project does instead.** The saving was in two cases, and the method already has an answer for one of them.

- **An unchanged tree.** Do not run `check` again. A role does not run `check` again on a tree it has not changed since a green run, and the run's result counts each `check` that ran on a tree an earlier green run had covered (ADR 0073, points 4 and 5). The one who knows the tree is unchanged decides not to run. No stored verdict claims it. The default branch running again on a tree a pull request already passed is #297.
- **A documentation-only change.** The gates over the change already judge nothing and say so: mutation of changed functions, coverage of changed lines, the flag states and `tests-changed` (`tiers.md`, "A gate over the change may find nothing to judge, and says so"). On a pull request, the binding's `Affected only` line may name path filters, and the default branch still runs everything (`tiers.md`, "Run only what the change can affect", ADR 0019). A path filter is a skip set, so trap 6 applies to it: write it by what the gate reads, never as a folder. The method has nothing that leaves a gate out of the local `check` or the commit hook for a prose-only change. A form of the commit hook that judges only the staged change is #295. A form of a tier 2 gate scoped to the change is #310. A scoped form is proven by its own bite, which a stored verdict never is.

**4. A gate that has an exact, cheap list of its inputs.** Issue #301 leaves this one door open. Today the method lets a pull request skip such a gate on that list alone, as a path filter on the `Affected only` line, because the default branch runs every gate after the merge. It allows the skip nowhere else: the local `check` and the default branch run every gate. Whether an exact input list, or a tree a pull request already passed, settles a gate on the default branch is #297's question.

**5. A gate whose result can change with machine load is not deterministic.** The test: run it twice on the same tree, once on a quiet machine and once on a loaded one. If the verdicts can differ, the gate is not deterministic. A property test that stops after a number of seconds tries fewer inputs on a busy machine. So a unit suite with one such test is not a deterministic gate, and neither is a test with a time limit per case, which fails where the machine was slow. The sentence goes into the method's definition of a gate. A project with a time-boxed test does two things:

- It bounds the test by a count of cases, with the fixed seed the suite row already asks for (`tiers.md`, "Does the code do what its tests claim?"), and sets no time limit on a case.
- It moves the time-boxed run, if it still wants one, to tier 2. That tier is allowed to be slow, and its time boxes are per thing judged under a cap (`tiers.md`, "A time box is per thing judged, under a cap"). A red there replays from the seed or input it prints.

**6. A hermetic engine stays as ADR 0074 left it.** ADR 0074, point 2, lets a project bind a pipeline engine under its entry points, and such an engine caches a step by the content of what it is given. That is not the store this record declines. The engine gives a step nothing else, so the key covers every input. A store kept beside a work tree that is not sandboxed must enumerate what a gate might read, and #301 shows it cannot. The engine's limits under ADR 0074 stay the same: the proof and a repeated run are never answered from its cache. Under point 5, neither is a gate that load can change.

## Refutation criterion

This is wrong, and the question reopens, when any of these is seen:

- A runner meets ADR 0074's conditions, and a reuse design built on it passes a test for each of the eight traps above.
- After ADR 0073's timing line and #295 are built, a project's runs still spend more than a tenth of their `check` time re-proving trees an earlier green run covered, with no product source changed.
- A project shows reuse saving time on a change to product source, measured as `pstack:benchmark-checklist` asks.

## Considered options

- **The plugin provides verdict reuse.** Issue #301, option 1. The plugin would own the key, the classes and the never-store rule, and would trace each gate's skip set. The four reviews of one project's build each found a stale green the build before had missed, and the reviewed design still saved nothing on product source. Owning it once moves those traps into every adopted project, together with the work to count and time a reused gate and to teach conformance that not every gate ran.
- **Each project builds its own, as #301's did.** The same traps once per project, each found by its own reviews or not found.
- **Reuse only for a gate with an exact list of its inputs.** This is point 4. On a pull request it is already a path filter. On the default branch it is #297's question.

## Consequences

- A `check` on an unchanged tree still runs if someone starts it. ADR 0073's timing line counts that case, so the cost of this record is measured.
- A documentation-only change pays a full local `check` until the scoped forms arrive (#295, #310).
- Some gates the method already has judge elapsed time or bound a wait. This record does not change them. Each is judged against point 5 in its own issue.
- A project's suite with a property test bounded by seconds is no longer a deterministic gate. Its fix is the project's: a count of cases, and tier 2 for any time-boxed run.
