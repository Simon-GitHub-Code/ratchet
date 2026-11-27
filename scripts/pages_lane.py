"""Fail-closed pages lane classifier for pure documentation changes (ADR 0103)."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import change_size
import gatelib
import markdown_text
import pins
import project_bite
import triggers

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hooks"))
from ratchet_guards import dependencies
from ratchet_guards import manifest as guard_manifest

if TYPE_CHECKING:
    from collections.abc import Sequence

MANIFEST_PATH = Path("guards/manifest.toml")
SPEED_SECTION = "## Speed"
FORMAL_SECTION = "## Formal verification"
PAGES_LANE_BULLET = "Pages lane"
PAGES_EXCLUDE_BULLET = "Pages exclude"
PAGE_DOCS = (".md", ".markdown")
VERDICT_RECORDS = ("docs/features/**/verdict.json",)

CODE_RUNTIME_TIER1_GATES: frozenset[str] = frozenset(
    {
        "test",
        "coverage",
        "diff-coverage",
        "test-counts",
        "tests-changed",
        "test-stability-changed",
        "mutation-changed",
        "contracts-check",
        "audit",
        "duplication",
        "build",
        "smoke",
        "telemetry",
        "flag-states",
        "load-smoke",
        "api-check",
        "data-compat",
        "sast",
        "authz-check",
        "log-redaction",
        "determinism",
        "exact-values",
        "oracle",
        "idempotency",
        "fail-closed",
        "schema-integrity",
        "iac",
        "artefact-scan",
        "type-coverage",
        "rollback",
        "dependency-faults",
        "config-refusal",
        "graceful-stop",
    }
)

TIER2_GATES: frozenset[str] = frozenset(
    {
        "mutation",
        "api-deep",
        "symbolic",
        "fuzz",
        "e2e",
        "test-stability",
        "reproducible",
        "sanitizers",
        "perf",
        "dast",
        "pentest",
        "load",
        "telemetry-e2e",
        "flags-expired",
        "a11y",
        "formal",
        "bite-all",
    }
)


@dataclass(frozen=True)
class Decision:
    active: bool
    files: tuple[str, ...] = ()
    reason: str = ""


def is_page(path: str) -> bool:
    return path.lower().endswith(PAGE_DOCS) or gatelib.matches(path, list(VERDICT_RECORDS))


def changed_paths(root: Path, base: str) -> list[str] | None:
    tracked = gatelib.git(root, "diff", "--name-only", "--no-renames", "-z", base)
    untracked = gatelib.git(root, "ls-files", "--others", "--exclude-standard", "-z")
    if tracked is None or untracked is None:
        return None
    return sorted({path for raw in (tracked, untracked) for path in raw.split("\0") if path})


def disabled_in_binding(binding: str) -> bool:
    bullets = markdown_text.bullets(markdown_text.section(binding, SPEED_SECTION))
    raw = bullets.get(PAGES_LANE_BULLET, "").strip("` ")
    return raw.lower().startswith("none:")


def reads_globs(binding: str) -> list[str]:
    globs: list[str] = []
    for line in markdown_text.section(binding, project_bite.READS).splitlines():
        if match := project_bite.READS_BULLET.match(line):
            for entry in match.group(2).split(","):
                item = entry.strip("` ")
                if item:
                    globs.append(item)
    return globs


def sensitive_globs(binding: str) -> list[str]:
    globs = reads_globs(binding)
    speed = markdown_text.bullets(markdown_text.section(binding, SPEED_SECTION))
    globs.extend(markdown_text.ticked(speed.get(PAGES_EXCLUDE_BULLET, "")))
    kinds, _ = triggers.parse(binding)
    for kind in kinds.values():
        globs.extend(kind.paths)
    dep = dependencies.parse(binding)
    if dep is not None:
        globs.extend(dep.manifests)
        globs.extend(dep.lockfiles)
    pin_rules, _ = pins.parse(binding)
    for rule in pin_rules or ():
        globs.extend(rule.files)
    test_globs, _ = change_size.test_paths(binding)
    globs.extend(test_globs or ())
    formal = markdown_text.bullets(markdown_text.section(binding, FORMAL_SECTION))
    for key in ("Model", "Mirrors"):
        value = formal.get(key, "")
        if value and not value.startswith("<"):
            globs.extend(markdown_text.ticked(value))
    return globs


def full(reason: str) -> Decision:
    return Decision(active=False, reason=reason)


def forced_full_reason(gates: Sequence[str]) -> str | None:
    if os.environ.get("RATCHET_BITING") or os.environ.get("RATCHET_FULL"):
        return "forced full"
    event = os.environ.get("GITHUB_EVENT_NAME")
    if event and event != "pull_request":
        return f"event {event}"
    if any(gate in TIER2_GATES for gate in gates):
        return "tier 2 run"
    return None


def base_rules(root: Path) -> tuple[str, str, guard_manifest.Manifest] | Decision:
    base = gatelib.base_commit(root)
    if base is None or gatelib.adoption(root, base):
        return full("no non-adoption base")
    binding = gatelib.show(root, base, str(gatelib.BINDING))
    manifest_text = gatelib.show(root, base, str(MANIFEST_PATH))
    if binding is None or manifest_text is None:
        return full("missing base binding or guard manifest")
    try:
        guards = guard_manifest.parse(manifest_text)
    except guard_manifest.ManifestError:
        return full("unreadable guard manifest")
    if disabled_in_binding(binding):
        return full("disabled in binding")
    return base, binding, guards


def classify(root: Path, gates: Sequence[str] = ()) -> Decision:
    if reason := forced_full_reason(gates):
        return full(reason)
    loaded = base_rules(root)
    if isinstance(loaded, Decision):
        return loaded
    base, binding, guards = loaded
    paths = changed_paths(root, base)
    if not paths:
        return full("no changed files")
    sensitive = sensitive_globs(binding)
    for path in paths:
        if (
            not is_page(path)
            or guards.group_of(path) is not None
            or (sensitive and gatelib.matches(path, sensitive))
        ):
            return full(f"non-page change: {path}")
    return Decision(active=True, files=tuple(paths))


def filter_gates(gates: Sequence[str], decision: Decision) -> tuple[list[str], list[str]]:
    if not decision.active:
        return list(gates), []
    kept = [gate for gate in gates if gate not in CODE_RUNTIME_TIER1_GATES]
    skipped = [gate for gate in gates if gate in CODE_RUNTIME_TIER1_GATES]
    return kept, skipped


def summary(decision: Decision, skipped: Sequence[str]) -> str:
    files_word = "file" if len(decision.files) == 1 else "files"
    gates_word = "gate" if len(skipped) == 1 else "gates"
    return (
        f"pages-lane: pages-only change ({len(decision.files)} {files_word});"
        f" skipped {len(skipped)} code/runtime tier-1 {gates_word}"
    )
