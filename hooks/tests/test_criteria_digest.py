import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/criteria_digest.py"
HEADING = "## Acceptance criteria"
EMPTY_BLOB = "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"
SHELL_PIPELINE = (
    "tr -d '\\r' | awk -v h=\"$1\" '/^## /{f=($0==h)} f && NF' | git hash-object --stdin"
)
BODY = (
    "# Save links\n\nStatus: needs-triage\n\n## What to build\nOne list.\n\n"
    f"{HEADING}\n- [ ] newest first\n\n \t\n- [ ] a re-pasted link is refused\n"
    "### Notes\nkept\n\n## Blocked by\nNone\n"
)
BODIES = (
    BODY,
    BODY.replace("\n", "\r\n"),
    f"{HEADING}\n- one\n- two",
    f"# A\n\n{HEADING}\n\n## Answers\n- Yes.\n",
    f"# A\n\n{HEADING}\n- one\n\n## Other\n- no\n{HEADING}\n- two\n",
    f"# A\n\n{HEADING} \n- not the heading\n\n{HEADING}\n- café → bar\n",
)


def digest(heading: str, body: bytes, *path: Path) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(  # noqa: S603 # -- the script as the conjecture skill runs it
        [sys.executable, str(SCRIPT), heading, *map(str, path)],
        input=None if path else body,
        capture_output=True,
        check=False,
    )


def git_blob(text: bytes) -> str:
    git = shutil.which("git")
    assert git
    done = subprocess.run(  # noqa: S603 # -- git's own blob hash of a fixed text
        [git, "hash-object", "--stdin"], input=text, capture_output=True, check=True
    )
    return done.stdout.decode().strip()


def shell_pipeline(heading: str, body: bytes) -> str:
    bash = shutil.which("bash")
    assert bash
    done = subprocess.run(  # noqa: S603 # -- the pipeline the skill gave before #312, $0 intact
        [bash, "-c", SHELL_PIPELINE, "pipeline", heading],
        input=body,
        capture_output=True,
        check=True,
    )
    return done.stdout.decode().strip()


def test_the_digest_is_the_blob_of_the_heading_and_each_non_blank_line_under_it() -> None:
    done = digest(HEADING, BODY.encode())
    assert done.returncode == 0, done.stderr
    kept = f"{HEADING}\n- [ ] newest first\n- [ ] a re-pasted link is refused\n### Notes\nkept\n"
    assert done.stdout.decode() == f"{git_blob(kept.encode())}\n"


@pytest.mark.parametrize("body", BODIES)
def test_the_digest_is_what_the_shell_pipeline_printed_with_its_record_intact(body: str) -> None:
    done = digest(HEADING, body.encode())
    assert done.returncode == 0, done.stderr
    assert done.stdout.decode().strip() == shell_pipeline(HEADING, body.encode())
    assert done.stdout.decode().strip() != EMPTY_BLOB


def test_a_carriage_return_changes_nothing() -> None:
    assert digest(HEADING, BODY.replace("\n", "\r\n").encode()).stdout == (
        digest(HEADING, BODY.encode()).stdout
    )


def test_a_body_in_a_file_has_the_digest_of_the_same_body_on_stdin(tmp_path: Path) -> None:
    path = tmp_path / "ticket.md"
    path.write_bytes(BODY.encode())
    assert digest(HEADING, b"", path).stdout == digest(HEADING, BODY.encode()).stdout


def test_a_changed_criterion_changes_the_digest_and_a_section_after_it_does_not() -> None:
    written = digest(HEADING, BODY.encode()).stdout
    assert digest(HEADING, BODY.replace("is refused", "is kept twice").encode()).stdout != written
    assert digest(HEADING, BODY.replace("None", "#4").encode()).stdout == written
    assert digest(HEADING, (BODY + "\n## Conjecture\n- One list.\n").encode()).stdout == written


def test_a_body_without_the_heading_prints_no_digest_and_says_so() -> None:
    body = BODY.replace(HEADING, "## Criteria")
    assert shell_pipeline(HEADING, body.encode()) == EMPTY_BLOB
    done = digest(HEADING, body.encode())
    assert done.returncode == 1
    assert done.stdout == b""
    assert done.stderr.decode() == f"criteria digest: the body has no `{HEADING}` line\n"


def test_a_file_that_cannot_be_read_is_an_error(tmp_path: Path) -> None:
    done = digest(HEADING, b"", tmp_path / "absent.md")
    assert done.returncode == 2
    assert done.stdout == b""
    assert "absent.md" in done.stderr.decode()
