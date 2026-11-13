"""Fold a run's event log into its measurements, and print them as a short table.

Each event is one JSON object with ``s`` (seconds since the run started), ``step`` and ``kind``.
The reducer reads nothing else, so a run reduced twice gives the same numbers, and a run recorded
by another driver (the one-off run, through ``oneoff.py``) reduces the same way.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

Event = dict[str, object]


def read(path: Path) -> Iterator[Event]:
    """The events of a log, one JSON object per line."""
    with path.open(encoding="utf-8") as lines:
        for line in lines:
            if line.strip():
                yield json.loads(line)


def _minute(event: Event) -> float:
    return round(float(str(event.get("s", 0))) / 60, 1)


def _num(value: object) -> float:
    try:
        return float(str(value))
    except ValueError:
        return 0.0


@dataclass
class _Steps:
    rows: list[dict[str, object]] = field(default_factory=list)
    cost: dict[str, float] = field(default_factory=dict)

    def start(self, event: Event, spent: float) -> None:
        self.rows.append(
            {
                "step": event.get("step"),
                "action": event.get("action"),
                "command": event.get("text"),
                "start_min": _minute(event),
                "minutes": None,
                "cost_usd": None,
                "_spent": spent,
            }
        )

    def end(self, event: Event, spent: float) -> None:
        for row in reversed(self.rows):
            if row["step"] == event.get("step") and row["minutes"] is None:
                row["minutes"] = round(_num(event.get("wall_s")) / 60, 1)
                row["cost_usd"] = round(spent - _num(row["_spent"]), 2)
                return

    def table(self) -> list[dict[str, object]]:
        return [{k: v for k, v in row.items() if not k.startswith("_")} for row in self.rows]


@dataclass
class _Fold:
    meta: Event = field(default_factory=dict)
    session_cost: dict[str, float] = field(default_factory=dict)
    owner_usd: float = 0.0
    questions: list[Event] = field(default_factory=list)
    prompts: list[Event] = field(default_factory=list)
    pastes: list[Event] = field(default_factory=list)
    asks: int = 0
    merges: list[Event] = field(default_factory=list)
    gates: list[Event] = field(default_factory=list)
    session_red: Counter[str] = field(default_factory=Counter)
    agents: dict[str, str] = field(default_factory=dict)
    workflows: int = 0
    guard_denials: int = 0
    refusals: list[Event] = field(default_factory=list)
    apps: list[Event] = field(default_factory=list)
    usable_min: float | None = None
    stop: Event = field(default_factory=dict)
    integrity: Event = field(default_factory=dict)
    models: list[str] = field(default_factory=list)
    steps: _Steps = field(default_factory=_Steps)
    last_s: float = 0.0

    @property
    def spent(self) -> float:
        return sum(self.session_cost.values())

    def take(self, event: Event) -> None:
        self.last_s = max(self.last_s, _num(event.get("s", 0)))
        kind = str(event.get("kind"))
        handler = getattr(self, "on_" + kind.replace("-", "_"), None)
        if handler is not None:
            handler(event)

    def on_run(self, event: Event) -> None:
        self.meta = {k: v for k, v in event.items() if k not in ("kind", "s", "step")}

    def on_init(self, event: Event) -> None:
        model = str(event.get("model"))
        if model not in self.models:
            self.models.append(model)

    def on_result(self, event: Event) -> None:
        # Why: the CLI reports a session's cost so far, not the turn's, so the last one counts.
        session = str(event.get("session"))
        self.session_cost[session] = max(
            self.session_cost.get(session, 0.0), _num(event.get("cost_usd"))
        )

    def on_owner_cost(self, event: Event) -> None:
        self.owner_usd += _num(event.get("usd"))

    def on_question(self, event: Event) -> None:
        self.questions.append(event)

    def on_prompt(self, event: Event) -> None:
        self.prompts.append(event)

    def on_paste(self, event: Event) -> None:
        self.pastes.append(event)

    def on_ask(self, _event: Event) -> None:
        self.asks += 1

    def on_merge(self, event: Event) -> None:
        self.merges.append(event)

    def on_gates(self, event: Event) -> None:
        self.gates.append(event)

    def on_session_red(self, event: Event) -> None:
        names = event.get("gates")
        self.session_red.update(str(n) for n in (names if isinstance(names, list) else []))

    def on_agent(self, event: Event) -> None:
        self.agents.setdefault(str(event.get("id")), str(event.get("via")))

    def on_workflow(self, _event: Event) -> None:
        self.workflows += 1

    def on_guard_denial(self, _event: Event) -> None:
        self.guard_denials += 1

    def on_refusal(self, event: Event) -> None:
        self.refusals.append(event)

    def on_app(self, event: Event) -> None:
        self.apps.append(event)

    def on_usable(self, event: Event) -> None:
        if self.usable_min is None:
            self.usable_min = _minute(event)

    def on_stop(self, event: Event) -> None:
        self.stop = {"reason": event.get("reason"), "detail": event.get("detail", "")}

    def on_integrity(self, event: Event) -> None:
        self.integrity = {"unchanged": event.get("unchanged"), "files": event.get("files")}

    def on_step(self, event: Event) -> None:
        self.steps.start(event, self.spent)

    def on_step_end(self, event: Event) -> None:
        self.steps.end(event, self.spent)


def _questions(questions: list[Event]) -> dict[str, object]:
    classes = Counter(str(q.get("class")) for q in questions)
    return {
        "total": len(questions),
        "product": classes["product"],
        "default_able": classes["default-able"],
        "recommended_offered": sum(q.get("recommended") is not None for q in questions),
        "took_recommended": sum(bool(q.get("took_recommended")) for q in questions),
        "by_channel": dict(Counter(str(q.get("channel")) for q in questions)),
        "list": [
            {
                "minute": _minute(q),
                "step": q.get("step"),
                "class": q.get("class"),
                "channel": q.get("channel"),
                "header": q.get("header"),
                "question": q.get("question"),
                "answer": q.get("answer"),
                "recommended": q.get("recommended"),
                "took_recommended": q.get("took_recommended"),
            }
            for q in questions
        ],
    }


def _red_gates(gates: list[Event]) -> list[dict[str, object]]:
    """Each red gate of a driver-run gate command, and whether the rerun was red too."""
    reruns: dict[tuple[object, object], set[str]] = {}
    for run in gates:
        if run.get("attempt") == 2:  # noqa: PLR2004
            reruns[(run.get("command"), run.get("of"))] = _names(run, "red")
    found = []
    for run in gates:
        if run.get("attempt") != 1:
            continue
        rerun = reruns.get((run.get("command"), run.get("id")))
        found.extend(
            {
                "gate": name,
                "command": run.get("command"),
                "minute": _minute(run),
                "red_on_rerun": None if rerun is None else name in rerun,
            }
            for name in _names(run, "red")
        )
    return found


def _names(run: Event, status: str) -> set[str]:
    listed = run.get("gates")
    return {
        str(g.get("name"))
        for g in (listed if isinstance(listed, list) else [])
        if isinstance(g, dict) and g.get("status") == status
    }


def _gate_coverage(gates: list[Event]) -> dict[str, object]:
    """Of the widest gate run the driver saw, the gates that judged and the ones that waited."""
    runs = [g for g in gates if g.get("attempt") == 1 and isinstance(g.get("gates"), list)]
    if not runs:
        return {"ran": [], "later": {}, "command": None}
    widest = max(runs, key=lambda g: len(g["gates"]))  # type: ignore[arg-type]
    listed = [g for g in widest["gates"] if isinstance(g, dict)]  # type: ignore[union-attr]
    return {
        "command": widest.get("command"),
        "minute": _minute(widest),
        "ran": sorted(str(g["name"]) for g in listed if g.get("status") != "later"),
        "later": {str(g["name"]): g.get("trigger") for g in listed if g.get("status") == "later"},
    }


def _app(apps: list[Event]) -> dict[str, object]:
    if not apps:
        return {"checked": False}
    last = apps[-1]
    return {
        "checked": True,
        "minute": _minute(last),
        "start_command": last.get("start_command"),
        "start_source": last.get("source"),
        "starts": bool(last.get("started")),
        "serves_page": bool(last.get("page_ok")),
        "slice_works": bool(last.get("slice_ok")),
        "finding": last.get("finding"),
        "checks": len(apps),
    }


def reduce(events: Iterable[Event]) -> dict[str, object]:
    """The run's measurements."""
    fold = _Fold()
    for event in events:
        fold.take(event)
    merges = [
        {
            "minute": _minute(m),
            "branch": m.get("branch"),
            "how": m.get("how"),
            "commit": m.get("commit"),
            "owner_text": m.get("owner_text"),
        }
        for m in fold.merges
    ]
    prompts = Counter(str(p.get("origin")) for p in fold.prompts)
    return {
        "meta": {**fold.meta, "models_seen": fold.models},
        "stop": {**fold.stop, "minute": round(fold.last_s / 60, 1)},
        "minutes": {
            "total": round(fold.last_s / 60, 1),
            "to_first_merge": merges[0]["minute"] if merges else None,
            "to_first_usable_merge": fold.usable_min,
        },
        "app": _app(fold.apps),
        "questions": _questions(fold.questions),
        "prompts": {
            "total": len(fold.prompts),
            "guard": prompts["guard"],
            "permission": prompts["permission"],
            "unknown": prompts["unknown"],
            "denied": sum(p.get("decision") == "deny" for p in fold.prompts),
        },
        "guard_denials": fold.guard_denials,
        "harness_refusals": [
            {"minute": _minute(r), "why": r.get("why"), "command": r.get("command")}
            for r in fold.refusals
        ],
        "pastes": {
            "count": len(fold.pastes),
            "commands": [
                {"minute": _minute(p), "command": p.get("command"), "exit": p.get("exit")}
                for p in fold.pastes
            ],
        },
        "asks": fold.asks,
        "merges": merges,
        "hand_merges": sum(m["how"] == "hand" for m in merges),
        "red_gates": _red_gates(fold.gates),
        "session_red_gates": dict(fold.session_red),
        "gates": _gate_coverage(fold.gates),
        "agents": {
            "spawned": len(fold.agents),
            "by_via": dict(Counter(fold.agents.values())),
            "workflows": fold.workflows,
        },
        "cost_usd": {
            "session": round(fold.spent, 2),
            "owner": round(fold.owner_usd, 2),
            "sessions": len(fold.session_cost),
        },
        "steps": fold.steps.table(),
        "integrity": fold.integrity,
    }


def _at(metrics: dict[str, object], path: str) -> object:
    value: object = metrics
    for key in path.split("."):
        value = value.get(key) if isinstance(value, dict) else None
    return value


def _cell(value: object) -> str:
    return "n/a" if value is None else str(value)


def _count(value: object) -> int:
    return len(value) if isinstance(value, (list, dict)) else 0


def table(metrics: dict[str, object]) -> str:
    """The measurements a person reads first, as a Markdown table."""

    def at(path: str) -> str:
        return _cell(_at(metrics, path))

    red = _at(metrics, "red_gates")
    reds = ", ".join(
        f"{r['gate']} ({_cell(r['red_on_rerun'])})"
        for r in (red if isinstance(red, list) else [])
        if isinstance(r, dict)
    )
    app = (
        f"starts: {at('app.starts')}, page: {at('app.serves_page')},"
        f" slice: {at('app.slice_works')}"
        if _at(metrics, "app.checked")
        else "not checked"
    )
    prompts = int(_at(metrics, "prompts.permission") or 0) + int(
        _at(metrics, "prompts.unknown") or 0
    )
    rows = [
        ("Stopped", f"{at('stop.reason')} at {at('stop.minute')} min"),
        ("Minutes to first merged change", at("minutes.to_first_merge")),
        ("Minutes to first usable merged change", at("minutes.to_first_usable_merge")),
        ("App", app),
        ("App finding", at("app.finding")),
        (
            "Questions (product / default-able)",
            f"{at('questions.total')} ({at('questions.product')} /"
            f" {at('questions.default_able')})",
        ),
        (
            "Recommended option offered / taken",
            f"{at('questions.recommended_offered')} / {at('questions.took_recommended')}",
        ),
        ("Guard approval prompts", at("prompts.guard")),
        ("Other permission prompts", str(prompts)),
        ("Guard denials the agent met", at("guard_denials")),
        ("Shell commands the owner pasted", at("pastes.count")),
        ("Things the owner asked the assistant to do", at("asks")),
        ("Hand merges", f"{at('hand_merges')} of {_count(_at(metrics, 'merges'))}"),
        ("Red gates (red again on rerun)", reds or "none"),
        (
            "Gates judged / waiting (`later:`)",
            f"{_count(_at(metrics, 'gates.ran'))} / {_count(_at(metrics, 'gates.later'))}",
        ),
        ("Agents spawned", f"{at('agents.spawned')} ({at('agents.workflows')} workflow runs)"),
        (
            "Cost, product session (owner model)",
            f"${at('cost_usd.session')} (${at('cost_usd.owner')})",
        ),
    ]
    lines = ["| Measure | Value |", "| --- | --- |"]
    lines += [f"| {name} | {value} |" for name, value in rows]
    return "\n".join(lines) + "\n"
