import os
import sys
from pathlib import Path

import pytest

import build_cache
from tests.repos import repository

HEADER = "# Binding\n\n## Speed\n\n"


def speed_doc(line: str) -> str:
    return f"{HEADER}{line}\n"


def test_parse_returns_none_and_no_errors_when_absent_placeholder_or_none_with_reason() -> None:
    for text in (
        "",
        "# Binding\n\n## Speed\n\n- Parallel: all\n",
        speed_doc(
            "- Build: <gate `build`; caches `path`, ...; cold Ns; warm Ns;"
            " reuse moved | shared `ENV=dir` | none: reason; consumers `smoke`, ...;"
            " leave this placeholder while no gate builds>"
        ),
        speed_doc("- Build: none: interpreted project with no build step"),
    ):
        assert build_cache.parse_build_spec(text) is None
        assert build_cache.validate_build_spec(text) == []


def test_parse_extracts_moved_shared_and_none_reuse_policies() -> None:
    moved = speed_doc(
        "- Build: gate `build`; caches `.build-cache`, `dist`; cold 180s; warm 30s;"
        " reuse moved; consumers `smoke`, `e2e`"
    )
    spec = build_cache.parse_build_spec(moved)
    assert spec == build_cache.BuildSpec(
        gate="build",
        cache_paths=(".build-cache", "dist"),
        cold_seconds=180.0,
        warm_seconds=30.0,
        reuse=build_cache.ReusePolicy(kind=build_cache.ReuseKind.MOVED),
        consumers=("smoke", "e2e"),
    )
    assert build_cache.validate_build_spec(moved, known_gates={"build", "smoke", "e2e"}) == []

    shared = speed_doc(
        "- Build: gate `compile`; caches `out`; cold 90.5s; warm 0s;"
        " reuse shared `TOOL_CACHE=/var/cache/tool`; consumers none"
    )
    spec_shared = build_cache.parse_build_spec(shared)
    assert spec_shared == build_cache.BuildSpec(
        gate="compile",
        cache_paths=("out",),
        cold_seconds=90.5,
        warm_seconds=0.0,
        reuse=build_cache.ReusePolicy(
            kind=build_cache.ReuseKind.SHARED,
            env_name="TOOL_CACHE",
            env_dir="/var/cache/tool",
        ),
        consumers=(),
    )
    assert build_cache.validate_build_spec(shared, known_gates={"compile"}) == []

    none_reuse = speed_doc(
        "- Build: gate `build`; caches none; cold 45s; warm 45s;"
        " reuse none: compiler has no incremental mode"
    )
    spec_none = build_cache.parse_build_spec(none_reuse)
    assert spec_none == build_cache.BuildSpec(
        gate="build",
        cache_paths=(),
        cold_seconds=45.0,
        warm_seconds=45.0,
        reuse=build_cache.ReusePolicy(
            kind=build_cache.ReuseKind.NONE,
            reason="compiler has no incremental mode",
        ),
        consumers=(),
    )
    assert build_cache.validate_build_spec(none_reuse) == []


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("- Build:", "unfilled"),
        ("- Build: none", "`none` with no reason"),
        ("- Build: none:", "`none` with no reason"),
        ("- Build: gate `build`; cold 10s; warm 2s; reuse moved", "missing"),
        ("- Build: ; gate `build`", "empty clause"),
        ("- Build: badclause", "expected `<key> <value>`"),
        ("- Build: unknown `x`", "unknown clause"),
        (
            "- Build: gate `a`; gate `b`; caches `c`; cold 10s; warm 2s; reuse moved",
            "duplicate clause",
        ),
        (
            "- Build: gate `a b`; caches `c`; cold 10s; warm 2s; reuse moved",
            "one backticked gate name",
        ),
        (
            "- Build: gate `a` `b`; caches `c`; cold 10s; warm 2s; reuse moved",
            "one backticked gate name",
        ),
        (
            "- Build: gate a; caches `c`; cold 10s; warm 2s; reuse moved",
            "one backticked gate name",
        ),
        (
            "- Build: gate `a`; caches c; cold 10s; warm 2s; reuse moved",
            "backticked relative paths",
        ),
        (
            "- Build: gate `a`; caches `/abs`; cold 10s; warm 2s; reuse moved",
            "relative paths without `..`",
        ),
        (
            "- Build: gate `a`; caches `../up`; cold 10s; warm 2s; reuse moved",
            "relative paths without `..`",
        ),
        (
            "- Build: gate `a`; caches `has space`; cold 10s; warm 2s; reuse moved",
            "without spaces",
        ),
        (
            "- Build: gate `a`; caches `c`; cold 0s; warm 0s; reuse moved",
            "positive number of seconds",
        ),
        (
            "- Build: gate `a`; caches `c`; cold nope; warm 1s; reuse moved",
            "positive number of seconds",
        ),
        (
            "- Build: gate `a`; caches `c`; cold nopes; warm 1s; reuse moved",
            "positive number of seconds",
        ),
        (
            "- Build: gate `a`; caches `c`; cold 10s; warm -1s; reuse moved",
            "non-negative number of seconds",
        ),
        (
            "- Build: gate `a`; caches `c`; cold 10s; warm infs; reuse moved",
            "non-negative number of seconds",
        ),
        (
            "- Build: gate `a`; caches `c`; cold 10s; warm 2s; reuse magic",
            "`moved`, `shared",
        ),
        (
            "- Build: gate `a`; caches `c`; cold 10s; warm 2s; reuse shared DIR",
            "`ENV=dir`",
        ),
        (
            "- Build: gate `a`; caches `c`; cold 10s; warm 2s; reuse shared `NOEQUALS`",
            "non-empty `ENV=dir`",
        ),
        (
            "- Build: gate `a`; caches `c`; cold 10s; warm 2s; reuse shared `1BAD=/tmp`",
            "valid identifier",
        ),
        (
            "- Build: gate `a`; caches `c`; cold 10s; warm 2s; reuse shared `ENV=a b`",
            "without spaces",
        ),
        (
            "- Build: gate `a`; caches `c`; cold 10s; warm 2s; reuse none:",
            "reason after `none:`",
        ),
        (
            "- Build: gate `a`; caches `c`; cold 10s; warm 2s; reuse moved; consumers smoke",
            "backticked gate names",
        ),
        (
            "- Build: gate `a`; caches `c`; cold 10s; warm 2s; reuse moved; consumers `a b`",
            "without spaces",
        ),
    ],
)
def test_validate_reports_malformed_build_lines(line: str, expected: str) -> None:
    doc = speed_doc(line)
    assert build_cache.parse_build_spec(doc) is None
    errors = build_cache.validate_build_spec(doc)
    assert any(expected in err for err in errors), errors


def test_validate_checks_known_gates_and_self_consumption() -> None:
    doc = speed_doc(
        "- Build: gate `build`; caches `.build`; cold 20s; warm 2s; reuse moved;"
        " consumers `build`, `ghost`"
    )
    errors = build_cache.validate_build_spec(doc, known_gates={"smoke", "lint"})
    assert any("unknown gate `build`" in err for err in errors), errors
    assert any("cannot list its own gate `build`" in err for err in errors), errors
    assert any("unknown consumer gate `ghost`" in err for err in errors), errors


def test_seed_worktree_copies_moved_caches_with_metadata_and_records_missing(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    dest = tmp_path / "dest"
    (source / ".build-cache" / "sub").mkdir(parents=True)
    (source / ".build-cache" / "sub" / "obj.o").write_text("obj", encoding="utf-8")
    (source / ".build-cache" / "link.o").symlink_to("sub/obj.o")
    (source / "dist.bin").write_bytes(b"bin")
    stamp = 1_700_000_000
    os.utime(source / "dist.bin", (stamp, stamp))
    dest.mkdir()
    spec = build_cache.BuildSpec(
        gate="build",
        cache_paths=(".build-cache", "dist.bin", "absent.cache"),
        cold_seconds=60.0,
        warm_seconds=5.0,
        reuse=build_cache.ReusePolicy(kind=build_cache.ReuseKind.MOVED),
    )
    result = build_cache.seed_worktree(source, dest, spec)
    assert result == build_cache.SeedResult(
        kind=build_cache.ReuseKind.MOVED,
        copied=(".build-cache", "dist.bin"),
        missing=("absent.cache",),
        env={},
    )
    assert (dest / ".build-cache" / "sub" / "obj.o").read_text(encoding="utf-8") == "obj"
    assert (dest / ".build-cache" / "link.o").is_symlink()
    assert (dest / "dist.bin").read_bytes() == b"bin"
    assert int((dest / "dist.bin").stat().st_mtime) == stamp


def test_seed_worktree_rejects_escaping_cache_paths(tmp_path: Path) -> None:
    source = tmp_path / "source"
    dest = tmp_path / "dest"
    outside = tmp_path / "outside"
    source.mkdir()
    dest.mkdir()
    outside.mkdir()
    (source / "escape").symlink_to(outside)
    for bad in ("../outside", "/abs/path", ".", "escape"):
        spec = build_cache.BuildSpec(
            gate="build",
            cache_paths=(bad,),
            cold_seconds=10.0,
            warm_seconds=1.0,
            reuse=build_cache.ReusePolicy(kind=build_cache.ReuseKind.MOVED),
        )
        with pytest.raises(ValueError, match="cache path"):
            build_cache.seed_worktree(source, dest, spec)


def test_seed_worktree_resolves_shared_env_and_handles_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    dest = tmp_path / "dest"
    home = tmp_path / "home"
    source.mkdir()
    dest.mkdir()
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    rel_spec = build_cache.BuildSpec(
        gate="build",
        cache_paths=("out",),
        cold_seconds=20.0,
        warm_seconds=2.0,
        reuse=build_cache.ReusePolicy(
            kind=build_cache.ReuseKind.SHARED,
            env_name="SHARED_CACHE",
            env_dir=".shared",
        ),
    )
    rel_result = build_cache.seed_worktree(source, dest, rel_spec)
    expected_rel = str((source / ".shared").resolve())
    assert rel_result == build_cache.SeedResult(
        kind=build_cache.ReuseKind.SHARED,
        env={"SHARED_CACHE": expected_rel},
    )
    assert Path(expected_rel).is_dir()

    tilde_spec = build_cache.BuildSpec(
        gate="build",
        cache_paths=("out",),
        cold_seconds=20.0,
        warm_seconds=2.0,
        reuse=build_cache.ReusePolicy(
            kind=build_cache.ReuseKind.SHARED,
            env_name="SHARED_CACHE",
            env_dir="~/tool-cache",
        ),
    )
    tilde_result = build_cache.seed_worktree(source, dest, tilde_spec)
    expected_tilde = str((home / "tool-cache").resolve())
    assert tilde_result.env == {"SHARED_CACHE": expected_tilde}
    assert Path(expected_tilde).is_dir()

    none_spec = build_cache.BuildSpec(
        gate="build",
        cache_paths=(),
        cold_seconds=20.0,
        warm_seconds=20.0,
        reuse=build_cache.ReusePolicy(kind=build_cache.ReuseKind.NONE, reason="no cache"),
    )
    assert build_cache.seed_worktree(source, dest, none_spec) == build_cache.SeedResult(
        kind=build_cache.ReuseKind.NONE
    )


def test_seed_from_binding_reads_binding_or_returns_none(tmp_path: Path) -> None:
    source = tmp_path / "source"
    dest = tmp_path / "dest"
    source.mkdir()
    dest.mkdir()
    assert build_cache.seed_from_binding(source, dest) is None

    binding = source / "docs" / "process" / "binding.md"
    binding.parent.mkdir(parents=True)
    binding.write_text("# Binding\n\n## Speed\n\n- Parallel: all\n", encoding="utf-8")
    assert build_cache.seed_from_binding(source, dest) is None

    binding.write_text(
        speed_doc("- Build: gate `build`; caches `.cache`; cold 30s; warm 5s; reuse moved"),
        encoding="utf-8",
    )
    (source / ".cache").mkdir()
    (source / ".cache" / "a").write_text("x", encoding="utf-8")
    seeded = build_cache.seed_from_binding(source, dest)
    assert seeded is not None
    assert seeded.copied == (".cache",)
    assert (dest / ".cache" / "a").read_text(encoding="utf-8") == "x"


def test_probe_moved_cache_judges_warm_cold_and_failures(tmp_path: Path) -> None:
    repo = repository(
        tmp_path / "repo",
        {
            ".gitignore": ".build-cache/\n",
            "src.txt": "ok\n",
            "deleted.txt": "gone\n",
        },
    )
    (repo / "deleted.txt").unlink()
    (repo / ".build-cache").mkdir()
    (repo / ".build-cache" / "warm.o").write_text("warm\n", encoding="utf-8")
    spec = build_cache.BuildSpec(
        gate="build",
        cache_paths=(".build-cache",),
        cold_seconds=100.0,
        warm_seconds=10.0,
        reuse=build_cache.ReusePolicy(kind=build_cache.ReuseKind.MOVED),
    )

    seen_warm: list[str] = []

    def warm_runner(_cmd: list[str], cwd: Path) -> tuple[int, float]:
        seen_warm.append((cwd / ".build-cache" / "warm.o").read_text(encoding="utf-8"))
        assert (cwd / "src.txt").read_text(encoding="utf-8") == "ok\n"
        return 0, 12.0

    warm = build_cache.probe_moved_cache(repo, ["true"], spec, runner=warm_runner)
    assert seen_warm == ["warm\n"]
    assert warm == build_cache.ProbeResult(
        survived=True, seconds=12.0, threshold=55.0, reason="moved cache stayed warm"
    )

    cold = build_cache.probe_moved_cache(repo, ["true"], spec, runner=lambda _c, _d: (0, 80.0))
    assert cold == build_cache.ProbeResult(
        survived=False,
        seconds=80.0,
        threshold=55.0,
        reason="moved cache was rejected and ran cold",
    )

    failed = build_cache.probe_moved_cache(repo, ["false"], spec, runner=lambda _c, _d: (2, 1.0))
    assert failed == build_cache.ProbeResult(
        survived=False, seconds=1.0, threshold=55.0, reason="command exited 2"
    )

    equal_spec = build_cache.BuildSpec(
        gate="build",
        cache_paths=(".build-cache",),
        cold_seconds=10.0,
        warm_seconds=10.0,
        reuse=build_cache.ReusePolicy(kind=build_cache.ReuseKind.MOVED),
    )
    assert build_cache.probe_threshold(equal_spec) == 12.5


def test_probe_moved_cache_handles_non_git_dir_and_real_subprocess(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    (plain / ".cache").mkdir(parents=True)
    (plain / "sub").mkdir()
    (plain / "sub" / "app.txt").write_text("hi", encoding="utf-8")
    (plain / ".cache" / "item").write_text("cached", encoding="utf-8")
    spec = build_cache.BuildSpec(
        gate="build",
        cache_paths=(".cache",),
        cold_seconds=60.0,
        warm_seconds=5.0,
        reuse=build_cache.ReusePolicy(kind=build_cache.ReuseKind.MOVED),
    )
    ok = build_cache.probe_moved_cache(
        plain,
        [sys.executable, "-c", "from pathlib import Path; Path('.cache/item').read_text()"],
        spec,
    )
    assert ok.survived is True
    missing_cmd = build_cache.probe_moved_cache(plain, ["nonexistent-command-xyz-358"], spec)
    assert missing_cmd.survived is False
    assert missing_cmd.reason == "command exited 127"


def test_cli_seed_and_probe(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = tmp_path / "source"
    dest = tmp_path / "dest"
    source.mkdir()
    dest.mkdir()
    assert (
        build_cache.main(["build_cache.py", "seed", "--source", str(source), "--dest", str(dest)])
        == 0
    )
    assert "no build spec" in capsys.readouterr().out
    assert build_cache.main(["build_cache.py", "probe", "--root", str(source), "--", "true"]) == 1
    assert "no build spec" in capsys.readouterr().out
    assert build_cache.main(["build_cache.py", "probe", "--root", str(source), "--"]) == 2
    assert "missing build command" in capsys.readouterr().out

    binding = source / "docs" / "process" / "binding.md"
    binding.parent.mkdir(parents=True)
    (source / ".cache").mkdir()
    (source / ".cache" / "warm").write_text("1", encoding="utf-8")
    binding.write_text(
        speed_doc("- Build: gate `build`; caches `.cache`; cold 30s; warm 5s; reuse moved"),
        encoding="utf-8",
    )
    assert (
        build_cache.main(["build_cache.py", "seed", "--source", str(source), "--dest", str(dest)])
        == 0
    )
    assert "moved:" in capsys.readouterr().out
    assert (
        build_cache.main(
            ["build_cache.py", "probe", "--root", str(source), "--", sys.executable, "-c", "pass"]
        )
        == 0
    )
    assert "survived" in capsys.readouterr().out
    assert (
        build_cache.main(
            [
                "build_cache.py",
                "probe",
                "--root",
                str(source),
                "--",
                sys.executable,
                "-c",
                "raise SystemExit(1)",
            ]
        )
        == 1
    )
    assert "rejected" in capsys.readouterr().out

    binding.write_text(
        speed_doc(
            "- Build: gate `build`; caches `.cache`; cold 30s; warm 5s;"
            " reuse shared `SHARED_DIR=.shared`"
        ),
        encoding="utf-8",
    )
    assert (
        build_cache.main(["build_cache.py", "seed", "--source", str(source), "--dest", str(dest)])
        == 0
    )
    assert "shared: SHARED_DIR=" in capsys.readouterr().out

    binding.write_text(
        speed_doc("- Build: gate `build`; caches none; cold 30s; warm 30s; reuse none: cold only"),
        encoding="utf-8",
    )
    assert (
        build_cache.main(["build_cache.py", "seed", "--source", str(source), "--dest", str(dest)])
        == 0
    )
    assert "build_cache: none" in capsys.readouterr().out
