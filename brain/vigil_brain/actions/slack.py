"""Formats live-alert findings as a Slack-ready message.

Dry-run only: prints instead of posting to a real webhook -- see
ROADMAP.md. Matches the project's design principle that live issues
(connection leaks, near-exhausted connection pools, an endpoint returning
an unbounded result set) bypass the sandbox entirely and go straight to a
human-facing alert, since there's nothing to benchmark or auto-propose --
either there's an active operational issue, or a judgment call only a
human can make.
"""

from vigil_brain.alerts import Alert

_ICONS = {
    "idle_in_transaction": ":warning:",
    "approaching_max_connections": ":rotating_light:",
    "possible_unbounded_query": ":inbox_tray:",
}


def format_slack_message(alert: Alert) -> str:
    icon = _ICONS.get(alert.rule, ":bell:")
    return f"{icon} *{alert.rule}*: {alert.message}"


def send_alert(alert: Alert) -> None:
    print(f"[DRY RUN -- would post to Slack] {format_slack_message(alert)}")
