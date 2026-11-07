"""The settings check: ``.claude/settings.json`` is generated from the manifest and kept so."""

import json
from pathlib import Path

import pytest

import settings_rules

TEMPLATES = Path(__file__).resolve().parents[2] / "skills/adopt/templates"
MANIFEST = """
[[group]]
name = "harness"
reason = "r"
edit = "deny"
paths = [".claude/settings.json", "~/.claude/settings.json"]

[[group]]
name = "decisions"
reason = "r"
paths = ["docs/adr/**", "GLOSSARY-MAP.md"]

[[group]]
name = "unbound"
reason = "r"
paths = []
"""


def repository(tmp_path: Path, manifest: str = MANIFEST) -> Path:
    (tmp_path / "guards").mkdir()
    (tmp_path / "guards/manifest.toml").write_text(manifest)
    return tmp_path


def test_every_pattern_becomes_one_edit_rule_beside_the_fixed_command_rules() -> None:
    (deny, ask), problems = settings_rules.rules(settings_rules.tomllib.loads(MANIFEST))
    assert problems == []
    assert deny == [
        "Edit(/.claude/settings.json)",
        "Edit(~/.claude/settings.json)",
        *settings_rules.COMMAND_DENIES,
    ]
    assert ask == ["Bash(gh pr merge:*)", "Edit(/docs/adr/**)", "Edit(/GLOSSARY-MAP.md)"]
    assert "Bash(gh label create:*)" not in deny


def test_the_template_settings_are_what_the_template_manifest_generates() -> None:
    # Why: the template ships both files; a hand edit to one without the other is the drift
    # this gate exists to catch, so the plugin's own suite catches it first.
    assert settings_rules.drift(TEMPLATES) == []


def test_write_then_check_round_trips_and_keeps_other_keys(tmp_path: Path) -> None:
    root = repository(tmp_path)
    (root / ".claude").mkdir()
    (root / ".claude/settings.json").write_text(
        json.dumps({"env": {"X": "1"}, "permissions": {"allow": ["Bash(ls:*)"], "ask": ["old"]}})
    )
    assert settings_rules.drift(root) != []
    assert settings_rules.write(root) == []
    assert settings_rules.drift(root) == []
    written = json.loads((root / ".claude/settings.json").read_text())
    assert written["env"] == {"X": "1"}
    assert written["permissions"]["allow"] == ["Bash(ls:*)"]
    assert "old" not in written["permissions"]["ask"]


def test_write_creates_the_settings_file_with_the_schema(tmp_path: Path) -> None:
    root = repository(tmp_path)
    assert settings_rules.write(root) == []
    written = json.loads((root / ".claude/settings.json").read_text())
    assert written["$schema"] == settings_rules.SCHEMA
    assert "Edit(/.claude/settings.json)" in written["permissions"]["deny"]


def test_drift_names_each_missing_and_foreign_rule(tmp_path: Path) -> None:
    root = repository(tmp_path)
    settings_rules.write(root)
    path = root / ".claude/settings.json"
    settings = json.loads(path.read_text())
    settings["permissions"]["ask"].remove("Edit(/GLOSSARY-MAP.md)")
    settings["permissions"]["deny"].append("Bash(gh label create:*)")
    path.write_text(json.dumps(settings))
    assert settings_rules.drift(root) == [
        ".claude/settings.json: 'Bash(gh label create:*)' in deny mirrors no manifest pattern",
        ".claude/settings.json: 'Edit(/GLOSSARY-MAP.md)' is missing from ask",
    ]


def test_an_exclude_cannot_be_mirrored_and_stops_the_write(tmp_path: Path) -> None:
    excluding = MANIFEST.replace(
        'edit = "deny"', 'edit = "deny"\nexclude = [".claude/worktrees/**"]'
    )
    root = repository(tmp_path, excluding)
    problems = settings_rules.write(root)
    expected = (
        "guards/manifest.toml: group 'harness' uses exclude, which no permission rule can "
        "express; list the paths it should cover instead"
    )
    assert problems == [expected]
    assert not (root / ".claude/settings.json").exists()
    assert settings_rules.drift(root)[0] == problems[0]


def test_unreadable_inputs_are_reported_not_raised(tmp_path: Path) -> None:
    assert settings_rules.drift(tmp_path) == [
        "guards/manifest.toml: missing or not TOML, so no permission rule can be derived"
    ]
    assert settings_rules.write(tmp_path) == settings_rules.drift(tmp_path)
    root = repository(tmp_path, "not toml [")
    assert settings_rules.read_manifest(root) is None
    (root / ".claude").mkdir()
    (root / ".claude/settings.json").write_text("[1, 2]")
    assert settings_rules.read_settings(root) == {}
    (root / ".claude/settings.json").write_text("{nope")
    assert settings_rules.read_settings(root) == {}
    assert settings_rules.listed({"permissions": {"ask": [1, "a"]}}, "ask") == ["a"]
    assert settings_rules.listed({"permissions": {"ask": "a"}}, "ask") == []
    (_, ask), _ = settings_rules.rules({"group": [1, {"name": "n", "paths": "x"}]})
    assert ask == ["Bash(gh pr merge:*)"]


def test_main_reports_and_exits(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = repository(tmp_path)
    assert settings_rules.main(["x", "--root", str(root), "--check"]) == 1
    assert capsys.readouterr().out.endswith("settings-check: red\n")
    assert settings_rules.main(["x", "--root", str(root), "--write"]) == 0
    assert "written from guards/manifest.toml" in capsys.readouterr().out
    assert settings_rules.main(["x", "--root", str(root)]) == 0
    assert capsys.readouterr().out == "settings-check: green\n"
