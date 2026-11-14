"""Run a project's dependency entry point as the merge base has it (ADR 0042).

The binding's ``## Dependencies`` section names ``just dependencies`` as the recipe that reads
manifests and lockfiles. It runs from a copy of the merge base's tree, never from the change under
judgement, so the change cannot rewrite the code that judges its own files. It is given a directory
holding the files to read, and is bounded: a time limit, a size limit on what it prints, and no
standard input. Whatever goes wrong is a refusal, never a pass.

Only ``guard-diff`` and the merge judge call it; the hook that runs before every tool call does
not (ADR 0011).
"""

import shutil
import subprocess
import tempfile
from collections.abc import Mapping
from pathlib import Path
from types import TracebackType
from typing import NoReturn, Self

from ratchet_guards.dependencies import NotDependencyOnlyError

RECIPE = "dependencies"
TIMEOUT = 60
LIMIT = 8_000_000


def _fail(message: str) -> NoReturn:
    raise NotDependencyOnlyError(message)


class Entry:
    """The entry point of the tree at ``rev`` in ``root``, made ready on its first call."""

    def __init__(self, root: Path, rev: str) -> None:
        """Remember where the tree comes from; nothing is extracted until a call needs it."""
        self.root, self.rev = root, rev
        self.scratch: tempfile.TemporaryDirectory[str] | None = None
        self.runs = 0

    def __enter__(self) -> Self:
        """Use the entry as a block that cleans its copy up."""
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        trace: TracebackType | None,
    ) -> None:
        """Remove the copy of the tree and the files it was given."""
        if self.scratch is not None:
            self.scratch.cleanup()

    def _tree(self) -> tuple[Path, Path]:
        if self.scratch is None:
            self.scratch = tempfile.TemporaryDirectory(prefix="ratchet-dependencies-")
            tree = Path(self.scratch.name) / "tree"
            tree.mkdir()
            archive = subprocess.run(  # noqa: S603 # -- a fixed argv, no shell
                ["git", "-C", str(self.root), "archive", "--format=tar", self.rev],  # noqa: S607 # -- git is resolved from PATH on purpose
                capture_output=True,
                check=False,
            )
            unpacked = subprocess.run(  # noqa: S603 # -- a fixed argv, no shell
                ["tar", "-x", "-C", str(tree)],  # noqa: S607 # -- tar is resolved from PATH on purpose
                input=archive.stdout,
                capture_output=True,
                check=False,
            )
            if archive.returncode or unpacked.returncode:
                _fail(f"the merge base's tree could not be copied to run {RECIPE} from")
        return Path(self.scratch.name) / "tree", Path(self.scratch.name)

    def __call__(self, files: Mapping[str, str]) -> str:
        """What ``just dependencies <dir>`` prints for ``files`` written under ``dir`` by path."""
        just = shutil.which("just")
        if just is None:
            _fail("just is not on the path, so the entry point cannot run")
        tree, scratch = self._tree()
        self.runs += 1
        directory = scratch / f"files-{self.runs}"
        for path, text in files.items():
            target = directory / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        try:
            done = subprocess.run(  # noqa: S603 # -- just from the path, and a recipe name
                [just, RECIPE, str(directory)],
                cwd=tree,
                capture_output=True,
                stdin=subprocess.DEVNULL,
                timeout=TIMEOUT,
                check=False,
            )
        except subprocess.TimeoutExpired:
            _fail(f"the entry point ran over {TIMEOUT} seconds")
        if done.returncode:
            _fail(f"the entry point exited {done.returncode}")
        if len(done.stdout) > LIMIT:
            _fail("the entry point printed more than it may")
        try:
            return done.stdout.decode()
        except UnicodeDecodeError:
            _fail("the entry point printed what is not text")
