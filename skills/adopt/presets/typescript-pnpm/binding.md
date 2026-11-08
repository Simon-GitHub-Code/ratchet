# typescript-pnpm preset: binding fragments

Adopt merges each section into `docs/process/binding.md`: the Runner paragraph replaces the template's placeholder, each row below fills the Tool cell of the Gates row with the same question, and the three closing sections replace their placeholders.

## Runner

`just`, with every tool run through `pnpm exec` so `pnpm-lock.yaml` pins it. `pnpm install --frozen-lockfile` installs the toolchain; the `justfile` is the command contract. The method's own gates and `diff-cover` run on `python3`, which the plugin needs anyway.

## Gates

| Question | Tool |
| --- | --- |
| format | `prettier --check` |
| lint, near-all rules | `eslint` with `typescript-eslint` `strictTypeChecked` and `--max-warnings 0`; each disable names its rule and a reason |
| strict types | `tsc --noEmit` with `strict`, `noUncheckedIndexedAccess`, `exactOptionalPropertyTypes`; `any` and the TypeScript ignore directives are banned by eslint |
| dependency direction | `dependency-cruiser` rules from the Layers table, plus the fitness test |
| dead code and dependency hygiene | `knip` |
| scripts and CI lint | `actionlint` and `shellcheck` |
| secrets | `gitleaks` over the tree and history |
| pins and least privilege | `ratchet` plugin `scripts/pins.py` plus `pnpm install --frozen-lockfile --offline` |
| size and complexity limits | eslint `complexity`, `max-lines`, `max-params`, `max-depth` with the Limits numbers, plus the fitness test |
| unit, property and fitness tests | `vitest` with `fast-check` (seeded) and `tests/fitness/` |
| coverage, absolute and ratcheted | `vitest --coverage` (v8) writing a Cobertura report, judged by `ratchet` plugin `scripts/coverage_groups.py` |
| diff coverage | `diff-cover` at 100% over the merge base |
| test count did not fall | `vitest list` at the merge base (a detached worktree) and at head |
| changed-code mutation | `stryker` over the changed modules, judged by `ratchet` plugin `scripts/mutation_survivors.py` against `quality/mutation-register.toml`: every live mutant killed or registered, no stale entry |
| contract drift | none: this stack generates no contract artefacts; ADR 0002 accepts the gap |
| shipped-dependency audit | `pnpm audit --prod --audit-level low` |
| duplication | `jscpd` at 8 lines, 1% ceiling |
| build | `pnpm build` |
| starts and answers | the application's health probe; bound per project |
| every checker bites | `ratchet` plugin `scripts/bite.py` plus `vitest tests/fitness -t bites` |
| public surface changed on purpose | `api-extractor` report under `api/`, diffed |
| old data still loads | none until the project persists data; ADR 0002 accepts the gap |
| documentation examples run | none: examples are type-checked only in docs builds; ADR 0002 accepts the gap |
| static security analysis | `semgrep` with `p/typescript` |
| artefact and image scan | `trivy fs` at HIGH and CRITICAL |
| type coverage, ratcheted | `type-coverage --strict` against `[typescript.types] coverage` |
| documentation coverage, ratcheted | none: no tool fits yet; ADR 0002 accepts the gap |
| full mutation | `stryker run` over the absolute paths, judged by `ratchet` plugin `scripts/mutation_survivors.py`; the score is the trend |
| deep interface conformance | none until the project exposes a stateful interface; ADR 0002 accepts the gap |
| symbolic checking | none: no symbolic checker fits this stack; ADR 0002 accepts the gap |
| fuzzing | `fast-check` under the `fuzz` vitest project for `FUZZ_SECONDS` |
| end to end | `playwright test` |
| suite order-independent and repeatable | `vitest --sequence.shuffle` three times, no retries |
| reproducible build | `pnpm build` twice, `sha256sum` over `dist/` compared |
| race and memory detectors | none: the JavaScript runtime has none to run; ADR 0002 accepts the gap |
| performance budget | `vitest bench` against the Limits |
| dynamic security scan | bound per project when it serves HTTP |
| accessibility | `axe` through the `a11y` playwright project |
| formal verification | the project-local verification skill, when `pstack` has generated one |

## Absolute paths

`src/core/**` and `tests/fitness/**`: 100% line and branch coverage, no coverage-ignore comment, no coverage exclude that reaches them. The `core` coverage group in `quality/thresholds.toml` is `absolute = true`.

## Limits

Cyclomatic complexity 10 (`complexity`); 400 lines per file (`max-lines`), 4 parameters (`max-params`), nesting depth 3 (`max-depth`), checked by eslint and the fitness test. Change size: `[ceilings.change]`, 40 files and 1500 lines until the first measured run lowers it.

## Tool versions

`pnpm-lock.yaml` pins every Node tool and `--frozen-lockfile` fails when it drifts from `package.json`; `packageManager` in `package.json` pins pnpm itself; `uvx` tools (`actionlint` from `actionlint-py`, `semgrep`, `diff-cover`) are pinned by version in the `justfile` once the first run names them; `gitleaks`, `trivy` and `shellcheck` are not Python packages, so they are binaries on `PATH` at the version named here, and a CI job that runs one installs it by pinned version first; GitHub actions by commit in the workflows.
