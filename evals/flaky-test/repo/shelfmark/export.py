"""The JSON export the browser extension imports."""

import time
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Bookmark:
    url: str
    title: str
    tags: tuple[str, ...] = field(default_factory=tuple)


def export_tags(bookmarks: list[Bookmark]) -> list[str]:
    """Every tag used by any bookmark, once each, in alphabetical order."""
    return list({tag for bookmark in bookmarks for tag in bookmark.tags})


def export(bookmarks: list[Bookmark]) -> dict[str, object]:
    return {
        "generated_at": time.time(),
        "tags": export_tags(bookmarks),
        "bookmarks": [
            {"url": b.url, "title": b.title, "tags": list(b.tags)} for b in bookmarks
        ],
    }
