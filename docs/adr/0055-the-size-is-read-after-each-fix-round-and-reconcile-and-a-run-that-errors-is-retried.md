---
status: accepted
---
# The size is read after each fix round and reconcile, and a run that errors is retried

Amends ADR 0023 (a pipeline run that errors) and ADR 0045 (where the change-size ceilings are read).

A model of `workflows/pipeline.js` found two paths that ended with the ticket parked for the owner where the drain has an answer.

**Size.** The script read the change-size gate's numbers after the spec and after the implementation only. A fix round (run once the project is `depended-on`) or a rejudge's Reconcile can take the product code over its ceiling; the run went on to a red `check` and a refused review, and the ticket was parked as an ordinary refusal, never reaching the split ADR 0046 gives a ticket that stops as too large.

**Errors.** An agent that dies ends a run in a thrown error and no result (ADR 0023 keeps that, so the run can be resumed). The drain skill gave no step for it, so a drain met a ticket with nothing to settle.

## Decision

**The same size read runs after each fix round and after Reconcile.** The fix agent and the reconcile agent return `changeSize`, as the implementer does. A product count over its ceiling stops the run `blocked` as too large (`tooLarge.stage` is `fix round N` or `the reconcile`), with the measured numbers, and the drain splits the ticket as ADR 0046 says. Test lines stop it only over the whole test ceiling: the critic's tests are already counted by then, so the two-thirds margin of ADR 0040 does not apply. The stop returns no re-critique and no review.

**A run that errors is resumed once, then closed for a retry.** If the resumed run errors too, the drain closes the ticket with `--outcome waits` and a reason that starts `errored:`. `errored` is one of `queue_notes.MECHANICAL`, so the bounded retry of ADR 0023 applies: the ticket, which has no commits, is built again from the start by the next drain, and after `RETRIES` closes in a row (three) it is held for the owner with a reason that says the run kept erroring.

Decided 2026-11-14.

## Refutation criterion

This is wrong if a fix round or a reconcile that ends under the ceilings is stopped as too large: the numbers it prints are the gate's own, so each stop is one the gate would have turned red. It is wrong if a ticket whose run errored is built again more than three times without an owner hold, or held after one error; each is a case in `hooks/tests/test_drain.py`.

## Considered options

- Stop only when `check` is red on size: rejected, the run has no way to tell a red size gate from another red gate and would not split.
- Count a re-critique's tests too: rejected, the critic's share before the fix is already in the measured test lines, and the product ceiling is the stop that matters here.
- Hold an errored ticket for the owner at once: rejected, a dead agent is more often a transient failure than a fact about the ticket.
