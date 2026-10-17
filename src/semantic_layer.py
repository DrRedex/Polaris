"""
semantic_layer.py  (Step 1: semantic schema grounding)

Maps a vague natural-language question ("the cloud API feels slow lately") onto the
SereneDB tables and columns that can answer it, and onto the parameterized MCP tool
that should answer it first. A sentence-embedding model (all-MiniLM-L6-v2) encodes a
glossary of the real schema once; each question is matched against it by cosine
similarity (exact KNN with NumPy; the glossary has a few dozen rows).

The model and the glossary matrix are loaded lazily on first use and cached for the
life of the process, so importing this module (e.g. when the MCP server starts) is cheap.
"""

import logging
import os
import threading
from typing import Any

import numpy as np

MODEL_NAME = os.getenv("SEMANTIC_MODEL", "all-MiniLM-L6-v2")

# One entry per column the agent may reason about. "recommended_mcp_tool" is the
# parameterized tool that answers questions about this column; guarded SQL is the fallback.
SERENE_DB_GLOSSARY: list[dict[str, str]] = [
    # --- bgl_logs: Blue Gene/L supercomputer RAS log, 4.7M lines, 2005-2006 -----------------
    {"table": "bgl_logs", "column": "line_id", "type": "BIGINT",
     "description": "Primary key, the line number in the original BGL log. Anchor for looking at events just before and after a failure.",
     "recommended_mcp_tool": "get_nearby_events"},
    {"table": "bgl_logs", "column": "event_time", "type": "TIMESTAMP",
     "description": "When the supercomputer event happened. Use for time windows, timelines, when did it start, daily or weekly trends.",
     "recommended_mcp_tool": "get_recurring_trends"},
    {"table": "bgl_logs", "column": "node_id", "type": "VARCHAR",
     "description": "Hardware location of the compute node, e.g. R02-M1-N0-C:J12-U11. Use for which machine, rack, node or midplane failed and how many nodes are affected.",
     "recommended_mcp_tool": "get_nearby_events"},
    {"table": "bgl_logs", "column": "component", "type": "VARCHAR",
     "description": "Supercomputer subsystem that logged the event: KERNEL, APP, MMCS, DISCOVERY, LINKCARD, HARDWARE, MONITOR. Use for which part of the cluster is failing.",
     "recommended_mcp_tool": "summarize_by_team_and_component"},
    {"table": "bgl_logs", "column": "severity", "type": "VARCHAR",
     "description": "BGL log level: INFO, WARNING, ERROR, SEVERE, FATAL, FAILURE. Use for fatal crashes, critical errors, how serious problems are.",
     "recommended_mcp_tool": "summarize_by_team_and_component"},
    {"table": "bgl_logs", "column": "label", "type": "VARCHAR",
     "description": "Labelled alert category ('-' means normal), e.g. KERNDTLB data TLB error, KERNSTOR storage, APPREAD read failure. Use for known alert types and incident categories.",
     "recommended_mcp_tool": "summarize_by_team_and_component"},
    {"table": "bgl_logs", "column": "is_anomaly", "type": "BOOLEAN",
     "description": "True when the BGL line carries a labelled alert. Use for counting real alerts versus normal noise on the supercomputer.",
     "recommended_mcp_tool": "summarize_by_team_and_component"},
    {"table": "bgl_logs", "column": "message", "type": "VARCHAR",
     "description": "Free-text body of the supercomputer log line, full-text indexed. Use for specific error messages such as machine check, parity error, TLB error, ciod failures, kernel panics.",
     "recommended_mcp_tool": "search_logs"},
    {"table": "bgl_logs", "column": "date_day", "type": "DATE",
     "description": "Calendar day of the BGL event. Use for how many days a problem was active and day-by-day counts.",
     "recommended_mcp_tool": "get_recurring_trends"},
    # --- openstack_logs: nova-api / nova-compute / nova-scheduler, 208k lines, 2017 ---------------
    {"table": "openstack_logs", "column": "line_id", "type": "BIGINT",
     "description": "Primary key of the OpenStack log line. Anchor for the events just before and after a cloud failure.",
     "recommended_mcp_tool": "get_nearby_events"},
    {"table": "openstack_logs", "column": "event_time", "type": "TIMESTAMP",
     "description": "When the cloud platform event happened. Use for time windows, hourly trends and incident timelines in OpenStack.",
     "recommended_mcp_tool": "get_recurring_trends"},
    {"table": "openstack_logs", "column": "latency_sec", "type": "DOUBLE",
     "description": "Response time of a nova-api HTTP request in seconds. Use for slow requests, lag, sluggish API, timeouts, latency spikes, p95 and p99 response times.",
     "recommended_mcp_tool": "get_openstack_latency_and_errors"},
    {"table": "openstack_logs", "column": "http_status", "type": "INTEGER",
     "description": "HTTP status code of a nova-api request (200, 404, 500). Use for failed API calls, 5xx server errors, error rates, broken endpoints.",
     "recommended_mcp_tool": "get_openstack_latency_and_errors"},
    {"table": "openstack_logs", "column": "http_route", "type": "VARCHAR",
     "description": "Normalized API endpoint such as /v2/{id}/servers/detail. Use for which API endpoint or route is slow or failing.",
     "recommended_mcp_tool": "get_openstack_latency_and_errors"},
    {"table": "openstack_logs", "column": "severity", "type": "VARCHAR",
     "description": "OpenStack log level: INFO, WARNING, ERROR. Use for errors and warnings in the cloud services.",
     "recommended_mcp_tool": "summarize_by_team_and_component"},
    {"table": "openstack_logs", "column": "component", "type": "VARCHAR",
     "description": "Cloud service that logged the line: nova-api, nova-compute or nova-scheduler. Use for which cloud service, hypervisor or scheduler is affected.",
     "recommended_mcp_tool": "summarize_by_team_and_component"},
    {"table": "openstack_logs", "column": "request_id", "type": "VARCHAR",
     "description": "OpenStack request identifier req-<uuid> that ties together all log lines of one API request. Use for tracing a single request end to end.",
     "recommended_mcp_tool": "get_nearby_events"},
    {"table": "openstack_logs", "column": "instance_id", "type": "VARCHAR",
     "description": "Virtual machine instance UUID. Use for which VM was affected, VM lifecycle, spawning, deleting or migrating instances.",
     "recommended_mcp_tool": "get_nearby_events"},
    {"table": "openstack_logs", "column": "anomaly_label", "type": "VARCHAR",
     "description": "'Anomaly' for lines on VM instances with injected failures (anomaly_labels.txt), else 'Normal'. Use for known faulty VMs and labelled cloud anomalies.",
     "recommended_mcp_tool": "get_openstack_latency_and_errors"},
    {"table": "openstack_logs", "column": "message", "type": "VARCHAR",
     "description": "Free-text body of the OpenStack log line, full-text indexed. Use for specific cloud errors such as instance not found, failed to spawn, image cache, claims.",
     "recommended_mcp_tool": "search_logs"},
    {"table": "openstack_logs", "column": "tenant_id", "type": "VARCHAR",
     "description": "Project or tenant that issued the API request. Use for which customer project or tenant is calling the API.",
     "recommended_mcp_tool": "get_openstack_latency_and_errors"},
    # --- team_ownership: fictional mapping (dataset, component) -> owning team -------------------
    {"table": "team_ownership", "column": "team_name", "type": "VARCHAR",
     "description": "Engineering team that owns a component. Use for who owns this, which team should fix it, accountability and routing work.",
     "recommended_mcp_tool": "summarize_by_team_and_component"},
    {"table": "team_ownership", "column": "escalation_tier", "type": "VARCHAR",
     "description": "Criticality tier of the owning team (Tier-1-Critical, Tier-2-High, Tier-3-Standard). Use for priority, urgency, escalation and what to fix first.",
     "recommended_mcp_tool": "summarize_by_team_and_component"},
    {"table": "team_ownership", "column": "team_lead_slack", "type": "VARCHAR",
     "description": "Slack channel of the owning team, e.g. #team-core-os. Use for who to contact, on-call escalation path, where to raise an issue.",
     "recommended_mcp_tool": "summarize_by_team_and_component"},
    {"table": "team_ownership", "column": "component_scope", "type": "VARCHAR",
     "description": "What the owned component covers, e.g. compute-node kernel, CPU, cache parity. Use for understanding responsibilities of a team.",
     "recommended_mcp_tool": "summarize_by_team_and_component"},
]

# Columns every query on a table needs for filtering and joining; appended to the
# semantic matches for each matched table.
TABLE_KEY_COLUMNS: dict[str, list[str]] = {
    "bgl_logs": ["event_time", "line_id", "node_id", "component"],
    "openstack_logs": ["event_time", "line_id", "request_id", "component"],
    "team_ownership": ["dataset", "component"],
}

TABLE_NOTES: dict[str, str] = {
    "bgl_logs": "Full-text: FROM bgl_logs_idx i JOIN bgl_logs l ON l.line_id = i.line_id WHERE i.message @@ ts_phrase('...').",
    "openstack_logs": "Full-text: FROM openstack_logs_idx i JOIN openstack_logs l ON l.line_id = i.line_id WHERE i.message @@ ts_phrase('...').",
    "team_ownership": "Join: team_ownership o ON o.dataset = 'BGL' (or 'OpenStack') AND o.component = l.component.",
}

_EXTRA_KEY_COLUMNS = {
    ("team_ownership", "dataset"): {"type": "VARCHAR", "description": "'BGL' or 'OpenStack'; join key together with component."},
    ("team_ownership", "component"): {"type": "VARCHAR", "description": "Component name; joins to bgl_logs.component or openstack_logs.component."},
}

_lock = threading.Lock()
_state: dict[str, Any] = {}


def _anchor_text(item: dict[str, str]) -> str:
    return f"Table: {item['table']} | Column: {item['column']} | Meaning: {item['description']}"


def get_model():
    """The embedding model, loaded once per process (thread-safe). Uses the local Hugging Face
    cache when present (no network round-trips) and downloads it (~90 MB) only the first time."""
    if "model" not in _state:
        with _lock:
            if "model" not in _state:
                os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
                os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
                for name in ("httpx", "huggingface_hub", "sentence_transformers", "transformers"):
                    logging.getLogger(name).setLevel(logging.WARNING)
                from sentence_transformers import SentenceTransformer  # heavy import, deferred
                try:
                    model = SentenceTransformer(MODEL_NAME, device="cpu", local_files_only=True)
                except Exception:
                    model = SentenceTransformer(MODEL_NAME, device="cpu")
                _state["model"] = model
    return _state["model"]


def _encode(texts):
    return get_model().encode(texts, show_progress_bar=False)


def get_glossary_matrix() -> np.ndarray:
    """L2-normalized glossary embeddings, computed once per process."""
    if "matrix" not in _state:
        model = get_model()
        with _lock:
            if "matrix" not in _state:
                vectors = model.encode([_anchor_text(item) for item in SERENE_DB_GLOSSARY], show_progress_bar=False)
                matrix = np.asarray(vectors, dtype=np.float32)
                _state["matrix"] = matrix / np.linalg.norm(matrix, axis=1, keepdims=True)
    return _state["matrix"]


def _lookup(table: str, column: str) -> dict[str, str]:
    for item in SERENE_DB_GLOSSARY:
        if item["table"] == table and item["column"] == column:
            return item
    extra = _EXTRA_KEY_COLUMNS[(table, column)]
    return {"table": table, "column": column, "type": extra["type"], "description": extra["description"],
            "recommended_mcp_tool": "summarize_by_team_and_component"}


def extract_relevant_schema_context(manager_query: str, k: int = 4) -> list[dict[str, Any]]:
    """
    KNN over the schema glossary: returns the top-k semantic matches (by cosine
    similarity), followed by the timestamp / identifier / join columns of every matched
    table so downstream queries have full filter and join context.

    Each item: {"confidence_score": float | None, "match_type": "semantic" | "required_context",
                "schema_match": {table, column, type, description, recommended_mcp_tool}}
    """
    manager_query = (manager_query or "").strip()
    if not manager_query or len(manager_query) > 1000:
        raise ValueError("query must be 1-1000 characters")
    k = max(1, min(int(k), len(SERENE_DB_GLOSSARY)))

    query_vector = np.asarray(_encode(manager_query), dtype=np.float32).reshape(1, -1)
    norm_query = query_vector / np.linalg.norm(query_vector, axis=1, keepdims=True)
    similarities = np.dot(norm_query, get_glossary_matrix().T)[0]
    top_indices = np.argsort(similarities)[::-1][:k]

    extracted: list[dict[str, Any]] = [
        {"confidence_score": round(float(similarities[i]), 4), "match_type": "semantic",
         "schema_match": SERENE_DB_GLOSSARY[i]}
        for i in top_indices
    ]
    seen = {(e["schema_match"]["table"], e["schema_match"]["column"]) for e in extracted}
    for table in dict.fromkeys(e["schema_match"]["table"] for e in extracted):
        for column in TABLE_KEY_COLUMNS[table]:
            if (table, column) not in seen:
                seen.add((table, column))
                extracted.append({"confidence_score": None, "match_type": "required_context",
                                  "schema_match": _lookup(table, column)})
    return extracted


def recommend_tools(extracted: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ranks MCP tools by the summed confidence of the semantic matches that point to them."""
    scores: dict[str, float] = {}
    for e in extracted:
        if e["match_type"] == "semantic":
            tool = e["schema_match"]["recommended_mcp_tool"]
            scores[tool] = scores.get(tool, 0.0) + max(e["confidence_score"], 0.0)
    return [{"tool": t, "score": round(s, 4)} for t, s in sorted(scores.items(), key=lambda kv: -kv[1])]
