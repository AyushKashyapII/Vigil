"""Deterministic fix suggestion for possible_n_plus_one findings that
match a simple single-column lookup shape: `SELECT ... FROM table WHERE
table.column = $1`, nothing else -- no join, no extra conditions, no
LIMIT/ORDER BY/GROUP BY.

No LLM needed here, unlike nested_subquery: `column = ANY(v1, ..., vN)`
is exactly the same predicate as running N individual `column = vi`
lookups and combining the results -- that's basic SQL semantics, not
something that needs to be checked empirically. Correctness follows from
the shape itself, same reasoning as why the index fixes never needed an
LLM either. Findings that don't match this narrow shape (joins, compound
WHERE clauses, etc.) aren't handled -- see fixes/rewrite.py for the
LLM-based path used when a mechanical transform isn't enough.
"""

import re
from dataclasses import dataclass

from vigil_brain.parser.store import Finding

_SIMPLE_LOOKUP_RE = re.compile(
    r"FROM\s+(\w+)\s+WHERE\s+\1\.(\w+)\s*=\s*\$1\s*$",
    re.IGNORECASE | re.DOTALL,
)


@dataclass
class NPlusOneFixSuggestion:
    finding_id: int
    rule: str
    description: str
    table: str
    column: str


def suggest_n_plus_one_fix(finding: Finding) -> NPlusOneFixSuggestion | None:
    if finding.rule != "possible_n_plus_one":
        return None
    match = _SIMPLE_LOOKUP_RE.search(finding.query)
    if not match:
        return None
    table, column = match.groups()
    return NPlusOneFixSuggestion(
        finding_id=finding.id,
        rule=finding.rule,
        description=(
            f"Batch repeated `{table}.{column} = ?` lookups into one "
            f"`{table}.{column} = ANY(...)` call ({finding.detail})"
        ),
        table=table,
        column=column,
    )
