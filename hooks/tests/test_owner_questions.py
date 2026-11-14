"""Only product questions reach the owner: the skills are held to ``skills/method/questions.toml``.

ADR 0034. Every question a skill tells an agent to put to the owner is written as
``**Ask** `<key>`: "<text>"``, and the registry lists the key with the kind ``product`` and the
same text, or, under ``architecture.`` alone, the kind ``architecture`` (ADR 0049). A question
that a default answers is an ``[[assume]]`` entry instead, and its skill writes
``**Assume** `<key>``` beside the default the hand-over states.

What this catches: a new ``**Ask**`` line whose key is not registered, whose kind is not
``product``, whose text differs from the registry's, or whose skill the registry does not list;
a sentence in any skill page or template that tells an agent to ask, confirm or wait for an
answer in the words below without an ``**Ask**`` key; a registered question no skill asks or an
assumption no skill states; a product question whose text uses a method term; and a sentence
about asking that puts questions to the owner one at a time, one by one, alone, one (question)
per message, in a message of its own or a separate message, separately, singly, individually,
each in turn or one after another, where ADR 0034 puts all of a step's open questions in one
message. That last check also reads ``workflows/pipeline.js``, whose strings tell the
orchestrator how to put a blocked run's open questions to the owner; the other checks skip
``workflows/``.

A negation before the asking words ("never ask the owner") is a rule, not a question, unless
``until`` comes between them ("do not start until you ask the owner"), which still asks.

What it cannot catch: a question worded outside ``ASKING`` ("see what the owner thinks", "run
the table past them"); a sentence that puts any other negation before the asking words and
still asks ("do not bind a row before you ask the owner"); a quoted question with no word that
addresses anyone within forty characters of it; a non-product question someone registers as
``product`` with matching skill text, since nothing reviews the registry's diff on its own (ADR
0034); questions a companion plugin's own skill asks, which only the rule in the ``AGENTS.md``
template governs; an unkeyed question in the pipeline's prompts in ``workflows/``, whose open
questions are bounded by their schema's kinds; whether an agent obeys the page at run time,
which only a live run shows; a sentence that asks one by one in words the one-message check
lacks ("then the next", "after each answer", "in a second message", "follow up with the
rest"); and one whose asking words are two sentences back or in another paragraph.
"""

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SKILLS = ROOT / "skills"
PIPELINE = ROOT / "workflows/pipeline.js"
REGISTRY = SKILLS / "method/questions.toml"

ASK_LINE = re.compile(r'\*\*Ask\*\* `([^`]+)`: "([^"]+)"')
ASK_KEY = re.compile(r"\*\*Ask\*\* `([^`]+)`")
ASSUME_KEY = re.compile(r"\*\*Assume\*\* `([^`]+)`")
ASKING = re.compile(
    r"question tool|AskUserQuestion|\bdialogs?\b"
    r"|\bask (?:the owner|the user|them|him|her|whether|if|for approval|for confirmation)\b"
    r"|\bconfirm (?:with|it|that|before|first)\b|\bshall I\b|\bcheck with the (?:owner|user)\b"
    r"|\bput\b[^.]{0,40}\bto the owner\b"
    r"|\bwait for (?:a yes|an? (?:answer|reply)|the owner|the user|their|his|her|approval)\b"
    r"|\blet the (?:owner|user) (?:pick|choose|decide|select)\b"
    r"|\b(?:owner|user)'s (?:OK|okay|go-ahead|sign-off|yes)\b"
    r"|\b(?:the owner|the user|them|him|her)\b[^.]{0,20}\bapprove\b"
    r"|\b(?:get|seek|request) (?:\w+ )?approval\b"
    r'|\b(?:say|tell|ask|to the owner|to the user)\b[^"]{0,40}"[^"]*\?"',
    re.IGNORECASE,
)
NEGATION = re.compile(r"\b(?:never|not|no|nothing|neither|nor|without)\b", re.IGNORECASE)
UNTIL = re.compile(r"\buntil (?:you |they |the agent )?$", re.IGNORECASE)
RULE = "product question"
ARCHITECTURE = "architecture"
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
    if UNTIL.search(sentence, 0, asking.start()):
        return True
    return NEGATION.search(sentence, 0, asking.start()) is None


def allowed_kind(entry: Entry) -> bool:
    """Whether a question's kind may reach the owner: product, or the architecture (ADR 0049).

    Only a key under ``architecture.`` may carry the kind ``architecture``, so the exception
    cannot grow into a tooling question under another name.
    """
    return entry.kind == "product" or (
        entry.kind == ARCHITECTURE and entry.key.startswith(f"{ARCHITECTURE}.")
    )


def ask_problems(text: str, skill: str, entries: dict[str, Entry]) -> list[tuple[int, str]]:
    """Each ``**Ask**`` line of a page that does not match its registered product question."""
    problems: list[tuple[int, str]] = []
    for match in ASK_KEY.finditer(text):
        key, line = match.group(1), text.count("\n", 0, match.start()) + 1
        entry = entries.get(key)
        if entry is None or entry.table != "ask":
            problems.append((line, f"`{key}` is not a registered question"))
            continue
        if not allowed_kind(entry):
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
        assert allowed_kind(entry), entry.key
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


def test_the_architecture_kind_is_allowed_only_under_its_own_keys(tmp_path: Path) -> None:
    # Why: ADR 0049 asks the architecture and the tools at adoption. The kind must not become a
    # way to register any tooling question.
    system = ASKED.replace('"demo.what"', '"architecture.what"').replace(
        '"product"', '"architecture"'
    )
    assert plant(tmp_path, '**Ask** `architecture.what`: "What is it?"\n', system) == []
    elsewhere = ASKED.replace('"product"', '"architecture"')
    assert plant(tmp_path / "b", '**Ask** `demo.what`: "What is it?"\n', elsewhere) == [
        "skills/demo/SKILL.md:1: `demo.what` is a 'architecture' question, not product"
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


@pytest.mark.parametrize(
    "sentence",
    [
        "Get the owner's OK on the tools table before you write the binding.",
        "Check with the owner which linter they prefer.",
        "Confirm before you install the tools.",
        'Then say to the owner: "Do you want Vitest or Jest for the tests?" and go on.',
        "Use AskUserQuestion to let the owner pick the test runner.",
        "Let the owner pick the linter from the two you found.",
        "Show the table and have the owner approve it.",
        "Print the tools table and wait for a yes.",
        "Do not start step 6 until you ask the owner whether the tools are fine.",
        "Confirm with the owner that the tools are right.",
        "Wait for the owner before you bind the rows.",
        "Get the owner's go-ahead, then install.",
        "Let the user choose the formatter.",
    ],
)
def test_each_way_of_asking_the_verifier_planted_is_caught(tmp_path: Path, sentence: str) -> None:
    # Why: the verifier of PR #47 planted ten phrasings that ask the owner a tooling question in
    # the adopt skill, and the lint caught three of them.
    assert plant(tmp_path, sentence + "\n") != [], sentence


def test_a_rule_against_asking_is_not_a_question(tmp_path: Path) -> None:
    page = (
        "Never ask the owner to confirm a draft.\n\n"
        'The owner is asked one thing.\n\n- **Ask** `demo.what`: "What is it?"\n'
    )
    assert plant(tmp_path, page, ASKED) == []
    assert plant(tmp_path / "b", "Ask the owner whether to go on, and do not wait.\n") != []


ONE_BY_ONE = re.compile(
    r"\bone at a time\b|\b(?:owner|asked|questions?) alone\b|\bone by one\b"
    r"|\bone (?:question )?per message\b|\bone after another\b|\ba message of its own\b"
    r"|\bseparate messages?\b|\bseparately\b|\bsingly\b|\bindividually\b|\beach in turn\b",
    re.IGNORECASE,
)
ABOUT_ASKING = re.compile(r"\bask(?:s|ed|ing)?\b|\bquestions?\b|\bdialogs?\b", re.IGNORECASE)
ONE_MESSAGE = (
    "in one message, each with your recommended answer first and why, so the owner can accept"
    " them all at once"
)


def asked_one_by_one(root: Path = SKILLS, also: tuple[Path, ...] = (PIPELINE,)) -> list[str]:
    """Each sentence that tells an agent to ask the owner one question per message.

    A sentence counts when it, or the sentence before it in the same paragraph, is about asking:
    "One at a time, in this order" follows the sentence that names the questions. Besides the
    skill pages it reads ``also``, by default the pipeline script, whose strings tell the
    orchestrator how to put a blocked run's open questions to the owner.
    """
    found: list[str] = []
    for page in [*pages(root), *also]:
        text = FENCE.sub(lambda f: "\n" * f.group(0).count("\n"), page.read_text(encoding="utf-8"))
        for block in re.finditer(r"[^\n]+(?:\n[^\n]+)*", text):
            first, previous = text.count("\n", 0, block.start()), ""
            for line, sentence in sentences(block.group(0)):
                if ONE_BY_ONE.search(sentence) and ABOUT_ASKING.search(previous + sentence):
                    found.append(f"{page.relative_to(root.parent)}:{first + line}: {sentence}")
                previous = sentence + " "
    return found


def test_all_open_questions_of_a_step_go_in_one_message() -> None:
    # Why: the skills said "one at a time" and "alone" while the spec step already put all of a
    # step's questions in one dialog, and the conjecture asked again in a message of its own. An
    # owner who accepts every recommendation should do it once per step.
    assert asked_one_by_one() == []
    places = ["init/templates/AGENTS.md", "method/SKILL.md", "method/records.md"]
    places += ["method/pipeline.md"]
    for page in places:
        text = " ".join((SKILLS / page).read_text(encoding="utf-8").split())
        assert ONE_MESSAGE in text, page
    for page in ("init/templates/AGENTS.md", "conjecture/SKILL.md"):
        text = " ".join((SKILLS / page).read_text(encoding="utf-8").split())
        assert "join the same message as the spec's" in text, page
    assert ONE_MESSAGE in " ".join(PIPELINE.read_text(encoding="utf-8").split())


def test_the_pipeline_script_is_read_for_questions_asked_one_by_one(tmp_path: Path) -> None:
    script = tmp_path / "pipeline.js"
    script.write_text(
        "const told = [\n  'Put each open question to the owner, one at a time',\n]\n",
        encoding="utf-8",
    )
    (tmp_path / "skills").mkdir()
    assert asked_one_by_one(tmp_path / "skills", also=(script,)) == [
        "pipeline.js:1: const told = [ 'Put each open question to the owner, one at a time', ]"
    ]


@pytest.mark.parametrize(
    "sentence",
    [
        "Ask the questions one by one, in this order.",
        "One question per message, each in the words written here.",
        "Each question goes in a message of its own.",
        "Ask the owner each question separately.",
        "Put each question to the owner in a separate message.",
        "Ask them singly, in the user's words.",
        "Ask every fork, each in turn, with the answer the spec leans to first.",
    ],
)
def test_each_way_of_asking_one_by_one_the_verifier_planted_is_caught(
    tmp_path: Path, sentence: str
) -> None:
    # Why: the verifier of PR #47 replaced "all in one message" with each of these, and the check
    # caught only "one at a time" and "one per message".
    page = tmp_path / "skills/demo/SKILL.md"
    page.parent.mkdir(parents=True)
    page.write_text(sentence + "\n", encoding="utf-8")
    assert asked_one_by_one(tmp_path / "skills", also=()) != [], sentence


def test_the_feature_skill_asks_a_parked_tickets_questions_in_one_message() -> None:
    # Why: the verifier removed "all in one message" from the step that asks them, and nothing
    # went red: the step said nothing about how many messages, which the check cannot see.
    text = " ".join((SKILLS / "feature/SKILL.md").read_text(encoding="utf-8").split())
    step_7 = text.split("7. **The questions, then the route.**", 1)[1].split(" ## ", 1)[0]
    assert "ask every parked ticket's open questions as `feature.open-question`, all in one" in (
        step_7
    )
    asking = text.split("## Questions for the owner", 1)[1]
    assert "All of them in one message, each as the spec role wrote it" in asking


def test_a_question_asked_one_by_one_is_caught(tmp_path: Path) -> None:
    page = tmp_path / "skills/demo/SKILL.md"
    page.parent.mkdir(parents=True)
    page.write_text(
        "Ask the questions one at a time.\n\n"
        "The owner is asked three things. One per message, in this order.\n\n"
        "Write the answers. Commit the file alone.\n\n"
        "Nothing is checked yet. Merge them one at a time.\n",
        encoding="utf-8",
    )
    assert asked_one_by_one(tmp_path / "skills", also=()) == [
        "skills/demo/SKILL.md:1: Ask the questions one at a time.",
        "skills/demo/SKILL.md:3: One per message, in this order.",
    ]
