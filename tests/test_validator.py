from src.prompts import Role
from src.validator import validate_answer

EVIDENCE = [{"total_matching_count": 4747963, "rows": [{"matching_log_entries": 240, "share": 60.81,
                                                        "node_id": "R02-M1-N0-C:J12-U11", "rate": 0.0123}]}]


def test_cited_numbers_pass():
    report = validate_answer(Role.ENGINEER, "KERNEL had 240 FATAL lines across 4,747,963 rows (4.7M).", EVIDENCE)
    assert report.passed, report


def test_rounded_percent_and_fraction_pass():
    assert validate_answer(Role.ENGINEER, "Share was 60.8% and the rate 1.23%.", EVIDENCE).passed


def test_invented_number_flagged():
    report = validate_answer(Role.ENGINEER, "There were 9,999 failures.", EVIDENCE)
    assert not report.passed and report.unverified_numbers == ["9,999"]


def test_derived_and_ordinals_and_identifiers_exempt():
    text = "## 1. Top issue\n1. Node R02-M1-N0-C:J12-U11 shows p99 and Tier-1 load (derived: 240/395 = 60.8%)."
    assert validate_answer(Role.ENGINEER, text, EVIDENCE).passed


def test_manager_business_claims_rejected():
    report = validate_answer(Role.MANAGER, "This outage cost $50k in revenue. Customers affected were many.", EVIDENCE)
    assert len(report.forbidden_claims) == 2


def test_manager_disclaimer_allowed():
    text = "Revenue, SLA penalties, customer impact and downtime are not recorded in the logs and were excluded."
    assert validate_answer(Role.MANAGER, text, EVIDENCE).passed


def test_business_words_allowed_for_other_roles():
    assert not validate_answer(Role.ENGINEER, "No revenue data here.", EVIDENCE).forbidden_claims


def test_digits_inside_ids_and_sql_do_not_ground_claims():
    evidence = [{"executed_queries": [{"sql": "SELECT ... LIMIT 9335", "bound_parameters": [7777]}],
                 "rows": [{"tenant_id": "54fadb412c4e40cdbaed9335e4c35a9e", "request_id": "req-1234",
                           "matching_log_entries": 240, "line_id": 104}]}]
    report = validate_answer(Role.ENGINEER, "We saw 9335 and 7777 and 1234 events.", evidence)
    assert report.unverified_numbers == ["9335", "7777", "1234"]
    assert validate_answer(Role.ENGINEER, "Line 104 shows 240 entries.", evidence).passed


def test_million_word_suffix():
    assert validate_answer(Role.ENGINEER, "About 4.7 million lines.", EVIDENCE).passed
    assert not validate_answer(Role.ENGINEER, "About 9.1 million lines.", EVIDENCE).passed
