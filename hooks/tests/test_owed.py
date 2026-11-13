"""The owed ledger: what an upgrade leaves is counted and not red, and the list only shrinks."""

import atexit
import functools
import shutil
import tempfile
from collections import Counter
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

import conformance
import lowered
import owed
import plugin_pin
import upgrade
from tests import adopted
from tests.repos import branch, commit, git
from tests.support import PLUGIN_ROOT

BINDING = "docs/process/binding.md"
LEDGER = str(owed.LEDGER)
WEEKLY = ".github/workflows/weekly.yml"
DEEP = ".github/workflows/deep.yml"
MARKER = "      # bind: toolchain setup, pinned, with the setup action's own cache on\n"
NEXT = "      - name: Run the weekly gates\n"
TEMPLATES = PLUGIN_ROOT / "skills/adopt/templates"
VERSION = str(plugin_pin.version_of(PLUGIN_ROOT))
ENTRY = owed.Entry("quality/bites.toml", "bites", "gate 'lint' has no bite", "0.12.0")


def entry(**changed: str) -> owed.Entry:
    fields = {"path": ENTRY.path, "rule": ENTRY.rule, "problem": ENTRY.problem}
    return owed.Entry(**{**fields, "since": ENTRY.since, **changed})


@functools.cache
def merged_upgrade() -> Path:
    """A 0.9.2 adoption whose upgrade was made on a branch and merged into ``main``, once."""
    scratch = Path(tempfile.mkdtemp(prefix="ratchet-owed-"))
    atexit.register(shutil.rmtree, scratch, ignore_errors=True)
    root = adopted.copy(adopted.adopted("0.9.2"), scratch / "repo")
    branch(root, "upgrade")
    assert upgrade.main(["upgrade.py", "--root", str(root), "--apply"]) == 0
    commit(root, "chore(method): upgrade the Ratchet plugin")
    git(root, "checkout", "-q", "main")
    git(root, "merge", "-q", "--ff-only", "upgrade")
    return root


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """The merged upgrade, on a new branch for one change."""
    root = adopted.copy(merged_upgrade(), tmp_path / "repo")
    branch(root, "change")
    return root


def edit(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text, f"{path} has no {old!r}"
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def gate(root: Path, capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    """What the ratchet-lowered gate says of the change: its exit code and its lines."""
    capsys.readouterr()
    code = lowered.main(["lowered.py", "--root", str(root)])
    return code, capsys.readouterr().out


def checked(root: Path, capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    """What the conformance gate says: its exit code and its lines."""
    capsys.readouterr()
    code = conformance.run(root)
    return code, capsys.readouterr().out


# --- the ledger as data -------------------------------------------------------------------------


@given(st.lists(st.tuples(*[st.text(min_size=1)] * 4), max_size=4))
def test_a_ledger_reads_back_what_was_written(fields: list[tuple[str, str, str, str]]) -> None:
    entries = [owed.Entry(*field) for field in fields]
    read = owed.parse(owed.render(entries))
    assert read.problems == ()
    assert list(read.entries) == sorted(entries)


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        ("[[owed\n", "does not parse"),
        ("owed = 1\n", "'owed' is not an array of tables"),
        ("owed = [1]\n", "entry 1 needs exactly rule, path, problem, since; it has int"),
        ('other = "x"\n', "unknown key 'other'"),
        ('[[owed]]\nrule = "a"\n', "entry 1 needs exactly rule, path, problem, since"),
        ('[[owed]]\nrule = "a"\npath = "b"\nproblem = "c"\nsince = 1\n', "entry 1: every field"),
        ('[[owed]]\nrule = ""\npath = "b"\nproblem = "c"\nsince = "1"\n', "entry 1: every field"),
    ],
)
def test_a_ledger_that_does_not_read_says_why(text: str, problem: str) -> None:
    ledger = owed.parse(text)
    assert ledger.entries == ()
    assert any(line.startswith(problem) for line in ledger.problems), ledger.problems


def test_an_entry_excuses_one_finding_with_its_check_file_and_problem() -> None:
    key = ENTRY.key
    other_gate = ("bites", ENTRY.path, "gate 'types' has no bite")
    other_file = ("bites", "elsewhere.toml", ENTRY.problem)
    other_rule = ("speed", ENTRY.path, ENTRY.problem)
    own = (owed.OWN, ENTRY.path, ENTRY.problem)
    hits, stale = owed.excused([key, key, other_gate, other_file, other_rule, own], [ENTRY])
    assert hits == [True, False, False, False, False, False]
    assert stale == Counter()
    hits, stale = owed.excused([], [ENTRY, ENTRY])
    assert hits == []
    assert stale == Counter({key: 2})
    hits, _ = owed.excused([own], [entry(rule=owed.OWN)])
    assert hits == [False], "the ledger's own findings are never owed"


def test_dropping_a_stale_entry_keeps_as_many_as_are_found() -> None:
    other = entry(problem="gate 'types' has no bite")
    text = owed.render([ENTRY, ENTRY, other])
    kept = owed.parse(owed.kept(ENTRY.key, 1)(text)).entries
    assert list(kept) == sorted([ENTRY, other])
    assert owed.kept(ENTRY.key, 2)(text) == text
    assert owed.kept(ENTRY.key, 0)("[[owed\n") == "[[owed\n", "a ledger that does not read stays"


def test_a_record_adds_entries_and_nothing_when_there_is_none() -> None:
    assert owed.recorded([])(None) == ""
    assert owed.parse(owed.recorded([ENTRY])(None)).entries == (ENTRY,)
    assert owed.recorded([ENTRY])("[[owed\n") == "[[owed\n"


@pytest.mark.parametrize(
    ("before", "after", "allowed"),
    [
        ("0.11.0", "0.12.0", True),
        (None, "0.12.0", True),
        ("0.11.0", "0.11.0", False),
        ("0.12.0", "0.11.0", False),
        ("0.11.0", None, False),
    ],
)
def test_the_ledger_may_grow_only_where_the_pin_moves_to_a_newer_version(
    before: str | None, after: str | None, *, allowed: bool
) -> None:
    def binding(version: str | None) -> str:
        return f"- Ratchet plugin: {version} at {'a' * 40}\n" if version else "# no pin\n"

    assert (owed.moved(binding(before), binding(after)) is not None) is allowed


def test_the_plain_count_names_the_latest_upgrade(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert owed.main(["owed.py", "--root", str(tmp_path)]) == 0
    assert capsys.readouterr().out == "", "a project that owes nothing hears nothing"
    (tmp_path / "quality").mkdir()
    (tmp_path / LEDGER).write_text(owed.render([ENTRY]))
    assert owed.main(["owed.py", "--root", str(tmp_path)]) == 0
    assert capsys.readouterr().out.startswith(
        "1 thing this project owes since the upgrade to 0.12.0. None of them stops a merge"
    )
    (tmp_path / LEDGER).write_text(owed.render([ENTRY, entry(since="0.9.10", path="a")]))
    assert str(owed.summary(tmp_path)).startswith("2 things this project owes since the upgrade")


# --- the upgrade writes it, conformance counts it --------------------------------------------


def test_an_upgrade_records_what_it_leaves_and_conformance_counts_it_green(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out = checked(repo, capsys)
    entries = owed.read(repo).entries
    assert code == 0
    assert entries
    assert {entry.since for entry in entries} == {VERSION}
    assert f"conformance: green (0 failures; {len(entries)} owed since an upgrade" in out
    assert out.count("\nowed: ") + out.startswith("owed: ") == len(entries)
    assert gate(repo, capsys)[0] == 0


def test_an_upgrade_run_again_on_the_merged_change_writes_nothing(repo: Path) -> None:
    again = upgrade.plan(repo, PLUGIN_ROOT)
    assert isinstance(again, upgrade.Plan)
    assert again.changes == {}
    assert again.red == []


def test_a_finding_whose_line_moves_stays_owed(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert ("toolchain", WEEKLY) in {(e.rule, e.path) for e in owed.read(repo).entries}
    edit(repo / WEEKLY, MARKER, "      # a comment the project added\n" + MARKER)
    code, out = checked(repo, capsys)
    assert code == 0, out
    assert f"owed: {WEEKLY}: line 40 still says" in out


# --- what no entry excuses ----------------------------------------------------------------------


def placeholder(name: str) -> str:
    """The template's toolchain placeholder in workflow ``name``, with the line it stands before."""
    lines = (TEMPLATES / name).read_text(encoding="utf-8").splitlines(keepends=True)
    return MARKER + lines[lines.index(MARKER) + 1]


def test_a_new_violation_of_an_owed_check_in_another_file_is_red(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert ("toolchain", DEEP) not in {(e.rule, e.path) for e in owed.read(repo).entries}
    edit(repo / DEEP, "    steps:\n", "    steps:\n" + placeholder(DEEP))
    code, out = checked(repo, capsys)
    assert code == 1
    red = [line for line in out.splitlines() if not line.startswith(("owed: ", "conformance: "))]
    assert any(line.startswith(f"{DEEP}: line ") for line in red), red


def test_a_second_violation_of_an_owed_check_in_the_same_file_is_red(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    edit(repo / WEEKLY, MARKER + NEXT, MARKER + NEXT + "\n" + MARKER + NEXT)
    code, out = checked(repo, capsys)
    assert code == 1
    assert "conformance: red (1 failures;" in out


def test_an_entry_whose_finding_is_fixed_goes_and_the_count_drops(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    before = len(owed.read(repo).entries)
    edit(repo / WEEKLY, MARKER, "      - run: ./scripts/install-toolchain\n")
    code, out = checked(repo, capsys)
    assert code == 1, "an entry that excuses nothing is red until it goes"
    assert "an entry owe 'still says '# bind: toolchain setup'" in out
    assert "`upgrade.py --apply` makes 1 of them" in out
    assert upgrade.main(["upgrade.py", "--root", str(repo), "--apply"]) == 0
    assert "write down" not in capsys.readouterr().out
    assert len(owed.read(repo).entries) == before - 1
    assert checked(repo, capsys)[0] == 0
    assert gate(repo, capsys)[0] == 0
    assert str(owed.summary(repo)).startswith(f"{before - 1} things this project owes")


# --- the gate before the merge: the ledger only shrinks -------------------------------------------


def add(root: Path, *entries: owed.Entry) -> None:
    held = owed.read(root).entries
    (root / LEDGER).write_text(owed.render([*held, *entries]), encoding="utf-8")


def repin(root: Path, version: str, commit_id: str) -> None:
    pin = plugin_pin.pinned((root / BINDING).read_text(encoding="utf-8"))
    assert pin is not None
    edit(root / BINDING, f"{pin.version} at {pin.commit}", f"{version} at {commit_id}")


def test_an_entry_added_without_moving_the_pin_is_red(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    add(repo, entry(since=VERSION))
    code, out = gate(repo, capsys)
    assert code == 1
    assert (
        f"{LEDGER}: adds or changes 'gate 'lint' has no bite' for quality/bites.toml (bites)"
        f" without moving the plugin pin to a newer version ({VERSION} at"
    ) in out


def test_an_entry_edited_to_cover_another_path_is_red(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    first = owed.read(repo).entries[0]
    edit(repo / LEDGER, f'path = "{first.path}"', 'path = "src/new_module.py"')
    code, out = gate(repo, capsys)
    assert code == 1
    assert f"for src/new_module.py ({first.rule}) without moving the plugin pin" in out


def test_a_ledger_deleted_and_written_again_with_more_is_red(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    held = owed.read(repo).entries
    (repo / LEDGER).unlink()
    commit(repo, "chore: drop the ledger")
    assert gate(repo, capsys)[0] == 0, "a ledger that only shrank is green here"
    assert checked(repo, capsys)[0] == 1, "and what it excused is red again"
    (repo / LEDGER).write_text(owed.render([*held, entry(since=VERSION)]), encoding="utf-8")
    code, out = gate(repo, capsys)
    assert code == 1
    assert out.count("without moving the plugin pin") == 1


def test_a_pin_moved_backwards_with_an_entry_added_is_red(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repin(repo, "0.1.0", "b" * 40)
    add(repo, entry(since="0.1.0"))
    code, out = gate(repo, capsys)
    assert code == 1
    assert f"({VERSION} at" in out
    assert "0.1.0 at bbbbbbbbbbbb here); an entry may only go" in out


def test_a_pin_moved_to_the_same_version_at_another_commit_is_red(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repin(repo, VERSION, "c" * 40)
    add(repo, entry(since=VERSION))
    code, out = gate(repo, capsys)
    assert code == 1
    assert "without moving the plugin pin to a newer version" in out


def test_an_entry_added_with_a_newer_pin_must_carry_that_version(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repin(repo, "999.0.0", "d" * 40)
    add(repo, entry(since="999.0.0"))
    assert gate(repo, capsys)[0] == 0
    add(repo, entry(since="0.1.0", problem="backdated"))
    code, out = gate(repo, capsys)
    assert code == 1
    assert (
        "since '0.1.0'; the change moves the plugin pin to 999.0.0, so a new entry is owed since"
        " 999.0.0"
    ) in out


def test_a_ledger_that_does_not_read_is_red_in_both_gates(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (repo / LEDGER).write_text("[[owed\n", encoding="utf-8")
    code, out = gate(repo, capsys)
    assert code == 1
    assert f"{LEDGER}: does not parse" in out
    code, out = checked(repo, capsys)
    assert code == 1
    assert f"{LEDGER}: does not parse" in out


def test_an_upgrade_that_does_not_move_the_pin_records_nothing(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    edit(repo / DEEP, "    steps:\n", "    steps:\n" + placeholder(DEEP))
    held = owed.read(repo).entries
    assert upgrade.main(["upgrade.py", "--root", str(repo), "--apply"]) == 1
    out = capsys.readouterr().out
    assert set(owed.read(repo).entries) < set(held), "it drops what the edit fixed, adds nothing"
    assert "Red until fixed (no owed entry excuses these" in out
    assert "1 thing stops a merge until fixed." in out


def test_the_template_manifest_sends_a_ledger_change_to_the_owner() -> None:
    text = (PLUGIN_ROOT / "skills/adopt/templates/guards/manifest.toml").read_text()
    manifest = upgrade.parse(text)
    group = manifest.group_of(LEDGER)
    assert group is not None
    assert group.name == "ratchets"


def test_a_missing_recipe_is_the_same_debt_as_what_it_needs_arrives() -> None:
    needs_all = conformance.missing_recipe("default:\n    true\n", "tier0-tree", "tier0-tree")
    needs_less = conformance.missing_recipe(
        'parallel := "x"\ndefault:\n    true\n', "tier0-tree", "tier0-tree"
    )
    assert needs_all.problem != needs_less.problem
    assert needs_all.key() == needs_less.key()


def test_a_missing_gates_row_is_the_same_debt_once_its_trigger_fires() -> None:
    question = "survives failing dependencies"
    failure = f"the Gates table has no '{question}' row; a row is bound or none, never dropped"
    trigger = "code:outbound-call"
    waiting = conformance.Fired({"outbound-call": []}, frozenset())
    fired = conformance.Fired({"outbound-call": ["src/client.py"]}, frozenset())
    before = conformance.dropped_row(failure, question, trigger=trigger, fired=waiting)
    after = conformance.dropped_row(failure, question, trigger=trigger, fired=fired)
    assert before.problem != after.problem
    assert before.key() == after.key()
