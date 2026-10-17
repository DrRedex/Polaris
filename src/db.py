"""
db.py

SereneDB connection manager and the EvidenceEnvelope that wraps every tool result.
SereneDB speaks the PostgreSQL wire protocol (default port 7890).

Every row-returning tool goes through EvidenceBuilder so the agent always receives
the exact SQL, bound parameters, timings, data scope and truncation status of the
queries that produced its evidence.
"""

import os
import threading
import time
from datetime import date, datetime, time as dtime, timedelta
from decimal import Decimal
from typing import Any, Optional

import psycopg
from psycopg.rows import dict_row
from dotenv import load_dotenv
from pydantic import BaseModel, Field

load_dotenv()

SERENEDB_DSN = os.getenv(
    "SERENEDB_DSN",
    f"postgresql://postgres:{os.getenv('SERENEDB_PASSWORD', 'serenedb')}@127.0.0.1:7890/postgres",
)
# Set to 1 if the server rejects extended-protocol (server-side) parameter binding.
CLIENT_SIDE_BINDING = os.getenv("SERENEDB_CLIENT_SIDE_BINDING", "0") == "1"

DATASET_TABLES = {"BGL": "bgl_logs", "OpenStack": "openstack_logs"}


READER_ROLE = "log_reader"


def reader_dsn() -> str:
    """Connection string of the SELECT-only role used for guarded LLM-written SQL."""
    return os.getenv("SERENEDB_READER_DSN") or (
        f"postgresql://{READER_ROLE}:{os.getenv('SERENEDB_READER_PASSWORD', 'log_reader')}@127.0.0.1:7890/postgres")


def get_connection(autocommit: bool = True, dsn: Optional[str] = None) -> psycopg.Connection:
    kwargs: dict[str, Any] = {"row_factory": dict_row, "autocommit": autocommit, "connect_timeout": 5}
    if CLIENT_SIDE_BINDING:
        kwargs["cursor_factory"] = psycopg.ClientCursor
    return psycopg.connect(dsn or SERENEDB_DSN, **kwargs)


def check_connection() -> dict[str, Any]:
    try:
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT version() AS version")
            row = cur.fetchone()
            return {"status": "connected", "version": row["version"] if row else "unknown"}
    except Exception as e:
        return {"status": "disconnected", "error": str(e)}


def to_jsonable(value: Any) -> Any:
    if isinstance(value, (datetime, date, dtime)):
        return value.isoformat()
    if isinstance(value, timedelta):
        return value.total_seconds()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {k: to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(v) for v in value]
    return value


class ExecutedQuery(BaseModel):
    label: str
    sql: str
    bound_parameters: list[Any]
    execution_time_ms: float
    result_rows: int


class EvidenceEnvelope(BaseModel):
    tool_name: str
    data_scope: dict[str, Any]
    executed_queries: list[ExecutedQuery]
    total_matching_count: Optional[int] = Field(
        None, description="Exact count computed in SereneDB over the full filtered corpus")
    returned_count: int
    is_truncated: bool
    truncation_notice: Optional[str] = None
    summary: Optional[dict[str, Any]] = Field(
        None, description="Derived figures computed from the complete query result, not from the sample")
    notes: list[str] = Field(default_factory=list)
    rows: list[dict[str, Any]]


# table -> (row count, monotonic time cached). Short-lived so a reload of the corpus while
# the MCP server or UI keeps running is reflected in data_scope within a minute.
_corpus_rows_cache: dict[str, tuple[int, float]] = {}
CORPUS_ROWS_TTL_S = 60.0


class EvidenceBuilder:
    """Runs parameterized SQL on one connection and records each query for the envelope."""

    def __init__(self, tool_name: str, dsn: Optional[str] = None):
        self.tool_name = tool_name
        self.dsn = dsn
        self.queries: list[ExecutedQuery] = []
        self.conn: Optional[psycopg.Connection] = None

    def __enter__(self) -> "EvidenceBuilder":
        self.conn = get_connection(dsn=self.dsn) if self.dsn else get_connection()
        return self

    def __exit__(self, *exc) -> None:
        if self.conn is not None:
            self.conn.close()

    def query(self, label: str, sql: str, params: list[Any] | tuple = (),
              timeout_s: Optional[float] = None) -> list[dict[str, Any]]:
        """Runs one statement. With timeout_s, the server is asked to cancel it when time runs out."""
        assert self.conn is not None, "use EvidenceBuilder as a context manager"
        sql = " ".join(line.strip() for line in sql.strip().splitlines() if line.strip())
        timer = threading.Timer(timeout_s, self.conn.cancel_safe) if timeout_s else None
        t0 = time.perf_counter()
        try:
            if timer:
                timer.start()
            with self.conn.cursor() as cur:
                cur.execute(sql, list(params))
                rows = cur.fetchall() if cur.description else []
        except psycopg.errors.QueryCanceled:
            raise TimeoutError(f"{label}: cancelled after {timeout_s:.0f} s; narrow the window") from None
        finally:
            if timer:
                timer.cancel()
        elapsed = (time.perf_counter() - t0) * 1000
        self.queries.append(ExecutedQuery(
            label=label,
            sql=sql,
            bound_parameters=to_jsonable(list(params)),
            execution_time_ms=round(elapsed, 1),
            result_rows=len(rows),
        ))
        return [to_jsonable(dict(r)) for r in rows]

    def corpus_rows(self, dataset: str) -> int:
        """Total rows in the dataset's base table (cached for CORPUS_ROWS_TTL_S)."""
        table = DATASET_TABLES[dataset]
        cached = _corpus_rows_cache.get(table)
        if cached is None or time.monotonic() - cached[1] > CORPUS_ROWS_TTL_S:
            assert self.conn is not None
            with self.conn.cursor() as cur:
                cur.execute(f"SELECT COUNT(*) AS n FROM {table}")
                cached = (int(cur.fetchone()["n"]), time.monotonic())
            _corpus_rows_cache[table] = cached
        return cached[0]

    def envelope(
        self,
        *,
        data_scope: dict[str, Any],
        rows: list[dict[str, Any]],
        total_matching_count: Optional[int] = None,
        is_truncated: bool = False,
        truncation_notice: Optional[str] = None,
        summary: Optional[dict[str, Any]] = None,
        notes: Optional[list[str]] = None,
        extra: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """extra: tool-specific top-level fields (e.g. executed_sql, knn_schema_matches)."""
        envelope = EvidenceEnvelope(
            tool_name=self.tool_name,
            data_scope=data_scope,
            executed_queries=self.queries,
            total_matching_count=total_matching_count,
            returned_count=len(rows),
            is_truncated=is_truncated,
            truncation_notice=truncation_notice,
            summary=summary,
            notes=notes or [],
            rows=rows,
        ).model_dump()
        if extra:
            envelope.update(to_jsonable(extra))
        return envelope
