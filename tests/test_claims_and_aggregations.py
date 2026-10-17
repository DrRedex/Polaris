"""Evidence IDs, deterministic claim verifier, burst / top-node aggregations, priority ranking,
latency outliers. Offline tests use the DuckDB fixture; live ones run with -m serenedb."""

import time

import pytest

from src.claim_verifier import verify_response_claims
from src import queries as q

LEDGER = {
    "EV-01": {"evidence_id": "EV-01", "total_matching_count": 152734,
              "rows": [{"component": "KERNEL", "matching_log_entries": 626450, "burst_count_30m": 501,
                        "top_node_share_pct": 24.3, "growth_pct": -17.2, "node_id": "R02-M1-N0-C:J12-U11"}]},
    "EV-02": {"evidence_id": "EV-02", "rows": [{"dataset": "BGL", "total_rows": 4747963, "span_days": 214.7}],
              "unknown_not_in_logs": ["financial_cost", "customer_impact"]},
}


# ----------------------------------------------------------------------------- claim verifier

def test_numbers_must_be_in_the_cited_evidence():
    text = ("KERNEL FATAL produced 626,450 lines in 501 bursts [EV-01]. "
            "The corpus holds 4.7 million lines over 214.7 days [EV-02]. "
            "Bursts were 501 [EV-02].")  # right number, wrong citation
    r = verify_response_claims(text, LEDGER, "Engineer")
    assert r.numbers_checked == 5 and r.numbers_verified == 4
    assert r.verified_number_rate == 0.8 and not r.passed
    bad = [c for c in r.checks if not c.verified]
    assert bad[0].number == "501" and bad[0].reason == "not_in_cited_evidence"


def test_uncited_and_unknown_ids_are_reported():
    r = verify_response_claims("There were 9,876 failures.\nAlso 626,450 lines [EV-07].", LEDGER, "Team Lead")
    reasons = {c.number: c.reason for c in r.checks}
    assert reasons == {"9,876": "uncited", "626,450": "unknown_evidence_id"}
    assert r.unknown_evidence_ids == ["EV-07"] and r.uncited_numeric_sentences == 1


def test_table_rows_and_multi_citations():
    text = ("Top problems [EV-01]:\n| Problem | Lines | Share |\n|---|---|---|\n"
            "| KERNEL FATAL | 626,450 | 24.3% |\n"
            "Combined scope: 501 bursts across 4,747,963 lines [EV-01, EV-02].")
    r = verify_response_claims(text, LEDGER, "Manager")
    assert r.passed and r.numbers_checked == 4


def test_derived_figures_and_ordinals_are_exempt():
    text = "## 1. Top issue\n1. KERNEL has 501 bursts [EV-01] (derived: 626450/501 = 1250.4 lines per burst)."
    assert verify_response_claims(text, LEDGER, "Engineer").passed


def test_manager_business_claims_flagged_and_stripped():
    text = ("KERNEL FATAL leads with 501 bursts [EV-01]. This caused $2M in lost revenue. "
            "Customer impact was severe. Revenue, SLA breaches and customer impact are not recorded in the logs.")
    warn = verify_response_claims(text, LEDGER, "Manager")
    assert len(warn.forbidden_claims) == 2 and not warn.passed
    strip = verify_response_claims(text, LEDGER, "Manager", mode="strip")
    assert "$2M" not in strip.cleaned_text and "Customer impact was severe" not in strip.cleaned_text
    assert "are not recorded in the logs" in strip.cleaned_text and "501 bursts" in strip.cleaned_text


def test_business_words_only_policed_for_manager():
    assert not verify_response_claims("Revenue impact unclear.", LEDGER, "Engineer").forbidden_claims


def test_verifier_is_fast():
    text = " ".join(f"KERNEL had 626,450 lines and 501 bursts [EV-01]." for _ in range(40))
    verify_response_claims(text, LEDGER, "Manager")  # warm the per-evidence number cache
    timings = []
    for _ in range(5):  # best of 5: measures the verifier, not scheduler noise on a busy machine
        t0 = time.perf_counter()
        verify_response_claims(text, LEDGER, "Manager")
        timings.append((time.perf_counter() - t0) * 1000)
    assert min(timings) < 5


# ----------------------------------------------------------------------------- evidence IDs

def test_mcp_tools_stamp_unique_evidence_ids(db):
    import asyncio
    from src.mcp_server import mcp
    a = asyncio.run(mcp.call_tool("get_corpus_scope", {})).structured_content["evidence_id"]
    b = asyncio.run(mcp.call_tool("get_corpus_scope", {})).structured_content["evidence_id"]
    assert a.startswith("EV-") and b.startswith("EV-") and a != b


# ----------------------------------------------------------------------------- aggregations (DuckDB)

def test_summary_has_bursts_top_node_and_periods(db):
    env = q.summarize_by_team_and_component("BGL", errors_only=False)
    row = next(r for r in env["rows"] if r["component"] == "KERNEL" and r["severity"] == "INFO")
    for field in ("burst_count_30m", "top_node", "top_node_share_pct", "current_period_bursts",
                  "previous_period_bursts", "growth_pct"):
        assert field in row
    assert 1 <= row["burst_count_30m"] <= row["matching_log_entries"]
    assert row["current_period_bursts"] + row["previous_period_bursts"] == row["burst_count_30m"]
    assert 0 < row["top_node_share_pct"] <= 100
    assert "periods" in env["data_scope"]["datasets"][0]


def test_burst_gap_rule_matches_python(db):
    """burst_count_30m equals a plain-Python 30-minute gap count over the same rows."""
    rows = db.execute("SELECT event_time FROM bgl_logs WHERE component = 'KERNEL' AND severity = 'FATAL' "
                      "ORDER BY event_time").fetchall()
    expected, prev = 0, None
    for (t,) in rows:
        if prev is None or (t - prev).total_seconds() > 1800:
            expected += 1
        prev = t
    env = q.summarize_by_team_and_component("BGL", errors_only=False)
    row = next(r for r in env["rows"] if r["component"] == "KERNEL" and r["severity"] == "FATAL")
    assert row["burst_count_30m"] == expected


def test_explicit_window_compares_with_previous_window(db):
    env = q.summarize_by_team_and_component("BGL", "2005-07-01", "2005-12-31", errors_only=False)
    periods = env["data_scope"]["datasets"][0]["periods"]
    assert periods["previous_end"] == periods["current_start"] == "2005-07-01T00:00:00"


def test_priority_ranking_is_deterministic_and_bounded(db):
    a = q.get_priority_ranking("BOTH")
    b = q.get_priority_ranking("BOTH")
    assert [(r["dataset"], r["component"], r["severity"], r["priority_score"]) for r in a["rows"]] == \
           [(r["dataset"], r["component"], r["severity"], r["priority_score"]) for r in b["rows"]]
    assert all(0 <= r["priority_score"] <= 100 for r in a["rows"])
    assert [r["rank"] for r in a["rows"]] == list(range(1, len(a["rows"]) + 1))
    assert a["unknown_not_in_logs"] == ["financial_cost", "customer_impact", "total_downtime", "sla_penalties"]
    scores = [r["priority_score"] for r in a["rows"]]
    assert scores == sorted(scores, reverse=True)


def test_latency_outliers_mad_rule(db):
    env = q.get_latency_outliers(k=3.0, min_excess_sec=0.0, min_samples=5)
    for r in env["rows"]:
        assert r["duration_sec"] > r["threshold_sec"]
        assert r["threshold_sec"] >= r["baseline_median_sec"] + 3.0 * r["baseline_mad_sec"] - 1e-6
    assert env["summary"]["patterns"]


# ----------------------------------------------------------------------------- live SereneDB

@pytest.mark.serenedb
def test_live_aggregations_and_priority():
    env = q.summarize_by_team_and_component("BGL")
    kernel = next(r for r in env["rows"] if r["component"] == "KERNEL" and r["severity"] == "FATAL")
    assert kernel["matching_log_entries"] == 626450 and isinstance(kernel["burst_count_30m"], int)
    assert kernel["current_period_bursts"] + kernel["previous_period_bursts"] == kernel["burst_count_30m"]
    top = q.get_priority_ranking("BOTH", limit=3)["rows"][0]
    assert top["component"] == "KERNEL" and top["severity"] == "FATAL" and top["rank"] == 1


@pytest.mark.serenedb
def test_live_silent_slow_spawns_found():
    env = q.get_latency_outliers(limit=100)
    spawns = [r for r in env["rows"] if r["operation"] == "spawn the instance on the hypervisor"]
    assert spawns and all(r["severity"] == "INFO" and r["instance_id"] for r in spawns)
    assert all(r["ratio_to_median"] > 1 for r in spawns)


def test_table_header_labels_are_not_claims():
    text = ("| Component | Bursts (30 min) | Lines |\n|---|---|---|\n"
            "| KERNEL | 501 | 626,450 | [EV-01] |")
    r = verify_response_claims(text, LEDGER, "Team Lead")
    assert r.passed and r.numbers_checked == 2


def test_disclaimer_section_bullets_are_not_stripped():
    text = ("## Priority matrix\nKERNEL FATAL leads with 501 bursts [EV-01].\n"
            "## What the logs do not establish\n- Financial impact such as revenue loss.\n"
            "- Specific statistics on downtime and detailed customer impact.\n"
            "## Recommended actions\nThe outage cost $3M in revenue.")
    r = verify_response_claims(text, LEDGER, "Manager", mode="strip")
    assert r.forbidden_claims == ["The outage cost $3M in revenue."]
    assert "Financial impact such as revenue loss." in r.cleaned_text


def test_answer_without_numbers_is_flagged_as_such():
    r = verify_response_claims("The kernel failed on several nodes [EV-01].", LEDGER, "Engineer")
    assert r.passed and r.no_numeric_claims


def test_real_run_false_positives_are_fixed():
    """Three sentences from live gpt-4o runs that were wrongly flagged."""
    ledger = {**LEDGER, "EV-03": {"rows": [{"relative_time_sec": 1664.249}, {"relative_time_sec": 2384.24}]}}
    text = ("- **00:35:57** (approximately 27.5 minutes later): core generated on the same node [EV-03] "
            "(derived: 1664.249/60 = 27.5 minutes).\n"
            "- KERNEL FATAL has 501 bursts on many nodes. Fix it first [EV-01].\n"
            "- BGL: 4,747,963 rows from June 3, 2005 to January 4, 2006 [EV-02].")
    r = verify_response_claims(text, ledger, "Manager")
    assert r.passed, [c for c in r.checks if not c.verified]
    assert {c.reason for c in r.checks} == {"derived", "matched"}


def test_wrong_numbers_still_caught_after_relaxations():
    text = ("- KERNEL FATAL has 999 bursts. Fix it first [EV-01].\n"
            "- It took 33.3 minutes [EV-01] (derived: 1664/60 = 27.7).\n"
            "- On May 17, 2017 there were 4,321 errors [EV-02].")
    r = verify_response_claims(text, LEDGER, "Engineer")
    assert r.unverified_numbers == ["999", "33.3", "4,321"]


def test_derived_arithmetic_is_evaluated_safely():
    from src.claim_verifier import _derived_values
    assert 12.0 in _derived_values("(derived: 2384.24 seconds - 1664.249 seconds = 720 seconds / 60 seconds per minute)")
    assert _derived_values("(derived: __import__('os').system('x'))") == []  # no eval: digits only, nothing runs
    text = "- Logged 12 minutes later [EV-01] (derived: 720 / 60)."
    assert verify_response_claims(text, LEDGER, "Engineer").passed
    assert not verify_response_claims("- Logged 13 minutes later [EV-01] (derived: 720 / 60).", LEDGER, "Engineer").passed
