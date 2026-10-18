"""Orchestrate the existing guarded pipeline and shape its evidence for Polaris."""

from __future__ import annotations

import json
import logging
import os
import statistics
import textwrap
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from pydantic_ai import Agent

from src.claim_verifier import verify_response_claims
from src.db import get_connection, role_dsn
from src.pipeline_orchestrator import run_full_intelligence_pipeline
from src.api.schemas import (
    AlertItem,
    ComponentErrorItem,
    EvidenceRowModel,
    ExecutedQueryModel,
    FindingModel,
    HypothesisModel,
    InvestigationRequest,
    InvestigationResponse,
    RoleContentResponse,
    StatModel,
    TimeseriesPoint,
    TimeseriesResponse,
    TimeseriesTableRow,
)

logger = logging.getLogger(__name__)

ROLE_NAMES = {"ENG": "Engineer", "LEAD": "Team Lead", "MGR": "Manager"}
ROLE_SLUGS = {"ENG": "engineer", "LEAD": "team_lead", "MGR": "manager"}
ROLE_SUBTITLES = {
    "ENG": "Root cause view for engineers",
    "LEAD": "Failure patterns view for team leads",
    "MGR": "Risk view for managers",
}

ROLE_GUIDANCE = {
    "ENG": "Focus on the sequence of events, affected component, and concrete technical checks.",
    "LEAD": "Focus on recurring patterns, component ownership, and practical team follow-up.",
    "MGR": "Summarize operational risk and next steps in plain language. Do not infer customer, financial, SLA, or downtime impact.",
}

# Evidence shown per investigation: the LLM query's rows first, then the deterministic grounding rows.
EVIDENCE_CAP = 20
LLM_ROW_CAP = 6
ENG_LINE_LIMIT = 8
ENG_SLOWEST_LIMIT = 5
LEAD_WINDOW_LIMIT = 10
LEAD_WINDOWS_PER_SEVERITY = 4
MGR_ROW_LIMIT = 12

# Manager queries are constrained to aggregate views. Never expose identifiers or raw message text there.
MGR_ALLOWED = {
    "dataset", "component", "severity", "frequency", "persistence_days",
    "avg_latency_sec", "p95_latency_sec", "team_name", "escalation_tier",
    "window_start", "log_lines", "anomaly_lines", "first_seen", "last_seen", "summary",
}

TS_KEYS = ("event_time", "timestamp", "ts", "window_start", "first_seen")
TRACE_KEYS = ("request_id", "trace_id", "trace", "node_id", "instance_id")
COMP_KEYS = ("component", "logger", "dataset")
SEV_KEYS = ("severity", "sev")

SEVERITY_ORDER = "CASE severity WHEN 'CRITICAL' THEN 0 WHEN 'ERROR' THEN 1 WHEN 'WARNING' THEN 2 ELSE 3 END"
WINDOW_30M = ("date_trunc('hour', event_time) + CASE WHEN EXTRACT(MINUTE FROM event_time) >= 30 "
              "THEN INTERVAL '30 minutes' ELSE INTERVAL '0 minutes' END")


# ---- value formatting -------------------------------------------------------

def _first(row: dict[str, Any], *keys: str, default: Any = "") -> Any:
    for key in keys:
        value = row.get(key)
        if value is not None and value != "":
            return value
    return default


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat(sep=" ", timespec="milliseconds")
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, float):
        return f"{value:.4f}".rstrip("0").rstrip(".")
    return str(value)


def _ts_text(value: Any) -> str:
    """Timestamps from the pipeline arrive as JSON strings; show them like the grounding rows."""
    if isinstance(value, str):
        try:
            return _text(datetime.fromisoformat(value))
        except ValueError:
            return value
    return _text(value)


def _short_ts(value: Any) -> str:
    """'2017-05-16 03:19:45.356' -> '05-16 03:19'."""
    text = _ts_text(value)
    return text[5:16] if len(text) >= 16 else text


def _severity(value: Any) -> str:
    severity = str(value or "INFO").upper()
    if severity in {"WARNING", "WARN"}:
        return "WARN"
    if severity in {"ERROR", "SEVERE", "FATAL", "FAILURE", "CRITICAL"}:
        return "ERROR"
    return "INFO"


def _sev_prefix(value: Any) -> str:
    """Keep severities the three-level scale folds away (CRITICAL -> ERROR) visible in the body."""
    severity = str(value or "").upper()
    return f"[{severity}] " if severity in {"CRITICAL", "SEVERE", "FATAL", "FAILURE"} else ""


# ---- evidence rows ----------------------------------------------------------

def _extract_rows(observed: dict[str, Any] | None) -> tuple[list[dict[str, Any]], str | None]:
    if not observed:
        return [], None
    data = observed.get("data") or {}
    columns = data.get("columns") or []
    rows = data.get("rows") or []
    result = [dict(zip(columns, row)) for row in rows]
    return result, observed.get("executed_sql")


def _key_of(row: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    return next((k for k in keys if row.get(k) is not None and row.get(k) != ""), None)


def _evidence_row(row: dict[str, Any], role: str) -> EvidenceRowModel:
    if role == "MGR":
        row = {key: value for key, value in row.items() if key in MGR_ALLOWED}
    ts_key, comp_key, sev_key = _key_of(row, TS_KEYS), _key_of(row, COMP_KEYS), _key_of(row, SEV_KEYS)
    raw_sev = row.get(sev_key) if sev_key else None
    line_key = _key_of(row, ("message", "body"))
    if line_key:
        body = _text(row[line_key])
    else:
        # Aggregate row: every value not already shown in the ts/component/severity columns.
        rest = {k: v for k, v in row.items() if k not in {ts_key, comp_key, sev_key} and v is not None}
        body = (_text(rest["summary"]) if set(rest) == {"summary"}
                else "; ".join(f"{k}: {_text(v)}" for k, v in rest.items()) or "Aggregate result")
    trace_key = None if role == "MGR" else _key_of(row, TRACE_KEYS)
    if trace_key:
        trace = _text(row[trace_key])
    elif line_key:
        trace = f"line {row['line_id']}" if row.get("line_id") is not None else "—"
    else:
        trace = "aggregate"
    return EvidenceRowModel(
        n=0,
        ts=_ts_text(row[ts_key]) if ts_key else "",
        trace=trace,
        sev=_severity(raw_sev),
        comp=_text(row[comp_key]) if comp_key else ("Aggregate" if role == "MGR" else "Unknown"),
        body=_sev_prefix(raw_sev) + body,
    )


# ---- grounding queries ------------------------------------------------------

@dataclass(frozen=True)
class GroundingQuery:
    title: str
    sql: str
    params: tuple[Any, ...] = ()
    # "corpus": one corpus-wide count; "lines": representative log lines; "rows": shown as returned.
    kind: str = "rows"
    # Run instead when this query returns no rows (e.g. the LLM looked at components with no warnings).
    fallback: "GroundingQuery | None" = None


@dataclass
class QueryRun:
    spec: GroundingQuery
    rows: list[dict[str, Any]] = field(default_factory=list)
    elapsed_ms: float | None = None
    error: str | None = None


def _window_clause(column: str, start: str | None, end: str | None) -> tuple[str, list[Any]]:
    # ISO strings cast in SQL: binary timestamp params fail on SereneDB.
    sql, params = "", []
    if start:
        sql += f" AND {column} >= %s::TIMESTAMP"
        params.append(start)
    if end:
        sql += f" AND {column} <= %s::TIMESTAMP"
        params.append(end)
    return sql, params


_ENG_LINES_SQL = """
WITH lines AS (
    SELECT line_id, event_time, request_id, severity, component, message,
           regexp_replace(message, '[0-9a-f]{{8}}[0-9a-f-]*', '<id>', 'g') AS template
    FROM openstack_logs
    WHERE severity IN ('CRITICAL', 'ERROR', 'WARNING') AND {component_filter}{window}
      AND message NOT LIKE 'Traceback%%' AND message NOT LIKE 'File "%%'
      -- Headline lines carry a request id; traceback bodies do not, except the exception line itself.
      AND (request_id IS NOT NULL
           OR (regexp_matches(message, '^[A-Z][A-Za-z]+: ')
               AND message NOT LIKE 'Command:%%' AND message NOT LIKE 'Stdout:%%'))
), ranked AS (
    SELECT *, COUNT(*) OVER (PARTITION BY component, severity, template) AS occurrences,
           ROW_NUMBER() OVER (PARTITION BY component, severity, template ORDER BY event_time, line_id) AS rn
    FROM lines
)
SELECT line_id, event_time, request_id, severity, component, message, occurrences
FROM ranked WHERE rn = 1
ORDER BY {severity_order}, occurrences DESC, event_time
LIMIT %s"""

_TOP_ERROR_COMPONENT = ("component = (SELECT component FROM openstack_logs WHERE severity IN ('ERROR', 'CRITICAL') "
                        "GROUP BY component ORDER BY COUNT(*) DESC LIMIT 1)")


def _components_in(rows: list[dict[str, Any]], limit: int = 5) -> list[str]:
    names = [r.get("component") for r in rows]
    return list(dict.fromkeys(n.strip() for n in names if isinstance(n, str) and n.strip()))[:limit]


def _grounding_queries(role: str, rows: list[dict[str, Any]], start: str | None = None,
                       end: str | None = None) -> list[GroundingQuery]:
    """Deterministic, role-scoped queries that ground the LLM-written query (see sql/05_row_level_security.sql)."""
    if role == "ENG":
        window, wparams = _window_clause("event_time", start, end)

        def lines(component_filter: str, cparams: list[Any], title: str, fallback=None) -> GroundingQuery:
            sql = _ENG_LINES_SQL.format(component_filter=component_filter, window=window,
                                        severity_order=SEVERITY_ORDER)
            return GroundingQuery(title, sql, (*cparams, *wparams, ENG_LINE_LIMIT), "lines", fallback)

        top = lines(_TOP_ERROR_COMPONENT, [], "Representative ERROR/WARNING lines, top error component")
        components = _components_in(rows)
        if components:
            placeholders = ", ".join(["%s"] * len(components))
            line_query = lines(f"component IN ({placeholders})", components,
                               f"Representative ERROR/WARNING lines, {', '.join(components)}", fallback=top)
        else:
            line_query = top
        return [
            GroundingQuery("Corpus-wide ERROR/CRITICAL count",
                           "SELECT 'OpenStack' AS dataset, COUNT(*) AS lines FROM openstack_logs\n"
                           "WHERE severity IN ('ERROR', 'CRITICAL')", (), "corpus"),
            line_query,
            GroundingQuery("Slowest timed operations",
                           "SELECT line_id, event_time, request_id, severity, component, operation,\n"
                           "       ROUND(duration_sec, 2) AS duration_sec, message\n"
                           f"FROM openstack_logs WHERE duration_sec IS NOT NULL{window}\n"
                           "ORDER BY duration_sec DESC, line_id LIMIT %s",
                           (*wparams, ENG_SLOWEST_LIMIT)),
        ]
    if role == "LEAD":
        window, wparams = _window_clause("window_start", start, end)
        return [
            GroundingQuery("Corpus-wide ERROR/CRITICAL count",
                           "SELECT 'OpenStack' AS dataset, COALESCE(SUM(log_lines), 0)::BIGINT AS lines\n"
                           "FROM v_team_lead_patterns WHERE dataset = 'OpenStack' AND severity IN ('ERROR', 'CRITICAL')",
                           (), "corpus"),
            GroundingQuery("Largest 30-minute windows per severity",
                           "SELECT window_start, component, severity, log_lines, anomaly_lines, team_name, escalation_tier\n"
                           "FROM (\n"
                           "    SELECT *, ROW_NUMBER() OVER (PARTITION BY severity ORDER BY log_lines DESC, window_start) AS rn\n"
                           "    FROM v_team_lead_patterns\n"
                           f"    WHERE dataset = 'OpenStack' AND severity IN ('CRITICAL', 'ERROR', 'WARNING'){window}\n"
                           ") t WHERE rn <= %s\n"
                           f"ORDER BY {SEVERITY_ORDER}, log_lines DESC, window_start LIMIT %s",
                           (*wparams, LEAD_WINDOWS_PER_SEVERITY, LEAD_WINDOW_LIMIT)),
        ]
    # MGR: v_manager_summary is corpus-wide and has no time column, so the window cannot apply.
    return [
        GroundingQuery("Corpus-wide ERROR/CRITICAL count",
                       "SELECT 'OpenStack' AS dataset, COALESCE(SUM(frequency), 0)::BIGINT AS lines\n"
                       "FROM v_manager_summary WHERE dataset = 'OpenStack' AND severity IN ('ERROR', 'CRITICAL')",
                       (), "corpus"),
        GroundingQuery("OpenStack summary by component and severity",
                       "SELECT component, severity, frequency, persistence_days,\n"
                       "       ROUND(p95_latency_sec, 3) AS p95_latency_sec, team_name, escalation_tier\n"
                       "FROM v_manager_summary WHERE dataset = 'OpenStack'\n"
                       f"ORDER BY {SEVERITY_ORDER}, frequency DESC LIMIT %s",
                       (MGR_ROW_LIMIT,)),
    ]


def _execute(conn: Any, spec: GroundingQuery) -> QueryRun:
    started = time.perf_counter()
    try:
        with conn.cursor() as cur:
            cur.execute(spec.sql, list(spec.params))
            rows = [dict(r) for r in cur.fetchall()]
    except Exception as exc:
        logger.exception("Grounding query failed: %s", spec.title)
        return QueryRun(spec, error=type(exc).__name__)
    return QueryRun(spec, rows, round((time.perf_counter() - started) * 1000, 1))


def run_grounding_queries(request: InvestigationRequest, llm_rows: list[dict[str, Any]]) -> list[QueryRun]:
    specs = _grounding_queries(request.role, llm_rows, request.time_window_start, request.time_window_end)
    runs = []
    with get_connection(dsn=role_dsn(ROLE_SLUGS[request.role])) as conn:
        for spec in specs:
            run = _execute(conn, spec)
            if not run.rows and run.error is None and spec.fallback is not None:
                run = _execute(conn, spec.fallback)
            runs.append(run)
    return runs


def _corpus_errors(runs: list[QueryRun]) -> int | None:
    for run in runs:
        if run.spec.kind == "corpus" and run.rows:
            return int(run.rows[0].get("lines") or 0)
    return None


def _shape(run: QueryRun) -> list[dict[str, Any]]:
    if run.spec.kind == "corpus":
        n = _corpus_errors([run])
        if n is None:
            return []
        return [{"dataset": "OpenStack", "summary": f"{n} ERROR/CRITICAL lines across the full OpenStack corpus "
                                                    "(all components, no time filter)"}]
    if run.spec.kind == "lines":
        shaped = []
        for row in run.rows:
            row = dict(row)
            seen = int(row.pop("occurrences", 1) or 1)
            if seen > 1:
                row["message"] = f"{row.get('message') or ''} (seen {seen} times)"
            shaped.append(row)
        return shaped
    return run.rows


def _build_evidence(role: str, llm_rows: list[dict[str, Any]], runs: list[QueryRun]) -> list[EvidenceRowModel]:
    """Number rows continuously across queries; `q` is the 1-based query card (Q1 is the LLM query)."""
    sources = [(1, llm_rows[:LLM_ROW_CAP])] + [(i, _shape(run)) for i, run in enumerate(runs, start=2)]
    evidence: list[EvidenceRowModel] = []
    for q, rows in sources:
        for row in rows:
            if len(evidence) >= EVIDENCE_CAP:
                return evidence
            evidence.append(_evidence_row(row, role).model_copy(update={"n": len(evidence) + 1, "q": q}))
    return evidence


def _display_sql(sql: str, params: tuple[Any, ...] | list[Any]) -> str:
    """The SQL as run, with its bound parameters shown inline (display only, never executed)."""
    text = textwrap.dedent(sql).strip()
    parts = text.split("%s")
    if len(parts) - 1 == len(params):
        literals = [str(p) if isinstance(p, (int, float)) else "'" + str(p).replace("'", "''") + "'" for p in params]
        text = parts[0] + "".join(lit + part for lit, part in zip(literals, parts[1:]))
    return text.replace("%%", "%")


def _meta(returned: int, shown: int, elapsed: float | None, error: str | None = None) -> str:
    if error:
        return f"failed ({error})"
    parts = [f"{returned} row{'s' if returned != 1 else ''}" + (f" ({shown} shown)" if shown != returned else "")]
    if elapsed is not None:
        parts.append(f"{elapsed:g} ms")
    return ", ".join(parts)


def _query_models(sql: str | None, result: dict[str, Any], llm_row_count: int, runs: list[QueryRun],
                  evidence: list[EvidenceRowModel], llm_error: str | None = None) -> list[ExecutedQueryModel]:
    shown = {q: sum(row.q == q for row in evidence) for q in range(1, len(runs) + 2)}
    elapsed = (result.get("efficiency_telemetry") or {}).get("sql_execution_ms")
    models = [ExecutedQueryModel(
        title="LLM-written SQL (guarded)",
        meta=_meta(llm_row_count, shown[1], elapsed, llm_error),
        sql=(sql or "").strip() or "-- The pipeline did not report the executed SQL.",
    )]
    for q, run in enumerate(runs, start=2):
        models.append(ExecutedQueryModel(title=run.spec.title, meta=_meta(len(run.rows), shown[q], run.elapsed_ms, run.error),
                                         sql=_display_sql(run.spec.sql, run.spec.params)))
    return models


# ---- stats and LLM content --------------------------------------------------

def _window_text(request: InvestigationRequest, prefix: str = "") -> str:
    if not (request.time_window_start or request.time_window_end):
        return "across the full corpus (no time filter)"
    return f"{prefix}{request.time_window_start or 'start'} to {request.time_window_end or 'end'}"


_ERROR_UNIT = {
    "ENG": "ERROR/CRITICAL log lines",
    "LEAD": "30-minute windows with ERROR/CRITICAL lines",
    "MGR": "component/severity groups at ERROR/CRITICAL",
}


def _stats(rows: list[EvidenceRowModel], request: InvestigationRequest, corpus_errors: int | None,
           query_count: int) -> list[StatModel]:
    role = request.role
    error_count = sum(row.sev == "ERROR" for row in rows)
    components = len({row.comp for row in rows if row.comp and row.comp not in {"OpenStack", "Aggregate", "Unknown"}})
    timestamps = sorted(row.ts for row in rows if row.ts)
    corpus_note = (f"corpus: {corpus_errors:,} ERROR/CRITICAL lines" if corpus_errors is not None
                   else "in returned evidence")
    if role == "MGR" or not timestamps:
        first = StatModel(
            label="Aggregate scope" if role == "MGR" else "First event",
            value="Corpus-wide" if role == "MGR" else "—",
            unit="", note="manager summary view" if role == "MGR" else "no timestamps in evidence",
            explain="The Manager database view returns corpus-wide aggregates without row-level timestamps."
            if role == "MGR" else "None of the returned evidence rows carries a timestamp.")
    else:
        first = StatModel(label="First event", value=_short_ts(timestamps[0]), unit="", note="earliest in evidence",
                          explain=f"Earliest timestamp among the evidence rows: {timestamps[0]}, "
                                  f"{_window_text(request, 'within the requested window ')}.")
    return [
        StatModel(label="Evidence rows", value=str(len(rows)), unit="",
                  note=f"from {query_count} quer{'y' if query_count == 1 else 'ies'}",
                  explain="Rows returned by the LLM-written query plus the role's grounding queries, "
                          f"capped at {EVIDENCE_CAP}. This is not a corpus-wide total."),
        StatModel(label="Errors shown", value=str(error_count), unit="", note=corpus_note,
                  explain=f"Evidence rows classified as ERROR ({_ERROR_UNIT[role]}). The note gives the "
                          "corpus-wide ERROR/CRITICAL line count from a separate query."),
        StatModel(label="Components", value=str(components), unit="", note="represented in evidence",
                  explain="Distinct components present in the evidence rows."),
        first,
    ]


def _agent_for(role: str) -> Agent[None, RoleContentResponse]:
    model = os.getenv("OPENAI_MODEL", "gpt-4o")
    if ":" not in model:
        model = f"openai:{model}"
    system_prompt = (
        "You are Polaris, a role-aware log investigation assistant. Return only the requested structured response. "
        "Use only the supplied database evidence. Every factual finding and hypothesis must cite one or more "
        "existing evidence row numbers in `cites`. Never invent counts, timings, causes, owners, or impacts. "
        "Keep recommendations actionable and avoid unsupported claims. " + ROLE_GUIDANCE[role]
    )
    return Agent(model=model, output_type=RoleContentResponse, system_prompt=system_prompt)


def _verified_findings(
    findings: list[FindingModel] | list[HypothesisModel],
    evidence: list[EvidenceRowModel],
    role: str,
) -> list[FindingModel] | list[HypothesisModel]:
    available = {row.n: row for row in evidence}
    ledger = {f"EV-{row.n:02d}": {"rows": [row.model_dump()]} for row in evidence}
    accepted = []
    for item in findings:
        cites = list(dict.fromkeys(n for n in item.cites if n in available))
        if not cites:
            continue
        citation_text = " ".join(f"[EV-{n:02d}]" for n in cites)
        report = verify_response_claims(f"{item.text} {citation_text}".strip(), ledger, ROLE_NAMES[role], mode="strip")
        if report.passed:
            accepted.append(item.model_copy(update={"cites": cites}))
    return accepted


async def _generate_content(request: InvestigationRequest, evidence: list[EvidenceRowModel],
                            stats: list[StatModel], queries: list[ExecutedQueryModel]) -> RoleContentResponse:
    evidence_payload = [row.model_dump() for row in evidence]
    query_titles = "; ".join(f"Q{i} {q.title} ({q.meta})" for i, q in enumerate(queries, start=1))
    user_prompt = (
        f"Role: {ROLE_NAMES[request.role]}\nQuestion: {request.question}\n"
        f"Time window: {_window_text(request)}\n"
        "Return an investigation title, a short subtitle, observed facts, hypotheses with likelihood tags, "
        "and recommended actions. Use evidence row numbers (`n`) as citations. If there is no evidence, say so plainly.\n"
        f"Evidence rows come from several queries (field `q`): {query_titles}. Q1 answers the question directly; "
        "the others are representative rows and corpus-wide counts that put it in context. Rows with trace "
        "'aggregate' are counts, not single log lines.\n"
        f"Evidence JSON:\n{json.dumps(evidence_payload, ensure_ascii=False, default=str)}"
    )
    generated = (await _agent_for(request.role).run(user_prompt)).output
    facts = _verified_findings(generated.facts, evidence, request.role)
    hyps = _verified_findings(generated.hyps, evidence, request.role)
    actions = [action for action in generated.actions if not any(char.isdigit() for char in action)]
    if not evidence:
        facts = [FindingModel(text="No database evidence rows were returned for this question and time window.", cites=[])]
        hyps = []
        actions = ["Broaden the time window or revise the question, then run the investigation again."]
    return RoleContentResponse(
        title=f"{ROLE_NAMES[request.role]} log investigation",
        sub=ROLE_SUBTITLES[request.role],
        stats=stats,
        facts=facts,
        hyps=hyps,
        actions=actions[:6],
    )


def _pipeline_call(request: InvestigationRequest) -> dict[str, Any]:
    return run_full_intelligence_pipeline(
        user_query=request.question,
        role=ROLE_SLUGS[request.role],
        time_window_start=request.time_window_start,
        time_window_end=request.time_window_end,
    )


async def investigate(request: InvestigationRequest) -> InvestigationResponse:
    from starlette.concurrency import run_in_threadpool

    # Fail before the pipeline spends an LLM call on SQL it could not run (raises RoleConfigError).
    role_dsn(ROLE_SLUGS[request.role])
    result = await run_in_threadpool(_pipeline_call, request)
    if result.get("status") not in {"success", "partial"}:
        raise RuntimeError(result.get("error") or "The investigation pipeline did not return evidence.")
    observed = result.get("observed_facts")
    llm_error = None
    if isinstance(observed, dict) and isinstance(observed.get("data"), dict):
        rows, sql = _extract_rows(observed)
    else:
        # The LLM's SQL failed (e.g. a Manager query on a raw table the role cannot read). The grounding
        # queries still answer from the role's own grants; Q1 is shown as failed rather than hidden.
        failure = next((e for e in result.get("errors") or [] if e.get("worker") == "structured"), None)
        if failure is None:
            raise RuntimeError("The investigation could not retrieve structured SereneDB evidence.")
        rows, sql = [], failure.get("sql")
        llm_error = (str(failure.get("error") or "query failed").strip().splitlines() or ["query failed"])[0][:160]
    runs = await run_in_threadpool(run_grounding_queries, request, rows)
    evidence = _build_evidence(request.role, rows, runs)
    queries = _query_models(sql, result, len(rows), runs, evidence, llm_error)
    stats = _stats(evidence, request, _corpus_errors(runs), len(queries))
    content = await _generate_content(request, evidence, stats, queries)
    return InvestigationResponse(content=content, evidence=evidence, queries=queries)


# ---- dashboard metrics ------------------------------------------------------

def _fetch(role: str, sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
    with get_connection(dsn=role_dsn(ROLE_SLUGS[role])) as conn, conn.cursor() as cur:
        cur.execute(sql, params or [])
        return [dict(r) for r in cur.fetchall()]


COMPONENT_LIMIT = 6

# Each role reads only what its database grants allow (sql/05_row_level_security.sql). Full corpus, no time filter.
_COMPONENT_SQL = {
    "ENG": """
        SELECT component,
               COUNT(*) FILTER (WHERE severity IN ('ERROR', 'CRITICAL')) AS errors,
               COUNT(*) FILTER (WHERE severity = 'WARNING') AS warnings
        FROM openstack_logs
        GROUP BY component ORDER BY errors DESC, warnings DESC, component LIMIT %s""",
    "LEAD": """
        SELECT component,
               COALESCE(SUM(log_lines) FILTER (WHERE severity IN ('ERROR', 'CRITICAL')), 0)::BIGINT AS errors,
               COALESCE(SUM(log_lines) FILTER (WHERE severity = 'WARNING'), 0)::BIGINT AS warnings
        FROM v_team_lead_patterns WHERE dataset = 'OpenStack'
        GROUP BY component ORDER BY errors DESC, warnings DESC, component LIMIT %s""",
    "MGR": """
        SELECT component,
               COALESCE(SUM(frequency) FILTER (WHERE severity IN ('ERROR', 'CRITICAL')), 0)::BIGINT AS errors,
               COALESCE(SUM(frequency) FILTER (WHERE severity = 'WARNING'), 0)::BIGINT AS warnings
        FROM v_manager_summary WHERE dataset = 'OpenStack'
        GROUP BY component ORDER BY errors DESC, warnings DESC, component LIMIT %s""",
}


def component_error_counts(role: str) -> list[ComponentErrorItem]:
    """Every OpenStack component with its ERROR/CRITICAL and WARNING lines, read through the role's own DSN."""
    rows = _fetch(role, _COMPONENT_SQL[role], [COMPONENT_LIMIT])
    total = sum(int(r["errors"] or 0) for r in rows)
    items = []
    for r in rows:
        errors, warnings = int(r["errors"] or 0), int(r["warnings"] or 0)
        share = f", {errors / total * 100:.0f}% of all ERROR/CRITICAL lines" if total and errors else ""
        items.append(ComponentErrorItem(
            name=str(r["component"]), n=errors, warnings=warnings,
            note=f"{errors} ERROR/CRITICAL and {warnings} WARNING lines across the full corpus{share}.",
        ))
    return items


ANOMALY_K = 3.0
MIN_HOURLY_SAMPLES = 100


def _mad_rule(values: list[float], k: float = ANOMALY_K) -> tuple[float, float]:
    """(baseline, threshold): median, and median + k * MAD, as in get_latency_outliers."""
    baseline = statistics.median(values)
    mad = statistics.median(abs(v - baseline) for v in values)
    return baseline, baseline + k * mad


def _series(rows: list[dict[str, Any]], digits: int) -> tuple[list[TimeseriesPoint], float | None, float | None, float | None]:
    values = [float(r["val"]) for r in rows]
    if not values:
        return [], None, None, None
    baseline, threshold = _mad_rule(values)
    points = [TimeseriesPoint(ts=r["ts"].isoformat() if hasattr(r["ts"], "isoformat") else str(r["ts"]),
                              val=round(float(r["val"]), digits), anomaly=float(r["val"]) > threshold)
              for r in rows]
    return points, round(baseline, digits), round(threshold, digits), round(max(values), digits)


def timeseries(role: str) -> TimeseriesResponse:
    if role == "ENG":
        rows = _fetch("ENG", """
            SELECT date_trunc('hour', event_time) AS ts, quantile_cont(latency_sec, 0.95) AS val, COUNT(*) AS samples
            FROM openstack_logs WHERE component = 'nova-api' AND latency_sec IS NOT NULL
            GROUP BY 1 ORDER BY 1""")
        kept = [r for r in rows if int(r["samples"]) >= MIN_HOURLY_SAMPLES]
        points, baseline, threshold, peak = _series(kept, 4)
        dropped = len(rows) - len(kept)
        note = (f"Hourly p95 of nova-api latency_sec over the full corpus. Baseline is the median hourly p95"
                f"{f' ({baseline:.3f} s)' if baseline is not None else ''}; hours above baseline + 3×MAD"
                f"{f' ({threshold:.3f} s)' if threshold is not None else ''} are flagged.")
        if dropped:
            note += f" {dropped} hour{'s' if dropped != 1 else ''} with fewer than {MIN_HOURLY_SAMPLES} requests left out."
        return TimeseriesResponse(title="Request latency, p95", unit="s", step_minutes=60, points=points,
                                  baseline=baseline, threshold=threshold, peak=peak, note=note)
    if role == "LEAD":
        rows = _fetch("LEAD", """
            SELECT window_start AS ts, SUM(log_lines)::BIGINT AS val FROM v_team_lead_patterns
            WHERE dataset = 'OpenStack' AND severity IN ('CRITICAL', 'ERROR', 'WARNING')
            GROUP BY 1 ORDER BY 1""")
        points, baseline, threshold, peak = _series(rows, 1)
        note = ("ERROR, CRITICAL and WARNING lines per 30-minute window from the team lead view. Baseline is the "
                "median window" + (f" ({baseline:g} lines); windows above baseline + 3×MAD ({threshold:g}) are flagged."
                                   if baseline is not None else "."))
        return TimeseriesResponse(title="ERROR + WARNING lines per 30 min", unit="lines", step_minutes=30,
                                  points=points, baseline=baseline, threshold=threshold, peak=peak, note=note)
    rows = _fetch("MGR", """
        SELECT component, severity, frequency, p95_latency_sec FROM v_manager_summary
        WHERE dataset = 'OpenStack' ORDER BY component, frequency DESC""")
    table: list[TimeseriesTableRow] = []
    for name in dict.fromkeys(r["component"] for r in rows):
        timed = [r for r in rows if r["component"] == name and r["p95_latency_sec"] is not None]
        if timed:
            best = timed[0]
            table.append(TimeseriesTableRow(name=name, val=round(float(best["p95_latency_sec"]), 3),
                                            note=f"p95 of the {best['severity']} group ({int(best['frequency'])} lines)"))
        else:
            table.append(TimeseriesTableRow(name=name, val=None, note="no latency recorded for this component"))
    vals = [t.val for t in table if t.val is not None]
    return TimeseriesResponse(
        title="Request latency, corpus-wide p95", unit="s", step_minutes=0, points=[],
        peak=max(vals) if vals else None, table=table,
        note="The manager view has no time column, so this shows one corpus-wide p95 per component instead of a time series.")


def alerts(role: str) -> list[AlertItem]:
    """Deterministic alerts from the role's own data source, most severe first."""
    if role == "ENG":
        windows = _fetch("ENG", f"""
            SELECT component, severity, {WINDOW_30M} AS window_start, COUNT(*) AS lines, MIN(event_time) AS first_seen
            FROM openstack_logs WHERE severity IN ('ERROR', 'CRITICAL')
            GROUP BY 1, 2, 3 ORDER BY first_seen""")
        criticals = _fetch("ENG", """
            SELECT event_time, component, message FROM openstack_logs
            WHERE severity = 'CRITICAL' ORDER BY event_time LIMIT 20""")
        items: list[AlertItem] = []
        for comp in dict.fromkeys(r["component"] for r in criticals):
            first = next(r for r in criticals if r["component"] == comp)
            count = sum(int(w["lines"]) for w in windows if w["component"] == comp and w["severity"] == "CRITICAL")
            message = str(first["message"] or "")
            items.append(AlertItem(
                sev="ERROR", title=f"{count} CRITICAL line{'s' if count != 1 else ''} on {comp}",
                detail=f'First: "{message[:100]}{"…" if len(message) > 100 else ""}"',
                time=_short_ts(first["event_time"]), q=f"What caused the CRITICAL errors on {comp}?"))
        error_windows = [w for w in windows if w["severity"] == "ERROR"]
        by_comp: dict[str, list[dict[str, Any]]] = {}
        for w in error_windows:
            by_comp.setdefault(w["component"], []).append(w)
        for comp, ws in sorted(by_comp.items(), key=lambda kv: -sum(int(w["lines"]) for w in kv[1])):
            total, peak = sum(int(w["lines"]) for w in ws), max(int(w["lines"]) for w in ws)
            spread = f"{total} ERROR lines across {len(ws)} half-hour window{'s' if len(ws) != 1 else ''}"
            items.append(AlertItem(
                sev="ERROR", title=f"ERROR bursts on {comp}" if peak > 1 else f"ERROR lines on {comp}",
                detail=f"{spread}, up to {peak} in one window." if peak > 1 else f"{spread}.",
                time=_short_ts(ws[0]["first_seen"]), q=f"What causes the ERROR lines on {comp}, and when do they recur?"))
        series = timeseries("ENG")
        if series.points and series.baseline:
            worst = max(series.points, key=lambda p: p.val)
            ratio = worst.val / series.baseline
            items.append(AlertItem(
                sev="WARN" if worst.anomaly else "INFO", title=f"Slowest hour: {worst.val:.3f} s p95",
                detail=f"{ratio:.2f}× the {series.baseline:.3f} s baseline (median hourly p95, nova-api)"
                       + (", above the 3×MAD threshold." if worst.anomaly else ", within the 3×MAD threshold."),
                time=_short_ts(worst.ts), q=f"Why was nova-api latency highest around {_short_ts(worst.ts)}?"))
        return items
    if role == "LEAD":
        rows = _fetch("LEAD", f"""
            SELECT window_start, component, severity, log_lines, team_name, escalation_tier
            FROM v_team_lead_patterns
            WHERE dataset = 'OpenStack' AND severity IN ('CRITICAL', 'ERROR', 'WARNING')
            ORDER BY {SEVERITY_ORDER}, log_lines DESC, window_start""")
        items, seen = [], set()
        for r in rows:
            key = (r["component"], r["severity"])
            if key in seen:
                continue
            seen.add(key)
            windows = sum(1 for x in rows if (x["component"], x["severity"]) == key)
            lines = int(r["log_lines"])
            items.append(AlertItem(
                sev=_severity(r["severity"]),
                title=f"{r['component']}: {lines} {r['severity']} line{'s' if lines != 1 else ''} in 30 min",
                detail=f"Largest of {windows} window{'s' if windows != 1 else ''} with {r['severity']} lines. "
                       f"Owner: {r['team_name'] or 'unassigned'}"
                       + (f" ({r['escalation_tier']})." if r["escalation_tier"] else "."),
                time=_short_ts(r["window_start"]),
                q=f"Why did {r['component']} log {r['severity']} lines in the window starting {_short_ts(r['window_start'])}?"))
            if len(items) == 3:
                break
        return items
    rows = _fetch("MGR", """
        SELECT component, severity, frequency, persistence_days, team_name, escalation_tier
        FROM v_manager_summary WHERE dataset = 'OpenStack'""")
    corpus_days = max((int(r["persistence_days"] or 0) for r in rows), default=0)
    summary: dict[str, dict[str, Any]] = {}
    for r in rows:
        s = summary.setdefault(r["component"], {"errors": 0, "warnings": 0, "days": 0,
                                                "team": r["team_name"], "tier": r["escalation_tier"]})
        if r["severity"] in {"ERROR", "CRITICAL"}:
            s["errors"] += int(r["frequency"])
            s["days"] = max(s["days"], int(r["persistence_days"] or 0))
        elif r["severity"] == "WARNING":
            s["warnings"] += int(r["frequency"])
    ranked = sorted(((c, s) for c, s in summary.items() if s["errors"]), key=lambda cs: (-cs[1]["errors"], -cs[1]["days"], cs[0]))
    return [AlertItem(
        sev="ERROR", title=f"{comp}: {s['errors']} ERROR/CRITICAL lines",
        detail=f"Seen on {s['days']} of {corpus_days} days with logs"
               + (f", plus {s['warnings']} WARNING lines" if s["warnings"] else "")
               + f". Owner: {s['team'] or 'unassigned'}" + (f" ({s['tier']})." if s["tier"] else "."),
        time="", q=f"How serious are the errors on {comp}, and who should act?")
        for comp, s in ranked]
