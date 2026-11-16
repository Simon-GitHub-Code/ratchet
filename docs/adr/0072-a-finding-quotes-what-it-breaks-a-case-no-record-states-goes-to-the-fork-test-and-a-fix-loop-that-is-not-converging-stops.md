---
status: accepted
---
# A finding quotes what it breaks, a case no record states goes to the fork test, and a fix loop that is not converging stops

Amends ADR 0056 (the rounds a run gets) and `skills/method/pipeline.md`, step 2's "After that the run decides, and the script asks the owner nothing". With ADR 0073 it answers one run that took three and a half hours on a small ticket and ended `request_changes`.

**Today** a run has one place to ask the owner: the spec role's open questions, before anything is built. After that every role decides alone, and nothing records what it decided. In the run that prompted this record:

- The first fix round answered a finding by changing a behaviour spec.md lists as assumed. Its notes said so ("I did not edit the spec"). No field carries that, so the next critic judged the change against the old assumption.
- A critic measured a case the ticket and spec.md say nothing about, and left it out of its findings. The critic of the next round reported the same case as a major, with no round left to fix it.
- The repository's own tests already repeat their checks over that case for the screen the change edited. spec.md did not say whether the new behaviour holds across it.
- The implementer's result asks for "anything the spec left open and how you resolved it", as prose.

The run used both fix rounds and the review, and returned a rejection.

Any answer must keep three things:

- **The owner is asked about direction, never to approve a change.** A finding that names what it breaks is fixed without a question.
- **The fork test decides whose a case is.** A case the test gives the role is assumed and listed; only a case it gives the owner is asked.
- **The script decides from fields.** No rule reads a role's prose to find a question in it.

## Decision

**1. A blocker or a major quotes what it breaks.** A critic's finding of those severities carries `breaks`: the record it breaks and the promise, criterion, non-negotiable or rule, quoted. The record is one the repository holds: the ticket, spec.md, AGENTS.md, the binding, a decision record, the glossary, the design brief or the guard manifest. A finding is owed a fix only when its quote appears, word for word, in the record it names. The review's blocker and major issues carry `breaks` too, on every path, and an issue the script raises itself takes its quote from the promise it reads.

**2. The quote is checked twice.** The role that returns a finding runs the plugin's quote check over its own findings and returns what it printed. The publish check runs the same check again over the run's result, where no role stands between the check and its answer: the skill writes the result to a file beside the start's report file before anything is published, the publish check reads that file, and it refuses when the file is absent. In a drain the guard refuses a commit until the final check has passed, so a script holds the orchestrator to this. A one-ticket run outside a drain has no such gate today: there the skill's page alone holds it, as it holds every other step of that run (issue #229). A run is not approved while its result holds a finding the run treated as owed whose quote the record does not hold.

**3. A case no record states goes to the fork test, and it is never dropped.** A critic, a fixer, an implementer and a reviewer each return `unstated`: every case they saw or measured where no record of point 1 says which behaviour is right. The role that returns an entry applies the fork test to it and says whose it is. Each entry has the shape a question has today: the case, what the code does now, the evidence, the options, and the answer the role would give with its reason.

- A case the test gives the owner is a question. The run returns `blocked` after the role that returned it.
- A case the test gives the role becomes an `assumptions` entry with the role's answer. The pull request already lists those for the owner to overrule.

A blocker or major whose quote the check did not find is moved to `unstated` by the script, from the check's output the role returned. The review applies the fork test to it, as to every `unstated` entry it reads. No role leaves a measured case out because it judged it unlikely.

**4. A departure from a criterion stops the run.** The implementer, the fixer, and a judge-again's Reconcile return `departures`: each criterion or assumption of spec.md the change no longer holds, quoted, with what the code does instead and why. The implementer's `notes` no longer asks how it resolved what the spec left open.

- A departure from a criterion ends the run, `blocked`, after the role that returned it.
- A departure from an assumption goes to the fork test. Where the test gives the choice to the role, the departure is the amended assumption, and the next critic and the review judge against it.

**5. A finding that survives the fix round it was given ends the loop.** The script numbers a critique's findings, and a re-critique says which earlier finding each of its own carries. When a finding the round was owed, by the same rule that made it owed, is carried and still owed after it, the script does not start another round, and the ticket parks with its findings as a refused review does today (ADR 0056). That is not a question. A new blocker or major that the re-critique finds still gets the second round (the owner's decision of 2026-11-16).

**6. A blocked run is built again from the ticket.** The questions of points 3 and 4 go to the owner in one message, each with the role's recommended answer first, as the spec role's questions do today. The answers go on the ticket. The run's work is kept as the stash a run that is not approved leaves today, and the ticket is built again from the preflight, so spec.md holds the answer from its first line.

**7. The spec role lists what the neighbouring tests vary.** A neighbouring test is one that drives a screen, command, endpoint or function the change edits, by name or by its address. For each set of values such a test repeats the same checks over (a list of sizes, locales, roles, screen shapes, classes of input), spec.md writes one line under `## Neighbouring cases`: the test, the values, and which of them the new behaviour holds for. The default is every value the neighbouring test uses. A narrower set is an assumption, with its reason. It is a question only when the fork test gives it to the owner. The ticket check reads each line as it reads an input case. A critic that finds a value set a neighbouring test repeats and spec.md does not list reports it as a finding against the spec.

## What this would have changed in that run

Points 3, 4 and 7 answer it. Point 7 makes the case the last critic raised a criterion before any code exists. Point 4 records the first fix round's change as an amended assumption, so the next critic judges the code and not the departure. Point 3 keeps the case the second critic measured and left out. Point 5 would not have fired: every finding that run carried was resolved in its round.

## Refutation criterion

This is wrong if any of these is seen:

- A run starts a second fix round on a finding the first round was given and did not resolve.
- A role's result holds a departure from a criterion, or an `unstated` case the fork test gives the owner, and the run's result holds no question for it.
- A run is approved whose result holds a blocker or major treated as owed, with a quote the record it names does not hold.
- The owner is asked a question that a record of point 1 already answers, or that the fork test gives to the role.
- Runs block so often on points 3 and 4 that building again from the ticket costs more than the rounds it saves. That is the sign to build the resume below.

## Considered options

- **Every case no record states is a question for the owner.** The first draft of this record. It sends the owner the cases the fork test exists to keep from them: both of the run's cases were layout, which the test gives to the role.
- **Any new blocker after a fix round ends the loop.** The owner declined it: a critic that finds a second, unrelated defect would park a ticket one more round could finish.
- **More rounds.** The run that prompted this used both and did not converge.
- **A role marks a finding "a design question" in its own words.** The script would have to trust a judgement with no evidence in it. Point 1 asks for a quote a check can find, and the absence of one is the signal.
- **An agent whose only job is the quote check, after each critique and review.** It adds an agent to every round to relay what a role can run itself, and the publish check already runs with no role in between.
- **A blocked run resumes at the role that stopped.** The run's state is the results of its agents, the script is a pure function of its arguments and those results, and by the time the owner answers the work is a stash and often another session. Resuming means a new argument that carries the stopped run's state, the skill applying the stash and recording a new start, and a new mode in the count of agents. It is worth that only if runs block often, which the last refutation line measures.
- **A wall-clock ceiling on a run.** The owner declined it: it cuts off a long build that is converging. ADR 0073 reports the time instead.

## For the builders

### What changes where

- `workflows/pipeline.js`: `breaks`, `unstated` with `whose`, and `departures` in the schemas of the critic, the reviewer, the implementer, the fixer and the reconciler; the quote check's output beside `checkOutput`; `owed` reads `breaks`; the script's own ticket issues fill `breaks` from the promise; findings numbered and `carries` on a re-critique (today a carried finding is matched by its title, which is prose); the stops of points 3 and 4 beside the block before Implement, returning `filesChanged`, the findings and the tests kept with the questions; the amended assumptions handed to the next critic and the review. A script cannot read a file, so it never checks a quote itself.
- `scripts/`: the quote check, a script that reads findings and the records they name and prints which quotes it found; `scripts/publish_check.py` runs it over the result, which the skill writes beside the start's report file, as `<report>.result.json`; today the orchestrator holds the result in memory only.
- The role prompts: the fork test, stated once and used by every role that returns an `unstated` entry or a departure; the critic's rule that a measured case is a finding or an `unstated` entry; point 7 in the spec prompt, the ticket check's and the critic's.
- `hooks/tests/pipeline_model.py` and its world: the answers, the state (`carried`, `asked`, `assumed`), the two new stops and the walk rules the builder's read lists; the stand-ins gain ids and record texts so the quote test is played.
- `skills/method/pipeline.md`, `skills/feature/SKILL.md`, `skills/method/questions.toml`: the list the skills' owner wrote for this record.

### Consequences

- A value set the neighbouring tests repeat is a criterion before any code exists.
- A role's choice the fork test allows is on the pull request as an assumption, where it used to be a sentence in a role's notes.
- A ticket that leaves open what becomes of something of the user's blocks with a question, where it used to end in a rejection, and is built again once answered.
- A critic's finding costs one more field and one run of a script. A finding that cannot quote what it breaks is no longer fixed on the critic's authority.

## Settled by the owner, 2026-11-16

- A new blocker after a fix round keeps the second round. The loop stops early only on points 3 to 5.
- A run has no time limit. Its time is reported (ADR 0073).
