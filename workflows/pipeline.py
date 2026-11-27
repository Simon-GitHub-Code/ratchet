from __future__ import annotations

import asyncio
import json
import os
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

STREAM_LIMIT = 16 * 1024 * 1024

HOST_JS = r"""
const fs = require('node:fs')
const readline = require('node:readline')
const pipelinePath = process.argv[1]
const source = fs.readFileSync(pipelinePath, 'utf8').replace(/^export\s+/gm, '')
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor
const send = obj => process.stdout.write(JSON.stringify(obj) + '\n')
const pending = new Map()
let nextId = 1
const rl = readline.createInterface({ input: process.stdin, crlfDelay: Infinity })
let started = false
rl.on('line', line => {
  if (!line.trim()) return
  const msg = JSON.parse(line)
  if (msg.op === 'start' && !started) {
    started = true
    const phase = name => send({ op: 'phase', name: String(name) })
    const log = (...parts) => send({ op: 'log', line: parts.map(String).join(' ') })
    const agent = (prompt, options = {}) => new Promise((resolve, reject) => {
      const id = nextId++
      pending.set(id, { resolve, reject })
      send({
        op: 'agent',
        id,
        prompt: String(prompt),
        label: options && options.label ? String(options.label) : '',
        schema: options && options.schema ? options.schema : { type: 'string' },
      })
    })
    const fn = new AsyncFunction('args', 'agent', 'phase', 'log', source)
    Promise.resolve(fn(msg.args, agent, phase, log)).then(
      result => { send({ op: 'done', result }); rl.close() },
      error => {
        const message = error && error.stack ? error.stack : String(error)
        send({ op: 'error', message })
        rl.close()
        process.exitCode = 1
      },
    )
    return
  }
  if (msg.op === 'reply') {
    const slot = pending.get(msg.id)
    if (!slot) return
    pending.delete(msg.id)
    if (msg.ok) slot.resolve(msg.result)
    else slot.reject(new Error(msg.error || 'agent failed'))
  }
})
"""


def _blocked(check: str, detail: str, produce: str) -> dict[str, Any]:
    return {
        "verdict": "blocked",
        "criticFindingsByRound": [],
        "unresolvedBlockingFindings": [],
        "reviewIssues": [],
        "reviewIssuesByRound": [],
        "preflight": {
            "ok": False,
            "failures": [{"check": check, "detail": detail, "produce": produce}],
        },
        "requiredByOrchestrator": [produce],
    }


def resolve_pipeline_js(
    workflow_args: object,
    *,
    script_path: str | Path | None = None,
) -> Path | None:
    candidates: list[Path] = []
    if script_path is not None:
        candidates.append(Path(script_path).resolve().with_name("pipeline.js"))
    if isinstance(workflow_args, dict):
        preflight = workflow_args.get("preflight")
        if isinstance(preflight, str) and preflight.strip():
            candidates.append(Path(preflight).resolve().parents[1] / "workflows/pipeline.js")
    for env_name in ("CLAUDE_PLUGIN_ROOT", "RATCHET_PLUGIN"):
        value = os.environ.get(env_name, "").strip()
        if value:
            candidates.append(Path(value).resolve() / "workflows/pipeline.js")
    candidates.append(Path.home() / ".gemini/config/plugins/ratchet/workflows/pipeline.js")
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


async def run_pipeline(
    workflow_args: object,
    *,
    agent: Callable[..., Awaitable[Any]],
    phase: Callable[[str], Any],
    log: Callable[..., Any],
    script_path: str | Path | None = None,
    pipeline_js: str | Path | None = None,
    node_bin: str | None = None,
) -> Any:
    js_path = (
        Path(pipeline_js)
        if pipeline_js is not None
        else resolve_pipeline_js(workflow_args, script_path=script_path)
    )
    if js_path is None or not js_path.is_file():
        return _blocked(
            "workflow",
            "workflows/pipeline.js could not be located beside workflows/pipeline.py",
            "Install the Ratchet plugin cleanly and pass preflight or CLAUDE_PLUGIN_ROOT",
        )
    node = node_bin if node_bin is not None else shutil.which("node")
    if not node:
        return _blocked(
            "runtime",
            "node executable was not found on PATH",
            "Install Node 20+ on PATH so workflows/pipeline.py can execute workflows/pipeline.js",
        )
    proc = await asyncio.create_subprocess_exec(
        node,
        "--no-warnings",
        "-e",
        HOST_JS,
        str(js_path),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        limit=STREAM_LIMIT,
    )
    assert proc.stdin is not None
    assert proc.stdout is not None
    assert proc.stderr is not None
    write_lock = asyncio.Lock()
    tasks: set[asyncio.Task[None]] = set()

    async def send(payload: dict[str, Any]) -> None:
        raw = (json.dumps(payload) + "\n").encode("utf-8")
        async with write_lock:
            proc.stdin.write(raw)
            await proc.stdin.drain()

    async def handle_agent(msg: dict[str, Any]) -> None:
        req_id = msg["id"]
        try:
            answer = await agent(
                msg["prompt"],
                schema=msg["schema"],
                role=msg["label"] or None,
            )
            await send({"op": "reply", "id": req_id, "ok": True, "result": answer})
        except Exception as exc:
            await send({"op": "reply", "id": req_id, "ok": False, "error": str(exc)})

    await send({"op": "start", "args": workflow_args})
    result: Any = None
    done = False
    error_message = ""
    try:
        while raw_line := await proc.stdout.readline():
            line = raw_line.decode("utf-8").strip()
            if not line:
                continue
            msg = json.loads(line)
            op = msg.get("op")
            if op == "phase":
                phase(msg["name"])
            elif op == "log":
                log(msg["line"])
            elif op == "agent":
                task = asyncio.create_task(handle_agent(msg))
                tasks.add(task)
                task.add_done_callback(tasks.discard)
            elif op == "done":
                result = msg.get("result")
                done = True
                break
            elif op == "error":
                error_message = str(msg.get("message") or "pipeline error")
                break
    finally:
        for task in list(tasks):
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        proc.stdin.close()
        stderr_bytes = await proc.stderr.read()
        await proc.wait()

    if error_message:
        raise RuntimeError(error_message)
    if not done:
        stderr_text = stderr_bytes.decode("utf-8", "replace").strip()
        raise RuntimeError(
            f"pipeline host exited ({proc.returncode}) before returning a result"
            + (f": {stderr_text}" if stderr_text else "")
        )
    return result


if "agent" in globals() and "args" in globals():
    _result = await run_pipeline(  # noqa: F821, PLE1142
        args,  # noqa: F821
        agent=agent,  # noqa: F821
        phase=phase,  # noqa: F821
        log=log,  # noqa: F821
        script_path=globals().get("__file__"),
    )
    print(json.dumps(_result))
