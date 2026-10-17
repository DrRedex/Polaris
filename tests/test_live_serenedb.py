"""Live tests against the SereneDB instance loaded by scripts/load_corpus.py.

    pytest -m serenedb
"""

import pytest

from src import queries as q
from src.db import EvidenceBuilder

pytestmark = pytest.mark.serenedb


@pytest.mark.parametrize("mode", ["all_terms", "any_terms", "phrase", "substring"])
def test_fulltext_modes(mode):
    env = q.search_logs("BGL", "parity error", mode=mode, limit=3)
    assert env["total_matching_count"] > 0 and env["returned_count"] <= 3


def test_fulltext_matches_substring_count():
    fts = q.search_logs("BGL", "data TLB error interrupt", mode="phrase", limit=1)["total_matching_count"]
    sub = q.search_logs("BGL", "data TLB error interrupt", mode="substring", limit=1)["total_matching_count"]
    assert fts == sub > 0


def test_fulltext_with_time_window():
    env = q.search_logs("BGL", "TLB", component="KERNEL", start_time="2005-06-01", end_time="2005-12-31", limit=1)
    assert env["total_matching_count"] > 0


def test_counts_match_independent_count():
    with EvidenceBuilder("check") as eb:
        bgl = eb.query("n", "SELECT COUNT(*) AS n FROM bgl_logs")[0]["n"]
    env = q.summarize_by_team_and_component("BGL", errors_only=False, limit=100)
    assert env["total_matching_count"] == bgl


def test_all_tools_run():
    q.get_corpus_scope()
    q.get_nearby_events("BGL", 1, 5, True)
    q.get_recurring_trends("BGL", "week")
    q.get_recurring_trends("OpenStack", "hour", "2017-05-14", "2017-05-17")
    q.get_openstack_latency_and_errors(breakdown="route")


def test_workspace_index_lifecycle():
    from src.extensions import manage_workspace_index as ws
    try:
        env = ws("benchmark", "BGL", "2005-06-03", "2005-06-10", "error", 2)
        assert env["summary"]["results_identical"]
        assert any(r["index_name"].startswith("ws_bgl_logs_") for r in ws("list")["rows"])
    finally:
        ws("drop", "BGL", "2005-06-03", "2005-06-10")
    assert not ws("list")["rows"]


def test_remote_otel_footer_overview():
    from src.extensions import query_remote_otel_parquet
    env = query_remote_otel_parquet("overview", parts_for_overview=1)
    assert env["total_matching_count"] == 1_000_000_000


def test_search_tolerates_punctuation_and_any_terms_is_not_an_expression():
    assert q.search_logs("BGL", "ciod: failed - read", limit=1)["total_matching_count"] > 0
    # Text that looks like a query expression is searched as words, not executed.
    assert q.search_logs("BGL", "ts_like('%')", mode="any_terms", limit=1)["total_matching_count"] < 1000
    a = q.search_logs("BGL", "parity", limit=1)["total_matching_count"]
    b = q.search_logs("BGL", "TLB", limit=1)["total_matching_count"]
    assert q.search_logs("BGL", "parity TLB", mode="any_terms", limit=1)["total_matching_count"] <= a + b


def test_search_uses_covering_workspace_index():
    from src.extensions import manage_workspace_index as ws
    kw = dict(start_time="2005-06-05T00:00:00", end_time="2005-06-06T00:00:00", limit=1)
    before = q.search_logs("BGL", "error", **kw)
    try:
        ws("create", "BGL", "2005-06-03T00:00:00", "2005-06-10T00:00:00")
        during = q.search_logs("BGL", "error", **kw)
        assert during["total_matching_count"] == before["total_matching_count"]
        assert any("workspace index" in n for n in during["notes"])
        assert "FROM ws_bgl_logs_" in during["executed_queries"][-1]["sql"]
    finally:
        ws("drop", "BGL", "2005-06-03T00:00:00", "2005-06-10T00:00:00")
