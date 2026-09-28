"""Creates disposable Postgres databases cloned from the real one, for
safely testing proposed fixes without ever touching production data.

Uses pg_dump/psql/createdb/dropdb via subprocess rather than a Python
driver or Alembic -- this clones whatever the real database's schema and
data *actually* are right now, works regardless of what migration tool
(if any) the target app uses, and is the technique that actually
generalizes to a database Vigil doesn't control the source code of.
"""

import os
import subprocess
import uuid
from dataclasses import dataclass
from urllib.parse import urlparse

DEFAULT_SOURCE_URL = "postgresql://postgres:postgres@localhost:5432/demo"


@dataclass
class ConnInfo:
    host: str
    port: int
    user: str
    password: str
    dbname: str


def _conn_info() -> ConnInfo:
    url = os.environ.get("SANDBOX_DATABASE_URL", DEFAULT_SOURCE_URL)
    parsed = urlparse(url)
    return ConnInfo(
        host=parsed.hostname or "localhost",
        port=parsed.port or 5432,
        user=parsed.username or "postgres",
        password=parsed.password or "",
        dbname=parsed.path.lstrip("/") or "postgres",
    )


def _env(conn: ConnInfo) -> dict:
    env = os.environ.copy()
    env["PGPASSWORD"] = conn.password
    return env


def _pg_args(conn: ConnInfo) -> list[str]:
    return ["-h", conn.host, "-p", str(conn.port), "-U", conn.user]


class Sandbox:
    """A disposable Postgres database cloned from the real one.

    Use as a context manager so it's always torn down, even on error:

        with Sandbox() as sb:
            ...  # sb.url() is a connection string to the clone
        # dropped here, unconditionally
    """

    def __init__(self) -> None:
        self.conn = _conn_info()
        self.name = f"vigil_sandbox_{uuid.uuid4().hex[:12]}"

    def __enter__(self) -> "Sandbox":
        self._create()
        self._clone()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._drop()

    def _create(self) -> None:
        subprocess.run(
            ["createdb", *_pg_args(self.conn), self.name],
            env=_env(self.conn),
            check=True,
        )

    def _clone(self) -> None:
        dump = subprocess.run(
            ["pg_dump", *_pg_args(self.conn), self.conn.dbname],
            env=_env(self.conn),
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["psql", *_pg_args(self.conn), "-d", self.name],
            env=_env(self.conn),
            input=dump.stdout,
            check=True,
            capture_output=True,
        )

    def _drop(self) -> None:
        # Best-effort: don't raise during teardown, a failed drop
        # shouldn't mask whatever happened inside the `with` block.
        subprocess.run(
            ["dropdb", *_pg_args(self.conn), "--if-exists", self.name],
            env=_env(self.conn),
            check=False,
        )

    def url(self) -> str:
        """Connection URL for this sandbox database itself."""
        return (
            f"postgresql://{self.conn.user}:{self.conn.password}"
            f"@{self.conn.host}:{self.conn.port}/{self.name}"
        )
