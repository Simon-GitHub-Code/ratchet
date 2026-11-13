"""The settings check: ``.claude/settings.json`` holds the fixed denies and no ask (ADR 0033)."""

import json
import tomllib
from pathlib import Path

import pytest

import settings_rules
from ratchet_guards.manifest import GIT_STATE_PATTERNS
from tests.support import STANDARD, make_guard, rules

TEMPLATES = Path(__file__).resolve().parents[2] / "skills/adopt/templates"


def test_the_deny_list_is_the_tamper_paths_then_the_command_rules() -> None:
    assert settings_rules.DENY[0] == "Edit(/.claude/settings.json)"
    assert "Edit(~/.claude/plugins/**)" in settings_rules.DENY
    assert settings_rules.DENY[len(settings_rules.TAMPER) :] == list(settings_rules.COMMAND_DENIES)
    assert settings_rules.ASK == []
    assert "Bash(gh label create:*)" not in settings_rules.DENY
    assert "Bash(gh pr merge:*)" not in settings_rules.DENY + settings_rules.ASK


def test_the_template_settings_are_what_the_generator_writes() -> None:
    # Why: the template ships the file; a hand edit to it is the drift this gate catches.
    assert settings_rules.drift(TEMPLATES) == []


def test_every_path_the_template_locks_and_the_hook_reads_refs_through_is_denied() -> None:
    # Why: the list is fixed so that it never follows a manifest edit; this keeps it no looser
    # than the paths the template still has the owner change by hand. The workflows are the one
    # exception: adoption writes their plugin refs after `just settings` has switched the rules
    # on, so until the adoption merges only the hook, reading the default branch's manifest,
    # refuses them.
    manifest = tomllib.loads((TEMPLATES / "guards/manifest.toml").read_text(encoding="utf-8"))
    locked = {p for g in manifest["group"] if g.get("edit") == "deny" for p in g["paths"]}
    assert ".github/workflows/**" in locked
    assert locked - {".github/workflows/**"} | set(GIT_STATE_PATTERNS) <= set(settings_rules.TAMPER)


def test_a_workflow_is_refused_on_a_branch_once_the_template_manifest_is_committed() -> None:
    guard = make_guard(manifest=STANDARD)
    assert rules("echo x > .github/workflows/ci.yml", guard) == ["guard-path-write"]
    assert rules("echo x > .github/pull_request_template.md", guard) == []
    assert rules("rm -rf .github", guard) == ["guard-path-write"]


@pytest.mark.parametrize("pattern", settings_rules.TAMPER)
def test_each_tamper_path_is_one_the_hook_refuses_on_a_branch(pattern: str) -> None:
    # Why: a settings deny the hook does not share would leave the two layers disagreeing
    # (ADR 0006); each of these acts before a merge, so the hook refuses it on any branch.
    path = pattern.replace("/**", "/x")
    command = f"echo x > {path}"
    assert rules(command, make_guard()) == ["guard-path-write"]


def test_write_then_check_round_trips_and_keeps_other_keys(tmp_path: Path) -> None:
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude/settings.json").write_text(
        json.dumps(
            {
                "env": {"X": "1"},
                "permissions": {"allow": ["Bash(ls:*)"], "ask": ["Edit(/justfile)"]},
            }
        )
    )
    assert settings_rules.drift(tmp_path) != []
    settings_rules.write(tmp_path)
    assert settings_rules.drift(tmp_path) == []
    written = json.loads((tmp_path / ".claude/settings.json").read_text())
    assert written["env"] == {"X": "1"}
    assert written["permissions"]["allow"] == ["Bash(ls:*)"]
    assert written["permissions"]["ask"] == []


def test_write_creates_the_settings_file_with_the_schema(tmp_path: Path) -> None:
    settings_rules.write(tmp_path)
    written = json.loads((tmp_path / ".claude/settings.json").read_text())
    assert written["$schema"] == settings_rules.SCHEMA
    assert written["permissions"]["deny"] == settings_rules.DENY


def test_drift_names_each_missing_and_foreign_rule(tmp_path: Path) -> None:
    settings_rules.write(tmp_path)
    path = tmp_path / ".claude/settings.json"
    settings = json.loads(path.read_text())
    settings["permissions"]["deny"].remove("Edit(/.claude/hooks/**)")
    settings["permissions"]["deny"].append("Bash(gh label create:*)")
    settings["permissions"]["ask"].append("Edit(/justfile)")
    path.write_text(json.dumps(settings))
    assert settings_rules.drift(tmp_path) == [
        ".claude/settings.json: 'Edit(/.claude/hooks/**)' is missing from deny",
        ".claude/settings.json: 'Bash(gh label create:*)' in deny is not a rule this tool writes",
        ".claude/settings.json: 'Edit(/justfile)' in ask is not a rule this tool writes",
    ]


def test_an_adopted_repository_still_on_the_old_rules_is_red_until_regenerated(
    tmp_path: Path,
) -> None:
    # Why: the ask rules the manifest used to generate are what this change removes; a
    # repository that keeps them meets the same prompts, so the gate names each one.
    (tmp_path / ".claude").mkdir()
    old = {"permissions": {"deny": settings_rules.DENY, "ask": ["Bash(gh pr merge:*)"]}}
    (tmp_path / ".claude/settings.json").write_text(json.dumps(old))
    assert settings_rules.drift(tmp_path) == [
        ".claude/settings.json: 'Bash(gh pr merge:*)' in ask is not a rule this tool writes"
    ]


def test_unreadable_settings_are_reported_not_raised(tmp_path: Path) -> None:
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude/settings.json").write_text("[1, 2]")
    assert settings_rules.read_settings(tmp_path) == {}
    (tmp_path / ".claude/settings.json").write_text("{nope")
    assert settings_rules.read_settings(tmp_path) == {}
    assert settings_rules.drift(tmp_path)[0].endswith("is missing from deny")
    assert settings_rules.listed({"permissions": {"ask": [1, "a"]}}, "ask") == ["a"]
    assert settings_rules.listed({"permissions": {"ask": "a"}}, "ask") == []
    assert settings_rules.listed({"permissions": []}, "ask") == []
    (tmp_path / ".claude/settings.json").write_text(json.dumps({"permissions": "x"}))
    settings_rules.write(tmp_path)
    assert settings_rules.drift(tmp_path) == []


def test_main_reports_and_exits(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert settings_rules.main(["x", "--root", str(tmp_path), "--check"]) == 1
    assert capsys.readouterr().out.endswith("settings-check: red\n")
    assert settings_rules.main(["x", "--root", str(tmp_path), "--write"]) == 0
    assert capsys.readouterr().out == "settings-check: .claude/settings.json written\n"
    assert settings_rules.main(["x", "--root", str(tmp_path)]) == 0
    assert capsys.readouterr().out == "settings-check: green\n"


@pytest.mark.parametrize(
    "rule", [*settings_rules.DEFAULT_BRANCH_DENIES, *settings_rules.OTHER_REF_DENIES]
)
def test_each_default_ref_permission_rule_is_a_command_the_hook_refuses(rule: str) -> None:
    # Why: a settings deny that the hook allows would refuse a command the hook lets through
    # (ADR 0006); every rule here names a spelling the hook already denies.
    command = rule.removeprefix("Bash(").removesuffix(")").replace(":*", " x").replace("*", "x")
    assert rules(command) == ["default-ref"]


@pytest.mark.parametrize(
    ("command", "rule"),
    [
        ("git config core.hooksPath /tmp/x", "hooks-path"),
        ("git -c core.hooksPath=/dev/null commit -m x", "hooks-path"),
    ],
)
def test_each_hooks_path_permission_rule_is_a_command_the_hook_refuses(
    command: str, rule: str
) -> None:
    assert rules(command) == [rule]
