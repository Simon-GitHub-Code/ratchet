"""The settings check: ``.claude/settings.json`` holds the fixed denies and no ask (ADR 0033)."""

import json
import tomllib
from pathlib import Path

import pytest
from hypothesis import example, given
from hypothesis import strategies as st

import plugin_pin
import settings_rules
from ratchet_guards.manifest import GIT_STATE_PATTERNS
from ratchet_guards.verdict import strictest
from tests import repos
from tests.adopted import adopted, copy, half_done
from tests.support import STANDARD, FakeRepo, make_guard, rules, verdicts

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
APPEND = """
[[group]]
name = "records"
reason = "r"
edit = "append"
paths = ["docs/adr/**", "GLOSSARY.md"]
"""


def repository(tmp_path: Path, manifest: str = MANIFEST) -> Path:
    (tmp_path / "guards").mkdir(parents=True, exist_ok=True)
    (tmp_path / "guards/manifest.toml").write_text(manifest)
    return tmp_path


def write(root: Path) -> int:
    """``settings_rules.py --write`` run from the plugin this suite lives in."""
    return settings_rules.main(["x", "--root", str(root), "--write"])


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
    template = json.loads((TEMPLATES / ".claude/settings.json").read_text())
    assert template["permissions"]["ask"] == []


def test_every_path_the_template_locks_and_the_hook_reads_refs_through_is_denied() -> None:
    # Why: the list is fixed so that it never follows a manifest edit; this keeps it no looser
    # than the paths the template still has the owner change by hand.
    manifest = tomllib.loads((TEMPLATES / "guards/manifest.toml").read_text(encoding="utf-8"))
    locked = {p for g in manifest["group"] if g.get("edit") == "deny" for p in g["paths"]}
    assert locked | set(GIT_STATE_PATTERNS) <= set(settings_rules.TAMPER)


def test_a_workflow_is_asked_on_a_branch_once_the_template_manifest_is_committed() -> None:
    # Why: GitHub runs a branch's own workflow before any merge, so the owner is asked at the
    # write; a settings deny would make him edit the file by hand, and adoption writes the
    # workflows before the manifest is on the default branch.
    guard = make_guard(manifest=STANDARD)
    assert rules("echo x > .github/workflows/ci.yml", guard) == ["workflow-write"]
    on_main = make_guard(FakeRepo(branch="main"), manifest=STANDARD)
    assert rules("echo x > .github/workflows/ci.yml", on_main) == [
        "guard-path-default",
        "workflow-write",
    ]
    assert rules("echo x > .github/pull_request_template.md", guard) == []
    assert not any("workflows" in rule for rule in settings_rules.TAMPER if ".github" in rule)


@pytest.mark.parametrize("pattern", settings_rules.TAMPER)
def test_each_tamper_path_is_one_the_hook_refuses_on_a_branch(pattern: str) -> None:
    # Why: a settings deny the hook does not share would leave the two layers disagreeing
    # (ADR 0006); each of these acts before a merge, so the hook refuses it on any branch.
    path = pattern.replace("/**", "/x")
    refused = strictest(verdicts(f"echo x > {path}", make_guard()))
    assert refused is not None
    assert refused.rule == "guard-path-write"


def test_write_then_check_round_trips_and_keeps_other_keys(tmp_path: Path) -> None:
    root = repository(tmp_path)
    (root / ".claude").mkdir()
    (root / ".claude/settings.json").write_text(
        json.dumps({"env": {"X": "1"}, "permissions": {"allow": ["Bash(ls:*)"]}})
    )
    assert settings_rules.drift(root) != []
    assert write(root) == 0
    assert settings_rules.drift(root) == []
    written = json.loads((root / ".claude/settings.json").read_text())
    assert written["env"] == {"X": "1"}
    assert written["permissions"]["allow"] == ["Bash(ls:*)"]
    assert written["permissions"]["ask"] == []


def test_the_rules_follow_no_manifest_and_an_exclude_no_longer_stops_the_write(
    tmp_path: Path,
) -> None:
    excluding = MANIFEST.replace(
        'edit = "deny"', 'edit = "deny"\nexclude = [".claude/worktrees/**"]'
    )
    first = repository(tmp_path / "a", excluding + APPEND)
    second = repository(tmp_path / "b", APPEND)
    assert write(first) == 0
    assert write(second) == 0
    assert (first / settings_rules.SETTINGS).read_text() == (
        second / settings_rules.SETTINGS
    ).read_text()


def test_write_creates_the_settings_file_with_the_schema(tmp_path: Path) -> None:
    root = repository(tmp_path)
    assert write(root) == 0
    written = json.loads((root / ".claude/settings.json").read_text())
    assert written["$schema"] == settings_rules.SCHEMA
    assert written["permissions"]["deny"] == settings_rules.DENY


def test_drift_names_each_missing_and_foreign_rule(tmp_path: Path) -> None:
    root = repository(tmp_path)
    write(root)
    path = root / ".claude/settings.json"
    settings = json.loads(path.read_text())
    settings["permissions"]["deny"].remove("Edit(/.claude/hooks/**)")
    settings["permissions"]["deny"].append("Bash(gh label create:*)")
    settings["permissions"]["ask"].append("Edit(/justfile)")
    path.write_text(json.dumps(settings))
    assert settings_rules.drift(root) == [
        ".claude/settings.json: 'Edit(/.claude/hooks/**)' is missing from deny",
        ".claude/settings.json: 'Bash(gh label create:*)' in deny is not a rule this tool writes",
        ".claude/settings.json: 'Edit(/justfile)' in ask is not a rule this tool writes",
    ]


def test_an_adopted_repository_still_on_the_old_rules_is_red_until_regenerated(
    tmp_path: Path,
) -> None:
    # Why: the ask rules the manifest used to generate are what this change removes; a
    # repository that keeps them meets the same prompts, so the gate names each one, and a
    # regeneration takes them out of ask.
    root = repository(tmp_path)
    (root / ".claude").mkdir()
    old = {
        "permissions": {
            "deny": settings_rules.DENY,
            "ask": ["Bash(gh pr merge:*)", "Edit(/docs/adr/**)"],
        }
    }
    (root / ".claude/settings.json").write_text(json.dumps(old))
    assert [(found.fault, found.line) for found in settings_rules.drifts(root)] == [
        (
            settings_rules.Fault.MISPLACED,
            ".claude/settings.json: 'Bash(gh pr merge:*)' in ask is not a rule this tool writes",
        ),
        (
            settings_rules.Fault.MISPLACED,
            ".claude/settings.json: 'Edit(/docs/adr/**)' in ask is not a rule this tool writes",
        ),
    ]
    assert write(root) == 0
    assert settings_rules.drift(root) == []
    assert json.loads((root / ".claude/settings.json").read_text())["permissions"]["ask"] == []


def test_unreadable_inputs_are_reported_not_raised(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    unreadable = "guards/manifest.toml: missing or not TOML, so no permission rule can be derived"
    assert settings_rules.drift(tmp_path) == [unreadable]
    assert write(tmp_path) == 1
    assert capsys.readouterr().out == f"{unreadable}\nsettings-check: red\n"
    root = repository(tmp_path, "not toml [")
    assert settings_rules.read_manifest(root) is None
    (root / ".claude").mkdir()
    (root / ".claude/settings.json").write_text("[1, 2]")
    assert settings_rules.read_settings(root) == {}
    (root / ".claude/settings.json").write_text("{nope")
    assert settings_rules.read_settings(root) == {}
    assert settings_rules.listed({"permissions": {"ask": [1, "a"]}}, "ask") == ["a"]
    assert settings_rules.listed({"permissions": {"ask": "a"}}, "ask") == []
    assert settings_rules.listed({"permissions": []}, "ask") == []


def test_main_reports_and_exits(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = repository(tmp_path)
    assert settings_rules.main(["x", "--root", str(root), "--check"]) == 1
    assert capsys.readouterr().out.endswith("settings-check: red\n")
    assert settings_rules.main(["x", "--root", str(root), "--write"]) == 0
    assert capsys.readouterr().out == "settings-check: .claude/settings.json written\n"
    assert settings_rules.main(["x", "--root", str(root)]) == 0
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


PARSED = settings_rules.tomllib.loads(MANIFEST + APPEND)


@pytest.mark.parametrize(
    ("rule", "expected"),
    [
        ("Edit(/.claude/settings.json)", True),
        ("Edit(~/.claude/settings.json)", True),
        ("Edit(/.claude/hooks/**)", True),
        ("Edit(/docs/adr/**)", True),
        ("Edit(/GLOSSARY.md)", True),
        ("Bash(gh pr merge:*)", True),
        ("Bash(git push --force:*)", True),
        ("Edit(/src/**)", False),
        ("Bash(gh label create:*)", False),
        ("old", False),
    ],
)
def test_a_rule_is_generated_when_some_version_writes_it_in_either_list(
    *, rule: str, expected: bool
) -> None:
    assert settings_rules.plugin_writes(rule, PARSED) is expected


def test_a_manifest_with_no_usable_group_generates_only_the_fixed_rules() -> None:
    odd = {"group": [1, {"name": "n"}, {"name": "m", "paths": ["a", 2]}]}
    assert settings_rules.plugin_writes("Edit(/a)", odd)
    assert not settings_rules.plugin_writes("Edit(/2)", odd)
    assert settings_rules.plugin_writes("Bash(gh pr merge:*)", {})
    assert settings_rules.plugin_writes("Edit(/.git/config)", {})
    assert not settings_rules.plugin_writes("Edit(/a)", {"group": "x"})


def test_own_rules_are_those_no_version_generates_with_their_list() -> None:
    settings = {
        "permissions": {
            "deny": ["Edit(/src/**)", "Edit(/docs/adr/**)", "Bash(rm:*)"],
            "ask": ["Edit(/GLOSSARY.md)", "mine", 3],
            "allow": ["Bash(ls:*)"],
        }
    }
    assert settings_rules.own_rules(settings, PARSED) == [
        ("deny", "Edit(/src/**)"),
        ("deny", "Bash(rm:*)"),
        ("ask", "mine"),
    ]
    assert settings_rules.own_rules({}, PARSED) == []


def test_regenerated_keeps_own_rules_other_keys_and_a_stricter_rule_and_moves_a_looser_one() -> (
    None
):
    held = {
        "$schema": "s",
        "env": {"X": "1"},
        "permissions": {
            "allow": ["Bash(ls:*)"],
            "deny": ["Bash(rm:*)", "Edit(/GLOSSARY-MAP.md)", "Edit(/GLOSSARY.md)"],
            "ask": ["Edit(/.claude/settings.json)", "mine"],
            "defaultMode": "plan",
        },
    }
    out = json.loads(settings_rules.regenerated(PARSED)(json.dumps(held)))
    kept = ["Edit(/GLOSSARY-MAP.md)", "Edit(/GLOSSARY.md)"]
    assert out["permissions"]["deny"] == [*settings_rules.DENY, *kept, "Bash(rm:*)"]
    assert out["permissions"]["ask"] == ["mine"]
    assert out["permissions"]["allow"] == ["Bash(ls:*)"]
    assert out["permissions"]["defaultMode"] == "plan"
    assert (out["$schema"], out["env"]) == ("s", {"X": "1"})
    assert list(out) == list(held)


def test_regenerated_makes_a_new_file_and_a_permissions_that_is_not_a_table() -> None:
    new = json.loads(settings_rules.regenerated(PARSED)(None))
    assert new["$schema"] == settings_rules.SCHEMA
    assert new["permissions"]["deny"] == settings_rules.DENY
    odd = json.loads(settings_rules.regenerated(PARSED)('{"permissions": 3}'))
    assert odd["permissions"] == {"deny": settings_rules.DENY, "ask": []}


@pytest.mark.parametrize("text", ["[1, 2]", "{nope", "", "3"])
def test_regenerated_leaves_text_that_is_not_a_json_object_as_it_is(text: str) -> None:
    assert settings_rules.regenerated(PARSED)(text) == text


POOL = [
    *settings_rules.DENY[:3],
    *settings_rules.EARLIER_ASKS,
    "Edit(/docs/adr/**)",
    "Edit(/GLOSSARY.md)",
    "Bash(rm:*)",
    "Edit(/src/**)",
    "mine",
    "",
]
RULE_LISTS = st.lists(
    st.one_of(st.sampled_from(POOL), st.text(max_size=6), st.integers()), max_size=8
)
PERMISSIONS = st.one_of(
    st.integers(),
    st.dictionaries(
        st.sampled_from(["allow", "deny", "ask", "defaultMode"]),
        st.one_of(RULE_LISTS, st.text(max_size=4), st.none()),
    ),
)
SETTINGS_FILES = st.fixed_dictionaries(
    {},
    optional={
        "$schema": st.text(max_size=5),
        "env": st.dictionaries(st.text(max_size=3), st.text(max_size=3), max_size=3),
        "permissions": PERMISSIONS,
    },
)


@given(SETTINGS_FILES)
@example({"$schema": "", "permissions": {"deny": ["Bash(gh pr merge:*)"]}})
def test_regenerated_is_idempotent_and_keeps_every_key_and_own_rule(
    settings: dict[str, object],
) -> None:
    want = settings_rules.regenerated(PARSED)
    once = want(json.dumps(settings))
    assert want(once) == once
    out = json.loads(once)
    held = settings.get("permissions")
    held = held if isinstance(held, dict) else {}
    kept = {key: value for key, value in held.items() if key not in ("deny", "ask")}
    assert {key: out["permissions"][key] for key in kept} == kept
    assert {key: value for key, value in settings.items() if key != "permissions"} == {
        key: value for key, value in out.items() if key != "permissions"
    }
    assert out["permissions"]["deny"][: len(settings_rules.DENY)] == settings_rules.DENY
    for key in ("deny", "ask"):
        own = [r for r in settings_rules.listed(settings, key) if not plugin_written(r)]
        assert [r for r in out["permissions"][key] if not plugin_written(r)] == own
    assert not [rule for rule in out["permissions"]["ask"] if plugin_written(rule)]
    assert set(settings_rules.listed(settings, "deny")) <= set(out["permissions"]["deny"])
    assert settings_rules.listed(out, "deny") == out["permissions"]["deny"]


def plugin_written(rule: str) -> bool:
    return settings_rules.plugin_writes(rule, PARSED)


MERGE = "Bash(gh pr merge:*)"
FORCE = "Bash(git push -f:*)"


@pytest.mark.parametrize(
    ("held", "where"),
    [
        ({"deny": [MERGE]}, {MERGE: (1, 0)}),
        ({"ask": [FORCE]}, {FORCE: (1, 0)}),
        ({"deny": [MERGE], "ask": [MERGE]}, {MERGE: (1, 0)}),
        ({"deny": [MERGE, MERGE]}, {MERGE: (1, 0)}),
        ({"deny": ["Bash(gh pr merge :*)"]}, {"Bash(gh pr merge :*)": (1, 0), MERGE: (0, 0)}),
    ],
    ids=[
        "an ask rule the owner denies",
        "a deny rule the owner asks for",
        "one rule in both lists",
        "one rule the owner denies twice",
        "a rule that differs only in spacing",
    ],
)
def test_a_held_rule_ends_once_in_one_list_never_looser_and_a_second_run_changes_nothing(
    held: dict[str, list[str]], where: dict[str, tuple[int, int]]
) -> None:
    want = settings_rules.regenerated(PARSED)
    once = want(json.dumps({"permissions": held}))
    out = json.loads(once)["permissions"]
    assert {rule: (out["deny"].count(rule), out["ask"].count(rule)) for rule in where} == where
    assert set(held.get("deny", [])) <= set(out["deny"])
    assert want(once) == once


def plugin(root: Path, version: str = "1.2.3") -> tuple[Path, str]:
    """A plugin checkout at ``root`` and the commit it is at."""
    repos.repository(root, {".claude-plugin/plugin.json": f'{{"version": "{version}"}}'})
    return root, repos.git(root, "rev-parse", "HEAD")


def pin(root: Path, commit: str | None) -> None:
    line = "" if commit is None else f"- Ratchet plugin: 1.2.3 at {commit}\n"
    repos.write(root, "docs/process/binding.md", f"# Binding\n\n## Runner\n\n`just`.\n\n{line}")


def checkout(root: Path, name: str, ref: str) -> None:
    text = f"jobs:\n  a:\n    steps:\n      - with: {{ repository: o/ratchet, ref: {ref} }}\n"
    repos.write(root, f".github/workflows/{name}.yml", text)


def write_from(root: Path, used: Path, monkeypatch: pytest.MonkeyPatch) -> int:
    """``--write`` run from the plugin at ``used``."""
    monkeypatch.setattr(plugin_pin, "PLUGIN", used)
    return write(root)


OTHER = "d" * 40
STALE = "ffffffffffffffffffffffffffffffffffffffff"


@pytest.mark.parametrize(
    ("pinned", "refs"),
    [(OTHER, ()), (None, (OTHER,)), (STALE, (OTHER,))],
    ids=["the pin is another commit", "refs only", "two commits"],
)
def test_write_refuses_off_the_commit_ci_runs_and_writes_nothing(
    *,
    pinned: str | None,
    refs: tuple[str, ...],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    used, _ = plugin(tmp_path / "plugin")
    root = repository(tmp_path / "repo")
    pin(root, pinned)
    for number, ref in enumerate(refs):
        checkout(root, f"w{number}", ref)
    code = write_from(root, used, monkeypatch)
    out = capsys.readouterr().out
    assert code == 1
    assert out.startswith(".claude/settings.json: not written, ")
    assert "upgrade.py --apply" in out
    assert "RATCHET_PLUGIN" in out
    assert out.endswith("\nsettings-check: red\n")
    assert not (root / ".claude/settings.json").exists()


def test_write_goes_ahead_when_the_repository_names_no_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    used, _ = plugin(tmp_path / "plugin")
    root = repository(tmp_path / "repo")
    pin(root, None)
    checkout(root, "w", "<ratchet-sha>")
    code = write_from(root, used, monkeypatch)
    out = capsys.readouterr().out
    assert (code, out.splitlines()[-1]) == (0, "settings-check: .claude/settings.json written")
    assert settings_rules.drift(root) == []


def test_write_goes_ahead_from_the_plugin_at_the_commit_ci_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    used, commit = plugin(tmp_path / "plugin")
    root = repository(tmp_path / "repo")
    pin(root, commit)
    checkout(root, "w", commit)
    assert write_from(root, used, monkeypatch) == 0
    assert settings_rules.drift(root) == []


def test_write_goes_ahead_from_a_copy_with_no_history_of_the_pinned_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    used = tmp_path / "cache/ratchet/1.2.3"
    repos.write(used, ".claude-plugin/plugin.json", '{"version": "1.2.3"}')
    root = repository(tmp_path / "repo")
    pin(root, OTHER)
    assert write_from(root, used, monkeypatch) == 0
    assert settings_rules.drift(root) == []


def test_write_keeps_the_projects_own_rules_and_refuses_a_settings_file_that_is_not_an_object(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = repository(tmp_path)
    (root / ".claude").mkdir()
    path = root / ".claude/settings.json"
    path.write_text(json.dumps({"permissions": {"deny": ["Bash(rm:*)"], "ask": ["Edit(/src/**)"]}}))
    assert write(root) == 0
    written = json.loads(path.read_text())["permissions"]
    assert written["deny"][-1] == "Bash(rm:*)"
    assert written["ask"][-1] == "Edit(/src/**)"
    assert settings_rules.drift(root) == [
        ".claude/settings.json: 'Bash(rm:*)' in deny is not a rule this tool writes",
        ".claude/settings.json: 'Edit(/src/**)' in ask is not a rule this tool writes",
    ]
    capsys.readouterr()
    path.write_text("{nope")
    assert write(root) == 1
    assert capsys.readouterr().out == (
        ".claude/settings.json: not a JSON object, so it is left as it is; fix or delete it\n"
        "settings-check: red\n"
    )
    assert path.read_text() == "{nope"


@pytest.mark.parametrize("fixture", ["0.9.2", "half-done"])
def test_an_adoption_at_an_older_plugin_refuses_the_current_one_and_stays_as_it_is(
    fixture: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = copy(half_done() if fixture == "half-done" else adopted("0.9.2"), tmp_path / "repo")
    before = (root / ".claude/settings.json").read_bytes()
    assert write(root) == 1
    out = capsys.readouterr().out
    assert out.startswith(".claude/settings.json: not written, CI runs the plugin at ")
    assert out.endswith("settings-check: red\n")
    assert (root / ".claude/settings.json").read_bytes() == before


def test_each_drift_says_whether_it_is_missing_misplaced_the_projects_own_or_the_manifests(
    tmp_path: Path,
) -> None:
    root = repository(tmp_path)
    write(root)
    path = root / ".claude/settings.json"
    settings = json.loads(path.read_text())
    settings["permissions"]["deny"].remove("Edit(/.claude/hooks/**)")
    settings["permissions"]["deny"].append("Bash(gh label create:*)")
    settings["permissions"]["deny"].append("Bash(gh pr merge:*)")
    settings["permissions"]["ask"].append("Bash(git push --force:*)")
    path.write_text(json.dumps(settings))
    fault = settings_rules.Fault
    assert [(found.fault, found.line.split("'")[1]) for found in settings_rules.drifts(root)] == [
        (fault.MISSING, "Edit(/.claude/hooks/**)"),
        (fault.OWN, "Bash(gh label create:*)"),
        (fault.MISPLACED, "Bash(git push --force:*)"),
    ]
    assert settings_rules.drifts(tmp_path / "none")[0].fault is fault.MANIFEST
    assert [found.line for found in settings_rules.drifts(root)] == settings_rules.drift(root)
