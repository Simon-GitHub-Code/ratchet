"""The plugin names no stack tool anywhere a model reads it (ADR 0042).

A name in the text an agent reads is a default: an agent handed "e.g. a worker pool such as X"
binds X. So every shipped page, template, prompt and script is searched for the names below,
whole words and any letter case, and the test is red on each one found. This file is never loaded
as context, since tests are not part of the method a skill or a hook hands an agent; that is why
the list lives here and only here.

What this does not name, and why. The plugin's own tools stay (ADR 0042): Python, its runner
`just`, `git`, the forges' CLIs, the `pre-commit` framework the commit-hook template uses and the
runner that starts it, and Node for the plugin's own workflow and adapters. Formats tied to no
stack stay: Cobertura, LCOV, JUnit XML, OTLP, TOML, JSON, YAML. A word that is also plain English
("express", "black", "render", "go") is left out, so the list misses those tools.

``RESIDUE`` is what still names a tool, file by file, each with the reason it cannot go yet: code
that must read a tool's own output or files, the guard hook's interpreter lists (its security
boundary, which a project must not be able to loosen), and lines another change owns. An entry
that no longer matches is red too, so the residue only shrinks.

What it cannot catch: a tool this list does not name, a name split across a line break or
written in another spelling, and a name in a file outside ``SHIPPED``.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# Every place a model reads: the skills (pages, the method, every template), the pipeline's
# prompts, and the scripts and hooks an agent runs or reads when a gate goes red.
SHIPPED = ("skills/**/*", "workflows/*.js", "scripts/*", "hooks/*.py", "hooks/ratchet_guards/*")

DENIED = (
    # test runners, their parallel and shard plugins, property-testing libraries
    "pytest",
    "pytest-xdist",
    "xdist",
    "pytest-split",
    "vitest",
    "jest",
    "mocha",
    "nextest",
    "go test",
    "hypothesis-jsonschema",
    "fast-check",
    "proptest",
    "quickcheck",
    # task runners, monorepo and pipeline engines
    "make -j",
    "turbo",
    "turborepo",
    "nx",
    "pants",
    "dagger",
    "earthly",
    "bazel",
    # formatters, linters, type checkers
    "eslint",
    "biome",
    "oxlint",
    "prettier",
    "ruff",
    "mypy",
    "pyright",
    "flake8",
    "pylint",
    "tsc",
    "clippy",
    "gofmt",
    "rustfmt",
    # mutation, coverage, duplication and dependency tools
    "mutmut",
    "cosmic-ray",
    "stryker",
    "cargo-mutants",
    "pitest",
    "coverage.py",
    "istanbul",
    "c8",
    "nyc",
    "jacoco",
    "tarpaulin",
    "import-linter",
    "dependency-cruiser",
    "knip",
    "vulture",
    "deptry",
    "jscpd",
    # package managers and their manifests
    "npm",
    "pnpm",
    "yarn",
    "bun",
    "pip",
    "poetry",
    "pipenv",
    "conda",
    "cargo",
    "maven",
    "gradle",
    "package.json",
    "pyproject.toml",
    "cargo.toml",
    "go.mod",
    "gemfile",
    # languages other than the plugin's own, frameworks and runtimes
    "typescript",
    "javascript",
    "rust",
    "golang",
    "java",
    "kotlin",
    "ruby",
    "rails",
    "django",
    "flask",
    "fastapi",
    "react",
    "vue",
    "svelte",
    "next.js",
    "deno",
    "hono",
    "electron",
    "tauri",
    "flutter",
    # stores, containers, hosts and their files
    "sqlite",
    "postgres",
    "postgresql",
    "mysql",
    "redis",
    "mongodb",
    "prisma",
    "alembic",
    "docker",
    "dockerfile",
    "kubernetes",
    "k8s",
    "terraform",
    "vercel",
    "netlify",
    "fly.toml",
    "heroku",
    "railway",
    "wrangler",
    "procfile",
    # security, browser, load and telemetry tools
    "semgrep",
    "codeql",
    "bandit",
    "gitleaks",
    "trufflehog",
    "hadolint",
    "shellcheck",
    "actionlint",
    "zizmor",
    "trivy",
    "grype",
    "snyk",
    "playwright",
    "cypress",
    "selenium",
    "lighthouse",
    "pa11y",
    "k6",
    "locust",
    "opentelemetry",
)
PATTERN = re.compile(
    r"(?<![\w.-])(" + "|".join(re.escape(name) for name in DENIED) + r")(?![\w-])", re.IGNORECASE
)

# Why each stays, by file: (the names, lower case, the reason).
RESIDUE: dict[str, tuple[frozenset[str], str]] = {
    "scripts/mutation_survivors.py": (
        frozenset({"mutmut", "stryker", "cargo", "pyproject.toml"}),
        "it reads two tools' reports as they are; the converters move to the project with an ADR",
    ),
    "scripts/bite.py": (
        frozenset({"mutmut", "stryker", "typescript"}),
        "the planted reports that prove the converters of mutation_survivors.py bite",
    ),
    "scripts/coverage_groups.py": (
        frozenset({"istanbul", "c8"}),
        "NO_COVER recognises the exclusion directives an absolute path may not carry",
    ),
    "scripts/pins.py": (
        frozenset({"docker", "dockerfile"}),
        "the pins gate reads container build files to hold each image to a digest",
    ),
    "scripts/prose.py": (
        frozenset({"dockerfile"}),
        "the extensions of the files whose identifiers the prose gate reads",
    ),
    "scripts/parse-js.mjs": (
        frozenset({"javascript", "typescript"}),
        "the plugin's own workflow and adapters, which it compiles as their runtime does",
    ),
    "hooks/ratchet_guards/bash.py": (
        frozenset({"poetry"}),
        "the guard hook's command classifier, its security boundary (ADR 0042)",
    ),
    "hooks/ratchet_guards/interpreters.py": (
        frozenset({"deno", "ruby", "bun"}),
        "the guard hook's inline-interpreter list, its security boundary (ADR 0042)",
    ),
    "hooks/ratchet_guards/writes.py": (
        frozenset({"deno", "ruby", "bun"}),
        "the guard hook's write detection, its security boundary (ADR 0042)",
    ),
    "skills/method/guards.md": (
        frozenset({"deno", "ruby", "bun"}),
        "it says which inline interpreters the guard hook reads, as the hook does",
    ),
    "skills/method/application-types.toml": (
        frozenset(
            {"fly.toml", "vercel", "netlify", "wrangler", "procfile", "railway", "k8s"}
            | {"prisma", "alembic"}
        ),
        "the route's signs of a life event; moving them into the binding changes the route",
    ),
    "workflows/pipeline.js": (
        frozenset({"cargo", "pnpm", "javascript"}),
        "two prompt examples the design pass's owner rewrites in a change of its own",
    ),
}


def shipped() -> list[Path]:
    found = {path for pattern in SHIPPED for path in ROOT.glob(pattern) if path.is_file()}
    return sorted(path for path in found if "__pycache__" not in path.parts)


def names_in(path: Path) -> set[str]:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return set()
    return {match.group(1).lower() for match in PATTERN.finditer(text)}


def test_no_shipped_file_names_a_stack_tool_beyond_its_residue() -> None:
    problems = []
    for path in shipped():
        relative = path.relative_to(ROOT).as_posix()
        allowed = RESIDUE.get(relative, (frozenset(), ""))[0]
        problems += [f"{relative}: names {name}" for name in sorted(names_in(path) - allowed)]
    assert problems == []


def test_the_residue_only_shrinks() -> None:
    stale = []
    for relative, (allowed, _) in RESIDUE.items():
        gone = allowed - names_in(ROOT / relative)
        stale += [f"{relative}: no longer names {name}; drop it from RESIDUE" for name in gone]
    assert stale == []


def test_the_search_finds_a_name_in_any_case_and_not_inside_a_word(tmp_path: Path) -> None:
    page = tmp_path / "page.md"
    page.write_text("Use Vitest, or `go test -p`; a turbocharged runner; not_ruff.\n")
    assert names_in(page) == {"vitest", "go test"}
    binary = tmp_path / "blob"
    binary.write_bytes(b"\xff\xfe pnpm")
    assert names_in(binary) == set()
