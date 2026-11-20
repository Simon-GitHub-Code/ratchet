import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/criteria_digest.py"
HEADING = "## Acceptance criteria"
EMPTY_BLOB = "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"
NO_HEADING = 3
DID_NOT_RUN = 2
ID_WIDTH = {"sha1": 40, "sha256": 64}
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
VIAS = ("file", "stdin")


def digest(
    body: bytes,
    via: str,
    where: Path,
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[bytes]:
    path = where / "ticket.md"
    path.write_bytes(body)
    return subprocess.run(  # noqa: S603 # -- the script as the conjecture skill runs it
        [sys.executable, str(SCRIPT), HEADING, *([str(path)] if via == "file" else [])],
        input=body if via == "stdin" else None,
        cwd=cwd,
        env=env,
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


def shell_pipeline(heading: str, body: bytes, cwd: Path | None = None) -> str:
    bash = shutil.which("bash")
    assert bash
    done = subprocess.run(  # noqa: S603 # -- the pipeline the skill gave before #312, $0 intact
        [bash, "-c", SHELL_PIPELINE, "pipeline", heading],
        input=body,
        cwd=cwd,
        capture_output=True,
        check=True,
    )
    return done.stdout.decode().strip()


def printed(body: str, tmp_path: Path, via: str = "stdin", *, cwd: Path | None = None) -> str:
    done = digest(body.encode(), via, tmp_path, cwd=cwd)
    assert done.returncode == 0, done.stderr
    assert done.stderr == b""
    return done.stdout.decode()


def test_the_digest_is_the_blob_of_the_heading_and_each_non_blank_line_under_it(
    tmp_path: Path,
) -> None:
    kept = f"{HEADING}\n- [ ] newest first\n- [ ] a re-pasted link is refused\n### Notes\nkept\n"
    assert printed(BODY, tmp_path) == f"{git_blob(kept.encode())}\n"


@pytest.mark.parametrize("body", BODIES)
def test_the_digest_is_what_the_shell_pipeline_printed_with_its_record_intact(
    body: str, tmp_path: Path
) -> None:
    assert printed(body, tmp_path).strip() == shell_pipeline(HEADING, body.encode())
    assert printed(body, tmp_path).strip() != EMPTY_BLOB


def repository_of(object_format: str, tmp_path: Path) -> Path:
    git = shutil.which("git")
    assert git
    root = tmp_path / object_format
    made = subprocess.run(  # noqa: S603 # -- a repository of the object format under test
        [git, "init", "-q", f"--object-format={object_format}", str(root)],
        capture_output=True,
        check=False,
    )
    if made.returncode != 0:
        pytest.skip(f"this git cannot make a {object_format} repository: {made.stderr!r}")
    return root


@pytest.mark.parametrize("via", VIAS)
@pytest.mark.parametrize("object_format", ["sha1", "sha256"])
def test_the_digest_is_the_id_the_shell_pipeline_printed_in_that_repository(
    object_format: str, via: str, tmp_path: Path
) -> None:
    root = repository_of(object_format, tmp_path)
    old = shell_pipeline(HEADING, BODY.encode(), cwd=root)
    assert len(old) == ID_WIDTH[object_format]
    assert printed(BODY, tmp_path, via, cwd=root) == f"{old}\n"


@pytest.mark.parametrize("via", VIAS)
def test_outside_any_repository_the_digest_is_the_id_the_shell_pipeline_printed(
    via: str, tmp_path: Path
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    probe = subprocess.run(  # noqa: S603 # -- the scratch directory is in no repository
        [shutil.which("git") or "git", "rev-parse", "--git-dir"],
        cwd=outside,
        capture_output=True,
        check=False,
    )
    assert probe.returncode != 0, "the scratch directory sits inside a repository"
    old = shell_pipeline(HEADING, BODY.encode(), cwd=outside)
    assert len(old) == 40
    assert printed(BODY, tmp_path, via, cwd=outside) == f"{old}\n"


def test_a_carriage_return_changes_nothing(tmp_path: Path) -> None:
    assert printed(BODY.replace("\n", "\r\n"), tmp_path) == printed(BODY, tmp_path)


def test_a_body_in_a_file_has_the_digest_of_the_same_body_on_stdin(tmp_path: Path) -> None:
    assert printed(BODY, tmp_path, "file") == printed(BODY, tmp_path, "stdin")


def test_a_changed_criterion_changes_the_digest_and_a_section_after_it_does_not(
    tmp_path: Path,
) -> None:
    written = printed(BODY, tmp_path)
    assert printed(BODY.replace("is refused", "is kept twice"), tmp_path) != written
    assert printed(BODY.replace("None", "#4"), tmp_path) == written
    assert printed(BODY + "\n## Conjecture\n- One list.\n", tmp_path) == written


@pytest.mark.parametrize("via", VIAS)
def test_a_body_without_the_heading_prints_no_digest_and_says_so(via: str, tmp_path: Path) -> None:
    body = BODY.replace(HEADING, "## Criteria")
    assert shell_pipeline(HEADING, body.encode()) == EMPTY_BLOB
    done = digest(body.encode(), via, tmp_path)
    assert done.returncode == NO_HEADING
    assert done.stdout == b""
    assert done.stderr.decode() == f"criteria digest: the body has no `{HEADING}` line\n"


def test_a_file_that_cannot_be_read_did_not_run(tmp_path: Path) -> None:
    done = subprocess.run(  # noqa: S603 # -- the script on a file that is not there
        [sys.executable, str(SCRIPT), HEADING, str(tmp_path / "absent.md")],
        capture_output=True,
        check=False,
    )
    assert done.returncode == DID_NOT_RUN
    assert done.stdout == b""
    assert "absent.md" in done.stderr.decode()


@pytest.mark.parametrize("via", VIAS)
def test_an_empty_body_is_a_body_that_was_not_read(via: str, tmp_path: Path) -> None:
    done = digest(b"", via, tmp_path)
    assert done.returncode == DID_NOT_RUN
    assert done.stdout == b""
    assert "empty" in done.stderr.decode()


@pytest.mark.parametrize("via", VIAS)
def test_without_git_the_script_did_not_run(via: str, tmp_path: Path) -> None:
    bare = tmp_path / "bin"
    bare.mkdir()
    done = digest(BODY.encode(), via, tmp_path, env={**os.environ, "PATH": str(bare)})
    assert done.returncode == DID_NOT_RUN
    assert done.stdout == b""
    assert "git" in done.stderr.decode()


@pytest.mark.parametrize("via", VIAS)
def test_a_git_that_fails_means_the_script_did_not_run(via: str, tmp_path: Path) -> None:
    bare = tmp_path / "bin"
    bare.mkdir()
    fake = bare / "git"
    fake.write_text("#!/bin/sh\necho 'fatal: git is broken here' >&2\nexit 3\n")
    fake.chmod(0o755)
    done = digest(BODY.encode(), via, tmp_path, env={**os.environ, "PATH": str(bare)})
    assert done.returncode == DID_NOT_RUN
    assert done.stdout == b""
    assert "git is broken here" in done.stderr.decode()
