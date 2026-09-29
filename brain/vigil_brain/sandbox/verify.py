"""Verifies a proposed fix by benchmarking it in a disposable sandbox:
run a representative operation before the fix, apply the fix, run it
again, compare -- "prove it, don't trust it," per the root README.

possible_missing_index benchmarks read latency (the query that's
sequentially scanning). possible_unused_index benchmarks write latency
instead (an UPDATE touching the indexed column across a batch of rows) --
dropping an unused index doesn't make reads faster, it makes every
INSERT/UPDATE/DELETE on that table cheaper by removing one index to
maintain. The UPDATE deliberately targets the indexed column itself (not
some other column) so Postgres can't take its HOT-update fast path, which
skips index maintenance entirely when no indexed column changes -- that
shortcut would hide the exact cost we're trying to measure.

Query-rewrite findings (nested_subquery, possible_n_plus_one,
possible_unbounded_query) don't need a different sandbox, just a
benchmark-query builder for their shape -- not built yet.
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

# How many rows to touch in the unused-index write benchmark. Large enough
# for the per-row index-maintenance cost to add up to a measurable
# difference, small enough to stay fast against a big table.
UNUSED_INDEX_BENCHMARK_ROWS = 2000

_MISSING_INDEX_SUBJECT_RE = re.compile(r"^table=([^.]+)\.(\S+) column=(\S+)$")
_UNUSED_INDEX_SUBJECT_RE = re.compile(r"^index=([^.]+)\.(\S+)$")


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


def _missing_index_query(finding: Finding) -> str | None:
    """Builds the read query a missing index would speed up. No sandbox
    needed -- everything it depends on is already in the finding.
    """
    match = _MISSING_INDEX_SUBJECT_RE.match(finding.subject)
    if not match:
        return None
    schema, table, column = match.groups()
    return (
        f"SELECT * FROM {schema}.{table} "
        f"WHERE {column} = (SELECT {column} FROM {schema}.{table} LIMIT 1)"
    )


def _unused_index_query(sb: Sandbox, finding: Finding) -> str | None:
    """Builds the write query dropping this index would speed up. Needs
    the sandbox itself: the finding only names the index, not which
    table/column it's actually built on, so that has to come from the
    sandbox's own pg_indexes.
    """
    match = _UNUSED_INDEX_SUBJECT_RE.match(finding.subject)
    if not match:
        return None
    schema, index_name = match.groups()

    result = subprocess.run(
        [
            "psql", *pg_args(sb.conn), "-d", sb.name, "-t", "-A", "-c",
            f"SELECT tablename, indexdef FROM pg_indexes "
            f"WHERE schemaname = '{schema}' AND indexname = '{index_name}'",
        ],
        env=env_for(sb.conn), capture_output=True, text=True, check=True,
    )
    line = result.stdout.strip()
    if "|" not in line:
        return None
    table, indexdef = line.split("|", 1)

    col_match = re.search(r"\(([^)]+)\)", indexdef)
    if not col_match:
        return None
    first_col = col_match.group(1).split(",")[0].strip()

    return (
        f"UPDATE {schema}.{table} SET {first_col} = {first_col} "
        f"WHERE ctid IN (SELECT ctid FROM {schema}.{table} LIMIT {UNUSED_INDEX_BENCHMARK_ROWS})"
    )


def explain_once(sb: Sandbox, query: str) -> float:
    """Runs EXPLAIN ANALYZE once, returns execution time in milliseconds.
    Exposed separately from measure_ms for callers that need many
    individual measurements summed (e.g. possible_n_plus_one benchmarking
    N sequential calls) rather than one noise-reduced median.
    """
    result = subprocess.run(
        [
            "psql", *pg_args(sb.conn), "-d", sb.name,
            "-t", "-A", "-c", f"EXPLAIN (ANALYZE, FORMAT JSON) {query}",
        ],
        env=env_for(sb.conn),
        capture_output=True, text=True, check=True,
    )
    plan = json.loads(result.stdout)
    return plan[0]["Execution Time"]


def measure_ms(sb: Sandbox, query: str) -> float:
    """Runs EXPLAIN ANALYZE BENCHMARK_RUNS times, returns the median
    execution time in milliseconds. Multiple runs because a single
    EXPLAIN ANALYZE can be noisy (cache effects).
    """
    times = [explain_once(sb, query) for _ in range(BENCHMARK_RUNS)]
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
    can benchmark yet, without ever spinning up a sandbox for it.
    """
    if finding.rule == "possible_missing_index":
        query = _missing_index_query(finding)
        if query is None:
            return None
        with Sandbox() as sb:
            before_ms = measure_ms(sb, query)
            _apply_fix(sb, fix)
            after_ms = measure_ms(sb, query)

    elif finding.rule == "possible_unused_index":
        if not _UNUSED_INDEX_SUBJECT_RE.match(finding.subject):
            return None
        with Sandbox() as sb:
            query = _unused_index_query(sb, finding)
            if query is None:
                return None
            before_ms = measure_ms(sb, query)
            _apply_fix(sb, fix)
            after_ms = measure_ms(sb, query)

    else:
        return None

    return VerificationResult(
        finding_id=finding.id,
        before_ms=before_ms,
        after_ms=after_ms,
        helped=after_ms <= before_ms * HELPED_THRESHOLD,
    )
