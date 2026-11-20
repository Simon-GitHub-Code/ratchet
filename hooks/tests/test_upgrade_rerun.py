import atexit
import functools
import json
import shutil
import tempfile
from collections import Counter
from dataclasses import replace
from pathlib import Path

import pytest

import conformance
import gatelib
import owed
import plugin_pin
import upgrade
from tests import adopted
from tests.repos import branch, commit, git, repository, write
from tests.test_owed import (
    COMMENT,
    DIFF_COVERAGE,
    LEDGER,
    OWN_RULE,
    VERSION,
    add,
    drop_row,
    entry,
    fill_lister,
    gate,
    merged_upgrade,
)

FIRST, SECOND, LATER = "998.0.0", "999.0.0", "1000.0.0"

Out = pytest.CaptureFixture[str]


@functools.cache
def plugins() -> dict[str, Path]:
    scratch = Path(tempfile.mkdtemp(prefix="ratchet-rerun-plugins-"))
    atexit.register(shutil.rmtree, scratch, ignore_errors=True)
    return {
        version: repository(
            scratch / version, {".claude-plugin/plugin.json": json.dumps({"version": version})}
        )
        for version in (FIRST, SECOND, LATER)
    }


def upgraded(root: Path, version: str) -> upgrade.Plan:
    result = upgrade.plan(root, plugins()[version])
    assert isinstance(result, upgrade.Plan), result
    upgrade.apply(root, result.changes)
    commit(root, f"chore(method): upgrade the Ratchet plugin to {version}")
    return result


def scratch(name: str) -> Path:
    place = Path(tempfile.mkdtemp(prefix=f"ratchet-rerun-{name}-"))
    atexit.register(shutil.rmtree, place, ignore_errors=True)
    return place / "repo"


@functools.cache
def upgrades(source: str, *versions: str) -> Path:
    base = adopted.adopted("0.9.2") if source == "0.9.2" else held_base()
    root = adopted.copy(base, scratch(f"{source}-{'-'.join(versions)}"))
    branch(root, "rerun")
    for version in versions:
        upgraded(root, version)
    return root


@functools.cache
def held_base() -> Path:
    root = adopted.copy(merged_upgrade(), scratch("held"))
    (root / LEDGER).write_text(owed.render(owed.read(root).entries[1:]), encoding="utf-8")
    commit(root, "chore(method): drop an owed entry by hand")
    return root


@pytest.fixture
def rerun(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(plugin_pin, "PLUGIN", plugins()[SECOND])
    return adopted.copy(upgrades("0.9.2", FIRST, SECOND), tmp_path / "repo")


def lines(out: str, part: str) -> list[str]:
    return [line for line in out.splitlines() if part in line]


@pytest.mark.parametrize("source", ["0.9.2", "held"])
def test_an_upgrade_run_again_on_its_open_branch_owes_what_one_upgrade_from_the_base_owes(
    source: str, monkeypatch: pytest.MonkeyPatch, capsys: Out
) -> None:
    twice, once = upgrades(source, FIRST, SECOND), upgrades(source, SECOND)
    assert (twice / LEDGER).read_text(encoding="utf-8") == (once / LEDGER).read_text(
        encoding="utf-8"
    ), "two upgrades on one open branch owe what one upgrade from the base owes"
    assert owed.read(twice).entries, "the upgrade owes something to compare"
    monkeypatch.setattr(plugin_pin, "PLUGIN", plugins()[SECOND])
    for root in (twice, once):
        code, out = gate(root, capsys)
        assert code == 0, out
    again = upgrade.plan(twice, plugins()[SECOND])
    assert isinstance(again, upgrade.Plan)
    assert again.changes == {}, "a third run changes nothing"


@functools.cache
def merged_by(how: str) -> Path:
    if how == "fast-forward":
        return merged_upgrade()
    root = adopted.copy(adopted.adopted("0.9.2"), scratch(how))
    branch(root, "upgrade")
    assert upgrade.main(["upgrade.py", "--root", str(root), "--apply"]) == 0
    commit(root, "chore(method): upgrade the Ratchet plugin")
    git(root, "checkout", "-q", "main")
    if how == "squash":
        git(root, "merge", "-q", "--squash", "upgrade")
        commit(root, "chore(method): upgrade the Ratchet plugin (#1)")
    else:
        git(root, "merge", "-q", "--no-ff", "--no-edit", "upgrade")
    return root


def once_from(source: Path, place: Path, *commits: str) -> Path:
    root = adopted.copy(source, place)
    branch(root, "once")
    for note in commits:
        noted(root, note)
    upgraded(root, SECOND)
    return root


def noted(root: Path, note: str) -> None:
    write(root, "notes.txt", f"{note}\n")
    commit(root, "docs: a note")


def remote(root: Path, place: Path, name: str = "origin") -> Path:
    git(place, "init", "-q", "--bare", f"{name}.git")
    git(root, "remote", "add", name, str(place / f"{name}.git"))
    git(root, "push", "-q", name, "main")
    git(root, "remote", "set-head", name, "main")
    return root


def on_the_default_branch(how: str, tmp_path: Path) -> Path:
    return remote(adopted.copy(merged_by(how), tmp_path / "writer"), tmp_path)


def without_a_remote(tmp_path: Path) -> Path:
    return adopted.copy(merged_by("fast-forward"), tmp_path / "writer")


def ahead_of_the_remote(tmp_path: Path, *, local: tuple[str, ...]) -> Path:
    writer = remote(adopted.copy(held_base(), tmp_path / "writer"), tmp_path)
    for step in local:
        if step == FIRST:
            upgraded(writer, FIRST)
        else:
            noted(writer, step)
    return writer


def on_an_open_branch(tmp_path: Path) -> Path:
    return adopted.copy(upgrades("held", FIRST), tmp_path / "writer")


def after_the_default_branch_moved(tmp_path: Path) -> Path:
    writer = adopted.copy(held_base(), tmp_path / "writer")
    branch(writer, "rerun")
    upgraded(writer, FIRST)
    git(writer, "checkout", "-q", "main")
    branch(writer, "other")
    upgraded(writer, FIRST)
    git(writer, "checkout", "-q", "main")
    git(writer, "merge", "-q", "--no-ff", "--no-edit", "other")
    git(writer, "checkout", "-q", "rerun")
    git(writer, "merge", "-q", "--no-edit", "main")
    return writer


def rebased(tmp_path: Path) -> Path:
    writer = remote(adopted.copy(held_base(), tmp_path / "writer"), tmp_path)
    branch(writer, "rerun")
    upgraded(writer, FIRST)
    git(writer, "checkout", "-q", "main")
    noted(writer, "the default branch moved")
    git(writer, "push", "-q", "origin", "main")
    git(writer, "checkout", "-q", "rerun")
    git(writer, "rebase", "-q", "main")
    return writer


def stacked(tmp_path: Path) -> Path:
    writer = remote(adopted.copy(held_base(), tmp_path / "writer"), tmp_path)
    branch(writer, "first")
    upgraded(writer, FIRST)
    branch(writer, "second")
    noted(writer, "stacked on the first")
    return writer


def behind_the_remote(tmp_path: Path) -> Path:
    writer = remote(adopted.copy(held_base(), tmp_path / "writer"), tmp_path)
    branch(writer, "elsewhere")
    noted(writer, "pushed from another clone")
    git(writer, "push", "-q", "origin", "elsewhere:main")
    git(writer, "checkout", "-q", "main")
    branch(writer, "rerun")
    upgraded(writer, FIRST)
    return writer


def with_another_remote(tmp_path: Path) -> Path:
    writer = remote(adopted.copy(held_base(), tmp_path / "writer"), tmp_path, "upstream")
    branch(writer, "rerun")
    upgraded(writer, FIRST)
    return writer


def with_one_commit(tmp_path: Path) -> Path:
    writer = adopted.copy(merged_upgrade(), tmp_path / "writer")
    git(writer, "checkout", "-q", "--orphan", "solo")
    commit(writer, "feat(core): the one commit")
    git(writer, "branch", "-q", "-D", "main")
    git(writer, "branch", "-q", "-m", "main")
    return writer


def with_no_default_branch(tmp_path: Path) -> Path:
    writer = adopted.copy(merged_upgrade(), tmp_path / "writer")
    git(writer, "branch", "-q", "-m", "trunk")
    return writer


def detached(tmp_path: Path) -> Path:
    writer = on_an_open_branch(tmp_path)
    git(writer, "checkout", "-q", "--detach")
    return writer


FLOWS = {
    "on the default branch, merged by fast-forward": functools.partial(
        on_the_default_branch, "fast-forward"
    ),
    "on the default branch, merged by squash": functools.partial(on_the_default_branch, "squash"),
    "on the default branch, merged by a merge commit": functools.partial(
        on_the_default_branch, "merge"
    ),
    "on the default branch, ahead of the remote": functools.partial(
        ahead_of_the_remote, local=("a local commit", "another local commit")
    ),
    "on the default branch, ahead of the remote by an upgrade": functools.partial(
        ahead_of_the_remote, local=(FIRST,)
    ),
    "on the default branch, ahead of the remote by an upgrade and a commit": functools.partial(
        ahead_of_the_remote, local=("a local commit", FIRST, "another local commit")
    ),
    "on the default branch, with no remote": without_a_remote,
    "on an open upgrade branch": on_an_open_branch,
    "on an open branch the moved default branch was merged into": after_the_default_branch_moved,
    "on a rebased branch": rebased,
    "on a branch stacked on an open upgrade branch": stacked,
    "on a branch from a default branch behind its remote": behind_the_remote,
    "on a branch of a remote not named origin": with_another_remote,
    "in a repository with one commit": with_one_commit,
    "on a branch no default branch is known for": with_no_default_branch,
    "on a detached HEAD": detached,
}


def runs_again_unchanged(root: Path) -> None:
    written = (root / LEDGER).read_bytes()
    for _ in range(2):
        again = upgrade.plan(root, plugins()[SECOND])
        assert isinstance(again, upgrade.Plan)
        assert again.changes == {}, "a further run plans nothing"
        assert (root / LEDGER).read_bytes() == written


@pytest.mark.parametrize("flow", list(FLOWS))
def test_an_upgrade_is_judged_against_the_base_it_was_written_from_on_the_commit_that_carries_it(
    flow: str, *, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Out
) -> None:
    writer = FLOWS[flow](tmp_path)
    written_from = owed.next_base(writer)
    held = owed.parse(gatelib.show(writer, str(written_from), str(LEDGER))).entries
    result = upgrade.plan(writer, plugins()[SECOND])
    assert isinstance(result, upgrade.Plan), result
    upgrade.apply(writer, result.changes)
    commit(writer, f"chore(method): upgrade the Ratchet plugin to {SECOND}")
    assert gatelib.base_commit(writer) == written_from, f"{flow}: judged from the base written from"
    assert [e for e in owed.read(writer).entries if e not in held and e.since != SECOND] == [], (
        f"{flow}: an entry the base does not hold is owed since the pin the change moves to"
    )
    monkeypatch.setattr(plugin_pin, "PLUGIN", plugins()[SECOND])
    code, out = gate(writer, capsys)
    assert code == 0, f"{flow}: {out}"
    runs_again_unchanged(writer)


def test_a_result_committed_on_a_new_branch_is_settled_by_one_rerun_there(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Out
) -> None:
    writer = ahead_of_the_remote(tmp_path, local=(FIRST,))
    upgrade.apply(writer, plan_of(writer).changes)
    branch(writer, "up")
    commit(writer, f"chore(method): upgrade the Ratchet plugin to {SECOND}")
    monkeypatch.setattr(plugin_pin, "PLUGIN", plugins()[SECOND])
    assert gate(writer, capsys)[0] == 1, "the branch's gate judges from the remote's branch"
    written_from = owed.next_base(writer)
    capsys.readouterr()
    assert upgrade.main(["upgrade.py", "--root", str(writer), "--apply"]) == 0
    out = capsys.readouterr().out
    assert f"  {LEDGER}\n    - write down " in out, "the rerun names the ledger it changes"
    commit(writer, "chore(method): owe the branch's entries since the pin it moves to")
    assert gatelib.base_commit(writer) == written_from
    code, out = gate(writer, capsys)
    assert code == 0, out
    runs_again_unchanged(writer)


def carried_to_the_default_branch(tmp_path: Path) -> Path:
    writer = ahead_of_the_remote(tmp_path, local=(FIRST,))
    branch(writer, "up")
    upgrade.apply(writer, plan_of(writer).changes)
    git(writer, "checkout", "-q", "main")
    return writer


def test_a_result_carried_to_the_default_branch_is_settled_by_one_rerun_there_before_the_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Out
) -> None:
    writer = carried_to_the_default_branch(tmp_path)
    written_from = owed.next_base(writer)
    held = owed.parse(gatelib.show(writer, str(written_from), str(LEDGER))).entries
    monkeypatch.setattr(plugin_pin, "PLUGIN", plugins()[SECOND])
    capsys.readouterr()
    assert upgrade.main(["upgrade.py", "--root", str(writer), "--apply"]) == 0
    out = capsys.readouterr().out
    [line] = lines(out, "set back the since of")
    assert f"the gate's base {written_from[:12]} holds, to the since it holds there: '" in line
    assert line.endswith(f") from '{SECOND}' to '{FIRST}'")
    assert [e for e in owed.read(writer).entries if e not in held and e.since != SECOND] == []
    commit(writer, f"chore(method): upgrade the Ratchet plugin to {SECOND}")
    assert gatelib.base_commit(writer) == written_from
    code, out = gate(writer, capsys)
    assert code == 0, out
    runs_again_unchanged(writer)


def test_a_result_committed_on_the_default_branch_before_the_rerun_stays_red_and_is_the_next_base(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Out
) -> None:
    writer = carried_to_the_default_branch(tmp_path)
    commit(writer, f"chore(method): upgrade the Ratchet plugin to {SECOND}")
    monkeypatch.setattr(plugin_pin, "PLUGIN", plugins()[SECOND])
    code, out = gate(writer, capsys)
    assert code == 1
    assert "which the upgrade's own plan does not leave red on the merge base" in out
    assert plan_of(writer).changes == {}, "the gate judges the red commit against its parent"
    noted(writer, "the next commit")
    code, out = gate(writer, capsys)
    assert code == 0, out


def plan_of(root: Path) -> upgrade.Plan:
    result = upgrade.plan(root, plugins()[SECOND])
    assert isinstance(result, upgrade.Plan), result
    return result


def test_a_shallow_clone_without_the_merge_base_says_it_measured_the_ledger_against_head(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Out
) -> None:
    writer = remote(adopted.copy(held_base(), tmp_path / "writer"), tmp_path)
    branch(writer, "rerun")
    upgraded(writer, FIRST)
    git(writer, "push", "-q", "origin", "rerun")
    shallow = tmp_path / "shallow"
    source = f"file://{tmp_path / 'origin.git'}"
    git(tmp_path, "clone", "-q", "--depth", "1", "--branch", "rerun", source, str(shallow))
    monkeypatch.setattr(plugin_pin, "PLUGIN", plugins()[SECOND])
    said = "The history is shallow, and the owed ledger was measured against HEAD.\n"
    for root, shown in ((shallow, True), (writer, False)):
        capsys.readouterr()
        upgrade.main(["upgrade.py", "--root", str(root)])
        assert (said in capsys.readouterr().out) is shown, root


def test_a_since_edited_on_an_entry_the_base_holds_is_set_back_by_a_rerun_and_named(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Out
) -> None:
    root = adopted.copy(held_base(), tmp_path / "repo")
    branch(root, "rerun")
    victim, *rest = owed.read(root).entries
    edited = replace(victim, since=FIRST)
    (root / LEDGER).write_text(owed.render([*rest, edited]), encoding="utf-8")
    commit(root, "chore(method): date an owed entry by hand")
    monkeypatch.setattr(plugin_pin, "PLUGIN", plugins()[SECOND])
    code, out = gate(root, capsys)
    assert code == 1
    assert f"adds or changes '{victim.problem}' for {victim.path} ({victim.rule})" in out
    capsys.readouterr()
    assert upgrade.main(["upgrade.py", "--root", str(root), "--apply"]) == 0
    base = git(root, "merge-base", "HEAD", "main")[:12]
    assert (
        f"set back the since of 1 entry the gate's base {base} holds, to the since it holds"
        f" there: '{victim.problem}' for {victim.path} ({victim.rule}) from '{FIRST}' to"
        f" '{victim.since}'"
    ) in capsys.readouterr().out
    assert victim in owed.read(root).entries
    assert edited not in owed.read(root).entries
    commit(root, f"chore(method): upgrade the Ratchet plugin to {SECOND}")
    code, out = gate(root, capsys)
    assert code == 0, out


def test_an_entry_the_merge_base_holds_keeps_its_since_through_a_rerun() -> None:
    base = owed.read(held_base()).entries
    entries = owed.read(upgrades("held", FIRST, SECOND)).entries
    assert set(base) < set(entries)
    assert {e.since for e in base} == {VERSION}
    assert [e.since for e in entries if e not in base] == [SECOND]


def test_a_ledger_that_does_not_read_is_still_red_after_a_rerun(rerun: Path, capsys: Out) -> None:
    assert gate(rerun, capsys)[0] == 0
    (rerun / LEDGER).write_text("[[owed\n", encoding="utf-8")
    code, out = gate(rerun, capsys)
    assert code == 1
    assert f"{LEDGER}: does not parse" in out


def test_a_comment_entry_the_change_may_not_record_is_still_red_after_a_rerun(
    rerun: Path, capsys: Out
) -> None:
    assert gate(rerun, capsys)[0] == 0
    add(rerun, replace(COMMENT, since=SECOND))
    code, out = gate(rerun, capsys)
    assert code == 1
    assert "only the adoption and the change that first fills the lister record what" in out


def test_a_comment_entry_dated_before_the_lister_s_pin_is_still_red_after_a_rerun(
    rerun: Path, capsys: Out
) -> None:
    fill_lister(rerun)
    add(rerun, replace(COMMENT, since=SECOND))
    assert gate(rerun, capsys)[0] == 0
    add(rerun, replace(COMMENT, problem="backdated", since=FIRST))
    code, out = gate(rerun, capsys)
    assert code == 1
    assert (
        f"adds 'backdated' for src/a.txt (comments) since '{FIRST}'; the change first fills the"
        f" lister under the pin {SECOND}"
    ) in out


def test_an_entry_dated_before_the_pin_the_change_moves_to_is_still_red_after_a_rerun(
    rerun: Path, capsys: Out
) -> None:
    assert gate(rerun, capsys)[0] == 0
    add(rerun, entry(since=FIRST, problem="backdated"))
    code, out = gate(rerun, capsys)
    assert code == 1
    assert (
        f"since '{FIRST}'; the change moves the plugin pin to {SECOND}, so a new entry is owed"
        f" since {SECOND}"
    ) in out


def test_a_pin_older_than_the_plugin_in_use_is_still_red_after_a_rerun(
    rerun: Path, monkeypatch: pytest.MonkeyPatch, capsys: Out
) -> None:
    monkeypatch.setattr(plugin_pin, "PLUGIN", plugins()[LATER])
    code, out = gate(rerun, capsys)
    assert code == 1
    assert f"{SECOND} is older than the plugin in use, {LATER}" in out


def test_growth_without_a_pin_move_is_still_red_after_a_rerun(rerun: Path, capsys: Out) -> None:
    git(rerun, "checkout", "-q", "main")
    git(rerun, "merge", "-q", "--ff-only", "rerun")
    branch(rerun, "change")
    written = own_entry(rerun, drop_row(rerun, DIFF_COVERAGE), SECOND)
    upgraded(rerun, SECOND)
    assert written in owed.read(rerun).entries
    code, out = gate(rerun, capsys)
    assert code == 1
    assert "without moving the plugin pin to a newer version" in out


def own_entry(root: Path, part: str, since: str) -> owed.Entry:
    [finding] = [f for f in conformance.findings(root) if part in f.problem]
    written = owed.Entry(finding.path, finding.rule, finding.key()[2], since)
    add(root, written)
    return written


def own_settings_rule(root: Path) -> None:
    path = root / ".claude/settings.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data.setdefault("permissions", {}).setdefault("ask", []).append(OWN_RULE)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def test_an_entry_the_base_does_not_owe_is_still_red_after_a_rerun_and_a_rerun_drops_it(
    rerun: Path, capsys: Out
) -> None:
    dropped = drop_row(rerun, DIFF_COVERAGE)
    written = own_entry(rerun, dropped, SECOND)
    code, out = gate(rerun, capsys)
    assert code == 1
    [line] = lines(out, "which the upgrade's own plan does not leave red on the merge base")
    assert f"adds '{written.problem}' for {written.path}" in line
    capsys.readouterr()
    assert upgrade.main(["upgrade.py", "--root", str(rerun)]) == 1
    out = capsys.readouterr().out
    base = git(rerun, "merge-base", "HEAD", "main")[:12]
    assert (
        f"drop 1 entry this change brought, which one upgrade from the gate's base {base}"
        f" would not write: '{written.problem}' for {written.path} ({written.rule})\n"
    ) in out
    assert "write down 0" not in out
    add(rerun, replace(written, since=FIRST))
    upgraded(rerun, SECOND)
    assert written.key not in {e.key for e in owed.read(rerun).entries}


def test_an_entry_another_gate_judges_is_still_red_after_a_rerun_and_a_rerun_drops_it(
    rerun: Path, capsys: Out
) -> None:
    own_settings_rule(rerun)
    written = own_entry(rerun, OWN_RULE, SECOND)
    assert owed.never(written.key)
    code, out = gate(rerun, capsys)
    assert code == 1
    [line] = lines(out, "which the upgrade's own plan does not leave red on the merge base")
    assert f"for {written.path} (settings)" in line
    upgraded(rerun, SECOND)
    assert written.key not in {e.key for e in owed.read(rerun).entries}


def test_the_entries_a_change_brings_are_those_the_gate_judges_but_its_edits_of_the_base_s() -> (
    None
):
    kept = entry(path="a.toml")
    moved = entry(path="old.toml", problem="moved")
    redated = entry(path="b.toml", since=VERSION)
    new = entry(path="c.toml")
    before = [kept, moved, entry(path="b.toml")]
    after = [kept, replace(moved, path="new.toml"), redated, new]
    renamed = {"old.toml": "new.toml"}
    brought, edits = owed.brought(before, after, renamed)
    assert brought == Counter([new])
    assert sorted(edits) == sorted(
        [(replace(moved, path="new.toml"), moved), (redated, entry(path="b.toml"))]
    )
    judged = owed.grown(
        owed.Ledger(tuple(before), ()),
        owed.Ledger(tuple(after), ()),
        binding=(None, None),
        floor=None,
        exempt=(None, renamed),
    )
    assert sorted(line.split(" (")[0] for line in judged) == sorted(
        f"{LEDGER}: adds or changes '{e.problem}' for {e.path}" for e in (redated, new)
    )


@pytest.mark.parametrize("ahead", [False, True])
@pytest.mark.parametrize("named", [None, "trunk"])
@pytest.mark.parametrize("on", ["main", "origin/main", "trunk", "feature", "HEAD"])
@pytest.mark.parametrize("remote_name", [None, "origin", "upstream", "team/origin"])
def test_the_writer_s_base_is_the_base_the_gate_takes_for_the_commit_made_next(
    *, remote_name: str | None, on: str, named: str | None, ahead: bool, tmp_path: Path
) -> None:
    binding = f"- Default branch: `{named}`\n" if named else "# Binding\n"
    root = repository(tmp_path / "repo", {str(gatelib.BINDING): binding})
    noted(root, "a second commit")
    if remote_name:
        remote(root, tmp_path, remote_name)
    if ahead:
        noted(root, "a commit the remote does not hold")
    if on == "HEAD":
        git(root, "checkout", "-q", "--detach")
    elif on != "main":
        branch(root, on)
    noted(root, f"a commit on {on}")
    written_from = owed.next_base(root)
    noted(root, "the commit the gate judges")
    assert gatelib.base_commit(root) == written_from


ELSEWHERE = "to commit it on another branch, run the upgrade again there first."
PLACES = {
    "carried to the default branch": (
        carried_to_the_default_branch,
        lambda _root: f"Commit this on main; {ELSEWHERE}",
    ),
    "on an open upgrade branch": (
        on_an_open_branch,
        lambda _root: f"Commit this on rerun; {ELSEWHERE}",
    ),
    "on a detached HEAD": (
        detached,
        lambda root: (
            f"Commit this on the detached HEAD at {git(root, 'rev-parse', 'HEAD')[:12]}; to commit"
            " it on a branch, run the upgrade again there first."
        ),
    ),
}


@pytest.mark.parametrize("flow", list(PLACES))
def test_the_plain_words_name_where_the_ledger_was_measured_for_and_how_to_commit_it_elsewhere(
    flow: str, *, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Out
) -> None:
    build, said = PLACES[flow]
    writer = build(tmp_path)
    monkeypatch.setattr(plugin_pin, "PLUGIN", plugins()[SECOND])
    capsys.readouterr()
    upgrade.main(["upgrade.py", "--root", str(writer), "--apply"])
    out = capsys.readouterr().out
    assert f"  {LEDGER}\n" in out, f"{flow}: the plan writes the ledger"
    assert lines(out.split("In plain words:")[1], "Commit this") == [said(writer)], flow
    capsys.readouterr()
    upgrade.main(["upgrade.py", "--root", str(writer)])
    out = capsys.readouterr().out
    assert "Commit this" not in out, f"{flow}: a plan that writes no ledger names no branch"


@pytest.mark.parametrize(
    "local", [(FIRST,), ("a local commit", FIRST, "another local commit")], ids=["last", "inside"]
)
def test_a_plan_that_leaves_an_owing_ledger_as_it_is_still_names_the_branch_it_holds_for(
    local: tuple[str, ...], *, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Out
) -> None:
    writer = ahead_of_the_remote(tmp_path, local=local)
    monkeypatch.setattr(plugin_pin, "PLUGIN", plugins()[SECOND])
    for apply in ([], ["--apply"]):
        capsys.readouterr()
        upgrade.main(["upgrade.py", "--root", str(writer), *apply])
        out = capsys.readouterr().out
        assert f"  {LEDGER}\n" not in out, "the plan leaves the ledger as it is"
        assert lines(out.split("In plain words:")[1], "Commit this") == [
            f"Commit this on main; {ELSEWHERE}"
        ], apply
    assert owed.read(writer).entries, "the ledger the plan leaves owes something"
