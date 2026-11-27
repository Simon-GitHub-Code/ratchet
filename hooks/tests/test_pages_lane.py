from __future__ import annotations

import argparse
import json
from typing import TYPE_CHECKING

import already_proven
import ci_jobs
import pages_lane
import parallel
from tests.repos import branch, commit, git, repository, write

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

MANIFEST = (
    '[[group]]\nname = "process"\nreason = "Method rules"\npaths = ["docs/process/**"]\n'
    '[[group]]\nname = "instructions"\nreason = "Agent rules"\npaths = ["AGENTS.md"]\n'
)

BINDING = """# Quality gate binding

## Gates

- Application types: service
- Life events: none

| Question | Tool | Tier | Threshold source | Entry point |
| --- | --- | --- | --- | --- |
| unit, property and fitness tests | pytest | 1 | | `just test` |
| conformance | ratchet | 1 | | `just conformance` |

## What each gate reads

- lint: `docs/lint-guide.md`,

## Triggers

| Kind | Paths | Pattern |
| --- | --- | --- |
| doc-example | `docs/examples/*.md` | |

## Dependencies

- Manifests: `package.json`
- Lockfiles: `package-lock.json`
- Entry point: `just dependencies`
- Registries: none

## Image pins

| Files | Reference |
| --- | --- |
| `docs/deploy.md` | `FROM (\\S+)` |

## Test paths

- Tests: `tests/**`, `docs/fixtures/*.md`

## Formal verification

- Model: `docs/model/*.md`
- Mirrors: `src/**`
- Synced: abc123

## Speed

- Shards: 1
- Pages exclude: `docs/excluded/*.md`
"""


def adopted_repo(tmp_path: Path, binding: str = BINDING, manifest: str = MANIFEST) -> Path:
    root = repository(
        tmp_path / "repo",
        {
            "docs/process/binding.md": binding,
            "guards/manifest.toml": manifest,
            "docs/design-brief.md": "# Brief\n",
            "docs/notes.markdown": "# Notes\n",
            "src/app.py": "x = 1\n",
        },
    )
    branch(root, "docs/prune-pages")
    return root


def test_pages_lane_activates_for_markdown_and_verdict_json_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GITHUB_EVENT_NAME", raising=False)
    monkeypatch.delenv("RATCHET_BITING", raising=False)
    monkeypatch.delenv("RATCHET_FULL", raising=False)
    root = adopted_repo(tmp_path)
    write(root, "docs/design-brief.md", "# Updated brief\n")
    write(root, "docs/features/prune-pages/verdict.json", '{"verdict": "approve"}\n')

    decision = pages_lane.classify(root, ["prose", "conformance", "test", "graceful-stop"])
    assert decision.active is True
    assert decision.files == ("docs/design-brief.md", "docs/features/prune-pages/verdict.json")

    kept, skipped = pages_lane.filter_gates(
        ["prose", "conformance", "doctests", "custom-doc", "test", "graceful-stop"],
        decision,
    )
    assert kept == ["prose", "conformance", "doctests", "custom-doc"]
    assert skipped == ["test", "graceful-stop"]
    assert (
        pages_lane.summary(decision, skipped)
        == "pages-lane: pages-only change (2 files); skipped 2 code/runtime tier-1 gates"
    )


def test_pages_lane_summary_singular_counts() -> None:
    decision = pages_lane.Decision(active=True, files=("docs/design-brief.md",))
    assert (
        pages_lane.summary(decision, ["test"])
        == "pages-lane: pages-only change (1 file); skipped 1 code/runtime tier-1 gate"
    )
    assert pages_lane.filter_gates(["test"], pages_lane.full("forced")) == (["test"], [])


def test_pages_lane_fails_closed_on_env_event_tier2_and_missing_base(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = adopted_repo(tmp_path)
    write(root, "docs/design-brief.md", "# Updated\n")

    monkeypatch.setenv("RATCHET_BITING", "1")
    assert pages_lane.classify(root).active is False
    monkeypatch.delenv("RATCHET_BITING")

    monkeypatch.setenv("RATCHET_FULL", "1")
    assert pages_lane.classify(root).active is False
    monkeypatch.delenv("RATCHET_FULL")

    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
    assert pages_lane.classify(root).active is False
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    assert pages_lane.classify(root).active is True
    monkeypatch.delenv("GITHUB_EVENT_NAME")

    assert pages_lane.classify(root, ["conformance", "mutation"]).active is False

    not_a_repo = tmp_path / "plain"
    not_a_repo.mkdir()
    assert pages_lane.classify(not_a_repo).active is False

    adopt = repository(tmp_path / "adopt", {"README.md": "# Hi\n"})
    branch(adopt, "chore/adopt")
    write(adopt, "docs/process/binding.md", BINDING)
    write(adopt, "guards/manifest.toml", MANIFEST)
    assert pages_lane.classify(adopt).active is False


def test_pages_lane_fails_closed_on_missing_or_bad_base_files_and_disabled_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GITHUB_EVENT_NAME", raising=False)
    no_manifest = repository(
        tmp_path / "no-manifest",
        {"docs/process/binding.md": BINDING, "docs/notes.md": "a\n"},
    )
    branch(no_manifest, "docs/edit")
    write(no_manifest, "docs/notes.md", "b\n")
    assert pages_lane.classify(no_manifest).active is False

    bad_manifest = adopted_repo(tmp_path / "bad-manifest", manifest="not valid toml [[[")
    write(bad_manifest, "docs/design-brief.md", "# Changed\n")
    assert pages_lane.classify(bad_manifest).active is False

    disabled = adopted_repo(
        tmp_path / "disabled",
        binding=BINDING + "- Pages lane: none: tests read all docs\n",
    )
    write(disabled, "docs/design-brief.md", "# Changed\n")
    assert pages_lane.classify(disabled).active is False


def test_pages_lane_fails_closed_on_empty_diff_git_error_and_sensitive_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GITHUB_EVENT_NAME", raising=False)
    root = adopted_repo(tmp_path)
    assert pages_lane.classify(root).active is False

    monkeypatch.setattr(pages_lane.gatelib, "git", lambda *_args: None)
    assert pages_lane.changed_paths(root, "HEAD") is None
    monkeypatch.undo()

    sensitive_cases = [
        ("src/app.py", "x = 2\n"),
        ("docs/process/binding.md", BINDING + "\n"),
        ("docs/lint-guide.md", "# Lint\n"),
        ("docs/examples/demo.md", "# Demo\n"),
        ("docs/deploy.md", "FROM alpine@sha256:abc\n"),
        ("docs/fixtures/case.md", "# Fixture\n"),
        ("docs/model/state.md", "# Model\n"),
        ("docs/excluded/manual.md", "# Manual\n"),
    ]
    for rel, content in sensitive_cases:
        case_root = adopted_repo(tmp_path / rel.replace("/", "_"))
        write(case_root, rel, content)
        assert pages_lane.classify(case_root).active is False, rel

    minimal_binding = (
        "# Binding\n\n## Formal verification\n\n- Model: <placeholder>\n- Mirrors: <placeholder>\n"
    )
    minimal_root = adopted_repo(tmp_path / "minimal", binding=minimal_binding)
    write(minimal_root, "docs/design-brief.md", "# Minimal change\n")
    assert pages_lane.classify(minimal_root).active is True


def test_pages_lane_reads_rules_from_merge_base_not_working_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GITHUB_EVENT_NAME", raising=False)
    root = adopted_repo(tmp_path)
    write(root, "docs/excluded/manual.md", "# Changed\n")
    write(
        root,
        "docs/process/binding.md",
        BINDING.replace("- Pages exclude: `docs/excluded/*.md`\n", ""),
    )
    assert pages_lane.classify(root).active is False


def test_parallel_and_ci_jobs_honor_pages_lane(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("GITHUB_EVENT_NAME", raising=False)
    monkeypatch.delenv("RATCHET_BITING", raising=False)
    monkeypatch.delenv("RATCHET_FULL", raising=False)
    root = repository(
        tmp_path / "repo",
        {
            "docs/process/binding.md": BINDING,
            "guards/manifest.toml": MANIFEST,
            "docs/design-brief.md": "# Brief\n",
            "justfile": (
                'tier1_gates := "test conformance"\n'
                'tier1_sharded := ""\n'
                'tier1_jobs := "test rest"\n'
                "test:\n    @exit 1\n"
                "conformance:\n    @echo conformance ok\n"
                "coverage:\n    @exit 1\n"
                "mutation-changed:\n    @exit 1\n"
            ),
        },
    )
    branch(root, "docs/only-brief")
    write(root, "docs/design-brief.md", "# Brief v2\n")

    code = parallel.main(
        [
            "parallel.py",
            "--runner",
            "just",
            "--root",
            str(root),
            "--serial",
            "coverage",
            "mutation-changed",
            "--after",
            "test:conformance",
            "--",
            "conformance",
            "test",
            "coverage",
            "mutation-changed",
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "pages-lane: pages-only change (1 file); skipped 3 code/runtime tier-1 gates" in out

    code_all_skipped = parallel.main(
        ["parallel.py", "--runner", "just", "--root", str(root), "--", "test"]
    )
    assert code_all_skipped == 0
    assert "skipped 1 code/runtime tier-1 gate" in capsys.readouterr().out

    code_full = parallel.main(
        ["parallel.py", "--runner", "just", "--root", str(root), "--full", "--", "test"]
    )
    assert code_full == 1
    capsys.readouterr()

    assert (
        ci_jobs.main(["ci_jobs.py", "--root", str(root), "--run", "test", "--runner", "just"]) == 0
    )
    assert "skipped 1 code/runtime tier-1 gate" in capsys.readouterr().out


def test_already_proven_records_pages_lane_and_refuses_push_skip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GITHUB_EVENT_NAME", raising=False)
    monkeypatch.delenv("RATCHET_BITING", raising=False)
    monkeypatch.delenv("RATCHET_FULL", raising=False)
    root = adopted_repo(tmp_path)
    write(root, "docs/design-brief.md", "# Brief v2\n")
    commit(root, "docs(brief): update brief")
    git(root, "switch", "-q", "main")
    git(root, "merge", "--no-ff", "--no-commit", "docs/prune-pages")
    git(root, "commit", "-q", "-m", "docs(brief): merge")

    proven_path = tmp_path / "proven.json"
    assert already_proven.main(["record", "--root", str(root), "--out", str(proven_path)]) == 0
    recorded = json.loads(proven_path.read_text(encoding="utf-8"))
    assert recorded["lane"] == "pages"

    head = already_proven.merge_head(root)
    assert isinstance(head, already_proven.MergeHead)
    run_path = tmp_path / "run.json"
    run_path.write_text(
        json.dumps(
            {
                "id": 99,
                "repository": {"full_name": "o/r"},
                "path": ".github/workflows/ci.yml",
                "event": "pull_request",
                "status": "completed",
                "conclusion": "success",
                "head_sha": head.head,
                "created_at": "2026-11-27T19:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    opts = argparse.Namespace(
        run=str(run_path),
        proven=str(proven_path),
        repository="o/r",
        max_age=3600.0,
    )
    now = already_proven.parse_instant("2026-11-27T19:05:00Z")
    assert now is not None
    assert already_proven.verify_run_and_proven(opts, head, now) == "PR ran in pages lane"
