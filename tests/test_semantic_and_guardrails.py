"""
Step 1 (semantic schema grounding) and Step 2 (deterministic SQL guardrails).

Scenario tests:
  * vague manager complaint  -> KNN grounds it on OpenStack latency/HTTP columns and the
                                get_openstack_latency_and_errors tool
  * hostile DROP TABLE       -> UnsafeQueryException before anything reaches the database
Plus guardrail unit tests and live SereneDB integration tests (pytest -m serenedb).

The semantic tests need the all-MiniLM-L6-v2 model (downloaded once, ~90 MB); they are
skipped if it cannot be loaded.
"""

import re
from pathlib import Path

import psycopg
import pytest

from src.guarded_sql_agent import SereneSQLAgent, UnsafeQueryException
from src.semantic_layer import SERENE_DB_GLOSSARY, TABLE_KEY_COLUMNS

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def knn():
    from src import semantic_layer
    try:
        semantic_layer.get_glossary_matrix()
    except Exception as e:  # no network and no cached model
        pytest.skip(f"embedding model unavailable: {e}")
    return semantic_layer.extract_relevant_schema_context


@pytest.fixture(scope="module")
def agent():
    return SereneSQLAgent()


# ----------------------------------------------------------------------------- scenarios

def test_scenario_vague_manager_complaint(knn, agent):
    query = "Customers keep complaining that the cloud API feels sluggish and keeps timing out lately."
    contexts = knn(query, k=4)
    semantic = [c for c in contexts if c["match_type"] == "semantic"]
    assert len(semantic) == 4
    scores = [c["confidence_score"] for c in semantic]
    assert scores == sorted(scores, reverse=True) and scores[0] > 0.25
    top_columns = {(c["schema_match"]["table"], c["schema_match"]["column"]) for c in semantic}
    assert ("openstack_logs", "latency_sec") in top_columns
    assert semantic[0]["schema_match"]["recommended_mcp_tool"] == "get_openstack_latency_and_errors"

    from src.semantic_layer import recommend_tools
    assert recommend_tools(contexts)[0]["tool"] == "get_openstack_latency_and_errors"

    prompt = agent.assemble_prompt(query, contexts)
    assert "latency_sec (DOUBLE)" in prompt and query in prompt
    assert "openstack_logs_idx" in prompt  # table usage notes for full-text search


def test_scenario_hostile_drop_table(agent):
    with pytest.raises(UnsafeQueryException, match="Prohibited tokens: \\['drop'\\]"):
        agent.apply_guardrails("DROP TABLE bgl_logs;")
    with pytest.raises(UnsafeQueryException, match="multiple statements"):
        agent.apply_guardrails("SELECT COUNT(*) FROM bgl_logs; DROP TABLE bgl_logs")


# ----------------------------------------------------------------------------- step 1

def test_knn_always_adds_time_and_key_columns(knn):
    contexts = knn("which team owns the failing hardware and how do I escalate", k=2)
    tables = {c["schema_match"]["table"] for c in contexts if c["match_type"] == "semantic"}
    for table in tables:
        present = {c["schema_match"]["column"] for c in contexts if c["schema_match"]["table"] == table}
        assert set(TABLE_KEY_COLUMNS[table]) <= present
    assert all(c["confidence_score"] is None for c in contexts if c["match_type"] == "required_context")


def test_knn_model_is_a_singleton(knn):
    from src import semantic_layer
    model = semantic_layer.get_model()
    knn("parity errors", k=1)
    assert semantic_layer.get_model() is model


def test_glossary_matches_real_schema():
    """Every glossary column exists in the SQL schema files, and has a real MCP tool."""
    schema_sql = (ROOT / "sql" / "01_schema_and_views.sql").read_text() + (ROOT / "sql" / "03_team_ownership.sql").read_text()
    columns: dict[str, set[str]] = {}
    for table, body in re.findall(r"CREATE TABLE (\w+) \((.*?)\n\);", schema_sql, re.S):
        columns[table] = {m.group(1) for m in re.finditer(r"^\s+(\w+)\s+[A-Z]", body, re.M)}
    import asyncio
    from src.mcp_server import mcp
    tools = {t.name for t in asyncio.run(mcp.list_tools())}
    for item in SERENE_DB_GLOSSARY:
        assert item["column"] in columns[item["table"]], item
        assert item["recommended_mcp_tool"] in tools, item
    required = {"latency_sec", "http_status", "severity", "anomaly_label", "component", "node_id",
                "request_id", "message", "team_name", "escalation_tier"}
    assert required <= {i["column"] for i in SERENE_DB_GLOSSARY}


# ----------------------------------------------------------------------------- step 2

@pytest.mark.parametrize("sql", [
    "SELECT line_id FROM bgl_logs WHERE message ILIKE '%update%' OR message ILIKE '%delete%'",
    "SELECT COUNT(*) FROM bgl_logs WHERE message = 'it''s a ; drop table'",
    "SELECT line_id FROM bgl_logs WHERE message ILIKE '%;%'",
])
def test_keywords_and_semicolons_inside_literals_are_allowed(agent, sql):
    assert agent.apply_guardrails(sql).endswith(";")


@pytest.mark.parametrize("sql, message", [
    ("SELECT 1 FROM bgl_logs; DELETE FROM bgl_logs", "multiple statements"),
    ("UPDATE bgl_logs SET severity = 'INFO'", "Prohibited tokens"),
    ("COPY bgl_logs TO '/tmp/out.csv'", "Prohibited tokens"),
    ("EXPLAIN SELECT 1", "must start with a 'SELECT'"),
    ("SELECT * FROM read_csv('/etc/passwd')", "table functions"),
    ("SELECT (SELECT COUNT(*) FROM read_text('/etc/hostname')) FROM bgl_logs", "table functions"),
    ("SELECT * FROM '/etc/passwd'", "not allowed"),
    ("SELECT * FROM pg_roles", "not allowed"),
    ("SELECT * FROM pg_catalog.pg_tables", "schema-qualified"),
    ("WITH pg_roles AS (SELECT * FROM pg_roles) SELECT * FROM pg_roles", "not allowed"),
    ("SELECT pg_sleep(60) FROM bgl_logs", "pg_sleep"),
    ("SELECT message FROM bgl_logs UNION ALL SELECT Body FROM read_parquet('https://x/y.parquet')", "table functions"),
    ("SELECT * FROM bgl_logs LIMIT (SELECT 5)", "plain number"),
])
def test_unsafe_queries_rejected(agent, sql, message):
    with pytest.raises(UnsafeQueryException, match=message):
        agent.apply_guardrails(sql)


def test_limit_added_clamped_or_kept(agent):
    assert agent.apply_guardrails("SELECT * FROM bgl_logs").endswith("LIMIT 50;")
    assert agent.apply_guardrails("SELECT * FROM bgl_logs", max_limit=500).endswith("LIMIT 50;")
    assert agent.apply_guardrails("SELECT * FROM bgl_logs LIMIT 100000").endswith("LIMIT 50;")
    assert agent.apply_guardrails("SELECT * FROM bgl_logs LIMIT 7").endswith("LIMIT 7;")
    # A LIMIT inside a subquery does not bound the outer query.
    assert agent.apply_guardrails("SELECT * FROM (SELECT * FROM bgl_logs LIMIT 5) t JOIN bgl_logs b ON true").endswith("LIMIT 50;")


def test_markdown_comments_and_cte_handled(agent):
    sql = agent.apply_guardrails("```sql\n-- top components\nWITH c AS (SELECT component, COUNT(*) n FROM bgl_logs "
                                 "GROUP BY 1) SELECT * FROM c ORDER BY n DESC;\n```")
    assert "```" not in sql and "--" not in sql and sql.endswith("LIMIT 50;")


# ----------------------------------------------------------------------------- live SereneDB

@pytest.mark.serenedb
def test_live_guarded_query_against_serenedb(knn):
    from src.agent import call_tool
    env, err = call_tool("execute_guarded_readonly_sql", {
        "user_query": "Which owning teams have the most fatal BGL alerts?",
        "candidate_sql": "SELECT o.team_name, o.escalation_tier, COUNT(*) AS fatal_lines "
                         "FROM bgl_logs l LEFT JOIN team_ownership o ON o.dataset = 'BGL' AND o.component = l.component "
                         "WHERE l.severity = 'FATAL' GROUP BY 1, 2 ORDER BY fatal_lines DESC"})
    assert err is None, err
    assert env["executed_sql"].endswith("LIMIT 50")
    assert env["knn_schema_matches"] and env["knn_schema_matches"][0]["confidence_score"] is not None
    assert env["total_matching_count"] == env["returned_count"] and not env["is_truncated"]
    assert env["rows"][0]["team_name"] == "Core Systems & OS Team"
    # Cross-check against the parameterized tool: same FATAL KERNEL count.
    summary, _ = call_tool("summarize_by_team_and_component", {"dataset": "BGL", "errors_only": False, "limit": 100})
    kernel_fatal = next(r for r in summary["rows"] if r["component"] == "KERNEL" and r["severity"] == "FATAL")
    assert env["rows"][0]["fatal_lines"] >= kernel_fatal["matching_log_entries"]


@pytest.mark.serenedb
def test_live_truncation_and_full_text_index(knn):
    from src.agent import call_tool
    env, err = call_tool("execute_guarded_readonly_sql", {
        "user_query": "show recent machine check messages",
        "candidate_sql": "SELECT l.line_id, l.event_time, l.node_id, l.message FROM bgl_logs_idx i "
                         "JOIN bgl_logs l ON l.line_id = i.line_id WHERE i.message @@ ts_phrase('machine check') "
                         "ORDER BY l.event_time DESC LIMIT 10000"})
    assert err is None, err
    assert env["returned_count"] == 50 and env["is_truncated"]
    assert env["total_matching_count"] == call_tool("search_logs", {"dataset": "BGL", "query_text": "machine check",
                                                                  "mode": "phrase", "limit": 1})[0]["total_matching_count"]


@pytest.mark.serenedb
def test_live_reader_role_cannot_write():
    """Defence in depth: even SQL that bypassed the guardrails cannot modify data."""
    from src.db import reader_dsn
    with psycopg.connect(reader_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM team_ownership")
        assert cur.fetchone()[0] > 0
        for stmt in ("DELETE FROM team_ownership", "DROP TABLE team_ownership",
                     "INSERT INTO team_ownership VALUES ('x', 'y', 'z', 'w', 'v', 'u')"):
            with pytest.raises(psycopg.Error):
                cur.execute(stmt)


@pytest.mark.serenedb
def test_live_hostile_sql_never_reaches_database(knn):
    from src.agent import call_tool
    for sql in ("DROP TABLE bgl_logs", "SELECT * FROM read_csv('/etc/passwd')"):
        env, err = call_tool("execute_guarded_readonly_sql", {"user_query": "x", "candidate_sql": sql})
        assert env is None and "Security Guardrail Violation" in err
    with psycopg.connect(__import__("src.db", fromlist=["SERENEDB_DSN"]).SERENEDB_DSN, autocommit=True) as conn:
        assert conn.execute("SELECT COUNT(*) FROM bgl_logs").fetchone()[0] == 4_747_963


def test_generated_sql_is_untrusted_until_guarded(agent):
    """The LLM's output goes through the same guardrails as any other candidate."""
    from types import SimpleNamespace
    from src.guarded_sql_agent import generate_candidate_sql
    reply = "```sql\nSELECT component, COUNT(*) FROM bgl_logs GROUP BY 1;\n```"
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
        create=lambda **kw: SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=reply))]))))
    sql = generate_candidate_sql("prompt", client=client, model="test")
    assert agent.apply_guardrails(sql) == "SELECT component, COUNT(*) FROM bgl_logs GROUP BY 1\nLIMIT 50;"
