"""Starting states that once made ``upgrade.py --apply`` loosen a gate, guess, or break a file."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

import conformance
import gatelib
import justfile_text
import markdown_text
import plugin_pin
import settings_rules
import upgrade
from tests import repos
from tests.support import PLUGIN_ROOT
from tests.test_conformance import BINDING, pristine, replace
from tests.test_upgrade import files, fixture, planned

JUSTFILE = "justfile"
SETTINGS = ".claude/settings.json"
RENAMED = {"tier0": "gate0", "tier1": "gate1", "tier2": "gate2", "check-deep": "all-deep"}
PIN_SCRIPT = "plugin_pin.py"
STRICTER = ("Bash(gh pr merge:*)", "Edit(/justfile)")
PROJECT_FLAG = '[[flag]]\nkey = "ignored-flag"\nowner = "me"\ndefault = false\n'
NEEDS_JUST = pytest.mark.skipif(shutil.which("just") is None, reason="needs `just` on PATH")


def commit(root: Path) -> None:
    repos.git(root, "add", "-A")
    repos.git(root, "commit", "-q", "-m", "chore: the project's own")


def heads(text: str) -> dict[str, tuple[str, ...]]:
    """Each recipe of a justfile, with the recipes it depends on before its body."""
    return {
        block.name: block.parsed.before
        for block in justfile_text.blocks(text.split("\n"))
        if block.parsed is not None
    }


def just_reads(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 # -- a fixed argv
        ["just", "--justfile", str(root / JUSTFILE), "--list"],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    )


def rename_tiers(root: Path) -> None:
    """The tiers under names of the project's own, in the justfile and the binding alike."""
    just = (root / JUSTFILE).read_text()
    binding = (root / BINDING).read_text()
    for old, new in [*RENAMED.items(), ("check", "all")]:
        just = just.replace(f"\n{old}:", f"\n{new}:")
        binding = binding.replace(f"`just {old}`", f"`just {new}`")
    just = just.replace("\nall: tier0 tier1", "\nall: gate0 gate1")
    just = just.replace("\nall-deep: check tier2", "\nall-deep: all gate2")
    (root / JUSTFILE).write_text(just)
    (root / BINDING).write_text(binding)
    commit(root)


def test_tiers_under_other_names_are_pinned_or_left_to_an_agent_never_claimed(
    tmp_path: Path,
) -> None:
    root = fixture("0.9.2", tmp_path)
    rename_tiers(root)
    result = planned(root)
    upgrade.apply(root, result.changes)
    justfile = conformance.read_justfile(root) or ""
    for tier in ("gate0", "gate1", "gate2", "all", "all-deep"):
        assert justfile_text.runs(justfile, tier, PIN_SCRIPT), tier
    assert heads(justfile)["gate1"][:1] == ("plugin-pin",)
    assert not [f for f in conformance.findings(root) if PIN_SCRIPT in f.problem]


def test_a_tier_that_does_not_run_the_pin_check_is_found_though_another_does(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    shutil.copytree(pristine(), root, symlinks=True)
    replace(JUSTFILE, "tier1: plugin-pin", "tier1:")(root)
    [found] = [f for f in conformance.findings(root) if PIN_SCRIPT in f.problem]
    assert isinstance(found.fix, conformance.Auto)
    made = found.fix.want((root / JUSTFILE).read_text())
    assert heads(made)["tier1"][:1] == ("plugin-pin",)


def test_an_offered_edit_is_auto_only_when_the_check_that_offered_it_no_longer_finds_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    shutil.copytree(pristine(), root, symlinks=True)
    agent = conformance.Agent(("do it by hand",))

    def marked(root: Path) -> list[conformance.Finding]:
        text = (root / "notes.md").read_text() if (root / "notes.md").is_file() else ""
        if "marked" in text:
            return []
        marks = conformance.Edit("mark it", lambda _: "marked\n", agent)
        touches = conformance.Edit("touch it", lambda _: "x\n", agent)
        return [
            conformance.Finding("notes.md", "is not marked", marks),
            conformance.Finding("notes.md", "is not marked", touches),
        ]

    monkeypatch.setattr(conformance, "CHECKS", (marked,))
    clears, touches = conformance.findings(root)
    assert isinstance(clears.fix, conformance.Auto)
    assert clears.fix.does == "mark it"
    assert touches.fix is agent
    assert not (root / "notes.md").exists()


def test_a_check_cannot_call_its_own_finding_auto(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def claims(_: Path) -> list[conformance.Finding]:
        auto = conformance.Auto("trust me", lambda _: "x\n")
        return [conformance.Finding("notes.md", "is wrong", auto)]

    monkeypatch.setattr(conformance, "CHECKS", (claims,))
    with pytest.raises(TypeError, match="only the trial makes a finding auto"):
        conformance.findings(tmp_path)


def import_the_pin(root: Path) -> None:
    """``plugin-pin`` defined in a file the justfile imports, run by tier0 alone."""
    (root / "method.just").write_text(
        'plugin-pin:\n    @python3 "{{ratchet_plugin}}/scripts/plugin_pin.py"\n'
    )
    just = (root / JUSTFILE).read_text()
    just = just.replace("\ndefault:", "\nimport 'method.just'\n\ndefault:", 1)
    just = just.replace("\ntier0: pre-commit commits", "\ntier0: plugin-pin pre-commit commits")
    (root / JUSTFILE).write_text(just)
    commit(root)


def test_a_recipe_an_imported_file_defines_is_never_copied_again(tmp_path: Path) -> None:
    root = fixture("0.9.2", tmp_path)
    import_the_pin(root)
    upgrade.apply(root, planned(root).changes)
    main, imported = (root / JUSTFILE).read_text(), (root / "method.just").read_text()
    assert "plugin-pin" not in [block.name for block in justfile_text.blocks(main.split("\n"))]
    assert imported.count("plugin-pin:") == 1
    justfile = conformance.read_justfile(root) or ""
    for tier in ("tier0", "tier1", "tier2", "check", "check-deep"):
        assert justfile_text.runs(justfile, tier, PIN_SCRIPT), tier
    assert heads(main)["tier1"][:1] == ("plugin-pin",)
    assert not [f for f in conformance.findings(root) if PIN_SCRIPT in f.problem]


@NEEDS_JUST
def test_a_justfile_that_imports_its_recipes_still_parses_after_an_upgrade(
    tmp_path: Path,
) -> None:
    root = fixture("0.9.2", tmp_path)
    import_the_pin(root)
    assert just_reads(root).returncode == 0
    upgrade.apply(root, planned(root).changes)
    done = just_reads(root)
    assert done.returncode == 0, done.stderr


@NEEDS_JUST
def test_a_planned_justfile_that_just_cannot_read_is_refused_and_nothing_is_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = fixture("0.9.2", tmp_path)
    before = files(root)

    def twice(root: Path) -> list[conformance.Finding]:
        text = (root / JUSTFILE).read_text()
        if text.endswith("default:\n    echo again\n"):
            return []

        def want(now: str | None) -> str:
            return f"{now}\ndefault:\n    echo again\n"

        edit = conformance.Edit("define default again", want, conformance.Agent())
        return [conformance.Finding(JUSTFILE, "has one default", edit)]

    monkeypatch.setattr(conformance, "CHECKS", (twice,))
    result = upgrade.plan(root, PLUGIN_ROOT)
    assert isinstance(result, upgrade.Refused)
    assert "`just` cannot read the planned justfile" in result.reason
    assert "redefined" in result.reason
    assert files(root) == before


def test_without_just_the_planned_justfile_is_not_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(shutil, "which", lambda _: None)
    assert upgrade.unreadable(tmp_path, tmp_path) is None


def test_a_file_git_ignores_is_never_written_over(tmp_path: Path) -> None:
    root = fixture("0.9.2", tmp_path)
    shutil.rmtree(root / "docs/features")
    (root / ".gitignore").write_text("flags.toml\ndocs/features/\n")
    commit(root)
    (root / "flags.toml").write_text(PROJECT_FLAG)
    before = files(root)
    result = upgrade.plan(root, PLUGIN_ROOT)
    assert isinstance(result, upgrade.Refused)
    assert "git ignores docs/features/.gitkeep and flags.toml" in result.reason
    assert files(root) == before


def deny_instead(root: Path, rules: tuple[str, ...]) -> None:
    path = root / SETTINGS
    data = json.loads(path.read_text())
    for rule in rules:
        data["permissions"]["ask"].remove(rule)
        data["permissions"]["deny"].append(rule)
    path.write_text(json.dumps(data, indent=2) + "\n")


def test_a_rule_the_owner_made_stricter_stays_stricter_and_is_named(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = fixture("0.9.2", tmp_path)
    deny_instead(root, STRICTER)
    commit(root)
    assert upgrade.main(["upgrade.py", "--root", str(root), "--apply"]) == 1
    out = capsys.readouterr().out
    permissions = json.loads((root / SETTINGS).read_text())["permissions"]
    for rule in STRICTER:
        assert rule in permissions["deny"]
        assert rule not in permissions["ask"]
        assert f"`{rule}`" in out.split("For an agent:")[0]
    assert "keeping in deny what the plugin asks:" in out
    assert not [d for d in settings_rules.drifts(root) if any(r in d.line for r in STRICTER)]


def test_a_stricter_rule_is_not_drift_and_a_looser_one_is(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    shutil.copytree(pristine(), root, symlinks=True)
    deny_instead(root, ("Bash(gh pr merge:*)",))
    assert settings_rules.drift(root) == []
    data = json.loads((root / SETTINGS).read_text())
    data["permissions"]["deny"].remove("Bash(git push --force:*)")
    data["permissions"]["ask"].append("Bash(git push --force:*)")
    (root / SETTINGS).write_text(json.dumps(data))
    manifest = settings_rules.read_manifest(root) or {}
    made = json.loads(settings_rules.regenerated(manifest)((root / SETTINGS).read_text()))
    assert "Bash(git push --force:*)" in made["permissions"]["deny"]
    assert "Bash(git push --force:*)" not in made["permissions"]["ask"]
    assert "Bash(gh pr merge:*)" in made["permissions"]["deny"]
    assert "Bash(gh pr merge:*)" not in made["permissions"]["ask"]


TRIGGERS = (
    "\n## Triggers\n\n| Kind | Paths | Pattern |\n| --- | --- | --- |\n"
    "| container | `Dockerfile` | |\n| flag | `flags.toml` | `^key = ` |\n"
)


def test_a_gates_row_whose_trigger_has_fired_is_never_steered_to_later(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = fixture("0.8.2", tmp_path)
    (root / BINDING).write_text((root / BINDING).read_text() + TRIGGERS)
    (root / "Dockerfile").write_text("FROM scratch\n")
    commit(root)
    assert upgrade.main(["upgrade.py", "--root", str(root)]) == 1
    out = capsys.readouterr().out
    fired = next(line for line in out.splitlines() if "'configuration hardening' row" in line)
    assert "bind it: its trigger `code:container` has fired here" in fired
    assert "later" not in fired.split("never dropped")[1]
    assert "'container' has 1 instance here: Dockerfile" in out
    waiting = next(line for line in out.splitlines() if "'changed flags off and on' row" in line)
    assert "or `later: code:flag` only while that trigger has not fired" in waiting
    assert "'flag' has no instance here yet" in out
    always = next(line for line in out.splitlines() if "'incidents tighten a gate' row" in line)
    assert "bind it: the question is asked from the first commit" in always
    assert "a row that says `later: <trigger>` needs none" not in out


def test_whether_a_trigger_fired_is_said_unknown_without_its_triggers_row(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = fixture("0.8.2", tmp_path)
    assert upgrade.main(["upgrade.py", "--root", str(root)]) == 1
    out = capsys.readouterr().out
    assert "the Triggers table has no row for 'container', so whether it has fired" in out


ENTRY_HEADING = "## Entry points\n"
BINDINGS = {
    "a CRLF heading": lambda text: text.replace(ENTRY_HEADING, "## Entry points\r\n"),
    "a duplicate heading first": lambda text: text.replace(
        "## Forge", "## Entry points\n\nSee the table below, left by an old edit.\n\n## Forge", 1
    ),
    "the heading named in prose first": lambda text: text.replace(
        "## Runner\n", "## Runner\n\nThe commands are under `## Entry points` below.\n"
    ),
}


@pytest.mark.parametrize("variant", BINDINGS)
def test_an_unusual_binding_gets_its_rows_and_no_false_item(variant: str, tmp_path: Path) -> None:
    root = fixture("0.9.2", tmp_path)
    (root / BINDING).write_bytes(BINDINGS[variant]((root / BINDING).read_text()).encode())
    commit(root)
    result = planned(root)
    assert not [f for f in result.agent if "Entry points table has no row" in f.problem]
    upgrade.apply(root, result.changes)
    rows = conformance.entry_points((root / BINDING).read_text())
    assert set(conformance.ENTRY_POINTS) <= set(rows)


def test_a_section_is_found_one_way_by_every_reader_and_writer() -> None:
    text = (
        "# T\n\nSee `## Rows` below.\n\n## Rows \r\n\nfirst\n\n## Other\n\n## Rows\n\n| a | b |\n"
    )
    assert markdown_text.has_section(text, "## Rows")
    assert not markdown_text.has_section("See `## Rows`.\n### Rows\n", "## Rows")
    assert "first" in markdown_text.section(text, "## Rows")
    assert "| a | b |" in markdown_text.section(text, "## Rows")
    assert "See" not in markdown_text.section(text, "## Rows")
    assert markdown_text.section(text, "## Missing") == ""


def test_a_file_that_leads_out_of_the_repository_by_a_relative_link_is_refused(
    tmp_path: Path,
) -> None:
    root = fixture("0.9.2", tmp_path)
    outside = tmp_path / "outside-justfile"
    shutil.copy(root / JUSTFILE, outside)
    (root / JUSTFILE).unlink()
    (root / JUSTFILE).symlink_to("../outside-justfile")
    commit(root)
    before = outside.read_bytes()
    result = upgrade.plan(root, PLUGIN_ROOT)
    assert isinstance(result, upgrade.Refused)
    assert "justfile leads outside the repository" in result.reason
    assert outside.read_bytes() == before


def test_a_file_read_through_a_link_out_of_the_repository_is_judged_as_it_reads(
    tmp_path: Path,
) -> None:
    root = repos.repository(tmp_path / "repo", {"kept.md": "a"})
    outside = tmp_path / "shared"
    outside.mkdir()
    (outside / "GLOSSARY.md").write_text("# Glossary\n")
    (outside / "docs").mkdir()
    (outside / "docs/brief.md").write_text("brief\n")
    (root / "GLOSSARY.md").symlink_to("../shared/GLOSSARY.md")
    (root / "shared-docs").symlink_to("../shared/docs")
    (root / "dangling.md").symlink_to("../nowhere.md")
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    out = gatelib.copy_seen(root, sandbox)
    away = "leads outside the repository"
    assert out == {"GLOSSARY.md": away, "shared-docs": away, "dangling.md": away}
    assert (sandbox / "GLOSSARY.md").read_text() == "# Glossary\n"
    assert not (sandbox / "GLOSSARY.md").is_symlink()
    assert (sandbox / "shared-docs/brief.md").read_text() == "brief\n"
    assert not (sandbox / "dangling.md").is_symlink()
    assert (sandbox / ".git").is_dir()


def test_a_write_stopped_midway_leaves_nothing_git_sees_and_the_next_run_clears_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = fixture("0.9.2", tmp_path)
    changes = planned(root).changes
    temporaries: list[Path] = []
    real = Path.replace

    def stopped(self: Path, _: Path) -> Path:
        temporaries.append(self)
        raise KeyboardInterrupt

    monkeypatch.setattr(Path, "replace", stopped)
    with pytest.raises(KeyboardInterrupt):
        upgrade.apply(root, changes)
    monkeypatch.setattr(Path, "replace", real)
    [temporary] = temporaries
    assert temporary.parent == root / ".ratchet/upgrade"
    left = temporary.parent / f"{upgrade.TEMPORARY}left-by-a-killed-run"
    left.write_bytes(b"half")
    assert repos.git(root, "status", "--porcelain", "--untracked-files=all") == ""
    upgrade.apply(root, planned(root).changes)
    assert not left.exists()
    assert [path.name for path in temporary.parent.iterdir()] == [upgrade.LOCK]


ASKING = "## Asking the owner"
LATIN = "\n- Owner: Ren\xe9 M\xfcller\n".encode("latin-1")


def test_a_file_that_is_not_utf8_is_never_rewritten_and_its_item_is_an_agent_s(
    tmp_path: Path,
) -> None:
    root = fixture("0.9.2", tmp_path)
    agents = root / "AGENTS.md"
    agents.write_bytes(agents.read_bytes() + LATIN)
    commit(root)
    before = agents.read_bytes()
    result = planned(root)
    assert "AGENTS.md" not in result.changes
    assert any(f.path == "AGENTS.md" and ASKING in f.problem for f in result.agent)
    upgrade.apply(root, result.changes)
    assert agents.read_bytes() == before


def test_a_binding_that_is_not_utf8_is_refused_with_its_name(tmp_path: Path) -> None:
    root = fixture("0.9.2", tmp_path)
    binding = root / BINDING
    binding.write_bytes(binding.read_bytes() + LATIN)
    commit(root)
    before = files(root)
    result = upgrade.plan(root, PLUGIN_ROOT)
    assert isinstance(result, upgrade.Refused)
    assert result.reason.startswith(f"{BINDING} is not UTF-8 text")
    assert files(root) == before


def test_a_file_a_check_reads_that_is_not_utf8_is_a_refusal_not_a_crash(tmp_path: Path) -> None:
    root = fixture("0.9.2", tmp_path)
    (root / JUSTFILE).write_bytes((root / JUSTFILE).read_bytes() + b"# caf\xe9\n")
    commit(root)
    result = upgrade.plan(root, PLUGIN_ROOT)
    assert isinstance(result, upgrade.Refused)
    assert "is not UTF-8 text" in result.reason


def ignored_by_local_config(root: Path) -> None:
    (root / ".git/local-excludes").write_text("flags.toml\n")
    repos.git(root, "config", "core.excludesFile", str(root / ".git/local-excludes"))
    (root / "flags.toml").write_text(PROJECT_FLAG)


def test_a_file_ignored_by_any_rule_git_reads_is_never_written_over(tmp_path: Path) -> None:
    root = fixture("0.9.2", tmp_path)
    ignored_by_local_config(root)
    quality = root / "quality"
    (quality / ".gitignore").write_text("*\n")
    (quality / "bites.toml").write_text("# the project's own bites\n")
    before = files(root)
    result = upgrade.plan(root, PLUGIN_ROOT)
    assert isinstance(result, upgrade.Refused)
    assert "git ignores flags.toml and quality/bites.toml" in result.reason
    assert files(root) == before
    assert (quality / "bites.toml").read_text() == "# the project's own bites\n"


def test_a_recipe_an_ignored_optional_import_defines_is_never_copied(tmp_path: Path) -> None:
    root = fixture("0.9.2", tmp_path)
    just = (root / JUSTFILE).read_text()
    (root / JUSTFILE).write_text(
        just.replace("\ndefault:", "\nimport? 'local.just'\n\ndefault:", 1)
    )
    (root / ".gitignore").write_text("local.just\n")
    commit(root)
    (root / "local.just").write_text("merge branch:\n    echo signs off {{branch}} first\n")
    upgrade.apply(root, planned(root).changes)
    main = [block.name for block in justfile_text.blocks((root / JUSTFILE).read_text().split("\n"))]
    assert "merge" not in main
    assert "hooks-install" in main


def test_a_line_that_only_starts_like_the_pin_is_never_replaced(tmp_path: Path) -> None:
    root = fixture("0.9.2", tmp_path)
    note = "- Ratchet plugin: the marketplace clone; CI checks out the `ref` in each workflow"
    binding = root / BINDING
    binding.write_text(binding.read_text() + f"\n## Tool versions\n\n{note}\n")
    commit(root)
    upgrade.apply(root, planned(root).changes)
    text = binding.read_text()
    assert note in text
    assert plugin_pin.pinned(text) is not None


def test_a_file_in_a_submodule_is_read_and_never_written(tmp_path: Path) -> None:
    root = fixture("0.9.2", tmp_path)
    docs = repos.repository(tmp_path / "docs-repo")
    shutil.copytree(root / "docs", docs, dirs_exist_ok=True)
    repos.commit(docs, "docs: the project's records")
    repos.git(root, "rm", "-q", "-r", "docs")
    shutil.rmtree(root / "docs", ignore_errors=True)
    repos.git(root, "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(docs), "docs")
    commit(root)
    before = (root / BINDING).read_text()
    result = upgrade.plan(root, PLUGIN_ROOT)
    assert isinstance(result, upgrade.Refused)
    assert result.reason == (
        f"{BINDING} is in the submodule docs, so the upgrade does not write it"
    )
    assert (root / BINDING).read_text() == before


def test_a_link_into_guards_is_never_written_through(tmp_path: Path) -> None:
    root = fixture("0.9.2", tmp_path)
    (root / "AGENTS.md").rename(root / "guards/AGENTS.md")
    (root / "AGENTS.md").symlink_to("guards/AGENTS.md")
    commit(root)
    before = (root / "guards/AGENTS.md").read_bytes()
    result = planned(root)
    assert not [name for name in result.changes if name.startswith("guards/")]
    assert any(f.path == "AGENTS.md" and ASKING in f.problem for f in result.agent)
    upgrade.apply(root, result.changes)
    assert (root / "guards/AGENTS.md").read_bytes() == before


def test_a_binding_kept_under_guards_is_refused(tmp_path: Path) -> None:
    root = fixture("0.9.2", tmp_path)
    (root / BINDING).rename(root / "guards/binding.md")
    (root / BINDING).symlink_to("../../guards/binding.md")
    commit(root)
    result = upgrade.plan(root, PLUGIN_ROOT)
    assert isinstance(result, upgrade.Refused)
    assert result.reason == "guards/binding.md is under guards/, which the upgrade never writes"


APPEND_GROUP = (
    '[[group]]\nname = "records"\nreason = "an agent adds one"\nedit = "append"\n'
    'paths = ["docs/records/**"]\n\n'
)


def test_a_deny_the_owner_holds_over_an_append_group_stays(tmp_path: Path) -> None:
    root = fixture("0.9.2", tmp_path)
    manifest = root / "guards/manifest.toml"
    manifest.write_text(manifest.read_text() + "\n" + APPEND_GROUP)
    path = root / SETTINGS
    data = json.loads(path.read_text())
    data["permissions"]["deny"].append("Edit(/docs/records/**)")
    path.write_text(json.dumps(data, indent=2) + "\n")
    commit(root)
    upgrade.apply(root, planned(root).changes)
    assert "Edit(/docs/records/**)" in json.loads(path.read_text())["permissions"]["deny"]
    assert not [d for d in settings_rules.drifts(root) if "docs/records" in d.line]
