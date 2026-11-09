# 0001: SQLite for storage

Date: 2026-03-02

## Decision

Notes and pins live in one SQLite database file, opened in WAL mode.

## Why

The team is four people, the load is small, and we are not allowed new infrastructure services. SQLite needs no server and backs up as a file copy.
