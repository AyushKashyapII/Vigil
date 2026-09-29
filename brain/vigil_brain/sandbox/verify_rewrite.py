"""Verifies an LLM-proposed query rewrite in a disposable sandbox.

Unlike index fixes, a rewrite has no correctness guarantee by
construction -- a wrong JOIN can silently change the result set. So this
checks two things, in order: do the original and rewritten query return
the same rows (an EXCEPT-based diff -- zero rows both directions means
equivalent), and only if so, is the rewrite actually faster. A rewrite
that's fast but wrong is rejected before timing is even measured.
"""

import re
import subprocess
from dataclasses import dataclass

from vigil_brain.parser.store import Finding
from vigil_brain.sandbox.postgres_sandbox import Sandbox, env_for, pg_args
from vigil_brain.sandbox.verify import HELPED_THRESHOLD, measure_ms

# WHERE-clause equality filter, e.g. "WHERE orders.user_id = $2" -- same
# shape possible_missing_index correlates against.
_WHERE_COLUMN_RE = re.compile(r"WHERE\s+(\w+)\.(\w+)\s*=\s*\$(\d+)", re.IGNORECASE)
_PLACEHOLDER_RE = re.compile(r"\$(\d+)")


@dataclass
class RewriteVerificationResult:
    finding_id: int
    results_match: bool
    before_ms: float | None
    after_ms: float | None
    helped: bool


def substitute_placeholders(query: str) -> str:
    """Turns pg_stat_statements' parameterized query text into something
    directly runnable. A WHERE-clause equality filter gets a real value
    pulled from the sandbox's own data via a subselect (same trick as
    possible_missing_index -- guaranteed to exist, unlike a guessed
    literal). Any other placeholder (e.g. a COALESCE default) falls back
    to a plain 0 -- not universally correct for every possible query
    shape, but matches every real case seen so far. See ROADMAP.md.
    """
    substitutions: dict[str, str] = {}
    for match in _WHERE_COLUMN_RE.finditer(query):
        table, column, num = match.groups()
        substitutions[f"${num}"] = f"(SELECT {column} FROM {table} LIMIT 1)"

    return _PLACEHOLDER_RE.sub(lambda m: substitutions.get(m.group(0), "0"), query)


def _results_match(sb: Sandbox, query_a: str, query_b: str) -> bool:
    diff_query = (
        f"(({query_a}) EXCEPT ({query_b})) UNION (({query_b}) EXCEPT ({query_a}))"
    )
    result = subprocess.run(
        [
            "psql", *pg_args(sb.conn), "-d", sb.name, "-t", "-A", "-c",
            f"SELECT count(*) FROM ({diff_query}) AS diff",
        ],
        env=env_for(sb.conn), capture_output=True, text=True, check=True,
    )
    return result.stdout.strip() == "0"


def verify_rewrite(finding: Finding, rewritten_query: str) -> RewriteVerificationResult | None:
    """Benchmarks an LLM-proposed rewrite against the original, in a
    disposable sandbox cloned from the real database. Returns None if
    this finding's rule isn't a type this can verify.
    """
    if finding.rule != "nested_subquery":
        return None

    original_query = substitute_placeholders(finding.query)

    with Sandbox() as sb:
        try:
            match = _results_match(sb, original_query, rewritten_query)
        except subprocess.CalledProcessError:
            # Couldn't even run the diff (e.g. mismatched column count/
            # types) -- treat that as "not equivalent", not an error to
            # propagate. A rewrite that can't be compared can't be trusted.
            match = False

        if not match:
            return RewriteVerificationResult(
                finding_id=finding.id, results_match=False,
                before_ms=None, after_ms=None, helped=False,
            )

        before_ms = measure_ms(sb, original_query)
        after_ms = measure_ms(sb, rewritten_query)

    return RewriteVerificationResult(
        finding_id=finding.id,
        results_match=True,
        before_ms=before_ms,
        after_ms=after_ms,
        helped=after_ms <= before_ms * HELPED_THRESHOLD,
    )
