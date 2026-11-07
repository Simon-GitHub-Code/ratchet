"""Architectural fitness tests: the decisions as executable checks (python-uv preset scaffold).

Adopt copies this file to ``tests/fitness/``. Each policy constant cites the ADR that holds it,
and one test fails when that ADR is gone. ``just limits`` runs ``-k limits``; ``just bite`` runs
``-k bites``, which feeds each check a synthetic violation and expects it to fail. Every check is
a plain function over its inputs, so the bite test can call it on a violation without touching
the repository.
"""

from __future__ import annotations

import ast
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
ADRS = ROOT / "docs/adr"
MAX_MODULE_LINES = 400  # ADR 0001: the binding's Limits
MAX_COMPLEXITY = 10  # ADR 0001: the binding's Limits, ruff C901
AVOID = re.compile(r"^_Avoid_:\s*(.+)$", re.MULTILINE)


def python_files(root: Path = SRC) -> list[Path]:
    return sorted(p for p in root.rglob("*.py") if ".venv" not in p.parts)


def over_the_line_limit(files: list[Path], limit: int = MAX_MODULE_LINES) -> list[Path]:
    return [p for p in files if len(p.read_text().splitlines()) > limit]


def banned_words(glossary: str) -> set[str]:
    words = {w.strip().strip("`").lower() for line in AVOID.findall(glossary) for w in line.split(",")}
    return {w for w in words if w and not w.startswith("<")}


def banned_identifiers(source: str, banned: set[str]) -> list[str]:
    """Identifiers defined or used in ``source`` that contain a banned word as a whole word."""
    found = []
    for node in ast.walk(ast.parse(source)):
        name = getattr(node, "name", None) or getattr(node, "id", None)
        if isinstance(name, str):
            words = re.split(r"_|(?<=[a-z])(?=[A-Z])", name)
            if any(w.lower() in banned for w in words):
                found.append(name)
    return found


def test_limits_modules_stay_within_the_line_limit() -> None:
    over = over_the_line_limit(python_files())
    assert over == [], f"split these modules (limit {MAX_MODULE_LINES} lines): {over}"


def test_limits_ruff_holds_the_same_complexity_limit_as_the_binding() -> None:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert config["tool"]["ruff"]["lint"]["mccabe"]["max-complexity"] == MAX_COMPLEXITY


def test_every_policy_constant_cites_an_existing_adr() -> None:
    cited = set(re.findall(r"# ADR (\d{4})", Path(__file__).read_text()))
    existing = {p.name[:4] for p in ADRS.glob("[0-9][0-9][0-9][0-9]-*.md")}
    assert cited, "a policy constant carries its ADR"
    assert cited <= existing, f"ADRs cited but not written: {sorted(cited - existing)}"


def test_identifiers_avoid_the_glossary_s_banned_words() -> None:
    banned = banned_words((ROOT / "GLOSSARY.md").read_text())
    offenders = [
        f"{p.relative_to(ROOT)}: {name}"
        for p in python_files()
        for name in banned_identifiers(p.read_text(), banned)
    ]
    assert offenders == [], offenders


def test_each_check_bites_on_its_synthetic_violation(tmp_path: Path) -> None:
    # Why: a check nobody has seen fail may never fire. Extend this with every new check.
    long_module = tmp_path / "long.py"
    long_module.write_text("x = 1\n" * (MAX_MODULE_LINES + 1))
    assert over_the_line_limit([long_module]) == [long_module]
    assert banned_identifiers("client_id = 1\ndef buyer(): ...\n", {"client", "buyer"}) == [
        "buyer",
        "client_id",
    ]
    assert banned_words("**Customer**:\nWho buys.\n_Avoid_: client, `buyer`, <synonym>\n") == {
        "client",
        "buyer",
    }
