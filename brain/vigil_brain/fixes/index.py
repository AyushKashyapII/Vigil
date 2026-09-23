"""Deterministic fix suggestions for index-related findings.

No LLM involved -- these are template-based: the finding's rule name
picks a fix shape, and the finding's own fields fill in the template. This
only works for findings that already carry enough information to act on
without inference (see ROADMAP.md for possible_missing_index, which
doesn't).
"""

import re
from dataclasses import dataclass

from vigil_brain.parser.store import Finding

_SUBJECT_RE = re.compile(r"^index=([^.]+)\.(.+)$")


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

    match = _SUBJECT_RE.match(finding.subject)
    if not match:
        return None
    schema, index_name = match.groups()

    return FixSuggestion(
        finding_id=finding.id,
        rule=finding.rule,
        description=f"Drop unused index {index_name} ({finding.detail})",
        sql=f"DROP INDEX IF EXISTS {schema}.{index_name};",
    )
