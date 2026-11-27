from __future__ import annotations

import ast
import asyncio
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any

import pytest

from tests.test_pipeline import (
    CHECKED,
    IMPLEMENTED,
    PREFLIGHT,
    REJUDGE,
    REVIEW,
    _run,
)

PIPELINE_PY = Path(__file__).resolve().parents[2] / "workflows/pipeline.py"
PIPELINE_JS = Path(__file__).resolve().parents[2] / "workflows/pipeline.js"


def load_pipeline_module() -> dict[str, Any]:
    source = PIPELINE_PY.read_text(encoding="utf-8")
    code = compile(source, str(PIPELINE_PY), "exec", ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)
    namespace: dict[str, Any] = {"__file__": str(PIPELINE_PY)}
    coro = eval(code, namespace)  # noqa: S307 # -- executing the plugin's own workflow script
    if asyncio.iscoroutine(coro):
        asyncio.run(coro)
    return namespace


def exec_top_level(
    args: object,
    answers: dict[str, Any],
    *,
    script_path: Path = PIPELINE_PY,
) -> dict[str, Any]:
    source = script_path.read_text(encoding="utf-8")
    code = compile(source, str(script_path), "exec", ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)
    labels: list[str] = []
    prompts: dict[str, str] = {}
    schemas: dict[str, Any] = {}
    phases: list[str] = []
    logs: list[str] = []

    async def fake_agent(
        prompt: str,
        *,
        schema: object = None,
        role: str | None = None,
    ) -> object:
        label = role or ""
        labels.append(label)
        prompts[label] = prompt
        schemas[label] = schema
        return answers.get(label)

    namespace: dict[str, Any] = {
        "__file__": str(script_path),
        "args": args,
        "agent": fake_agent,
        "phase": phases.append,
        "log": lambda *parts: logs.append(" ".join(map(str, parts))),
    }
    captured = io.StringIO()
    with redirect_stdout(captured):
        coro = eval(code, namespace)  # noqa: S307 # -- executing the plugin's own workflow script
        asyncio.run(coro)
    result = json.loads(captured.getvalue().strip())
    return {
        "result": result,
        "labels": labels,
        "prompts": prompts,
        "schemas": schemas,
        "phases": phases,
        "logs": logs,
    }


def test_pipeline_py_compiles_with_top_level_await() -> None:
    source = PIPELINE_PY.read_text(encoding="utf-8")
    compile(source, str(PIPELINE_PY), "exec", ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)


def test_pipeline_py_matches_pipeline_js_on_blocked_and_unfit_args() -> None:
    for bad_args in ({}, {"ref": "-bad", "branch": "feature/1"}):
        js_out = _run(bad_args)
        py_out = exec_top_level(bad_args, {})
        assert py_out["result"] == js_out["result"]
        assert py_out["labels"] == js_out["labels"] == []


def test_pipeline_py_matches_pipeline_js_on_full_feature_and_rejudge() -> None:
    workflow_args = {"ref": "4", "branch": "feature/4-thing", "base": "main"}
    spec = {
        "slug": "thing",
        "specPath": "docs/features/thing/spec.md",
        "namedGates": [],
        "testFiles": ["tests/test_thing.py"],
        "layersTouched": [],
        "openQuestions": [],
        "assumptions": [],
        "dependencies": [],
        "guardEdits": [],
        "failingForRightReason": True,
        "failures": [{"testFile": "tests/test_thing.py", "content": "AssertionError"}],
        "checkOutput": "tier0: green",
    }
    answers: dict[str, Any] = {
        "preflight": {**PREFLIGHT, "critic": False, "criticReason": "young"},
        "spec": spec,
        "ticket-check": CHECKED,
        "implement": IMPLEMENTED,
        "review": REVIEW,
    }
    js_out = _run(workflow_args, **answers)
    py_out = exec_top_level(workflow_args, answers)
    assert py_out["result"] == js_out["result"]
    assert py_out["result"]["verdict"] == "approve"
    assert py_out["labels"] == js_out["labels"]
    assert py_out["prompts"] == js_out["prompts"]
    assert py_out["schemas"] == js_out["schemas"]
    assert py_out["logs"] == js_out["logs"]
    assert "Preflight" in py_out["phases"]
    assert "Review" in py_out["phases"]

    rejudge_args = {"ref": "4", "branch": "feature/4-expiry", "rejudge": {**REJUDGE}}
    rejudge_answers: dict[str, Any] = {"review-again": REVIEW}
    assert (
        exec_top_level(rejudge_args, rejudge_answers)["result"]
        == _run(rejudge_args, **rejudge_answers)["result"]
    )


def test_pipeline_py_handles_parallel_agents_and_error_propagation(tmp_path: Path) -> None:
    ns = load_pipeline_module()
    parallel_js = tmp_path / "parallel.js"
    parallel_js.write_text(
        "phase('Fan out')\n"
        "log('starting', 2)\n"
        "const [a, b] = await Promise.all([\n"
        "  agent('first', { label: 'A', schema: { type: 'string' } }),\n"
        "  agent('second', { label: 'B' }),\n"
        "])\n"
        "return { a, b }\n",
        encoding="utf-8",
    )
    seen_phases: list[str] = []
    seen_logs: list[str] = []

    async def concurrent_agent(prompt: str, *, schema: object, role: str | None) -> str:
        await asyncio.sleep(0.01 if role == "A" else 0.0)
        assert isinstance(schema, dict)
        return f"{role}:{prompt}"

    out = asyncio.run(
        ns["run_pipeline"](
            {},
            agent=concurrent_agent,
            phase=seen_phases.append,
            log=seen_logs.append,
            pipeline_js=parallel_js,
        )
    )
    assert out == {"a": "A:first", "b": "B:second"}
    assert seen_phases == ["Fan out"]
    assert seen_logs == ["starting 2"]

    async def failing_agent(prompt: str, *, schema: object, role: str | None) -> str:
        msg = f"boom {role} {prompt} {bool(schema)}"
        raise ValueError(msg)

    with pytest.raises(RuntimeError, match="boom A first True"):
        asyncio.run(
            ns["run_pipeline"](
                {},
                agent=failing_agent,
                phase=seen_phases.append,
                log=seen_logs.append,
                pipeline_js=parallel_js,
            )
        )


def test_pipeline_py_blocks_cleanly_when_runtime_or_js_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ns = load_pipeline_module()

    async def unused(*_args: object, **_kwargs: object) -> object:
        raise AssertionError

    missing_js = asyncio.run(
        ns["run_pipeline"](
            {},
            agent=unused,
            phase=lambda _: None,
            log=lambda *_: None,
            pipeline_js=tmp_path / "missing.js",
        )
    )
    assert missing_js["verdict"] == "blocked"
    assert missing_js["preflight"]["failures"][0]["check"] == "workflow"

    missing_node = asyncio.run(
        ns["run_pipeline"](
            {},
            agent=unused,
            phase=lambda _: None,
            log=lambda *_: None,
            pipeline_js=PIPELINE_JS,
            node_bin="",
        )
    )
    assert missing_node["verdict"] == "blocked"
    assert missing_node["preflight"]["failures"][0]["check"] == "runtime"

    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(PIPELINE_JS.parents[1]))
    assert ns["resolve_pipeline_js"]({}) == PIPELINE_JS.resolve()
    monkeypatch.delenv("CLAUDE_PLUGIN_ROOT", raising=False)
    monkeypatch.delenv("RATCHET_PLUGIN", raising=False)
    assert (
        ns["resolve_pipeline_js"](
            {"preflight": str(PIPELINE_JS.parents[1] / "scripts/feature_preflight.py")}
        )
        == PIPELINE_JS.resolve()
    )
