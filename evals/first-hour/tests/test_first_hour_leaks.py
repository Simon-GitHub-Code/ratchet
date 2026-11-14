import pytest

from first_hour import leaks


def record(kind: str, **msg: object) -> dict[str, object]:
    return {"type": kind, "msg": msg}


def use(block_id: str, name: str, **tool_input: object) -> dict[str, object]:
    return {"_type": "ToolUseBlock", "id": block_id, "name": name, "input": tool_input}


def result(block_id: str, content: object) -> dict[str, object]:
    return {"_type": "ToolResultBlock", "tool_use_id": block_id, "content": content}


def test_hook_output_a_skill_load_and_a_read_of_the_plugins_files_are_the_plugins_text() -> None:
    records = [
        record("SystemMessage", subtype="hook_response", data={"output": "Run the pytest gate."}),
        record("SystemMessage", subtype="init", data={"output": "not a hook"}),
        record("AssistantMessage", content=[use("1", "Skill", skill="ratchet:adopt")]),
        record(
            "UserMessage", content=[result("1", [{"type": "text", "text": "Pick a Django app."}])]
        ),
        record("AssistantMessage", content=[use("2", "Read", file_path="/r/plugin/skills/x.md")]),
        record("UserMessage", content=[result("2", "Install with npm.")]),
        record("AssistantMessage", content=[use("3", "Read", file_path="/r/app/src/main.py")]),
        record("UserMessage", content=[result("3", "import flask")]),
        record("AssistantMessage", content=[use("4", "Edit", file_path="/r/plugin/skills/x.md")]),
        record("UserMessage", content="Base directory for this skill: /r/plugin/skills/adopt"),
        record("UserMessage", content=[{"_type": "TextBlock", "text": "plain"}, "x"]),
        {"type": "UserMessage", "msg": "not a dict"},
    ]
    found = leaks.plugin_text(records)
    assert [s.label.split(" ")[0] for s in found] == ["hook", "Skill", "Read", "skill"]
    assert [s.text for s in found[:3]] == [
        "Run the pytest gate.",
        "Pick a Django app.",
        "Install with npm.",
    ]


def test_a_name_the_owner_never_said_is_a_leak_once_per_source_with_where_it_was() -> None:
    sources = [
        leaks.Source("hook a", "Before you start, install pytest and then run pytest again."),
        leaks.Source("hook b", "pytest again"),
        leaks.Source("Skill c", "The python3 scripts need nothing; go run it; gofmt it. Use Rust."),
    ]
    found = leaks.scan(sources, frozenset({"Rust"}))
    names = {(i["name"], i["source"]): i for i in found["items"]}
    assert set(names) == {("pytest", "hook a"), ("pytest", "hook b"), ("Go", "Skill c")}
    assert "install pytest" in str(names[("pytest", "hook a")]["excerpt"])
    assert found["names"] == ["Go", "pytest"]
    assert found["count"] == 3
    assert found["owner_said"] == ["Rust"]


def test_the_same_name_twice_in_one_source_counts_once_with_its_count() -> None:
    source = leaks.Source("hook", "x")
    twice = [source, leaks.Source("hook", "Use Cargo.")]
    twice += [leaks.Source("hook", "Use Cargo here too.")]
    found = leaks.scan(twice, frozenset())
    assert found["count"] == 1
    assert found["items"][0]["count"] == 2


def test_the_plugins_own_runtime_and_runner_are_not_names() -> None:
    text = 'python3 "${CLAUDE_PLUGIN_ROOT}/scripts/route.py" then `just check` with git and gh'
    assert leaks.scan([leaks.Source("skill", text)], frozenset())["count"] == 0


@pytest.mark.parametrize(
    ("text", "label"),
    [
        ("Use Python.", "Python"),
        ("a TypeScript file", "TypeScript"),
        ("node.js", "Node.js"),
        ("cargo test", "Rust"),
        ("the JVM", "Java"),
        ("ktlint", "Kotlin"),
        ("rubocop", "Ruby"),
        ("dotnet test", "C#/.NET"),
        ("C# code", "C#/.NET"),
        ("sqlite file", "SQLite"),
        ("run uv sync", "uv"),
        ("yarn add x", "yarn"),
        ("pip install x", "pip"),
        ("a hypothesis about the bug", None),
        ("a hypothesis library", "hypothesis"),
        ("React app", "React"),
        ("react to it", None),
        ("go test ./...", "Go"),
        ("go ahead", None),
    ],
)
def test_each_lexicon_entry_finds_its_name_and_not_the_plain_word(
    text: str, label: str | None
) -> None:
    found = leaks.scan([leaks.Source("s", text)], frozenset())
    assert (label in found["names"]) if label else not found["names"]


def test_the_owner_saying_go_covers_the_go_commands() -> None:
    said = leaks.stated("It must be written in Go.", "")
    assert "Go" in said
    assert "Go" not in leaks.stated("go ahead and build it")
    assert leaks.stated("I want Rust and Ruby", "pytest") == {"Rust", "Ruby", "pytest"}
