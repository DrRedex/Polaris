"""Four-step pipeline coordinator with explicit SQL generation and failure states."""
from __future__ import annotations

import os
import time
from typing import Any

import sqlglot
from sqlglot import exp

from src.dual_engine import DualEnginePipeline, generate_management_summary
from src.guarded_sql_agent import SereneSQLAgent, generate_candidate_sql
from src.predictive_bottleneck import PredictiveBottleneckEngine
from src.semantic_engine import extract_relevant_schema_context
from src.db import EvidenceBuilder, role_dsn


def run_full_intelligence_pipeline(user_query: str, role: str, db_executor_fn=None,
                                   candidate_sql: str | None = None,
                                   time_window_start: str | None = None,
                                   time_window_end: str | None = None) -> dict[str, Any]:
    started = time.perf_counter()
    role = role.lower().strip()
    if role not in {"engineer", "team lead", "manager", "team_lead"}:
        return {"status": "error", "error": "role must be Engineer, Team Lead, or Manager", "role": role}
    normalized_role = "team_lead" if role in {"team lead", "team_lead"} else role
    try:
        contexts = extract_relevant_schema_context(user_query, k=4)
        if normalized_role == "manager":
            columns = [("dataset", "VARCHAR"), ("component", "VARCHAR"), ("severity", "VARCHAR"),
                       ("frequency", "BIGINT"), ("persistence_days", "BIGINT"),
                       ("avg_latency_sec", "DOUBLE"), ("p95_latency_sec", "DOUBLE"),
                       ("team_name", "VARCHAR"), ("escalation_tier", "VARCHAR")]
            contexts = [{"confidence_score": None, "match_type": "role_policy_context",
                         "schema_match": {"table": "v_manager_summary", "column": col, "type": typ,
                                          "description": "Database-enforced aggregated Manager view.",
                                          "recommended_mcp_tool": "run_dual_engine_investigation"}}
                        for col, typ in columns]
        elif normalized_role == "team_lead":
            columns = [("dataset", "VARCHAR"), ("component", "VARCHAR"), ("severity", "VARCHAR"),
                       ("window_start", "TIMESTAMP"), ("log_lines", "BIGINT"), ("anomaly_lines", "BIGINT"),
                       ("first_seen", "TIMESTAMP"),
                       ("last_seen", "TIMESTAMP"), ("team_name", "VARCHAR"), ("escalation_tier", "VARCHAR")]
            contexts = [{"confidence_score": None, "match_type": "role_policy_context",
                         "schema_match": {"table": "v_team_lead_patterns", "column": col, "type": typ,
                                          "description": "Database-enforced Team Lead pattern view.",
                                          "recommended_mcp_tool": "run_dual_engine_investigation"}}
                        for col, typ in columns]
        agent = SereneSQLAgent()
        prompt = agent.assemble_prompt(user_query, contexts)
        sql = candidate_sql
        if sql is None:
            if not os.getenv("OPENAI_API_KEY"):
                raise RuntimeError("SQL generation is not configured. Set OPENAI_API_KEY or pass candidate_sql explicitly.")
            sql = generate_candidate_sql(prompt)
        secured = agent.apply_guardrails(sql, max_limit=50)
        if time_window_start or time_window_end:
            secured = _apply_time_window(secured, time_window_start, time_window_end)
        if db_executor_fn is None:
            def db_executor_fn(statement):
                dsn = role_dsn(normalized_role)
                with EvidenceBuilder("pipeline_structured", dsn=dsn) as eb:
                    rows = eb.query("validated role-scoped SQL", statement)
                    return {"rows": rows, "sql_execution_ms": eb.queries[-1].execution_time_ms,
                            "executed_sql": eb.queries[-1].sql}
        evidence = DualEnginePipeline().retrieve_all_evidence(secured, user_query, db_executor_fn=db_executor_fn)
        summary = generate_management_summary(user_query, evidence, normalized_role)
        audit = PredictiveBottleneckEngine().run_proactive_audit({}, {},
                    evidence.get("structured", {}).get("evidence_id") if evidence.get("structured") else None)
        observed = evidence.get("structured")
        if normalized_role == "manager" and observed:
            # Preserve only approved aggregate fields for the Manager API/UI. Raw rows,
            # request IDs, instance IDs, and message bodies remain unavailable.
            data = observed.get("data") or {}
            safe_columns = {
                "dataset", "component", "severity", "frequency", "persistence_days",
                "avg_latency_sec", "p95_latency_sec", "team_name", "escalation_tier",
                "window_start", "log_lines", "anomaly_lines", "first_seen", "last_seen",
            }
            columns = [column for column in data.get("columns", []) if column in safe_columns]
            column_indexes = [data.get("columns", []).index(column) for column in columns]
            safe_rows = [[row[index] for index in column_indexes] for row in data.get("rows", [])]
            observed = {
                key: value for key, value in observed.items() if key != "data"
            }
            observed["data"] = {"columns": columns, "rows": safe_rows}
        return {"status": evidence["status"], "role": normalized_role, "user_query": user_query,
                "schema_context": contexts, "summary": summary, "observed_facts": observed,
                "runbook_hypotheses": evidence.get("runbook_hypotheses", []), "predictive_risks": audit,
                "efficiency_telemetry": {"client_elapsed_ms": round((time.perf_counter()-started)*1000, 2),
                                          "sql_execution_ms": evidence.get("structured", {}).get("sql_execution_ms") if evidence.get("structured") else None,
                                          "sql_timing_kind": "client round-trip execution time" if (evidence.get("structured") or {}).get("sql_execution_ms") is not None else "unavailable",
                                          "rows_scanned": None,
                                          "rows_scanned_unavailable_reason": "SereneDB driver does not expose reliable scan counts in this path.",
                                          "rows_returned": len(evidence.get("structured", {}).get("data", {}).get("rows", [])) if evidence.get("structured") else 0,
                                          "message_chars_truncated": evidence.get("structured", {}).get("data", {}).get("message_chars_truncated", 0) if evidence.get("structured") else 0,
                                          "deduplication": evidence.get("structured", {}).get("data", {}).get("deduplication", {"enabled": False, "runs_folded": 0}) if evidence.get("structured") else {"enabled": False, "runs_folded": 0},
                                          "compression_ratio": None,
                                          "compression_ratio_unavailable_reason": "Reliable rows_scanned is unavailable."},
                "errors": evidence.get("errors", []), "forecast_note": "No current/historical metrics were supplied; no forecasts inferred."}
    except Exception as exc:
        return {"status": "error", "role": normalized_role, "user_query": user_query,
                "error": str(exc), "observed_facts": None, "runbook_hypotheses": [],
                "efficiency_telemetry": {"client_elapsed_ms": round((time.perf_counter()-started)*1000, 2)}}


def _apply_time_window(sql: str, start: str | None, end: str | None) -> str:
    """Add validated timestamp bounds to every permitted time-bearing relation."""
    tree = sqlglot.parse_one(sql, read="duckdb")
    predicates = []
    for table in tree.find_all(exp.Table):
        relation = table.name.lower()
        if relation in {"bgl_logs", "openstack_logs"}:
            time_column = "event_time"
        elif relation == "v_team_lead_patterns":
            time_column = "window_start"
        else:
            # v_manager_summary is intentionally a corpus-wide aggregate view and has no
            # timestamp column. Its allowed aggregate output remains scope-safe.
            continue
        column = exp.column(time_column, table=table.alias_or_name)
        if start:
            low = exp.Cast(this=exp.Literal.string(start), to=exp.DataType.build("TIMESTAMP"))
            predicates.append(exp.GTE(this=column.copy(), expression=low))
        if end:
            high = exp.Cast(this=exp.Literal.string(end), to=exp.DataType.build("TIMESTAMP"))
            predicates.append(exp.LTE(this=column.copy(), expression=high))
    if not predicates:
        return sql
    condition = predicates[0]
    for predicate in predicates[1:]:
        condition = exp.and_(condition, predicate)
    where = tree.args.get("where")
    if where is not None:
        condition = exp.and_(where.this, condition)
    tree.set("where", exp.Where(this=condition))
    return tree.sql(dialect="duckdb")
