"""Generates the Alembic migration + PR body for a sandbox-verified fix.

Dry-run only: writes to PROPOSALS_DIR instead of pushing a branch or
calling `gh pr create` -- see ROADMAP.md. Only ever called with a
VerificationResult where helped=True, matching the project's "prove it,
don't trust it" principle: nothing gets proposed without a benchmark
behind it.

Handles both CREATE INDEX and DROP INDEX fixes. A DROP's downgrade can't
be fully automated -- the original index definition isn't reconstructable
from the DROP statement alone -- so its downgrade() is a manual note
instead of real SQL, rather than guessing at a definition.
"""

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path

from vigil_brain.fixes.index import FixSuggestion
from vigil_brain.parser.store import Finding
from vigil_brain.sandbox.verify import BENCHMARK_RUNS, VerificationResult

ALEMBIC_VERSIONS_DIR = os.environ.get("ALEMBIC_VERSIONS_DIR", "/alembic-versions")
PROPOSALS_DIR = os.environ.get("PROPOSALS_DIR", "/proposals")

_REVISION_RE = re.compile(r'^revision:\s*str\s*=\s*"([^"]+)"', re.MULTILINE)
_DOWN_REVISION_RE = re.compile(r'^down_revision.*=\s*"([^"]+)"', re.MULTILINE)
_CREATE_INDEX_RE = re.compile(r"CREATE INDEX (\S+) ON (\S+)", re.IGNORECASE)
_DROP_INDEX_RE = re.compile(r"DROP INDEX(?: IF EXISTS)?\s+(?:[\w]+\.)?([\w]+)", re.IGNORECASE)


@dataclass
class PRDraft:
    finding_id: int
    title: str
    body: str
    migration_filename: str
    migration_content: str


def _find_head_revision() -> str | None:
    """The one revision under ALEMBIC_VERSIONS_DIR nothing else's
    down_revision points to. Returns None (don't guess) if the directory
    is missing or the head is ambiguous.
    """
    versions_dir = Path(ALEMBIC_VERSIONS_DIR)
    if not versions_dir.is_dir():
        return None
    revisions: set[str] = set()
    down_revisions: set[str] = set()
    for path in versions_dir.glob("*.py"):
        text = path.read_text()
        rev = _REVISION_RE.search(text)
        down = _DOWN_REVISION_RE.search(text)
        if rev:
            revisions.add(rev.group(1))
        if down:
            down_revisions.add(down.group(1))
    heads = revisions - down_revisions
    if len(heads) != 1:
        return None
    return heads.pop()


def _migration_body(fix: FixSuggestion) -> tuple[str, str, str] | None:
    """Returns (index_name, upgrade_sql, downgrade_body) for a recognized
    fix shape, or None for a shape this doesn't know how to turn into a
    migration. downgrade_body is the literal Python source for the
    downgrade() function's body (indented, ready to drop into the
    template) -- a real op.execute(...) call for a CREATE INDEX fix,
    since the reverse (dropping it) is fully known; a manual note for a
    DROP INDEX fix, since the original index definition can't be
    reconstructed from the DROP statement alone.
    """
    create_match = _CREATE_INDEX_RE.search(fix.sql)
    if create_match:
        index_name, _table = create_match.groups()
        return (
            index_name,
            fix.sql.rstrip(";"),
            f'    op.execute("DROP INDEX IF EXISTS {index_name}")',
        )

    drop_match = _DROP_INDEX_RE.search(fix.sql)
    if drop_match:
        index_name = drop_match.group(1)
        return (
            index_name,
            fix.sql.rstrip(";"),
            f"    # {index_name}'s original definition can't be reconstructed from a DROP.\n"
            f"    # Recreate it manually from schema history if reverting this migration.\n"
            f"    pass",
        )

    return None


def build_pr(finding: Finding, fix: FixSuggestion, result: VerificationResult) -> PRDraft | None:
    if not result.helped:
        return None

    parsed = _migration_body(fix)
    if parsed is None:
        return None
    index_name, upgrade_sql, downgrade_body = parsed

    head = _find_head_revision()
    if head is None:
        return None

    # Deterministic, not sequential: reruns against the same fix produce
    # the same revision id instead of allocating a new one every poll
    # cycle the finding still exists.
    revision = hashlib.sha256(fix.sql.encode()).hexdigest()[:8]

    migration_content = f'''"""{fix.description}

Revision ID: {revision}
Revises: {head}

Auto-proposed by Vigil after sandbox verification:
  {result.before_ms:.2f}ms -> {result.after_ms:.2f}ms ({result.improvement_pct:.0f}% faster)
Finding: {finding.rule} {finding.subject} -- {finding.detail}
"""
from typing import Sequence, Union

from alembic import op

revision: str = "{revision}"
down_revision: Union[str, Sequence[str], None] = "{head}"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("{upgrade_sql}")


def downgrade() -> None:
{downgrade_body}
'''

    body = f"""## {fix.description}

**Finding:** `{finding.rule}` on `{finding.subject}`
{finding.detail}

**Proposed fix:**
```sql
{fix.sql}
```

**Sandbox verification** (median of {BENCHMARK_RUNS} runs against a disposable clone of the live schema):

| | Before | After | Change |
|---|---|---|---|
| Execution time | {result.before_ms:.2f}ms | {result.after_ms:.2f}ms | {result.improvement_pct:.0f}% faster |

Migration: `{revision}_{index_name}.py`, chained onto current head `{head}`.

---
*Generated by Vigil -- prove it, don't trust it. Dry-run mode: this PR was not opened.*
"""

    return PRDraft(
        finding_id=finding.id,
        title=f"vigil: {fix.description}",
        body=body,
        migration_filename=f"{revision}_{index_name}.py",
        migration_content=migration_content,
    )


def write_draft(draft: PRDraft) -> str:
    """Writes the migration file + PR body to PROPOSALS_DIR. Returns the
    directory it wrote to. Dry-run only -- nothing is pushed or opened.
    """
    out_dir = Path(PROPOSALS_DIR) / f"finding-{draft.finding_id}"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / draft.migration_filename).write_text(draft.migration_content)
    (out_dir / "PR_BODY.md").write_text(f"# {draft.title}\n\n{draft.body}")
    return str(out_dir)
