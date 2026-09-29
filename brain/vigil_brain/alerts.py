"""Live-condition alerts: findings where the right action is telling a
human right now, not proposing and sandbox-verifying a fix.

idle_in_transaction and approaching_max_connections need no sandbox
because there's nothing to benchmark in an operational emergency.
possible_unbounded_query is here for a different reason: its "fix" (add a
LIMIT/pagination) necessarily changes the result set on purpose -- that's
not a correctness bug the way an LLM's wrong rewrite would be, and no
sandbox can tell us whether whatever calls this endpoint actually needs
every row or not. That's an API-contract judgment call, not something to
prove and auto-propose -- so it stays an alert, not a fix suggestion.

Unlike fix suggestions, alerts need a recency check: `latest_by_subject`
only guarantees "most recently recorded", not "still true". The collector
re-evaluates all three rules every poll cycle the underlying condition
holds, so a genuinely ongoing issue always has a fresh row; a resolved
one just stops getting new rows and ages out.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from vigil_brain.parser.store import Finding

# A few poll cycles' worth of slack (collector polls activity every 5s).
# Wide enough to tolerate a slow poll, narrow enough that a stale finding
# from a since-resolved condition won't be mistaken for a live one.
ALERT_FRESHNESS = timedelta(seconds=30)


@dataclass
class Alert:
    finding_id: int
    rule: str
    message: str


def is_fresh(finding: Finding, now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    try:
        recorded = datetime.fromisoformat(finding.recorded_at)
    except ValueError:
        # Findings recorded before recorded_at switched to RFC3339Nano
        # carry Go's old debug-string format and won't parse here. Fail
        # closed (not fresh) rather than crash the whole pipeline on one
        # old row.
        return False
    return now - recorded <= ALERT_FRESHNESS


def check_idle_in_transaction(finding: Finding) -> Alert | None:
    if finding.rule != "idle_in_transaction":
        return None
    return Alert(
        finding_id=finding.id,
        rule=finding.rule,
        message=f"{finding.subject} {finding.detail} -- query: {finding.query!r}",
    )


def check_approaching_max_connections(finding: Finding) -> Alert | None:
    if finding.rule != "approaching_max_connections":
        return None
    return Alert(
        finding_id=finding.id,
        rule=finding.rule,
        message=finding.detail,
    )


def check_unbounded_query(finding: Finding) -> Alert | None:
    if finding.rule != "possible_unbounded_query":
        return None
    return Alert(
        finding_id=finding.id,
        rule=finding.rule,
        message=f"{finding.detail} -- query: {finding.query!r}",
    )


ALERTERS = [check_idle_in_transaction, check_approaching_max_connections, check_unbounded_query]
