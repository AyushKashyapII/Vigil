"""Verifies a possible_n_plus_one finding by benchmarking what actually
happens today (N sequential single-row lookups) against a batched
ANY() rewrite, in a disposable sandbox.

No correctness check here, unlike verify_rewrite.py's LLM-proposed
rewrites: fixes/nplusone.py only matches a query already confirmed to be
exactly `SELECT ... FROM table WHERE table.column = $1` with no other
condition, and `column = ANY(v1, ..., vN)` is mathematically the same
predicate as unioning N individual `column = vi` lookups. Correctness
follows from the shape, not from checking it.
"""

import re
import subprocess
from dataclasses import dataclass

from vigil_brain.parser.store import Finding
from vigil_brain.sandbox.postgres_sandbox import Sandbox, env_for, pg_args
from vigil_brain.sandbox.verify import HELPED_THRESHOLD, explain_once, measure_ms

_SIMPLE_LOOKUP_RE = re.compile(
    r"FROM\s+(\w+)\s+WHERE\s+\1\.(\w+)\s*=\s*\$1\s*$",
    re.IGNORECASE | re.DOTALL,
)
_NUMERIC_RE = re.compile(r"^-?\d+(\.\d+)?$")

# How many real values to batch in the sandbox test. Fixed, not derived
# from the finding's actual observed call count -- keeps the benchmark
# fast and consistent regardless of how bursty real traffic happened to
# be, same reasoning as UNUSED_INDEX_BENCHMARK_ROWS.
N_PLUS_ONE_BATCH_SIZE = 50


@dataclass
class NPlusOneVerificationResult:
    finding_id: int
    before_ms: float
    after_ms: float
    helped: bool

    @property
    def improvement_pct(self) -> float:
        if self.before_ms == 0:
            return 0.0
        return (self.before_ms - self.after_ms) / self.before_ms * 100


def _sql_literal(value: str) -> str:
    if _NUMERIC_RE.match(value):
        return value
    return "'" + value.replace("'", "''") + "'"


def _fetch_values(sb: Sandbox, table: str, column: str) -> list[str]:
    result = subprocess.run(
        [
            "psql", *pg_args(sb.conn), "-d", sb.name, "-t", "-A", "-c",
            f"SELECT DISTINCT {column} FROM {table} "
            f"WHERE {column} IS NOT NULL LIMIT {N_PLUS_ONE_BATCH_SIZE}",
        ],
        env=env_for(sb.conn), capture_output=True, text=True, check=True,
    )
    return [line for line in result.stdout.strip().split("\n") if line]


def _query_for_value(query: str, value: str) -> str:
    literal = _sql_literal(value)
    return re.sub(r"\$1\s*$", lambda _m: literal, query.rstrip())


def _batched_query(query: str, table: str, column: str, values: list[str]) -> str:
    array_literal = "ARRAY[" + ", ".join(_sql_literal(v) for v in values) + "]"
    return re.sub(
        rf"WHERE\s+{re.escape(table)}\.{re.escape(column)}\s*=\s*\$1\s*$",
        lambda _m: f"WHERE {table}.{column} = ANY({array_literal})",
        query.rstrip(),
        flags=re.IGNORECASE,
    )


def verify_n_plus_one(finding: Finding) -> NPlusOneVerificationResult | None:
    if finding.rule != "possible_n_plus_one":
        return None

    match = _SIMPLE_LOOKUP_RE.search(finding.query)
    if not match:
        return None
    table, column = match.groups()

    with Sandbox() as sb:
        values = _fetch_values(sb, table, column)
        if len(values) < 2:
            return None  # not enough real data to meaningfully benchmark

        before_total = sum(
            explain_once(sb, _query_for_value(finding.query, v)) for v in values
        )
        after_ms = measure_ms(sb, _batched_query(finding.query, table, column, values))

    return NPlusOneVerificationResult(
        finding_id=finding.id,
        before_ms=before_total,
        after_ms=after_ms,
        helped=after_ms <= before_total * HELPED_THRESHOLD,
    )
