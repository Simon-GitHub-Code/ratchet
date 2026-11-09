"""Score a finished `claude plugin eval --keep-temp --json` run beyond what the harness grades.

Per case and arm: the harness score's mean and spread, whether the session-start hook's stance
reached each run's context (read from the trace, so the treatment is shown, not assumed), the
with-only indicators, and, where the case has hidden/check.py, whether that hidden acceptance
test passes on the workspace the agent left (copied out of the kept sandbox first). Then the
with-minus-without delta with a bootstrap interval, and Fisher's exact test per grader.

Usage: python3 evals/hidden.py <aggregate.json> [--chmod]
--chmod opens each kept sandbox's sealed/ directory (mode 000) so the workspace can be read.
"""

import json
import math
import random
import shutil
import statistics
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

EVALS = Path(__file__).resolve().parent
STANCE = "Work as a critical rationalist"


@dataclass
class Arm:
    scores: list[float] = field(default_factory=list)
    costs: list[float] = field(default_factory=list)
    stance: list[bool] = field(default_factory=list)
    passes: dict[str, list[bool]] = field(default_factory=dict)
    indicators: dict[str, list[bool]] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


def stance_reached(trace: Path) -> bool:
    for line in trace.read_text(encoding="utf-8").splitlines():
        event = json.loads(line)
        if event.get("subtype") == "hook_response" and STANCE in str(event.get("output", "")):
            return True
    return False


def hidden_passes(case: str, kept: Path, chmod: bool) -> bool | None:
    sealed = kept / "sealed"
    if chmod:
        sealed.chmod(0o700)
    check = EVALS / case / "hidden" / "check.py"
    if not check.is_file():
        return None
    workspace = sealed / "home" / "cwd"
    with tempfile.TemporaryDirectory() as scratch:
        copy = Path(scratch) / "ws"
        shutil.copytree(workspace, copy, ignore=shutil.ignore_patterns(".git", "__pycache__"))
        done = subprocess.run(
            [sys.executable, "-B", str(check), str(copy)],
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
    return done.returncode == 0


def spread(values: list[float]) -> str:
    if not values:
        return "n/a"
    sd = statistics.stdev(values) if len(values) > 1 else 0.0
    return f"{statistics.mean(values):.2f} (sd {sd:.2f}, n {len(values)})"


def fisher_two_sided(a: int, n: int, b: int, m: int) -> float:
    """Fisher's exact test on a/n passes against b/m passes."""
    total = a + b

    def prob(x: int) -> float:
        return math.comb(n, x) * math.comb(m, total - x) / math.comb(n + m, total)

    observed = prob(a)
    support = range(max(0, total - m), min(n, total) + 1)
    return min(1.0, sum(p for x in support if (p := prob(x)) <= observed * (1 + 1e-9)))


def bootstrap_delta(with_: list[float], without: list[float]) -> str:
    rng = random.Random(0)
    deltas = sorted(
        statistics.mean(rng.choices(with_, k=len(with_)))
        - statistics.mean(rng.choices(without, k=len(without)))
        for _ in range(4000)
    )
    delta = statistics.mean(with_) - statistics.mean(without)
    return f"{delta:+.3f} (95% bootstrap CI {deltas[100]:+.3f} to {deltas[3899]:+.3f})"


def collect(case: dict, chmod: bool) -> dict[str, Arm]:
    arms: dict[str, Arm] = {}
    for arm, runs in case["arms"].items():
        data = arms[arm] = Arm()
        for run in runs:
            if run.get("error"):
                data.errors.append(run["error"][:160])
                continue
            trace = Path(run["tracePath"])
            data.scores.append(run["score"])
            data.costs.append(run.get("costUsd", 0))
            data.stance.append(stance_reached(trace))
            for g in run["graders"]:
                if g.get("scored", True):
                    data.passes.setdefault(g["name"], []).append(g["passed"])
                else:
                    data.indicators.setdefault(g["name"], []).append(g["passed"])
            verdict = hidden_passes(case["name"], trace.parent.parent, chmod)
            if verdict is not None:
                data.passes.setdefault("hidden-test (offline)", []).append(verdict)
    return arms


def report(name: str, arms: dict[str, Arm]) -> None:
    print(f"\n## {name}")
    for arm, data in arms.items():
        print(
            f"  {arm}: score {spread(data.scores)}; stance in context "
            f"{sum(data.stance)}/{len(data.stance)}; agent cost/run {spread(data.costs)}"
        )
        for error in data.errors:
            print(f"    run error: {error}")
        for grader, flags in data.indicators.items():
            print(f"    indicator {grader}: {sum(flags)}/{len(flags)}")
    with_, without = arms.get("with"), arms.get("without")
    if not (with_ and without and with_.scores and without.scores):
        return
    print(f"  delta (with - without): {bootstrap_delta(with_.scores, without.scores)}")
    for grader, flags in with_.passes.items():
        other = without.passes.get(grader, [])
        p = fisher_two_sided(sum(flags), len(flags), sum(other), len(other))
        print(
            f"    {grader}: with {sum(flags)}/{len(flags)}, without "
            f"{sum(other)}/{len(other)}, Fisher p={p:.3f}"
        )


def main() -> int:
    result = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    chmod = "--chmod" in sys.argv
    print(f"suite cost ${result.get('costUsd', 0):.2f}")
    pooled: dict[str, list[float]] = {"with": [], "without": []}
    for case in result["cases"]:
        arms = collect(case, chmod)
        report(case["name"], arms)
        for arm, data in arms.items():
            pooled.setdefault(arm, []).extend(data.scores)
    if pooled["with"] and pooled["without"]:
        print(f"\n## pooled\n  delta: {bootstrap_delta(pooled['with'], pooled['without'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
