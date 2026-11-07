"""The stack presets: each carries the same fragments, and each fragment fits its template."""

import json
import re
import tomllib
from pathlib import Path

import pytest

import pins

ADOPT = Path(__file__).resolve().parents[2] / "skills/adopt"
PRESETS = sorted(p for p in (ADOPT / "presets").iterdir() if p.is_dir())
TEMPLATE_JUSTFILE = (ADOPT / "templates/justfile").read_text(encoding="utf-8")
TEMPLATE_BINDING = (ADOPT / "templates/docs/process/binding.md").read_text(encoding="utf-8")
TEMPLATE_MANIFEST = tomllib.loads((ADOPT / "templates/guards/manifest.toml").read_text())
RECIPE = re.compile(r"^([a-z][a-z0-9-]*)(?:\s+[^:\n]*)?:\s*(?:#.*)?$", re.MULTILINE)
QUESTION = re.compile(r"^\| ([^|]+?) \| ", re.MULTILINE)


def recipes(justfile: str) -> set[str]:
    return set(RECIPE.findall(justfile))


def test_there_are_presets_for_both_stacks() -> None:
    assert [p.name for p in PRESETS] == ["python-uv", "typescript-pnpm"]


@pytest.mark.parametrize("preset", PRESETS, ids=lambda p: p.name)
def test_every_preset_carries_every_fragment(preset: Path) -> None:
    for name in ("justfile", "binding.md", "manifest.toml", "thresholds.toml", "ci.yml"):
        assert (preset / name).is_file(), name
    assert any((preset / "tools").iterdir())
    assert any((preset / "fitness").iterdir())


@pytest.mark.parametrize("preset", PRESETS, ids=lambda p: p.name)
def test_preset_recipes_are_template_recipes(preset: Path) -> None:
    # Why: a preset recipe replaces the template stub of the same name; a name the template
    # lacks would be an entry point no binding table names.
    mine = recipes((preset / "justfile").read_text(encoding="utf-8"))
    assert mine, "a preset binds at least one recipe"
    assert mine <= recipes(TEMPLATE_JUSTFILE), sorted(mine - recipes(TEMPLATE_JUSTFILE))
    shipped = {"markers", "suppressions", "prose", "snapshots", "snapshots-check", "commits"}
    assert not (mine & shipped), "the shipped gates are bound in the template already"


@pytest.mark.parametrize("preset", PRESETS, ids=lambda p: p.name)
def test_preset_binding_rows_answer_template_questions(preset: Path) -> None:
    text = (preset / "binding.md").read_text(encoding="utf-8")
    questions = set(QUESTION.findall(text)) - {"Question", "---"}
    template_questions = set(QUESTION.findall(TEMPLATE_BINDING)) - {"Question", "---"}
    assert questions, "the preset fills Tool cells"
    assert questions <= template_questions, sorted(questions - template_questions)
    for heading in ("## Runner", "## Absolute paths", "## Limits", "## Tool versions"):
        assert heading in text, heading


@pytest.mark.parametrize("preset", PRESETS, ids=lambda p: p.name)
def test_preset_manifest_groups_exist_in_the_template(preset: Path) -> None:
    groups = tomllib.loads((preset / "manifest.toml").read_text())["group"]
    template_groups = {g["name"] for g in TEMPLATE_MANIFEST["group"]}
    for group in groups:
        assert group["name"] in template_groups, group["name"]
        assert group["paths"], group["name"]
        assert all(not p.startswith(("/", "~")) for p in group["paths"])


@pytest.mark.parametrize("preset", PRESETS, ids=lambda p: p.name)
def test_preset_thresholds_declare_an_absolute_core_group(preset: Path) -> None:
    data = tomllib.loads((preset / "thresholds.toml").read_text())
    language = next(k for k in data if k != "ceilings")
    core = data[language]["coverage"]["core"]
    assert core["absolute"] is True
    assert core["paths"]
    assert all("paths" in g for g in data[language]["coverage"].values())
    assert data["ceilings"]["change"] == {"files": 40, "lines": 1500}


@pytest.mark.parametrize("preset", PRESETS, ids=lambda p: p.name)
def test_preset_ci_steps_are_green_on_the_pins_gate(preset: Path) -> None:
    # Why: item 6 of the owner-free brief: `pins` must be green on the template, presets included.
    fragment = (preset / "ci.yml").read_text(encoding="utf-8")
    assert "steps:" in fragment
    assert "cache:" in fragment
    workflow = "name: x\npermissions:\n  contents: read\njobs:\n  a:\n" + "\n".join(
        f"    {line}" for line in fragment.splitlines() if not line.startswith("#")
    )
    assert pins.check_workflow(".github/workflows/ci.yml", workflow) == []
    assert "uses:" in workflow


def test_the_typescript_package_fragment_is_json_and_pins_pnpm() -> None:
    data = json.loads((ADOPT / "presets/typescript-pnpm/tools/package.json").read_text())
    assert data["packageManager"].startswith("pnpm@")
    assert "vitest" in data["devDependencies"]


def test_the_python_pyproject_fragment_parses_and_forbids_coverage_exclusions() -> None:
    data = tomllib.loads((ADOPT / "presets/python-uv/tools/pyproject.toml").read_text())
    assert data["tool"]["ruff"]["lint"]["mccabe"]["max-complexity"] == 10
    assert "exclude_lines" not in data["tool"]["coverage"]["report"]
    assert "omit" not in data["tool"]["coverage"]["run"]
