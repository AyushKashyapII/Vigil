"""Turns a nested_subquery finding into a proposed rewrite via an LLM.

Unlike everything in fixes/index.py, this can't be a deterministic
template -- rewriting a correlated subquery into a JOIN correctly
requires understanding what the query means, not just substituting a
table/column name into a fixed shape. Its output is a suggestion only:
vigil_brain.sandbox.verify_rewrite proves whether it's actually correct
and faster before anything trusts it.
"""

import re

from vigil_brain.llm.groq_client import chat
from vigil_brain.parser.store import Finding

_SYSTEM_PROMPT = (
    "You are a PostgreSQL query optimization expert. Given a SQL query "
    "that uses correlated subqueries where a JOIN would be more "
    "efficient, rewrite it to use a JOIN (with GROUP BY if needed) while "
    "returning exactly the same columns, in the same order, and the same "
    "rows as the original. Respond with ONLY the rewritten SQL statement "
    "-- no explanation, no markdown code fences, no commentary."
)

_FENCE_RE = re.compile(r"^```(?:sql)?\s*|\s*```$", re.IGNORECASE | re.MULTILINE)


def _clean_sql(text: str) -> str:
    return _FENCE_RE.sub("", text).strip()


def suggest_nested_subquery_rewrite(finding: Finding, literal_query: str) -> str | None:
    """literal_query must already have its $N placeholders substituted --
    the LLM reasons about plain, runnable SQL, never Postgres's
    parameterized form, which it has no way to fill in itself.
    """
    if finding.rule != "nested_subquery":
        return None

    response = chat(_SYSTEM_PROMPT, literal_query)
    if response is None:
        return None

    rewritten = _clean_sql(response)
    if not rewritten or not rewritten.upper().startswith("SELECT"):
        return None
    return rewritten
