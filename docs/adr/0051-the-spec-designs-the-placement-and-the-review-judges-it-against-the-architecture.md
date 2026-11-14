---
status: accepted
---
# The spec designs the placement and the review judges it against the architecture

Builds on ADR 0034 (only product questions reach the owner) and ADR 0036 (the spec is checked against the ticket).

The method holds that a project's macro and micro architecture is right and enforced deterministically. The pipeline did not use a design skill anywhere. The spec role placed the feature "as far as placing the feature needs", the Placement entry of `spec.md` named a layer and a reason with nothing to hold the reason to, and the review had axes A to E (spec, standards, guard paths, ticket, inputs) with none that asked whether the change fits the architecture. A layer violation was a blocker only when a tool caught it. A change could place logic in the wrong module, bypass a seam or add a shallow wrapper, pass every axis and every gate, and the owner, who does not read the code, would have no sign of it.

## Decision

**The spec role designs the placement.** It loads `mattpocock-skills:codebase-design` and reads the architecture ADR in `docs/adr/` (the one that records the project's macro and micro architecture) and the layers `AGENTS.md` declares. `spec.md`'s Placement entry becomes a Design section of three short parts: placement, with each piece's layer or module and a reason tied to the architecture ADR and the declared layers by name; seams, each one the feature adds or changes, its interface, and why the module behind it is deep and not a shallow wrapper; and what was considered and rejected, with the reason. The spec role's other duties are unchanged.

**A change that implies an architecture change is the owner's.** A new layer, a new kind of storage or a new process boundary is a `direction` open question under the fork test (ADR 0034, ADR 0036): the product the user gets, and what it costs to change later, differ with the choice. It carries the options (the one that keeps the architecture, when the feature can be built within it, among them) and a recommendation first. The spec role places everything the question does not touch inside the current architecture. It raises no other design question: a placement within the declared architecture is its own to decide.

**The review gains axis F, the design.** It judges the change against the architecture ADR, the declared layers and the spec's Design section. A layer violation is a blocker, as the severity list already says. A dependency direction the architecture ADR rules out, and an architecture decision the change makes that the spec did not raise with the owner, are major. A module that reaches around a declared seam is a violation of that seam, so a blocker when the seam is a declared layer boundary and major otherwise. A shallow wrapper, logic that fits its layer but sits in a worse module, and a placement the Design section does not explain are minor. Where the Design section says one thing and the code does another, the issue is major. Issues carry the axis `design`. The review's structured result is otherwise unchanged, and the verdict is still re-derived from the issues.

## What is unchanged

The result schemas keep their fields; the issue's `axis` enum gains `design`. The size stops (ADR 0045), the ticket check, the critic and the fix loop are untouched, and no agent is added to a run: the design pass is part of the spec role and of the review.

## Dependency

The architecture ADR and the layers' declaration in the binding are created at adoption by separate work. Until it lands, the prompts name them by what they are, and a project that records no architecture is judged on its declared layers and the spec's own Design section.

## Refutation criterion

This is wrong if, over the next ten tickets, a change merges whose placement or seams the owner later has to have redone while the spec's Design section and axis F both passed it: then the review shares the spec role's blind spot and the design needs a role that does not read the spec's reasons. It is also wrong if the owner is asked an architecture question for a change that fit the existing architecture, in more than one ticket in ten.
