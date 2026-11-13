---
status: accepted
---
# The review reads the ticket the spec was written from

The pipeline writes a spec from the ticket, builds to the spec, and the review judged the build against the spec. Before this decision no role after the spec read the ticket. A spec that dropped or bent a promise of the ticket was built to the letter, every check passed, and the owner got something he had not asked for. An audit of the method's promise, that the owner need not review the code, named this its weakest point.

A prototype on 2026-11-13 checked the spec of the dogfood's ticket 01 (a read-later list) against the ticket, with one agent that read only the two. It took 1.9 minutes and 57k tokens. It found that one ticket promise, "no client JavaScript", had no criterion: the spec said it only in its Problem and Out of scope sections. It found that an assumption in the spec, "ticket 01 saves whatever is pasted", decided two real defects: a blank link and a `javascript:` link were both saved. It did not find what the ticket never mentioned, cross-site posts and input length.

## Decision

**The review reads the ticket.** The Review role reads the ticket as the Spec role does, from the same pointer (`ticketHint()` in `workflows/pipeline.js`): the tracker issue with its comments, or the local ticket file, with its `## Answers` section. The script cannot read a file, so the text is not threaded through the run. Each agent reads it from where the owner agreed it, and a second review after a rebase reads it the same way. The implementer and the critic still never see the ticket.

**Its result accounts for every promise and every input.** The review's schema requires `ticketPromises`, with at least one entry: each promise of the ticket in its own words, read as `covered` (with the criteria that carry it), `dropped`, `bent` (with what the spec says instead) or `assumed` (with what the owner would see). Each reading except `covered` quotes the spec line it rests on. It also requires `acceptedInputs`: for each thing the change lets users or other systems send the running product, the criterion that decides an empty, an oversized and a hostile value, or null. A review that skips either axis does not fit the schema, and the Workflow runtime validates each structured result against its schema and has the agent retry on a mismatch.

**The script decides what blocks.** It files each `dropped` or `bent` promise and each null input case as a major review issue. These issues take the path every other issue takes: they force `request_changes`, the verdict record lists them as unresolved, and the merge judge refuses the change. The judge is unchanged. An `assumed` promise is no issue. It joins the spec's `assumptions`, which the pull request lists for the owner to overrule after the merge (ADR 0013). An assumption that contradicts the ticket is `bent`.

**No agent is added.** The check is two more axes of the one review, so a run spawns as many agents as before, and the Spec role is told that the review checks both.

## Refutation criterion

This is wrong if a change the pipeline merges after this decision lacks a promise its ticket made, and the review's `ticketPromises` called that promise `covered`. Then the reviewer is anchored on the spec it also reads, and the check needs its own agent that sees only the ticket and the spec, as the prototype did. It is also wrong if the owner overrules more than one in three of the `dropped` or `bent` findings over the next ten tickets. Then the axis blocks on readings, not on promises, and it must ask less.

## Considered options

- **A separate agent that reads only the ticket and the spec, before Implement.** Rejected for now. It would catch a dropped promise before any code was written, but it costs a whole agent on every ticket, and the review already reads the spec in full. The refutation criterion above says when to revisit it.
- **Thread the ticket text through the run.** Rejected. The script cannot read the tracker, and only the preflight agent or the spec agent could relay the text. An agent's copy would be one more thing the reviewer must trust. The second review after a rebase has the same ticket pointer, since the skill passes `ref` again.
- **Ask the reviewer in the prompt alone.** Rejected. A reviewer could skip the axis and still return a valid result. The schema makes the skipped axis an invalid result.
- **Have the reviewer file the findings in `issues` itself.** Rejected. The reviewer could read a promise as dropped and file nothing. The script derives the issues from the readings, so the reading alone decides.
