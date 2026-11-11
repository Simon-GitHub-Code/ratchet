"""The adopt templates: pinned, naming the plugin by commit, and the records' placeholders."""

from pathlib import Path

import pins

TEMPLATES = Path(__file__).resolve().parents[2] / "skills/adopt/templates"
ADOPT = (TEMPLATES.parent / "SKILL.md").read_text(encoding="utf-8")


def test_the_ci_templates_are_green_on_the_pins_gate() -> None:
    # Why: item 6 of the owner-free brief; a template that fails its own gate teaches the
    # wrong shape to every adopter.
    for name in ("ci.yml", "deep.yml"):
        path = f".github/workflows/{name}"
        text = (TEMPLATES / path).read_text(encoding="utf-8")
        assert pins.check_workflow(path, text) == [], name
        assert pins.check_images(path, text) == [], name
        assert 'repository: Simon-GitHub-Code/ratchet, ref: "<ratchet-sha>"' in text
        assert "RATCHET_PLUGIN: ${{ github.workspace }}/ratchet" in text


def test_adr_0001_names_the_version_and_the_commit() -> None:
    text = (TEMPLATES / "docs/adr/0001-adopt-the-ratchet-method.md").read_text(encoding="utf-8")
    assert "version <X.Y.Z> at commit `<ratchet-sha>`" in text
    assert "https://github.com/Simon-GitHub-Code/ratchet" in text


def test_adopt_names_the_two_skills_and_the_owner_s_steps() -> None:
    assert "`mattpocock-skills:grilling`" in ADOPT
    assert "`mattpocock-skills:domain-modeling`" in ADOPT
    assert "Run `mattpocock-skills:grill-with-docs`" not in ADOPT
    assert "do not create `DESIGN.md`" in ADOPT
    assert "`pre-commit install`" in ADOPT
    assert "scripts/hooks_installed.py" in ADOPT
    assert "`git config --unset core.hooksPath`" not in ADOPT
    assert "exempts a range made only of such commits" in ADOPT
    assert 'ref: "<ratchet-sha>"' in ADOPT
