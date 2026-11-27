---
status: accepted
---
# UI changes carry structured visual verification evidence

Ratchet's feature pipeline previously instructed the Implement role to load the `ui_design` companion skill (`shape` and `polish`) when touching a user interface, and instructed the Critique role to run `audit` and `critique`. Two gaps left UI work unverified in practice:

1. **Visual verification was prompt-only.** `IMPLEMENT_SCHEMA` carried no structured field requiring proof that the implementer actually booted the live deliverable, captured rendered screenshots of the changed surfaces, visually inspected them against `DESIGN.md`, and fixed visual defects spotted.
2. **Critique is skipped before `life:depended-on`.** Early-stage repositories building their first screens run without Critique, so an implementer that skipped visual inspection could ship broken layout, contrast, overflow, or missing UI states with no deterministic check catching the omission.

## Decision

1. **Spec declares whether a change touches UI (`touchesUI`).** `SPEC_SCHEMA` requires `touchesUI` (`boolean`): `true` when the feature introduces or modifies any user-facing screen, page, view, component, layout, or visual state; `false` for pure backend, API, CLI, data, or non-visual work.
2. **Implement and Fix return structured `visualVerification`.** `IMPLEMENT_SCHEMA` (and `BUILD_SCHEMA`) requires `visualVerification`: `{ applicable, command, surfaces: [{ target, screenshot, observation }], defectsFixed }`.
   - When `spec.touchesUI` is `true` (or the change alters any UI), the Implement and Fix roles must boot the live deliverable (via the binding's `start` convenience entry point or preview command), capture rendered screenshots of every new or changed surface and state in temp/scratch space (never committed to git), inspect those screenshots visually against `DESIGN.md`, fix visual defects and re-capture to confirm, and record the evidence in `visualVerification`.
   - When no UI is touched, the role returns `{ applicable: false, command: "", surfaces: [], defectsFixed: [] }`.
3. **Review inspects the evidence and catches undeclared UI diffs.** `reviewPrompt` receives `spec.touchesUI` and the latest `visualVerification` record. On Axis B (Standards conformance), the Reviewer verifies that `visualVerification` covers every changed UI surface and state and files a `major` standards issue if the diff touches UI while `touchesUI` is `false` or `visualVerification` is missing or incomplete.
4. **The pipeline deterministically forces `request_changes` on missing visual verification.** Active from the first commit — before and after `life:depended-on` — `workflows/pipeline.js` (`missingVisualVerification`) forces `verdict = "request_changes"` via `verdictForcedBy` whenever `spec.touchesUI === true` (or `visualVerification.applicable === true`) and `visualVerification` is omitted, marked `applicable: false`, lacks a boot/capture `command`, lists no inspected `surfaces`, or has a surface entry missing `target`, `screenshot`, or `observation`.
5. **Exposed in pipeline output for the pull request.** The pipeline result returns `touchesUI` and `visualVerification` and instructs the orchestrator to list the visually inspected surfaces (and visual defects fixed) in the pull request description while keeping scratch screenshots uncommitted. Because `workflows/pipeline.py` executes `workflows/pipeline.js` directly, both harnesses inherit the enforcement identically.

## Refutation criterion

This is wrong if:
- a UI-touching feature with `touchesUI: true` can reach `verdict: "approve"` without a non-empty `command` and at least one fully populated inspected surface in `visualVerification`,
- a pure non-visual change (`touchesUI: false`, `visualVerification.applicable: false`) is falsely blocked by visual verification, or
- scratch screenshots required by visual verification are committed into the repository instead of kept in temporary scratch space.

## Considered options

- **Run the full Critique stage on all UI PRs before `life:depended-on`.** Rejected. Early-stage projects skip Critique intentionally to avoid heavy multi-round adversarial overhead (mutation, fuzzing, fitness sabotage, concurrency attacks) while the product shape is still forming. Enforcing structured visual verification in Implement, Fix, Review, and `forcedReasons` closes the visual feedback gap from commit #1 without adding Critique's full cost.
- **Require committed screenshot files in the repository.** Rejected. Committing binary screenshots bloats git history and creates merge noise; recorded outputs for expect tests already live under `## Recorded outputs`, while visual inspection screenshots belong in scratch/temp space and their observations belong in `visualVerification` and the PR description.
- **Keep visual verification prompt-only.** Rejected. Prompt-only instructions are routinely skipped under token or time pressure unless backed by schema fields and deterministic verdict forcing.
