---
name: audit
description: Audit a repository for full compliance with the Ratchet method, combining the deterministic scorecard with a semantic check of the binding, guards, bites and life events.
disable-model-invocation: true
---

# Audit compliance with the Ratchet method

`/ratchet:audit` inspects a repository without modifying any tracked file and reports every compliance gap: blocking (`red`) failures, debt and drift (`warn`), and semantic gaps that deterministic checks cannot judge on their own. Load the `ratchet:method` skill first.

This command is read-only. It edits nothing, commits nothing and asks no questions.

## Steps

1. **Run the deterministic scorecard.** Run this in one Bash call:

   ```bash
   python3 -E -s -B -X pycache_prefix=/nonexistent/ratchet-no-bytecode "${CLAUDE_PLUGIN_ROOT}/scripts/audit.py" --root .
   ```

   The scorecard covers thirteen deterministic areas: `conformance`, `plugin-pin`, `hooks`, `settings`, `incidents`, `triggers`, `life-events`, `formal-model`, `owed`, `opt-ins`, `budgets`, `records`, and `binding-review`.

   Done when you hold the full scorecard output.

2. **Audit what scripts cannot judge.** Read `docs/process/binding.md`, `guards/manifest.toml`, `quality/thresholds.toml`, and `quality/bites.toml` against the live repository tree and check four things:
   - **Honesty of `later:` and standing `none:` rows.** For every `later: <trigger>` row listed under `binding-review`, verify whether that kind of code or artefact already exists in the tree even if a trigger regex missed it (for example, a formal model directory while `formal` is still `later: code:formal-model`, outbound network calls, config reads, stored formats, listeners, or subprojects). For every standing `none:` row, note whether its citing ADR still holds or whether a real tool now exists in the stack.
   - **Missed lifecycle events.** Compare `- Life events:` in `docs/process/binding.md` against both the `life-events` section (fresh and previously-declined signs) and real-world clues that file globs miss: live deployment URLs, domains or hosting configs (`deployed`), persistent user/customer databases, uploads or billing data (`irreplaceable-data`), and external users or SLAs (`depended-on`). State plainly which evidence is present and which `/ratchet:safeguard <event>` command declares each if it now holds.
   - **Guard, lockfile and test-path completeness.** Check `guards/manifest.toml` to verify that every real linter, typechecker, security-scanner, test-runner and build config is listed under `tool-config`, and every real migration, infrastructure or release-path file is covered by a one-way door. Check `## Dependencies` and `## Test paths` in `docs/process/binding.md` against every package manifest, lockfile and test directory in the tree (including subprojects).
   - **Bite strength and gate substance.** Spot-check `quality/bites.toml` and `justfile` recipes for hollow gates, weak planted faults that only test a toy error instead of the row's real question, a `formal` gate that checks only an abstract model or a couple of hand-picked assertions instead of verifying real-code execution traces across all modeled actions against the unmodified model, or slow unsharded gates (`- Shards: 1` when `bite-all` is near or over its ceiling).

   Done when every `later:` row, standing `none:` row, guard group, lockfile and key bite has been checked against the real tree.

3. **Report the audit.** Present the findings in four tight sections:
   - **Verdict and deterministic scorecard:** relay the `ratchet-audit:` summary line and every `red` and `warn` section from step 1 verbatim or in a compact table (note which sections are `green`).
   - **Semantic compliance findings:** list concrete gaps found in step 2 with exact file paths and line numbers, or state explicitly that none were found.
   - **Remediation plan:** order concrete next actions from blocking (`red`) to debt (`warn`) to hardening (semantic gaps), naming the exact Ratchet command for each (`upgrade.py --apply`, `/ratchet:fill`, `/ratchet:safeguard`, `/ratchet:architecture`, `/ratchet:prune`, `just budget <gate> --set`, or a targeted branch). Note clearly that this audit ran static and recorded checks (and did not run `just bite-all` or `just check-deep`).
   - **The route:** end by running `python3 -E -s -B -X pycache_prefix=/nonexistent/ratchet-no-bytecode "${CLAUDE_PLUGIN_ROOT}/scripts/route.py" --root .` in one Bash call and printing its lines as they are.
