import sys
from pathlib import Path

import pytest

import parser_fuzz


def write_script(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


CLEAN_PARSER = """import pathlib, sys
data = pathlib.Path(sys.argv[1]).read_bytes()
try:
    text = data.decode("utf-8")
except UnicodeDecodeError:
    sys.exit(1)
sys.stdout.write(str(len(text)))
"""

CLEAN_LISTER = """import pathlib, sys
path = sys.argv[1]
assert path.endswith(".src")
data = pathlib.Path(path).read_bytes()
if b"\\x00" in data:
    sys.exit(1)
lines = data.decode("utf-8", errors="replace").splitlines()
for idx, line in enumerate(lines, 1):
    if "#" in line:
        print(f"{path}\\t{idx}\\tcomment\\t#")
    elif "/*" in line:
        print(f"{path}\\t{idx}\\tdoc\\t/*")
        print("")
"""


def test_clean_parser_passes_with_appended_path_and_token_substitution(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    script = write_script(tmp_path, "clean_parser.py", CLEAN_PARSER)
    assert (
        parser_fuzz.main(["parser-fuzz", "--extension", ".src", "--", sys.executable, str(script)])
        == 0
    )
    assert "parser-fuzz: green" in capsys.readouterr().out
    assert (
        parser_fuzz.main(
            ["parser-fuzz", "--mode", "parser", "--", sys.executable, str(script), "{file}"]
        )
        == 0
    )
    assert "parser-fuzz: green" in capsys.readouterr().out


def test_clean_lister_passes_with_valid_records_and_deterministic_rejections(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    script = write_script(tmp_path, "clean_lister.py", CLEAN_LISTER)
    assert (
        parser_fuzz.main(
            [
                "parser-fuzz",
                "--mode",
                "lister",
                "--extension",
                ".src",
                "--",
                sys.executable,
                str(script),
                "{file}",
            ]
        )
        == 0
    )
    assert "parser-fuzz: green" in capsys.readouterr().out


def test_parser_signal_crash_fails_red(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    script = write_script(
        tmp_path,
        "crasher.py",
        "import os, pathlib, signal, sys\n"
        "if b'unterminated' in pathlib.Path(sys.argv[1]).read_bytes():\n"
        "    os.kill(os.getpid(), signal.SIGKILL)\n",
    )
    assert parser_fuzz.main(["parser-fuzz", "--", sys.executable, str(script)]) == 1
    out = capsys.readouterr().out
    assert "crashed with signal" in out
    assert "parser-fuzz: red" in out


def test_parser_timeout_fails_red(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    script = write_script(
        tmp_path,
        "slow.py",
        "import pathlib, sys, time\n"
        "if pathlib.Path(sys.argv[1]).read_bytes() == b'':\n"
        "    time.sleep(2.0)\n",
    )
    assert (
        parser_fuzz.main(["parser-fuzz", "--timeout", "0.05", "--", sys.executable, str(script)])
        == 1
    )
    out = capsys.readouterr().out
    assert "timed out after 0.05s" in out
    assert "parser-fuzz: red" in out


def test_non_deterministic_parser_fails_red(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    counter = tmp_path / "count.txt"
    script = write_script(
        tmp_path,
        "flaky.py",
        "import pathlib, sys\n"
        f"p = pathlib.Path({str(counter)!r})\n"
        "n = int(p.read_text()) + 1 if p.exists() else 1\n"
        "p.write_text(str(n))\n"
        "print(n)\n",
    )
    assert parser_fuzz.main(["parser-fuzz", "--", sys.executable, str(script)]) == 1
    out = capsys.readouterr().out
    assert "non-deterministic" in out
    assert "parser-fuzz: red" in out


def test_second_run_crash_fails_red(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    counter = tmp_path / "second.txt"
    script = write_script(
        tmp_path,
        "second_crash.py",
        "import os, pathlib, signal\n"
        f"p = pathlib.Path({str(counter)!r})\n"
        "if p.exists():\n"
        "    os.kill(os.getpid(), signal.SIGKILL)\n"
        "p.write_text('1')\n",
    )
    assert parser_fuzz.main(["parser-fuzz", "--", sys.executable, str(script)]) == 1
    out = capsys.readouterr().out
    assert "second run crashed with signal" in out
    assert "parser-fuzz: red" in out


@pytest.mark.parametrize(
    ("emitted", "expected"),
    [
        ("a\\t1\\tcomment\\n", "malformed lister record"),
        ("\\t1\\tcomment\\talpha\\n", "malformed lister record"),
        ("a\\tone\\tcomment\\talpha\\n", "malformed lister record"),
        ("a\\t1\\tremark\\talpha\\n", "malformed lister record"),
        ("a\\t0\\tcomment\\talpha\\n", "out of range"),
        ("a\\t99\\tcomment\\talpha\\n", "out of range"),
        ("a\\t1\\tcomment\\tmissing-text\\n", "not found on line 1"),
    ],
)
def test_bad_lister_output_fails_red(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], *, emitted: str, expected: str
) -> None:
    script = write_script(
        tmp_path,
        "bad_lister.py",
        "import pathlib, sys\n"
        "if b'\\xef\\xbb\\xbf' in pathlib.Path(sys.argv[1]).read_bytes():\n"
        f"    sys.stdout.write('{emitted}')\n",
    )
    assert (
        parser_fuzz.main(["parser-fuzz", "--mode", "lister", "--", sys.executable, str(script)])
        == 1
    )
    out = capsys.readouterr().out
    assert expected in out
    assert "parser-fuzz: red" in out


def test_cli_usage_errors_and_unexecutable_command(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as missing_sep:
        parser_fuzz.main(["parser-fuzz"])
    assert missing_sep.value.code == 2
    assert "must follow '--'" in capsys.readouterr().err

    with pytest.raises(SystemExit) as empty_cmd:
        parser_fuzz.main(["parser-fuzz", "--"])
    assert empty_cmd.value.code == 2

    with pytest.raises(SystemExit) as bad_timeout:
        parser_fuzz.main(["parser-fuzz", "--timeout", "0", "--", "echo"])
    assert bad_timeout.value.code == 2
    assert "--timeout must be positive" in capsys.readouterr().err

    assert parser_fuzz.main(["parser-fuzz", "--", "/no/such/command-parser-fuzz"]) == 1
    out = capsys.readouterr().out
    assert "cannot execute" in out
    assert "parser-fuzz: red" in out
