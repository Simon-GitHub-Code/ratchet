import ast
import io
import json
import re
import shutil
import subprocess
import tokenize
import tomllib
from collections import Counter
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path, PurePosixPath
from typing import NamedTuple

import pytest
from pygments.lexer import Lexer
from pygments.lexers import get_lexer_by_name
from pygments.token import Comment

import comments
import gatelib

ROOT = Path(__file__).resolve().parents[2]
RESIDUE = Path(__file__).with_name("no_prose_comments.toml")
JS_LISTER = Path(__file__).with_name("js_comments.mjs")

EVAL_PROJECTS = ("evals/*/repo/**", "evals/*/branch/**", "evals/_common/justfile")
TEMPLATES = ("skills/*/templates/**",)
NODE_SUFFIXES = (".js", ".mjs", ".ts")

DIRECTIVES = {
    "interpreter line": r"^[[:space:]]*#!/",
    "lint ignore": r"#[[:space:]]*noqa\b",
    "type ignore": r"#[[:space:]]*type:[[:space:]]*ignore\b",
    "coverage pragma": r"#[[:space:]]*pragma:[[:space:]]*no[[:space:]]+(cover|branch)\b",
    "mutation pragma": r"#[[:space:]]*pragma:[[:space:]]*no[[:space:]]+mutate\b",
    "pinned version note": (
        r"uses:[[:space:]]*[^[:space:]#]+@[0-9a-f]{40}[[:space:]]+#[[:space:]]*v?[0-9]"
    ),
}
SCRIPT_METADATA = re.compile(
    r"(?m)^# /// (?P<type>[a-zA-Z0-9-]+)$\s(?P<content>(^#(| .*)$\s)+)^# ///$"
)
HEADERS = frozenset({"def", "class"})
OPEN = {"(": 1, "[": 1, "{": 1, ")": -1, "]": -1, "}": -1}
BEFORE_A_STATEMENT = frozenset({tokenize.NL, tokenize.NEWLINE, tokenize.INDENT, tokenize.COMMENT})
BESIDE_A_STRING = frozenset({tokenize.NL, tokenize.COMMENT})


class Tally(NamedTuple):
    comments: int
    docstrings: int


ZERO = Tally(0, 0)
Lister = Callable[[str, str], list[comments.Record]]


def python_records(path: str, text: str) -> list[comments.Record]:
    tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    found = [
        comments.Record(path, token.start[0], comments.Kind.COMMENT, token.string[1:].strip())
        for token in tokens
        if token.type == tokenize.COMMENT
    ]
    lines = text.split("\n")
    for line in docstring_lines(tokens):
        shown = lines[line - 1].strip().lstrip("rRuU").strip("\"'").strip()
        found.append(comments.Record(path, line, comments.Kind.DOC, shown))
    return found


def plain_string(token: tokenize.TokenInfo) -> bool:
    return token.type == tokenize.STRING and token.string.lstrip("rRuU")[0] in "'\""


def docstring_lines(tokens: Iterable[tokenize.TokenInfo]) -> Iterator[int]:
    awaited, header, depth, candidate = True, False, 0, 0
    for token in tokens:
        if candidate and (plain_string(token) or token.type in BESIDE_A_STRING):
            continue
        if candidate and (token.type == tokenize.NEWLINE or token.string == ";"):
            yield candidate
        candidate = 0
        if awaited and token.type in BEFORE_A_STATEMENT:
            continue
        if awaited and plain_string(token):
            candidate = token.start[0]
        awaited = False
        if token.type == tokenize.NAME and token.string in HEADERS:
            header, depth = True, 0
        elif header and token.type == tokenize.OP:
            depth += OPEN.get(token.string, 0)
            header = not (token.string == ":" and depth == 0)
            awaited = not header


def lexed(lexer: Lexer) -> Lister:
    def records(path: str, text: str) -> list[comments.Record]:
        found = []
        for index, kind, value in lexer.get_tokens_unprocessed(text):
            if kind in Comment:
                first = text.count("\n", 0, index) + 1
                parts = value.rstrip("\n").split("\n")
                found += [
                    comments.Record(path, first + offset, comments.Kind.COMMENT, part.strip("# "))
                    for offset, part in enumerate(parts)
                ]
        return found

    return records


def gitignore_records(path: str, text: str) -> list[comments.Record]:
    return [
        comments.Record(path, number, comments.Kind.COMMENT, line[1:].strip())
        for number, line in enumerate(text.split("\n"), 1)
        if line.startswith("#")
    ]


BASH = lexed(get_lexer_by_name("bash"))
BY_NAME: dict[str, Lister] = {"justfile": BASH, ".gitignore": gitignore_records}
BY_SUFFIX: dict[str, Lister] = {
    ".py": python_records,
    ".sh": BASH,
    ".yml": lexed(get_lexer_by_name("yaml")),
    ".yaml": lexed(get_lexer_by_name("yaml")),
    ".toml": lexed(get_lexer_by_name("toml")),
}


def lister(path: str) -> Lister | None:
    pure = PurePosixPath(path)
    return BY_NAME.get(pure.name) or BY_SUFFIX.get(pure.suffix)


def in_a_counted_language(path: str) -> bool:
    return path.endswith(NODE_SUFFIXES) or lister(path) is not None


def tracked(root: Path) -> list[str]:
    paths = (gatelib.git(root, "ls-files", "-z") or "").split("\0")
    return sorted(
        path
        for path in paths
        if path
        and in_a_counted_language(path)
        and not gatelib.matches(path, [*EVAL_PROJECTS, *TEMPLATES])
    )


def node_records(root: Path, paths: list[str]) -> list[comments.Record]:
    if not paths:
        return []
    node = shutil.which("node")
    assert node, "node lists the comments of the JavaScript files"
    listed = subprocess.run(  # noqa: S603
        [node, "--expose-internals", "--no-warnings", str(JS_LISTER), str(root), *paths],
        capture_output=True,
        text=True,
        check=True,
    )
    return comments.parsed(listed.stdout, frozenset(paths))


def script_metadata(root: Path, paths: Iterable[str]) -> set[tuple[str, int]]:
    lines = set()
    for path in paths:
        if path.endswith(".py"):
            text = (root / path).read_text(encoding="utf-8")
            for block in SCRIPT_METADATA.finditer(text):
                first = text.count("\n", 0, block.start()) + 1
                lines |= {(path, first + n) for n in range(block.group(0).count("\n") + 1)}
    return lines


def prose(root: Path, paths: list[str]) -> list[comments.Record]:
    records = node_records(root, [path for path in paths if path.endswith(NODE_SUFFIXES)])
    for path in paths:
        read = lister(path)
        if read is not None:
            records += read(path, (root / path).read_text(encoding="utf-8"))
    comments.shown(root, records)
    metadata = script_metadata(root, paths)
    allowed = comments.Allowed(tuple(DIRECTIVES.values()), ())
    found = comments.findings(root, records, allowed)
    return [record for record in found if (record.path, record.line) not in metadata]


def count(root: Path, paths: list[str]) -> dict[str, Tally]:
    kinds = Counter((record.path, record.kind) for record in prose(root, paths))
    return {
        path: Tally(kinds[path, comments.Kind.COMMENT], kinds[path, comments.Kind.DOC])
        for path in sorted({path for path, _ in kinds})
    }


def render(tallies: dict[str, Tally]) -> str:
    return "".join(
        f"{json.dumps(path)} = {{ comments = {tally.comments}, docstrings = {tally.docstrings} }}\n"
        for path, tally in sorted(tallies.items())
    )


def residue(text: str) -> dict[str, Tally]:
    return {path: Tally(**entry) for path, entry in tomllib.loads(text).items()}


def drift(found: dict[str, Tally], allowed: dict[str, Tally]) -> list[str]:
    problems = []
    for path in sorted(found.keys() | allowed.keys()):
        have, may = found.get(path, ZERO), allowed.get(path, ZERO)
        if path in allowed and may == ZERO:
            problems.append(f"{path}: an entry at zero; drop it")
        for field, held, entry in zip(Tally._fields, have, may, strict=True):
            if held > entry:
                problems.append(f"{path}: {held} prose {field}, more than its entry of {entry}")
            elif held < entry:
                problems.append(f"{path}: {held} prose {field}, under its entry of {entry}")
    return problems


def test_each_file_holds_exactly_the_prose_its_residue_entry_names() -> None:
    problems = drift(count(ROOT, tracked(ROOT)), residue(RESIDUE.read_text(encoding="utf-8")))
    assert not problems, "\n".join(problems)


def test_the_residue_file_is_in_the_form_the_recount_writes() -> None:
    text = RESIDUE.read_text(encoding="utf-8")
    assert render(residue(text)) == text


def test_the_lister_reads_the_plugins_languages_but_not_eval_projects_or_templates() -> None:
    paths = tracked(ROOT)
    assert {"justfile", ".gitignore", "workflows/pipeline.js", "scripts/parse-js.mjs"} <= set(paths)
    assert {".github/workflows/ci.yml", "pyproject.toml", "evals/_common/scaffold.sh"} <= set(paths)
    assert "adapters/pi/ratchet-guard.ts" in paths
    assert not [path for path in paths if path.endswith((".md", ".json", ".lock"))]
    assert not [path for path in paths if "/templates/" in path or "/repo/" in path]


PLANTED = [
    pytest.param("scripts/owed.py", "x = 1  # planted\n", Tally(1, 0), id="python comment"),
    pytest.param(
        "scripts/owed.py",
        'def planted() -> None:\n    """Planted."""\n',
        Tally(0, 1),
        id="python docstring",
    ),
    pytest.param("scripts/parse-js.mjs", "// planted\n", Tally(1, 0), id="module comment"),
    pytest.param(
        "adapters/pi/ratchet-guard.ts",
        "/* planted\n   over two lines */\n",
        Tally(2, 0),
        id="typed block comment",
    ),
    pytest.param("evals/_common/scaffold.sh", "# planted\n", Tally(1, 0), id="shell"),
    pytest.param(".github/workflows/ci.yml", "# planted\n", Tally(1, 0), id="workflow"),
    pytest.param("pyproject.toml", "# planted\n", Tally(1, 0), id="settings"),
    pytest.param("justfile", "# planted\n", Tally(1, 0), id="justfile"),
    pytest.param(".gitignore", "# planted\n", Tally(1, 0), id="gitignore"),
]


@pytest.mark.parametrize(("path", "planted", "rise"), PLANTED)
def test_a_comment_planted_in_a_copy_of_a_file_is_red(
    tmp_path: Path, *, path: str, planted: str, rise: Tally
) -> None:
    copy = tmp_path / path
    copy.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / path, copy)
    before = count(tmp_path, [path]).get(path, ZERO)
    with copy.open("a", encoding="utf-8") as file:
        file.write(planted)
    after = count(tmp_path, [path]).get(path, ZERO)
    assert after == Tally(*(a + b for a, b in zip(before, rise, strict=True)))
    assert drift({path: after}, {path: before} if before != ZERO else {}) != []


def test_an_entry_left_higher_than_its_file_is_red() -> None:
    assert drift({"a.py": Tally(1, 2)}, {"a.py": Tally(2, 2)}) == [
        "a.py: 1 prose comments, under its entry of 2"
    ]
    assert drift({"a.py": Tally(1, 2)}, {"a.py": Tally(1, 3)}) == [
        "a.py: 2 prose docstrings, under its entry of 3"
    ]


def test_a_file_with_prose_and_no_entry_is_red_and_a_file_at_zero_has_none() -> None:
    assert drift({"a.py": Tally(1, 0)}, {}) == ["a.py: 1 prose comments, more than its entry of 0"]
    assert drift({}, {"a.py": ZERO}) == ["a.py: an entry at zero; drop it"]
    assert drift({}, {}) == []


SHA = "fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09"
METADATA = '# /// script\n# requires-python = ">=3.11"\n# dependencies = []\n# ///\n'
ALLOWED = [
    pytest.param("a.py", "#!/usr/bin/env python3\nx = 1\n", id="interpreter line"),
    pytest.param("justfile", "run:\n    #!/usr/bin/env bash\n    true\n", id="recipe interpreter"),
    pytest.param("a.py", "x = 1  # noqa: E501\n", id="lint ignore"),
    pytest.param("a.py", "x = 1  # noqa: E501 # -- a reason\n", id="lint ignore with reason"),
    pytest.param("a.py", "x = f()  # type: ignore[arg-type]\n", id="type ignore"),
    pytest.param("a.py", "x = f()  # type: ignore[misc] # -- a reason\n", id="type ignore reason"),
    pytest.param("a.py", "x = 1  # pragma: no cover\n", id="coverage pragma"),
    pytest.param("a.py", "x = 1  # pragma: no branch # -- a reason\n", id="coverage reason"),
    pytest.param("a.py", "x = 1  # pragma: no mutate # -- a reason\n", id="mutation pragma"),
    pytest.param("a.py", f"{METADATA}x = 1\n", id="inline script metadata"),
    pytest.param("a.yml", f"steps:\n  - uses: a/b@{SHA} # v5\n", id="pinned version note"),
    pytest.param("a.yml", f"steps:\n  - uses: a/b@{SHA} # v5.0.1 the reason\n", id="note reason"),
]


@pytest.mark.parametrize(("path", "text"), ALLOWED)
def test_a_directive_and_a_reason_on_its_line_are_not_prose(
    tmp_path: Path, path: str, text: str
) -> None:
    (tmp_path / path).write_text(text, encoding="utf-8")
    assert count(tmp_path, [path]) == {}


PROSE_AFTER = [
    pytest.param("a.py", "#!/usr/bin/env python3\n# what it does\n", id="interpreter line"),
    pytest.param("a.py", "x = 1  # noqa: E501\n# the reason\n", id="lint ignore"),
    pytest.param("a.py", "x = f()  # type: ignore[misc]\n    # the reason\n", id="type ignore"),
    pytest.param("a.py", "x = 1  # pragma: no cover\n# the reason\n", id="coverage pragma"),
    pytest.param("a.py", f"{METADATA}# the reason\n", id="inline script metadata"),
    pytest.param("a.yml", f"steps:\n  - uses: a/b@{SHA} # v5\n  # the reason\n", id="note"),
]


@pytest.mark.parametrize(("path", "text"), PROSE_AFTER)
def test_prose_on_the_line_after_a_directive_is_prose(tmp_path: Path, path: str, text: str) -> None:
    (tmp_path / path).write_text(text, encoding="utf-8")
    assert count(tmp_path, [path]) == {path: Tally(1, 0)}


DOCSTRING_FORMS = '''\
"""Module."""
import x
class A(B, metaclass=M):
    "Class." "joined"
    def f(self, a={1: 2}, b=lambda: 1) -> dict[str, int]:  # beside the header
        """Method."""  # beside the docstring
        return a
    async def g(self): r"""Raw."""
    def h(self): f"not {x}"
    def i(self): b"bytes"
    def j(self): "not" + "a docstring"
    def k(self):
        if x:
            def inner():

                """Inner."""
        try:
            pass
        except E:
            class C:
                \'Handled.\'; y = 1
        "not the first statement"
f = lambda: "no body"
'''


def test_the_docstring_reader_finds_what_the_language_calls_a_docstring() -> None:
    documented = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    expected = sorted(
        node.body[0].lineno
        for node in ast.walk(ast.parse(DOCSTRING_FORMS))
        if isinstance(node, documented) and ast.get_docstring(node) is not None
    )
    tokens = tokenize.generate_tokens(io.StringIO(DOCSTRING_FORMS).readline)
    assert list(docstring_lines(tokens)) == expected == [1, 4, 6, 8, 16, 21]


IN_STRINGS = [
    pytest.param("a.py", 'x = "# not a comment"\n', id="python"),
    pytest.param("a.mjs", 'const s = "// no"\nconst r = /\\/\\* no/\n', id="module"),
    pytest.param("a.sh", 'echo "# no" a#b\n', id="shell"),
    pytest.param("a.yml", 'a: "# no"\nb: x#y\n', id="yaml"),
    pytest.param("a.toml", 'a = "# no"\n', id="toml"),
]


@pytest.mark.parametrize(("path", "text"), IN_STRINGS)
def test_a_comment_marker_inside_a_string_is_no_comment(
    tmp_path: Path, path: str, text: str
) -> None:
    (tmp_path / path).write_text(text, encoding="utf-8")
    assert count(tmp_path, [path]) == {}


if __name__ == "__main__":
    RESIDUE.write_text(render(count(ROOT, tracked(ROOT))), encoding="utf-8")
