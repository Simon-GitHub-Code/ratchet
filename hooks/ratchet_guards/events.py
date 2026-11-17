import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

LOG_NAME = "ratchet-guard-events.jsonl"
PLUGIN_RECORD = "ratchet-plugin"
FEATURE_STARTS = "ratchet-feature-starts"
LIMIT = 4096
CLIP = 64
_FLAGS = os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK


@dataclass(frozen=True, slots=True)
class Event:
    at: str
    decision: str
    tool: str | None
    rule: str
    detail: str
    branch: str | None

    def line(self) -> bytes:
        fields = asdict(self)
        text = json.dumps(fields)
        if len(text) >= LIMIT:
            cut = {key: v[:CLIP] if isinstance(v, str) else v for key, v in fields.items()}
            text = json.dumps({**cut, "truncated": True})
        return (text + "\n").encode()


def append(log: Path, line: bytes) -> None:
    descriptor = os.open(log, _FLAGS, 0o600)
    try:
        os.write(descriptor, line)
    finally:
        os.close(descriptor)


def replace(record: Path, text: str) -> None:
    descriptor, name = tempfile.mkstemp(dir=record.parent, prefix=f".{record.name}.")
    try:
        try:
            pending = memoryview(text.encode())
            while pending:
                pending = pending[os.write(descriptor, pending) :]
        finally:
            os.close(descriptor)
        Path(name).replace(record)
    except OSError:
        Path(name).unlink(missing_ok=True)
        raise
