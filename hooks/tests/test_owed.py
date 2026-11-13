"""The owed ledger: what an upgrade leaves is counted and not red, and the list only shrinks."""

import atexit
import functools
import json
import shutil
import subprocess
import sys
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
    kept = owed.parse(owed.kept(Counter([ENTRY.key, other.key]))(text)).entries
    assert list(kept) == sorted([ENTRY, other])
    assert owed.parse(owed.kept(Counter([ENTRY.key]))(text)).entries == (ENTRY,)
    assert owed.kept(Counter([ENTRY.key, ENTRY.key, other.key]))(text) == text
    assert owed.kept(Counter())("[[owed\n") == "[[owed\n", "a ledger that does not read stays"


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

    assert (owed.moved(binding(before), binding(after), floor=None) is not None) is allowed
    assert owed.moved(None, binding("0.0.1"), floor="0.11.0") is None, (
        "an unpinned base has a floor"
    )
    assert owed.moved(None, binding("0.11.0"), floor="0.11.0") is not None


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


def test_a_list_that_does_not_read_is_said_plainly(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "quality").mkdir()
    (tmp_path / LEDGER).write_text("[[owed\n")
    assert owed.main(["owed.py", "--root", str(tmp_path)]) == 0
    said = capsys.readouterr().out
    assert said.startswith(f"The list of what this project owes ({LEDGER}) cannot be read"), said
    assert "every check it held back is red until it reads" in said


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
    assert f"owed: {WEEKLY}: line 40 in job `weekly` still says" in out


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


def test_an_entry_cannot_excuse_a_placeholder_in_another_job_of_its_workflow(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    edit(repo / WEEKLY, MARKER, "      - run: ./scripts/install-toolchain\n")
    other = (
        "  other:\n    runs-on: ubuntu-latest\n    steps:\n" + MARKER + NEXT + "        run: true\n"
    )
    (repo / WEEKLY).write_text((repo / WEEKLY).read_text(encoding="utf-8") + other)
    code, out = checked(repo, capsys)
    assert code == 1, out
    red = [line for line in out.splitlines() if not line.startswith(("owed: ", "conformance: "))]
    assert any(line.startswith(f"{WEEKLY}: line ") and "job `other`" in line for line in red), red
    assert any("an entry owes" in line and "job `weekly`" in line for line in red), red


def test_every_stale_entry_goes_in_one_edit(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    held = owed.read(repo).entries
    stale = [
        entry(problem=f"a finding fixed long ago, number {n}") for n in range(upgrade.PASSES + 2)
    ]
    add(repo, *stale)
    code, out = checked(repo, capsys)
    assert code == 1
    red = [line for line in out.splitlines() if line.startswith(f"{LEDGER}: ")]
    assert len(red) == 1, red
    assert red[0].startswith(f"{LEDGER}: {len(stale)} entries owe what is no longer found: ")
    assert "`upgrade.py --apply` makes 1 of them" in out
    assert upgrade.main(["upgrade.py", "--root", str(repo), "--apply"]) == 0
    assert f"drop the {len(stale)} entries whose findings are fixed" in capsys.readouterr().out
    assert owed.read(repo).entries == held


def test_an_entry_whose_finding_is_fixed_goes_and_the_count_drops(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    before = len(owed.read(repo).entries)
    edit(repo / WEEKLY, MARKER, "      - run: ./scripts/install-toolchain\n")
    code, out = checked(repo, capsys)
    assert code == 1, "an entry that excuses nothing is red until it goes"
    assert "an entry owes 'in job `weekly` still says '# bind: toolchain setup'" in out
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


def test_an_unpinned_base_may_only_move_to_a_pin_no_older_than_the_plugin_in_use(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = adopted.copy(adopted.adopted("0.9.2"), tmp_path / "repo")
    branch(root, "change")
    assert plugin_pin.pinned((root / BINDING).read_text(encoding="utf-8")) is None
    old = f"- Ratchet plugin: 0.0.1 at {'a' * 40}"
    (root / BINDING).write_text(plugin_pin.with_pin(old)((root / BINDING).read_text()))
    (root / LEDGER).write_text(owed.render([entry(since="0.0.1")]), encoding="utf-8")
    code, out = gate(root, capsys)
    assert code == 1, out
    assert f"0.0.1 is older than the plugin in use, {VERSION}" in out


def test_a_ledger_that_grows_where_the_thresholds_are_new_is_still_judged(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = adopted.copy(merged_upgrade(), tmp_path / "repo")
    (root / "quality/thresholds.toml").unlink()
    commit(root, "chore: the thresholds go")
    branch(root, "change")
    git(root, "checkout", "-q", "HEAD~1", "--", "quality/thresholds.toml")
    add(root, entry(since=VERSION))
    code, out = gate(root, capsys)
    assert "is new at" in out
    assert code == 1, out
    assert "without moving the plugin pin to a newer version" in out


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


KEYS_FROM = """
import json, re, shutil, sys
from pathlib import Path
plugin, scratch, *repositories = map(Path, sys.argv[1:])
sys.path.insert(0, str(plugin / "scripts"))
import conformance
templates = plugin / "skills"
found = {}
for label, mutate in conformance.mutations():
    target = scratch / re.sub(r"\\W+", "-", label)
    shutil.copytree(templates / "init/templates", target, symlinks=True)
    shutil.copytree(templates / "adopt/templates", target, symlinks=True, dirs_exist_ok=True)
    conformance.fill_placeholders(target)
    mutate(target)
    found[label] = [list(failure.key()) for _, failure in conformance.raised(target)]
for repository in repositories:
    found[repository.name] = [list(f.key()) for _, f in conformance.raised(repository)]
print(json.dumps(found))
"""
PLUGIN_PARTS = (".claude-plugin", "scripts", "skills", "hooks/ratchet_guards")


def keys_from(plugin: Path, place: Path) -> dict[str, list[list[str]]]:
    """Every key every check raises, from a plugin copy at ``plugin``, on repositories in ``place``.

    The repositories are each self-test mutation of the templates and the two older adoptions.
    """
    for part in PLUGIN_PARTS:
        ignore = shutil.ignore_patterns("__pycache__")
        shutil.copytree(PLUGIN_ROOT / part, plugin / part, symlinks=True, ignore=ignore)
    fixtures = [adopted.copy(adopted.adopted(v), place / f"adopted-{v}") for v in adopted.RELEASES]
    done = subprocess.run(  # noqa: S603 # -- a fixed argv, this interpreter on the plugin copy
        [sys.executable, "-c", KEYS_FROM, str(plugin), str(place / "mutated"), *map(str, fixtures)],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(done.stdout)


def test_no_owed_key_holds_where_the_plugin_or_the_project_lies(tmp_path: Path) -> None:
    """An owed entry is the same debt from any checkout of the plugin, so CI excuses its list."""
    one = keys_from(tmp_path / "plugin-one", tmp_path / "here")
    two = keys_from(tmp_path / "elsewhere/plugin-two", tmp_path / "there/deeper")
    assert one.keys() == two.keys()
    for name, keys in one.items():
        assert keys == two[name], name
        assert not [key for key in keys if str(tmp_path) in json.dumps(key)], name


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


# --- what the change brought is never owed ------------------------------------------------------

DIFF_COVERAGE = "| diff coverage |"
MARKERS = "| marker comments did not rise |"


def drop_row(root: Path, prefix: str) -> str:
    """Drop the binding's Gates row that starts with ``prefix``; the problem its absence raises."""
    text = (root / BINDING).read_text(encoding="utf-8")
    [row] = [line for line in text.splitlines(keepends=True) if line.startswith(prefix)]
    (root / BINDING).write_text(text.replace(row, ""), encoding="utf-8")
    return f"the Gates table has no '{prefix.strip('| ')}' row"


@pytest.mark.parametrize("release", ["0.8.2", "0.9.2"])
def test_a_violation_the_change_brought_is_not_owed_by_its_upgrade(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], release: str
) -> None:
    root = adopted.copy(adopted.adopted(release), tmp_path / "repo")
    branch(root, "feature")
    dropped = drop_row(root, DIFF_COVERAGE)
    commit(root, "feat(core): a feature that also drops a gate row")
    capsys.readouterr()
    assert upgrade.main(["upgrade.py", "--root", str(root), "--apply"]) == 1
    out = capsys.readouterr().out
    assert not [entry for entry in owed.read(root).entries if dropped in entry.problem]
    red = out.split("Red until fixed")[1].split("Owed, ")[0]
    assert dropped in red
    assert "1 thing stops a merge until fixed." in out
    commit(root, "chore(method): upgrade the Ratchet plugin")
    code, said = checked(root, capsys)
    assert code == 1
    assert f"{BINDING}: {dropped}" in said


def test_an_entry_the_upgrade_could_not_have_written_is_red_before_the_merge(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = adopted.copy(adopted.adopted("0.9.2"), tmp_path / "repo")
    branch(root, "upgrade")
    assert upgrade.main(["upgrade.py", "--root", str(root), "--apply"]) == 0
    assert gate(root, capsys)[0] == 0, "what the upgrade wrote is what the base owes"
    dropped = drop_row(root, DIFF_COVERAGE)
    found = [f for f in conformance.findings(root) if f.rule != owed.OWN]
    written = [owed.Entry(f.path, f.rule, f.key()[2], VERSION) for f in found]
    (root / LEDGER).write_text(owed.render(written), encoding="utf-8")
    assert checked(root, capsys)[0] == 0, "conformance alone reads every entry as owed"
    code, out = gate(root, capsys)
    assert code == 1, out
    [line] = [line for line in out.splitlines() if "which the upgrade's own plan" in line]
    assert f"adds '{dropped}; a row is bound or none, never dropped' for {BINDING}" in line


def test_another_plugin_than_the_pin_skips_the_question_and_says_so(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repin(repo, "999.0.0", "d" * 40)
    add(repo, entry(since="999.0.0"))
    code, out = gate(repo, capsys)
    assert code == 0
    assert "is asked of the plugin the binding pins, 999.0.0 at dddddddddddd" in out
    assert "CI, which runs the pinned plugin, asks it" in out


# --- what another gate judges is never owed -----------------------------------------------------

OWN_RULE = "Bash(my-own-tool:*)"


def test_a_rule_of_the_project_s_own_stays_red_and_stays_a_question(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """settings-check judges the same drift, so owing it here would print green over a red gate."""
    root = adopted.copy(adopted.adopted("0.9.2"), tmp_path / "repo")
    branch(root, "feature")
    path = root / ".claude/settings.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data.setdefault("permissions", {}).setdefault("ask", []).append(OWN_RULE)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    commit(root, "chore: a rule of the project's own")
    capsys.readouterr()
    assert upgrade.main(["upgrade.py", "--root", str(root), "--apply"]) == 1
    out = capsys.readouterr().out
    assert not [entry for entry in owed.read(root).entries if entry.rule == "settings"]
    assert OWN_RULE in out.split("Red until fixed")[1].split("Owed, ")[0]
    assert "and 0 red" not in out
    assert "1 thing stops a merge until fixed." in out
    plain = out.split("In plain words:")[1]
    assert "asks you before it runs `my-own-tool`" in plain
    assert "one you added" not in plain
    commit(root, "chore(method): upgrade the Ratchet plugin")
    code, said = checked(root, capsys)
    assert code == 1
    assert "in ask mirrors no manifest pattern" in said.split("owed: ")[0]


@pytest.mark.parametrize(
    ("rule", "path"),
    [
        ("settings", ".claude/settings.json"),
        ("incidents", "docs/incidents/2026-01-01-outage.md"),
        ("glossary", "GLOSSARY.md (or GLOSSARY-MAP.md)"),
        ("quality", "quality/thresholds.toml"),
        (owed.OWN, LEDGER),
    ],
)
def test_an_entry_never_excuses_what_another_gate_judges(rule: str, path: str) -> None:
    key = (rule, path, "a finding")
    hits, _ = owed.excused([key], [owed.Entry(path, rule, "a finding", "0.12.0")])
    assert hits == [False]
