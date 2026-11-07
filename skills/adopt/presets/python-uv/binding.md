# python-uv preset: binding fragments

Adopt merges each section into `docs/process/binding.md`: the Runner paragraph replaces the template's placeholder, each row below fills the Tool cell of the Gates row with the same question, and the three closing sections replace their placeholders.

## Runner

`just`, with every tool run through `uv run` so `uv.lock` pins it. `uv sync --group dev` installs the toolchain; the `justfile` is the command contract.

## Gates

| Question | Tool |
| --- | --- |
| format | `ruff format --check` |
| lint, near-all rules | `ruff check` with `select = ["ALL"]`; each `noqa` names its rule and a reason |
| strict types | `mypy --strict` with `disallow_any_explicit`; `Any` and `type: ignore` without a code are banned |
| dependency direction | `import-linter` contracts from the Layers table, plus the fitness test |
| dead code and dependency hygiene | `vulture` (confidence 80) and `deptry` |
| scripts and CI lint | `actionlint` and `shellcheck` |
| secrets | `gitleaks` over the tree and history |
| pins and least privilege | `ratchet` plugin `scripts/pins.py` plus `uv lock --check` |
| size and complexity limits | ruff `C901`, `PLR0912`, `PLR0913`, `PLR0915`, `PLR0917` with the Limits numbers, plus the fitness test |
| unit, property and fitness tests | `pytest` with `hypothesis` (seeded profiles) and `tests/fitness/` |
| coverage, absolute and ratcheted | `pytest-cov` writing `coverage.xml`, judged by `ratchet` plugin `scripts/coverage_groups.py` |
| diff coverage | `diff-cover` at 100% over the merge base |
| test count did not fall | `pytest --collect-only` at the merge base (a detached worktree) and at head |
| changed-code mutation | `mutmut` over the changed modules, judged by `ratchet` plugin `scripts/mutation_survivors.py` against `quality/mutation-register.toml`: every live mutant killed or registered, no stale entry |
| contract drift | none: this stack generates no contract artefacts; ADR 0002 accepts the gap |
| shipped-dependency audit | `pip-audit --strict` |
| duplication | `pylint` `duplicate-code` at 8 lines |
| build | `uv build` |
| starts and answers | the application's health probe; bound per project |
| every checker bites | `ratchet` plugin `scripts/bite.py` plus `tests/fitness -k bites` |
| public surface changed on purpose | `griffe check` against the merge base |
| old data still loads | none until the project persists data; ADR 0002 accepts the gap |
| documentation examples run | `pytest --doctest-modules src` |
| static security analysis | `bandit -r src` |
| artefact and image scan | `trivy fs` at HIGH and CRITICAL |
| type coverage, ratcheted | none: `mypy --strict` leaves nothing untyped to measure; ADR 0002 accepts the gap |
| documentation coverage, ratcheted | `interrogate` against `[python.docs] coverage` |
| full mutation | `mutmut run` over the absolute paths, judged by `ratchet` plugin `scripts/mutation_survivors.py`; the score is the trend |
| deep interface conformance | none until the project exposes a stateful interface; ADR 0002 accepts the gap |
| symbolic checking | `crosshair check src` over the contracts in docstrings |
| fuzzing | `hypothesis` under the `fuzz` profile for `FUZZ_SECONDS` |
| end to end | bound per project |
| suite order-independent and repeatable | `pytest-randomly` three times with fresh seeds |
| reproducible build | `uv build` twice, `sha256sum` compared |
| race and memory detectors | none: pure Python; ADR 0002 accepts the gap |
| performance budget | `pytest-benchmark` with a 10% regression ceiling |
| dynamic security scan | bound per project when it serves HTTP |
| accessibility | bound per project when it has a user interface |
| formal verification | the project-local verification skill, when `pstack` has generated one |

## Absolute paths

`src/<package>/core/**` and `tests/fitness/**`: 100% line and branch coverage, no `pragma: no cover`, no coverage omit that reaches them. The `core` coverage group in `quality/thresholds.toml` is `absolute = true`.

## Limits

McCabe complexity 10 (`C901`); 12 branches (`PLR0912`), 5 arguments (`PLR0913`), 3 positional (`PLR0917`), 40 statements (`PLR0915`) per function; 400 lines per module, checked by the fitness test. Change size: `[ceilings.change]`, 40 files and 1500 lines until the first measured run lowers it.

## Tool versions

`uv.lock` pins every Python tool, `uv lock --check` fails when it drifts from `pyproject.toml`; `uvx` tools (`actionlint`, `gitleaks`, `trivy`, `cyclonedx-py`) are pinned by version in the `justfile` once the first run names them; GitHub actions by commit in the workflows.
