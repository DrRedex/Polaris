"""Evidence, grounding queries, timeseries and alerts for the Polaris API (stubbed DB and pipeline, no LLM)."""

from contextlib import contextmanager
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

import src.api.main as api_main
import src.api.service as service
from src.api.schemas import InvestigationRequest, RoleContentResponse
from src.dual_engine import DualEnginePipeline


@pytest.fixture
def client():
    # Not used as a context manager, so the SereneDB lifespan health check does not run.
    return TestClient(api_main.app)


# ---- executed_sql pass-through ---------------------------------------------

@pytest.mark.parametrize("executor_result,expected", [
    ({"rows": [{"n": 1}], "sql_execution_ms": 2.0, "executed_sql": "SELECT 1 AS n LIMIT 50"}, "SELECT 1 AS n LIMIT 50"),
    ([{"n": 1}], "SELECT 1 AS n"),  # executor without metadata: fall back to the secured SQL
])
def test_dual_engine_keeps_executed_sql(monkeypatch, executor_result, expected):
    monkeypatch.setattr(DualEnginePipeline, "_execute_unstructured_knn", lambda self, anchor, k=2: [])
    result = DualEnginePipeline().retrieve_all_evidence("SELECT 1 AS n", "q", db_executor_fn=lambda sql: executor_result)
    assert result["structured"]["executed_sql"] == expected


# ---- grounding query selection ---------------------------------------------

RAW_TABLES = ("openstack_logs", "bgl_logs")


def test_eng_grounding_uses_llm_components_with_top_component_fallback():
    specs = service._grounding_queries("ENG", [{"component": "nova-api"}, {"component": "nova-api"}, {"component": None}])
    corpus, lines, slowest = specs
    assert corpus.kind == "corpus" and "openstack_logs" in corpus.sql
    assert "component IN (%s)" in lines.sql
    assert lines.params == ("nova-api", service.ENG_LINE_LIMIT)
    assert lines.fallback is not None and "ORDER BY COUNT(*) DESC LIMIT 1" in lines.fallback.sql
    assert "Traceback" in lines.sql and 'File "' in lines.sql
    assert "duration_sec DESC" in slowest.sql and slowest.params == (service.ENG_SLOWEST_LIMIT,)


def test_eng_grounding_without_components_goes_straight_to_top_component():
    _, lines, _ = service._grounding_queries("ENG", [{"avg_latency": 0.3}])
    assert "ORDER BY COUNT(*) DESC LIMIT 1" in lines.sql and lines.fallback is None


def test_eng_grounding_applies_time_window_as_iso_strings():
    _, lines, slowest = service._grounding_queries("ENG", [], "2017-05-16T00:00:00", "2017-05-17T00:00:00")
    assert "event_time >= %s::TIMESTAMP" in lines.sql
    assert lines.params == ("2017-05-16T00:00:00", "2017-05-17T00:00:00", service.ENG_LINE_LIMIT)
    assert slowest.params[:2] == ("2017-05-16T00:00:00", "2017-05-17T00:00:00")


@pytest.mark.parametrize("role,source", [("LEAD", "v_team_lead_patterns"), ("MGR", "v_manager_summary")])
def test_lead_and_manager_grounding_stay_on_their_views(role, source):
    specs = service._grounding_queries(role, [{"component": "nova-api"}], "2017-05-16T00:00:00", None)
    assert specs and all(source in spec.sql for spec in specs)
    assert not any(table in spec.sql for spec in specs for table in RAW_TABLES)
    assert any("window_start >= %s::TIMESTAMP" in spec.sql for spec in specs) == (role == "LEAD")


class _Cursor:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params):
        self.conn.log.append((sql, params))

    def fetchall(self):
        return self.conn.responses.pop(0)


class _Conn:
    def __init__(self, responses):
        self.responses, self.log = list(responses), []

    def cursor(self):
        return _Cursor(self)


def test_run_grounding_falls_back_when_llm_components_have_no_lines(monkeypatch):
    conn = _Conn([[{"dataset": "OpenStack", "lines": 196}], [], [{"message": "boom", "occurrences": 2}], []])
    dsns = []

    @contextmanager
    def fake_conn(dsn=None):
        dsns.append(dsn)
        yield conn

    monkeypatch.setattr(service, "get_connection", fake_conn)
    monkeypatch.setattr(service, "role_dsn", lambda slug: f"dsn-for-{slug}")
    runs = service.run_grounding_queries(InvestigationRequest(role="ENG", question="q"), [{"component": "KERNEL"}])
    assert dsns == ["dsn-for-engineer"]
    assert len(runs) == 3 and runs[1].rows == [{"message": "boom", "occurrences": 2}]
    assert runs[1].spec.fallback is None and "ORDER BY COUNT(*) DESC LIMIT 1" in runs[1].spec.sql
    assert len(conn.log) == 4  # corpus, lines (empty), fallback lines, slowest


def test_failed_grounding_query_is_reported_not_raised():
    class Broken:
        def cursor(self):
            raise RuntimeError("permission denied for table openstack_logs")

    run = service._execute(Broken(), service.GroundingQuery("t", "SELECT 1"))
    assert run.rows == [] and run.error == "RuntimeError"
    assert service._meta(0, 0, None, run.error) == "failed (RuntimeError)"


# ---- evidence numbering -----------------------------------------------------

def _run(kind, rows, title="g", sql="SELECT %s", params=(5,)):
    return service.QueryRun(service.GroundingQuery(title, sql, params, kind), rows, 4.2)


def test_evidence_is_numbered_across_queries_with_q():
    llm = [{"component": "nova-api", "avg_latency": 0.25}] * 8
    runs = [
        _run("corpus", [{"dataset": "OpenStack", "lines": 196}]),
        _run("lines", [{"line_id": 7, "event_time": datetime(2017, 5, 16, 3, 19, 45, 356000), "request_id": None,
                        "severity": "CRITICAL", "component": "nova-api", "message": "Unable to validate token",
                        "occurrences": 2}]),
        _run("rows", [{"window_start": datetime(2017, 5, 16, 6, 0), "component": "nova-api", "severity": "ERROR",
                       "log_lines": 1}]),
    ]
    evidence = service._build_evidence("ENG", llm, runs)
    assert [r.n for r in evidence] == list(range(1, 10))
    assert [r.q for r in evidence] == [1] * service.LLM_ROW_CAP + [2, 3, 4]
    assert evidence[0].trace == "aggregate" and evidence[0].body == "avg_latency: 0.25"
    corpus, line, window = evidence[6:]
    assert corpus.trace == "aggregate" and "196 ERROR/CRITICAL lines" in corpus.body
    assert line.ts == "2017-05-16 03:19:45.356" and line.trace == "line 7" and line.sev == "ERROR"
    assert line.body == "[CRITICAL] Unable to validate token (seen 2 times)"
    assert window.ts == "2017-05-16 06:00:00.000" and window.trace == "aggregate" and window.body == "log_lines: 1"


def test_evidence_is_capped():
    runs = [_run("rows", [{"component": "c", "lines": i} for i in range(30)])]
    evidence = service._build_evidence("ENG", [], runs)
    assert len(evidence) == service.EVIDENCE_CAP and evidence[-1].n == service.EVIDENCE_CAP


def test_manager_evidence_never_carries_raw_fields():
    llm = [{"component": "nova-api", "frequency": 5, "message": "secret", "request_id": "req-1"}]
    (row,) = service._build_evidence("MGR", llm, [])
    assert row.trace == "aggregate" and "secret" not in row.body and "req-1" not in row.body


def test_pipeline_timestamps_are_normalised():
    (row,) = service._build_evidence("ENG", [{"event_time": "2017-05-16T03:19:45.356000", "message": "m"}], [])
    assert row.ts == "2017-05-16 03:19:45.356" and row.trace == "—"


def test_query_models_list_llm_query_first_then_grounding():
    runs = [_run("rows", [{"a": 1}, {"a": 2}], title="Slowest", sql="SELECT a FROM t WHERE c = %s AND d LIKE 'x%%' LIMIT %s",
                 params=("nova'api", 2))]
    evidence = service._build_evidence("ENG", [{"x": i} for i in range(8)], runs)
    queries = service._query_models("SELECT x FROM t LIMIT 50", {"efficiency_telemetry": {"sql_execution_ms": 3.5}},
                                    8, runs, evidence)
    assert [q.title for q in queries] == ["LLM-written SQL (guarded)", "Slowest"]
    assert queries[0].sql == "SELECT x FROM t LIMIT 50" and queries[0].meta == "8 rows (6 shown), 3.5 ms"
    assert queries[1].sql == "SELECT a FROM t WHERE c = 'nova''api' AND d LIKE 'x%' LIMIT 2"
    assert queries[1].meta == "2 rows, 4.2 ms"


def test_stats_use_evidence_and_corpus_count():
    runs = [_run("corpus", [{"lines": 196}]),
            _run("lines", [{"event_time": datetime(2017, 5, 16, 3, 19), "severity": "ERROR", "component": "nova-compute",
                            "message": "e"},
                           {"event_time": datetime(2017, 5, 14, 19, 39), "severity": "WARNING", "component": "nova-compute",
                            "message": "w"}])]
    evidence = service._build_evidence("ENG", [], runs)
    stats = {s.label: s for s in service._stats(evidence, InvestigationRequest(role="ENG", question="q"), 196, 3)}
    assert stats["Errors shown"].value == "1" and "196" in stats["Errors shown"].note
    assert stats["First event"].value == "05-14 19:39"
    assert stats["Components"].value == "1"
    mgr = {s.label: s for s in service._stats(evidence, InvestigationRequest(role="MGR", question="q"), 196, 3)}
    assert mgr["Aggregate scope"].value == "Corpus-wide"


def test_investigate_returns_sql_and_grounded_evidence(monkeypatch):
    monkeypatch.setenv("SERENEDB_ENGINEER_DSN", "postgresql://x")
    monkeypatch.setattr(service, "_pipeline_call", lambda request: {
        "status": "success",
        "observed_facts": {"executed_sql": "SELECT component FROM openstack_logs LIMIT 50",
                           "data": {"columns": ["component", "errors"], "rows": [["nova-compute", 192]]}},
        "efficiency_telemetry": {"sql_execution_ms": 5.0},
    })
    monkeypatch.setattr(service, "run_grounding_queries", lambda request, rows: [
        _run("corpus", [{"lines": 196}]),
        _run("lines", [{"event_time": datetime(2017, 5, 16, 3, 19), "request_id": "req-1", "severity": "ERROR",
                        "component": "nova-compute", "message": "Error during ComputeManager._run_image_cache_manager_pass"}]),
    ])

    class FakeAgent:
        async def run(self, prompt):
            assert "req-1" in prompt  # the LLM sees the grounding rows
            return type("R", (), {"output": RoleContentResponse(
                title="t", sub="s", stats=[], hyps=[], actions=["Check the image cache"],
                facts=[{"text": "nova-compute logged 192 ERROR lines.", "cites": [1]},
                       {"text": "The image cache pass failed.", "cites": [3]}])})()

    monkeypatch.setattr(service, "_agent_for", lambda role: FakeAgent())
    res = TestClient(api_main.app).post("/api/investigate", json={"role": "ENG", "question": "Why?"})
    assert res.status_code == 200
    body = res.json()
    assert body["queries"][0]["sql"] == "SELECT component FROM openstack_logs LIMIT 50"
    assert len(body["queries"]) == 3
    assert [(r["n"], r["q"]) for r in body["evidence"]] == [(1, 1), (2, 2), (3, 3)]
    assert body["evidence"][2]["trace"] == "req-1" and body["evidence"][2]["ts"].startswith("2017-05-16 03:19")
    assert [f["cites"] for f in body["content"]["facts"]] == [[1], [3]]


def test_failed_llm_query_still_answers_from_grounding(monkeypatch):
    monkeypatch.setenv("SERENEDB_MANAGER_DSN", "postgresql://x")
    monkeypatch.setattr(service, "_pipeline_call", lambda request: {
        "status": "partial", "observed_facts": None,
        "errors": [{"worker": "structured", "sql": "SELECT component FROM bgl_logs LIMIT 50",
                    "error": "permission denied for table bgl_logs\nLINE 1: ..."}],
    })
    monkeypatch.setattr(service, "run_grounding_queries", lambda request, rows: [_run("corpus", [{"lines": 196}])])

    class FakeAgent:
        async def run(self, prompt):
            assert "failed (permission denied for table bgl_logs)" in prompt
            return type("R", (), {"output": RoleContentResponse(title="t", sub="s", stats=[], facts=[], hyps=[], actions=[])})()

    monkeypatch.setattr(service, "_agent_for", lambda role: FakeAgent())
    res = TestClient(api_main.app).post("/api/investigate", json={"role": "MGR", "question": "Why?"})
    assert res.status_code == 200
    q1 = res.json()["queries"][0]
    assert q1["meta"] == "failed (permission denied for table bgl_logs)"
    assert q1["sql"] == "SELECT component FROM bgl_logs LIMIT 50"
    assert [r["q"] for r in res.json()["evidence"]] == [2]


def test_pipeline_reports_sql_failure_instead_of_crashing(monkeypatch):
    from src import pipeline_orchestrator as po

    monkeypatch.setattr(DualEnginePipeline, "_execute_unstructured_knn", lambda self, anchor, k=2: [{"id": "x", "similarity": 0.5}])
    monkeypatch.setattr(po, "extract_relevant_schema_context", lambda query, k=4: [])

    def denied(sql):
        raise RuntimeError("permission denied for table bgl_logs")

    result = po.run_full_intelligence_pipeline("q", "manager", db_executor_fn=denied,
                                               candidate_sql="SELECT component FROM v_manager_summary")
    assert result["status"] == "partial" and result["observed_facts"] is None
    assert result["errors"][0]["error"] == "permission denied for table bgl_logs"
    assert "v_manager_summary" in result["errors"][0]["sql"]


# ---- timeseries -------------------------------------------------------------

def _hours(values, samples=1000):
    start = datetime(2017, 5, 16, 0, 0)
    return [{"ts": start + timedelta(hours=i), "val": v, "samples": samples} for i, v in enumerate(values)]


def test_eng_timeseries_flags_points_above_median_plus_3_mad(monkeypatch):
    rows = _hours([0.38, 0.37, 0.39, 0.38, 0.38, 0.80]) + [{"ts": datetime(2017, 5, 17, 7), "val": 2.0, "samples": 12}]
    monkeypatch.setattr(service, "_fetch", lambda role, sql, params=None: rows)
    ts = service.timeseries("ENG")
    # median 0.38, MAD 0.005 -> threshold 0.395; the 12-sample hour (2.0 s) is left out entirely.
    assert ts.baseline == 0.38 and ts.threshold == 0.395 and ts.peak == 0.8
    assert [p.anomaly for p in ts.points] == [False, False, False, False, False, True]
    assert len(ts.points) == 6 and "1 hour with fewer than 100 requests" in ts.note
    assert ts.points[0].ts == "2017-05-16T00:00:00" and ts.step_minutes == 60


def test_lead_timeseries_reads_the_team_lead_view(monkeypatch):
    seen = []
    monkeypatch.setattr(service, "_fetch", lambda role, sql, params=None: seen.append((role, sql)) or
                        [{"ts": datetime(2017, 5, 16, 0, 30 * i % 60), "val": v} for i, v in enumerate([60, 70, 64, 200])])
    ts = service.timeseries("LEAD")
    assert seen[0][0] == "LEAD" and "v_team_lead_patterns" in seen[0][1]
    assert [p.anomaly for p in ts.points] == [False, False, False, True] and ts.step_minutes == 30


def test_manager_timeseries_is_a_table(monkeypatch):
    monkeypatch.setattr(service, "_fetch", lambda role, sql, params=None: [
        {"component": "nova-api", "severity": "INFO", "frequency": 100, "p95_latency_sec": 0.3818},
        {"component": "nova-api", "severity": "ERROR", "frequency": 2, "p95_latency_sec": None},
        {"component": "nova-compute", "severity": "INFO", "frequency": 90, "p95_latency_sec": None},
    ])
    ts = service.timeseries("MGR")
    assert ts.points == [] and "no time column" in ts.note
    assert [(t.name, t.val) for t in ts.table] == [("nova-api", 0.382), ("nova-compute", None)]


# ---- alerts -----------------------------------------------------------------

def test_eng_alerts_rank_critical_errors_then_latency(monkeypatch):
    def fake_fetch(role, sql, params=None):
        if "latency_sec" in sql:
            return _hours([0.38, 0.38, 0.38, 0.50])
        if "severity = 'CRITICAL'" in sql:
            return [{"event_time": datetime(2017, 5, 16, 6, 25), "component": "nova-api", "message": "Unable to validate token"}]
        return [
            {"component": "nova-compute", "severity": "ERROR", "window_start": None, "lines": 24,
             "first_seen": datetime(2017, 5, 16, 3, 19)},
            {"component": "nova-compute", "severity": "ERROR", "window_start": None, "lines": 24,
             "first_seen": datetime(2017, 5, 16, 22, 16)},
            {"component": "nova-api", "severity": "CRITICAL", "window_start": None, "lines": 2,
             "first_seen": datetime(2017, 5, 16, 6, 25)},
        ]

    monkeypatch.setattr(service, "_fetch", fake_fetch)
    items = service.alerts("ENG")
    assert [a.title for a in items] == ["2 CRITICAL lines on nova-api", "ERROR bursts on nova-compute",
                                        "Slowest hour: 0.500 s p95"]
    assert items[1].detail == "48 ERROR lines across 2 half-hour windows, up to 24 in one window."
    assert items[1].time == "05-16 03:19"
    assert items[2].sev == "WARN" and "1.32×" in items[2].detail
    assert all(a.q for a in items)


def test_lead_alerts_pick_top_window_per_component_and_severity(monkeypatch):
    rows = [
        {"window_start": datetime(2017, 5, 16, 6), "component": "nova-api", "severity": "CRITICAL", "log_lines": 1,
         "team_name": "API", "escalation_tier": "Tier-1"},
        {"window_start": datetime(2017, 5, 16, 3), "component": "nova-compute", "severity": "ERROR", "log_lines": 24,
         "team_name": "Compute", "escalation_tier": None},
        {"window_start": datetime(2017, 5, 16, 4), "component": "nova-compute", "severity": "ERROR", "log_lines": 20,
         "team_name": "Compute", "escalation_tier": None},
        {"window_start": datetime(2017, 5, 16, 21, 30), "component": "nova-compute", "severity": "WARNING",
         "log_lines": 73, "team_name": None, "escalation_tier": None},
        {"window_start": datetime(2017, 5, 16, 1), "component": "nova-api", "severity": "WARNING", "log_lines": 2,
         "team_name": "API", "escalation_tier": "Tier-1"},
    ]
    monkeypatch.setattr(service, "_fetch", lambda role, sql, params=None: rows)
    items = service.alerts("LEAD")
    assert [a.title for a in items] == ["nova-api: 1 CRITICAL line in 30 min", "nova-compute: 24 ERROR lines in 30 min",
                                        "nova-compute: 73 WARNING lines in 30 min"]
    assert [a.sev for a in items] == ["ERROR", "ERROR", "WARN"]
    assert "Largest of 2 windows" in items[1].detail and "unassigned" in items[2].detail


def test_manager_alerts_rank_components_by_errors(monkeypatch):
    monkeypatch.setattr(service, "_fetch", lambda role, sql, params=None: [
        {"component": "nova-api", "severity": "ERROR", "frequency": 2, "persistence_days": 2, "team_name": "API",
         "escalation_tier": "Tier-1"},
        {"component": "nova-api", "severity": "CRITICAL", "frequency": 2, "persistence_days": 1, "team_name": "API",
         "escalation_tier": "Tier-1"},
        {"component": "nova-compute", "severity": "ERROR", "frequency": 192, "persistence_days": 2,
         "team_name": "Compute", "escalation_tier": "Tier-1"},
        {"component": "nova-compute", "severity": "INFO", "frequency": 1000, "persistence_days": 3,
         "team_name": "Compute", "escalation_tier": "Tier-1"},
        {"component": "nova-scheduler", "severity": "INFO", "frequency": 866, "persistence_days": 3,
         "team_name": "Sched", "escalation_tier": "Tier-2"},
    ])
    items = service.alerts("MGR")
    assert [a.title for a in items] == ["nova-compute: 192 ERROR/CRITICAL lines", "nova-api: 4 ERROR/CRITICAL lines"]
    assert items[0].detail.startswith("Seen on 2 of 3 days with logs") and items[0].time == ""


# ---- routes -----------------------------------------------------------------

@pytest.mark.parametrize("path,env", [
    ("/api/metrics/timeseries", "SERENEDB_TEAM_LEAD_DSN"),
    ("/api/alerts", "SERENEDB_TEAM_LEAD_DSN"),
])
def test_metric_routes_missing_role_dsn_is_503(client, monkeypatch, path, env):
    monkeypatch.delenv(env, raising=False)
    res = client.get(path, params={"role": "LEAD"})
    assert res.status_code == 503 and env in res.json()["detail"]


@pytest.mark.parametrize("path,attr", [("/api/metrics/timeseries", "timeseries"), ("/api/alerts", "alerts")])
def test_metric_routes_failure_is_502_without_leaking(client, monkeypatch, path, attr):
    def boom(role):
        raise RuntimeError("db down at 10.0.0.1")
    monkeypatch.setattr(api_main, attr, boom)
    res = client.get(path, params={"role": "ENG"})
    assert res.status_code == 502 and "10.0.0.1" not in res.text


def test_alert_route_returns_items(client, monkeypatch):
    monkeypatch.setattr(api_main, "alerts", lambda role: [
        {"sev": "ERROR", "title": "t", "detail": "d", "time": "05-16 03:19", "q": "Why?"}])
    res = client.get("/api/alerts", params={"role": "MGR"})
    assert res.status_code == 200 and res.json()[0]["q"] == "Why?"


# ---- live (pytest -m serenedb) ---------------------------------------------

@pytest.mark.serenedb
@pytest.mark.parametrize("role", ["ENG", "LEAD", "MGR"])
def test_live_role_queries_run_on_their_own_grants(role):
    runs = service.run_grounding_queries(InvestigationRequest(role=role, question="q"), [])
    assert all(run.error is None and run.rows for run in runs)
    assert service._corpus_errors(runs) > 0
    assert len(service.component_error_counts(role)) >= 3
    ts = service.timeseries(role)
    assert ts.points if role != "MGR" else ts.table
    assert service.alerts(role)


@pytest.mark.serenedb
@pytest.mark.parametrize("role", ["LEAD", "MGR"])
def test_live_restricted_roles_cannot_read_raw_tables(role):
    import psycopg

    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        service._fetch(role, "SELECT COUNT(*) FROM openstack_logs")
