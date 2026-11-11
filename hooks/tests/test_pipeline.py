"""The pipeline script's verdict helpers, run in node over the agents' reports."""

import json
import shutil
import subprocess
from pathlib import Path

PIPELINE = Path(__file__).resolve().parents[2] / "workflows/pipeline.js"


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
