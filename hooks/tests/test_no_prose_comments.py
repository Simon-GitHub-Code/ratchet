import ast
import importlib
import inspect
import io
import re
import shutil
import subprocess
import tokenize
import tomllib
from collections import Counter
from collections.abc import Callable, Iterable
from pathlib import Path, PurePosixPath
from types import FunctionType, ModuleType
from typing import NamedTuple

import pytest
from pygments.lexer import Lexer
from pygments.lexers import get_lexer_by_name
from pygments.token import Comment

import comments
import gatelib

ROOT = Path(__file__).resolve().parents[2]
JS_LISTER = Path(__file__).with_name("js_comments.mjs")

OUT_OF_SCOPE = (
    "evals/*/repo/**",
    "evals/*/branch/**",
    "evals/*/hidden/**",
    "evals/_common/justfile",
    ".github/**",
)
NODE_SUFFIXES = (".js", ".mjs")

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
Registry = Callable[[ModuleType], Iterable[FunctionType]]
REGISTRIES: dict[str, Registry] = {
    "tests.drain_model": lambda m: [*m.RULES.values(), *m.LEASE_RULES.values()],
    "tests.pipeline_model": lambda m: m.RULES.values(),
    "tests.ratchet_model": lambda m: m.RULES.values(),
    "tests.safeguard_model": lambda m: [
        check for rule in m.RULES.values() for check in (rule.state_check, rule.step_check) if check
    ],
}
READERS = {
    ("hooks/tests/drain_model.py", "holds"): "tests.drain_model",
    ("hooks/tests/test_drain_model.py", "rule_holds"): "tests.drain_model",
    ("hooks/tests/pipeline_model.py", "rule"): "tests.pipeline_model",
    ("hooks/tests/test_pipeline_model.py", "rule_holds"): "tests.pipeline_model",
    ("hooks/tests/ratchet_model.py", "RULES[rule]"): "tests.ratchet_model",
    ("hooks/tests/test_ratchet_model.py", "rule_holds"): "tests.ratchet_model",
    ("hooks/tests/safeguard_model.py", "check"): "tests.safeguard_model",
}
SOURCE_READERS = frozenset({"getdoc", "getsource"})
DOCUMENTED = (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)


class Tally(NamedTuple):
    comments: int
    docstrings: int


class Readers(NamedTuple):
    kept: frozenset[tuple[str, str]]
    unresolved: tuple[str, ...]


Lister = Callable[[str, str], list[comments.Record]]


def docstrings(text: str) -> dict[int, str]:
    tree = ast.parse(text)
    found = {tree.body[0].lineno: ""} if ast.get_docstring(tree, clean=False) is not None else {}

    def walk(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if not isinstance(child, DOCUMENTED):
                walk(child, prefix)
                continue
            name = f"{prefix}{child.name}"
            if ast.get_docstring(child, clean=False) is not None:
                found[child.body[0].lineno] = name
            walk(child, f"{name}." if isinstance(child, ast.ClassDef) else f"{name}.<locals>.")

    walk(tree, "")
    return found


def python_records(path: str, text: str) -> list[comments.Record]:
    tokens = tokenize.generate_tokens(io.StringIO(text).readline)
    found = [
        comments.Record(path, token.start[0], comments.Kind.COMMENT, token.string[1:].strip())
        for token in tokens
        if token.type == tokenize.COMMENT
    ]
    lines = text.split("\n")
    for line in docstrings(text):
        shown = lines[line - 1].strip().lstrip("rRuU").strip("\"'").strip()
        found.append(comments.Record(path, line, comments.Kind.DOC, shown))
    return found


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
BY_NAME: dict[str, Lister] = {
    "justfile": BASH,
    ".gitignore": gitignore_records,
    "gitignore": gitignore_records,
}
BY_SUFFIX: dict[str, Lister] = {
    ".py": python_records,
    ".sh": BASH,
    ".yml": lexed(get_lexer_by_name("yaml")),
    ".yaml": lexed(get_lexer_by_name("yaml")),
    ".toml": lexed(get_lexer_by_name("toml")),
    ".ts": lexed(get_lexer_by_name("typescript")),
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
        if path and in_a_counted_language(path) and not gatelib.matches(path, list(OUT_OF_SCOPE))
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


def metadata_lines(block: str) -> list[int]:
    rows = block.rstrip("\n").split("\n")
    content = [row.removeprefix("#").removeprefix(" ") for row in rows[1:-1]]
    try:
        tomllib.loads("\n".join(content))
    except tomllib.TOMLDecodeError:
        return []
    kept = [n + 1 for n, line in enumerate(content) if not line.lstrip().startswith("#")]
    return [0, *kept, len(rows) - 1]


def script_metadata(root: Path, paths: Iterable[str]) -> set[tuple[str, int]]:
    lines = set()
    for path in paths:
        if path.endswith(".py"):
            text = (root / path).read_text(encoding="utf-8")
            for block in SCRIPT_METADATA.finditer(text):
                first = text.count("\n", 0, block.start()) + 1
                lines |= {(path, first + n) for n in metadata_lines(block.group(0))}
    return lines


def registered(module: str) -> set[tuple[str, str]]:
    found = set()
    for function in REGISTRIES[module](importlib.import_module(module)):
        source = Path(inspect.getsourcefile(function) or "").resolve()
        found.add((source.relative_to(ROOT).as_posix(), function.__qualname__))
    return found


def readers(root: Path, paths: Iterable[str]) -> Readers:
    kept: set[tuple[str, str]] = set()
    unresolved = []
    for path in paths:
        if not path.endswith(".py"):
            continue
        for node in ast.walk(ast.parse((root / path).read_text(encoding="utf-8"))):
            where = f"{path}:{getattr(node, 'lineno', 0)}"
            if isinstance(node, ast.Name) and node.id == "__doc__":
                kept.add((path, ""))
            elif isinstance(node, ast.Attribute) and node.attr == "__doc__":
                module = READERS.get((path, ast.unparse(node.value)))
                if module is None:
                    unresolved.append(f"{where}: {ast.unparse(node)}")
                else:
                    kept |= registered(module)
            elif (isinstance(node, ast.Attribute) and node.attr in SOURCE_READERS) or (
                isinstance(node, ast.Name) and node.id in SOURCE_READERS
            ):
                unresolved.append(f"{where}: {ast.unparse(node)}")
    return Readers(frozenset(kept), tuple(unresolved))


def kept_lines(root: Path, kept: frozenset[tuple[str, str]]) -> set[tuple[str, int]]:
    lines = set()
    for path in {path for path, _ in kept}:
        source = root / path
        if source.is_file():
            names = {name for kept_path, name in kept if kept_path == path}
            text = source.read_text(encoding="utf-8")
            lines |= {(path, line) for line, name in docstrings(text).items() if name in names}
    return lines


def prose(root: Path, paths: list[str]) -> list[comments.Record]:
    records = node_records(root, [path for path in paths if path.endswith(NODE_SUFFIXES)])
    for path in paths:
        read = lister(path)
        if read is not None:
            records += read(path, (root / path).read_text(encoding="utf-8"))
    comments.shown(root, records)
    allowed = comments.Allowed(tuple(DIRECTIVES.values()), ())
    found = comments.findings(root, records, allowed)
    metadata = script_metadata(root, paths)
    kept = kept_lines(root, readers(root, paths).kept)
    return [
        record
        for record in found
        if (record.path, record.line) not in metadata
        and not (record.kind is comments.Kind.DOC and (record.path, record.line) in kept)
    ]


def count(root: Path, paths: list[str]) -> dict[str, Tally]:
    kinds = Counter((record.path, record.kind) for record in prose(root, paths))
    return {
        path: Tally(kinds[path, comments.Kind.COMMENT], kinds[path, comments.Kind.DOC])
        for path in sorted({path for path, _ in kinds})
    }


def test_the_plugin_holds_no_prose_comment_and_no_docstring_its_code_does_not_read() -> None:
    found = prose(ROOT, tracked(ROOT))
    assert not found, "\n".join(f"{r.path}:{r.line}: {r.kind.value}: {r.text}" for r in found)


def test_every_reader_of_a_docstring_names_the_docstrings_it_reads() -> None:
    assert readers(ROOT, tracked(ROOT)).unresolved == ()


def test_the_docstrings_kept_are_the_help_of_the_scripts_and_the_sentences_of_the_rules() -> None:
    kept = readers(ROOT, tracked(ROOT)).kept
    modules = {path for path, name in kept if not name}
    assert {"scripts/release.py", "scripts/parallel.py", "evals/first-hour/run.py"} <= modules
    for module in REGISTRIES:
        assert registered(module) <= kept
    models = {path for path, name in kept if name}
    assert models == {
        "hooks/tests/drain_model.py",
        "hooks/tests/pipeline_model.py",
        "hooks/tests/ratchet_model.py",
        "hooks/tests/safeguard_model.py",
    }


def test_the_lister_reads_the_plugin_and_its_skills_but_not_eval_projects_or_workflows() -> None:
    paths = set(tracked(ROOT))
    assert {"justfile", ".gitignore", "workflows/pipeline.js", "scripts/parse-js.mjs"} <= paths
    assert {"pyproject.toml", "evals/_common/scaffold.sh", "adapters/pi/ratchet-guard.ts"} <= paths
    assert {"skills/adopt/templates/justfile", "skills/init/templates/gitignore"} <= paths
    assert {"skills/adopt/templates/.github/workflows/ci.yml"} <= paths
    assert not [path for path in paths if path.endswith((".md", ".json", ".lock"))]
    assert not [path for path in paths if path.startswith(".github/")]
    assert not [path for path in paths if re.match(r"evals/[^/]+/(repo|branch|hidden)/", path)]


PLANTED = [
    pytest.param("scripts/owed.py", "x = 1  # planted\n", Tally(1, 0), id="python comment"),
    pytest.param(
        "scripts/owed.py",
        'def planted() -> None:\n    """Planted."""\n',
        Tally(0, 1),
        id="python docstring",
    ),
    pytest.param(
        "scripts/release.py",
        'def planted() -> None:\n    """Planted."""\n',
        Tally(0, 1),
        id="docstring beside a kept help",
    ),
    pytest.param(
        "hooks/tests/drain_model.py",
        'def planted() -> None:\n    """Planted."""\n',
        Tally(0, 1),
        id="docstring beside kept rules",
    ),
    pytest.param("scripts/parse-js.mjs", "// planted\n", Tally(1, 0), id="module comment"),
    pytest.param(
        "adapters/pi/ratchet-guard.ts",
        "type T = {\n  // planted\n  a: string\n}\n",
        Tally(1, 0),
        id="comment inside a type",
    ),
    pytest.param(
        "adapters/pi/ratchet-guard.ts",
        "/* planted\n   over two lines */\n",
        Tally(2, 0),
        id="typed block comment",
    ),
    pytest.param("evals/_common/scaffold.sh", "# planted\n", Tally(1, 0), id="shell"),
    pytest.param("pyproject.toml", "# planted\n", Tally(1, 0), id="settings"),
    pytest.param("justfile", "# planted\n", Tally(1, 0), id="justfile"),
    pytest.param(".gitignore", "# planted\n", Tally(1, 0), id="gitignore"),
    pytest.param("skills/adopt/templates/justfile", "# planted\n", Tally(1, 0), id="template"),
    pytest.param(
        "skills/adopt/templates/.github/workflows/ci.yml",
        "# planted\n",
        Tally(1, 0),
        id="template workflow",
    ),
    pytest.param(
        "skills/init/templates/gitignore", "# planted\n", Tally(1, 0), id="template ignore"
    ),
    pytest.param("skills/method/questions.toml", "# planted\n", Tally(1, 0), id="skill data"),
]


@pytest.mark.parametrize(("path", "planted", "found"), PLANTED)
def test_a_comment_planted_in_a_copy_of_a_file_is_red(
    tmp_path: Path, *, path: str, planted: str, found: Tally
) -> None:
    copy = tmp_path / path
    copy.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / path, copy)
    assert count(tmp_path, [path]) == {}
    with copy.open("a", encoding="utf-8") as file:
        file.write(planted)
    assert count(tmp_path, [path]) == {path: found}


def test_a_module_that_reads_its_own_doc_keeps_its_module_docstring_and_no_other(
    tmp_path: Path,
) -> None:
    text = '"""Usage."""\nprint(__doc__)\n\n\ndef f() -> None:\n    """Not read."""\n'
    (tmp_path / "a.py").write_text(text, encoding="utf-8")
    assert count(tmp_path, ["a.py"]) == {"a.py": Tally(0, 1)}


def test_a_docstring_read_by_a_reader_the_table_does_not_know_is_unresolved(
    tmp_path: Path,
) -> None:
    text = 'def f() -> None:\n    """Read."""\n\n\nprint(f.__doc__, inspect.getdoc(f))\n'
    (tmp_path / "a.py").write_text(text, encoding="utf-8")
    assert readers(tmp_path, ["a.py"]).unresolved == (
        "a.py:5: f.__doc__",
        "a.py:5: inspect.getdoc",
    )
    assert count(tmp_path, ["a.py"]) == {"a.py": Tally(0, 1)}


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
    pytest.param(
        "a.py", "# /// script\n# # the reason\n# dependencies = []\n# ///\n", id="metadata comment"
    ),
    pytest.param("a.yml", f"steps:\n  - uses: a/b@{SHA} # v5\n  # the reason\n", id="note"),
]


@pytest.mark.parametrize(("path", "text"), PROSE_AFTER)
def test_prose_on_the_line_after_a_directive_is_prose(tmp_path: Path, path: str, text: str) -> None:
    (tmp_path / path).write_text(text, encoding="utf-8")
    assert count(tmp_path, [path]) == {path: Tally(1, 0)}


DOCSTRING_AS_DIRECTIVE = [
    pytest.param('def f() -> None:\n    """noqa: E501"""\n', id="lint ignore"),
    pytest.param('def f() -> None:\n    """pragma: no cover"""\n', id="coverage pragma"),
    pytest.param('def f() -> None:  # noqa: D103\n    """Doc."""  # noqa: E501\n', id="beside"),
    pytest.param('#!/usr/bin/env python3\n"""Usage."""\n', id="after an interpreter line"),
]


METADATA_WITH_PROSE = "# /// script\n# the reason it is pinned\n# dependencies = []\n# ///\n"


def test_a_metadata_block_that_holds_prose_is_no_directive(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text(METADATA_WITH_PROSE, encoding="utf-8")
    assert count(tmp_path, ["a.py"]) == {"a.py": Tally(4, 0)}


@pytest.mark.parametrize("text", DOCSTRING_AS_DIRECTIVE)
def test_a_docstring_never_passes_as_a_directive(tmp_path: Path, text: str) -> None:
    (tmp_path / "a.py").write_text(text, encoding="utf-8")
    assert count(tmp_path, ["a.py"]) == {"a.py": Tally(0, 1)}


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


def test_the_docstring_reader_names_each_docstring_as_its_qualified_name() -> None:
    assert docstrings(DOCSTRING_FORMS) == {
        1: "",
        4: "A",
        6: "A.f",
        8: "A.g",
        16: "A.k.<locals>.inner",
        21: "A.k.<locals>.C",
    }


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
