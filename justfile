set shell := ["bash", "-euo", "pipefail", "-c"]

export UV_LINK_MODE := "copy"

default:
    @just --list --unsorted

tier0_gates := "lint types validate parse-workflow parse-adapters"
tier1_gates := "test test-floor conformance-self-test lowered-smoke bite"

test_shards := "32"
shard := env_var_or_default("SHARD", "1")
shards := env_var_or_default("SHARDS", "1")
parallel := "python3 scripts/parallel.py --runner '" + just_executable() + "' --report .ratchet/timing.jsonl --wide test test-floor"

configured_jobs := `git config --get ratchet.jobs 2>/dev/null || true`
workers := env_var_or_default("RATCHET_JOBS", if configured_jobs == "" { "auto" } else { configured_jobs })

tier0: sync
    {{parallel}} -- {{tier0_gates}}

tier1: sync
    {{parallel}} -- {{tier1_gates}}

check: sync
    {{parallel}} -- {{tier0_gates}} {{tier1_gates}}

check-fast: sync
    {{parallel}} --fail-fast -- {{tier0_gates}} {{tier1_gates}}

sync:
    uv sync --locked

lint:
    uv run ruff check hooks scripts evals/first-hour
    uv run ruff format --check hooks scripts evals/first-hour

types:
    uv run mypy --strict hooks/ratchet_guards scripts
    uv run mypy --strict evals/first-hour/first_hour --exclude 'first_hour/drive\.py$'

validate:
    claude plugin validate .

parse-workflow:
    node --no-warnings scripts/parse-js.mjs workflow workflows/pipeline.js
    python3 -c 'import ast, pathlib; compile(pathlib.Path("workflows/pipeline.py").read_text(encoding="utf-8"), "workflows/pipeline.py", "exec", ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)'

parse-adapters:
    node --no-warnings scripts/parse-js.mjs adapter adapters/pi/ratchet-guard.ts adapters/opencode/ratchet-guard.ts

test:
    uv run pytest -n {{workers}} --dist worksteal --durations=10 --cov {{cov_report}} hooks/tests evals/first-hour/tests
    {{cov_keep}}

cov_report := if shards == "1" { "--cov-report=term-missing:skip-covered" } else { "--cov-report= --cov-fail-under=0" }
cov_keep := if shards == "1" { "true" } else { "mv .coverage coverage-shard-" + shard + ".data" }

coverage-combine: sync
    uv run coverage combine coverage-shards/*.data
    uv run coverage report --skip-covered

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

test-floor:
    UV_PROJECT_ENVIRONMENT=.venv-floor uv run --frozen --python 3.11 pytest -n {{workers}} --dist worksteal -p no:cacheprovider hooks/tests

conformance-self-test:
    python3 scripts/conformance.py --self-test

bite:
    python3 scripts/bite.py

lowered-smoke:
    python3 scripts/lowered.py

first-hour plugin dir *flags:
    uv run evals/first-hour/run.py run --plugin {{plugin}} --dir {{dir}} {{flags}}

first-hour-matrix plugin dir *flags:
    uv run evals/first-hour/run.py matrix --plugin {{plugin}} --dir {{dir}} {{flags}}

budget key *flags:
    cmd=(python3 scripts/budget.py {{key}} --report .ratchet/timing.jsonl {{flags}}); case " {{flags}} " in *" --set "*|*" --set="*|*" -- "*) ;; *) cmd+=(-- just {{key}}) ;; esac; "${cmd[@]}"
