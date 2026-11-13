---
status: accepted
---
# The spec is checked against the ticket before the build

The pipeline writes a spec from the ticket, builds to the spec, and the review judged the build against the spec. Before this decision no role after the spec read the ticket. A spec that dropped or bent a promise of the ticket was built to the letter, every check passed, and the owner got something he had not asked for. An audit of the method's promise, that the owner need not review the code, named this its weakest point.

A prototype on 2026-11-13 checked the spec of a test project's first ticket (a read-later list) against the ticket, with one agent that read only the two. It took 1.9 minutes and 57k tokens. It found that one ticket promise, "no client JavaScript", had no criterion: the spec said it only in its Problem and Out of scope sections. It found that an assumption in the spec, "ticket 01 saves whatever is pasted", decided two real defects: a blank link and a `javascript:` link were both saved. It did not find what the ticket never mentioned, cross-site posts and input length.

A first version put the check in the review alone. An independent verification traced what happened when it found something: nothing repaired it. The review filed the issue and the script forced `request_changes`. With the critic off, Critique and Fix did not run, and with it on, the fix loop fixes only the critic's findings. The owner got a stash and an issue title, and his only move was a rerun whose spec role never saw the finding. On the spec that test project's spec role really wrote, every ticket that accepts input would have stopped this way. One of three readings of a faithful spec took a sentence about what a gate charges for a product promise the spec bent. A check that stops the owner's work on a reading he cannot judge gets in his way.

## Decision

**A ticket check runs after Spec and before Implement.** One agent reads the ticket and `spec.md`, and no code, no tests and no other record. It returns two axes. `ticketPromises` gives each promise of the ticket in the ticket's words, read as `covered` (with the criteria that carry it), `dropped`, `bent` (with what the spec says instead) or `assumed` (with what the owner would see). Each reading except `covered` quotes the spec line it rests on. `acceptedInputs` gives, for each thing the change lets users or other systems send the running product, the criterion that decides an empty, an oversized and a hostile value, or null. A sentence about how the method treats the work, such as what a gate charges, is no promise.

**A gap goes back to the spec role once.** A promise read `dropped` or `bent`, or an input case no criterion decides, goes to the spec role with the findings in hand. It amends `spec.md` and its tests, or leaves a finding it reads otherwise, and the check reads the amended spec once more. No code exists yet, so nothing is thrown away.

**What is still open goes to the owner before the build.** A promise still `dropped` and a case still undecided become open questions in plain words, and take the path every open question takes. The run stops before Implement, the owner answers, the answer goes on the ticket under `## Answers`, and the next run reads it there. For a dropped promise the question reads: "The ticket says: "…". The plan leaves this out (…). Should the app be built without it?" A promise still read as `bent` is a reading the check and the spec role do not share. It joins the assumptions the pull request lists, and holds nothing.

**The review stays the backstop, with a narrower job.** The review reads the ticket and receives the readings the check settled, and does not argue them again. It judges whether the change, as built, still keeps each promise and decides each input case as `spec.md` states. A `dropped` promise or a null case there means the build lost what the spec had. The script files it as a major issue, the verdict record lists it as unresolved, and the merge judge refuses. A `bent` reading at the review joins the assumptions and holds no merge. The result's `ticketFindings` carry what was lost. The `/ratchet:feature` skill builds the ticket again once with them in `args.ticketFindings`, so the next spec role starts from them.

**The ticket is read as the owner agreed it.** The spec role, the check and the review read a local ticket file as the base branch holds it (`git show <base>:<path>`), not the copy in the working tree that an agent of the run could have edited. A tracker issue is read with `gh issue view`. An agent can still edit an issue's body or add a comment under the owner's token, and nothing records what the owner agreed. A hash of the body and comments recorded at the preflight would; it is parked.

**What the schema holds, and what it cannot.** The check and the review share one definition of the two axes. The schema makes each role answer both and gives each reading the evidence that shows it. It cannot make an answer complete: a list that leaves a promise out fits it, and so does an empty `acceptedInputs`. Completeness rests on the prompt and on two independent reads.

**What it costs.** The check is one more agent on every run, and two more when it finds a gap. A run spawns at most seven agents before the project is `depended-on` (preflight, spec, check, amend, second check, implement, review) and at most twelve after, where it spawned four and nine. A run whose check finds nothing spawns five and six. `AGENTS_AT_MOST` in `workflows/pipeline.js` declares the numbers, `scripts/drain.py` reads them, and a test counts a worst-case run against them. This changes the number ADR 0023 states.

## Refutation criterion

This is wrong if the owner overrules more than one in three of the questions and `bent` assumptions the check raises over the next ten tickets. Then the check reports readings, not promises, and must ask less. It is also wrong if a change merged after this decision lacks a promise its ticket made while both the check and the review called it `covered`. Then two reads of the same spec share its blind spot, and the promise needs a role that runs the built product, after Implement.

## Considered options

- **Check only in the review.** The first version. Rejected by the trace above: the review comes after the code, and nothing between the review and the merge repairs a spec.
- **Thread the ticket text through the run.** Rejected. The script cannot read the tracker, and only an agent could relay the text. An agent's copy would be one more thing the reader must trust.
- **Ask in the prompt alone.** Rejected. A role could skip an axis and still return a valid result. The schema makes each axis required.
- **Have the reviewer file the findings in `issues` itself.** Rejected. The reviewer could read a promise as dropped and file nothing. The script derives the issues from the readings, so the reading alone decides.
- **Stop on a `bent` reading after the amend.** Rejected. One faithful spec in three drew a false `bent`, and the spec role, with the finding in hand, keeps its reading. The owner sees it as an assumption and counters it if it is wrong.
- **Skip the check when the ticket promises nothing the product shows.** Rejected. Every ticket promises something, and the check reads two documents and no code.
