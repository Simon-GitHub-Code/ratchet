"""The pipeline script's verdict helpers, run in node over the agents' reports."""

import json
import shutil
import subprocess
from pathlib import Path

import merge_ready
from tests.repos import branch, commit, repository, write

PIPELINE = Path(__file__).resolve().parents[2] / "workflows/pipeline.js"
MANIFEST_TOML = '[[group]]\nname = "g"\nreason = "Because it is."\npaths = ["lint.toml"]\n'


def _paths(call: str, *arguments: object) -> list[str]:
    """The sorted paths one of the script's helpers returns for ``arguments``."""
    source = PIPELINE.read_text(encoding="utf-8")
    helpers = source[source.index("function packageName") : source.index("function must(")]
    program = (
        f"{helpers}\nconst args = JSON.parse(process.argv[1])\n"
        f"console.log(JSON.stringify([...{call}(...args)].sort()))"
    )
    node = shutil.which("node")
    assert node, "node runs the workflow script"
    run = subprocess.run(  # noqa: S603 # -- a fixed argv, no shell
        [node, "-e", program, json.dumps(arguments)],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(run.stdout)


def _decided(spec: list[dict[str, str]] | None, notes: list[dict[str, object]] | None) -> list[str]:
    return _paths("specDecidedDependencies", spec, notes)


SPEC = [{"name": "Attrs", "reason": "value objects"}]
MANIFEST = {"path": "pyproject.toml", "note": "dependencies added", "packages": ["attrs"]}
LOCK = {"path": "uv.lock", "note": "lockfile", "packages": ["attrs", "ruff"]}


def test_a_dependency_the_spec_named_covers_its_manifest_and_lockfile() -> None:
    assert _decided(SPEC, [MANIFEST, LOCK]) == ["pyproject.toml", "uv.lock"]


def test_an_added_package_the_spec_did_not_name_covers_nothing() -> None:
    extra = {**MANIFEST, "packages": ["attrs", "pytest-nocov"]}
    assert _decided(SPEC, [extra, LOCK]) == []


def test_a_lockfile_alone_is_not_covered() -> None:
    assert _decided(SPEC, [LOCK]) == []
    version_only = {**MANIFEST, "packages": []}
    assert _decided(SPEC, [version_only, LOCK]) == ["pyproject.toml"]


def test_missing_reports_cover_nothing() -> None:
    assert _decided(None, None) == []


def test_a_record_the_change_only_adds_to_is_covered() -> None:
    notes = [
        {"path": "GLOSSARY.md", "additions": ["term Click (avoid: hit, visit)"]},
        {"path": "docs/adr/0004-links-expire.md", "additions": ["ADR 0004 A link expires"]},
        {"path": "src/billing/GLOSSARY.md", "additions": []},
    ]
    assert _paths("additiveRecords", notes) == [
        "GLOSSARY.md",
        "docs/adr/0004-links-expire.md",
        "src/billing/GLOSSARY.md",
    ]


def test_a_record_note_without_a_path_or_a_list_covers_nothing() -> None:
    broken = [None, {"additions": ["term Click"]}, {"path": "GLOSSARY.md"}, {"path": ""}]
    assert _paths("additiveRecords", broken) == []
    assert _paths("additiveRecords", None) == []


def _record(**fields: object) -> dict[str, object]:
    """The verdict record the script's helper builds from ``fields``."""
    source = PIPELINE.read_text(encoding="utf-8")
    helper = source[source.index("function verdictRecord") : source.index("function must(")]
    program = f"{helper}\nconsole.log(JSON.stringify(verdictRecord(JSON.parse(process.argv[1]))))"
    node = shutil.which("node")
    assert node, "node runs the workflow script"
    run = subprocess.run(  # noqa: S603 # -- a fixed argv, no shell
        [node, "-e", program, json.dumps(fields)], capture_output=True, text=True, check=True
    )
    record: dict[str, object] = json.loads(run.stdout)
    return record


def test_the_verdict_record_names_what_is_still_open_as_texts() -> None:
    findings = [
        {"severity": "blocker", "title": " loses the order "},
        {"severity": "major", "title": ""},
        {"severity": "minor", "title": "a typo"},
        None,
    ]
    assumption = {"question": "q", "assumed": "a", "refuter": "r"}
    assert _record(
        slug="thing",
        ticket=4,
        verdict="request_changes",
        forcedBy=["1 unresolved blocker/major critic finding(s)"],
        findings=findings,
        openQuestions=["Which currency?"],
        assumptions=[assumption],
    ) == {
        "slug": "thing",
        "ticket": "4",
        "verdict": "request_changes",
        "forcedBy": ["1 unresolved blocker/major critic finding(s)"],
        "unresolved": ["loses the order", "a finding with no title"],
        "openQuestions": ["Which currency?"],
        "assumptions": [assumption],
    }


def test_a_clean_approval_records_nothing_open() -> None:
    record = _record(slug="thing", ticket="4", verdict="approve")
    assert record == {
        "slug": "thing",
        "ticket": "4",
        "verdict": "approve",
        "forcedBy": [],
        "unresolved": [],
        "openQuestions": [],
        "assumptions": [],
    }


def test_the_judge_passes_the_record_the_script_builds(tmp_path: Path) -> None:
    root = repository(tmp_path, {"guards/manifest.toml": MANIFEST_TOML, "src/a.py": "x = 1\n"})
    branch(root, "feature/4-thing")
    write(root, "src/a.py", "x = 2\n")
    commit(root, "feat(thing): the change")
    record = _record(slug="thing", ticket="4", verdict="approve")
    assert merge_ready.write_record(root, json.dumps(record)) == 0
    commit(root, "docs(thing): the verdict record")
    assert merge_ready.judge(root, "main", "HEAD") == []
