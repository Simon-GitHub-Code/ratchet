# 0002: Plain WSGI, no framework

Date: 2026-03-09

## Decision

The app is one WSGI callable with a hand-written router.

## Why

Six routes do not need a framework, and every dependency is one more thing to upgrade.
