"""``upgrade``: the plan, the write, and the report, on repositories adoption left behind."""

import hashlib
import os
import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest

import conformance
import gatelib
import justfile_text
import markdown_text
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
    """A copy of an adopted repository for one test to upgrade."""
    source = adopted.half_done() if name == "half-done" else adopted.adopted(name)
    return adopted.copy(source, tmp_path / "repo")


def files(root: Path) -> dict[str, str]:
    """Every file under ``root`` but the repository's own, by path, with a digest of its bytes."""
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file() and ".git" not in path.relative_to(root).parts
    }


def counts(root: Path) -> tuple[int, int, int]:
    """How many findings are auto, agent and owner."""
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
    """The report's line for an agent: the counts."""
    return next(line for line in reversed(report.splitlines()) if line.startswith("upgrade: "))


def plain(report: str) -> str:
    """The report's block for the owner."""
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


def test_an_upgrade_that_stops_after_any_file_is_finished_by_running_it_again(
    tmp_path: Path,
) -> None:
    whole = fixture("0.8.2", tmp_path / "whole")
    complete = planned(whole)
    upgrade.apply(whole, complete.changes)
    order = upgrade.write_order(complete.changes)
    assert order[-1] == str(gatelib.BINDING)
    for stopped in range(len(order)):
        root = fixture("0.8.2", tmp_path / f"stopped-{stopped}")
        partial = {name: complete.changes[name] for name in order[:stopped]}
        upgrade.apply(root, partial)
        upgrade.apply(root, planned(root).changes)
        assert files(root) == files(whole), f"stopped after {stopped} files"


def keeps_in_order(before: str, after: str) -> bool:
    """Whether every line of ``before`` is in ``after``, in the same order."""
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
    for sections in ("AGENTS.md", ".github/pull_request_template.md"):
        assert keeps_in_order(before[sections], after[sections]), sections


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
    """Two edits of one file, each clearing its own finding and raising the other's."""
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
    assert "  quality/owed.toml\n    - write down 83 things owed since " in out
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
    assert verdict(out).startswith("upgrade: would write 62 edits in 13 files. 83 items are owed")
    assert "and 0 red; 4 of them hold an assumption" in verdict(out)
    assert "The change touches guard paths" in verdict(out)
    assert plain(out) == "\n".join(
        [
            "In plain words:",
            (
                "This project would move from Ratchet 0.8.2 to"
                f" {plugin_pin.version_of(PLUGIN_ROOT)}. The upgrade would make 62 changes in 13"
                " files. The project owes 83 things since the upgrade. None of them turns a check"
                " red or stops a merge, and between upgrades the list only gets shorter. Nothing"
                " fixes them by itself: ask an agent to. Each is in a guard path: an agent can"
                " make all the fixes in one change on a branch, and you decide at that one merge."
            ),
            (
                "Checks that do not run until then: the weekly checks, configuration hardening,"
                " load smoke, refuses bad configuration, rolls back safely, shuts down gracefully,"
                " survives failing dependencies, telemetry emitted, the dependency-only"
                " judgement of guard-diff and the merge judge, which sends every dependency change"
                " to the owner until the section is filled, the suppressions gate, which counts"
                " nothing until its section is filled, the check that a fresh checkout has the"
                " tools the recipes run."
            ),
            "Assumed until you say otherwise:",
            (
                f"- {conformance.UNSTATED_STAGE} The checks such a project needs do not run until"
                " you say it is one: active security scan, load, telemetry arrives, backup"
                " restores, production answers."
            ),
            f"- {conformance.NO_DOOR}",
            "Your one step is the merge.",
        ]
    )


def test_a_repository_that_conforms_is_told_so_and_exits_0_once_the_pin_has_moved(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = repos.repository(tmp_path / "repo")
    shutil.copytree(pristine(), root, symlinks=True, dirs_exist_ok=True)
    repos.git(root, "add", "-A")
    assert upgrade.main(["upgrade.py", "--root", str(root), "--apply"]) == 0
    out = capsys.readouterr().out
    assert "Wrote 5 files:" in out
    assert "upgrade: wrote 5 edits in 5 files. 0 items are owed and 0 red" in out
    assert not (root / "quality/owed.toml").exists(), "a project that owes nothing has no ledger"
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
