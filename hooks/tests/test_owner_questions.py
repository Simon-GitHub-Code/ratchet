"""Only product questions reach the owner: the skills are held to ``skills/method/questions.toml``.

ADR 0034. Every question a skill tells an agent to put to the owner is written as
``**Ask** `<key>`: "<text>"``, and the registry lists the key with the kind ``product`` and the
same text. A question that a default answers is an ``[[assume]]`` entry instead, and its skill
writes ``**Assume** `<key>``` beside the default the hand-over states.

What this catches: a new ``**Ask**`` line whose key is not registered, whose kind is not
``product``, whose text differs from the registry's, or whose skill the registry does not list;
a sentence in any skill page or template that tells an agent to ask, confirm or wait for an
answer in the words below without an ``**Ask**`` key; a registered question no skill asks or an
assumption no skill states; a product question whose text uses a method term.

What it cannot catch: a question worded outside ``ASKING`` ("get the owner's OK"); a sentence
that puts a negation before the asking words and still asks; a non-product question someone
registered as ``product`` (a reviewer reads that diff of the registry); questions a companion
plugin's own skill asks, which only the rule in the ``AGENTS.md`` template governs; the
pipeline's prompts in ``workflows/``, whose open questions are bounded by their schema's kinds;
and whether an agent obeys the page at run time, which only a live run shows.
"""

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SKILLS = ROOT / "skills"
REGISTRY = SKILLS / "method/questions.toml"

ASK_LINE = re.compile(r'\*\*Ask\*\* `([^`]+)`: "([^"]+)"')
ASK_KEY = re.compile(r"\*\*Ask\*\* `([^`]+)`")
ASSUME_KEY = re.compile(r"\*\*Assume\*\* `([^`]+)`")
ASKING = re.compile(
    r"question tool|AskUserQuestion|\bdialogs?\b"
    r"|\bask (?:the owner|the user|them|him|her|whether|if|for approval|for confirmation)\b"
    r"|\bconfirm (?:with|it|that)\b|\bshall I\b|\bcheck with the (?:owner|user)\b"
    r"|\bput\b[^.]{0,40}\bto the owner\b|\bwait for (?:the owner's|their) (?:answer|approval)\b",
    re.IGNORECASE,
)
NEGATION = re.compile(r"\b(?:never|not|no|nothing|neither|nor|without)\b", re.IGNORECASE)
RULE = "product question"
SENTENCE = re.compile(r"(?<=[.?!])\s+|\n\s*[-*|]\s+|\n\s*\d+\.\s+|\n\n")
FENCE = re.compile(r"^```.*?^```", re.MULTILINE | re.DOTALL)
METHOD_TERMS = re.compile(
    r"\b(?:tiers?|bindings?|binds?|guard|gates?|conformance|triggers?|seams?|manifest|ADR"
    r"|application types?|life events?|disputes?|ratchet|spec|pipeline|lint|branch|commit|merge)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Entry:
    key: str
    table: str
    skills: tuple[str, ...]
    kind: str
    text: str


def registry(path: Path = REGISTRY) -> dict[str, Entry]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    entries: dict[str, Entry] = {}
    for table in ("ask", "assume"):
        for row in data.get(table, []):
            entry = Entry(
                key=row["key"],
                table=table,
                skills=tuple(row["skills"]),
                kind=row.get("kind", ""),
                text=row.get("text", ""),
            )
            assert entry.key not in entries, f"{entry.key} is registered twice"
            entries[entry.key] = entry
    return entries


def pages(root: Path = SKILLS) -> list[Path]:
    return sorted(page for page in root.rglob("*.md") if not page.is_symlink())


def skill_of(page: Path, root: Path = SKILLS) -> str:
    return page.relative_to(root).parts[0]


def sentences(text: str) -> list[tuple[int, str]]:
    """Each sentence of a page outside code fences, with the line it starts on."""
    text = FENCE.sub(lambda fence: "\n" * fence.group(0).count("\n"), text)
    found: list[tuple[int, str]] = []
    start = 0
    for separator in [*SENTENCE.finditer(text), None]:
        end = separator.start() if separator else len(text)
        chunk = text[start:end]
        if chunk.strip():
            line = text.count("\n", 0, start + len(chunk) - len(chunk.lstrip())) + 1
            found.append((line, " ".join(chunk.split())))
        start = separator.end() if separator else end
    return found


def asks_unmarked(sentence: str) -> bool:
    """Whether a sentence tells an agent to ask the owner without naming a registered question."""
    asking = ASKING.search(sentence)
    if asking is None or ASK_KEY.search(sentence) or RULE in sentence.lower():
        return False
    return NEGATION.search(sentence, 0, asking.start()) is None


def ask_problems(text: str, skill: str, entries: dict[str, Entry]) -> list[tuple[int, str]]:
    """Each ``**Ask**`` line of a page that does not match its registered product question."""
    problems: list[tuple[int, str]] = []
    for match in ASK_KEY.finditer(text):
        key, line = match.group(1), text.count("\n", 0, match.start()) + 1
        entry = entries.get(key)
        if entry is None or entry.table != "ask":
            problems.append((line, f"`{key}` is not a registered question"))
            continue
        if entry.kind != "product":
            problems.append((line, f"`{key}` is a {entry.kind!r} question, not product"))
        if skill not in entry.skills:
            problems.append((line, f"`{key}` is not registered for the {skill} skill"))
        quoted = ASK_LINE.match(text, match.start())
        if quoted is None or quoted.group(2) != entry.text:
            problems.append((line, f'`{key}` must read "{entry.text}"'))
    return problems


def findings(root: Path = SKILLS, entries: dict[str, Entry] | None = None) -> list[str]:
    """Every way the pages under ``root`` break ADR 0034's rule, one line each."""
    entries = registry() if entries is None else entries
    problems: list[str] = []
    asked: set[str] = set()
    stated: set[str] = set()
    for page in pages(root):
        text, where = page.read_text(encoding="utf-8"), page.relative_to(root.parent)
        problems += [
            f"{where}:{line}: asks the owner with no registered key: {sentence}"
            for line, sentence in sentences(text)
            if asks_unmarked(sentence)
        ]
        keyed = ask_problems(text, skill_of(page, root), entries)
        problems += [f"{where}:{line}: {why}" for line, why in keyed]
        asked.update(match.group(1) for match in ASK_KEY.finditer(text))
        stated.update(match.group(1) for match in ASSUME_KEY.finditer(text))
    for entry in entries.values():
        if entry.table == "ask" and entry.key not in asked:
            problems.append(f"{REGISTRY.name}: `{entry.key}` is registered and no skill asks it")
        if entry.table == "assume" and entry.key not in stated:
            problems.append(f"{REGISTRY.name}: `{entry.key}` is assumed and no skill states it")
    return problems


def test_the_owner_is_asked_only_registered_product_questions() -> None:
    assert findings() == []


def test_every_registered_question_is_a_product_question_in_plain_words() -> None:
    for entry in registry().values():
        if entry.table != "ask":
            continue
        assert entry.kind == "product", entry.key
        assert METHOD_TERMS.search(entry.text) is None, f"{entry.key}: {entry.text}"


def plant(tmp_path: Path, page: str, registered: str = "") -> list[str]:
    skill = tmp_path / "skills/demo/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(page, encoding="utf-8")
    table = tmp_path / "questions.toml"
    table.write_text(registered, encoding="utf-8")
    return findings(tmp_path / "skills", registry(table))


ASKED = '[[ask]]\nkey = "demo.what"\nskills = ["demo"]\nkind = "product"\ntext = "What is it?"\n'


def test_a_question_a_default_answers_is_caught_however_it_is_added(tmp_path: Path) -> None:
    # Why: the 2026-11-13 dogfood asked the owner ten questions a default answers. Each way of
    # adding one back must turn this red.
    assert plant(tmp_path, "Ask through the question tool which linter to use.\n") == [
        (
            "skills/demo/SKILL.md:1: asks the owner with no registered key:"
            " Ask through the question tool which linter to use."
        )
    ]


def test_an_unregistered_key_and_a_kind_other_than_product_are_caught(tmp_path: Path) -> None:
    tooling = ASKED.replace('"product"', '"tooling"')
    assert plant(tmp_path, '**Ask** `demo.what`: "What is it?"\n', tooling) == [
        "skills/demo/SKILL.md:1: `demo.what` is a 'tooling' question, not product"
    ]
    assert plant(tmp_path / "b", '**Ask** `demo.tools`: "Which linter?"\n') == [
        "skills/demo/SKILL.md:1: `demo.tools` is not a registered question"
    ]


def test_a_reworded_question_and_one_no_skill_asks_are_caught(tmp_path: Path) -> None:
    assert plant(tmp_path, '**Ask** `demo.what`: "Which tools?"\n', ASKED) == [
        'skills/demo/SKILL.md:1: `demo.what` must read "What is it?"'
    ]
    assume = '[[assume]]\nkey = "demo.tools"\nskills = ["demo"]\nreplaces = "Which linter?"\n'
    assert plant(tmp_path / "b", "Nothing is asked.\n", ASKED + assume) == [
        "questions.toml: `demo.what` is registered and no skill asks it",
        "questions.toml: `demo.tools` is assumed and no skill states it",
    ]


def test_a_rule_against_asking_is_not_a_question(tmp_path: Path) -> None:
    page = (
        "Never ask the owner to confirm a draft.\n\n"
        'The owner is asked one thing.\n\n- **Ask** `demo.what`: "What is it?"\n'
    )
    assert plant(tmp_path, page, ASKED) == []
    assert plant(tmp_path / "b", "Ask the owner whether to go on, and do not wait.\n") != []
