---
status: accepted
---
# Cutting another unit's steering page is the owner's merge

Settles issue #272 (option (b)). Builds on ADR 0064 ([`0064-a-document-has-a-kind-and-a-steering-document-leaves-with-its-feature.md`](0064-a-document-has-a-kind-and-a-steering-document-leaves-with-its-feature.md)) and ADR 0065 ([`0065-owed-comments-and-stale-pages-are-pruned-by-an-agent-and-judged-at-the-merge.md`](0065-owed-comments-and-stale-pages-are-pruned-by-an-agent-and-judged-at-the-merge.md)).

**What was asked.** Steering documents carry the plans, criteria, and answers that guide an agent's work (`local feature` files under `.scratch/<unit>/` and `feature spec` files under `docs/features/<slug>/spec.md`, per ADR 0064). Before this decision, the merge judge prevented a feature branch from editing a labelled ticket or the spec a labelled ticket inherits from (`agreement`), and prevented `/ratchet:prune` from cutting pages outside `pass` kinds (ADR 0065). However, an agent branch building one unit could still remove lines from or delete another unit's unlabelled ticket, unsplit local spec, or `docs/features/<other>/spec.md` without triggering an owner merge.

## Decision

**1. Steering files and units.** A path is a steering file when `documents.is_steering(path)` holds:

- `local feature`: `.scratch/<unit>/spec.md` and `.scratch/<unit>/issues/*.md`
- `feature spec`: `docs/features/<slug>/spec.md`

Each steering file belongs to the unit returned by `documents.unit_of(path)` (`.scratch/<unit>` or `docs/features/<slug>`).

**2. This change's own unit(s).** Determined deterministically from the merge base to head diff alone (`own_units`):

- `docs/features/<slug>` and `.scratch/<slug>` when the change keeps `docs/features/<slug>/verdict.json` (`verdict_slug`).
- `documents.unit_of(found.ticket.ref)` when that verdict record's slug uniquely matches a labelled local ticket (`ticket_of`).
- `documents.unit_of(path)` for any local ticket `.scratch/<unit>/issues/*.md` whose `Status:` moves from the agent-ready label to `resolved` (`status_resolved`).

**3. Cutting another unit's steering file (`steering-cut`) is the owner's merge.** For any steering file that existed at the merge base (`change.held(path)`) and belongs to a unit not in `own_units`:

- Deleting the file (`not change.kept(path)`) or removing any line (`-` hunk line in `git diff -U0 --no-renames`, excluding the `---` file header) triggers `merge: owner` with reason `steering-cut` naming each affected steering file.
- Pure additions (adding `+` lines with zero removed `-` lines) stay agent-mergeable.
- In a records-only change, link-only normalizations (`link_only` rewriting `Parent:` or `Blocked by:` to the same target file) and drafting/redrafting a sole unsplit local spec whose unit has no tickets at merge base or head stay agent-mergeable. Partial line deletions on any other steering file in a records-only change still require the owner's merge (`steering-cut`, or the specific `record:` refusal where `records_alone` already rejects the path).

**4. Exemption for `documents.py expire` (ADR 0064).** Whole-file deletion (`not change.kept(path)`) of every steering file of an expired unit (`path in expiring(change)` per ADR 0064) is exempt from `steering-cut`, so records-only and build-time `documents.py expire` cleanups stay `merge: agent`. Partial line removal inside another unit's steering file is never exempted by `expire`. `ratchet-lowered` (`scripts/lowered.py::dropped_answers`) and `carried_words` in `scripts/merge_ready.py` continue to verify that all `## Answers` lines of deleted steering files are preserved in `docs/design-brief.md` under `## In the owner's words`.

## Refutation criterion

This decision is wrong, and must be revised, if any of these is observed:

- An agent branch deletes or removes lines from an existing steering file belonging to another unit (outside whole-unit expiry deletion) and `scripts/merge_ready.py` returns `merge: agent`.
- A records-only or build-time `documents.py expire` cleanup that deletes a whole expired unit and carries its `## Answers` to `docs/design-brief.md` is rejected with `steering-cut`.
- A branch editing or resolving its own unit's steering file, or making pure additions to another unit's steering file, is rejected with `steering-cut`.
- Ordinary design or documentation work on `never`-kind pages outside guard paths (such as `PRODUCT.md` or `DESIGN.md`) is sent to the owner by `steering-cut`.

## Considered options

- **Option (c): require the owner's merge when cutting lines from `never`-kind standing pages outside guard paths (`PRODUCT.md`, `DESIGN.md`).** Rejected. Ordinary design and documentation work rewrites those pages frequently; sending them to the owner on every line edit would slow normal design iterations without protecting steering state (`/ratchet:prune` already forbids cutting `never`-kind pages).
- **Block all edits (including pure additions) to another unit's steering files.** Rejected. Appending notes, cross-references, or `## Answers` without deleting existing lines preserves everything already written.
- **Exempt all steering deletions or edits during a `records-only` change.** Rejected. Leaving `records-only` unchecked would allow partial line cuts or deleting an unexpired unit's steering files without owner review.

## Consequences

- `scripts/merge_ready.py` adds `own_units`, `removed_lines`, `steering_cut`, and `steering_reasons`, emitting `steering-cut` when an existing steering file outside the change's own units loses lines outside whole-unit expiry.
- `skills/method/guards.md` and `docs/gates.md` document `steering-cut` and the `documents.py expire` exemption.
