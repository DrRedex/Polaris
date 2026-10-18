"""Parallel structured evidence and explicitly non-authoritative runbook clues."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Callable
import numpy as np

from src.semantic_engine import get_embedding_model
from src.db import EvidenceBuilder, reader_dsn, record_evidence, to_llm_compact_envelope, to_jsonable

EVIDENCE_KNOWLEDGE_BASE = [
    {"id": "INC-412", "text": "Reference incident record: recurring BGL kernel machine-check and parity errors; validate counts in SereneDB."},
    {"id": "SLACK-OPS-99", "text": "Reference operations discussion: investigate nova API latency and connection pool exhaustion."},
    {"id": "WIKI-DB-01", "text": "Reference database note: compare service trends and ownership before escalation."},
    {"id": "BGL-KERNEL-MCE", "text": "Runbook hypothesis: KERNEL machine-check/parity errors; inspect node, severity and nearby sequence."},
    {"id": "BGL-LINKCARD-MIDPLANE", "text": "Runbook hypothesis: LINKCARD faults may cluster by midplane; compare affected node locations."},
    {"id": "BGL-MMCS-TIMEOUT", "text": "Runbook hypothesis: MMCS control-plane timeouts; check time buckets and control-plane dependencies."},
    {"id": "OS-NOVA-COMPUTE-SPAWN", "text": "Runbook hypothesis: nova-compute libvirt VM-spawn lag; compare operation duration outliers."},
    {"id": "OS-NOVA-API-POOL", "text": "Runbook hypothesis: nova-api HTTP 500s may accompany connection-pool exhaustion; inspect measured status and latency."},
    {"id": "OS-NOVA-SCHED-LOCK", "text": "Runbook hypothesis: nova-scheduler placement-lock contention; inspect scheduler errors and timing."},
]


def _db_execute(sql: str) -> list[dict[str, Any]]:
    with EvidenceBuilder("dual_engine_structured", dsn=reader_dsn()) as eb:
        rows = eb.query("validated SQL", sql)
        return {"rows": rows, "sql_execution_ms": eb.queries[-1].execution_time_ms,
                "executed_sql": eb.queries[-1].sql}


class DualEnginePipeline:
    def __init__(self, kb: list[dict[str, str]] | None = None):
        self.kb = kb or EVIDENCE_KNOWLEDGE_BASE

    def _execute_unstructured_knn(self, anchor: str, k: int = 2) -> list[dict[str, Any]]:
        model = get_embedding_model()
        docs = [x["text"] for x in self.kb]
        vectors = np.asarray(model.encode(docs + [anchor], show_progress_bar=False), dtype=np.float32)
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        norms[norms == 0] = 1
        scores = np.dot(vectors[-1:] / norms[-1:], (vectors[:-1] / norms[:-1]).T)[0]
        indices = np.argsort(scores)[::-1][:max(0, k)]
        return [{**self.kb[int(i)], "similarity": float(scores[i]), "source_type": "RUNBOOK_HYPOTHESIS_CLUE",
                 "authority": "unverified reference/runbook content"} for i in indices]

    def retrieve_all_evidence(self, secured_sql: str, search_context_anchor: str,
                              db_executor_fn: Callable[[str], Any] | None = None) -> dict[str, Any]:
        executor = db_executor_fn or _db_execute
        results: dict[str, Any] = {"status": "partial", "structured": None, "runbook_hypotheses": [], "errors": []}
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = {pool.submit(executor, secured_sql): "structured",
                       pool.submit(self._execute_unstructured_knn, search_context_anchor, 2): "knn"}
            for future in as_completed(futures):
                key = futures[future]
                try:
                    value = future.result()
                    if key == "structured":
                        rows = value.get("rows", []) if isinstance(value, dict) else value
                        sql_ms = value.get("sql_execution_ms") if isinstance(value, dict) else None
                        executed_sql = (value.get("executed_sql") if isinstance(value, dict) else None) or secured_sql
                        rows = [to_jsonable(dict(r)) for r in rows]
                        eid = record_evidence({"operation": "validated SQL", "source": "SereneDB",
                                               "executed_sql": executed_sql, "parameters": [], "rows": rows,
                                               "row_count": len(rows), "elapsed_ms": sql_ms,
                                               "recorded_at": datetime.now(timezone.utc).isoformat(),
                                               "role_context": "role-scoped connection or injected executor",
                                               "evidence_type": "observed_database_result", "status": "success"})
                        results["structured"] = {"source_type": "OBSERVED_SERENEDB_FACT", "evidence_id": eid,
                                                 "data": to_llm_compact_envelope(rows), "sql_execution_ms": sql_ms,
                                                 "executed_sql": executed_sql}
                    else:
                        results["runbook_hypotheses"] = value
                except Exception as exc:
                    if key == "structured":
                        record_evidence({"operation": "validated SQL", "source": "SereneDB",
                                         "executed_sql": secured_sql, "status": "error", "error": str(exc),
                                         "recorded_at": datetime.now(timezone.utc).isoformat(),
                                         "evidence_type": "observed_database_error"})
                    results["errors"].append({"worker": key, "error": str(exc),
                                              **({"sql": secured_sql} if key == "structured" else {})})
        results["status"] = "success" if not results["errors"] else "partial" if results["structured"] or results["runbook_hypotheses"] else "error"
        return results


def generate_management_summary(user_query: str, combined_evidence: dict[str, Any], role: str = "engineer") -> dict[str, Any]:
    role = role.lower()
    observed = combined_evidence.get("structured")
    clues = combined_evidence.get("runbook_hypotheses", [])
    if role == "manager":
        # Never pass detail rows or trace identifiers through this response surface.
        observed = {k: v for k, v in (observed or {}).items() if k != "data"}
    return {"role": role, "user_query": user_query, "observed_facts": observed,
            "runbook_hypotheses": [{"id": c["id"], "label": "hypothesis, not measured fact",
                                    "similarity": c["similarity"]} for c in clues],
            "status": combined_evidence.get("status"), "errors": combined_evidence.get("errors", [])}
