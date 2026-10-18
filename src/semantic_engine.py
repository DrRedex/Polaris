"""Stable Step 1 API; the established implementation lives in semantic_layer."""
from __future__ import annotations

from typing import Any

from src import semantic_layer as _impl

REFERENCE_SCHEMA_GLOSSARY = [
    {"table": "service_metrics", "kind": "conceptual_reference", "description": "Conceptual service latency, error-rate and resource metrics; not a live table."},
    {"table": "incident_logs", "kind": "conceptual_reference", "description": "Conceptual incident records; not a live table."},
    {"table": "team_assignments", "kind": "conceptual_reference", "description": "Conceptual team ownership mapping; the live table is team_ownership."},
]


def get_embedding_model():
    """Return the process-wide, thread-safe model; never substitute synthetic vectors."""
    return _impl.get_model()


def extract_relevant_schema_context(user_query: str, k: int = 3,
                                    dataset_filter: str | None = None) -> list[dict[str, Any]]:
    if not isinstance(user_query, str) or not user_query.strip():
        return []
    if k <= 0:
        return []
    return _impl.extract_relevant_schema_context(user_query, k=k, dataset_filter=dataset_filter)


__all__ = ["get_embedding_model", "extract_relevant_schema_context", "REFERENCE_SCHEMA_GLOSSARY"]
