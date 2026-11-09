# Does the stance change how the agent works?

This suite measures one claim: the epistemology that the Ratchet method injects (the `## The stance` section of `skills/method/SKILL.md`, put in context by the SessionStart and SubagentStart hook, plus the `ratchet:method` skill it tells the agent to load) improves how an agent frames and builds software. It compares a with-plugin arm against a no-plugin arm on the same repositories.

## The cases

Each case scaffolds a small Python repository that is on the method: its `AGENTS.md` names `ratchet:method`, and it has a minimal `docs/process/binding.md` and a `justfile` (`_common/`). The without arm sees the same repository with no plugin. That is the realistic counterfactual: a project that adopted the method, opened by an agent that doesn't have the plugin.

| Case | Task | Trait under test | Task-success graders |
| --- | --- | --- | --- |
| `misdirected-bug` | The reporter blames `format_money`. The real cause is float truncation in `invoice.py`. | Eliminates the reporter's suspect with a check, claims no more than it checked, and names where its fix could be wrong. | The formatter is unchanged, the truncation is gone, and `hidden/check.py` passes (run offline). |
| `ready-to-merge` | "Is it ready to merge?" on a green limiter. Two spec items are untested, and the code gets one of them wrong. | Reports the green run by what it attempted and what it left out, and asks whether an existing test could fail at all. | Not-ready verdict that names the window-edge defect. The spec was read. |
| `flaky-test` | The reporter blames timing for an intermittent test. The cause is set order under per-process hash randomisation. | Gives a mechanism, not "timing", and claims no more than it checked. | Code sorts the tags, no retry is added, and `hidden/check.py` passes under 24 hash seeds (run offline). |
| `design-choice` | Session store: an in-process dict (a colleague's preference) or a SQLite table. | Decides by the criticism that kills each option, and names an observable sign that the choice is failing. | A decision record is written and it chooses SQLite. |

Every case also carries two with-only indicators that the harness excludes from the score. `stance-in-context` checks that the hook's stance is in the run's trace. `method-skill-loaded` checks that the agent loaded `ratchet:method`.

## Rerun it

```
claude plugin eval . --ablation with-without --scaffold --runs 10 --model sonnet \
  --judge-model sonnet --allow-tools Edit Write --trust-plugin --keep-temp \
  --no-publish --max-cost-usd 40 --threshold 0 -j 4 --json /tmp/epistemology.json
python3 evals/hidden.py /tmp/epistemology.json --chmod
```

`hidden.py` reads the run's JSON. For each arm it confirms from the trace that the stance reached the context, runs the hidden acceptance tests on the workspaces the agents left behind (`--keep-temp` keeps them), and prints the score delta with a bootstrap interval and Fisher's exact test per grader. Results go to `evals/results/`, which git ignores.

Bash is not granted. On the machine that produced these numbers, the harness sandbox cannot start a shell: AppArmor's `kernel.apparmor_restrict_unprivileged_userns=1` blocks the nested user namespace its seccomp step needs. The agents can read and edit but cannot execute anything. Two graders exploit that on purpose (`claims-match-checks`): any claim that tests pass is unsupported. On a machine where the sandbox works, adding `Bash` to `allowed_tools` and `--allow-tools` measures a different, richer task. Compare like with like.

## Last run

Run on 2026-11-08 with Claude Code 2.1.319: sonnet agents, a sonnet judge (3 votes per grader, 2 must pass), 10 runs per case per arm, $11.84. Every one of the 40 with-arm traces carries the hook's `hook_response` with the stance, and none of the 40 without-arm traces does. All 40 with-arm runs loaded `ratchet:method`.

| Case | With (mean, sd) | Without (mean, sd) | Delta, 95% bootstrap CI |
| --- | --- | --- | --- |
| misdirected-bug | 0.88 (0.14) | 0.64 (0.08) | +0.24 (+0.14 to +0.34) |
| ready-to-merge | 0.80 (0.11) | 0.72 (0.08) | +0.08 (0.00 to +0.15) |
| flaky-test | 0.88 (0.10) | 0.78 (0.11) | +0.10 (+0.02 to +0.18) |
| design-choice | 1.00 (0.00) | 0.78 (0.08) | +0.23 (+0.18 to +0.25) |
| pooled | | | +0.16 (+0.11 to +0.21) |

Pass counts per grader, with against without, n=10 each. The p-value is Fisher's exact test, uncorrected across 21 graders.

| Grader | With | Without | p | Kind |
| --- | --- | --- | --- | --- |
| every task-success grader, the offline hidden tests included | 10/10 | 10/10 | 1.0 | deterministic or verdict |
| design-choice `refuter-stated` | 10 | 1 | <0.001 | reasoning, tightened after the pilot |
| misdirected-bug `names-own-weak-spot` | 9 | 0 | <0.001 | reasoning, added after the pilot |
| misdirected-bug `suspect-eliminated` | 5 | 2 | 0.35 | reasoning |
| flaky-test `names-own-weak-spot` | 4 | 1 | 0.30 | reasoning, added after the pilot |
| flaky-test `claims-match-checks` | 10 | 8 | 0.47 | reasoning |
| ready-to-merge `tests-could-fail` | 2 | 0 | 0.47 | reasoning, added after the pilot |
| ready-to-merge `green-reported-by-attempts` | 10 | 9 | 1.0 | reasoning |
| `mechanism-named`, `decided-by-elimination`, misdirected `claims-match-checks` | 10 | 10 | 1.0 | reasoning |

What it shows: the stance changed no outcome. Both arms fixed both bugs (hidden tests 10/10 each), refused the merge for the right reason, and chose SQLite. Both arms also already did the core moves without the plugin: they ruled out the reporter's suspect, named the hash-seed mechanism, decided by elimination, and admitted they had run nothing. The whole delta comes from two reporting moves that the stance states almost word for word. The with-arm ends a decision record with "What would show this decision wrong" and observable signals (`database is locked`, p95 latency); the without-arm writes "revisit if load grows". The with-arm says where it would look first if its fix is wrong; the without-arm offers to re-check old invoices. With-arm runs cost 30% to 90% more per run ($0.15 to $0.21 against $0.09 to $0.15), because the method skill loads into every session.

What limits the number:
- The graders that move were written or tightened after a one-run pilot showed both arms at ceiling. They target stance bullets, but their exact form was chosen after I had read with-arm text. The graders written before the pilot show only small differences, none significant.
- Every reasoning grader is an LLM judge from the same model family as the agent. Votes were nearly always unanimous, but a hand audit found at least one wrong unanimous verdict: `suspect-eliminated` failed a without-arm reply that gave the reason ("it splits pence into pounds and pence and formats them exactly").
- There are four cases, small and single-file, at n=10. Ceiling effects hide any gain in diagnosis or decision quality. A harder task, where the baseline gets the cause wrong, is the experiment this suite does not run.
- Without Bash, "eliminate it with a check" can only mean checking by reading. The traits that need execution (reproducing a flake, watching a test go red) are not measured.
