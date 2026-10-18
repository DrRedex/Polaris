"""
extensions.py

Optional tools (Phase 5):

manage_workspace_index
    The agent builds a partial inverted index over the time window it is investigating,
    benchmarks it against the full-corpus index, and drops it when done. Index names are
    derived from validated arguments; the LLM never supplies identifiers or SQL.

query_remote_otel_parquet
    Zero-download queries over the public 50-billion-row OpenTelemetry Parquet benchmark
    on S3 (50 files x ~1B rows). The files are sorted by Timestamp, so a time-window filter
    lets SereneDB fetch only the row groups whose min/max statistics overlap the window
    via HTTP range reads. The tool reports how many row groups and bytes that pruning skipped.
"""

import math
import os
import re
from datetime import datetime, timedelta
from typing import Any, Literal, Optional

import psycopg

from src.db import DATASET_TABLES, EvidenceBuilder
from src.queries import _dataset, _parse_time, _short_text, _ts

# ----------------------------------------------------------------------------
# Workspace indexes
# ----------------------------------------------------------------------------

MAX_WORKSPACE_INDEXES = 3


def _ws_name(table: str, start: datetime, end: datetime) -> str:
    # Second precision: search_logs reads the window back from the name (WORKSPACE_NAME_RE)
    # to decide whether this index covers a query, so it must match the index predicate exactly.
    if start.microsecond or end.microsecond:
        raise ValueError("workspace windows are limited to whole seconds")
    return f"ws_{table}_{start:%Y%m%d%H%M%S}_{end:%Y%m%d%H%M%S}"


def _existing_workspace_indexes(eb: EvidenceBuilder) -> list[dict[str, Any]]:
    return eb.query("list workspace indexes", """
        SELECT indexname AS index_name, tablename AS table_name, indexdef AS definition
        FROM pg_indexes WHERE left(indexname, 3) = 'ws_' ORDER BY indexname
    """)


def _create_ws(eb: EvidenceBuilder, name: str, table: str, start: datetime, end: datetime) -> float:
    # DDL cannot take bound parameters. Every interpolated value is a validated datetime
    # rendered by isoformat, or a name derived from one, so no caller text reaches the SQL.
    eb.query("create workspace index", f"""
        CREATE INDEX {name} ON {table} USING inverted (line_id, message log_text)
        WHERE event_time >= TIMESTAMP '{_ts(start)}' AND event_time <= TIMESTAMP '{_ts(end)}'
    """)
    eb.query("publish index", f"VACUUM (REFRESH_TABLE) {table}")
    return eb.queries[-2].execution_time_ms + eb.queries[-1].execution_time_ms


def manage_workspace_index(action: Literal["create", "benchmark", "drop", "list"],
                           dataset: str = "BGL", start_time: Optional[str] = None, end_time: Optional[str] = None,
                           query_text: str = "error", repetitions: int = 5) -> dict[str, Any]:
    with EvidenceBuilder("manage_workspace_index") as eb:
        if action == "list":
            rows = _existing_workspace_indexes(eb)
            return eb.envelope(data_scope={"action": "list"}, rows=rows)

        dataset = _dataset(dataset)
        table = DATASET_TABLES[dataset]
        start, end = _parse_time(start_time, "start_time"), _parse_time(end_time, "end_time")
        if not start or not end or end <= start:
            raise ValueError("start_time and end_time are required, with end_time after start_time")
        name = _ws_name(table, start, end)
        existing = {r["index_name"] for r in _existing_workspace_indexes(eb)}
        scope = {"dataset": dataset, "table": table, "index_name": name,
                 "time_filter_start": start.isoformat(), "time_filter_end": end.isoformat()}

        if action == "drop":
            eb.query("drop workspace index", f"DROP INDEX IF EXISTS {name}")
            return eb.envelope(data_scope=scope, rows=[{"index_name": name, "dropped": name in existing}])

        created_ms = None
        if name not in existing:
            if len(existing) >= MAX_WORKSPACE_INDEXES:
                raise ValueError(f"at most {MAX_WORKSPACE_INDEXES} workspace indexes; drop one of {sorted(existing)}")
            created_ms = _create_ws(eb, name, table, start, end)
        if action == "create":
            n = eb.query("rows covered", f"SELECT COUNT(*) AS n FROM {table} WHERE event_time >= %s::TIMESTAMP "
                                         "AND event_time <= %s::TIMESTAMP", [_ts(start), _ts(end)])[0]["n"]
            return eb.envelope(data_scope=scope, rows=[{
                "index_name": name, "already_existed": created_ms is None,
                "creation_time_ms": created_ms, "rows_in_window": n}],
                notes=["search_logs now uses this index automatically for full-text searches whose time window "
                       "lies inside this one. Drop it when the investigation ends."])

        # benchmark: same full-text count through the full-corpus index (with a time
        # filter) and through the workspace index.
        query_text = (query_text or "").strip()
        if not query_text or len(query_text) > 200:
            raise ValueError("query_text must be 1-200 characters")
        repetitions = max(1, min(int(repetitions), 20))
        base_sql = (f"SELECT COUNT(*) AS n FROM {table}_idx i JOIN {table} l ON l.line_id = i.line_id "
                    "WHERE i.message @@ ts_phrase(%s) AND l.event_time >= %s::TIMESTAMP AND l.event_time <= %s::TIMESTAMP")
        ws_sql = f"SELECT COUNT(*) AS n FROM {name} i WHERE i.message @@ ts_phrase(%s)"
        timings: dict[str, list[float]] = {"full_corpus_index": [], "workspace_index": []}
        counts = {}
        for _ in range(repetitions):
            counts["full_corpus_index"] = eb.query("full-corpus index", base_sql, [query_text, _ts(start), _ts(end)])[0]["n"]
            timings["full_corpus_index"].append(eb.queries[-1].execution_time_ms)
            counts["workspace_index"] = eb.query("workspace index", ws_sql, [query_text])[0]["n"]
            timings["workspace_index"].append(eb.queries[-1].execution_time_ms)

        avg = {k: round(sum(v) / len(v), 1) for k, v in timings.items()}
        saving = avg["full_corpus_index"] - avg["workspace_index"]
        break_even = math.ceil(created_ms / saving) if created_ms and saving > 0 else None
        # Keep the evidence compact: one recorded execution per benchmarked query.
        seen: set[str] = set()
        kept = []
        for q in eb.queries:
            if q.label in timings or q.label in ("full-corpus index", "workspace index"):
                if q.label in seen:
                    continue
                seen.add(q.label)
            kept.append(q)
        eb.queries = kept
        return eb.envelope(
            data_scope={**scope, "query_text": query_text, "repetitions": repetitions},
            rows=[{"strategy": k, "avg_query_ms": avg[k], "all_runs_ms": [round(x, 1) for x in timings[k]],
                   "matching_rows": counts[k]} for k in timings],
            summary={
                "creation_time_ms": created_ms,
                "created_in_this_call": created_ms is not None,
                "avg_saving_per_query_ms": round(saving, 1),
                "speedup": round(avg["full_corpus_index"] / avg["workspace_index"], 2) if avg["workspace_index"] else None,
                "break_even_queries": break_even,
                "results_identical": counts["full_corpus_index"] == counts["workspace_index"],
            },
            notes=["break_even_queries = ceil(creation_time_ms / avg_saving_per_query_ms): after that many searches "
                   "in this window the index has paid for itself. Drop it when the investigation ends.",
                   "While it exists, search_logs uses it automatically for full-text searches whose time window "
                   "lies inside this one."],
        )


# ----------------------------------------------------------------------------
# Remote OpenTelemetry Parquet (50B rows on S3)
# ----------------------------------------------------------------------------

OTEL_URL = "https://public-pme.s3.eu-west-3.amazonaws.com/text_bench/part_{:03d}.parquet"
OTEL_PARTS = 50
MAX_WINDOW_SECONDS = 30
REMOTE_TIMEOUT_S = 150
OVERVIEW_BATCH = 10


def _url(part: int) -> str:
    if not 0 <= int(part) < OTEL_PARTS:
        raise ValueError(f"part must be 0-{OTEL_PARTS - 1}")
    return OTEL_URL.format(int(part))


def _pruning(eb: EvidenceBuilder, url: str, start: str, end: str, columns: list[str]) -> dict[str, Any]:
    """Row groups / bytes the window touches versus the whole file, from Parquet footer statistics."""
    cols = ", ".join(f"'{c}'" for c in columns)  # fixed column names from this module
    row = eb.query("row-group pruning estimate (footer statistics only)", f"""
        WITH ts AS (
            SELECT row_group_id, row_group_num_rows,
                   (stats_max::TIMESTAMPTZ >= %s::TIMESTAMPTZ AND stats_min::TIMESTAMPTZ < %s::TIMESTAMPTZ) AS hit
            FROM parquet_metadata('{url}') WHERE path_in_schema = 'Timestamp'
        ), sizes AS (
            SELECT row_group_id, SUM(total_compressed_size) AS bytes
            FROM parquet_metadata('{url}') WHERE path_in_schema IN ({cols}) GROUP BY row_group_id
        )
        SELECT COUNT(*) AS row_groups_in_file,
               COUNT(*) FILTER (WHERE ts.hit) AS row_groups_read,
               SUM(ts.row_group_num_rows) AS rows_in_file,
               SUM(ts.row_group_num_rows) FILTER (WHERE ts.hit) AS rows_in_read_row_groups,
               SUM(sizes.bytes) AS column_bytes_in_file,
               SUM(sizes.bytes) FILTER (WHERE ts.hit) AS column_bytes_read
        FROM ts JOIN sizes USING (row_group_id)
    """, [start, end], timeout_s=REMOTE_TIMEOUT_S)[0]
    return row


def query_remote_otel_parquet(action: Literal["overview", "service_summary", "search_body"] = "overview",
                              part: int = 0, start_time: Optional[str] = None, window_seconds: int = 5,
                              parts_for_overview: int = 1, service_name: Optional[str] = None,
                              severity: Optional[str] = None, search_text: Optional[str] = None,
                              limit: int = 10) -> dict[str, Any]:
    with EvidenceBuilder("query_remote_otel_parquet") as eb:
        if action == "overview":
            n = max(1, min(int(parts_for_overview), OTEL_PARTS))
            rows = []
            # Batches of 10 files: opening all 50 at once floods the container's DNS resolver.
            for first in range(0, n, OVERVIEW_BATCH):
                urls = ", ".join(f"'{_url(i)}'" for i in range(first, min(first + OVERVIEW_BATCH, n)))
                sql = f"""
                    SELECT regexp_extract(file_name, 'part_[0-9]+') AS part,
                           CAST(SUM(row_group_num_rows) AS BIGINT) AS rows,
                           COUNT(*) AS row_groups,
                           MIN(stats_min) AS first_timestamp,
                           MAX(stats_max) AS last_timestamp
                    FROM parquet_metadata([{urls}])
                    WHERE path_in_schema = 'Timestamp'
                    GROUP BY 1 ORDER BY 1
                """
                label = f"footer statistics, files {first}-{min(first + OVERVIEW_BATCH, n) - 1}"
                try:
                    rows += eb.query(label, sql, timeout_s=REMOTE_TIMEOUT_S)
                except psycopg.Error:
                    rows += eb.query(label + " (retry)", sql, timeout_s=REMOTE_TIMEOUT_S)
            total = sum(int(r["rows"]) for r in rows)
            return eb.envelope(
                data_scope={"source": "s3://public-pme/text_bench", "files": n,
                            "url_pattern": OTEL_URL.replace("{:03d}", "NNN")},
                rows=rows[:limit * 5],
                total_matching_count=total,
                summary={"files": len(rows), "total_rows": total,
                         "row_groups": sum(int(r["row_groups"]) for r in rows),
                         "elapsed_ms": round(sum(q.execution_time_ms for q in eb.queries), 1)},
                notes=["Read from Parquet footers via HTTP range requests; no row data was downloaded."],
            )

        url = _url(part)
        window_seconds = max(1, min(int(window_seconds), MAX_WINDOW_SECONDS))
        limit = max(1, min(int(limit), 25))
        service_name = _short_text(service_name, "service_name")
        severity = _short_text(severity, "severity")
        start = _parse_time(start_time, "start_time")
        if start is None:
            first = eb.query("file start time", f"""
                SELECT MIN(stats_min) AS t FROM parquet_metadata('{url}') WHERE path_in_schema = 'Timestamp'
            """, timeout_s=REMOTE_TIMEOUT_S)[0]["t"]
            start = datetime.fromisoformat(str(first)).replace(tzinfo=None)
        end = start + timedelta(seconds=window_seconds)
        s, e = _ts(start) + "+00", _ts(end) + "+00"

        where = ['"Timestamp" >= %s::TIMESTAMPTZ', '"Timestamp" < %s::TIMESTAMPTZ']
        params: list[Any] = [s, e]
        if service_name:
            where.append('"ServiceName" = %s')
            params.append(service_name)
        if severity:
            where.append('lower("SeverityText") = lower(%s)')
            params.append(severity)
        source = f"read_parquet('{url}')"  # URL is built from the validated part number

        if action == "service_summary":
            columns = ["Timestamp", "ServiceName", "SeverityText"]
            rows = eb.query("service x severity counts in window", f"""
                SELECT "ServiceName" AS service_name, "SeverityText" AS severity, COUNT(*) AS log_lines
                FROM {source} WHERE {' AND '.join(where)}
                GROUP BY 1, 2 ORDER BY log_lines DESC LIMIT 100
            """, params, timeout_s=REMOTE_TIMEOUT_S)
            total = sum(int(r["log_lines"]) for r in rows)
        elif action == "search_body":
            text = (search_text or "").strip()
            if not text or len(text) > 100:
                raise ValueError("search_text must be 1-100 characters")
            escaped = re.sub(r"([\\%_])", r"\\\1", text)
            columns = ["Timestamp", "ServiceName", "SeverityText", "TraceId", "SpanId", "Body"]
            rows = eb.query("case-insensitive Body search in window", f"""
                SELECT "Timestamp" AS ts, "ServiceName" AS service_name, "SeverityText" AS severity,
                       "TraceId" AS trace_id, "SpanId" AS span_id, "Body" AS body,
                       COUNT(*) OVER () AS total_matching_count
                FROM {source} WHERE {' AND '.join(where)} AND "Body" ILIKE %s ESCAPE '\\'
                ORDER BY "Timestamp" LIMIT %s
            """, params + [f"%{escaped}%", limit], timeout_s=REMOTE_TIMEOUT_S)
            total = int(rows[0]["total_matching_count"]) if rows else 0
            message_chars_truncated = 0
            for r in rows:
                r.pop("total_matching_count")
                raw_body = r["body"] or ""
                message_chars_truncated += max(0, len(raw_body) - 300)
                r["body"] = raw_body[:300]
                if len(raw_body) > 300:
                    r["message_truncated"] = True
        else:
            raise ValueError(f"unknown action {action!r}")

        scan_ms = eb.queries[-1].execution_time_ms
        pruning = _pruning(eb, url, s, e, columns)
        result = eb.envelope(
            data_scope={"source": url, "part": part, "window_start": s, "window_end": e,
                        "additional_filters": {k: v for k, v in
                                               {"service_name": service_name, "severity": severity,
                                                "search_text": search_text}.items() if v}},
            rows=rows,
            total_matching_count=total,
            is_truncated=(total > len(rows)) if action == "search_body" else len(rows) >= 100,
            summary={
                **{k: int(v) if v is not None else None for k, v in pruning.items()},
                "row_groups_skipped_pct": round(100 * (1 - pruning["row_groups_read"] / pruning["row_groups_in_file"]), 2)
                if pruning["row_groups_in_file"] else None,
                "scan_wall_time_ms": scan_ms,
                "message_chars_truncated": message_chars_truncated if action == "search_body" else 0,
                "rows_per_second": round(int(pruning["rows_in_read_row_groups"] or 0) / (scan_ms / 1000))
                if scan_ms else None,
            },
            notes=["The file is sorted by Timestamp; only row groups overlapping the window are fetched with HTTP "
                   "range requests. column_bytes_read is the compressed size of the needed columns in those row "
                   "groups (an estimate of network transfer).",
                   f"Windows are capped at {MAX_WINDOW_SECONDS} s (~600k rows per second of log time)."],
        )
        result["efficiency_telemetry"]["message_chars_truncated"] = message_chars_truncated if action == "search_body" else 0
        return result


def correlate_otel_trace(trace_id: str, num_partitions: int = 1,
                         confirm_multpartition: bool = False) -> dict[str, Any]:
    """Find a distributed trace in an explicit prefix of public partitions (Engineer only)."""
    trace_id = (trace_id or "").strip()
    if not trace_id or len(trace_id) > 128 or not re.fullmatch(r"[A-Fa-f0-9-]+", trace_id):
        raise ValueError("trace_id must be 1-128 hexadecimal/dash characters")
    if not 1 <= int(num_partitions) <= OTEL_PARTS:
        raise ValueError("num_partitions must be 1-50")
    if int(num_partitions) > 1 and not confirm_multpartition:
        raise ValueError("multi-partition trace searches require confirm_multpartition=true")
    with EvidenceBuilder("correlate_otel_trace") as eb:
        rows = []
        message_chars_truncated = 0
        partitions = [_url(i) for i in range(int(num_partitions))]
        for part, url in enumerate(partitions):
            rows.extend(eb.query(f"trace lookup partition {part}", f"""
                SELECT "Timestamp" AS timestamp, "ServiceName" AS service_name,
                       "SeverityText" AS severity, "TraceId" AS trace_id,
                       "SpanId" AS span_id, "Body" AS body
                FROM read_parquet('{url}') WHERE "TraceId" = %s
                ORDER BY "Timestamp" LIMIT 50
            """, [trace_id], timeout_s=REMOTE_TIMEOUT_S))
        rows.sort(key=lambda row: str(row.get("timestamp", "")))
        for row in rows[:50]:
            body = str(row.get("body") or "")
            message_chars_truncated += max(0, len(body) - 300)
            row["body"] = body[:300]
            if len(body) > 300:
                row["message_truncated"] = True
        result = eb.envelope(data_scope={"partitions": partitions, "trace_id": trace_id,
                                       "source": "public OTel Parquet", "query_role": "Engineer"},
                           rows=rows[:50], is_truncated=len(rows) > 50,
                           summary={"spans_returned": min(len(rows), 50), "matched": bool(rows),
                                    "message_chars_truncated": message_chars_truncated},
                           notes=["TraceId/SpanId fields are queried directly from the selected remote Parquet files.",
                                  "No match is distinct from inaccessible objects or query errors."])
        result["efficiency_telemetry"]["message_chars_truncated"] = message_chars_truncated
        return result


def query_otel_scale_summary(num_partitions: int = 1, service_filter: Optional[str] = None,
                             severity_filter: Optional[str] = None,
                             confirm_multpartition: bool = False) -> dict[str, Any]:
    if not 1 <= int(num_partitions) <= OTEL_PARTS:
        raise ValueError("num_partitions must be 1-50")
    if int(num_partitions) > 1 and not confirm_multpartition:
        raise ValueError("multi-partition scans require confirm_multpartition=true")
    started = __import__("time").perf_counter()
    partitions = []
    summaries = []
    pruning = []
    matching_rows = 0
    source_rows = 0
    windows = []
    evidence_ids = []
    price_per_gb = os.getenv("OTEL_SCAN_PRICE_USD_PER_GB")
    for part in range(int(num_partitions)):
        env = query_remote_otel_parquet("service_summary", part=part, window_seconds=30,
                                        service_name=service_filter, severity=severity_filter)
        partitions.append(_url(part))
        windows.append(env.get("data_scope", {}))
        if env.get("evidence_id"):
            evidence_ids.append(env["evidence_id"])
        summaries.extend(env.get("rows", []))
        summary = env.get("summary") or {}
        pruning.append({"partition": part, "rows_in_file_from_footer": summary.get("rows_in_file"),
                        "rows_in_read_row_groups_from_footer": summary.get("rows_in_read_row_groups"),
                        "row_groups_in_file": summary.get("row_groups_in_file"),
                        "row_groups_read": summary.get("row_groups_read"),
                        "column_bytes_read_estimate": summary.get("column_bytes_read")})
        source_rows += int(summary.get("rows_in_file") or 0)
        matching_rows += int(env.get("total_matching_count") or 0)
    byte_estimate = sum(int(item.get("column_bytes_read_estimate") or 0) for item in pruning)
    cost = None
    cost_reason = "No explicit OTEL_SCAN_PRICE_USD_PER_GB configured." if not price_per_gb else None
    if price_per_gb:
        try:
            price_value = float(price_per_gb)
            if not math.isfinite(price_value) or price_value < 0:
                raise ValueError
            cost = byte_estimate / 1_000_000_000 * price_value
        except ValueError:
            cost_reason = "OTEL_SCAN_PRICE_USD_PER_GB must be a finite nonnegative USD/GB value."
    return {"status": "success", "partitions": partitions,
            "evidence_ids": evidence_ids,
            "filters": {"service": service_filter, "severity": severity_filter},
            "query_windows": windows,
            "distribution": summaries, "total_rows_queried": matching_rows,
            "matching_rows_in_selected_window": matching_rows,
            "total_source_rows_from_footer": source_rows or None,
            "partition_pruning_metadata": pruning,
            "estimated_scanned_column_bytes_from_footer": byte_estimate or None,
            "estimated_cloud_scan_cost_formula": "estimated_column_bytes / 1,000,000,000 * OTEL_SCAN_PRICE_USD_PER_GB",
            "estimated_cloud_scan_cost_unit_price_usd_per_gb": price_per_gb,
            "elapsed_ms": round((__import__("time").perf_counter()-started)*1000, 1),
            "rows_scanned": None, "rows_scanned_reason": "SereneDB scan counters are not exposed by this client path.",
            "estimated_cloud_scan_cost_usd": cost,
            "estimated_cloud_scan_cost_reason": cost_reason or "Estimate uses compressed column bytes from Parquet footer row-group metadata; not a provider invoice."}
