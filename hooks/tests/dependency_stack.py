"""A made-up stack for the dependency contract: files, a binding and an entry point, no real tool.

The manifest is ``app.manifest``: a ``use <table> <name> <spec>`` line is one dependency, a spec
that starts with ``@`` is fetched some other way, an ``own-version`` line is the package's own, and
any other line is settings. The lockfile is ``app.lock``: a ``pin`` line is one resolved version in
the order the contract's ``locked`` record takes, any other line is settings. The entry point is a
script that prints the contract's records for the files it is given. Nothing here names a language,
a package manager or a format a real tool writes, so a pass shows the plugin knows none.
"""

import subprocess
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path

BINDING = """\
# Binding

- Application types: service, library
- Life events: deployed

## Dependencies

What guard-diff reads.

- Manifests: `*.manifest`
- Lockfiles: `*.lock`
- Entry point: `just dependencies`
- Registries: `https://registry.example`
"""
READER = """\
import hashlib
import sys
from pathlib import Path

root = Path(sys.argv[1])
for path in sorted(root.rglob("*")):
    if not path.is_file():
        continue
    name = path.relative_to(root).as_posix()
    rest = []
    for line in path.read_text(encoding="utf-8").splitlines():
        words = line.split()
        if name.endswith(".manifest") and words[:1] == ["use"]:
            _, table, dependency, spec = words
            origin = "other" if spec.startswith("@") else "registry"
            print("declared", name, table, dependency, spec, origin, sep="\\t")
        elif name.endswith(".lock") and words[:1] == ["pin"]:
            _, dependency, version, order, origin, source, integrity, sides = words
            fields = ("locked", name, "main", dependency, version, order, origin, source)
            print(*fields, "" if integrity == "-" else integrity, sides, sep="\\t")
        elif words[:1] != ["own-version"]:
            rest.append(line)
    print("rest", name, hashlib.sha256("\\n".join(rest).encode()).hexdigest(), sep="\\t")
"""
JUSTFILE = "dependencies dir:\n    python3 tools/read_deps.py {{dir}}\n"
REGISTRY = "https://registry.example"
MANIFEST = "own-version 1.0\nsetting strict on\nuse main alpha ^1.0\nuse dev gamma >=3\n"


def pin(
    name: str, version: str, *, sum_: str | None = None, source: str = REGISTRY, sides: str = "one"
) -> str:
    """One lock line for a registry version; its integrity is ``sum-<name><version>`` by default."""
    parts = version.split(".")
    while len(parts) > 1 and parts[-1] == "0":
        parts.pop()
    integrity = sum_ or f"sum-{name}{version}"
    return f"pin {name} {version} {'.'.join(parts)} registry {source} {integrity} {sides}\n"


LOCK = "setting frozen yes\n" + pin("alpha", "1.2.0") + pin("gamma", "3.0.0")
FILES = {
    "docs/process/binding.md": BINDING,
    "justfile": JUSTFILE,
    "tools/read_deps.py": READER,
    "app.manifest": MANIFEST,
    "app.lock": LOCK,
}


def read(files: Mapping[str, str]) -> str:
    """What the entry point prints for ``files`` written under a directory by path."""
    with tempfile.TemporaryDirectory(prefix="ratchet-reader-") as scratch:
        for path, text in files.items():
            target = Path(scratch) / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        done = subprocess.run(  # noqa: S603 # -- this interpreter, on the made-up reader above
            [sys.executable, "-c", READER, scratch], capture_output=True, check=True, text=True
        )
    return done.stdout
