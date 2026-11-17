from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

PLUGIN_MARKERS = (
    "CLAUDE_PLUGIN_ROOT",
    "/plugin/skills/",
    "/plugin/scripts/",
    "/plugin/workflows/",
    "/plugin/hooks/",
    "/plugins/cache/",
)
READERS = frozenset({"Skill", "Read", "Bash", "Grep", "Glob"})
CONTEXT = 50


@dataclass(frozen=True)
class Name:
    label: str
    pattern: re.Pattern[str]
    said: re.Pattern[str] | None = None


SAID = {"Go": re.compile(r"\bGo\b|\bGolang\b")}


def _names(*entries: tuple[str, str, bool]) -> tuple[Name, ...]:
    return tuple(
        Name(label, re.compile(pattern, re.IGNORECASE if fold else 0), SAID.get(label))
        for label, pattern, fold in entries
    )


LEXICON = _names(
    ("Python", r"\bpython\b", True),
    ("TypeScript", r"\btypescript\b", True),
    ("JavaScript", r"\bjavascript\b", True),
    ("Node.js", r"\bnode\.?js\b", True),
    ("Go", r"\b(?i:golang|gofmt)\b|\bgo (?:test|build|run|mod|vet|install)\b", False),
    ("Rust", r"\brust(?:c|fmt|up)?\b|\bcargo\b", True),
    ("Java", r"\bjava\b|\bjvm\b", True),
    ("Kotlin", r"\bkotlin\b|\bktlint\b|\bdetekt\b", True),
    ("Ruby", r"\bruby\b|\brails\b|\bbundler\b|\brubocop\b|\brspec\b", True),
    ("C#/.NET", r"\bc#|\.net\b|\bdotnet\b|\basp\.net\b|\bnuget\b|\bxunit\b", True),
    ("PHP", r"\bphp\b|\blaravel\b", True),
    ("Elixir", r"\belixir\b", True),
    ("Flask", r"\bflask\b", True),
    ("Django", r"\bdjango\b", True),
    ("FastAPI", r"\bfastapi\b", True),
    ("React", r"\bReact(?:\.js)?\b(?! to\b)", False),
    ("Next.js", r"\bnext\.js\b", True),
    ("npm", r"\bnpm\b|\bpnpm\b", True),
    ("yarn", r"\byarn (?:add|install|run)\b", False),
    ("pip", r"\bpip (?:install|freeze)\b|\bpoetry\b", True),
    ("uv", r"\buv (?:run|sync|add|pip|lock)\b", False),
    ("pytest", r"\bpytest\b|\bunittest\b", True),
    ("ruff", r"\bruff\b|\bmypy\b|\bpyright\b|\bmutmut\b", True),
    ("eslint", r"\beslint\b|\bprettier\b|\bvitest\b|\bjest\b|\bstryker\b", True),
    ("hypothesis", r"\bhypothesis\b(?= (?:test|librar|strateg|property))", True),
    ("clippy", r"\bclippy\b|\bproptest\b", True),
    ("gradle", r"\bgradle\b|\bmaven\b|\bjunit\b", True),
    ("SQLite", r"\bsqlite\b", True),
    ("PostgreSQL", r"\bpostgres(?:ql)?\b|\bmysql\b|\bredis\b|\bmongodb\b", True),
    ("Docker", r"\bdocker\b|\bkubernetes\b", True),
)


@dataclass(frozen=True)
class Source:
    label: str
    text: str


def _text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(_text(v) for v in value)
    if isinstance(value, dict):
        parts = (value.get(k) for k in ("text", "content", "output", "stdout", "additionalContext"))
        return "\n".join(_text(p) for p in parts if p)
    return ""


def _blocks(msg: Mapping[str, object]) -> list[Mapping[str, object]]:
    content = msg.get("content")
    return [b for b in (content if isinstance(content, list) else []) if isinstance(b, dict)]


def plugin_text(records: Iterable[Mapping[str, object]]) -> list[Source]:
    found: list[Source] = []
    watched: dict[str, str] = {}
    for record in records:
        raw = record.get("msg")
        msg: Mapping[str, object] = raw if isinstance(raw, dict) else {}
        kind = record.get("type")
        data = msg.get("data")
        if kind == "SystemMessage" and str(msg.get("subtype") or "").startswith("hook_"):
            found.append(Source(f"hook {msg.get('subtype')}", _text(data)))
        for block in _blocks(msg):
            name = str(block.get("name") or "")
            if block.get("_type") == "ToolUseBlock" and name in READERS:
                args = json.dumps(block.get("input"), default=str)
                if name == "Skill" or any(m in args for m in PLUGIN_MARKERS):
                    watched[str(block.get("id"))] = f"{name} {args[:80]}"
            elif (
                block.get("_type") == "ToolResultBlock" and str(block.get("tool_use_id")) in watched
            ):
                found.append(
                    Source(watched[str(block.get("tool_use_id"))], _text(block.get("content")))
                )
        if kind == "UserMessage" and "Base directory for this skill" in _text(msg.get("content")):
            found.append(Source("skill text", _text(msg.get("content"))))
    return found


def stated(*texts: str) -> frozenset[str]:
    joined = "\n".join(texts)
    return frozenset(n.label for n in LEXICON if (n.said or n.pattern).search(joined))


def scan(sources: Iterable[Source], owner_said: frozenset[str]) -> dict[str, object]:
    items: dict[tuple[str, str], dict[str, object]] = {}
    for source in sources:
        for name in LEXICON:
            if name.label in owner_said:
                continue
            match = name.pattern.search(source.text)
            if match is None:
                continue
            key = (name.label, source.label)
            if key in items:
                items[key]["count"] = int(str(items[key]["count"])) + 1
                continue
            lo, hi = max(match.start() - CONTEXT, 0), match.end() + CONTEXT
            excerpt = " ".join(source.text[lo:hi].split())
            items[key] = {
                "name": name.label,
                "source": source.label,
                "excerpt": excerpt,
                "count": 1,
            }
    names = sorted({str(i["name"]) for i in items.values()})
    return {
        "count": len(items),
        "names": names,
        "owner_said": sorted(owner_said),
        "items": list(items.values()),
    }
