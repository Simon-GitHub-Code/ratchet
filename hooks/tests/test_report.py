"""``report``: a shared report is produced once for a run and read by every gate that asks."""

import os
import stat
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

import gatelib
import report
from tests import repos

HEAD = "| Report | Producer | Paths | Readers |\n| --- | --- | --- | --- |\n"
SUITE = "| suite | `just suite-report` | `out.txt` | `just test`, `just coverage` |\n"
FAKE_JUST = (
    "#!/bin/sh\n"
    'echo "$@" >> "$COUNT"\n'
    "sleep 0.3\n"
    '[ -n "$WRITES" ] && echo built > out.txt\n'
    'exit "${STATUS:-0}"\n'
)


NO_TABLE = (
    "'## Shared reports' lacks its table with the columns Report | Producer | Paths | Readers"
)


def unreadable(name: str) -> str:
    return (
        f"the shared reports row '{name}' needs a name, one `just <recipe>` producer,"
        " backticked paths and backticked `just <recipe>` readers"
    )


def binding(rows: str = SUITE, heading: str = report.SECTION) -> str:
    return f"# Binding\n\n{heading}\n\nWords.\n\n{HEAD}{rows}\n## Weekly\n\nWords.\n"


@pytest.fixture
def work(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    tools = tmp_path / "tools"
    tools.mkdir()
    just = repos.hook_script(tools, "just", "")
    just.write_text(FAKE_JUST, encoding="utf-8")
    root = repos.repository(
        tmp_path / "repo",
        {str(gatelib.BINDING): binding(), "src.txt": "one\n", ".gitignore": "out.txt\n"},
    )
    monkeypatch.setenv("PATH", f"{tools}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("COUNT", str(tmp_path / "count"))
    monkeypatch.setenv("WRITES", "1")
    for name in (report.RUN, "SHARD", "SHARDS", "STATUS"):
        monkeypatch.delenv(name, raising=False)
    return root


def runs(root: Path) -> int:
    count = root.parent / "count"
    return len(count.read_text(encoding="utf-8").splitlines()) if count.exists() else 0


def ask(root: Path, name: str = "suite") -> int:
    return report.main([name, "--root", str(root)])


def test_concurrent_readers_run_the_producer_once_and_all_read_what_it_wrote(
    work: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(report.RUN, "run-1")
    with ThreadPoolExecutor(4) as pool:
        statuses = list(pool.map(lambda _: ask(work), range(4)))
    assert statuses == [0, 0, 0, 0]
    assert runs(work) == 1
    assert capfd.readouterr().out.splitlines() == ["out.txt"] * 4


def test_a_report_is_reused_within_a_run_on_an_unchanged_tree(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(report.RUN, "run-1")
    assert [ask(work), ask(work)] == [0, 0]
    assert runs(work) == 1


def test_a_changed_tree_produces_the_report_again(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(report.RUN, "run-1")
    ask(work)
    (work / "src.txt").write_text("two\n", encoding="utf-8")
    ask(work)
    assert runs(work) == 2
    (work / "new.txt").write_text("three\n", encoding="utf-8")
    ask(work)
    assert runs(work) == 3
    ask(work)
    assert runs(work) == 3


def test_what_the_state_folder_holds_is_not_the_trees_content(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(report.RUN, "run-1")
    ask(work)
    (work / gatelib.STATE / "timing.jsonl").write_text("{}\n", encoding="utf-8")
    ask(work)
    assert runs(work) == 1


def test_a_new_run_produces_the_report_again(work: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(report.RUN, "run-1")
    ask(work)
    monkeypatch.setenv(report.RUN, "run-2")
    ask(work)
    assert runs(work) == 2


def test_with_no_run_id_every_ask_produces_the_report(work: Path) -> None:
    assert [ask(work), ask(work)] == [0, 0]
    assert runs(work) == 2


def test_a_failed_producers_exit_status_reaches_every_reader(
    work: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(report.RUN, "run-1")
    monkeypatch.setenv("STATUS", "3")
    with ThreadPoolExecutor(3) as pool:
        statuses = list(pool.map(lambda _: ask(work), range(3)))
    assert statuses == [1, 1, 1]
    assert runs(work) == 1
    lines = capfd.readouterr().err.splitlines()
    assert lines == ["report suite: `just suite-report` exited 3; `test` judges the failure"] * 3


def test_a_report_is_kept_for_each_shard(work: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(report.RUN, "run-1")
    monkeypatch.setenv("SHARDS", "2")
    for shard in ("1", "2", "1", "2"):
        monkeypatch.setenv("SHARD", shard)
        ask(work)
    assert runs(work) == 2


def test_a_producer_that_writes_no_path_is_red_with_the_path(
    work: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("WRITES", "")
    assert ask(work) == 1
    assert capfd.readouterr().err == "report suite: `just suite-report` wrote no out.txt\n"


def test_a_record_that_does_not_read_is_produced_again(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(report.RUN, "run-1")
    ask(work)
    for record in (work / report.FOLDER).glob("*.json"):
        record.write_text("[1]", encoding="utf-8")
    ask(work)
    assert runs(work) == 2


def test_a_report_the_binding_does_not_name_is_red(
    work: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    assert ask(work, "other") == 1
    assert capfd.readouterr().err == f"report: '{report.SECTION}' names no report 'other'\n"
    assert runs(work) == 0


def test_a_binding_that_does_not_read_is_red(work: Path, capfd: pytest.CaptureFixture[str]) -> None:
    (work / gatelib.BINDING).write_text(binding(heading="## Elsewhere"), encoding="utf-8")
    assert ask(work) == 1
    assert capfd.readouterr().err == (
        f"report: {gatelib.BINDING}: has no '{report.SECTION}' section\n"
    )


def test_the_content_id_follows_what_git_would_add(work: Path) -> None:
    before = report.content_id(work)
    assert report.content_id(work) == before
    (work / "src.txt").chmod(0o755)
    assert report.content_id(work) != before
    assert (work / "src.txt").stat().st_mode & stat.S_IXUSR
    (work / "src.txt").chmod(0o644)
    assert report.content_id(work) == before
    (work / "link").symlink_to("src.txt")
    linked = report.content_id(work)
    assert linked != before
    (work / "src.txt").unlink()
    assert report.content_id(work) != linked


def test_the_content_id_passes_over_a_nested_repository(work: Path) -> None:
    before = report.content_id(work)
    repos.repository(work / "nested")
    assert report.content_id(work) == before


def test_the_content_id_of_a_folder_with_no_repository_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(subprocess.CalledProcessError):
        report.content_id(tmp_path / "missing")


def test_the_table_names_each_report() -> None:
    reports, problems = report.parse(binding())
    assert problems == []
    assert reports == [
        report.Report("suite", "just suite-report", ("out.txt",), ("just test", "just coverage"))
    ]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (None, "has no '## Shared reports' section"),
        (binding(heading="## Elsewhere"), "has no '## Shared reports' section"),
        (
            "## Shared reports\n\nWords.\n",
            NO_TABLE,
        ),
        (
            binding("| <name> | <`just recipe`> | <`path`> | <`just recipe`> |\n"),
            "the shared reports table still has the template's placeholder row",
        ),
        (
            binding("| none: x | none | none | none |\n"),
            "a shared reports row says none with no reason: say why",
        ),
        (binding("| suite | `just a` | | `just b` |\n"), unreadable("suite")),
        (binding("| suite | `a` | `p` | `just b` |\n"), unreadable("suite")),
        (binding("| suite | `just a` | `p` | `b` |\n"), unreadable("suite")),
        (binding("| suite | `just a` | `p` | |\n"), unreadable("suite")),
        (binding("| 1 | `just a` | `p` | `just b` |\n"), unreadable("1")),
        (
            binding(SUITE + SUITE),
            "two shared reports rows have one name",
        ),
    ],
)
def test_a_binding_that_does_not_name_its_reports_is_refused(
    text: str | None, expected: str
) -> None:
    reports, problems = report.parse(text)
    assert reports is None
    assert problems[-1] == expected


def test_a_binding_that_shares_none_names_no_report() -> None:
    assert report.parse(binding("| none: the suite is one gate's own | | | |\n")) == ([], [])
