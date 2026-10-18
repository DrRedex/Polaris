"""GET /api/metrics/components: routing, validation, CORS and per-role SQL selection (no live DB)."""

from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

import src.api.main as api_main
import src.api.service as service
from src.api.schemas import ComponentErrorItem


@pytest.fixture
def client():
    # Not used as a context manager, so the SereneDB lifespan health check does not run.
    return TestClient(api_main.app)


def test_components_returns_items(client, monkeypatch):
    seen = []
    monkeypatch.setattr(api_main, "component_error_counts",
                        lambda role: seen.append(role) or [ComponentErrorItem(name="nova-compute", n=3, note="3 lines")])
    res = client.get("/api/metrics/components", params={"role": "LEAD"})
    assert res.status_code == 200
    assert res.json() == [{"name": "nova-compute", "n": 3, "warnings": 0, "note": "3 lines"}]
    assert seen == ["LEAD"]


def test_components_rejects_unknown_role(client):
    assert client.get("/api/metrics/components", params={"role": "Engineer"}).status_code == 422


def test_components_failure_is_502(client, monkeypatch):
    def boom(role):
        raise RuntimeError("db down")
    monkeypatch.setattr(api_main, "component_error_counts", boom)
    res = client.get("/api/metrics/components", params={"role": "ENG"})
    assert res.status_code == 502
    assert "db down" not in res.text


def test_components_missing_role_dsn_is_503(client, monkeypatch):
    monkeypatch.delenv("SERENEDB_ENGINEER_DSN", raising=False)
    res = client.get("/api/metrics/components", params={"role": "ENG"})
    assert res.status_code == 503
    assert res.json()["detail"] == "Engineer database login is not configured (set SERENEDB_ENGINEER_DSN)."


def test_investigate_missing_role_dsn_is_503_before_pipeline(client, monkeypatch):
    monkeypatch.delenv("SERENEDB_MANAGER_DSN", raising=False)

    def must_not_run(request):
        raise AssertionError("pipeline (and its LLM call) must not run without a role DSN")
    monkeypatch.setattr(service, "_pipeline_call", must_not_run)
    res = client.post("/api/investigate", json={"role": "MGR", "question": "What is failing?"})
    assert res.status_code == 503
    assert "SERENEDB_MANAGER_DSN" in res.json()["detail"]


def test_cors_allows_loopback_origin(client):
    res = client.options("/api/investigate", headers={
        "Origin": "http://127.0.0.1:3000", "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type"})
    assert res.headers.get("access-control-allow-origin") == "http://127.0.0.1:3000"


class _FakeCursor:
    def __init__(self, rows, log):
        self.rows, self.log = rows, log

    def execute(self, sql, params):
        self.log.append((sql, params))

    def fetchall(self):
        return self.rows


@pytest.mark.parametrize("role,source", [
    ("ENG", "openstack_logs"),
    ("LEAD", "v_team_lead_patterns"),
    ("MGR", "v_manager_summary"),
])
def test_component_error_counts_uses_role_source(monkeypatch, role, source):
    log, dsns = [], []

    @contextmanager
    def fake_conn(dsn=None):
        dsns.append(dsn)
        yield type("C", (), {"cursor": lambda self: _cursor_ctx()})()

    @contextmanager
    def _cursor_ctx():
        yield _FakeCursor([{"component": "nova-compute", "errors": 3, "warnings": 7},
                           {"component": "nova-api", "errors": 1, "warnings": 0},
                           {"component": "nova-scheduler", "errors": 0, "warnings": None}], log)

    monkeypatch.setattr(service, "get_connection", fake_conn)
    monkeypatch.setattr(service, "role_dsn", lambda slug: f"dsn-for-{slug}")
    items = service.component_error_counts(role)

    sql, params = log[0]
    assert source in sql
    assert dsns == [f"dsn-for-{service.ROLE_SLUGS[role]}"]
    assert params == [service.COMPONENT_LIMIT]
    assert [i.n for i in items] == [3, 1, 0]
    assert [i.warnings for i in items] == [7, 0, 0]
    assert "75%" in items[0].note
    # Components with no errors stay in the list instead of disappearing.
    assert items[2].name == "nova-scheduler" and "%" not in items[2].note
