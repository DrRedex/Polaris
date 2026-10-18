"""
guarded_sql_agent.py  (Step 2: prompt construction + deterministic SQL guardrails)

Fallback path for questions the parameterized MCP tools cannot express. An LLM writes a
candidate SELECT from a sandbox prompt grounded by semantic_layer; the candidate is only
executed after deterministic checks, in this order:

  1. strip markdown fences and SQL comments (string literals are preserved)
  2. reject internal semicolons (multi-statement)            -- checked with literals masked
  3. reject forbidden keywords (drop, delete, update, ...)   -- checked with literals masked,
     so  WHERE message ILIKE '%delete%'  is allowed
  4. require the statement to start with SELECT (or WITH ... SELECT)
  5. parse with sqlglot and allow only the corpus tables and their full-text indexes:
     table functions such as read_csv('/etc/passwd'), system catalogs and other schemas
     are rejected (a plain SELECT can otherwise read server files in SereneDB)
  6. enforce a top-level LIMIT: added when missing, clamped when above the cap

Execution then runs as the SELECT-only database role log_reader (see
scripts/load_corpus.py) with a statement timeout, so the database itself refuses writes
even if a check above were bypassed.
"""

import re
from dataclasses import dataclass, field
from typing import Any, Optional

import sqlglot
from sqlglot import exp

ALLOWED_RELATIONS = {"bgl_logs", "openstack_logs", "team_ownership", "bgl_logs_idx", "openstack_logs_idx",
                    "v_team_lead_patterns", "v_manager_summary"}
HARD_MAX_LIMIT = 50

# SereneDB is DuckDB-based: these functions read files, URLs or run nested SQL.
FORBIDDEN_FUNCTION_RE = re.compile(
    r"^(read_\w*|parquet_\w*|iceberg_\w*|delta_\w*|glob|sniff_csv|query|query_table|current_setting|set_config"
    r"|pg_sleep\w*|sleep|getenv|load_\w*|install_\w*|duckdb_\w*|pg_read_\w*|lo_\w*)$",
    re.IGNORECASE)

# String literals ('it''s' style escaping) and SQL comments, matched together so that
# a comment marker inside a literal is left alone.
_LITERAL_OR_COMMENT_RE = re.compile(r"'(?:[^']|'')*'|--[^\n]*|/\*.*?\*/", re.DOTALL)
_LITERAL_RE = re.compile(r"'(?:[^']|'')*'")
_TRAILING_LIMIT_RE = re.compile(r"\blimit\s+(\d+)(\s+offset\s+\d+)?\s*$", re.IGNORECASE)


class UnsafeQueryException(Exception):
    """Raised when a generated SQL query fails safety guardrails."""


def _inside_own_cte(table: exp.Table, name: str) -> bool:
    # In "WITH t AS (SELECT * FROM t)" the inner t is the real relation, not the CTE
    # (a non-recursive CTE cannot see itself), so it must pass the allowlist.
    node = table.parent
    while node is not None:
        if isinstance(node, exp.CTE) and node.alias_or_name.lower() == name:
            with_ = node.parent
            return not (isinstance(with_, exp.With) and with_.args.get("recursive"))
        node = node.parent
    return False


@dataclass
class GuardrailReport:
    sql: str                      # validated SQL with the enforced LIMIT (no trailing semicolon)
    sql_without_limit: str        # same query without its top-level LIMIT (for exact counts)
    tables: list[str]
    limit: int
    limit_action: str             # "added", "clamped" or "kept"
    original_limit: Optional[int]
    checks: list[str] = field(default_factory=list)


class SereneSQLAgent:
    def __init__(self, dialect_rules: Optional[str] = None):
        self.dialect_rules = dialect_rules or (
            "- SereneDB speaks PostgreSQL-compatible SQL (DuckDB engine): date_trunc('day', ts), "
            "quantile_cont(x, 0.95), COUNT(*) FILTER (WHERE ...), TIMESTAMP '2005-06-03 00:00:00'.\n"
            "- Full-text search only works through the index relation: "
            "FROM bgl_logs_idx i JOIN bgl_logs l ON l.line_id = i.line_id WHERE i.message @@ ts_phrase('machine check'). "
            "Same pattern for openstack_logs_idx.\n"
            "- Join owners with: LEFT JOIN team_ownership o ON o.dataset = 'BGL' AND o.component = l.component "
            "(use 'OpenStack' for openstack_logs).\n"
            "- Prefer aggregations (COUNT, COUNT DISTINCT, GROUP BY) over listing raw rows; bgl_logs has 4.7M rows.\n"
            "- Order raw log rows by event_time DESC by default to show recent evidence first.\n"
            f"- Always end with LIMIT n (n <= {HARD_MAX_LIMIT})."
        )
        self.forbidden_tokens = {
            "drop", "delete", "update", "insert", "alter", "truncate", "grant", "revoke",
            "create", "copy", "attach", "detach", "install", "load", "pragma", "call", "export",
            "import", "vacuum", "checkpoint", "set", "reset", "merge", "upsert", "comment", "reindex",
        }

    def assemble_prompt(self, user_query: str, extracted_contexts: list) -> str:
        schema_context_str = ""
        tables = []
        for item in extracted_contexts:
            schema = item["schema_match"]
            score = item.get("confidence_score")
            tables.append(schema["table"])
            schema_context_str += (
                f"\nTable ID: {schema['table']}\n"
                f" - Column Reference: {schema['column']} ({schema['type']})\n"
                f" - Semantic Scope: {schema['description']}\n"
                f" - Match: {item.get('match_type', 'semantic')}"
                + (f", confidence {score:.3f}" if score is not None else "") + "\n"
            )
        from src.semantic_layer import TABLE_NOTES
        notes = "".join(f"\n- {t}: {TABLE_NOTES[t]}" for t in dict.fromkeys(tables) if t in TABLE_NOTES)

        prompt = f"""
You are the dedicated Text-to-SQL translation agent for Serene DB.
Your job is to convert a natural language problem statement into clean, efficient, read-only SQL queries.

[SERENE DB DIALECT CONSTRAINTS]
{self.dialect_rules}

[PERMITTED DATA SCHEMA CONTEXT]
You may ONLY reference the tables and columns explicitly listed below. Do not assume or hallucinate external structures.
{schema_context_str}
[TABLE USAGE NOTES]{notes}

[CRITICAL INSTRUCTIONS]
1. Output ONLY a raw, executable SQL string. Do not include markdown wraps (e.g., ```sql), markdown blocks, or text explanations.
2. The query must be purely read-only (SELECT), a single statement, with no semicolons inside it.
3. No table functions (read_csv, read_parquet, glob, ...) and no tables other than: {", ".join(sorted(ALLOWED_RELATIONS))}.

[USER QUERY TO TRANSLATE]
"{user_query}"

Generated SQL String:"""
        return prompt.strip()

    # ------------------------------------------------------------------ guardrails

    def validate(self, generated_sql: str, max_limit: int = 50) -> GuardrailReport:
        """Runs every guardrail and returns the validated query with its analysis."""
        if not isinstance(generated_sql, str) or not generated_sql.strip():
            raise UnsafeQueryException("Security Guardrail Violation: empty query.")
        if len(generated_sql) > 8000:
            raise UnsafeQueryException("Security Guardrail Violation: query longer than 8000 characters.")
        try:
            requested_cap = int(max_limit)
        except (TypeError, ValueError):
            raise UnsafeQueryException("Security Guardrail Violation: max_limit must be an integer from 1 to 50.") from None
        if requested_cap < 1:
            raise UnsafeQueryException("Security Guardrail Violation: max_limit must be at least 1.")
        cap = min(requested_cap, HARD_MAX_LIMIT)
        checks: list[str] = []

        # 1. Markdown fences and comments go; literals stay byte-for-byte.
        sql = re.sub(r"```[a-zA-Z]*", "", generated_sql)
        sql = _LITERAL_OR_COMMENT_RE.sub(lambda m: m.group(0) if m.group(0).startswith("'") else " ", sql)
        sql = sql.strip()
        sql = re.sub(r"(\s*;)+\s*$", "", sql)  # trailing terminators are harmless
        checks.append("stripped markdown fences, comments and trailing semicolons")

        masked = _LITERAL_RE.sub("''", sql)  # string literals cannot trigger keyword checks
        # Quoted identifiers are parsed structurally below, not treated as executable keywords.
        masked = re.sub(r'"(?:""|[^"])*"', '""', masked)

        # 2. One statement only.
        if ";" in masked:
            raise UnsafeQueryException(
                "Security Guardrail Violation: multiple statements detected (internal ';'). Submit one SELECT.")
        checks.append("single statement")

        # 3. Destructive or state-changing keywords outside string literals.
        tokens = set(re.findall(r"\b\w+\b", masked.lower()))
        violations = self.forbidden_tokens.intersection(tokens)
        if violations:
            raise UnsafeQueryException(
                f"Security Guardrail Violation: Destructive operations detected! Prohibited tokens: {sorted(violations)}")
        checks.append("no forbidden keywords outside string literals")

        # 4. Must be a query.
        head = masked.lstrip("( \n\t").lower()
        if not (head.startswith("select") or head.startswith("with")):
            raise UnsafeQueryException(
                "Security Guardrail Violation: Generated query format invalid. Queries must start with a 'SELECT' "
                "(or 'WITH ... SELECT') statement.")
        checks.append("starts with SELECT / WITH")

        # 5. Structural check: one read-only query over allowlisted relations only.
        try:
            statements = [s for s in sqlglot.parse(sql, read="postgres") if s is not None]
        except sqlglot.errors.ParseError as e:
            raise UnsafeQueryException(f"Security Guardrail Violation: query could not be parsed safely: {e}") from None
        if len(statements) != 1 or not isinstance(statements[0], exp.Query):
            raise UnsafeQueryException("Security Guardrail Violation: exactly one SELECT query is allowed.")
        tree = statements[0]
        for node_type in (exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Create, exp.Alter, exp.Command,
                          exp.Merge, exp.Copy, exp.Set, exp.Pragma, exp.Use, exp.TruncateTable):
            if any(True for _ in tree.find_all(node_type)):
                raise UnsafeQueryException(f"Security Guardrail Violation: {node_type.__name__} is not allowed.")
        cte_names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
        tables = set()
        for table in tree.find_all(exp.Table):
            if not isinstance(table.this, exp.Identifier):
                raise UnsafeQueryException(
                    "Security Guardrail Violation: table functions (read_csv, read_parquet, glob, ...) are not allowed.")
            if table.args.get("db") or table.args.get("catalog"):
                if (table.db or "").lower() != "public" or table.catalog:
                    raise UnsafeQueryException(
                        f"Security Guardrail Violation: schema-qualified relation {table.sql()} is not allowed.")
            name = table.name.lower()
            if name in cte_names and not _inside_own_cte(table, name):
                continue  # reference to a CTE defined in this query
            if name not in ALLOWED_RELATIONS:
                raise UnsafeQueryException(
                    f"Security Guardrail Violation: relation '{table.name}' is not allowed. "
                    f"Permitted: {', '.join(sorted(ALLOWED_RELATIONS))}.")
            tables.add(name)
        for func in tree.find_all(exp.Func):
            fname = func.sql_name() if not isinstance(func, exp.Anonymous) else func.name
            if FORBIDDEN_FUNCTION_RE.match(fname or ""):
                raise UnsafeQueryException(f"Security Guardrail Violation: function {fname}() is not allowed.")
        if not tables:
            raise UnsafeQueryException("Security Guardrail Violation: the query must read from a corpus table.")
        checks.append(f"relations allowlisted: {sorted(tables)}")

        # 6. Hard LIMIT on the outermost query.
        limit_node = tree.args.get("limit")
        if limit_node is None:
            limited = f"{sql}\nLIMIT {cap}"
            without_limit, original, action = sql, None, "added"
        else:
            m = _TRAILING_LIMIT_RE.search(sql)
            if not m:
                raise UnsafeQueryException(
                    "Security Guardrail Violation: the outermost LIMIT must be a plain number at the end of the query.")
            original = int(m.group(1))
            without_limit = sql[:m.start()].rstrip()
            if original > cap:
                limited = sql[:m.start()] + f"LIMIT {cap}" + (m.group(2) or "")
                action = "clamped"
            else:
                limited, action = sql, "kept"
        checks.append(f"LIMIT {action} ({min(original or cap, cap)} max {cap})")

        return GuardrailReport(sql=limited, sql_without_limit=without_limit, tables=sorted(tables),
                               limit=min(original, cap) if original is not None else cap,
                               limit_action=action, original_limit=original, checks=checks)

    def apply_guardrails(self, generated_sql: str, max_limit: int = 50) -> str:
        """Returns the validated, LIMIT-enforced query terminated by ';', or raises UnsafeQueryException."""
        return f"{self.validate(generated_sql, max_limit).sql};"


# ---------------------------------------------------------------------------
# Execution (used by the execute_guarded_readonly_sql MCP tool)
# ---------------------------------------------------------------------------

GUARDED_TIMEOUT_S = 30


def generate_candidate_sql(sandbox_prompt: str, *, client: Any = None, model: Optional[str] = None) -> str:
    """Asks the OpenAI model for one candidate SELECT using the sandbox prompt. The result is
    untrusted: it still has to pass execute_guarded_readonly_sql."""
    import os
    if client is None:
        from openai import OpenAI
        if not os.getenv("OPENAI_API_KEY"):
            raise RuntimeError("OPENAI_API_KEY is not set")
        client = OpenAI()
    resp = client.chat.completions.create(model=model or os.getenv("OPENAI_MODEL", "gpt-4o"),
                                          messages=[{"role": "user", "content": sandbox_prompt}])
    return (resp.choices[0].message.content or "").strip()


def resolve_semantic_schema(user_query: str, k: int = 4) -> dict[str, Any]:
    """Step 1 + prompt: KNN schema matches, recommended parameterized tools and the
    sandbox prompt an LLM should use to write a candidate query. No database access."""
    from src.db import EvidenceEnvelope
    from src.semantic_layer import MODEL_NAME, extract_relevant_schema_context, recommend_tools

    contexts = extract_relevant_schema_context(user_query, k=k)
    agent = SereneSQLAgent()
    tables = list(dict.fromkeys(c["schema_match"]["table"] for c in contexts))
    envelope = EvidenceEnvelope(
        tool_name="resolve_semantic_schema",
        data_scope={"user_query": user_query, "k": k, "embedding_model": MODEL_NAME, "matched_tables": tables},
        executed_queries=[],
        returned_count=len(contexts),
        is_truncated=False,
        summary={"recommended_tools": recommend_tools(contexts),
                 "top_confidence": max((c["confidence_score"] or 0) for c in contexts)},
        notes=["Prefer the top recommended parameterized tool. Write SQL only if no tool can express the "
               "question, then submit it to execute_guarded_readonly_sql.",
               "confidence_score is cosine similarity between the question and the column description "
               "(all-MiniLM-L6-v2); required_context columns are the matched tables' time, id and join keys."],
        rows=[{"confidence_score": c["confidence_score"], "match_type": c["match_type"], **c["schema_match"]}
              for c in contexts],
    ).model_dump()
    envelope["knn_schema_matches"] = contexts
    envelope["sandbox_prompt"] = agent.assemble_prompt(user_query, contexts)
    return envelope


def execute_guarded_readonly_sql(user_query: str, candidate_sql: str, max_limit: int = 50) -> dict[str, Any]:
    """Grounds the question, validates the candidate SQL and runs it as the read-only role."""
    from src.db import DATASET_TABLES, EvidenceBuilder, reader_dsn
    from src.semantic_layer import extract_relevant_schema_context, recommend_tools

    contexts = extract_relevant_schema_context(user_query, k=4)
    agent = SereneSQLAgent()
    report = agent.validate(candidate_sql, max_limit)  # raises UnsafeQueryException

    notes = ["Executed as the SELECT-only role log_reader after deterministic guardrails; prefer the "
             "parameterized tools when one fits the question."]
    with EvidenceBuilder("execute_guarded_readonly_sql", dsn=reader_dsn()) as eb:
        rows = eb.query("guarded read-only query", report.sql, timeout_s=GUARDED_TIMEOUT_S)
        try:
            total = int(eb.query("exact row count without LIMIT",
                                 f"SELECT COUNT(*) AS n FROM ({report.sql_without_limit}) AS guarded_q",
                                 timeout_s=GUARDED_TIMEOUT_S)[0]["n"])
        except TimeoutError:
            total = None
            notes.append(f"Exact row count timed out after {GUARDED_TIMEOUT_S} s; is_truncated is based on LIMIT.")
        truncated = total > len(rows) if total is not None else len(rows) >= report.limit
        corpus = {table: eb.corpus_rows(ds) for ds, table in DATASET_TABLES.items() if table in report.tables
                  or f"{table}_idx" in report.tables}
        return eb.envelope(
            data_scope={"tables": report.tables, "total_corpus_rows": corpus, "user_query": user_query},
            rows=rows,
            total_matching_count=total,
            is_truncated=truncated,
            truncation_notice=f"Showing {len(rows)} of {total:,} result rows (LIMIT {report.limit})."
            if truncated and total is not None else None,
            summary={"guardrail_checks": report.checks, "limit_action": report.limit_action,
                     "original_limit": report.original_limit, "recommended_tools": recommend_tools(contexts)},
            notes=notes,
            extra={"executed_sql": report.sql, "candidate_sql": candidate_sql, "knn_schema_matches": contexts},
        )
