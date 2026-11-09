"""The WSGI app."""

import json
import time

from pinboard.db import connect


def list_notes(conn):
    rows = conn.execute("SELECT id, body, pinned FROM notes ORDER BY pinned DESC, id DESC")
    return [{"id": i, "body": b, "pinned": bool(p)} for i, b, p in rows]


def add_note(conn, body):
    conn.execute("INSERT INTO notes (body, created_at) VALUES (?, ?)", (body, time.time()))
    conn.commit()


def application(environ, start_response):
    conn = connect()
    try:
        if environ["REQUEST_METHOD"] == "POST" and environ["PATH_INFO"] == "/notes":
            size = int(environ.get("CONTENT_LENGTH") or 0)
            add_note(conn, json.loads(environ["wsgi.input"].read(size))["body"])
            start_response("201 Created", [("Content-Type", "application/json")])
            return [b"{}"]
        start_response("200 OK", [("Content-Type", "application/json")])
        return [json.dumps(list_notes(conn)).encode()]
    finally:
        conn.close()
