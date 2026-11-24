import re
import tempfile
from pathlib import Path

import pytest

import project_bite
from tests import repos
from tests.repos import branch, commit, repository, write

TABLE = "| Question | Tool | Tier | Threshold source | Entry point |\n|---|---|---|---|---|\n"
LINT = 'if grep -rl planted src; then echo "L001 a planted fault"; exit 1; fi'


def bite_toml(
    gate: str,
    name: str,
    *,
    path: str = "src/_bite.txt",
    alone: str = "",
) -> str:
    extra = f"alone = {alone}\n" if alone else ""
    return (
        f'[[bite]]\ngate = "{gate}"\nname = "{name}"\n'
        f'write = {{ path = "{path}", content = "planted\\n" }}\n'
        f'expect = "L001"\n{extra}'
    )


def row(name: str, tier: str = "0") -> str:
    return f"| {name} | a tool | {tier} | | `sh gate {name}` |\n"


def project(
    root: Path,
    gates: dict[str, str],
    registry: str,
    *,
    tiers: dict[str, str] | None = None,
) -> Path:
    cases = "".join(f"{name})\n{body}\n;;\n" for name, body in gates.items())
    table = TABLE + "".join(row(name, (tiers or {}).get(name, "0")) for name in gates)
    files = {
        "docs/process/binding.md": f"# Binding\n\n## Gates\n\n{table}\n## Speed\n\n| a | b |\n",
        "gate": f'#!/bin/sh\ncase "$1" in\n{cases}esac\n',
        "src/code.txt": "clean\n",
        "quality/bites.toml": registry,
    }
    root.mkdir(exist_ok=True)
    return repository(root, files)


def run_bite(root: Path, *flags: str) -> int:
    return project_bite.main(["project_bite.py", "--root", str(root), "--jobs", "1", *flags])


def proved_faults(output: str) -> list[tuple[str, str]]:
    pattern = re.compile(r"^bite: ([^:]+): ([^:]+): bites \(", re.MULTILINE)
    return [(match[1], match[2]) for match in pattern.finditer(output)]


def logged_events(log: Path) -> tuple[set[frozenset[str]], dict[str, set[str]]]:
    running: set[str] = set()
    met: set[frozenset[str]] = set()
    told: dict[str, set[str]] = {}
    for line in log.read_text(encoding="utf-8").splitlines():
        sign, key, *jobs = line.split()
        if sign == "-":
            running.discard(key)
            continue
        met.update(frozenset((key, other)) for other in running)
        running.add(key)
        told.setdefault(key, set()).update(jobs)
    return met, told


@pytest.fixture(autouse=True)
def scratch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "scratch"
    directory.mkdir()
    repos.skeleton()
    monkeypatch.setattr(tempfile, "tempdir", str(directory))
    monkeypatch.delenv(project_bite.BITING, raising=False)
    return directory


def test_shard_partitions_all_faults_deterministically_across_slices(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    gates = {"alpha": LINT, "beta": LINT, "gamma": LINT}
    registry = (
        bite_toml("alpha", "first", path="src/a1.txt")
        + bite_toml("alpha", "second", path="src/a2.txt")
        + bite_toml("beta", "first", path="src/b1.txt")
        + bite_toml("beta", "second", path="src/b2.txt")
        + bite_toml("gamma", "first", path="src/g1.txt")
        + bite_toml("gamma", "second", path="src/g2.txt")
    )
    root = project(tmp_path / "repo", gates, registry)
    expected = [
        ("alpha", "first"),
        ("alpha", "second"),
        ("beta", "first"),
        ("beta", "second"),
        ("gamma", "first"),
        ("gamma", "second"),
    ]
    seen: list[tuple[str, str]] = []
    slice_two: list[tuple[str, str]] = []
    for index in range(1, 4):
        assert run_bite(root, "--shard", f"{index}/3") == 0
        part = proved_faults(capsys.readouterr().out)
        if index == 2:
            slice_two = part
        seen.extend(part)
    assert sorted(seen) == sorted(expected)
    assert len(seen) == len(expected)
    assert run_bite(root, "--shard", "2/3") == 0
    assert proved_faults(capsys.readouterr().out) == slice_two


def test_shard_with_changed_slices_only_changed_faults_and_empty_slices_are_green(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    gates = {"lint": LINT, "tests": LINT}
    registry = (
        bite_toml("lint", "one", path="src/one.txt")
        + bite_toml("lint", "two", path="src/two.txt")
        + bite_toml("tests", "other", path="src/other.txt")
    )
    root = project(tmp_path / "repo", gates, registry, tiers={"lint": "0", "tests": "1"})
    branch(root)
    write(
        root,
        "quality/bites.toml",
        registry.replace('content = "planted\\n"', 'content = "planted-more\\n"', 1),
    )
    commit(root, "test(bite): adjust lint fault")
    capsys.readouterr()

    seen: list[tuple[str, str]] = []
    empty_outputs: list[str] = []
    for index in range(1, 6):
        assert run_bite(root, "--changed", "--shard", f"{index}/5") == 0
        out = capsys.readouterr().out
        part = proved_faults(out)
        if not part:
            empty_outputs.append(out)
            assert f"bite: green: slice {index}/5 has no faults to prove" in out
        seen.extend(part)
    assert sorted(seen) == [("lint", "one"), ("lint", "two")]
    assert len(empty_outputs) >= 3


def test_shard_on_full_proof_allows_empty_slices_but_fails_when_registry_has_no_bites(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = project(tmp_path / "repo", {"lint": LINT}, bite_toml("lint", "only"))
    empty_count = 0
    for index in range(1, 5):
        assert run_bite(root, "--shard", f"{index}/4") == 0
        out = capsys.readouterr().out
        if not proved_faults(out):
            empty_count += 1
            assert f"bite: green: slice {index}/4 has no faults to prove" in out
    assert empty_count == 3

    assert run_bite(root, "--shard", "1/2", "--gate", "missing") == 1
    assert "no bite to run" in capsys.readouterr().out


@pytest.mark.parametrize("spec", ["0/3", "4/3", "1/0", "abc", "1", "1/2/3", "/2", "1/", "-1/2"])
def test_invalid_shard_spec_exits_with_usage_error(
    tmp_path: Path, scratch: Path, *, capsys: pytest.CaptureFixture[str], spec: str
) -> None:
    root = project(tmp_path / "repo", {"lint": LINT}, bite_toml("lint", "only"))
    with pytest.raises(SystemExit) as stop:
        run_bite(root, f"--shard={spec}")
    assert stop.value.code == 2
    assert "invalid shard" in capsys.readouterr().err
    assert list(scratch.iterdir()) == []


@pytest.mark.parametrize("count", ["0", "-1", "abc"])
def test_invalid_shard_check_exits_with_usage_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], count: str
) -> None:
    root = project(tmp_path / "repo", {"lint": LINT}, bite_toml("lint", "only"))
    with pytest.raises(SystemExit) as stop:
        run_bite(root, f"--shard-check={count}")
    assert stop.value.code == 2
    assert "invalid shard count" in capsys.readouterr().err


def test_shard_and_shard_check_cannot_be_combined(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = project(tmp_path / "repo", {"lint": LINT}, bite_toml("lint", "only"))
    with pytest.raises(SystemExit) as stop:
        run_bite(root, "--shard", "1/2", "--shard-check", "2")
    assert stop.value.code == 2
    assert "--shard and --shard-check" in capsys.readouterr().err


def test_shard_check_verifies_partition_and_rejects_broken_partition(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    gates = {"alpha": LINT, "beta": LINT}
    registry = (
        bite_toml("alpha", "one", path="src/1.txt")
        + bite_toml("alpha", "two", path="src/2.txt")
        + bite_toml("beta", "three", path="src/3.txt")
    )
    root = project(tmp_path / "repo", gates, registry)
    for slices in (1, 3, 7):
        assert run_bite(root, "--shard-check", str(slices)) == 0
        assert (
            f"bite: shard-check: green: 3 faults partitioned across {slices} slices"
            in capsys.readouterr().out
        )

    def broken_slice(
        tasks: list[project_bite.Task], shard: project_bite.Shard
    ) -> list[project_bite.Task]:
        if shard.index == 1:
            return [tasks[0], tasks[0]]
        return []

    monkeypatch.setattr(project_bite, "slice_tasks", broken_slice)
    assert run_bite(root, "--shard-check", "2") == 1
    err = capsys.readouterr().out
    assert "duplicated fault: alpha/one" in err
    assert "omitted fault: alpha/two" in err
    assert "omitted fault: beta/three" in err


def test_shard_check_with_unexpected_fault_and_empty_registry(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    root = project(tmp_path / "repo", {"lint": LINT}, bite_toml("lint", "one"))
    fake_row = project_bite.Row("ghost", "tool", "0", "sh gate ghost")
    fake_bite = project_bite.Bite("ghost", "phantom", "L001", project_bite.Write("src/p.txt", "p"))

    def extra_slice(
        tasks: list[project_bite.Task], shard: project_bite.Shard
    ) -> list[project_bite.Task]:
        return [*tasks, (fake_row, [fake_bite])] if shard.index == 1 else []

    monkeypatch.setattr(project_bite, "slice_tasks", extra_slice)
    assert run_bite(root, "--shard-check", "2") == 1
    assert "unexpected fault: ghost/phantom" in capsys.readouterr().out

    assert run_bite(root, "--shard-check", "2", "--gate", "missing") == 1
    assert "no bite to run" in capsys.readouterr().out


def test_per_fault_alone_isolates_only_that_fault_while_siblings_share_lanes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    log, mark = tmp_path / "log", tmp_path / "mark"
    mark.mkdir()
    monkeypatch.setenv("LOG", str(log))
    monkeypatch.setenv("MARK", str(mark))
    monkeypatch.setenv("RATCHET_JOBS", "4")
    monkeypatch.setattr(project_bite.os, "cpu_count", lambda: 4)
    alpha_script = (
        'tag="green"\n'
        'test -f src/shared.txt && tag="shared"\n'
        'test -f src/solo.txt && tag="solo"\n'
        'printf "+ alpha:%s %s\\n" "$tag" "${RATCHET_JOBS:-unset}" >> "$LOG"\n'
        'if [ "$tag" = "shared" ]; then\n'
        '  touch "$MARK/alpha-shared"; n=0;\n'
        '  while [ ! -e "$MARK/beta-other" ] && [ "$n" -lt 40 ]; do sleep 0.05; n=$((n+1)); done;\n'
        'elif [ "$tag" = "solo" ]; then\n'
        "  sleep 0.15\n"
        "fi\n"
        'printf -- "- alpha:%s\\n" "$tag" >> "$LOG"\n' + LINT
    )
    beta_script = (
        'printf "+ beta:other %s\\n" "${RATCHET_JOBS:-unset}" >> "$LOG"\n'
        'touch "$MARK/beta-other"; n=0;\n'
        'while [ ! -e "$MARK/alpha-shared" ] && [ "$n" -lt 40 ]; do sleep 0.05; n=$((n+1)); done;\n'
        'printf -- "- beta:other\\n" >> "$LOG"\n' + LINT
    )
    registry = (
        bite_toml("alpha", "shared", path="src/shared.txt")
        + bite_toml("alpha", "solo", path="src/solo.txt", alone="true")
        + bite_toml("beta", "other", path="src/other.txt")
    )
    root = project(tmp_path / "repo", {"alpha": alpha_script, "beta": beta_script}, registry)
    assert project_bite.main(["project_bite.py", "--root", str(root), "--jobs", "2"]) == 0
    out = capsys.readouterr().out
    assert "bite: green: 3 of 3 faults bit, over 2 gates in " in out
    met, told = logged_events(log)
    assert frozenset({"alpha:shared", "beta:other"}) in met
    assert all("alpha:solo" not in pair for pair in met)
    assert told["alpha:shared"] == {"2"}
    assert told["beta:other"] == {"2"}
    assert told["alpha:solo"] == {"4"}


@pytest.mark.parametrize("bad", ['"yes"', "1", '["true"]'])
def test_non_bool_alone_in_registry_is_rejected(tmp_path: Path, bad: str) -> None:
    registry = bite_toml("lint", "x", alone=bad)
    _, problems = project_bite.read_registry(registry)
    assert problems == ["bite 1 (lint): 'alone' is true or false"]
    root = project(tmp_path / "repo", {"lint": LINT}, registry)
    assert run_bite(root) == 1


def test_changing_alone_in_registry_triggers_changed_proof(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    registry = bite_toml("lint", "one", path="src/1.txt") + bite_toml(
        "tests", "two", path="src/2.txt"
    )
    root = project(tmp_path / "repo", {"lint": LINT, "tests": LINT}, registry)
    branch(root)
    write(
        root,
        "quality/bites.toml",
        bite_toml("lint", "one", path="src/1.txt", alone="true")
        + bite_toml("tests", "two", path="src/2.txt"),
    )
    commit(root, "chore(bite): mark lint/one alone")
    assert run_bite(root, "--changed") == 0
    out = capsys.readouterr().out
    assert proved_faults(out) == [("lint", "one")]


def stub_run_seconds(monkeypatch: pytest.MonkeyPatch, *values: float) -> None:
    durations = iter(values)
    real_run = project_bite.Shell.run

    def timed(*args: object, **kwargs: int) -> project_bite.Run:
        return real_run(*args, **kwargs)._replace(seconds=next(durations, 0.0))  # type: ignore[arg-type]

    monkeypatch.setattr(project_bite.Shell, "run", timed)


def test_slowest_report_names_top_faults_and_percentage_share(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    gates = {"lint": LINT, "tests": LINT}
    registry = (
        bite_toml("lint", "fast", path="src/1.txt")
        + bite_toml("lint", "med", path="src/2.txt")
        + bite_toml("tests", "slow", path="src/3.txt")
        + bite_toml("tests", "slowest", path="src/4.txt")
    )
    root = project(tmp_path / "repo", gates, registry)
    stub_run_seconds(monkeypatch, 0.25, 0.25, 0.0, 0.75, 0.75, 0.0, 1.5, 1.5, 0.0, 2.5, 2.5, 0.0)
    assert run_bite(root) == 0
    out = capsys.readouterr().out
    assert (
        "bite: slowest: tests/slowest 5.0s (50%), tests/slow 3.0s (30%), lint/med 1.5s (15%)" in out
    )
    assert "lint/fast" not in out


def test_slowest_report_precedes_load_on_red_and_skips_zero_total(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    red_root = project(tmp_path / "red", {"lint": "exit 0"}, bite_toml("lint", "fail"))
    stub_run_seconds(monkeypatch, 1.0, 1.0)
    assert run_bite(red_root) == 1
    lines = capsys.readouterr().out.splitlines()
    assert "bite: slowest: lint/fail 2.0s (100%)" in lines
    assert lines[-1].startswith("bite: load: ")

    zero_root = project(tmp_path / "zero", {"lint": LINT}, bite_toml("lint", "zero"))
    stub_run_seconds(monkeypatch, 0.0, 0.0, 0.0)
    assert run_bite(zero_root) == 0
    assert "bite: slowest:" not in capsys.readouterr().out

    def no_faults(
        _plan: project_bite.Proof,
        *,
        limit: float,  # noqa: ARG001 # -- matches execute_lanes signature
    ) -> list[project_bite.Outcome]:
        return [
            project_bite.Outcome(
                project_bite.Bite("format", "rec", "0", project_bite.Write("x", "")),
                project_bite.Verdict.ACCEPTS_RECORD,
                seconds=1.0,
                fault=False,
            )
        ]

    monkeypatch.setattr(project_bite, "execute_lanes", no_faults)
    assert run_bite(zero_root) == 0
    assert "bite: slowest:" not in capsys.readouterr().out


def make_task(
    key: str,
    *,
    tier: str = "0",
    args: tuple[str, ...] = (),
    whole: str = "",
    alone: bool = False,
) -> project_bite.Task:
    gate, name = key.split("/", 1)
    gate_row = project_bite.Row(gate, "tool", tier, f"sh gate {gate}")
    bite = project_bite.Bite(
        gate,
        name,
        "L001",
        project_bite.Write(f"src/{gate}_{name}.txt", "planted\n"),
        args=args,
        whole=whole,
        alone=alone,
    )
    return (gate_row, [bite])


def test_fault_weight_combines_tier_scope_and_alone_factors() -> None:
    narrowed = ("src/one.txt",)
    assert project_bite.fault_weight(make_task("lint/a", tier="0", args=narrowed)) == 1
    assert project_bite.fault_weight(make_task("lint/b", tier="1", args=narrowed)) == 1
    assert project_bite.fault_weight(make_task("deep/c", tier="2", args=narrowed)) == 2
    assert project_bite.fault_weight(make_task("lint/d", tier="0")) == 2
    assert project_bite.fault_weight(make_task("lint/e", tier="0", whole="whole tree")) == 8
    assert project_bite.fault_weight(make_task("lint/f", tier="0", args=narrowed, alone=True)) == 2
    assert project_bite.fault_weight(make_task("deep/g", tier="2", args=narrowed, alone=True)) == 4
    assert project_bite.fault_weight(make_task("deep/h", tier="2", whole="whole tree")) == 16
    assert (
        project_bite.fault_weight(make_task("deep/i", tier="2", whole="whole tree", alone=True))
        == 32
    )


def test_slice_tasks_balances_heavy_faults_evenly_and_preserves_order() -> None:
    heavy = [
        make_task(f"deep/heavy-{i}", tier="2", whole="whole tree", alone=True) for i in range(6)
    ]
    light = [make_task(f"lint/light-{i}", tier="0", args=(f"src/l{i}.txt",)) for i in range(12)]
    tasks = [*heavy, *light]
    heavy_ids = {project_bite.task_id(task) for task in heavy}

    assert project_bite.check_shards(tasks, 3) == 0
    slices = [project_bite.slice_tasks(tasks, project_bite.Shard(i, 3)) for i in range(1, 4)]
    assert slices == [
        project_bite.slice_tasks(tasks, project_bite.Shard(i, 3)) for i in range(1, 4)
    ]
    for part in slices:
        heavy_count = sum(1 for task in part if project_bite.task_id(task) in heavy_ids)
        assert heavy_count == 2
        assert sum(project_bite.fault_weight(task) for task in part) == 68
        positions = [tasks.index(task) for task in part]
        assert positions == sorted(positions)


def test_slice_tasks_separates_explicit_whole_and_spreads_each_gate_evenly() -> None:
    whole_tier2 = (
        [make_task(f"a11y/a-{i}", tier="2", whole="whole boot") for i in range(2)]
        + [make_task(f"e2e/e-{i}", tier="2", whole="whole boot") for i in range(2)]
        + [make_task(f"perf/p-{i}", tier="2", whole="whole boot") for i in range(2)]
    )
    unnarrowed_fast = [make_task(f"formal/f-{i}", tier="2") for i in range(12)]
    narrowed_tier2 = [
        make_task(f"mutation/m-{i}", tier="2", args=(f"src/m{i}.ts",)) for i in range(3)
    ]
    tasks = [*whole_tier2, *unnarrowed_fast, *narrowed_tier2]
    slices = [project_bite.slice_tasks(tasks, project_bite.Shard(i, 3)) for i in range(1, 4)]
    for part in slices:
        assert sum(1 for _, bites in part if bites[0].whole) == 2
        assert sum(1 for row, _ in part if row.gate == "formal") == 4
        assert sum(1 for row, _ in part if row.gate == "mutation") == 1


def test_shard_cli_balances_heavy_tier2_whole_alone_faults_across_slices(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    heavy_entries = [
        (
            f'[[bite]]\ngate = "deep"\nname = "heavy-{i}"\n'
            f'write = {{ path = "src/h{i}.txt", content = "planted\\n" }}\n'
            f'expect = "L001"\nwhole = "whole tree"\nalone = true\n'
        )
        for i in range(6)
    ]
    light_entries = [
        (
            f'[[bite]]\ngate = "lint"\nname = "light-{i}"\n'
            f'write = {{ path = "src/l{i}.txt", content = "planted\\n" }}\n'
            f'expect = "L001"\nargs = ["src/l{i}.txt"]\n'
        )
        for i in range(6)
    ]
    root = project(
        tmp_path / "repo",
        {"deep": LINT, "lint": LINT},
        "".join([*heavy_entries, *light_entries]),
        tiers={"deep": "2", "lint": "0"},
    )
    assert run_bite(root, "--shard-check", "3") == 0
    capsys.readouterr()

    seen: list[tuple[str, str]] = []
    for index in range(1, 4):
        assert run_bite(root, "--shard", f"{index}/3") == 0
        part = proved_faults(capsys.readouterr().out)
        assert sum(1 for gate, _ in part if gate == "deep") == 2
        assert sum(1 for gate, _ in part if gate == "lint") == 2
        seen.extend(part)
    assert len(seen) == 12
    assert len(set(seen)) == 12
