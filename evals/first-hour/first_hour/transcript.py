"""Events the reducer counts, read from one recorded SDK message.

The driver records every message as ``{"type": <class name>, "msg": <fields>}``. Content blocks
are dicts here; the one-off run recorded them as their ``repr``, so whatever is read from a block
is read from the message's JSON text, which holds in both forms.
"""

from __future__ import annotations

import json
import re

GUARD_DENIED = "Ratchet guard denied this tool call"
RED = re.compile(r"parallel: ([\w.-]+): red \(exit")
AGENT_TYPES = frozenset({"local_agent", "remote_agent"})


def _data(msg: dict[str, object]) -> dict[str, object]:
    data = msg.get("data")
    return data if isinstance(data, dict) else {}


def _workflow_agents(data: dict[str, object]) -> list[dict[str, object]]:
    progress = data.get("workflow_progress")
    return [
        {"kind": "agent", "via": "workflow", "id": item.get("agentId"), "label": item.get("label")}
        for item in (progress if isinstance(progress, list) else [])
        if isinstance(item, dict) and item.get("type") == "workflow_agent" and item.get("agentId")
    ]


def _task_started(data: dict[str, object]) -> list[dict[str, object]]:
    task_type = data.get("task_type")
    if task_type in AGENT_TYPES:
        return [
            {
                "kind": "agent",
                "via": "Agent",
                "id": data.get("task_id"),
                "label": data.get("subagent_type") or data.get("description"),
            }
        ]
    if task_type == "local_workflow":
        return [{"kind": "workflow", "name": data.get("workflow_name"), "id": data.get("task_id")}]
    return []


def _result(msg: dict[str, object]) -> list[dict[str, object]]:
    return [
        {
            "kind": "result",
            "session": msg.get("session_id"),
            "cost_usd": msg.get("total_cost_usd"),
            "turns": msg.get("num_turns"),
            "subtype": msg.get("subtype"),
            "is_error": msg.get("is_error"),
            "text": msg.get("result") or "",
        }
    ]


def _user(msg: dict[str, object]) -> list[dict[str, object]]:
    text = json.dumps(msg.get("content"), default=str)
    found: list[dict[str, object]] = []
    found.extend({"kind": "guard-denial"} for _ in range(text.count(GUARD_DENIED)))
    reds = list(dict.fromkeys(RED.findall(text)))
    if reds:
        found.append({"kind": "session-red", "gates": reds})
    return found


def events(type_name: str, msg: dict[str, object]) -> list[dict[str, object]]:
    """The countable events in one recorded message."""
    if type_name == "ResultMessage":
        return _result(msg)
    if type_name == "UserMessage":
        return _user(msg)
    data = _data(msg)
    if type_name == "TaskStartedMessage":
        return _task_started(data)
    if type_name == "TaskProgressMessage":
        return _workflow_agents(data)
    if type_name == "SystemMessage" and msg.get("subtype") == "init":
        return [{"kind": "init", "model": data.get("model"), "session": data.get("session_id")}]
    return []
