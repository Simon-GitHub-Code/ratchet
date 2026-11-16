"""``lane.py``: only a change of records and pages takes the light lane; any doubt runs it all."""

import re
import subprocess
from pathlib import Path

import pytest

import lane
from tests import page_reads
from tests.repos import branch, commit, git, repository, write

BASE = {
    "README.md": "# A\n",
    "docs/adr/0001-x.md": "# 1\n",
    "docs/model/route.md": "# route\n",
    "docs/model/drain.md": "# drain\n",
    "scripts/lane.py": "x = 1\n",
    "justfile": "default:\n",
    ".github/workflows/ci.yml": "name: ci\n",
    "skills/feature/SKILL.md": "# skill\n",
    "src/app.py": "y = 1\n",
}
CLASSIFIER_PATHS = ["scripts/lane.py", "justfile", ".github/workflows/ci.yml"]


def change(root: Path, edits: dict[str, str | None]) -> None:
    for path, text in edits.items():
        if text is None:
            (root / path).unlink()
        else:
            write(root, path, text)
    commit(root, "docs: the change")


def lane_of_change(
    root: Path, edits: dict[str, str | None], monkeypatch: pytest.MonkeyPatch
) -> str:
    repository(root, dict(BASE))
    branch(root)
    change(root, edits)
    monkeypatch.chdir(root)
    return lane.lane_for("main")


PROSE_PAGES = [
    ({"README.md": "# B\n"}, ["skills"]),
    ({"docs/harness/a.md": "# h\n"}, ["skills"]),
    ({"docs/acceptance.md": "# a\n"}, ["acceptance_record", "skills"]),
    ({"docs/adr/0034-x.md": "# 34\n"}, ["skills"]),
    ({"docs/adr/0001-x.md": "# 1b\n"}, []),
    ({"CHANGELOG.md": "# c\n"}, ["release"]),
]
MODEL_PAGES = [
    ({"docs/model/route.md": "# r\n"}, ["skills", "route_model"]),
    ({"docs/model/drain.md": None}, ["skills", "drain_model"]),
    (
        {"docs/model/route.md": "# r\n", "docs/model/drain.md": "# d\n", "README.md": "# B\n"},
        ["skills", "drain_model", "route_model"],
    ),
]


@pytest.mark.parametrize("case", [*MODEL_PAGES, *PROSE_PAGES])
def test_a_changed_page_owes_only_the_tests_that_read_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: tuple[dict[str, str | None], list[str]],
) -> None:
    edits, names = case
    said = lane_of_change(tmp_path, edits, monkeypatch)
    assert said == " ".join(["pages", *sorted(f"hooks/tests/test_{name}.py" for name in names)])


@pytest.mark.parametrize(
    "edits",
    [
        {"docs/adr/0002-new.md": "# 2\n"},
        {"docs/adr/0001-x.md": "# 1 again\n"},
        {"CHANGELOG.md": "# log\n"},
        {"docs/adr/0001-x.md": None},
        {"docs/harness/a.md": "# h\n"},
        {"docs/adr/0002-new.md": "# 2\n", "README.md": "# B\n", "CHANGELOG.md": "# log\n"},
    ],
)
def test_a_change_of_records_and_pages_alone_takes_the_light_lane(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, edits: dict[str, str | None]
) -> None:
    assert lane_of_change(tmp_path, edits, monkeypatch).split()[0] == "pages"


@pytest.mark.parametrize(
    "edits",
    [
        {"src/app.py": "y = 2\n"},
        {"docs/model/new.md": "# new\n"},
        {"docs/deep/er/page.md": "# deeper\n"},
        {"docs/process/binding.md": "# b\n"},
        {"docs/model/route/view_flag.mjs": "x\n"},
        {"docs/model/route.md": "# r\n", "docs/model/new.md": "# new\n"},
        {"skills/feature/SKILL.md": "# skill again\n"},
        {"skills/adopt/templates/docs/adr/0000-x.md": "# t\n"},
        {"docs/notes.txt": "plain\n"},
        {"docs/image.png": "png\n"},
        {"src/README.md": "# nested\n"},
        {"readme.md": "# lower\n"},
        {".github/PULL_REQUEST_TEMPLATE.md": "# t\n"},
        {"evals/x/docs/page.md": "# eval\n"},
        {"docs.md": "# not under docs\n"},
        {"docs/adr/0002-new.md": "# 2\n", "src/app.py": "y = 2\n"},
        {"README.md": "# B\n", "skills/feature/SKILL.md": "# skill again\n"},
        {"src/app.py": None},
    ],
)
def test_a_change_that_touches_anything_else_runs_the_full_suite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, edits: dict[str, str | None]
) -> None:
    assert lane_of_change(tmp_path, edits, monkeypatch) == "full"


@pytest.mark.parametrize("path", CLASSIFIER_PATHS)
def test_a_change_to_the_classifier_the_justfile_or_the_workflows_is_never_light(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    alone = lane_of_change(tmp_path / "alone", {path: "changed\n"}, monkeypatch)
    mixed = lane_of_change(
        tmp_path / "mixed", {path: "changed\n", "docs/adr/0002-new.md": "# 2\n"}, monkeypatch
    )
    assert (alone, mixed) == ("full", "full")


@pytest.mark.parametrize("path", CLASSIFIER_PATHS)
def test_no_glob_of_the_list_matches_the_classifier_the_justfile_or_the_workflows(
    path: str,
) -> None:
    assert lane.owed([path]) is None
    assert not any(rule.fullmatch(path) for rule, _ in lane.RULES)


def test_a_rename_counts_by_both_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    moves = {
        "out": ("docs/model/route.md", "scripts/page.py"),
        "in": ("src/app.py", "docs/model/app.md"),
        "within": ("docs/model/route.md", "docs/adr/0002-page.md"),
    }
    found = []
    for name, (old, new) in moves.items():
        root = repository(tmp_path / name, dict(BASE))
        branch(root)
        git(root, "mv", old, new)
        commit(root, "docs: move")
        monkeypatch.chdir(root)
        found.append(lane.lane_for("main"))
    assert found == [
        "full",
        "full",
        "pages hooks/tests/test_route_model.py hooks/tests/test_skills.py",
    ]


def test_the_diff_is_against_the_merge_base_not_the_tip_of_the_base(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository(tmp_path, dict(BASE))
    branch(tmp_path)
    change(tmp_path, {"docs/adr/0002-new.md": "# 2\n"})
    git(tmp_path, "checkout", "-q", "main")
    change(tmp_path, {"src/app.py": "y = 2\n"})
    git(tmp_path, "checkout", "-q", "change")
    monkeypatch.chdir(tmp_path)
    assert lane.lane_for("main") == "pages"


def test_an_empty_diff_runs_the_full_suite(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repository(tmp_path, dict(BASE))
    branch(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert lane.lane_for("main") == "full"


def test_no_base_runs_the_full_suite(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repository(tmp_path, dict(BASE))
    branch(tmp_path)
    change(tmp_path, {"README.md": "# B\n"})
    monkeypatch.chdir(tmp_path)
    assert lane.lane_for("") == "full"


def test_a_base_git_cannot_find_runs_the_full_suite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository(tmp_path, dict(BASE))
    branch(tmp_path)
    change(tmp_path, {"README.md": "# B\n"})
    monkeypatch.chdir(tmp_path)
    assert lane.lane_for("no-such-ref") == "full"


def test_a_git_that_cannot_start_runs_the_full_suite(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(*_args: object, **_kwargs: object) -> None:
        raise FileNotFoundError

    monkeypatch.setattr(subprocess, "run", missing)
    assert lane.lane_for("main") == "full"


def test_a_name_that_is_not_text_runs_the_full_suite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository(tmp_path, dict(BASE))
    branch(tmp_path)
    odd = tmp_path / b"docs/\xff.md".decode("utf-8", "surrogateescape")
    odd.write_text("# odd\n", encoding="utf-8")
    commit(tmp_path, "docs: odd")
    monkeypatch.chdir(tmp_path)
    assert lane.lane_for("main") == "full"


def test_a_listing_that_does_not_end_in_a_separator_runs_the_full_suite(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    answers = iter([b"abc\n", b"docs/a.md"])
    monkeypatch.setattr(lane, "git", lambda *_args: next(answers))
    assert lane.changed("main") is None


def test_the_lane_is_printed_for_the_workflow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repository(tmp_path, dict(BASE))
    branch(tmp_path)
    change(tmp_path, {"README.md": "# B\n"})
    monkeypatch.chdir(tmp_path)
    assert lane.main(["lane.py", "main"]) == 0
    assert lane.main(["lane.py"]) == 0
    assert capsys.readouterr().out == "pages hooks/tests/test_skills.py\nfull\n"


def test_every_model_page_a_test_reads_is_mapped_to_that_test() -> None:
    tests = Path(__file__).parent
    named = {
        match.group(0): path.name
        for path in tests.glob("test_*.py")
        for match in re.finditer(r"docs/model/[a-z-]+\.md", path.read_text(encoding="utf-8"))
        if f'PLUGIN_ROOT / "{match.group(0)}"' in path.read_text(encoding="utf-8")
    }
    assert named
    for page, name in named.items():
        assert f"hooks/tests/{name}" in lane.PAGES[page]


def test_every_test_file_the_mapping_names_exists() -> None:
    root = Path(__file__).resolve().parents[2]
    owed = {name for names in lane.PAGES.values() for name in names}
    assert owed
    assert all((root / name).is_file() for name in owed)


def test_the_changelog_owes_the_test_that_reads_its_notes_through_the_release_script() -> None:
    tests = Path(__file__).parent
    readers = {
        p.name
        for p in tests.glob("test_*.py")
        if p.name != "test_lane.py"
        and "PLUGIN_ROOT" in (text := p.read_text(encoding="utf-8"))
        and "release.notes(PLUGIN_ROOT" in text
    }
    assert readers == {"test_release.py"}
    assert lane.PAGES["CHANGELOG.md"] == tuple(f"hooks/tests/{name}" for name in sorted(readers))


NOT_SEEN = (
    "The scan reads source and does not see a path a function receives, a loop builds or another"
    " module names, nor a read a script, git or a subprocess makes for the test."
)


def unmapped_reads() -> list[tuple[str, int, str]]:
    found = []
    for name, (hits, _dynamic) in page_reads.reads().items():
        for line, path in sorted(hits):
            owed = {
                test
                for glob, tests in lane.PAGES.items()
                for test in tests
                if lane.pattern(glob).fullmatch(path) or glob.startswith(f"{path}/") or path == "*"
            }
            if f"hooks/tests/{name}" not in owed:
                found.append((name, line, path))
    return found


def covers_adrs(path: str) -> bool:
    return path in {"*", "docs"} or path == "docs/adr" or path.startswith("docs/adr/")


def test_a_test_the_mapping_does_not_name_reads_no_record_by_a_path_its_source_builds() -> None:
    found = [read for read in unmapped_reads() if covers_adrs(read[2])]
    assert found == [], f"reads docs/adr, which lane.PAGES maps to no such test. {NOT_SEEN}"


def test_a_test_the_mapping_does_not_name_reads_no_page_by_a_path_its_source_builds() -> None:
    found = [read for read in unmapped_reads() if not covers_adrs(read[2])]
    assert found == [], f"reads a page lane.PAGES does not map to this test. {NOT_SEEN}"


def test_the_scan_follows_a_path_through_names_joins_globs_and_parents(tmp_path: Path) -> None:
    source = tmp_path / "test_x.py"
    source.write_text(
        "from pathlib import Path\n"
        "from tests.support import PLUGIN_ROOT\n"
        "HERE = Path(__file__).resolve().parents[2]\n"
        "SKILLS = HERE / 'skills'\n"
        "ADRS = SKILLS.parent / 'docs' / 'adr'\n"
        "a = ADRS.glob('0034-*.md')\n"
        "b = PLUGIN_ROOT.joinpath('docs', 'harness').rglob('*.md')\n"
        "c = Path('README.md').read_text()\n"
        "d = (HERE / 'CHANGELOG.md').read_text()\n"
        "e = [p for p in HERE.iterdir()]\n"
        "f = PLUGIN_ROOT / name\n"
        "g = PLUGIN_ROOT / 'skills' / 'x.md'\n",
        encoding="utf-8",
    )
    scan = page_reads.Scan(source)
    paths = {path for _line, path in scan.hits}
    assert paths == {
        "docs",
        "docs/adr",
        "docs/adr/0034-*.md",
        "docs/harness",
        "docs/harness/*.md",
        "README.md",
        "CHANGELOG.md",
        "*",
    }
    assert {path for _line, path in scan.dynamic} == {""}


def test_a_path_joined_to_the_root_by_a_name_the_scan_cannot_resolve_is_reviewed() -> None:
    reviewed = {
        "test_route_model.py": "the skills pages its divergences name",
        "test_skills.py": "skills, workflows and scripts",
        "test_owed.py": "skills, scripts and .claude-plugin to copy",
        "test_stack_names.py": "the shipped globs: skills, workflows, scripts, hooks",
        "test_lane.py": "the files lane.PAGES names",
        "repos.py": "every tracked file, as an installed copy the tests hash or run as the plugin",
        "test_release.py": "the manifest and workflows; CHANGELOG.md through release.notes",
    }
    joined = {
        path.name
        for path in page_reads.TESTS.glob("*.py")
        if any(tail == "" for _line, tail in page_reads.Scan(path).dynamic)
    }
    assert joined <= set(reviewed), "a new unresolved join to the root: review what it reads"
