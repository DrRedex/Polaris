"""Query layer tests: results are checked against counts computed directly from the fixture files."""

from pathlib import Path

import pytest

from src import queries as q

FIXTURES = Path(__file__).parent / "fixtures"
BGL_LINES = (FIXTURES / "BGL_sample.log").read_text(encoding="utf-8").splitlines()
OS_LINES = (FIXTURES / "OpenStack_sample.log").read_text(encoding="utf-8").splitlines()


def test_prepare_extracts_openstack_fields(db):
    row = db.execute("SELECT * FROM openstack_logs WHERE line_id = 1").fetchone()
    cols = [d[0] for d in db.description]
    rec = dict(zip(cols, row))
    assert rec["component"] == "nova-api"
    assert rec["http_status"] == 200 and rec["latency_sec"] == pytest.approx(0.2477829)
    assert rec["request_id"].startswith("req-") and rec["tenant_id"]
    assert rec["http_route"] == "/v2/{id}/servers/detail"


def test_anomaly_labels_applied(db):
    n = db.execute("SELECT COUNT(*) FROM openstack_logs WHERE is_anomaly").fetchone()[0]
    expected = sum("544fd51c-4edc-4780-baae-ba1d80a0acfc" in l and "[instance:" in l for l in OS_LINES)
    assert n == expected > 0


def test_search_counts_match_source(db):
    env = q.search_logs("BGL", "parity error", mode="substring", limit=5)
    assert env["total_matching_count"] == sum("parity error" in l.lower() for l in BGL_LINES)
    assert env["returned_count"] == 5 and env["is_truncated"]
    assert env["data_scope"]["total_corpus_rows"] == len(BGL_LINES)
    assert env["executed_queries"][0]["bound_parameters"][-1] == 5


def test_search_filters_and_time_window(db):
    env = q.search_logs("BGL", "TLB", mode="substring", component="KERNEL", severity="fatal",
                        start_time="2005-06-01T00:00:00", end_time="2005-12-31T23:59:59", limit=100)
    assert all(r["severity"] == "FATAL" and r["component"] == "KERNEL" for r in env["rows"])
    assert env["data_scope"]["additional_filters"]["severity"] == "FATAL"


def test_substring_escapes_like_wildcards(db):
    assert q.search_logs("BGL", "%_%", mode="substring")["total_matching_count"] == 0


@pytest.mark.parametrize("kwargs, message", [
    (dict(dataset="Foo", query_text="x"), "dataset"),
    (dict(dataset="BGL", query_text=""), "query_text"),
    (dict(dataset="BGL", query_text="x", start_time="yesterday"), "ISO-8601"),
])
def test_search_rejects_bad_input(db, kwargs, message):
    with pytest.raises(ValueError, match=message):
        q.search_logs(**kwargs)


def test_nearby_same_node(db):
    anchor = q.search_logs("BGL", "TLB error", mode="substring", severity="FATAL", limit=1)["rows"][0]
    env = q.get_nearby_events("BGL", anchor["line_id"], window_lines=3, same_entity_only=True, max_seconds=86400)
    assert {r["node_id"] for r in env["rows"]} == {anchor["node_id"]}
    anchors = [r for r in env["rows"] if r["is_anchor"]]
    assert len(anchors) == 1 and anchors[0]["relative_line_offset"] == 0
    assert env["rows"] == sorted(env["rows"], key=lambda r: r["line_id"])


def test_nearby_adjacent_window(db):
    env = q.get_nearby_events("BGL", 50, window_lines=2, same_entity_only=False)
    assert [r["relative_line_offset"] for r in env["rows"]] == [-2, -1, 0, 1, 2]


def test_nearby_missing_anchor(db):
    with pytest.raises(ValueError, match="not found"):
        q.get_nearby_events("BGL", 10**9)


def test_summary_totals_and_team_join(db):
    env = q.summarize_by_team_and_component("BGL", errors_only=False)
    assert env["total_matching_count"] == len(BGL_LINES)
    kernel = [r for r in env["rows"] if r["component"] == "KERNEL"]
    assert kernel and all(r["team_name"] == "Core Systems & OS Team" for r in kernel)


def test_summary_both_has_both_datasets(db):
    env = q.summarize_by_team_and_component("BOTH", errors_only=False)
    assert {r["dataset"] for r in env["rows"]} == {"BGL", "OpenStack"}
    assert env["total_matching_count"] == len(BGL_LINES) + len(OS_LINES)


def test_trends_summary_covers_all_rows(db):
    env = q.get_recurring_trends("BGL", "week", errors_only=False)
    assert sum(c["total_log_entries"] for c in env["summary"]["components"]) == len(BGL_LINES)


def test_trends_hourly_needs_short_window(db):
    with pytest.raises(ValueError, match="31 days"):
        q.get_recurring_trends("BGL", "hour")


def test_latency_percentiles_ordered(db):
    env = q.get_openstack_latency_and_errors()
    api = next(r for r in env["rows"] if r["component"] == "nova-api")
    assert api["p50_latency_sec"] <= api["p95_latency_sec"] <= api["p99_latency_sec"] <= api["max_latency_sec"]
    assert api["http_requests"] == sum(" status: " in l and l.startswith("nova-api") for l in OS_LINES)


def test_corpus_scope(db):
    env = q.get_corpus_scope()
    assert {r["dataset"]: r["total_rows"] for r in env["rows"]} == {"BGL": len(BGL_LINES), "OpenStack": len(OS_LINES)}


def test_openstack_line_without_message_is_kept(db):
    # ERROR lines like "... ERROR oslo_service.periodic_task" have no body and must not be dropped.
    row = db.execute("SELECT severity, logger, message FROM openstack_logs "
                     "WHERE logger = 'oslo_service.periodic_task'").fetchall()
    assert row and row[0][0] == "ERROR"
    assert db.execute("SELECT COUNT(*) FROM openstack_logs").fetchone()[0] == len(OS_LINES)


def test_punctuation_terms_are_ignored_or_rejected(db):
    with pytest.raises(ValueError, match="no searchable words"):
        q.search_logs("BGL", "- :", mode="all_terms")
    with pytest.raises(ValueError, match="at most 8 terms"):
        q.search_logs("BGL", "a b c d e f g h i", mode="any_terms")


def test_component_filter_is_case_insensitive(db):
    upper = q.search_logs("BGL", "TLB", mode="substring", component="KERNEL")["total_matching_count"]
    assert upper > 0
    assert q.search_logs("BGL", "TLB", mode="substring", component="kernel")["total_matching_count"] == upper


def test_timezone_offset_converted_to_utc():
    from src.queries import _parse_time
    assert _parse_time("2005-06-03T02:00:00+02:00", "t").isoformat() == "2005-06-03T00:00:00"
    assert _parse_time("2005-06-03T00:00:00Z", "t").isoformat() == "2005-06-03T00:00:00"
