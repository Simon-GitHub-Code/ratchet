---
status: accepted
---
# No gate reuses an earlier green, and no gate before tier 2 rests on what fits in a time box

Settles issue #301. Read with ADR 0071, ADR 0073 and ADR 0074. Amends `skills/method/tiers.md`, the list of what holds for any tool.

ADR 0071 declined to reuse a report from one run to the next, since what a gate reads is wider than the tree (its third considered option). ADR 0073 left reuse to a later record, for a runner that can make a gate's inputs complete. ADR 0074 found no such runner and said what would be one. This record is the later record. It answers a project that built the store anyway.

**What was asked.** An adopted project on 0.13.3 built a store that lets a local tier skip a gate whose inputs match an earlier green run. The key was a digest of every file under the root by a direct walk, git's state for the gates that read it, the environment and the tools' versions. It was taken before and after each run. After four build rounds and four independent reviews, the project's owner parked it. Issue #301 asks for one of two things, decided in a record:

1. The plugin provides verdict reuse, with the key, the classes of gate and the never-store rule owned and tested once.
2. The method says a project should not build it, gives the reason, and says what to do instead where the saving was.

## Decision

**1. The plugin does not provide verdict reuse, and a project does not build its own.** A gate's green comes from a run of that gate on this tree. No gate is skipped because an earlier run on matching inputs was green. The one exception is an engine under the entry points that gives a step only its declared inputs (point 6).

**2. The reason is the project's own figures.** Measured on 0.13.3 at 4 lanes, `check` without the proof:

- On a change to product source, reuse saved nothing, and taking the key cost 4 to 8 seconds. That is the change a `check` exists to judge.
- On an unchanged tree, 97 seconds against 193. The floor was the suite, which the store could never reuse (trap 5 below).
- On a documentation-only change, 164 seconds on a loaded machine, perhaps 100 on a quiet one.
- Only 28 of about 50 gates could ever be reused. The 12 that mattered cost 394 seconds fresh, and 145 of those were a build that other gates need anyway.

The price was the one failure the method cannot accept: a green the code did not earn. Each of the four reviews found a new way to one. These are the traps, and they are the test plan for anyone who reopens this record. Each needs a test that is red on a store that falls into it.

1. A gate that reads the base or `HEAD`, keyed without them.
2. A key taken from git's index misses files marked assume-unchanged or skip-worktree, and a racily clean edit inside the same second.
3. Generated files under the build output, left out of the key, which a gate reads.
4. Two runs in one work tree. The first overwrites a report while the second reads it, and the second stores a green judged on the first's content.
5. A gate whose result changes with load. A mutation run counts a timeout as a kill, and a suite with property tests bounded by seconds tries fewer inputs on a busy machine. Such a gate must never be stored, and that removed the whole suite from the store.
6. A skip set written as a folder, when the gate reads by file extension: a linter reads a script under a documentation folder. A tracer cannot catch it, since it sees only the files that exist when it runs.
7. A change to the index or `HEAD`, made and undone during a run.
8. A digest whose fields are joined by spaces, an environment variable on the inert list that a library reads, and a link that points out of the tree.

**3. What a project does instead.** The saving was in two cases, and the method already has an answer for one of them.

- **An unchanged tree.** Do not run `check` again. A role does not run `check` again on a tree it has not changed since a green run, and the run's result counts each `check` that ran on a tree an earlier green run had covered (ADR 0073, points 4 and 5). The one who knows the tree is unchanged decides not to run. No stored verdict claims it. The default branch running again on a tree a pull request already passed is #297.
- **A documentation-only change.** The gates over the change already judge nothing and say so: mutation of changed functions, coverage of changed lines and the flag states (`tiers.md`, "A gate over the change may find nothing to judge, and says so"), and `tests-changed` (ADR 0073, point 2). On a pull request, the binding's `Affected only` line may name path filters, and the default branch still runs everything (`tiers.md`, "Run only what the change can affect", ADR 0019). The templates have no filter mechanism, so a project that adds one meets these facts:
  - A filter on the workflow's trigger starts no `ci` run. The merge judge then reads the pull request as `unnamed` and never merges it by itself.
  - So a filter belongs on a job. A skipped job concludes `success`, and the judge reads that as green. Whether it should is a question #297 leaves undecided.
  - A filter that misses is caught on the default branch after the merge, and a red run there holds merges (ADR 0069, settled items 2 and 5).
  - A path filter is a skip set, so trap 6 applies to it: write it by what the gate reads, never as a folder.

  The method has nothing that leaves a gate out of the local `check` or the commit hook for a prose-only change. A form of the commit hook that judges only the staged change is #295. A scoped form is proven by its own bite, which a stored verdict never is.

**4. A gate that has an exact, cheap list of its inputs.** Issue #301 leaves this one door open. Today the method lets a pull request skip such a gate on that list alone, as a path filter on its job, because the default branch runs every gate after the merge. It allows the skip nowhere else: the local `check` and the default branch run every gate. Whether an exact input list, or a tree a pull request already passed, settles a gate on the default branch is #297's question.

**5. No tier 0 or tier 1 gate rests on how much work fit in a number of seconds.** This covers two kinds of test. A test or search bounded by seconds tries fewer cases on a busy machine, so its green means less. A case with a time limit fails a correct case where the machine was slow. Neither is deterministic. A project bounds such a test by a count of cases, with the deterministic seeds the suite row already asks for (`tiers.md`, "Does the code do what its tests claim?"), and sets no time limit on a case. Any time-boxed run it still wants goes to tier 2. The rule goes into `tiers.md`, in the list of what holds for any tool.

Other uses of time stand:

- **A limit that only ends a hung run.** A healthy run never comes near it, and reaching it is red in words that name time: the service boot's wait for readiness, CI's job time limit, and the proof's `TIMED OUT` (ADR 0069). Where load makes such a limit fire on a healthy run, the fault is the load. In #291 the bite proof's lanes starved a project's tests that each had a time limit, which this rule also rules out.
- **A time-boxed search in tier 2.** Fuzzing and symbolic search give each target a fixed number of seconds under a cap (`tiers.md`, "A time box is per thing judged, under a cap"). Their red is a real failure on an input they found. Their green says only that this budget found nothing, which is why they are tier 2's.
- **A gate whose question is time.** The ceilings, the performance budget and the load test judge elapsed time on purpose. Each is measured alone (`tiers.md`, "A gate that judges elapsed time is measured alone"), and a ceiling blocks only on the default branch and nightly (`tiers.md`, "Measured, not hoped"). A seconds ceiling measures the runner as well as the work, which is #299.
- **The load smoke and the graceful stop.** Their numbers catch a stall, not a slow machine. The template's load smoke asks for 20 answers from 8 clients in 5 seconds and gives each request 5 seconds. The graceful stop gives the service 10 seconds to exit after the signal. A miss that happens only under the proof's lanes is #291.

Mutation counts a timed-out mutant as killed, as mutation tools do, and a timeout of a mutant the register holds is red (ADR 0018, `ratchet.md`). Whether load can turn a surviving mutant into a kill through the time limit is not decided here.

**6. An engine that gives a step only its declared inputs may answer it from its cache.** ADR 0074, point 2, lets a project bind a pipeline engine under its entry points. Where such an engine gives a step only its declared inputs, its cache is not the store this record declines, since the key covers every input the step can read. Whether an engine does that is what the spike in ADR 0074's refutation criterion is there to show: "a change to the host's environment changes nothing". A store kept beside a work tree that is not sandboxed must enumerate what a gate might read, and #301 shows it cannot. ADR 0074 point 2's limit stays: "The proof that gates can fail and a gate that runs twice to find instability are never answered from the engine's cache."

## Refutation criterion

This is wrong, and the question reopens, when any of these is seen:

- A runner meets ADR 0074's conditions, and a reuse design built on it passes a test for each of the eight traps above.
- ADR 0073's timing line (`scripts/run_time.py`) shows a project's runs spending more than a tenth of their `check` time on trees an earlier green `check` had covered.
- A project shows reuse saving time on a change to product source, measured as `pstack:benchmark-checklist` asks.

## Considered options

- **The plugin provides verdict reuse.** Issue #301, option 1. The plugin would own the key, the classes and the never-store rule, and would trace each gate's skip set. The four reviews of one project's build each found a stale green the build before had missed, and the reviewed design still saved nothing on product source. Owning it once moves those traps into every adopted project, together with the work to count and time a reused gate and to teach conformance that not every gate ran.
- **Each project builds its own, as #301's did.** The same traps once per project, each found by its own reviews or not found.
- **Reuse only for a gate with an exact list of its inputs.** This is point 4. On a pull request it is already a path filter on a job. On the default branch it is #297's question.

## Consequences

- A `check` on an unchanged tree still runs if someone starts it. ADR 0073's timing line counts that case, so the cost of this record is measured.
- A documentation-only change pays a full local `check` until a scoped form of the commit hook arrives (#295).
- The uses of time that point 5 lets stand do not change. Whether load can turn a surviving mutant into a kill stays open.
- A project's suite with a test bounded by seconds, or with a time limit per case, breaks the rule in `tiers.md`. Its fix is the project's: a count of cases, and tier 2 for any time-boxed run.
