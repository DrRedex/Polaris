"""Repeatable workspace-index benchmark. Live DB writes require --live; no dataset is downloaded."""
from __future__ import annotations
import argparse, json, math, statistics, sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="create/drop a temporary SereneDB partial index")
    ap.add_argument("--start", default="2005-06-03T00:00:00")
    ap.add_argument("--end", default="2005-06-17T00:00:00")
    ap.add_argument("--output", default=str(ROOT / "docs" / "PERFORMANCE_AND_AWARDS.md"))
    args = ap.parse_args()
    timestamp = datetime.now(timezone.utc).isoformat()
    if not args.live:
        result = {"status": "skipped", "reason": "Live indexing benchmark requires --live.", "timestamp": timestamp}
    else:
        from src.db import EvidenceBuilder, to_llm_compact_envelope
        from src.workspace_indexing import create_time_range_index, drop_time_range_index
        from src.queries import _parse_time
        from src.db import check_connection
        s, e = _parse_time(args.start, "start"), _parse_time(args.end, "end")
        from src.workspace_indexing import _registry
        from src.workspace_indexing import create_time_range_index as create
        from src.workspace_indexing import drop_time_range_index as drop
        table_name = f"ws_bgl_logs_{s:%Y%m%d%H%M%S}_{e:%Y%m%d%H%M%S}"
        with EvidenceBuilder("benchmark preflight") as eb:
            anchor = eb.query("nearby context anchor", "SELECT line_id FROM bgl_logs WHERE event_time >= %s::TIMESTAMP AND event_time < %s::TIMESTAMP ORDER BY line_id LIMIT 1", [args.start,args.end])
        anchor_id = int(anchor[0]["line_id"]) if anchor else 0
        definitions = [
        ("full_text_machine_check", "SELECT COUNT(*) AS n FROM bgl_logs_idx i JOIN bgl_logs l ON l.line_id=i.line_id WHERE i.message @@ ts_phrase(%s) AND l.event_time >= %s::TIMESTAMP AND l.event_time < %s::TIMESTAMP", ["machine check", args.start, args.end]),
            ("nearby_context", "SELECT line_id,event_time,node_id,component,severity FROM bgl_logs WHERE line_id BETWEEN %s AND %s AND event_time >= %s::TIMESTAMP AND event_time < %s::TIMESTAMP ORDER BY line_id", [max(0,anchor_id-10),anchor_id+10,args.start,args.end]),
            ("component_counts", "SELECT component,COUNT(*) AS n FROM bgl_logs WHERE event_time >= %s::TIMESTAMP AND event_time < %s::TIMESTAMP GROUP BY component ORDER BY n DESC", [args.start,args.end]),
        ]
        runs = {name: {"unindexed": [], "indexed": []} for name,_,_ in definitions}
        create_ms = cleanup_ms = 0.0
        status, errors = "success", []
        try:
            with EvidenceBuilder("benchmark_indexing") as eb:
                for name, sql, params in definitions:
                    for _ in range(10):
                        eb.query(name+" unindexed", sql, params)
                        runs[name]["unindexed"].append(eb.queries[-1].execution_time_ms)
            env = create_time_range_index("BGL", args.start, args.end, table_name)
            create_ms = float(env.get("rows", [{}])[0].get("creation_time_ms") or 0)
            with EvidenceBuilder("benchmark_indexing") as eb:
                for name, sql, params in definitions:
                    for _ in range(10):
                        if name == "full_text_machine_check":
                            index_sql = f"SELECT COUNT(*) AS n FROM {table_name} i JOIN bgl_logs l ON l.line_id=i.line_id WHERE i.message @@ ts_phrase(%s) AND l.event_time >= %s::TIMESTAMP AND l.event_time < %s::TIMESTAMP"
                            index_params = [params[0],params[1],params[2]]
                        else:
                            index_sql, index_params = sql, params
                        eb.query(name+" indexed", index_sql, index_params)
                        runs[name]["indexed"].append(eb.queries[-1].execution_time_ms)
        except Exception as exc:
            status, errors = "failed", [str(exc)]
        finally:
            try:
                before = datetime.now(timezone.utc)
                if table_name in _registry:
                    drop_time_range_index(table_name)
                    cleanup_ms = (datetime.now(timezone.utc)-before).total_seconds()*1000
            except Exception as exc:
                status = "partial"
                errors.append(f"cleanup: {exc}")
        summary = {}
        for name, sides in runs.items():
            u = statistics.mean(sides["unindexed"]) if sides["unindexed"] else None
            ix = statistics.mean(sides["indexed"]) if sides["indexed"] else None
            saving = (u-ix) if u is not None and ix is not None else None
            applies_to_indexed_query = name == "full_text_machine_check"
            summary[name] = {"avg_unindexed_query_ms": u, "median_unindexed_query_ms": statistics.median(sides["unindexed"]) if sides["unindexed"] else None,
                             "avg_indexed_query_ms": ix, "median_indexed_query_ms": statistics.median(sides["indexed"]) if sides["indexed"] else None,
                             "time_saved_per_query_ms": saving,
                             "speedup_factor": (u/ix if ix else None) if u is not None else None,
                             "setup_only_breakeven_query_count": math.ceil(create_ms/saving) if applies_to_indexed_query and saving and saving > 0 else None,
                             "lifecycle_breakeven_query_count": math.ceil((create_ms+cleanup_ms)/saving) if applies_to_indexed_query and saving and saving > 0 else None,
                             "break_even_unavailable_reason": None if applies_to_indexed_query else "The partial full-text index does not target this query; timing differences are not treated as index savings.",
                             "raw_ms": sides}
        db_status = check_connection()
        from src.queries import get_corpus_scope
        corpus_scope = get_corpus_scope()
        with EvidenceBuilder("context compression benchmark") as eb:
            context_rows = eb.query("bounded message sample for compression metrics",
                "SELECT line_id,event_time,message FROM bgl_logs WHERE event_time >= %s::TIMESTAMP AND event_time < %s::TIMESTAMP ORDER BY line_id LIMIT 50",
                [args.start,args.end])
        compact = to_llm_compact_envelope(context_rows, deduplicate_consecutive=True)
        role_dsns = {"engineer": bool(__import__("os").getenv("SERENEDB_ENGINEER_DSN")),
                     "team_lead": bool(__import__("os").getenv("SERENEDB_TEAM_LEAD_DSN")),
                     "manager": bool(__import__("os").getenv("SERENEDB_MANAGER_DSN"))}
        result = {"status": status, "timestamp": timestamp, "live_database": True,
                  "serenedb_version": db_status.get("version"), "dataset": "BGL",
                  "corpus_scope": corpus_scope.get("rows", []),
                  "anchor_line_id_for_nearby_context": anchor_id,
                  "window": [args.start,args.end], "query_runs_per_arm": 10,
                  "context_compression": {"status": "measured", "rows_returned": len(context_rows),
                      "rows_scanned": None, "rows_scanned_reason": "Engine scan counters are not exposed.",
                      "message_chars_truncated": compact["message_chars_truncated"],
                      "deduplication": compact["deduplication"], "compression_ratio": None,
                      "compression_ratio_unavailable_reason": "Reliable rows_scanned is unavailable."},
                  "rls_verification": {"status": "unavailable" if not all(role_dsns.values()) else "not_run",
                      "role_dsns_configured": role_dsns,
                      "reason": "Actual Manager raw-data denial requires pytest -q -m serenedb with SERENEDB_MANAGER_DSN."},
                  "per_role_model_usage": {"status": "unavailable", "reason": "Per-role model calls were not included in this database benchmark."},
                  "cache_conditions": "Sequential execution; no cache flush was requested. First samples may be cold; later samples are warm.",
                  "raw_sample_count_per_query_arm": {name:{side:len(vals) for side,vals in sides.items()} for name,sides in runs.items()},
                  "query_definitions": [{"name": n,"sql": q,"parameters": p} for n,q,p in definitions],
                  "index_name": table_name, "index_creation_time_ms": create_ms,"cleanup_time_ms": cleanup_ms,
                  "setup_only_formula": "ceil(creation_ms / max(avg_unindexed_ms - avg_indexed_ms, 0))",
                  "lifecycle_formula": "ceil((creation_ms + cleanup_ms) / saving_ms)", "results": summary,"errors": errors}
    out = Path(args.output)
    section = [f"\n## Benchmark run {timestamp}", "", f"Status: **{result['status']}**", "", "```json", json.dumps(result, indent=2, default=str), "```", ""]
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("a", encoding="utf-8") as f: f.write("\n".join(section))
    print(json.dumps(result, indent=2, default=str))
    return 0 if result["status"] in {"success", "skipped"} else 1

if __name__ == "__main__": raise SystemExit(main())
