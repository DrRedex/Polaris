"""Offline tests for the integrated four-step pipeline; no model or public-network use."""
import numpy as np
import pytest

from src.guarded_sql_agent import SereneSQLAgent, UnsafeQueryException
from src.db import to_llm_compact_envelope, to_llm_evidence_view
from src.predictive_bottleneck import PredictiveBottleneckEngine


@pytest.mark.parametrize("sql", [
    "SELECT message FROM bgl_logs WHERE message='update'",
    'SELECT line_id AS "update" FROM bgl_logs',
    "WITH c AS (SELECT component FROM bgl_logs) SELECT * FROM c",
    "SELECT * FROM bgl_logs -- DROP TABLE bgl_logs",
])
def test_guarded_select_inputs(sql):
    assert "LIMIT 50" in SereneSQLAgent().apply_guardrails(sql)


@pytest.mark.parametrize("sql", [
    "SELECT 1 FROM bgl_logs; DELETE FROM bgl_logs",
    "WITH x AS (DELETE FROM bgl_logs RETURNING *) SELECT * FROM x",
    "SELECT * FROM bgl_logs LIMIT 1000",
])
def test_guard_rejects_or_caps_adversarial_sql(sql):
    if "1000" in sql:
        assert "LIMIT 50" in SereneSQLAgent().apply_guardrails(sql)
    else:
        with pytest.raises(UnsafeQueryException):
            SereneSQLAgent().apply_guardrails(sql)


def test_compact_envelope_truncates_and_folds_only_consecutive_identical_rows():
    rows = [{"line_id": 1, "message": "x" * 350}, {"line_id": 2, "message": "same"},
            {"line_id": 2, "message": "same"}, {"line_id": 1, "message": "x" * 350}]
    env = to_llm_compact_envelope(rows, deduplicate_consecutive=True)
    assert env["message_chars_truncated"] == 100
    assert env["deduplication"]["runs_folded"] == 1
    assert len(env["rows"]) == 3
    assert "message_truncated" in env["columns"]


def test_model_facing_envelope_is_columnar_and_keeps_sql_local():
    view = to_llm_evidence_view({"rows": [{"message": "x"*400, "count": 3}],
                                 "executed_sql": "SELECT secret FROM bgl_logs", "executed_queries": [{"sql": "SELECT ...", "label": "q"}]})
    assert set(view["rows"]["columns"]) == {"message", "message_truncated", "count"}
    assert len(view["rows"]["rows"][0][0]) == 300
    assert "executed_sql" not in view and "sql" not in view["executed_queries"][0]
    assert view["efficiency_telemetry"]["message_chars_truncated"] == 100


def test_schema_filter_uses_only_selected_live_table(monkeypatch):
    from src import semantic_engine as semantic
    monkeypatch.setattr(semantic._impl, "_encode", lambda _: np.array([1., 0.]))
    monkeypatch.setattr(semantic._impl, "get_glossary_matrix", lambda: np.tile(np.array([[1., 0.]]), (len(semantic._impl.SERENE_DB_GLOSSARY), 1)))
    rows = semantic.extract_relevant_schema_context("latency", 2, "OpenStack")
    assert rows and all(x["schema_match"]["table"] == "openstack_logs" for x in rows)
    assert semantic.extract_relevant_schema_context(" ", 2) == []
    assert semantic.extract_relevant_schema_context("x", 0) == []
    monkeypatch.setattr(semantic._impl, "_encode", lambda _: np.zeros((1, 2)))
    assert semantic.extract_relevant_schema_context("zero norm", 2) == []


def test_step_three_parallel_results_and_error_are_explicit(monkeypatch):
    from src import dual_engine
    class Model:
        def encode(self, texts, **kwargs):
            return np.array([[1., 0.] for _ in texts])
    monkeypatch.setattr(dual_engine, "get_embedding_model", lambda: Model())
    result = dual_engine.DualEnginePipeline().retrieve_all_evidence(
        "SELECT component FROM bgl_logs", "kernel", db_executor_fn=lambda sql: [{"component": "KERNEL"}])
    assert result["status"] == "success"
    assert result["structured"]["source_type"] == "OBSERVED_SERENEDB_FACT"
    assert result["runbook_hypotheses"][0]["source_type"] == "RUNBOOK_HYPOTHESIS_CLUE"
    failed = dual_engine.DualEnginePipeline().retrieve_all_evidence(
        "SELECT component FROM bgl_logs", "kernel", db_executor_fn=lambda sql: (_ for _ in ()).throw(RuntimeError("offline")))
    assert failed["errors"] and failed["status"] == "partial"


def test_bottleneck_thresholds_trends_and_provenance():
    engine = PredictiveBottleneckEngine()
    current = engine.analyze_saturation_bottlenecks({"cpu_utilization": 80, "connection_pool": 90,
                                                     "disk_io_queue": 21})
    assert [x["status"] for x in current] == ["WARNING", "CRITICAL", "CRITICAL"]
    trends = engine.analyze_degradation_trends({"latency": {"values": [1, 2, 3], "evidence_id": "E-test"}})
    assert trends[0]["projected_three_steps"] == pytest.approx(6)
    assert trends[0]["evidence_id"] == "E-test"
    assert not engine.analyze_degradation_trends({"bad": [1, float("nan")]})


def test_orchestrator_fails_without_sql_generator(monkeypatch):
    from src import pipeline_orchestrator as pipe
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(pipe, "extract_relevant_schema_context", lambda *a, **kw: [])
    result = pipe.run_full_intelligence_pipeline("count errors", "Engineer")
    assert result["status"] == "error"
    assert "not configured" in result["error"]


def test_orchestrator_runs_with_explicit_sql_and_injected_executor(monkeypatch):
    from src import pipeline_orchestrator as pipe, dual_engine
    monkeypatch.setattr(pipe, "extract_relevant_schema_context", lambda *a, **kw: [])
    class Model:
        def encode(self, texts, **kwargs): return np.array([[1., 0.] for _ in texts])
    monkeypatch.setattr(dual_engine, "get_embedding_model", lambda: Model())
    result = pipe.run_full_intelligence_pipeline("count kernel logs", "Engineer",
        db_executor_fn=lambda sql: [{"component": "KERNEL", "count": 2}],
        candidate_sql="SELECT component, COUNT(*) AS count FROM bgl_logs GROUP BY component")
    assert result["status"] == "success"
    assert result["observed_facts"]["source_type"] == "OBSERVED_SERENEDB_FACT"
    assert result["efficiency_telemetry"]["rows_returned"] == 1


def test_manager_summary_does_not_include_rows_or_traces():
    from src.dual_engine import generate_management_summary
    combined = {"status": "success", "structured": {"evidence_id": "E-x", "data": {"rows": [["raw"]]}},
                "runbook_hypotheses": []}
    result = generate_management_summary("overview", combined, "manager")
    assert result["observed_facts"] == {"evidence_id": "E-x"}


def test_requested_mcp_tools_are_registered_with_typed_schemas():
    import asyncio
    from src.mcp_server import mcp
    tools = {tool.name: tool for tool in asyncio.run(mcp.list_tools())}
    required = {"run_dual_engine_investigation", "run_proactive_bottleneck_audit", "create_time_range_index",
                "drop_time_range_index", "query_otel_scale_summary", "correlate_otel_trace", "get_30min_burst_summary"}
    assert required <= set(tools)
    assert "num_partitions" in tools["query_otel_scale_summary"].input_schema["properties"]


def test_remote_and_workspace_validation_happens_before_database_access():
    from src.extensions import correlate_otel_trace, query_otel_scale_summary
    from src.workspace_indexing import create_time_range_index
    with pytest.raises(ValueError, match="1-50"):
        query_otel_scale_summary(0)
    with pytest.raises(ValueError, match="trace_id"):
        correlate_otel_trace("not a trace id")
    with pytest.raises(ValueError, match="dataset"):
        create_time_range_index("other", "2026-01-02", "2026-01-03", "ws_test")


@pytest.mark.serenedb
def test_manager_role_raw_access_is_denied_by_database():
    import os, psycopg
    from src.db import role_dsn
    if not os.getenv("SERENEDB_MANAGER_DSN"):
        pytest.skip("SERENEDB_MANAGER_DSN is not configured; role denial is unverified")
    with psycopg.connect(role_dsn("manager"), autocommit=True) as conn:
        with pytest.raises(psycopg.Error):
            conn.execute("SELECT message FROM bgl_logs LIMIT 1").fetchall()
