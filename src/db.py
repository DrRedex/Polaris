"""
db.py

SereneDB connection manager and the EvidenceEnvelope that wraps every tool result.
SereneDB speaks the PostgreSQL wire protocol (default port 7890).

Every row-returning tool goes through EvidenceBuilder so the agent always receives
the exact SQL, bound parameters, timings, data scope and truncation status of the
queries that produced its evidence.
"""

import os
import uuid
import threading
import time
from datetime import date, datetime, time as dtime, timedelta, timezone
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

# Process-local audit store. Full SQL/rows never need to be copied into agent payloads.
EVIDENCE_LEDGER: dict[str, dict[str, Any]] = {}
_ledger_lock = threading.RLock()


def record_evidence(metadata: dict[str, Any]) -> str:
    evidence_id = f"E-{uuid.uuid4().hex[:12]}"
    with _ledger_lock:
        EVIDENCE_LEDGER[evidence_id] = to_jsonable(metadata)
    return evidence_id


def to_llm_compact_envelope(rows: list[dict[str, Any]], *, message_limit: int = 300,
                            deduplicate_consecutive: bool = False) -> dict[str, Any]:
    """Columnar-ish compact result with transparent message truncation and run folding."""
    safe = []
    removed = 0
    for source in rows:
        row = dict(source)
        msg = row.get("message")
        if isinstance(msg, str) and len(msg) > message_limit:
            removed += len(msg) - message_limit
            row["message"] = msg[:message_limit]
            row["message_truncated"] = True
        safe.append(row)
    folded = []
    dedup_runs = 0
    if deduplicate_consecutive:
        for row in safe:
            # Keep temporal/id fields to avoid combining different events; only identical rows fold.
            if folded and {k: v for k, v in folded[-1]["row"].items() if k not in {"run_count"}} == row:
                folded[-1]["run_count"] += 1
                dedup_runs += 1
            else:
                folded.append({"row": row, "run_count": 1})
        safe = [({**x["row"], **({"run_count": x["run_count"]} if x["run_count"] > 1 else {})}) for x in folded]
    columns = list(dict.fromkeys(k for row in safe for k in row))
    return {"columns": columns, "rows": [[row.get(c) for c in columns] for row in safe],
            "message_chars_truncated": removed,
            "deduplication": {"enabled": deduplicate_consecutive, "runs_folded": dedup_runs,
                              "input_rows": len(rows), "output_rows": len(safe)}}


def to_llm_evidence_view(envelope: dict[str, Any]) -> dict[str, Any]:
    """Compact model-facing copy; full SQL and detailed rows stay in the local evidence ledger."""
    view = dict(envelope)
    rows = envelope.get("rows") or []
    compact = to_llm_compact_envelope(rows)
    view["rows"] = compact
    view["efficiency_telemetry"] = dict(envelope.get("efficiency_telemetry") or {})
    view["efficiency_telemetry"]["message_chars_truncated"] = max(
        int(view["efficiency_telemetry"].get("message_chars_truncated") or 0), compact["message_chars_truncated"])
    view["truncation_deduplication"] = compact["deduplication"]
    if isinstance(view.get("executed_queries"), list):
        view["executed_queries"] = [{k: v for k, v in q.items() if k != "sql"}
                                     for q in view["executed_queries"] if isinstance(q, dict)]
    view.pop("executed_sql", None)
    view.pop("candidate_sql", None)
    return to_jsonable(view)


READER_ROLE = "log_reader"


def reader_dsn() -> str:
    """Connection string of the SELECT-only role used for guarded LLM-written SQL."""
    return os.getenv("SERENEDB_READER_DSN") or (
        f"postgresql://{READER_ROLE}:{os.getenv('SERENEDB_READER_PASSWORD', 'log_reader')}@127.0.0.1:7890/postgres")


class RoleConfigError(RuntimeError):
    """A role's least-privilege DSN is missing. The message names the env var, never a credential."""

    def __init__(self, env_name: str):
        super().__init__(f"{env_name} is not configured; refusing to run with elevated credentials")
        self.env_name = env_name


def role_dsn(role: str) -> str:
    """Return an explicitly configured least-privilege role DSN; never fall back to superuser."""
    env_name = {"engineer": "SERENEDB_ENGINEER_DSN", "team_lead": "SERENEDB_TEAM_LEAD_DSN",
                "manager": "SERENEDB_MANAGER_DSN"}.get(role.lower())
    if not env_name:
        raise ValueError("role must be engineer, team_lead, or manager")
    value = os.getenv(env_name)
    if not value:
        raise RoleConfigError(env_name)
    return value


def get_connection(autocommit: bool = True, dsn: Optional[str] = None) -> psycopg.Connection:
    kwargs: dict[str, Any] = {"row_factory": dict_row, "autocommit": autocommit, "connect_timeout": 5}
    if CLIENT_SIDE_BINDING:
        kwargs["cursor_factory"] = psycopg.ClientCursor
    selected_dsn = dsn
    if selected_dsn is None:
        configured_role = os.getenv("SERENEDB_APP_ROLE")
        selected_dsn = role_dsn(configured_role) if configured_role else SERENEDB_DSN
    return psycopg.connect(selected_dsn, **kwargs)


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
    try:
        import numpy as np
        if isinstance(value, np.generic):
            return value.item()
        if isinstance(value, np.ndarray):
            return [to_jsonable(v) for v in value.tolist()]
    except ImportError:
        pass
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
    evidence_id: Optional[str] = Field(
        None, description="Citation handle (EV-01, EV-02, ...) assigned when the result is returned to an agent")
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
        except psycopg.errors.QueryCanceled as exc:
            record_evidence({"tool_name": self.tool_name, "query_label": label, "executed_sql": sql,
                             "bound_parameters": to_jsonable(list(params)), "execution_time_ms": (time.perf_counter()-t0)*1000,
                             "status": "timeout", "error": f"cancelled after {timeout_s:.0f} s" if timeout_s else str(exc),
                             "role_context": os.getenv("SERENEDB_APP_ROLE", "unspecified"),
                             "recorded_at": datetime.now(timezone.utc).isoformat(), "evidence_type": "serenedb_query_error"})
            timeout_note = f"cancelled after {timeout_s:.0f} s; narrow the window" if timeout_s else "cancelled by SereneDB"
            raise TimeoutError(f"{label}: {timeout_note}") from None
        except Exception as exc:
            record_evidence({"tool_name": self.tool_name, "query_label": label, "executed_sql": sql,
                             "bound_parameters": to_jsonable(list(params)), "execution_time_ms": (time.perf_counter()-t0)*1000,
                             "status": "error", "error": str(exc),
                             "role_context": os.getenv("SERENEDB_APP_ROLE", "unspecified"),
                             "recorded_at": datetime.now(timezone.utc).isoformat(), "evidence_type": "serenedb_query_error"})
            raise
        finally:
            if timer:
                timer.cancel()
        elapsed = (time.perf_counter() - t0) * 1000
        self.queries.append(ExecutedQuery(
            label=label,
            sql=sql,
            bound_parameters=to_jsonable(list(params)),
            execution_time_ms=round(elapsed, 3),
            result_rows=len(rows),
        ))
        return [to_jsonable(dict(r)) for r in rows]

    def corpus_rows(self, dataset: str) -> int:
        """Total rows in the dataset's base table (cached for CORPUS_ROWS_TTL_S)."""
        table = DATASET_TABLES[dataset]
        cached = _corpus_rows_cache.get(table)
        if cached is None or time.monotonic() - cached[1] > CORPUS_ROWS_TTL_S:
            assert self.conn is not None
            started = time.perf_counter()
            statement = f"SELECT COUNT(*) AS n FROM {table}"
            with self.conn.cursor() as cur:
                cur.execute(statement)
                cached = (int(cur.fetchone()["n"]), time.monotonic())
            self.queries.append(ExecutedQuery(label=f"{dataset} corpus row count", sql=statement,
                bound_parameters=[], execution_time_ms=(time.perf_counter()-started)*1000, result_rows=1))
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
        original_rows = rows
        if any("message" in row for row in rows):
            rows = rows[:50]
            if len(original_rows) > len(rows):
                is_truncated = True
                truncation_notice = truncation_notice or "Raw log rows capped at 50 for agent-facing output."
        evidence_id = record_evidence({"tool_name": self.tool_name, "executed_queries": [q.model_dump() for q in self.queries],
                                       "data_scope": data_scope, "rows": original_rows,
                                       "status": "success", "evidence_type": "serenedb_query",
                                       "role_context": os.getenv("SERENEDB_APP_ROLE", "unspecified"),
                                       "recorded_at": datetime.now(timezone.utc).isoformat()})
        compact = to_llm_compact_envelope(rows)
        rows = [dict(zip(compact["columns"], row)) for row in compact["rows"]]
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
        envelope["evidence_id"] = evidence_id
        envelope["efficiency_telemetry"] = {
            "sql_execution_ms": round(sum(q.execution_time_ms for q in self.queries), 2),
            "sql_timing_kind": "client round-trip execution time",
            "rows_scanned": None,
            "rows_scanned_unavailable_reason": "SereneDB scan counters are not exposed by the configured driver path.",
            "rows_returned": len(rows),
            "message_chars_truncated": compact["message_chars_truncated"],
            "compression_ratio": None,
            "compression_ratio_unavailable_reason": "Reliable rows_scanned metric is unavailable.",
            "deduplication": compact["deduplication"],
        }
        return envelope
