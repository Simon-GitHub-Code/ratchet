"""The grouped coverage gate over a Cobertura report."""

from pathlib import Path

import pytest

import coverage_groups
from tests.repos import write

THRESHOLDS = """# thresholds
[python.coverage.core]
paths = ["src/core/**"]
absolute = true

[python.coverage.app]
paths = ["src/app/**"]
line = 50.0

[ceilings.seconds]
tier0 = 60
"""


def report(root: Path, *, core_hits: int = 1, app_hits: int = 1, extra: str = "") -> Path:
    def cls(filename: str, hits: int) -> str:
        return (
            f'<class filename="{filename}"><lines><line number="1" hits="{hits}"/>'
            f'<line number="2" hits="1" branch="true" condition-coverage="50% (1/2)"/>'
            "</lines></class>"
        )

    xml = (
        f"<coverage><sources><source>{root}</source></sources><packages><package><classes>"
        f"{cls('src/core/a.py', core_hits)}{cls('src/app/b.py', app_hits)}{extra}"
        "</classes></package></packages></coverage>"
    )
    path = root / "coverage.xml"
    path.write_text(xml)
    return path


def project(tmp_path: Path, thresholds: str = THRESHOLDS) -> Path:
    write(tmp_path, "quality/thresholds.toml", thresholds)
    write(tmp_path, "src/core/a.py", "x = 1\ny = 2\n")
    write(tmp_path, "src/app/b.py", "x = 1\ny = 2\n")
    return tmp_path


def test_groups_are_read_from_every_language_table() -> None:
    found = coverage_groups.groups(
        {
            "python": {"coverage": {"core": {"paths": ["a"], "absolute": True}, "bad": 3}},
            "ts": {"coverage": {"web": {"paths": "x", "line": 1, "branch": True}}},
            "ceilings": {"seconds": {"tier0": 1}},
            "name": "x",
        }
    )
    assert [(g.key, g.paths, g.absolute, g.line, g.branch) for g in found] == [
        ("python.coverage.core", ["a"], True, None, None),
        ("ts.coverage.web", [], False, 1.0, None),
    ]


def test_absolute_groups_need_full_coverage_and_no_exclusion(tmp_path: Path) -> None:
    root = project(tmp_path)
    assert coverage_groups.problems(root, [report(root)]) == [
        "python.coverage.core: branch coverage 50.0% on an absolute path, needs 100%",
        "python.coverage.app: no branch threshold recorded; `--write` records 50.0%",
    ]
    write(root, "src/core/a.py", "x = 1  # pragma: no cover\ny = 2\n")
    full = report(root).read_text().replace('condition-coverage="50% (1/2)"', "")
    (root / "coverage.xml").write_text(full)
    write(
        root,
        "quality/thresholds.toml",
        THRESHOLDS.replace("line = 50.0", "line = 50.0\nbranch = 50.0"),
    )
    assert coverage_groups.problems(root, [root / "coverage.xml"]) == [
        "src/core/a.py:1: a coverage exclusion on an absolute path",
    ]


def test_ratcheted_groups_compare_and_write_raises_only(tmp_path: Path) -> None:
    root = project(tmp_path)
    report(root, app_hits=0)
    assert coverage_groups.problems(root, [root / "coverage.xml"]) == [
        "python.coverage.core: branch coverage 50.0% on an absolute path, needs 100%",
        "python.coverage.app: no branch threshold recorded; `--write` records 50.0%",
    ]
    assert coverage_groups.main(["x", "--root", str(root), "--write"]) == 1
    text = (root / "quality/thresholds.toml").read_text()
    assert '[python.coverage.app]\npaths = ["src/app/**"]\nline = 50.0\nbranch = 50.0\n' in text
    assert "absolute = true\n\n[python.coverage.app]" in text, "the absolute group is untouched"
    report(root, app_hits=1)
    coverage_groups.main(["x", "--root", str(root), "--write"])
    text = (root / "quality/thresholds.toml").read_text()
    assert "line = 100.0\nbranch = 50.0\n" in text
    write(root, "quality/thresholds.toml", text.replace("line = 100.0", "line = 60.0"))
    report(root, app_hits=0)
    assert coverage_groups.problems(root, [root / "coverage.xml"])[-1] == (
        "python.coverage.app: line coverage 50.0% below the ratchet 60.0%"
    )
    coverage_groups.main(["x", "--root", str(root), "--write"])
    assert "line = 60.0" in (root / "quality/thresholds.toml").read_text(), "never lowered"


def test_a_file_in_no_group_fails_and_paths_resolve_through_sources(tmp_path: Path) -> None:
    root = project(tmp_path)
    write(root, "scripts/x.py", "x\n")
    path = report(root, extra='<class filename="scripts/x.py"><lines/></class>')
    assert (
        coverage_groups.problems(root, [path])[0]
        == "scripts/x.py: measured but in no coverage group"
    )
    assert coverage_groups.relative("/elsewhere/a.py", [], root) == "/elsewhere/a.py"
    assert coverage_groups.relative("a.py", ["/elsewhere"], root) == "a.py"
    assert coverage_groups.relative(str(root / "src/core/a.py"), [], root) == "src/core/a.py"
    assert coverage_groups.relative("src/core/a.py", [str(root)], root) == "src/core/a.py"
    assert coverage_groups.relative("src/core/a.py", [], root) == "src/core/a.py"


def test_missing_inputs_are_reported(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert coverage_groups.main(["x", "--root", str(tmp_path)]) == 1
    assert "coverage.xml: missing" in capsys.readouterr().out
    report(tmp_path)
    assert coverage_groups.problems(tmp_path, [tmp_path / "coverage.xml"]) == [
        "quality/thresholds.toml: no [<language>.coverage.<group>] table with paths"
    ]
    assert (
        coverage_groups.main(
            ["x", "--root", str(tmp_path), "--report", str(tmp_path / "coverage.xml")]
        )
        == 1
    )


def test_tally_rates_and_exclusion_scan_skip_data_files(tmp_path: Path) -> None:
    tally = coverage_groups.Tally()
    assert tally.rate("line") == 100.0
    assert tally.rate("branch") == 100.0
    assert not tally.measured
    assert coverage_groups.Tally(files=["a"]).measured
    write(tmp_path, "a.json", "pragma: no cover\n")
    write(tmp_path, "a.py", "# istanbul ignore next\n")
    assert coverage_groups.exclusions(tmp_path, ["a.json", "a.py", "missing.py"]) == [
        "a.py:1: a coverage exclusion on an absolute path"
    ]


def test_raised_inserts_a_missing_key_after_the_header() -> None:
    group = coverage_groups.Group("py.coverage.app", ["x"], absolute=False, line=None, branch=10.0)
    tally = coverage_groups.Tally(lines=2, lines_hit=1, branches=4, branches_hit=1)
    text = '[py.coverage.app]\npaths = ["x"]\nbranch = 10.0\n\n[other]\nbranch = 1\n'
    assert coverage_groups.raised(text, group, tally) == (
        '[py.coverage.app]\npaths = ["x"]\nbranch = 25.0\nline = 50.0\n\n[other]\nbranch = 1\n'
    )


TWO_STACKS = """[python.coverage.app]
paths = ["backend/**"]
line = 50.0
branch = 50.0

[typescript.coverage.app]
paths = ["frontend/src/**"]
line = 50.0
branch = 50.0
"""


def cobertura(root: Path, path: str, filename: str, *, hits: int) -> Path:
    xml = (
        f"<coverage><sources><source>{root}</source></sources><packages><package><classes>"
        f'<class filename="{filename}"><lines><line number="1" hits="{hits}"/>'
        f'<line number="2" hits="1" branch="true" condition-coverage="50% (1/2)"/>'
        "</lines></class></classes></package></packages></coverage>"
    )
    write(root, path, xml)
    return root / path


def test_an_unmeasured_group_is_not_scored_and_not_written(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Why: issue #1. Judging the backend report alone scored every typescript group 100% and
    # `--write` recorded it, so the frontend report could never pass again.
    root = tmp_path
    write(root, "quality/thresholds.toml", TWO_STACKS)
    write(root, "backend/a.py", "x = 1\ny = 2\n")
    write(root, "frontend/src/b.ts", "x\ny\n")
    backend = cobertura(root, "backend/coverage.xml", "backend/a.py", hits=1)
    assert (
        coverage_groups.main(["x", "--root", str(root), "--report", str(backend), "--write"]) == 0
    )
    out = capsys.readouterr().out
    assert "typescript.coverage.app: not measured; no file in the report falls in it" in out
    assert "python.coverage.app: 100.0% lines, 50.0% branches" in out
    text = (root / "quality/thresholds.toml").read_text()
    assert text.count("line = 100.0") == 1, "only the measured group was raised"
    assert 'paths = ["frontend/src/**"]\nline = 50.0\nbranch = 50.0\n' in text


def test_several_reports_are_tallied_together(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path
    write(root, "quality/thresholds.toml", TWO_STACKS)
    write(root, "backend/a.py", "x = 1\ny = 2\n")
    write(root, "frontend/src/b.ts", "x\ny\n")
    backend = cobertura(root, "backend/coverage.xml", "backend/a.py", hits=1)
    frontend = cobertura(root, "frontend/cobertura-coverage.xml", "frontend/src/b.ts", hits=0)
    assert (
        coverage_groups.main(
            ["x", "--root", str(root), "--report", str(backend), "--report", str(frontend)]
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "python.coverage.app: 100.0% lines, 50.0% branches" in out
    assert "typescript.coverage.app: 50.0% lines, 50.0% branches" in out
    assert "not measured" not in out
    frontend.unlink()
    assert coverage_groups.problems(root, [backend, frontend]) == [
        f"{frontend}: missing; run the suite with a Cobertura report first"
    ]
