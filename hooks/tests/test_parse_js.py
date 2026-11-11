"""``scripts/parse-js.mjs``, behind ``just parse-workflow`` and ``just parse-adapters``."""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/parse-js.mjs"
WORKFLOW = ROOT / "workflows/pipeline.js"
ADAPTERS = [ROOT / "adapters/pi/ratchet-guard.ts", ROOT / "adapters/opencode/ratchet-guard.ts"]


def parse(kind: str, *files: Path) -> subprocess.CompletedProcess[str]:
    node = shutil.which("node")
    assert node, "node runs the syntax checks"
    return subprocess.run(  # noqa: S603 # -- a fixed argv, no shell
        [node, "--no-warnings", str(SCRIPT), kind, *map(str, files)],
        capture_output=True,
        text=True,
        check=False,
    )


def broken(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_the_shipped_workflow_and_adapters_parse() -> None:
    assert parse("workflow", WORKFLOW).returncode == 0
    assert parse("adapter", *ADAPTERS).returncode == 0


def test_a_workflow_may_return_and_await_at_the_top_level_and_export_its_meta(
    tmp_path: Path,
) -> None:
    source = (
        "export const meta = { name: 'x' }\n"
        "if (!args) {\n  return { verdict: 'blocked' }\n}\n"
        "await agent()\n"
    )
    assert parse("workflow", broken(tmp_path, "ok.js", source)).returncode == 0


@pytest.mark.parametrize(
    "source",
    [
        "const x = = 1\n",
        "export const meta = {\nreturn 1\n",
        "import fs from 'node:fs'\n",
        "function f( {\n",
        "const a = 1\nconst a = 2\n",
    ],
)
def test_a_workflow_with_a_syntax_error_is_red(tmp_path: Path, source: str) -> None:
    done = parse("workflow", broken(tmp_path, "bad.js", source))
    assert done.returncode == 1
    assert "bad.js:" in done.stderr


@pytest.mark.parametrize(
    "source",
    [
        "const x: number = = 1\n",
        "const x: Record<string, string> = {{\n",
        "type T = <{\n",
        "export const a = 1\nexport const a = 2\n",
    ],
)
def test_an_adapter_with_a_syntax_error_is_red(tmp_path: Path, source: str) -> None:
    done = parse("adapter", broken(tmp_path, "bad.ts", source))
    assert done.returncode == 1
    assert "bad.ts:" in done.stderr


def test_one_bad_file_among_good_ones_fails_the_run_and_names_only_it(tmp_path: Path) -> None:
    bad = broken(tmp_path, "bad.ts", "const x: = 1\n")
    done = parse("adapter", ADAPTERS[0], bad, ADAPTERS[1])
    assert done.returncode == 1
    assert [line.split(":")[0] for line in done.stderr.splitlines() if "bad.ts" in line] == [
        str(bad)
    ]
    assert str(ADAPTERS[0]) not in done.stderr


@pytest.mark.parametrize(
    "arguments", [(), ("workflow",), ("typescript", "x.ts"), ("constructor", "x")]
)
def test_a_wrong_command_line_is_a_usage_error(arguments: tuple[str, ...]) -> None:
    node = shutil.which("node")
    assert node
    done = subprocess.run(  # noqa: S603 # -- a fixed argv, no shell
        [node, str(SCRIPT), *arguments], capture_output=True, text=True, check=False
    )
    assert done.returncode == 2
    assert "usage:" in done.stderr
