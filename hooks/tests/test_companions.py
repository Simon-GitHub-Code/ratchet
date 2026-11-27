import json
import re
from pathlib import Path

import pytest

import companions

ROOT = Path(__file__).resolve().parents[2]


def write_skill(
    root: Path,
    slug: str,
    description: str,
    *,
    category: str = "",
    user_only: bool = False,
) -> Path:
    folder = root / "skills" / category / slug if category else root / "skills" / slug
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "SKILL.md"
    flag = "disable-model-invocation: true\n" if user_only else ""
    body = f'---\nname: {slug}\ndescription: "{description}"\n{flag}  ignored: value\n---\nbody\n'
    path.write_text(body, encoding="utf-8")
    return path


def test_registry_matches_declared_companions_and_pipeline_defaults() -> None:
    declared = companions.declared_plugins()
    all_roles = companions.roles()
    assert all_roles
    for role in all_roles.values():
        assert role.plugin in declared
        assert role.kind in (companions.OWNER, companions.AGENT)
        assert role.slug
        assert role.fallback
        assert role.signals
    pipeline = (ROOT / "workflows/pipeline.js").read_text(encoding="utf-8")
    matched = re.search(r"const DEFAULT_COMPANIONS = \{(.*?)\}", pipeline, re.DOTALL)
    assert matched is not None
    for name in companions.PIPELINE_ROLES:
        role = all_roles[name]
        expected = f"{role.plugin}:{role.slug}"
        assert f"{name}: '{expected}'" in matched.group(1)


def test_plugin_root_discovers_harnesses_and_picks_newest_claude_version(tmp_path: Path) -> None:
    assert companions.plugin_root("mattpocock-skills", home=tmp_path) is None
    cache = tmp_path / ".claude/plugins/cache/mattpocock/mattpocock-skills"
    (cache / "0.9.2/skills").mkdir(parents=True)
    (cache / "0.9.10/skills").mkdir(parents=True)
    (cache / "not-a-dir").write_text("", encoding="utf-8")
    assert companions.plugin_root("mattpocock-skills", home=tmp_path) == cache / "0.9.10"
    agy = tmp_path / ".gemini/config/plugins/mattpocock-skills"
    (agy / "skills").mkdir(parents=True)
    assert companions.plugin_root("mattpocock-skills", home=tmp_path) == agy
    bad = tmp_path / "empty"
    bad.mkdir()
    assert companions.plugin_root("mattpocock-skills", overrides={"mattpocock-skills": bad}) is None
    good = tmp_path / "override"
    (good / "skills").mkdir(parents=True)
    assert (
        companions.plugin_root("mattpocock-skills", overrides={"mattpocock-skills": good}) == good
    )
    empty_cache = tmp_path / "empty_claude"
    (empty_cache / ".claude/plugins/cache/pstack-claude/pstack/1.0").mkdir(parents=True)
    assert companions.plugin_root("pstack", home=empty_cache) is None


def test_resolve_uses_direct_slug_offline_default_and_detects_flag_drift(tmp_path: Path) -> None:
    offline = companions.resolve("to_spec", home=tmp_path)
    assert offline.slug == "to-spec"
    assert offline.text == "/mattpocock-skills:to-spec"
    assert not offline.drifted

    plug = tmp_path / "mp"
    write_skill(plug, "to-spec", "Turn into a spec on the issue tracker", category="engineering")
    (plug / "skills/README.md").write_text("note", encoding="utf-8")
    found = companions.resolve("to_spec", overrides={"mattpocock-skills": plug})
    assert found.slug == "to-spec"
    assert found.text == "/mattpocock-skills:to-spec"
    assert not found.drifted

    pstack = tmp_path / "pstack"
    write_skill(pstack, "poteto-mode", "poteto's agent style", user_only=True)
    flagged = companions.resolve("poteto_mode", overrides={"pstack": pstack})
    assert flagged.drifted
    assert "disable-model-invocation: true" in flagged.note


def test_resolve_heals_renamed_slug_and_falls_back_when_missing_or_ambiguous(
    tmp_path: Path,
) -> None:
    plug = tmp_path / "mp"
    write_skill(
        plug,
        "write-spec",
        "Turn the conversation into a spec on the project issue tracker.",
        category="engineering",
        user_only=True,
    )
    healed = companions.resolve("to_spec", overrides={"mattpocock-skills": plug})
    assert healed.drifted
    assert healed.slug == "write-spec"
    assert healed.text == "/mattpocock-skills:write-spec"
    assert "resolved to mattpocock-skills:write-spec" in healed.note

    missing = companions.resolve("triage", overrides={"mattpocock-skills": plug})
    assert missing.drifted
    assert missing.slug is None
    assert missing.text == companions.roles()["triage"].fallback

    write_skill(plug, "tdd-owner", "test-driven red-green-refactor", user_only=True)
    assert companions.resolve("tdd", overrides={"mattpocock-skills": plug}).slug is None

    write_skill(plug, "tdd-a", "test-driven red-green-refactor")
    write_skill(plug, "tdd-b", "test-driven red-green-refactor")
    ambiguous = companions.resolve("tdd", overrides={"mattpocock-skills": plug})
    assert ambiguous.drifted
    assert ambiguous.slug is None


def test_read_skill_handles_missing_frontmatter_and_pipeline_map(tmp_path: Path) -> None:
    plain = tmp_path / "p/skills/plain-skill/SKILL.md"
    plain.parent.mkdir(parents=True)
    plain.write_text("# No frontmatter\n", encoding="utf-8")
    meta = companions.read_skill(plain)
    assert meta == companions.SkillMeta("plain-skill", "", user_only=False)
    mapped = companions.pipeline_map(home=tmp_path)
    assert set(mapped) == set(companions.PIPELINE_ROLES)
    assert mapped["tdd"] == "mattpocock-skills:tdd"


def test_cli_check_json_and_errors(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert companions.main(["companions.py", "--home", str(tmp_path)]) == 0
    assert companions.main(["companions.py", "--check", "--home", str(tmp_path)]) == 0
    assert "companions: green" in capsys.readouterr().out

    assert (
        companions.main(
            ["companions.py", "--check", "--require-installed", "--home", str(tmp_path)]
        )
        == 1
    )
    out = capsys.readouterr().out
    assert "mattpocock-skills: plugin not installed" in out
    assert out.count("mattpocock-skills: plugin not installed") == 1

    assert companions.main(["companions.py", "--json", "--home", str(tmp_path)]) == 0
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["to_spec"] == "/mattpocock-skills:to-spec"

    assert companions.main(["companions.py", "--plugin-dir", "bad-spec"]) == 2
    assert "expected NAME=PATH" in capsys.readouterr().err

    plug = tmp_path / "imp"
    write_skill(plug, "wrong", "unrelated")
    override = f"impeccable={plug}"
    args = ["companions.py", "--check", "--home", str(tmp_path), "--plugin-dir", override]
    assert companions.main(args) == 1
    assert "ui_design: impeccable:impeccable not found" in capsys.readouterr().out

    healthy = tmp_path / "imp_ok"
    write_skill(healthy, "impeccable", "Use to design a frontend.")
    ok_args = [
        "companions.py",
        "--check",
        "--home",
        str(tmp_path),
        "--plugin-dir",
        f"impeccable={healthy}",
    ]
    assert companions.main(ok_args) == 0
    assert "companions: green" in capsys.readouterr().out
