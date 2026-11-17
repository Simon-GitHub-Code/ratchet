import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

SHIPPED = ("skills/**/*", "workflows/*.js", "scripts/*", "hooks/*.py", "hooks/ratchet_guards/*")

DENIED = (
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
    "make -j",
    "turbo",
    "turborepo",
    "nx",
    "pants",
    "dagger",
    "earthly",
    "bazel",
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

RESIDUE: dict[str, tuple[frozenset[str], str]] = {
    "hooks/ratchet_guards/bash.py": (
        frozenset({"poetry"}),
        "the guard hook's command classifier, its security boundary (ADR 0042)",
    ),
    "hooks/ratchet_guards/interpreters.py": (
        frozenset({"deno", "ruby", "bun"}),
        "the guard hook's inline-interpreter list, its security boundary (ADR 0042)",
    ),
    "hooks/ratchet_guards/writes.py": (
        frozenset({"ruby"}),
        "the guard hook's write detection, its security boundary (ADR 0042)",
    ),
    "skills/method/guards.md": (
        frozenset({"deno", "ruby", "bun"}),
        "it says which inline interpreters the guard hook reads, as the hook does",
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
