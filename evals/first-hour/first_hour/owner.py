from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

    from first_hour.persona import Persona

RECOMMENDED = re.compile(r"\brecommend(?:ed)?\b", re.IGNORECASE)
NOT_RECOMMENDED = re.compile(r"\b(?:not|n't|never)\s+(?:\w+\s+)?recommend", re.IGNORECASE)
GO_AHEAD = "Please go ahead with what you recommend."
TURN_TAIL = 6000
OFFER_TAIL = 3000
QUESTION_TAIL = 800


class Kind(StrEnum):
    PRODUCT = "product"
    DEFAULT_ABLE = "default-able"


class Channel(StrEnum):
    DIALOG = "dialog"
    PROSE = "prose"
    ALSO = "also"


@dataclass(frozen=True)
class Option:
    label: str
    description: str = ""


@dataclass(frozen=True)
class Question:
    header: str
    text: str
    options: tuple[Option, ...]
    multi: bool = False

    @property
    def recommended(self) -> str | None:
        for option in self.options:
            if RECOMMENDED.search(option.label):
                return option.label
        for option in self.options:
            if RECOMMENDED.search(option.description) and not NOT_RECOMMENDED.search(
                option.description
            ):
                return option.label
        return None


@dataclass(frozen=True)
class Verdict:
    kind: Kind
    picks: tuple[str, ...]
    reason: str


@dataclass(frozen=True)
class Answer:
    question: Question
    kind: Kind
    text: str
    recommended: str | None
    took_recommended: bool
    reason: str

    def event(self, channel: Channel) -> dict[str, object]:
        return {
            "kind": "question",
            "channel": channel.value,
            "class": self.kind.value,
            "header": self.question.header,
            "question": self.question.text,
            "options": [o.label for o in self.question.options],
            "descriptions": [o.description for o in self.question.options],
            "recommended": self.recommended,
            "took_recommended": self.took_recommended,
            "answer": self.text,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class Reply:
    needs_reply: bool
    kind: Kind
    text: str
    reason: str


def questions_of(tool_input: dict[str, object]) -> list[Question]:
    found = []
    raw = tool_input.get("questions")
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        options = item.get("options")
        found.append(
            Question(
                header=str(item.get("header", "")),
                text=str(item.get("question", "")),
                options=tuple(
                    Option(str(o.get("label", "")), str(o.get("description", "")))
                    for o in (options if isinstance(options, list) else [])
                    if isinstance(o, dict)
                ),
                multi=bool(item.get("multiSelect")),
            )
        )
    return found


def _kind(raw: object) -> Kind:
    try:
        return Kind(str(raw))
    except ValueError:
        msg = f"class must be one of {[k.value for k in Kind]}, not {raw!r}"
        raise ValueError(msg) from None


def _as_dict(raw: object) -> dict[str, object]:
    if isinstance(raw, str):
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match is None:
            msg = f"no JSON object in the owner's reply: {raw[:200]!r}"
            raise ValueError(msg)
        raw = json.loads(match.group(0))
    if not isinstance(raw, dict):
        msg = f"the owner's reply is not an object: {raw!r}"
        raise TypeError(msg)
    return raw


def parse_verdict(raw: object) -> Verdict:
    data = _as_dict(raw)
    picks = data.get("answer")
    if isinstance(picks, str):
        picks = [picks]
    if not isinstance(picks, list):
        picks = []
    return Verdict(
        _kind(data.get("class")), tuple(str(p) for p in picks), str(data.get("reason", ""))
    )


def parse_reply(raw: object) -> Reply:
    data = _as_dict(raw)
    kind = _kind(data.get("class", Kind.DEFAULT_ABLE.value))
    needs = bool(data.get("needs_reply"))
    text = GO_AHEAD if kind is Kind.DEFAULT_ABLE else str(data.get("reply", "")).strip()
    return Reply(needs and bool(text), kind, text, str(data.get("reason", "")))


def _norm(text: str) -> str:
    return " ".join(RECOMMENDED.sub("", text).replace("()", "").split()).casefold()


def names(pick: str, label: str) -> bool:
    wanted, have = _norm(pick), _norm(label)
    rest = wanted.removeprefix(have)
    return bool(have) and wanted.startswith(have) and not rest[:1].isalnum()


def label_for(labels: Iterable[str], pick: str) -> str | None:
    matches = [label for label in labels if names(pick, label)]
    return max(matches, key=len) if matches else None


def took(labels: Iterable[str], answer: str, recommended: str | None) -> bool:
    return recommended is not None and label_for([*labels, recommended], answer) == recommended


def _label(question: Question, pick: str) -> str | None:
    return label_for((o.label for o in question.options), pick)


def answer(question: Question, verdict: Verdict) -> Answer:
    recommended = question.recommended
    labels = [label for pick in verdict.picks if (label := _label(question, pick))]
    if verdict.kind is Kind.DEFAULT_ABLE and recommended is not None:
        labels = [recommended]
    elif not question.multi:
        labels = labels[:1]
    if labels:
        text = ", ".join(dict.fromkeys(labels))
    elif verdict.kind is Kind.PRODUCT and verdict.picks:
        text = " ".join(verdict.picks).strip()
    else:
        text = question.options[0].label if question.options else GO_AHEAD
    return Answer(
        question=question,
        kind=verdict.kind,
        text=text,
        recommended=recommended,
        took_recommended=took((o.label for o in question.options), text, recommended),
        reason=verdict.reason,
    )


def life_event_question(line: str) -> tuple[str, Question] | None:
    match = re.search(r"a sign that the project is `([a-z-]+)` \(([^)]*)\)", line)
    if match is None:
        return None
    event, meaning = match.groups()
    return event, Question(
        header="Life event",
        text=f"Is the project now `{event}` ({meaning})? {line}",
        options=(Option("Yes, it is"), Option("Not yet")),
    )


QUESTION_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "class": {"type": "string", "enum": [k.value for k in Kind]},
        "answer": {"type": "array", "items": {"type": "string"}},
        "reason": {"type": "string"},
    },
    "required": ["class", "answer", "reason"],
}

REPLY_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "needs_reply": {"type": "boolean"},
        "class": {"type": "string", "enum": [k.value for k in Kind]},
        "reply": {"type": "string"},
        "reason": {"type": "string"},
    },
    "required": ["needs_reply", "class", "reply", "reason"],
}

RULES = """
You are role-playing the owner described below, answering a coding assistant that is building
your idea. Stay in character. Decide each question's class first:

- "product": the answer depends on what you want the app to do or look like, who it is for, what
  matters first, or one of your firm positions (for example keeping everything local, or the
  language and shape the app must have, if you state one). Answer it in character: pick the option
  labels that fit you, or write your answer in your own words when no option fits.
- "default-able": the question is about how the software is built or run (languages, tools,
  tests, git, branches, labels, checks, the method, approvals of the assistant's own technical
  work) and none of your firm positions covers it. You do not understand it and would leave it to
  the assistant: answer with the option the assistant recommends, or the one that sounds safest
  if none is recommended.

When a firm position names a language or a shape, a question about the language or shape is
product: hold the position, and never pick an option that breaks it even if it is recommended.

A question that asks you to approve a technical change the assistant proposes is default-able. A
question that would put anything online, or create an account or a remote, is product: you say no.

Reply with JSON only.
"""


def system_prompt(persona: Persona) -> str:
    return f"{RULES}\nYour idea: {persona.idea}\n\nWho you are:\n{persona.profile}\n"


def question_prompt(question: Question) -> str:
    options = "\n".join(
        f"- {o.label}" + (f": {o.description}" if o.description else "") for o in question.options
    )
    multi = "You may pick several options." if question.multi else "Pick one option."
    return (
        f"The assistant asks ({question.header}):\n{question.text}\n\nOptions:\n"
        f"{options or '(none: answer in your own words)'}\n\n{multi} Reply as JSON with"
        ' "class" ("product" or "default-able"), "answer" (a list of the exact option labels you'
        ' pick, or one string in your own words) and "reason" (one sentence).'
    )


def prose_question(turn: str, reply: Reply) -> dict[str, object]:
    offered = bool(RECOMMENDED.search(turn[-OFFER_TAIL:]))
    return {
        "kind": "question",
        "channel": Channel.PROSE.value,
        "class": reply.kind.value,
        "header": "turn end",
        "question": turn[-QUESTION_TAIL:],
        "read": turn[-TURN_TAIL:],
        "options": [],
        "recommended": "the assistant's recommendation" if offered else None,
        "took_recommended": offered and reply.kind is Kind.DEFAULT_ABLE,
        "answer": reply.text,
        "reason": reply.reason,
    }


def turn_prompt(text: str) -> str:
    tail = text[-TURN_TAIL:]
    return (
        "The assistant finished its turn with this message:\n\n<<<\n"
        f"{tail}\n>>>\n\nDoes it end by asking you something you must answer before work can go"
        " on: a question, a choice, or a request for your approval? A message that only reports"
        " what was done, or tells you which command to type next, needs no reply. Reply as JSON"
        ' with "needs_reply" (true or false), "class" ("product" or "default-able"), "reply"'
        ' (what you would type back, in character; empty when no reply is needed) and "reason"'
        " (one sentence)."
    )
