import pytest

import build_cache

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
