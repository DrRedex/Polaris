"""Evidence-aware saturation and simple linear trend audit."""
from __future__ import annotations

from typing import Any
import numpy as np

BOUNDARIES = {
    "connection_pool": (100.0, "connections"), "cpu_utilization": (100.0, "%"),
    "disk_io_queue": (20.0, "operations"), "midplane_faulty_nodes": (32.0, "nodes"),
    "nova_api_p95_latency_ms": (2000.0, "ms"), "hourly_error_burst_rate": (50.0, "bursts/hour"),
}


class PredictiveBottleneckEngine:
    def analyze_saturation_bottlenecks(self, current_metrics: dict[str, Any]) -> list[dict[str, Any]]:
        risks = []
        for name, raw in current_metrics.items():
            if name not in BOUNDARIES or raw is None:
                continue
            value = raw.get("value") if isinstance(raw, dict) else raw
            evidence_id = raw.get("evidence_id") if isinstance(raw, dict) else None
            try:
                value = float(value)
            except (TypeError, ValueError):
                continue
            if not np.isfinite(value):
                continue
            boundary, unit = BOUNDARIES[name]
            utilization = value if name == "cpu_utilization" else 100.0 * value / boundary
            level = "CRITICAL" if utilization >= 90 else "WARNING" if utilization >= 80 else "NORMAL"
            risks.append({"metric": name, "value": value, "unit": unit, "boundary": boundary,
                          "utilization_pct": utilization, "status": level, "evidence_id": evidence_id,
                          "kind": "observed_saturation"})
        return risks

    def analyze_degradation_trends(self, historical_timeseries: dict[str, Any],
                                   slope_threshold: float = 0.05) -> list[dict[str, Any]]:
        output = []
        for name, raw in historical_timeseries.items():
            values = raw.get("values", []) if isinstance(raw, dict) else raw
            evidence_id = raw.get("evidence_id") if isinstance(raw, dict) else None
            try:
                y = np.asarray(values, dtype=float)
            except (TypeError, ValueError):
                continue
            if len(y) < 2 or not np.all(np.isfinite(y)):
                continue
            slope = float(np.polyfit(np.arange(len(y)), y, 1)[0])
            projection = float(y[-1] + slope * 3)
            output.append({"metric": name, "slope_per_step": slope, "last_value": float(y[-1]),
                           "projected_three_steps": projection,
                           "method": "linear_polyfit_gradient_y_mx_c",
                           "status": "WARNING" if slope >= slope_threshold else "NORMAL",
                           "is_forecast": True, "evidence_id": evidence_id,
                           "note": "Simple linear projection, not a guarantee."})
        return output

    def run_proactive_audit(self, current_snapshot: dict[str, Any], historical_logs: dict[str, Any],
                            source_evidence_id: str | None = None) -> dict[str, Any]:
        saturation = self.analyze_saturation_bottlenecks(current_snapshot)
        trends = self.analyze_degradation_trends(historical_logs)
        for row in saturation + trends:
            row["evidence_id"] = row.get("evidence_id") or source_evidence_id
        states = [r["status"] for r in saturation + trends]
        health = ("CRITICAL_ALERT" if "CRITICAL" in states else "DEGRADED_WARNING" if "WARNING" in states
                  else "OPTIMAL" if states else None)
        return {"system_health_status": health, "saturation_risks": saturation,
                "degradation_forecasts": trends,
                "priority_rule": "critical saturation, then any warning; optimal only when supported metrics exist and none trigger",
                "source_evidence_id": source_evidence_id,
                "availability": "measured" if states else "unavailable",
                "unavailable_reason": None if states else "No supported current or historical metric values were supplied."}

    def audit_from_serenedb_trends(self, trend_rows: list[dict[str, Any]],
                                   latency_rows: list[dict[str, Any]]) -> dict[str, Any]:
        history: dict[str, Any] = {}
        current: dict[str, Any] = {}
        for row in trend_rows:
            metric, value = row.get("metric"), row.get("value")
            if metric and value is not None:
                history.setdefault(metric, {"values": [], "evidence_id": row.get("evidence_id")})["values"].append(value)
                current[metric] = {"value": value, "evidence_id": row.get("evidence_id")}
        latency = [r.get("p95_ms") for r in latency_rows if r.get("p95_ms") is not None]
        if latency:
            history["nova_api_p95_latency_ms"] = {"values": latency, "evidence_id": latency_rows[0].get("evidence_id")}
            current["nova_api_p95_latency_ms"] = {"value": latency[-1], "evidence_id": latency_rows[-1].get("evidence_id")}
        return self.run_proactive_audit(current, history)
