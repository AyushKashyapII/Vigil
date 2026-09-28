"""Verifies a proposed fix by benchmarking it in a disposable sandbox:
run a representative query before the fix, apply the fix, run it again,
compare -- "prove it, don't trust it," per the root README.

Only possible_missing_index is supported so far. possible_unused_index
needs a genuinely different benchmark (write performance, since dropping
an index helps INSERT/UPDATE/DELETE, not reads) -- next increment, not
built yet. Query-rewrite findings (nested_subquery, possible_n_plus_one,
possible_unbounded_query) don't need a different sandbox, just a
benchmark-query builder for their shape -- also not built yet.
"""

import json
import re
import subprocess
from dataclasses import dataclass

from vigil_brain.fixes.index import FixSuggestion
from vigil_brain.parser.store import Finding
from vigil_brain.sandbox.postgres_sandbox import Sandbox, env_for, pg_args

BENCHMARK_RUNS = 3
# At least this much faster to call it a real improvement, not noise.
HELPED_THRESHOLD = 0.8  # after_ms must be <= 80% of before_ms (20%+ faster)

_MISSING_INDEX_SUBJECT_RE = re.compile(r"^table=([^.]+)\.(\S+) column=(\S+)$")


@dataclass
class VerificationResult:
    finding_id: int
    before_ms: float
    after_ms: float
    helped: bool

    @property
    def improvement_pct(self) -> float:
        if self.before_ms == 0:
            return 0.0
        return (self.before_ms - self.after_ms) / self.before_ms * 100


def _benchmark_query(finding: Finding) -> str | None:
    """Builds a representative query to benchmark. Returns None for rule
    types not supported yet, rather than guessing.
    """
    if finding.rule == "possible_missing_index":
        match = _MISSING_INDEX_SUBJECT_RE.match(finding.subject)
        if not match:
            return None
        schema, table, column = match.groups()
        return (
            f"SELECT * FROM {schema}.{table} "
            f"WHERE {column} = (SELECT {column} FROM {schema}.{table} LIMIT 1)"
        )
    return None


def _measure_ms(sb: Sandbox, query: str) -> float:
    """Runs EXPLAIN ANALYZE BENCHMARK_RUNS times, returns the median
    execution time in milliseconds. Multiple runs because a single
    EXPLAIN ANALYZE can be noisy (cache effects).
    """
    times = []
    for _ in range(BENCHMARK_RUNS):
        result = subprocess.run(
            [
                "psql", *pg_args(sb.conn), "-d", sb.name,
                "-t", "-A", "-c", f"EXPLAIN (ANALYZE, FORMAT JSON) {query}",
            ],
            env=env_for(sb.conn),
            capture_output=True, text=True, check=True,
        )
        plan = json.loads(result.stdout)
        times.append(plan[0]["Execution Time"])
    times.sort()
    return times[len(times) // 2]


def _apply_fix(sb: Sandbox, fix: FixSuggestion) -> None:
    subprocess.run(
        ["psql", *pg_args(sb.conn), "-d", sb.name, "-c", fix.sql],
        env=env_for(sb.conn),
        capture_output=True, text=True, check=True,
    )


def verify_fix(finding: Finding, fix: FixSuggestion) -> VerificationResult | None:
    """Benchmarks a proposed fix in a disposable sandbox cloned from the
    real database. Returns None if this finding's rule isn't a type this
    can benchmark yet.
    """
    query = _benchmark_query(finding)
    if query is None:
        return None

    with Sandbox() as sb:
        before_ms = _measure_ms(sb, query)
        _apply_fix(sb, fix)
        after_ms = _measure_ms(sb, query)

    return VerificationResult(
        finding_id=finding.id,
        before_ms=before_ms,
        after_ms=after_ms,
        helped=after_ms <= before_ms * HELPED_THRESHOLD,
    )
