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


def on_the_default_branch(how: str, tmp_path: Path) -> tuple[Path, Path]:
    writer = adopted.copy(merged_by(how), tmp_path / "writer")
    return writer, once_from(merged_by(how), tmp_path / "once")


def ahead_of_the_remote(tmp_path: Path) -> tuple[Path, Path]:
    writer = adopted.copy(held_base(), tmp_path / "writer")
    git(tmp_path, "init", "-q", "--bare", "origin.git")
    git(writer, "remote", "add", "origin", str(tmp_path / "origin.git"))
    git(writer, "push", "-q", "origin", "main")
    noted(writer, "a local commit")
    upgraded(writer, FIRST)
    noted(writer, "another local commit")
    reference = once_from(held_base(), tmp_path / "once", "a local commit", "another local commit")
    return writer, reference


def on_an_open_branch(tmp_path: Path) -> tuple[Path, Path]:
    return adopted.copy(upgrades("held", FIRST), tmp_path / "writer"), upgrades("held", SECOND)


def after_the_default_branch_moved(tmp_path: Path) -> tuple[Path, Path]:
    writer = adopted.copy(held_base(), tmp_path / "writer")
    branch(writer, "rerun")
    upgraded(writer, FIRST)
    git(writer, "checkout", "-q", "main")
    branch(writer, "other")
    upgraded(writer, FIRST)
    git(writer, "checkout", "-q", "main")
    git(writer, "merge", "-q", "--no-ff", "--no-edit", "other")
    reference = adopted.copy(writer, tmp_path / "moved")
    git(writer, "checkout", "-q", "rerun")
    git(writer, "merge", "-q", "--no-edit", "main")
    return writer, once_from(reference, tmp_path / "once")


def with_one_commit(tmp_path: Path) -> tuple[Path, Path]:
    writer = adopted.copy(merged_upgrade(), tmp_path / "writer")
    git(writer, "checkout", "-q", "--orphan", "solo")
    commit(writer, "feat(core): the one commit")
    git(writer, "branch", "-q", "-D", "main")
    git(writer, "branch", "-q", "-m", "main")
    return writer, once_from(writer, tmp_path / "once")


def detached(tmp_path: Path) -> tuple[Path, Path]:
    writer, reference = on_an_open_branch(tmp_path)
    git(writer, "checkout", "-q", "--detach")
    return writer, reference


FLOWS = {
    "on the default branch, merged by fast-forward": functools.partial(
        on_the_default_branch, "fast-forward"
    ),
    "on the default branch, merged by squash": functools.partial(on_the_default_branch, "squash"),
    "on the default branch, merged by a merge commit": functools.partial(
        on_the_default_branch, "merge"
    ),
    "on the default branch, ahead of the remote": ahead_of_the_remote,
    "on an open upgrade branch": on_an_open_branch,
    "on an open branch the moved default branch was merged into": after_the_default_branch_moved,
    "in a repository with one commit": with_one_commit,
    "on a detached HEAD": detached,
}


@pytest.mark.parametrize("flow", list(FLOWS))
def test_whatever_order_the_upgrades_run_in_the_ledger_is_one_upgrade_from_what_is_merged(
    flow: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Out
) -> None:
    writer, reference = FLOWS[flow](tmp_path)
    result = upgrade.plan(writer, plugins()[SECOND])
    assert isinstance(result, upgrade.Plan), result
    upgrade.apply(writer, result.changes)
    assert (writer / LEDGER).read_text(encoding="utf-8") == (reference / LEDGER).read_text(
        encoding="utf-8"
    ), f"{flow}: the ledger is the one a single upgrade from what is merged writes"
    if git(writer, "rev-parse", "--abbrev-ref", "HEAD") in {"main", "HEAD"}:
        branch(writer, "up")
    commit(writer, f"chore(method): upgrade the Ratchet plugin to {SECOND}")
    monkeypatch.setattr(plugin_pin, "PLUGIN", plugins()[SECOND])
    code, out = gate(writer, capsys)
    assert code == 0, f"{flow}: {out}"
    again = upgrade.plan(writer, plugins()[SECOND])
    assert isinstance(again, upgrade.Plan)
    assert again.changes == {}, f"{flow}: a further run plans nothing"


def test_an_entry_the_merge_base_holds_is_left_as_the_change_has_it_and_the_gate_judges_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Out
) -> None:
    root = adopted.copy(held_base(), tmp_path / "repo")
    branch(root, "rerun")
    victim, *rest = owed.read(root).entries
    edited = replace(victim, since=FIRST)
    (root / LEDGER).write_text(owed.render([*rest, edited]), encoding="utf-8")
    commit(root, "chore(method): date an owed entry by hand")
    upgraded(root, SECOND)
    assert edited in owed.read(root).entries, "the upgrade leaves the base's own entry as it is"
    monkeypatch.setattr(plugin_pin, "PLUGIN", plugins()[SECOND])
    code, out = gate(root, capsys)
    assert code == 1
    assert (
        f"adds '{victim.problem}' for {victim.path} ({victim.rule}) since '{FIRST}'; the change"
        f" moves the plugin pin to {SECOND}"
    ) in out


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
    add(rerun, replace(written, since=FIRST))
    capsys.readouterr()
    assert upgrade.main(["upgrade.py", "--root", str(rerun)]) == 1
    out = capsys.readouterr().out
    base = git(rerun, "merge-base", "HEAD", "main")[:12]
    assert (
        f"    - drop 1 entry this change brought, which one upgrade from the merge base {base}"
        f" would not write: '{written.problem}' for {written.path} ({written.rule})\n"
    ) in out
    assert "write down 0" not in out
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
    brought = owed.brought(before, after, renamed)
    assert brought == Counter([new])
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
