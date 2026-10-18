"""Opt-in, bounded remote OpenTelemetry benchmark; never downloads files locally."""
from __future__ import annotations
import argparse, json, sys
from datetime import datetime, timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))

def main():
    p=argparse.ArgumentParser(); p.add_argument("--live",action="store_true"); p.add_argument("--partitions",type=int,default=1)
    p.add_argument("--confirm-multpartition",action="store_true"); p.add_argument("--output",default=str(ROOT/"docs"/"PERFORMANCE_AND_AWARDS.md")); a=p.parse_args()
    stamp=datetime.now(timezone.utc).isoformat()
    if not 1<=a.partitions<=50: p.error("--partitions must be 1-50")
    if a.partitions>1 and not a.confirm_multpartition: p.error("multi-partition scans require --confirm-multpartition")
    if not a.live: result={"status":"skipped","reason":"Remote scan requires --live.","partitions_requested":a.partitions,"timestamp":stamp}
    else:
        from src.extensions import query_otel_scale_summary, query_remote_otel_parquet, correlate_otel_trace
        from src.db import check_connection
        start=__import__('time').perf_counter()
        try:
            result=query_otel_scale_summary(a.partitions, confirm_multpartition=a.confirm_multpartition)
            result.update({"status":"success","completed":True,"scale_summary_elapsed_ms":round((__import__('time').perf_counter()-start)*1000,1),
                           "timestamp":stamp,"serenedb_version":check_connection().get("version"),
                           "rows_scanned":None,"rows_scanned_reason":"Engine scan counters unavailable."})
            errors=[r for r in result.get("distribution",[]) if str(r.get("severity","")).lower() in {"error","fatal"}]
            lead_rows=sorted(errors,key=lambda r:int(r.get("log_lines",0)),reverse=True)[:5]
            result["role_actionability_smoke"]={
                "engineer":{"status":"pending_trace_lookup"},
                "team_lead":{"status":"measured_summary","error_groups":lead_rows},
                "manager":{"status":"measured_aggregate","error_log_lines":sum(int(r.get("log_lines",0)) for r in errors),
                            "affected_services":len({r.get("service_name") for r in errors})}}
            # Find one real trace id from a bounded five-second slice; no trace is invented.
            try:
                body_env=query_remote_otel_parquet("search_body",part=0,window_seconds=5,search_text="error",limit=1)
                trace_id=next((r.get("trace_id") for r in body_env.get("rows",[]) if r.get("trace_id")),None)
                if trace_id:
                    timeline=correlate_otel_trace(trace_id,1)
                    result["role_actionability_smoke"]["engineer"]={"status":"measured_timeline" if timeline.get("rows") else "no_match",
                        "trace_id":trace_id,"spans_returned":len(timeline.get("rows",[])),"evidence_id":timeline.get("evidence_id")}
                else:
                    result["role_actionability_smoke"]["engineer"]={"status":"unavailable","reason":"No trace ID found in the bounded Body search."}
            except Exception as exc:
                result["role_actionability_smoke"]["engineer"]={"status":"failed","reason":str(exc)}
        except Exception as exc: result={"status":"failed","completed":False,"error":str(exc),"timestamp":stamp,"partitions_requested":a.partitions}
        if result.get("completed"):
            result["benchmark_wall_time_ms"] = round((__import__('time').perf_counter()-start)*1000,1)
    out=Path(a.output); out.parent.mkdir(parents=True,exist_ok=True)
    with out.open("a",encoding="utf-8") as f:f.write(f"\n## OTel benchmark run {stamp}\n\n```json\n{json.dumps(result,indent=2,default=str)}\n```\n")
    print(json.dumps(result,indent=2,default=str)); return 0 if result["status"] in {"success","skipped"} else 1
if __name__=="__main__":raise SystemExit(main())
