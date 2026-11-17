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
    def __init__(self, root: Path, rev: str) -> None:
        self.root, self.rev = root, rev
        self.scratch: tempfile.TemporaryDirectory[str] | None = None
        self.runs = 0

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        trace: TracebackType | None,
    ) -> None:
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
