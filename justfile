# The command contract of the ratchet plugin itself. The method says gates are gated; this is
# where that applies to the method.

set shell := ["bash", "-euo", "pipefail", "-c"]

# uv's default hard links give .venv files two names, and `claude plugin eval` refuses a plugin
# directory holding such a file.
export UV_LINK_MODE := "copy"

default:
    @just --list --unsorted

# The gates of each tier, and the one runner that runs a list of them at once: a tier takes as
# long as its slowest gate, not the sum, and one run names every red gate. No two of these write
# the same file or directory; a gate that did would be declared with `--serial`.
tier0_gates := "lint types validate parse-workflow parse-adapters"
tier1_gates := "test test-floor conformance-self-test lowered-smoke bite"

# How many jobs CI splits each of `test` and `test-floor` across: the one number, read by the
# workflow. A gate's ceiling is the wall clock of one job, so it holds per shard. `conftest.py` runs
# `SHARD` of `SHARDS`; a local run sets neither and is shard 1 of 1, the whole suite.
test_shards := "4"
shard := env_var_or_default("SHARD", "1")
shards := env_var_or_default("SHARDS", "1")
parallel := "python3 scripts/parallel.py --runner '" + just_executable() + "' --report .ratchet/timing.jsonl"

# Tier 0: shape.
tier0: sync
    {{parallel}} -- {{tier0_gates}}

# Tier 1: behaviour. `test` is the suite once, under coverage, which is the coverage gate too.
tier1: sync
    {{parallel}} -- {{tier1_gates}}

check: sync
    {{parallel}} -- {{tier0_gates}} {{tier1_gates}}

# The inner loop: the same gates, stopped at the first red.
check-fast: sync
    {{parallel}} --fail-fast -- {{tier0_gates}} {{tier1_gates}}

# One environment sync before the gates start, so no two `uv run` race to build it.
sync:
    uv sync --locked

lint:
    uv run ruff check hooks scripts evals/first-hour
    uv run ruff format --check hooks scripts evals/first-hour

# The first-hour driver needs the Agent SDK, which the dev environment does not install.
types:
    uv run mypy --strict hooks/ratchet_guards scripts
    uv run mypy --strict evals/first-hour/first_hour --exclude 'first_hour/drive\.py$'

validate:
    claude plugin validate .

# The Workflow runtime runs this script as the body of an async function, where its top-level
# `return` is legal; `node --check` reads a file as a module or script and rejects it on Node 24.
# scripts/parse-js.mjs compiles it the way the runtime does. Node 22.13 or later.
parse-workflow:
    node --no-warnings scripts/parse-js.mjs workflow workflows/pipeline.js

# Types are stripped explicitly: `node --check` does not strip them on Node 24. Node 22.13 or later.
parse-adapters:
    node --no-warnings scripts/parse-js.mjs adapter adapters/pi/ratchet-guard.ts adapters/opencode/ratchet-guard.ts

# The suite once, across every core and in random order, under coverage. The gate scripts, the
# guard hook and its two shims are absolute paths: every line and branch must run under test, and
# `fail_under = 100` in pyproject.toml makes this run red when one does not. It runs on the Python
# `.python-version` pins, whose `sys.monitoring` traces branches at a fraction of the older
# tracer's cost (ADR 0038). An idle worker takes queued tests from a busy one, and the ten slowest
# are named beside the budget line on every run. CI runs it as `SHARD` of `SHARDS` jobs; a local run
# is shard 1 of 1, the whole suite, and judges coverage itself.
test:
    uv run pytest -n auto --dist worksteal --durations=10 --cov {{cov_report}} hooks/tests evals/first-hour/tests
    {{cov_keep}}

# One shard gives no coverage verdict, since its slice alone cannot reach every line: it keeps its
# data (pytest-cov has already merged its workers') for `coverage-combine`, which holds the whole
# suite to `fail_under`.
cov_report := if shards == "1" { "--cov-report=term-missing:skip-covered" } else { "--cov-report= --cov-fail-under=0" }
cov_keep := if shards == "1" { "true" } else { "mv .coverage coverage-shard-" + shard + ".data" }

# The shards' coverage data combined and judged against `fail_under = 100`; red names the lines no
# shard ran. CI downloads each shard's `coverage-shard-N.data` into `coverage-shards/` first.
coverage-combine: sync
    uv run coverage combine coverage-shards/*.data
    uv run coverage report --skip-covered

# Every test runs in exactly one shard: the shards' collected sets must partition the whole
# collection, none left out and none run twice.
shard-check: sync
    #!/usr/bin/env bash
    set -euo pipefail
    dir="$(mktemp -d)"
    trap 'rm -rf "$dir"' EXIT
    collect() { uv run pytest --collect-only -p no:randomly hooks/tests evals/first-hour/tests | grep '::' | sort; }
    collect > "$dir/all"
    : > "$dir/union"
    for n in $(seq 1 {{test_shards}}); do
        SHARD=$n SHARDS={{test_shards}} collect > "$dir/$n"
        echo "shard $n: $(wc -l < "$dir/$n") tests"
        cat "$dir/$n" >> "$dir/union"
    done
    echo "whole suite: $(wc -l < "$dir/all") tests"
    sort "$dir/union" | diff "$dir/all" - > "$dir/diff" || { echo "the shards do not partition the suite (< left out, > run twice):"; cat "$dir/diff"; exit 1; }

# The suite again on Python 3.11, the oldest the hook and the gate scripts support (ADR 0007),
# without coverage and in an environment of its own.
test-floor:
    UV_PROJECT_ENVIRONMENT=.venv-floor uv run --frozen --python 3.11 pytest -n auto --dist worksteal -p no:cacheprovider hooks/tests

conformance-self-test:
    python3 scripts/conformance.py --self-test

# Every shipped gate against its synthetic violation; each must go red, and green without it.
bite:
    python3 scripts/bite.py

# The lowered gate on this repository's own thresholds: no ceiling raised against the merge base.
lowered-smoke:
    python3 scripts/lowered.py

# One unattended first-hour run of a scripted owner on a plugin checkout (evals/first-hour). It
# costs real money and up to two hours, so no tier runs it.
first-hour plugin dir *flags:
    uv run evals/first-hour/run.py run --plugin {{plugin}} --dir {{dir}} {{flags}}

# The first-hour run once for each persona that states a stack, one
# after another, and one table of them all in <dir>/matrix.md. About an hour or more a persona and
# real money each, so no tier runs it.
first-hour-matrix plugin dir *flags:
    uv run evals/first-hour/run.py matrix --plugin {{plugin}} --dir {{dir}} {{flags}}

# Time one gate against its ceiling in quality/thresholds.toml; CI wraps every matrix job in it.
budget key *flags:
    python3 scripts/budget.py {{key}} --report .ratchet/timing.jsonl {{flags}} -- just {{key}}
