import fcntl
import os
import stat
import subprocess
import sys
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

import gatelib
import report
from tests import repos

HEAD = "| Report | Producer | Paths | Readers | Judged by |\n| --- | --- | --- | --- | --- |\n"
SUITE = "| suite | `just suite-report` | `out.txt` | `just coverage`, `just test` | `just test` |\n"
FAKE_JUST = (
    "#!/bin/sh\n"
    'echo "$@" >> "$COUNT"\n'
    '[ -n "$PRINT" ] && echo "$PRINT"\n'
    'if [ -n "$RELEASE" ]; then\n'
    '  echo start >> "$MARKS"\n'
    '  while [ ! -e "$RELEASE" ]; do sleep 0.05; done\n'
    '  echo end >> "$MARKS"\n'
    "fi\n"
    "sleep 0.3\n"
    '[ -n "$WRITES" ] && echo built > out.txt\n'
    'exit "${STATUS:-0}"\n'
)


NO_TABLE = (
    "'## Shared reports' lacks its table with the columns"
    " Report | Producer | Paths | Readers | Judged by"
)


def unreadable(name: str) -> str:
    return (
        f"the shared reports row '{name}' needs a name, one `just <recipe>` producer,"
        " backticked paths and backticked `just <recipe>` readers"
    )


def judgeless(name: str) -> str:
    return (
        f"the shared reports row '{name}' needs exactly one judge in `Judged by`,"
        " and it is one of the row's readers"
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
    monkeypatch.setenv("PRINT", "")
    monkeypatch.setenv("RELEASE", "")
    monkeypatch.setenv("MARKS", str(tmp_path / "marks"))
    for name in (report.RUN, "SHARD", "SHARDS", "STATUS"):
        monkeypatch.delenv(name, raising=False)
    return root


def runs(root: Path) -> int:
    count = root.parent / "count"
    return len(count.read_text(encoding="utf-8").splitlines()) if count.exists() else 0


def ask(root: Path, name: str = "suite", reader: str = "test") -> int:
    return report.main([name, "--reader", reader, "--root", str(root)])


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
    monkeypatch.setenv("PRINT", "FAILED test_the_distinctive_one")
    assert ask(work, reader="coverage") == 1
    first = capfd.readouterr().err
    assert first == "report suite: `just suite-report` exited 3; `test` judges the failure\n"
    assert ask(work, reader="test") == 1
    judged = capfd.readouterr().err
    assert judged == (
        "FAILED test_the_distinctive_one\n"
        "report suite: `just suite-report` exited 3; `test` judges the failure\n"
    )
    assert runs(work) == 1


def test_a_reader_the_row_does_not_list_is_refused(
    work: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    assert ask(work, reader="mutation") == 2
    assert capfd.readouterr().err == (
        "report suite: 'mutation' is not one of its readers in '## Shared reports'\n"
    )
    assert runs(work) == 0


def test_a_reader_is_required() -> None:
    with pytest.raises(SystemExit) as stop:
        report.main(["suite"])
    assert stop.value.code == 2


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
        report.Report(
            "suite", "just suite-report", ("out.txt",), ("just coverage", "just test"), "just test"
        )
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
            binding(
                "| <name> | <`just recipe`> | <`path`> | <`just recipe`> | <`just recipe`> |\n"
            ),
            report.PLACEHOLDER,
        ),
        (
            binding("| none: x | none | none | none | none |\n"),
            "a shared reports row says none with no reason: say why",
        ),
        (binding("| suite | `just a` | | `just b` | `just b` |\n"), unreadable("suite")),
        (binding("| suite | `a` | `p` | `just b` | `just b` |\n"), unreadable("suite")),
        (binding("| suite | `just a` | `p` | `b` | `b` |\n"), unreadable("suite")),
        (binding("| suite | `just a` | `p` | | |\n"), unreadable("suite")),
        (binding("| 1 | `just a` | `p` | `just b` | `just b` |\n"), unreadable("1")),
        (binding("| suite | `just a` | `p` | `just b` | `just c` |\n"), judgeless("suite")),
        (
            binding("| suite | `just a` | `p` | `just b`, `just c` | `just b`, `just c` |\n"),
            judgeless("suite"),
        ),
        (binding("| suite | `just a` | `p` | `just b` | |\n"), judgeless("suite")),
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


def test_a_failed_producer_names_the_judge_of_its_row(
    work: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    row = SUITE.replace("| `just test` |\n", "| `just coverage` |\n")
    (work / gatelib.BINDING).write_text(binding(row), encoding="utf-8")
    monkeypatch.setenv("STATUS", "2")
    assert ask(work) == 1
    assert capfd.readouterr().err == (
        "report suite: `just suite-report` exited 2; `coverage` judges the failure\n"
    )


def test_a_report_with_fewer_than_two_readers_is_not_shared() -> None:
    row = "| suite | `just a` | `p` | `just b` | `just b` |\n"
    reports, problems = report.parse(binding(row))
    assert reports is None
    problem = (
        "the shared reports row 'suite' has fewer than two readers, so it is not shared:"
        " say `none: <reason>` in its Report cell"
    )
    assert problems == [problem]


def test_the_name_may_be_backticked_and_each_producer_is_listed() -> None:
    row = "| `suite` | `just a` | `p` | `just b`, `just c` | `just b` |\n"
    text = binding(row + "| none: x is one gate's | none | none | none | none |\n")
    reports, _ = report.parse(text)
    assert reports is not None
    assert [found.name for found in reports] == ["suite"]
    assert report.producers(text) == [("suite", "a")]
    assert report.producers("# Binding\n") == []


def test_a_binding_that_shares_none_names_no_report() -> None:
    assert report.parse(
        binding("| none: the suite is one gate's own | none | none | none | none |\n")
    ) == ([], [])


def test_a_path_is_printed_alone_and_one_the_row_does_not_list_is_refused(
    work: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    assert report.main(["suite", "--reader", "test", "--path", "out.txt", "--root", str(work)]) == 0
    assert capfd.readouterr().out == "out.txt\n"
    args = ["suite", "--reader", "test", "--path", "other.txt", "--root", str(work)]
    assert report.main(args) == 2
    assert capfd.readouterr().err == (
        "report suite: 'other.txt' is not a path the suite row of ## Shared reports in"
        " docs/process/binding.md lists\n"
    )


def test_a_report_produced_again_in_one_run_says_why(
    work: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(report.RUN, "run-1")
    ask(work)
    assert capfd.readouterr().err == ""
    (work / "src.txt").write_text("two\n", encoding="utf-8")
    ask(work)
    assert capfd.readouterr().err == (
        "report suite: produced again, because the tree's content changed since this run"
        " produced it\n"
    )
    monkeypatch.setenv(report.RUN, "run-2")
    (work / "src.txt").write_text("three\n", encoding="utf-8")
    ask(work)
    assert capfd.readouterr().err == ""


def waiting_for(condition: Callable[[], bool], what: str) -> None:
    deadline = time.monotonic() + 30
    while not condition():
        assert time.monotonic() < deadline, what
        time.sleep(0.05)


def test_a_reader_killed_mid_production_leaves_the_lock_held_and_no_record(
    work: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    release = tmp_path / "release"
    marks = tmp_path / "marks"
    monkeypatch.setenv(report.RUN, "run-1")
    monkeypatch.setenv("RELEASE", str(release))
    command = [sys.executable, report.__file__, "suite", "--reader", "test", "--root", str(work)]
    first = subprocess.Popen(  # noqa: S603 # -- a fixed argv, no shell
        command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    second = None
    try:
        waiting_for(marks.exists, "the producer started")
        first.kill()
        first.wait()
        folder = work / report.FOLDER
        assert not list(folder.glob("*.json"))
        with (folder / "suite-1of1.lock").open("w") as lock, pytest.raises(BlockingIOError):
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        second = subprocess.Popen(  # noqa: S603 # -- a fixed argv, no shell
            command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        assert marks.read_text(encoding="utf-8").split() == ["start"]
    finally:
        release.write_text("", encoding="utf-8")
        if second is not None:
            assert second.wait(timeout=60) == 0
    waiting_for(lambda: marks.read_text(encoding="utf-8").split().count("end") == 2, "both ended")
    assert marks.read_text(encoding="utf-8").split() == ["start", "end", "start", "end"]
