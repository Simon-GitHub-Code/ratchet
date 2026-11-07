# The command contract of the ratchet plugin itself. The method says gates are gated; this is
# where that applies to the method.

set shell := ["bash", "-euo", "pipefail", "-c"]

default:
    @just --list --unsorted

# Tier 0: shape.
tier0: lint types validate parse-workflow

# Tier 1: behaviour.
tier1: test conformance-self-test lowered-smoke

check: tier0 tier1

lint:
    uv run ruff check hooks scripts
    uv run ruff format --check hooks scripts

types:
    uv run mypy --strict hooks/ratchet_guards scripts

validate:
    claude plugin validate .

parse-workflow:
    node --check workflows/feature.js

test:
    uv run pytest -q hooks/tests

conformance-self-test:
    python3 scripts/conformance.py --self-test

# The lowered gate against this repository's own (absent) thresholds file must report the first-adoption case cleanly.
lowered-smoke:
    python3 scripts/lowered.py --root skills/adopt/templates --base HEAD || true
