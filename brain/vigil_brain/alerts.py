"""Live-condition alerts: findings where the right action is telling a
human right now, not proposing and sandbox-verifying a fix. No sandbox
needed -- these are operational states (a stuck transaction, a connection
pool nearing capacity), not schema changes that need proving safe first.

Unlike fix suggestions, these need a recency check: `latest_by_subject`
only guarantees "most recently recorded", not "still true". The collector
re-fires idle_in_transaction/approaching_max_connections on every poll
cycle the condition holds, so a genuinely ongoing issue always has a
fresh row; a resolved one just stops getting new rows and ages out.
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


ALERTERS = [check_idle_in_transaction, check_approaching_max_connections]
