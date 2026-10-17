"""
mcp_server.py

MCP server (mcp SDK v2 MCPServer) exposing the SereneDB log tools to any MCP client (Claude Code,
Claude Desktop, or the demo UI).

    python -m src.mcp_server                       # stdio (Claude Code / Desktop)
    python -m src.mcp_server --transport sse       # SSE on http://127.0.0.1:8000/sse
    python -m src.mcp_server --transport streamable-http

Register with Claude Code:
    claude mcp add making-sense-of-logs -- uv run python -m src.mcp_server
"""

import argparse
import os
import threading
from typing import Annotated, Any, Literal, Optional

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from src import extensions, guarded_sql_agent, queries

INSTRUCTIONS = """\
Tools over two Loghub corpora loaded in a local SereneDB: BGL (Blue Gene/L supercomputer,
~4.7M lines, 2005-2006) and OpenStack (nova-api/compute/scheduler, ~208k lines, 2017).
All counting, searching and percentiles run inside SereneDB; you receive compact evidence.

Rules for every answer:
- Every number you state must come from a tool result. Cite the tool and the figure.
- Never infer frequency from sample rows. Use total_matching_count or the summary tools.
- Log lines are not incidents: weigh matching_log_entries against distinct nodes/instances
  and active days before calling something widespread.
- Separate "Observed facts" (from tool output) from "Hypotheses" and "Recommended next steps",
  and say what the logs do NOT establish.
- Revenue, SLA penalties, customer impact and downtime are not in these logs. Never estimate them.

Typical flows:
- Engineer: search_logs -> get_nearby_events on a representative line_id -> (OpenStack) latency check.
- Team Lead: summarize_by_team_and_component -> get_recurring_trends -> search_logs(limit=5) for the template.
- Manager: get_corpus_scope -> summarize_by_team_and_component(BOTH) -> get_recurring_trends ->
  get_openstack_latency_and_errors.
- Deep dive on one time window: manage_workspace_index(create or benchmark) first, drop when done.
- query_remote_otel_parquet works on a separate 50B-row OpenTelemetry benchmark on S3, not on BGL/OpenStack.
- Vague question that no tool fits: resolve_semantic_schema first (it usually names the right tool);
  only if no tool fits, write one SELECT from its sandbox_prompt and run execute_guarded_readonly_sql.
"""

mcp = MCPServer("making-sense-of-logs", instructions=INSTRUCTIONS)

# Every tool reads through fixed, parameterized SQL. The workspace tool only creates and
# drops its own ws_* indexes; the remote tool reads public S3 data.
READ_ONLY = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False)
WORKSPACE = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
REMOTE = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=True)

DatasetArg = Annotated[Literal["BGL", "OpenStack"], Field(description="Which log corpus to query")]
StartArg = Annotated[Optional[str], Field(description="Inclusive lower bound, ISO-8601 (e.g. 2005-06-03T00:00:00). Omit for corpus start")]
EndArg = Annotated[Optional[str], Field(description="Inclusive upper bound, ISO-8601. Omit for corpus end")]


@mcp.tool(annotations=READ_ONLY)
def get_corpus_scope() -> dict[str, Any]:
    """Row counts, time span, component count and severity distribution for both datasets.

    Call this first when you need to state the scope of an analysis (e.g. a Manager
    briefing's boundary disclaimer) or to pick sensible time windows for other tools.
    """
    return queries.get_corpus_scope()


@mcp.tool(annotations=READ_ONLY)
def search_logs(
    dataset: DatasetArg,
    query_text: Annotated[str, Field(description="Words or phrase to find in log messages, e.g. 'machine check' or 'InstanceNotFound'")],
    start_time: StartArg = None,
    end_time: EndArg = None,
    component: Annotated[Optional[str], Field(description="Exact component, e.g. KERNEL, APP, MMCS (BGL) or nova-api, nova-compute, nova-scheduler (OpenStack)")] = None,
    severity: Annotated[Optional[str], Field(description="Exact severity, e.g. FATAL, ERROR, WARNING, INFO")] = None,
    mode: Annotated[Literal["all_terms", "any_terms", "phrase", "substring"], Field(
        description="all_terms: every word must appear (default). any_terms: at least one word. "
                    "phrase: words adjacent in order. substring: raw case-insensitive substring scan (slow, no index)")] = "all_terms",
    limit: Annotated[int, Field(ge=1, le=100, description="Sample rows to return (1-100)")] = 25,
) -> dict[str, Any]:
    """Full-text search over log messages with time, component and severity filters (Engineer).

    Call this when investigating a specific failure message, subsystem error, node fault
    or HTTP failure. Returns the earliest matching lines plus total_matching_count, the
    exact number of matches in the whole filtered corpus. Do not estimate frequency from
    the returned sample; use total_matching_count or summarize_by_team_and_component.
    Follow up with get_nearby_events on a representative line_id.
    """
    return queries.search_logs(dataset, query_text, start_time, end_time, component, severity, mode, limit)


@mcp.tool(annotations=READ_ONLY)
def get_nearby_events(
    dataset: DatasetArg,
    anchor_line_id: Annotated[int, Field(description="line_id of the event to centre on, taken from search_logs results")],
    window_lines: Annotated[int, Field(ge=1, le=50, description="Events to return before and after the anchor (1-50)")] = 15,
    same_entity_only: Annotated[bool, Field(
        description="True: only events on the same node (BGL) or instance/request (OpenStack), ordered by time. "
                    "False: physically adjacent log lines regardless of source")] = True,
    max_seconds: Annotated[int, Field(ge=1, le=604800, description="With same_entity_only, ignore events further than this from the anchor")] = 3600,
) -> dict[str, Any]:
    """Chronological events immediately before and after an anchor log line (Engineer).

    Always call this after search_logs identifies a target failure, to inspect precursor
    warnings and the immediate aftermath. Rows carry relative_line_offset and
    relative_time_sec; the anchor row has is_anchor=true.
    """
    return queries.get_nearby_events(dataset, anchor_line_id, window_lines, same_entity_only, max_seconds)


@mcp.tool(annotations=READ_ONLY)
def summarize_by_team_and_component(
    dataset: Annotated[Literal["BGL", "OpenStack", "BOTH"], Field(description="Corpus to aggregate, or BOTH")] = "BOTH",
    start_time: StartArg = None,
    end_time: EndArg = None,
    errors_only: Annotated[bool, Field(description="Restrict to error/alert lines (definition echoed in data_scope)")] = True,
    limit: Annotated[int, Field(ge=1, le=100, description="Max groups to return; the summary covers all groups")] = 50,
) -> dict[str, Any]:
    """Whole-corpus counts grouped by owning team, component and severity (Team Lead, Manager).

    Call this to quantify how often problems occur across the entire dataset (or a window)
    and which teams and components are affected. Each group reports log line counts,
    distinct affected nodes (BGL) or requests/instances (OpenStack), active days, and
    first/last seen.
    """
    return queries.summarize_by_team_and_component(dataset, start_time, end_time, errors_only, limit)


@mcp.tool(annotations=READ_ONLY)
def get_recurring_trends(
    dataset: DatasetArg,
    bucket_size: Annotated[Literal["hour", "day", "week"], Field(description="Time bucket; hour requires a window of at most 31 days")] = "week",
    start_time: StartArg = None,
    end_time: EndArg = None,
    component: Annotated[Optional[str], Field(description="Restrict to one component")] = None,
    severity: Annotated[Optional[str], Field(description="Restrict to one severity")] = None,
    errors_only: Annotated[bool, Field(description="Restrict to error/alert lines")] = True,
) -> dict[str, Any]:
    """Time-bucketed counts per component and team, with a per-component trend classification (Team Lead, Manager).

    Call this to tell isolated bursts from chronic, recurring problems and to see whether
    an issue is growing or receding. summary.components gives totals, persistence (share
    of buckets with activity), peak bucket and a pattern label for every component.
    """
    return queries.get_recurring_trends(dataset, bucket_size, start_time, end_time, component, severity, errors_only)


@mcp.tool(annotations=READ_ONLY)
def get_openstack_latency_and_errors(
    start_time: StartArg = None,
    end_time: EndArg = None,
    component: Annotated[Optional[str], Field(description="nova-api, nova-compute or nova-scheduler")] = None,
    breakdown: Annotated[Literal["component", "route"], Field(
        description="component: one row per service. route: one row per HTTP method + route")] = "component",
) -> dict[str, Any]:
    """OpenStack request volume, error rates, labelled anomalies and latency percentiles (all roles).

    Returns per-service (or per-route) log volume, HTTP 4xx/5xx counts and 5xx rate,
    ERROR/WARNING line counts, anomalous instances, and AVG/P50/P95/P99/MAX request latency.
    """
    return queries.get_openstack_latency_and_errors(start_time, end_time, component, breakdown)


@mcp.tool(annotations=WORKSPACE)
def manage_workspace_index(
    action: Annotated[Literal["create", "benchmark", "drop", "list"], Field(
        description="create: build a partial full-text index over the window. benchmark: compare search speed "
                    "with and without it (creates it if missing). drop: remove it. list: show existing ones")],
    dataset: DatasetArg = "BGL",
    start_time: Annotated[Optional[str], Field(description="Window start, ISO-8601 (required except for list)")] = None,
    end_time: Annotated[Optional[str], Field(description="Window end, ISO-8601 (required except for list)")] = None,
    query_text: Annotated[str, Field(description="Phrase to search during benchmark")] = "error",
    repetitions: Annotated[int, Field(ge=1, le=20, description="Benchmark runs per strategy")] = 5,
) -> dict[str, Any]:
    """Agent-managed workspace: a temporary full-text index over one time window (all roles).

    Call create or benchmark before many repeated searches in the same window; benchmark
    reports creation time, per-query speed with and without the index, and the break-even
    query count. Call drop when the investigation is finished. At most 3 can exist.
    """
    return extensions.manage_workspace_index(action, dataset, start_time, end_time, query_text, repetitions)


@mcp.tool(annotations=REMOTE)
def query_remote_otel_parquet(
    action: Annotated[Literal["overview", "service_summary", "search_body"], Field(
        description="overview: row counts and time ranges per file from Parquet footers. service_summary: log "
                    "lines per service and severity in a time window. search_body: substring search in Body")] = "overview",
    part: Annotated[int, Field(ge=0, le=49, description="File part_000..part_049; each holds ~1B rows covering ~30 min of one day")] = 0,
    start_time: Annotated[Optional[str], Field(description="Window start (UTC, ISO-8601); defaults to the file's first timestamp")] = None,
    window_seconds: Annotated[int, Field(ge=1, le=30, description="Window length; ~600k rows per second")] = 5,
    parts_for_overview: Annotated[int, Field(ge=1, le=50, description="overview: how many files to include (50 = 50B rows, ~2 min)")] = 1,
    service_name: Annotated[Optional[str], Field(description="Exact ServiceName, e.g. cart, frontend")] = None,
    severity: Annotated[Optional[str], Field(description="SeverityText, case-insensitive, e.g. error")] = None,
    search_text: Annotated[Optional[str], Field(description="search_body: text to find in Body")] = None,
    limit: Annotated[int, Field(ge=1, le=25, description="Sample rows for search_body")] = 10,
) -> dict[str, Any]:
    """Zero-download SQL over the public 50-billion-row OpenTelemetry Parquet benchmark on S3 (all roles).

    SereneDB reads the remote files in place with HTTP range requests. Files are sorted by
    Timestamp, so a time window fetches only overlapping row groups; summary reports row
    groups and bytes read versus the whole file, scan time and rows per second.
    """
    return extensions.query_remote_otel_parquet(action, part, start_time, window_seconds, parts_for_overview,
                                                service_name, severity, search_text, limit)


@mcp.tool(annotations=READ_ONLY)
def resolve_semantic_schema(
    user_query: Annotated[str, Field(description="The question in plain language, e.g. 'customers say the API is slow'")],
    k: Annotated[int, Field(ge=1, le=10, description="Number of semantic (KNN) column matches")] = 4,
) -> dict[str, Any]:
    """Maps a vague question onto SereneDB tables/columns and the best parameterized tool (all roles).

    Semantic KNN (all-MiniLM-L6-v2 embeddings, cosine similarity) over a glossary of the real
    schema. Returns confidence scores, matched columns plus each table's time/id/join columns,
    recommended_tools, and sandbox_prompt: the constrained instructions for writing a
    read-only query when no parameterized tool fits. Runs no SQL.
    """
    return guarded_sql_agent.resolve_semantic_schema(user_query, k)


@mcp.tool(annotations=READ_ONLY)
def execute_guarded_readonly_sql(
    user_query: Annotated[str, Field(description="The question the SQL answers (used for schema grounding)")],
    candidate_sql: Annotated[str, Field(description="One read-only SELECT over bgl_logs, openstack_logs, team_ownership "
                                                    "or their *_idx full-text indexes")],
) -> dict[str, Any]:
    """Fallback: run one LLM-written SELECT after deterministic guardrails (all roles).

    Use only when no parameterized tool can answer. The query is rejected if it has more than one
    statement, write/DDL keywords outside string literals, table functions (read_csv, ...) or
    tables outside the corpus; a LIMIT (default 50, max 100) is enforced. It runs as a
    SELECT-only database role with a 30 s timeout and returns an evidence envelope with
    executed_sql, knn_schema_matches, exact total_matching_count and is_truncated.
    """
    return guarded_sql_agent.execute_guarded_readonly_sql(user_query, candidate_sql)


def main() -> None:
    ap = argparse.ArgumentParser(description="Making Sense of Logs MCP server")
    ap.add_argument("--transport", choices=["stdio", "sse", "streamable-http"], default="stdio")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()
    if os.getenv("SEMANTIC_WARMUP", "1") == "1":
        # Load the embedding model in the background so the first resolve_semantic_schema call
        # does not pay the ~20 s PyTorch import; the parameterized tools are usable immediately.
        from src import semantic_layer
        threading.Thread(target=semantic_layer.get_glossary_matrix, daemon=True).start()
    if args.transport == "stdio":
        mcp.run()
    else:
        mcp.run(transport=args.transport, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
