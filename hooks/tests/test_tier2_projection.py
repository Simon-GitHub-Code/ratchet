import json
from pathlib import Path

import pytest

import tier2_projection

BINDING_HEADER = (
    "# Binding\n\n## Speed\n\n- Shards: {shards}\n\n## Gates\n\n"
    "| Question | Tool | Tier | Threshold source | Entry point |\n"
    "| --- | --- | --- | --- | --- |\n"
)
BITE_ALL_ROW = (
    "| every gate bites | `ratchet` plugin `scripts/bite.py` and `scripts/project_bite.py`"
    " | 2 | | `just bite-all` |"
)


def write_repo(
    root: Path,
    *,
    rows: list[str] | None = None,
    shards: int | str = 1,
    thresholds: str = "",
    justfile: str = "",
) -> None:
    if rows is not None:
        binding = root / "docs/process/binding.md"
        binding.parent.mkdir(parents=True, exist_ok=True)
        text = BINDING_HEADER.format(shards=shards) + "\n".join(rows) + "\n"
        binding.write_text(text, encoding="utf-8")
    if thresholds:
        path = root / "quality/thresholds.toml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(thresholds, encoding="utf-8")
    if justfile:
        (root / "justfile").write_text(justfile, encoding="utf-8")


def write_timing(root: Path, entries: list[str]) -> None:
    path = root / ".ratchet/timing.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(entries) + "\n", encoding="utf-8")


def record(key: str, seconds: float, *, exit_code: int = 0, status: str = "green") -> str:
    return json.dumps({"key": key, "seconds": seconds, "status": status, "exit": exit_code})


def fault(gate: str, bite: str, seconds: float, *, narrowed: bool = True) -> str:
    return json.dumps(
        {
            "kind": "fault",
            "gate": gate,
            "bite": bite,
            "seconds": seconds,
            "narrowed": narrowed,
        }
    )


def test_missing_files_and_missing_timing_return_empty(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert tier2_projection.projections(tmp_path) == []
    assert tier2_projection.proposals(tmp_path) == []
    assert tier2_projection.main(["tier2_projection", "--root", str(tmp_path), "--strict"]) == 0
    assert "tier2-projection: green" in capsys.readouterr().out

    write_repo(
        tmp_path,
        rows=["| full mutation | a tool | 2 | | `just mutation` |"],
        thresholds="[ceilings.seconds]\ntier2 = 100\n",
    )
    assert tier2_projection.projections(tmp_path) == []


def test_under_warn_share_is_green_and_missing_or_zero_ceiling_is_skipped(
    tmp_path: Path,
) -> None:
    write_repo(
        tmp_path,
        rows=[
            "| full mutation | a tool | 2 | | `just mutation` |",
            "| end to end | a tool | 2 | | `just e2e` |",
            "| lint | a linter | 0 | | `just lint` |",
            "| skipped | none: not needed | 2 | | `just skipped` |",
            "| later | later: code:source | 2 | | `just later` |",
        ],
        thresholds="[ceilings.seconds]\nmutation = 100\ne2e = 0\n",
    )
    write_timing(
        tmp_path,
        [
            record("mutation", 79.0),
            record("e2e", 50.0),
            record("lint", 99.0),
        ],
    )
    assert tier2_projection.projections(tmp_path) == []

    write_repo(
        tmp_path,
        rows=["| end to end | a tool | 2 | | `just e2e` |"],
        thresholds="[ceilings.seconds]\nmutation = 100\n",
    )
    write_timing(tmp_path, [record("e2e", 99.0)])
    assert tier2_projection.projections(tmp_path) == []


def test_normal_tier2_gate_uses_last_five_green_runs_and_ignores_corrupt_and_non_green(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_repo(
        tmp_path,
        rows=["| full mutation | a tool | 2 | | `just mutation` |"],
        thresholds="[ceilings.seconds]\ntier2 = 100\n",
    )
    write_timing(
        tmp_path,
        [
            "{not-json",
            json.dumps({"kind": "tier", "key": "mutation", "seconds": 999.0, "exit": 0}),
            json.dumps({"seconds": 999.0, "exit": 0}),
            record("mutation", 10.0),
            record("mutation", 12.0),
            record("mutation", 999.0, exit_code=1, status="failed"),
            json.dumps({"key": "mutation", "seconds": True, "exit": 0}),
            json.dumps({"key": "mutation", "seconds": 999.0, "exit": False}),
            record("mutation", 80.0),
            record("mutation", 85.0),
            record("mutation", 90.0),
            record("mutation", 82.0),
            record("mutation", 84.0),
        ],
    )
    found = tier2_projection.projections(tmp_path)
    assert len(found) == 1
    item = found[0]
    assert item == tier2_projection.Projection(
        gate="mutation",
        projected=84.0,
        ceiling=100.0,
        share=0.84,
        sharded=False,
        current_shards=1,
        recommended_shards=2,
        source="runs",
    )
    assert "tier2_sharded" in item.line()
    assert "tier2_sharded" in item.proposal()
    assert tier2_projection.proposals(tmp_path) == [item.proposal()]
    assert tier2_projection.main(["tier2_projection", "--root", str(tmp_path)]) == 0
    assert "mutation projected at 84.0s" in capsys.readouterr().out
    assert tier2_projection.main(["tier2_projection", "--root", str(tmp_path), "--strict"]) == 1
    assert "tier2_sharded" in capsys.readouterr().out


def test_gate_ceiling_vs_tier2_fallback_and_sharded_recommendation_and_sorting(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_repo(
        tmp_path,
        rows=[
            "| full mutation | a tool | 2 | | `just mutation` |",
            "| end to end | a tool | 2 | | `just e2e` |",
        ],
        shards=2,
        justfile='tier2_sharded := "mutation"\n',
        thresholds="[ceilings.seconds]\nmutation = 100\ntier2 = 200\n",
    )
    write_timing(
        tmp_path,
        [
            record("mutation", 90.0),
            record("e2e", 160.0),
        ],
    )
    found = tier2_projection.projections(tmp_path)
    assert [p.gate for p in found] == ["mutation", "e2e"]
    mut, e2e = found
    assert mut.ceiling == 100.0
    assert mut.sharded is True
    assert mut.current_shards == 2
    assert mut.recommended_shards == 3
    assert "raise `- Shards:` from 2 to 3" in mut.line()
    assert e2e.ceiling == 200.0
    assert e2e.share == pytest.approx(0.8)
    assert e2e.sharded is False
    assert e2e.current_shards == 1
    assert e2e.recommended_shards == 2

    assert tier2_projection.main(["tier2_projection", "--root", str(tmp_path), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert [entry["gate"] for entry in payload] == ["mutation", "e2e"]
    assert payload[0]["recommended_shards"] == 3


def test_bite_all_fault_sum_unsharded_vs_sharded_and_max_with_direct_runs(
    tmp_path: Path,
) -> None:
    write_repo(
        tmp_path,
        rows=[BITE_ALL_ROW],
        shards=1,
        thresholds="[ceilings.seconds]\nbite-all = 100\n",
    )
    write_timing(
        tmp_path,
        [
            fault("lint", "a", 100.0),
            fault("tests", "b", 100.0),
            fault("types", "c", 100.0),
            fault("fmt", "d", 100.0),
            fault("extra", "e", 50.0),
        ],
    )
    unsharded = tier2_projection.projections(tmp_path)
    assert len(unsharded) == 1
    assert unsharded[0].projected == pytest.approx(112.5)
    assert unsharded[0].source == "faults"
    assert unsharded[0].recommended_shards == 2

    write_repo(
        tmp_path,
        rows=[BITE_ALL_ROW],
        shards=2,
        justfile='tier2_sharded := "bite-all"\n',
        thresholds="[ceilings.seconds]\nbite-all = 100\n",
    )
    assert tier2_projection.projections(tmp_path) == []

    write_timing(
        tmp_path,
        [
            record("bite-all", 85.0),
            fault("lint", "a", 80.0),
            fault("tests", "b", 80.0),
        ],
    )
    from_runs = tier2_projection.projections(tmp_path)
    assert len(from_runs) == 1
    assert from_runs[0].projected == 85.0
    assert from_runs[0].source == "runs"
    assert from_runs[0].recommended_shards == 3

    write_timing(
        tmp_path,
        [
            record("bite-all", 50.0),
            fault("lint", "a", 180.0),
            fault("tests", "b", 180.0),
            fault("types", "c", 180.0),
            fault("fmt", "d", 180.0),
        ],
    )
    from_faults = tier2_projection.projections(tmp_path)
    assert len(from_faults) == 1
    assert from_faults[0].projected == 90.0
    assert from_faults[0].source == "faults"
    assert from_faults[0].recommended_shards == 3

    write_repo(
        tmp_path,
        rows=[BITE_ALL_ROW],
        shards=1,
        thresholds="[ceilings.seconds]\nbite-all = 100\n",
    )
    write_timing(tmp_path, [record("bite-all", 85.0)])
    direct_only = tier2_projection.projections(tmp_path)
    assert len(direct_only) == 1
    assert direct_only[0].projected == 85.0
    assert direct_only[0].source == "runs"


def test_invalid_shards_defaults_to_one_and_empty_thresholds_returns_empty(
    tmp_path: Path,
) -> None:
    write_repo(
        tmp_path,
        rows=["| full mutation | a tool | 2 | | `just mutation` |"],
        shards="invalid",
    )
    write_timing(tmp_path, [record("mutation", 90.0)])
    assert tier2_projection.projections(tmp_path) == []

    write_repo(
        tmp_path,
        rows=["| full mutation | a tool | 2 | | `just mutation` |"],
        shards="invalid",
        thresholds="[ceilings.seconds]\nmutation = 100\n",
        justfile='tier2_sharded := "mutation"\n',
    )
    [item] = tier2_projection.projections(tmp_path)
    assert item.current_shards == 1
    assert item.recommended_shards == 2
