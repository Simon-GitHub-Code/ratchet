import json

import pytest

from first_hour import owner
from first_hour.persona import load

DIALOG = {
    "questions": [
        {
            "header": "Stack",
            "question": "Which stack should it use?",
            "options": [
                {"label": "TypeScript, Node, SQLite (Recommended)", "description": "Fits."},
                {"label": "Python, Flask", "description": "Also fine."},
            ],
            "multiSelect": False,
        },
        {
            "header": "Out",
            "question": "What stays out of the first slice?",
            "options": [
                {"label": "Tags", "description": ""},
                {"label": "Search", "description": "We recommend leaving it out."},
                {"label": "Sharing", "description": ""},
            ],
            "multiSelect": True,
        },
        "not a question",
    ]
}


def test_a_dialog_reads_as_its_questions_with_the_recommended_option_found():
    stack, out = owner.questions_of(DIALOG)
    assert stack.recommended == "TypeScript, Node, SQLite (Recommended)"
    assert out.multi
    assert out.recommended == "Search"
    assert owner.questions_of({"questions": "nonsense"}) == []


def test_a_question_with_no_recommendation_has_none():
    question = owner.Question("H", "Q?", (owner.Option("A"), owner.Option("B")))
    assert question.recommended is None


def test_a_default_able_question_takes_the_recommended_option_whatever_the_model_picked():
    stack, _ = owner.questions_of(DIALOG)
    verdict = owner.Verdict(owner.Kind.DEFAULT_ABLE, ("Python, Flask",), "no idea")
    answer = owner.answer(stack, verdict)
    assert answer.text == "TypeScript, Node, SQLite (Recommended)"
    assert answer.took_recommended
    event = answer.event(owner.Channel.DIALOG)
    assert event["class"] == "default-able"
    assert event["took_recommended"] is True
    assert event["channel"] == "dialog"


def test_a_product_question_keeps_the_owners_pick_and_says_the_recommendation_was_declined():
    stack, _ = owner.questions_of(DIALOG)
    verdict = owner.Verdict(owner.Kind.PRODUCT, ("python, flask",), "I like Python")
    answer = owner.answer(stack, verdict)
    assert answer.text == "Python, Flask"
    assert answer.recommended == "TypeScript, Node, SQLite (Recommended)"
    assert not answer.took_recommended


def test_a_pick_matches_a_label_with_or_without_its_recommended_suffix():
    stack, _ = owner.questions_of(DIALOG)
    verdict = owner.Verdict(owner.Kind.PRODUCT, ("TypeScript, Node, SQLite",), "")
    assert owner.answer(stack, verdict).took_recommended


def test_a_multi_select_product_question_answers_every_matching_label_once():
    _, out = owner.questions_of(DIALOG)
    verdict = owner.Verdict(owner.Kind.PRODUCT, ("Tags", "Sharing", "Tags", "Nope"), "")
    assert owner.answer(out, verdict).text == "Tags, Sharing"


def test_a_single_select_keeps_only_the_first_matching_pick():
    stack, _ = owner.questions_of(DIALOG)
    verdict = owner.Verdict(owner.Kind.PRODUCT, ("Python, Flask", "TypeScript, Node, SQLite"), "")
    assert owner.answer(stack, verdict).text == "Python, Flask"


def test_a_product_answer_in_the_owners_own_words_is_kept_as_free_text():
    question = owner.Question("Purpose", "What is it?", (owner.Option("A web app"),))
    verdict = owner.Verdict(owner.Kind.PRODUCT, ("A read-later list for me.",), "")
    answer = owner.answer(question, verdict)
    assert answer.text == "A read-later list for me."
    assert answer.recommended is None
    assert not answer.took_recommended


def test_a_default_able_question_with_no_recommendation_and_no_match_takes_the_first_option():
    question = owner.Question("Seam", "Which seam?", (owner.Option("A"), owner.Option("B")))
    verdict = owner.Verdict(owner.Kind.DEFAULT_ABLE, ("whatever",), "")
    assert owner.answer(question, verdict).text == "A"


def test_a_question_with_no_options_and_no_pick_is_told_to_go_ahead():
    question = owner.Question("Go", "Shall I?", ())
    verdict = owner.Verdict(owner.Kind.DEFAULT_ABLE, (), "")
    assert owner.answer(question, verdict).text == owner.GO_AHEAD


def test_a_verdict_parses_from_structured_output_or_from_text_around_json():
    structured = owner.parse_verdict({"class": "product", "answer": ["Tags"], "reason": "r"})
    assert structured == owner.Verdict(owner.Kind.PRODUCT, ("Tags",), "r")
    text = 'Sure: {"class": "default-able", "answer": "Yes", "reason": "tech"} done'
    assert owner.parse_verdict(text).picks == ("Yes",)
    assert owner.parse_verdict({"class": "product", "answer": 3}).picks == ()


@pytest.mark.parametrize(
    ("raw", "error"),
    [
        ("no json here", ValueError),
        ('{"class": "maybe", "answer": []}', ValueError),
        (json.dumps([1, 2]), ValueError),
        (["a list"], TypeError),
    ],
)
def test_a_reply_that_is_not_a_verdict_is_refused(raw, error):
    with pytest.raises(error):
        owner.parse_verdict(raw)


def test_a_turn_that_asks_nothing_needs_no_reply():
    reply = owner.parse_reply(
        {"needs_reply": False, "class": "product", "reply": "", "reason": "x"}
    )
    assert not reply.needs_reply


def test_a_default_able_turn_question_is_answered_with_go_ahead_whatever_the_model_wrote():
    raw = {"needs_reply": True, "class": "default-able", "reply": "use Postgres", "reason": "r"}
    reply = owner.parse_reply(raw)
    assert reply.needs_reply
    assert reply.text == owner.GO_AHEAD


def test_a_product_turn_question_is_answered_in_the_owners_words_and_an_empty_one_is_no_reply():
    raw = {"needs_reply": True, "class": "product", "reply": " Keep one item. ", "reason": "r"}
    assert owner.parse_reply(raw).text == "Keep one item."
    empty = {"needs_reply": True, "class": "product", "reply": "", "reason": "r"}
    assert not owner.parse_reply(empty).needs_reply


def test_a_life_event_proposal_becomes_a_question_and_other_also_lines_do_not():
    line = (
        "`Dockerfile` is a sign that the project is `deployed` (people reach it): if it is,"
        " type `/ratchet:safeguard deployed`"
    )
    event, question = owner.life_event_question(line)
    assert event == "deployed"
    assert [o.label for o in question.options] == ["Yes, it is", "Not yet"]
    assert owner.life_event_question("the Ratchet plugin in use is 0.12.0, newer") is None


def test_the_prompts_carry_the_persona_the_options_and_the_turns_tail():
    persona = load("read-later")
    assert persona.idea in owner.system_prompt(persona)
    assert "Local only" in owner.system_prompt(persona)
    stack, out = owner.questions_of(DIALOG)
    assert "- Python, Flask: Also fine." in owner.question_prompt(stack)
    assert "several" in owner.question_prompt(out)
    bare = owner.Question("H", "Q?", ())
    assert "own words" in owner.question_prompt(bare)
    long = "x" * 10_000 + "THE END"
    prompt = owner.turn_prompt(long)
    assert "THE END" in prompt
    assert len(prompt) < 7_000


def test_a_pick_that_quotes_a_label_and_its_description_names_the_longest_such_label():
    question = owner.Question(
        "Defaults",
        "Accept the defaults?",
        (owner.Option("Accept (Recommended)"), owner.Option("Accept, keep it local")),
    )
    verdict = owner.Verdict(owner.Kind.PRODUCT, ("Accept, keep it local: No remote.",), "")
    answer = owner.answer(question, verdict)
    assert answer.text == "Accept, keep it local"
    assert not answer.took_recommended
    assert owner.names("Yes, trusted (Recommended): fits", "Yes, trusted (Recommended)")
    assert not owner.names("Yesterday", "Yes")
    assert not owner.names("anything", "")
