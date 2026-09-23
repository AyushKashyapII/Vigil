"""Reads findings the collector has persisted to its SQLite store."""

import os
import sqlite3
from dataclasses import dataclass

DEFAULT_STORE_PATH = "/data/vigil.db"


@dataclass
class Finding:
    id: int
    recorded_at: str
    rule: str
    subject: str
    query: str
    detail: str


def store_path() -> str:
    return os.environ.get("VIGIL_STORE_PATH", DEFAULT_STORE_PATH)


def read_findings() -> list[Finding]:
    """Reads every finding from the collector's store, oldest first.

    Opens the SQLite file read-only -- this is the collector's store,
    brain only ever reads it, never writes to it.
    """
    uri = f"file:{store_path()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        cursor = conn.execute(
            "SELECT id, recorded_at, rule, subject, query, detail FROM findings ORDER BY id"
        )
        return [Finding(*row) for row in cursor.fetchall()]
    finally:
        conn.close()


def latest_by_subject(findings: list[Finding]) -> list[Finding]:
    """Keeps only the most recent finding per (rule, subject) pair.

    The same problem (e.g. the same unused index) gets flagged repeatedly
    across poll cycles. For proposing fixes, only the latest instance of
    each is useful -- not one identical suggestion per historical
    occurrence. Relies on findings being ordered oldest-first (as
    read_findings returns them), so the last write per key wins.
    """
    latest: dict[tuple[str, str], Finding] = {}
    for f in findings:
        latest[(f.rule, f.subject)] = f
    return list(latest.values())
