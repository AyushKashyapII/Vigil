"""Deterministic fix suggestions for index-related findings.

No LLM involved -- these are template-based: the finding's rule name
picks a fix shape, and the finding's own fields fill in the template. This
only works for findings that already carry enough information to act on
without inference.
"""

import re
from dataclasses import dataclass

from vigil_brain.parser.store import Finding

_UNUSED_SUBJECT_RE = re.compile(r"^index=([^.]+)\.(.+)$")
_MISSING_SUBJECT_RE = re.compile(r"^table=([^.]+)\.(\S+)(?:\s+column=(\S+))?$")


@dataclass
class FixSuggestion:
    finding_id: int
    rule: str
    description: str
    sql: str


def suggest_unused_index_fix(finding: Finding) -> FixSuggestion | None:
    """Proposes dropping an index flagged as unused.

    finding.subject is "index=schema.indexname" -- exactly what's needed
    to drop it, no inference required (unlike possible_missing_index,
    which needs a column name nothing currently captures).
    """
    if finding.rule != "possible_unused_index":
        return None

    match = _UNUSED_SUBJECT_RE.match(finding.subject)
    if not match:
        return None
    schema, index_name = match.groups()

    return FixSuggestion(
        finding_id=finding.id,
        rule=finding.rule,
        description=f"Drop unused index {index_name} ({finding.detail})",
        sql=f"DROP INDEX IF EXISTS {schema}.{index_name};",
    )


def suggest_missing_index_fix(finding: Finding) -> FixSuggestion | None:
    """Proposes an index for a table getting expensive sequential scans.

    finding.subject is "table=schema.table" or, when collector managed to
    correlate a filtered column from the same poll cycle's query text,
    "table=schema.table column=col". Only proposes a fix in the latter
    case -- without a column, we genuinely don't know what to index, and
    this deliberately returns None rather than guess (same "honest
    couldn't-tell" reasoning collector itself uses).
    """
    if finding.rule != "possible_missing_index":
        return None

    match = _MISSING_SUBJECT_RE.match(finding.subject)
    if not match:
        return None
    schema, table, column = match.groups()
    if column is None:
        return None

    index_name = f"idx_{table}_{column}"
    return FixSuggestion(
        finding_id=finding.id,
        rule=finding.rule,
        description=f"Add index on {schema}.{table}({column}) ({finding.detail})",
        sql=f"CREATE INDEX {index_name} ON {schema}.{table} ({column});",
    )
