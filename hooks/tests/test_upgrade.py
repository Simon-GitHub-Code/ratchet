import hashlib
import io
import os
import re
import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest

import budget
import comments
import conformance
import documents
import gatelib
import justfile_text
import markdown_text
import owed
import plugin_pin
import upgrade
from tests import adopted, repos
from tests.support import PLUGIN_ROOT
from tests.test_conformance import BINDING, MANIFEST, pristine, replace

FIXTURES = ["0.8.2", "0.9.2", "half-done"]
PROJECT_STEP = "./scripts/install-browsers"
PROJECT_BITE = "./scripts/project-bites --all"
PLUGIN_COMMIT = plugin_pin.commit_of(PLUGIN_ROOT)
PROJECT_RECIPE = "\ndeploy:\n    ./scripts/deploy --to production\n"
ENTRY = "| deploy | `just deploy` |\n"
DENY = "Bash(terraform apply:*)"


def fixture(name: str, tmp_path: Path) -> Path:
    source = adopted.half_done() if name == "half-done" else adopted.adopted(name)
    return adopted.copy(source, tmp_path / "repo")


def files(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file() and ".git" not in path.relative_to(root).parts
    }


def counts(root: Path) -> tuple[int, int, int]:
    kinds = [f.fix for f in conformance.findings(root)]
    return (
        sum(isinstance(fix, conformance.Auto) for fix in kinds),
        sum(isinstance(fix, conformance.Agent) for fix in kinds),
        sum(isinstance(fix, conformance.Owner) for fix in kinds),
    )


def planned(root: Path) -> upgrade.Plan:
    result = upgrade.plan(root, PLUGIN_ROOT)
    assert isinstance(result, upgrade.Plan), result
    return result


def lines(found: Sequence[conformance.Finding]) -> list[str]:
    return sorted(str(f) for f in found)


def verdict(report: str) -> str:
    return next(line for line in reversed(report.splitlines()) if line.startswith("upgrade: "))


def plain(report: str) -> str:
    return report[report.index("In plain words:") :].rstrip("\n")


@pytest.mark.parametrize("name", FIXTURES)
def test_an_upgrade_leaves_no_auto_finding_and_the_residual_is_what_the_plan_said(
    name: str, tmp_path: Path
) -> None:
    root = fixture(name, tmp_path)
    autos, _, _ = counts(root)
    result = planned(root)
    assert autos > 0
    assert result.changes
    upgrade.apply(root, result.changes)
    assert counts(root)[0] == 0
    left = lines(conformance.findings(root))
    assert left == lines(result.agent + result.owner)
    assert result.edits >= len(result.changes)


@pytest.mark.parametrize("name", FIXTURES)
def test_a_dry_run_writes_nothing_and_leaves_what_the_apply_leaves(
    name: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = fixture(name, tmp_path)
    before = files(root)
    assert upgrade.main(["upgrade.py", "--root", str(root)]) == 1
    dry = capsys.readouterr().out
    assert files(root) == before
    assert upgrade.main(["upgrade.py", "--root", str(root), "--apply"]) == 0, "all of it is owed"
    done = capsys.readouterr().out
    assert files(root) != before

    def residual(report: str) -> list[str]:
        kept = ("Red ", "Owed, ", "upgrade: w")
        return [block for block in report.split("\n\n") if block.startswith(kept)]

    assert dry.count("Would write") == 1
    assert done.count("Wrote ") == 1
    assert residual(dry)[:-1] == residual(done)[:-1]
    assert residual(dry)[-1].replace("would write", "wrote") == residual(done)[-1]
    assert plain(dry).replace("would move", "moves").replace("would make", "made") == plain(done)


BARE_PLUGIN_CALL = re.compile(
    r"python3 +\"\{\{ratchet_plugin\}\}|'python3 \"' \+ ratchet_plugin|PYTHONPATH="
)


@pytest.mark.parametrize("name", FIXTURES)
def test_an_upgrade_runs_every_plugin_script_of_an_older_justfile_under_the_template_interpreter(
    name: str, tmp_path: Path
) -> None:
    root = fixture(name, tmp_path)
    held = (root / "justfile").read_text(encoding="utf-8")
    assert BARE_PLUGIN_CALL.search(held), "the older justfile calls the bare interpreter"
    assert upgrade.main(["upgrade.py", "--root", str(root), "--apply"]) == 0
    done = (root / "justfile").read_text(encoding="utf-8")
    code = "\n".join(line for line in done.splitlines() if not line.lstrip().startswith("#"))
    assert not BARE_PLUGIN_CALL.search(code)
    assert '{{plugin_py}} "{{ratchet_plugin}}/scripts/' in done
    assert counts(root)[0] == 0
    assert not [f for f in conformance.findings(root) if "bare `python3`" in f.problem]


def test_an_upgrade_of_a_project_as_the_last_release_left_it_carries_the_interpreter(
    tmp_path: Path,
) -> None:
    root = adopted.copy(adopted.adopted("0.12.3"), tmp_path / "repo")
    mine = " --strict-by-hand\n"
    path = root / "justfile"
    held = path.read_text(encoding="utf-8")
    call = 'python3 "{{ratchet_plugin}}/scripts/markers.py"'
    assert call in held
    path.write_text(held.replace(call + "\n", call + mine, 1), encoding="utf-8")
    repos.git(root, "commit", "-q", "-am", "chore: a recipe of my own")
    assert upgrade.main(["upgrade.py", "--root", str(root), "--apply"]) == 0
    done = path.read_text(encoding="utf-8")
    code = "\n".join(line for line in done.splitlines() if not line.lstrip().startswith("#"))
    assert not BARE_PLUGIN_CALL.search(code)
    assert f'{{{{plugin_py}}}} "{{{{ratchet_plugin}}}}/scripts/markers.py"{mine}' in done
    assert "{{plugin_py}} -P -c 'import sys, urllib.request;" in done
    assert "python3 -c 'import sys, urllib.request;" not in done
    assert "#   'FLAG_OVERRIDES=$(python3 \"' + ratchet_plugin" in done
    just = shutil.which("just")
    if just is None:
        pytest.skip("just is not installed")
    listed = subprocess.run(  # noqa: S603 # -- a fixed argv; the justfile is the upgraded one
        [just, "--justfile", str(path), "--list"], capture_output=True, text=True, check=False
    )
    assert listed.returncode == 0, listed.stderr
    left = conformance.findings(root)
    assert not [f for f in left if isinstance(f.fix, conformance.Auto)]
    assert not [f for f in left if "bare `python3`" in f.problem]
    [ceiling] = [f for f in left if f.rule == "bite_all_ceiling"]
    assert "is held to tier 2's ceiling of 3600s" in ceiling.problem


def test_an_upgrade_of_a_project_adopted_before_the_jobs_setting_carries_the_templates_jobs_lines(
    tmp_path: Path,
) -> None:
    root = adopted.copy(adopted.adopted("0.12.0"), tmp_path / "repo")
    path = root / "justfile"
    old = 'jobs := env_var_or_default("RATCHET_JOBS", num_cpus())'
    assert old in path.read_text(encoding="utf-8")
    upgrade.main(["upgrade.py", "--root", str(root), "--apply"])
    done = path.read_text(encoding="utf-8")
    template = conformance.template_file("justfile")
    carried = "\n".join(
        (
            *justfile_text.definition(template, "configured_jobs"),
            *justfile_text.definition(template, "jobs"),
        )
    )
    assert carried in done
    assert old not in done
    assert not [f for f in conformance.findings(root) if "ratchet.jobs" in f.problem]


def upgraded_last_release(tmp_path: Path) -> Path:
    root = fixture("0.12.4", tmp_path)
    upgrade.main(["upgrade.py", "--root", str(root), "--apply"])
    return root


def test_an_upgrade_of_the_last_release_runs_the_comments_gate_in_tier_0(tmp_path: Path) -> None:
    root = upgraded_last_release(tmp_path)
    done = (root / "justfile").read_text(encoding="utf-8")
    assert "comments" in justfile_text.listed(done, "tier0_tree_gates")
    assert "comments" in justfile_text.reaches(done, "tier0-tree")
    assert "comments" in justfile_text.reaches(done, "tier0")
    assert not [f for f in conformance.findings(root) if "no tier runs `comments`" in f.problem]
    assert "no tier runs `comments`" not in "\n".join(e.problem for e in owed.read(root).entries)


def test_an_upgrade_of_the_last_release_passes_the_default_branchs_tier_1_to_the_merge_judge(
    tmp_path: Path,
) -> None:
    root = upgraded_last_release(tmp_path)
    done = (root / ".github/workflows/merge.yml").read_text(encoding="utf-8")
    template = conformance.template_file(".github/workflows/merge.yml")
    line = next(line for line in template.splitlines() if line.lstrip().startswith("tier1="))
    assert line in done.splitlines()
    assert '--tier1 "$tier1"' in done
    assert done.index("tier1=") < done.index("tier2=")
    assert "--tier1" not in "\n".join(e.problem for e in owed.read(root).entries)
    assert not [f for f in conformance.findings(root) if "'--tier1' absent" in f.problem]


def test_an_upgrade_of_the_last_release_exports_the_run_id_as_the_template_does(
    tmp_path: Path,
) -> None:
    root = upgraded_last_release(tmp_path)
    done = (root / "justfile").read_text(encoding="utf-8")
    template = conformance.template_file("justfile")
    line = next(line for line in template.splitlines() if line.startswith("export RATCHET_RUN :="))
    assert line in done.splitlines()
    assert not [f for f in conformance.findings(root) if "RATCHET_RUN" in f.problem]
    assert "RATCHET_RUN" not in "\n".join(e.problem for e in owed.read(root).entries)


@pytest.mark.parametrize("name", FIXTURES)
def test_a_second_upgrade_changes_nothing(name: str, tmp_path: Path) -> None:
    root = fixture(name, tmp_path)
    upgrade.apply(root, planned(root).changes)
    settled = files(root)
    again = planned(root)
    assert again.changes == {}
    assert again.edits == 0
    upgrade.apply(root, again.changes)
    assert files(root) == settled


@pytest.mark.parametrize("name", FIXTURES)
def test_every_edit_an_upgrade_makes_changes_nothing_when_made_again(
    name: str, tmp_path: Path
) -> None:
    root = fixture(name, tmp_path)
    made = 0
    for found in conformance.findings(root):
        if isinstance(found.fix, conformance.Auto):
            once = found.fix.want(gatelib.read_text(root / found.path))
            assert found.fix.want(once) == once, found.problem
            made += 1
    assert made


STOP_CHUNKS = 6


@pytest.fixture(scope="module")
def whole_upgrade(tmp_path_factory: pytest.TempPathFactory) -> tuple[upgrade.Plan, dict[str, str]]:
    whole = fixture("0.8.2", tmp_path_factory.mktemp("whole"))
    complete = planned(whole)
    upgrade.apply(whole, complete.changes)
    return complete, files(whole)


@pytest.mark.parametrize(
    "chunk", [pytest.param(n, marks=pytest.mark.shard_slot(n + 6)) for n in range(STOP_CHUNKS)]
)
def test_an_upgrade_that_stops_after_any_file_is_finished_by_running_it_again(
    tmp_path: Path, whole_upgrade: tuple[upgrade.Plan, dict[str, str]], chunk: int
) -> None:
    complete, settled = whole_upgrade
    order = upgrade.write_order(complete.changes)
    assert order[-1] == str(gatelib.BINDING)
    for stopped in range(chunk, len(order), STOP_CHUNKS):
        root = fixture("0.8.2", tmp_path / f"stopped-{stopped}")
        partial = {name: complete.changes[name] for name in order[:stopped]}
        upgrade.apply(root, partial)
        upgrade.apply(root, planned(root).changes)
        assert files(root) == settled, f"stopped after {stopped} files"


def keeps_in_order(before: str, after: str) -> bool:
    rest = iter(after.splitlines())
    return all(line in rest for line in before.splitlines())


@pytest.mark.parametrize("name", ["0.8.2", "0.9.2"])
def test_what_the_project_wrote_survives_the_upgrade(name: str, tmp_path: Path) -> None:
    root = fixture(name, tmp_path)
    (root / "justfile").write_text((root / "justfile").read_text() + PROJECT_RECIPE)
    binding = (root / BINDING).read_text()
    gates = markdown_text.table(binding, "## Gates")
    assert gates is not None
    binding = binding.replace("\n".join(gates.lines), "\n".join(gates.render(gates.rows)))
    binding = binding.replace("| tier0 | `just tier0` |\n", f"| tier0 | `just tier0` |\n{ENTRY}")
    (root / BINDING).write_text(binding)
    settings = root / ".claude/settings.json"
    settings.write_text(settings.read_text().replace('"deny": [', f'"deny": [\n      "{DENY}",'))
    repos.git(root, "add", "-A")
    repos.git(root, "commit", "-q", "-m", "chore: the project's own")
    before = {name: (root / name).read_text() for name in files(root)}
    result = planned(root)
    upgrade.apply(root, result.changes)
    after = {name: (root / name).read_text() for name in files(root)}
    assert PROJECT_STEP in after[".github/workflows/deep.yml"]
    assert PROJECT_BITE in after["justfile"]
    assert PROJECT_RECIPE in after["justfile"]
    assert ENTRY in after[BINDING]
    kept, now = (markdown_text.table(text[BINDING], "## Gates") for text in (before, after))
    assert kept is not None
    assert now is not None
    rows = iter(now.rows)
    assert all(row in rows for row in kept.rows), "every row the project wrote, in its order"
    assert DENY in after[".claude/settings.json"]
    assert [f for f in result.owner if DENY in str(f)]
    names = conformance.companions()
    plugin_written = {
        line.rstrip("\r\n")
        for item in conformance.companion_items(before["AGENTS.md"], names)
        for line in item.lines
    }
    project_written = "\n".join(
        line for line in before["AGENTS.md"].splitlines() if line not in plugin_written
    )
    assert keeps_in_order(project_written, after["AGENTS.md"]), "AGENTS.md"
    assert all(conformance.plain_companion(name) in after["AGENTS.md"] for name in names)
    template = ".github/pull_request_template.md"
    assert keeps_in_order(before[template], after[template]), template


def test_a_padded_table_is_padded_again_with_the_new_row(tmp_path: Path) -> None:
    root = fixture("0.8.2", tmp_path)
    binding = (root / BINDING).read_text()
    shipped = markdown_text.table(binding, "## Entry points")
    assert shipped is not None
    (root / BINDING).write_text(
        binding.replace("\n".join(shipped.lines), "\n".join(shipped.render(shipped.rows)))
    )
    upgrade.apply(root, planned(root).changes)
    after = markdown_text.table((root / BINDING).read_text(), "## Entry points")
    assert after is not None
    assert after.padded
    assert {"tier0-tree", "merge"} <= {row[0] for row in after.rows}


@pytest.mark.parametrize("name", FIXTURES)
def test_the_first_recipe_is_the_one_it_was_and_just_can_read_the_justfile(
    name: str, tmp_path: Path
) -> None:
    root = fixture(name, tmp_path)

    def first(text: str) -> str:
        return next(b.name for b in justfile_text.blocks(text.split("\n")) if b.parsed)

    before = first((root / "justfile").read_text())
    upgrade.apply(root, planned(root).changes)
    assert first((root / "justfile").read_text()) == before
    if shutil.which("just"):
        done = subprocess.run(  # noqa: S603 # -- a fixed argv
            ["just", "--justfile", str(root / "justfile"), "--summary"],  # noqa: S607
            capture_output=True,
            text=True,
            check=False,
        )
        assert done.returncode == 0, done.stderr


def test_the_dropped_gates_rows_appear_without_a_types_line_and_the_line_adds_none(
    tmp_path: Path,
) -> None:
    root = fixture("0.8.2", tmp_path)

    def dropped(root: Path) -> set[str]:
        return {f.problem for f in conformance.findings(root) if "Gates table has no" in f.problem}

    without = dropped(root)
    assert without
    binding = (root / BINDING).read_text()
    (root / BINDING).write_text(binding.replace("## Gates", "- Application types: cli\n\n## Gates"))
    assert dropped(root) == without


def flip_flop(root: Path) -> list[conformance.Finding]:
    text = (root / "AGENTS.md").read_text()
    if text.endswith("left\n"):
        right = conformance.Edit("lean right", lambda now: f"{now}right\n", conformance.Agent())
        return [conformance.Finding("AGENTS.md", "leans left", right)]
    left = conformance.Edit("lean left", lambda now: f"{now}left\n", conformance.Agent())
    return [conformance.Finding("AGENTS.md", "does not lean left", left)]


def test_a_plan_that_never_settles_is_stuck_and_touches_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = fixture("0.9.2", tmp_path)
    before = files(root)
    monkeypatch.setattr(conformance, "CHECKS", (flip_flop,))
    assert upgrade.main(["upgrade.py", "--root", str(root), "--apply"]) == 3
    out = capsys.readouterr().out
    assert "does not settle, which is a bug in the plugin; nothing was written" in out
    assert "AGENTS.md: " in out
    assert files(root) == before


def test_an_edit_that_keeps_changing_a_file_without_clearing_its_finding_is_never_planned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = fixture("0.9.2", tmp_path)

    def check(_: Path) -> list[conformance.Finding]:
        grows = conformance.Edit("grow", lambda text: f"{text or ''}more\n", conformance.Agent())
        return [conformance.Finding("flags.toml", "always more", grows)]

    monkeypatch.setattr(conformance, "CHECKS", (check,))
    result = planned(root)
    assert "flags.toml" not in result.changes
    assert [str(f) for f in result.agent] == ["flags.toml: always more"]


@pytest.mark.parametrize(
    ("label", "mutate"),
    conformance.mutations(),
    ids=[label for label, _ in conformance.mutations()],
)
def test_no_auto_finding_the_self_test_can_reach_is_stuck(
    label: str, mutate: conformance.Mutation, tmp_path: Path
) -> None:
    root = tmp_path / "repo"
    shutil.copytree(pristine(), root, symlinks=True)
    mutate(root)
    shutil.copytree(repos.skeleton(), root / ".git", symlinks=True)
    repos.git(root, "add", "-A")
    result = upgrade.plan(root, PLUGIN_ROOT)
    assert isinstance(result, upgrade.Plan), (label, result)
    upgrade.apply(root, result.changes)
    assert counts(root)[0] == 0


def plugin_at(root: Path, version: str) -> Path:
    repos.repository(root, {".claude-plugin/plugin.json": f'{{"version": "{version}"}}'})
    return root


def pin_to(root: Path, version: str, commit: str) -> None:
    binding = root / BINDING
    binding.write_text(
        plugin_pin.with_pin(f"- Ratchet plugin: {version} at {commit}")(binding.read_text())
    )


def test_a_plugin_older_than_the_pin_is_refused(tmp_path: Path) -> None:
    root = fixture("0.9.2", tmp_path)
    pin_to(root, "99.0.0", "a" * 40)
    before = files(root)
    result = upgrade.plan(root, PLUGIN_ROOT)
    assert isinstance(result, upgrade.Refused)
    assert "older than the 99.0.0 at " in result.reason
    assert files(root) == before


def test_a_plugin_whose_order_against_the_pin_cannot_be_told_is_refused(tmp_path: Path) -> None:
    used = plugin_at(tmp_path / "plugin", "1.2.3")
    root = fixture("0.9.2", tmp_path)
    pin_to(root, "1.2.3", "a" * 40)
    result = upgrade.plan(root, used)
    assert isinstance(result, upgrade.Refused)
    assert "which is newer than the 1.2.3 at " in result.reason


def test_a_plugin_copy_without_history_is_refused(tmp_path: Path) -> None:
    used = tmp_path / "cache/ratchet"
    repos.write(used, ".claude-plugin/plugin.json", '{"version": "9.9.9"}')
    result = upgrade.plan(fixture("0.9.2", tmp_path), used)
    assert isinstance(result, upgrade.Refused)
    assert "has no commit to pin" in result.reason


def test_a_directory_that_is_not_a_git_tree_or_has_no_binding_is_refused(tmp_path: Path) -> None:
    result = upgrade.plan(tmp_path / "loose", PLUGIN_ROOT)
    assert isinstance(result, upgrade.Refused)
    assert "is not a git work tree" in result.reason
    bare = repos.repository(tmp_path / "bare")
    result = upgrade.plan(bare, PLUGIN_ROOT)
    assert isinstance(result, upgrade.Refused)
    assert "docs/process/binding.md is missing" in result.reason


def test_a_refusal_exits_2_and_a_stuck_plan_exits_3_with_nothing_written(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = fixture("0.9.2", tmp_path)
    pin_to(root, "99.0.0", "a" * 40)
    before = files(root)
    assert upgrade.main(["upgrade.py", "--root", str(root), "--apply"]) == 2
    assert capsys.readouterr().out.startswith("upgrade: refused. the plugin in use is ")
    assert files(root) == before


def test_a_file_named_outside_the_tree_is_never_written_and_is_left_to_an_agent(
    tmp_path: Path,
) -> None:
    root = fixture("0.9.2", tmp_path)
    shutil.rmtree(root / ".github/pull_request_template.md", ignore_errors=True)
    (root / ".github/pull_request_template.md").unlink()
    replace(BINDING, "- Pull request template: `.github/pull_request_template.md`", "")(root)
    binding = root / BINDING
    binding.write_text(binding.read_text() + "\n- Change template: `../outside.md`\n")
    result = planned(root)
    assert [name for name in result.changes if name.startswith("..")] == []
    assert "../outside.md" in [finding.path for finding in result.agent]
    upgrade.apply(root, result.changes)
    assert not (tmp_path / "outside.md").exists()


def test_a_symlinked_file_is_edited_where_it_lands_and_the_link_stays(tmp_path: Path) -> None:
    root = fixture("0.9.2", tmp_path)
    text = (root / "AGENTS.md").read_text().replace("## Asking the owner", "## Asking")
    (root / "AGENTS.md").unlink()
    (root / "CLAUDE.md").write_text(text)
    (root / "AGENTS.md").symlink_to("CLAUDE.md")
    repos.git(root, "add", "-A")
    result = planned(root)
    assert "CLAUDE.md" in result.changes
    assert "AGENTS.md" not in result.changes
    upgrade.apply(root, result.changes)
    assert (root / "AGENTS.md").is_symlink()
    assert "## Asking the owner" in (root / "AGENTS.md").read_text()


def test_a_link_to_a_file_outside_the_tree_is_refused(tmp_path: Path) -> None:
    root = fixture("0.9.2", tmp_path)
    outside = tmp_path / "outside.md"
    outside.write_text("# Instructions\n\nload ratchet:method\n")
    (root / "AGENTS.md").unlink()
    (root / "AGENTS.md").symlink_to(outside)
    repos.git(root, "add", "-A")
    result = upgrade.plan(root, PLUGIN_ROOT)
    assert isinstance(result, upgrade.Refused)
    assert "AGENTS.md leads outside the repository" in result.reason
    assert outside.read_text() == "# Instructions\n\nload ratchet:method\n"


def test_a_file_with_crlf_endings_keeps_them(tmp_path: Path) -> None:
    root = fixture("0.9.2", tmp_path)
    path = root / "AGENTS.md"
    path.write_bytes(
        path.read_text().replace("## Asking the owner", "## Asking").replace("\n", "\r\n").encode()
    )
    upgrade.apply(root, planned(root).changes)
    data = path.read_bytes()
    assert b"## Asking the owner" in data
    assert data.count(b"\n") == data.count(b"\r\n")


def test_edited_gives_none_for_an_unchanged_file_and_bytes_for_a_changed_or_absent_one() -> None:
    same = gatelib.created("x")
    assert gatelib.edited(same, b"kept\n") is None
    assert gatelib.edited(same, None) == b"x"
    assert gatelib.edited(lambda text: (text or "") + "b\n", b"a\n") == b"a\nb\n"
    assert gatelib.edited(lambda text: (text or "") + "b\n", b"a\r\n") == b"a\r\nb\r\n"
    assert gatelib.edited(lambda text: (text or "") + "b\n", b"a\r\nc\n") == b"a\r\nc\nb\n"


def test_a_written_file_keeps_its_mode_and_a_new_one_is_not_executable(tmp_path: Path) -> None:
    kept = tmp_path / "scripts/run"
    kept.parent.mkdir()
    kept.write_text("old\n")
    kept.chmod(0o755)
    holder = tmp_path / ".ratchet/upgrade"
    holder.mkdir(parents=True)
    upgrade.write_atomically(kept, b"new\n", holder)
    upgrade.write_atomically(tmp_path / "docs/new.md", b"fresh\n", holder)
    assert kept.read_text() == "new\n"
    assert os.access(kept, os.X_OK)
    assert (tmp_path / "docs/new.md").stat().st_mode & 0o777 == 0o644
    assert sorted(path.name for path in tmp_path.rglob(".upgrade-*")) == []


def test_the_binding_is_written_last() -> None:
    changes = {
        str(gatelib.BINDING): upgrade.Change(None, b"b"),
        "a.md": upgrade.Change(None, b"a"),
        "z.md": upgrade.Change(None, b"z"),
    }
    assert upgrade.write_order(changes) == ["a.md", "z.md", str(gatelib.BINDING)]


def test_only_what_git_sees_is_copied_and_a_link_stays_a_link(tmp_path: Path) -> None:
    root = repos.repository(
        tmp_path / "repo", {"kept.md": "a", "gone.md": "b", ".gitignore": "ignored.txt\n"}
    )
    (root / "gone.md").unlink()
    (root / "ignored.txt").write_text("secret")
    (root / "new.md").write_text("c")
    (root / "link.md").symlink_to("kept.md")
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    assert gatelib.copy_seen(root, sandbox) == {}
    assert sorted(
        str(p.relative_to(sandbox))
        for p in sandbox.rglob("*")
        if (p.is_file() or p.is_symlink()) and ".git" not in p.parts
    ) == [".gitignore", "kept.md", "link.md", "new.md"]
    assert (sandbox / "link.md").is_symlink()
    assert (sandbox / ".git").is_dir()


ABSENT = re.compile(r"\('(.+?)' absent\)")
BEFORE_ISSUES_CLOSE = "d2b36ab^"
BEFORE_TIER_1_READ = "903d902"
BEFORE_KEPT_BRANCH = "2e9dbe4"
KEPT = "base=$branch"
MERGE = ".github/workflows/merge.yml"


@pytest.mark.parametrize("name", ["0.8.2", "0.9.2"])
def test_an_upgrade_lists_a_finding_for_each_marker_the_old_workflows_lack(
    name: str, tmp_path: Path
) -> None:
    root = fixture(name, tmp_path)
    upgrade.apply(root, planned(root).changes)
    found = [str(f) for f in conformance.findings(root)]
    for workflow in ("ci.yml", "deep.yml"):
        path = f".github/workflows/{workflow}"
        text = (root / path).read_text(encoding="utf-8")
        owed = {m for m in conformance.workflow_markers(workflow) if m not in text}
        named = {
            marker
            for line in found
            if line.startswith(f"{path}: nothing ")
            for marker in ABSENT.findall(line)
        }
        assert named == owed


def test_a_merge_workflow_from_before_it_closed_issues_is_owed_every_marker_since(
    tmp_path: Path,
) -> None:
    root = fixture("0.9.2", tmp_path)
    upgrade.apply(root, planned(root).changes)
    template = (root / MERGE).read_text(encoding="utf-8")
    old = adopted.history("show", f"{BEFORE_ISSUES_CLOSE}:skills/adopt/templates/{MERGE}")
    commit = re.search(r"ref: \"([0-9a-f]{40})\"", template)
    assert commit
    (root / MERGE).write_text(old.replace("<ratchet-sha>", commit.group(1)) + "\n")
    found = [f.problem for f in conformance.findings(root) if f.path == MERGE]
    assert found == [
        f"nothing {meaning} ('{marker}' absent)"
        for marker, meaning in conformance.MERGE_SHAPE
        if marker in {"--tier1", "issues: write", "closingIssuesReferences", KEPT}
    ]


def test_a_merge_workflow_from_before_it_read_tier_1_on_the_default_branch_is_owed_that_read(
    tmp_path: Path,
) -> None:
    root = fixture("0.9.2", tmp_path)
    upgrade.apply(root, planned(root).changes)
    template = (root / MERGE).read_text(encoding="utf-8")
    old = adopted.history("show", f"{BEFORE_TIER_1_READ}:skills/adopt/templates/{MERGE}")
    commit = re.search(r"ref: \"([0-9a-f]{40})\"", template)
    assert commit
    (root / MERGE).write_text(old.replace("<ratchet-sha>", commit.group(1)) + "\n")
    found = [f.problem for f in conformance.findings(root) if f.path == MERGE]
    assert f"nothing {dict(conformance.MERGE_SHAPE)['--tier1']} ('--tier1' absent)" in found


def test_a_merge_workflow_from_before_it_kept_a_targeted_branch_is_given_the_template_s_step(
    tmp_path: Path,
) -> None:
    root = fixture("0.9.2", tmp_path)
    upgrade.apply(root, planned(root).changes)
    commit = re.search(r"ref: \"([0-9a-f]{40})\"", (root / MERGE).read_text(encoding="utf-8"))
    assert commit
    old = adopted.history("show", f"{BEFORE_KEPT_BRANCH}:skills/adopt/templates/{MERGE}")
    (root / MERGE).write_text(old.replace("<ratchet-sha>", commit.group(1)) + "\n")
    found = [f.problem for f in conformance.findings(root) if f.path == MERGE]
    assert found == [f"nothing {dict(conformance.MERGE_SHAPE)[KEPT]} ('{KEPT}' absent)"]
    upgrade.apply(root, planned(root).changes)
    done = (root / MERGE).read_text(encoding="utf-8")
    template = conformance.template_file(MERGE).replace("<ratchet-sha>", commit.group(1))
    assert done.rstrip("\n") == template.rstrip("\n")
    assert not [f for f in conformance.findings(root) if f.path == MERGE]
    assert not [e for e in owed.read(root).entries if e.path == MERGE]


def test_an_upgrade_owes_the_bite_all_ceiling_a_project_adopted_before_it_lacks(
    tmp_path: Path,
) -> None:
    root = fixture("0.9.2", tmp_path)
    path = root / "quality/thresholds.toml"
    path.write_text(budget.with_ceiling(path.read_text(encoding="utf-8"), "tier2", 600.0))
    repos.git(root, "commit", "-q", "-am", "chore(quality): the first ceilings")
    repos.branch(root, "upgrade")
    assert upgrade.main(["upgrade.py", "--root", str(root), "--apply"]) == 0
    entries = [e for e in owed.read(root).entries if e.rule == "bite_all_ceiling"]
    assert [(e.path, e.since) for e in entries] == [
        ("quality/thresholds.toml", plugin_pin.version_of(PLUGIN_ROOT))
    ]


def test_the_upgrade_report_shows_what_the_missing_bite_all_ceiling_costs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = fixture("0.9.2", tmp_path)
    path = root / "quality/thresholds.toml"
    path.write_text(budget.with_ceiling(path.read_text(encoding="utf-8"), "tier2", 600.0))
    repos.git(root, "commit", "-q", "-am", "chore(quality): the first ceilings")
    repos.branch(root, "upgrade")
    assert upgrade.main(["upgrade.py", "--root", str(root), "--apply"]) == 0
    report = capsys.readouterr().out
    assert "held to tier 2's ceiling of 600s and goes red when it runs longer" in report


def test_the_report_says_where_from_where_to_what_was_written_and_what_is_left(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = fixture("0.8.2", tmp_path)
    assert upgrade.main(["upgrade.py", "--root", str(root)]) == 1
    out = capsys.readouterr().out
    first = out.splitlines()[0]
    assert first.startswith(f"upgrade: {root} from 0.8.2 at {adopted.RELEASES['0.8.2'][:12]} (CI")
    assert f"to {plugin_pin.in_use(PLUGIN_ROOT)}." in first
    assert "Would write 13 files (run with --apply):" in out
    assert "  quality/owed.toml\n    - write down 105 things owed since " in out
    assert "  .claude/settings.json\n    - regenerate the permission rules" in out
    assert f"    - pinned: {plugin_pin.line_for(PLUGIN_ROOT)}" in out
    assert out.index("\n  docs/process/binding.md\n") > out.index("\n  justfile\n")
    assert "Owed, none of them red, for an agent to work through (quality/owed.toml):" in out
    assert "\n  justfile: a guard path (group `gate-commands`)" in out
    assert "?" not in plain(out), "the upgrade asks the owner nothing"
    assert "    - has no '- Life events:' line;" in out
    assert f"        assumed: {conformance.UNSTATED_STAGE}" in out
    assert "        settled by the owner's answer to: Which of these has already happened" in out
    assert "        seen: no file in the tree is a sign of a life event" in out
    assert "        then: `deployed` owes the Gates rows " in out
    assert verdict(out).startswith("upgrade: would write 81 edits in 13 files. 105 items are owed")
    assert "and 0 red; 4 of them hold an assumption" in verdict(out)
    assert "The change touches guard paths" in verdict(out)
    assert plain(out) == "\n".join(
        [
            "In plain words:",
            (
                "This project would move from Ratchet 0.8.2 to"
                f" {plugin_pin.version_of(PLUGIN_ROOT)}. The upgrade would make 81 changes in 13"
                " files. The project owes 105 things since the upgrade. None of them turns a check"
                " red or stops a merge, and between upgrades the list only gets shorter. Nothing"
                " fixes them by itself: ask an agent to. Each is in a guard path: an agent can"
                " make all the fixes in one change on a branch, and you decide at that one merge."
            ),
            (
                "Checks that do not run until then: the weekly checks, changed tests run,"
                " configuration hardening, formal verification of concurrency, formal"
                " verification of state, formal verification of untrusted input, load smoke,"
                " refuses bad configuration, rolls back"
                " safely, shuts down gracefully,"
                " survives failing dependencies, telemetry emitted, the dependency-only"
                " judgement of guard-diff and the merge judge, which sends every dependency change"
                " to the owner until the section is filled, the suppressions gate and the coverage"
                " gate's search for exclusions on absolute paths, which count nothing until the"
                " section is filled, the comments gate, which judges nothing until its lister is"
                " bound, the route's proposal of a life event it sees a sign of,"
                " which waits for the table, the pins gate's reading of the project's image"
                " files, which reads none until filled, the check that a fresh checkout has the"
                " tools the recipes run."
            ),
            "Assumed until you say otherwise:",
            (
                f"- {conformance.UNSTATED_STAGE} The checks such a project needs do not run until"
                " you say it is one: active security scan, load, telemetry arrives, backup"
                " restores, production answers."
            ),
            f"- {conformance.NO_DOOR}",
            (
                "Commit this on main; to commit it on another branch, run the upgrade again there"
                " first."
            ),
            "Your one step is the merge.",
        ]
    )


@pytest.mark.parametrize("ledger", [None, ""])
def test_a_repository_that_conforms_is_told_so_and_exits_0_once_the_pin_has_moved(
    ledger: str | None,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = repos.repository(tmp_path / "repo")
    shutil.copytree(pristine(), root, symlinks=True, dirs_exist_ok=True)
    if ledger is not None:
        repos.write(root, "quality/owed.toml", ledger)
    repos.git(root, "add", "-A")
    assert upgrade.main(["upgrade.py", "--root", str(root), "--apply"]) == 0
    out = capsys.readouterr().out
    assert "Wrote 5 files:" in out
    assert "upgrade: wrote 5 edits in 5 files. 0 items are owed and 0 red" in out
    assert "Commit this" not in out, "a plan that leaves no owed entry names no branch"
    assert (root / "quality/owed.toml").exists() == (ledger is not None)
    assert upgrade.main(["upgrade.py", "--root", str(root)]) == 0
    out = capsys.readouterr().out
    assert "Would write" not in out
    assert verdict(out) == (
        "upgrade: nothing to write. 0 items are owed and 0 red; 0 of them hold an assumption"
        " until the owner says otherwise."
    )
    assert plain(out) == (
        "In plain words:\nThis project is on Ratchet"
        f" {plugin_pin.version_of(PLUGIN_ROOT)}, and the upgrade has nothing to change."
    )


def test_a_change_to_no_guard_path_does_not_send_the_owner_to_the_merge_judge(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = repos.repository(tmp_path / "repo")
    shutil.copytree(pristine(), root, symlinks=True, dirs_exist_ok=True)
    repos.git(root, "add", "-A")
    assert upgrade.main(["upgrade.py", "--root", str(root), "--apply"]) == 0
    (root / "docs/features/.gitkeep").unlink()
    repos.git(root, "add", "-A")
    capsys.readouterr()
    assert upgrade.main(["upgrade.py", "--root", str(root)]) == 1
    assert verdict(capsys.readouterr().out) == (
        "upgrade: would write 1 edits in 1 files. 0 items are owed and 0 red; 0 of them hold an"
        " assumption until the owner says otherwise."
    )


def test_where_the_repository_runs_the_plugin_from_is_told(tmp_path: Path) -> None:
    root = fixture("half-done", tmp_path)
    assert upgrade.standing(root, PLUGIN_ROOT) == (
        f"0.9.2 at {adopted.RELEASES['0.9.2'][:12]}"
    ) or upgrade.standing(root, PLUGIN_ROOT).startswith("0.9.2 at ")
    unpinned = fixture("0.8.2", tmp_path / "unpinned")
    assert upgrade.standing(unpinned, PLUGIN_ROOT).endswith(
        "(CI checks it out; the binding pins nothing)"
    )
    binding = unpinned / BINDING
    binding.write_text(binding.read_text())
    workflow = unpinned / ".github/workflows/deep.yml"
    workflow.write_text(workflow.read_text().replace(adopted.RELEASES["0.8.2"], "e" * 40))
    assert "(CI checks them all out; the binding pins nothing)" in upgrade.standing(
        unpinned, PLUGIN_ROOT
    )
    for name in (
        ".github/workflows/ci.yml",
        ".github/workflows/deep.yml",
        ".github/workflows/merge.yml",
        ".github/workflows/weekly.yml",
    ):
        (unpinned / name).unlink(missing_ok=True)
    assert upgrade.standing(unpinned, PLUGIN_ROOT) == "no plugin pinned"


def test_a_commit_the_plugin_history_lacks_has_no_version() -> None:
    assert upgrade.version_at(PLUGIN_ROOT, "f" * 40) == ""
    assert upgrade.version_at(PLUGIN_ROOT, str(PLUGIN_COMMIT)) != ""


def test_who_edits_a_file_follows_the_manifest(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    shutil.copytree(pristine(), repo, symlinks=True)
    assert upgrade.editor(repo, ".claude/settings.json") == (
        "the owner edits it by hand: its guard group `harness` denies agents"
    )
    assert upgrade.editor(repo, "docs/adr") == (
        "a record (group `records`): an addition passes, a change goes to the owner"
    )
    assert upgrade.editor(repo, "docs/features") == "not a guard path: an agent edits it"
    assert upgrade.editor(repo, "AGENTS.md (or CLAUDE.md)").startswith("a guard path")
    (repo / MANIFEST).write_text("not = [toml")
    assert upgrade.editor(repo, "justfile") == "guards/manifest.toml does not say who edits it"
    (repo / MANIFEST).unlink()
    assert upgrade.editor(repo, "justfile") == "guards/manifest.toml does not say who edits it"


def test_each_kind_of_fix_prints_what_it_asks_or_starts_from() -> None:
    assert upgrade.describe(conformance.Agent(("| a | b |", ""), ("bind it",))) == [
        "        | a | b |",
        "",
        "        then: bind it",
    ]
    assert upgrade.describe(
        conformance.Owner("is it live?", "it is not yet", ("a Dockerfile",), ("owes a row",))
    ) == [
        "        assumed: it is not yet",
        "        settled by the owner's answer to: is it live?",
        "        seen: a Dockerfile",
        "        then: owes a row",
    ]


def test_without_arguments_it_reads_the_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(fixture("0.9.2", tmp_path))
    assert upgrade.main(["upgrade.py"]) == 1
    assert capsys.readouterr().out.startswith("upgrade: ")


def test_the_plan_reads_the_plugin_the_script_runs_from(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    used = plugin_at(tmp_path / "plugin", "999.0.0")
    monkeypatch.setattr(plugin_pin, "PLUGIN", used)
    root = fixture("0.9.2", tmp_path)
    assert upgrade.main(["upgrade.py", "--root", str(root)]) == 1
    assert f"to 999.0.0 at {repos.git(used, 'rev-parse', 'HEAD')}." in capsys.readouterr().out


def test_an_unfiltered_changed_recipe_is_red_in_the_summary_and_not_owed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = fixture("0.8.2", tmp_path)
    script = '"{{ratchet_plugin}}/scripts/mutation_survivors.py"'
    recipe = (
        f'files="$(python3 {script} --tool made-up --changed)"; '
        '[ -z "$files" ] && exit 0; made-up run --mutate "$files"'
    )
    path = root / "justfile"
    path.write_text(f"{path.read_text()}\nmutation-own:\n    {recipe}\n", encoding="utf-8")
    repos.git(root, "add", "-A")
    assert upgrade.main(["upgrade.py", "--root", str(root), "--apply"]) == 1
    out = capsys.readouterr().out
    assert "and 1 red;" in verdict(out)
    assert "and 0 red" not in verdict(out)
    assert "Red until fixed" in out
    assert "    - a recipe hands the output of mutation_survivors.py --changed" in out
    assert "1 thing stops a merge until fixed." in plain(out)


READERS = ("test", "coverage", "diff-coverage", "test-counts")


def bound_readers(root: Path) -> None:
    path = root / "justfile"
    text = path.read_text(encoding="utf-8")
    for gate in READERS:
        pattern = re.compile(
            rf'^({re.escape(gate)}:[^\n]*\n    )@echo "bind:[^\n]*; exit 1\n(?:    [^\n]*\n)*',
            re.MULTILINE,
        )
        text, count = pattern.subn(rf"\1@echo ran-{gate}\n", text, count=1)
        assert count == 1, gate
    path.write_text(text, encoding="utf-8")
    repos.git(root, "commit", "-q", "-am", "feat(tests): bind the suite gates")


def test_an_upgrade_leaves_bound_suite_gates_as_they_are_and_owes_the_producer_first(
    tmp_path: Path,
) -> None:
    root = fixture("0.12.3", tmp_path)
    bound_readers(root)
    repos.branch(root, "upgrade")
    text = (root / "justfile").read_text(encoding="utf-8")
    before = {gate: justfile_text.definition(text, gate)[1:] for gate in READERS}
    assert all(f"@echo ran-{gate}" in "\n".join(body) for gate, body in before.items())
    assert upgrade.main(["upgrade.py", "--root", str(root), "--apply"]) == 0
    after_text = (root / "justfile").read_text(encoding="utf-8")
    assert {gate: justfile_text.definition(after_text, gate)[1:] for gate in READERS} == before
    assert justfile_text.defines_recipe(after_text, "suite-report")
    producer = "\n".join(justfile_text.definition(after_text, "suite-report"))
    assert "suite-report" not in after_text.replace(producer, "")
    red, _ = conformance.owed_split(root, conformance.findings(root))
    assert red == []
    owing = [e.problem for e in owed.read(root).entries if e.rule == "shared_reports"]
    assert len(owing) == 1
    assert "bind `suite-report` first" in owing[0]
    assert all(f"`{gate}`" in owing[0] for gate in READERS)


SUITE_RECIPES = ("test", "e2e")


def bound_suites(root: Path) -> None:
    path = root / "justfile"
    text = path.read_text(encoding="utf-8")
    for gate in SUITE_RECIPES:
        pattern = re.compile(
            rf'^({re.escape(gate)}(?: [*+]\w+)?:[^\n]*\n    )@echo "bind:[^\n]*; exit 1\n',
            re.MULTILINE,
        )
        text, count = pattern.subn(rf"\1@echo ran-{gate}\n", text, count=1)
        assert count == 1, gate
    path.write_text(text, encoding="utf-8")
    repos.git(root, "commit", "-q", "-am", "feat(tests): bind the suites")


def test_an_upgrade_leaves_bound_suites_green_and_owes_the_gate_and_the_files_taking_bind(
    tmp_path: Path,
) -> None:
    root = fixture("0.12.3", tmp_path)
    bound_suites(root)
    repos.branch(root, "upgrade")
    text = (root / "justfile").read_text(encoding="utf-8")
    before = {gate: justfile_text.definition(text, gate)[1:] for gate in SUITE_RECIPES}
    assert upgrade.main(["upgrade.py", "--root", str(root), "--apply"]) == 0
    after_text = (root / "justfile").read_text(encoding="utf-8")
    assert {
        gate: justfile_text.definition(after_text, gate)[1:] for gate in SUITE_RECIPES
    } == before
    assert justfile_text.defines_recipe(after_text, "tests-changed")
    recipe = "\n".join(justfile_text.definition(after_text, "tests-changed"))
    assert "changed_tests.py" in recipe
    red, _ = conformance.owed_split(root, conformance.findings(root))
    assert red == []
    owing = [e.problem for e in owed.read(root).entries if e.rule == "changed_tests"]
    assert len(owing) == 1
    assert owing[0].startswith("'## Tests' has no suites table (Suite | Gate | Files | Run files).")
    assert "changed tests run" in "\n".join(e.problem for e in owed.read(root).entries)


@pytest.mark.parametrize("name", ["0.8.2", "0.9.2"])
def test_an_upgrade_stays_green_and_owes_the_incremental_line_without_blocking_a_feature(
    name: str, tmp_path: Path
) -> None:
    root = fixture(name, tmp_path)
    repos.branch(root, "upgrade")
    assert upgrade.main(["upgrade.py", "--root", str(root), "--apply"]) == 0
    red, _ = conformance.owed_split(root, conformance.findings(root))
    assert red == []
    owing = [e for e in owed.read(root).entries if "'- Incremental:'" in e.problem]
    assert [e.rule for e in owing] == ["speed"]
    assert owing[0].path == "docs/process/binding.md"
    assert owing[0].rule not in owed.FILL_FIRST


@pytest.mark.parametrize("name", ["0.8.2", "0.9.2"])
def test_an_upgrade_carries_the_incremental_variable_and_owes_the_recipe_its_use(
    name: str, tmp_path: Path
) -> None:
    root = fixture(name, tmp_path)
    assert "mutation_incremental" not in (root / "justfile").read_text(encoding="utf-8")
    repos.branch(root, "upgrade")
    assert upgrade.main(["upgrade.py", "--root", str(root), "--apply"]) == 0
    text = (root / "justfile").read_text(encoding="utf-8")
    assert re.search(r"^mutation_incremental := if env_var_or_default\(\"CI\"", text, re.MULTILINE)
    assert 'env_var_or_default("RATCHET_BITING"' in text
    binding = (root / "docs/process/binding.md").read_text(encoding="utf-8")
    (root / "docs/process/binding.md").write_text(
        re.sub(
            r"^(- Shards: .*)$",
            r"\1\n- Incremental: --mode-on",
            binding,
            count=1,
            flags=re.MULTILINE,
        ),
        encoding="utf-8",
    )
    bound = re.sub(r'"bind: the mutation tool[^"]*"', '"--mode-on"', text)
    recipe = "\nmutation-changed:\n    ./run-mutants --shard {{shard}}\n"
    head = re.sub(r"\nmutation-changed:\n(?:    .*\n?)+", "", bound)
    (root / "justfile").write_text(head + recipe, encoding="utf-8")
    problems = [
        f.problem for f in conformance.findings(root) if "mutation_incremental" in f.problem
    ]
    assert len(problems) == 1
    assert problems[0].startswith("the recipe `mutation-changed` does not use")


@pytest.mark.parametrize("name", ["0.8.2", "0.9.2"])
def test_an_upgrade_owes_the_comments_section_and_is_never_red_for_a_comment_that_exists(
    name: str,
    tmp_path: Path,
    *,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = fixture(name, tmp_path)
    repos.write(root, "src/old.txt", "x // a reason from before\n")
    repos.commit(root, "feat(core): a file with a comment")
    repos.branch(root, "upgrade")
    assert upgrade.main(["upgrade.py", "--root", str(root), "--apply"]) == 0
    red, _ = conformance.owed_split(root, conformance.findings(root))
    assert red == []
    section = [e for e in owed.read(root).entries if e.rule == "comments_section"]
    assert section
    assert {e.path for e in section} == {"docs/process/binding.md"}
    assert all(e.rule in owed.FILL_FIRST for e in section)
    text = (root / "justfile").read_text(encoding="utf-8")
    assert justfile_text.defines_recipe(text, "comments")
    assert justfile_text.defines_recipe(text, comments.LISTER)
    assert "comments" in justfile_text.reaches(text, "tier0")
    binding = (root / "docs/process/binding.md").read_text(encoding="utf-8")
    assert "| no prose comments |" in binding
    capsys.readouterr()
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    assert comments.main(["comments", "--root", str(root), "--changed"]) == 0
    assert "the lister is not bound yet, so nothing is judged" in capsys.readouterr().out


@pytest.mark.parametrize("name", ["0.8.2", "0.9.2"])
def test_an_upgrade_owes_the_documents_section_and_is_never_red_for_a_file_of_no_kind(
    name: str, tmp_path: Path
) -> None:
    root = fixture(name, tmp_path)
    assert upgrade.main(["upgrade.py", "--root", str(root), "--apply"]) == 0
    red, _ = conformance.owed_split(root, conformance.findings(root))
    assert red == []
    owing = [e for e in owed.read(root).entries if e.rule == "documents"]
    assert owing
    assert {e.path for e in owing} == {"docs/process/binding.md"}
    assert all(e.rule in owed.FILL_FIRST for e in owing)
    binding = (root / "docs/process/binding.md").read_text(encoding="utf-8")
    assert "## Documents" in binding
    stray = documents.unkinded(gatelib.tracked_paths(root), documents.kinds_of(root))
    assert [e.problem.split("`")[1] for e in owing if "of no kind" in e.problem] == stray
