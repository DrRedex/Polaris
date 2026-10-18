"""
queries.py

Parameterized SereneDB query templates behind every agent tool. The LLM never writes
SQL: it picks a tool and supplies typed, validated arguments. Values are always bound
as parameters; only fixed fragments from this file are ever concatenated into SQL.

Each function returns an EvidenceEnvelope dict (see src/db.py).
"""

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Optional

from src.db import DATASET_TABLES, EvidenceBuilder

Dataset = Literal["BGL", "OpenStack"]
SearchMode = Literal["all_terms", "any_terms", "phrase", "substring"]
BucketSize = Literal["hour", "day", "week"]

MAX_SEARCH_LIMIT = 100
MAX_SEARCH_TERMS = 8
WORKSPACE_NAME_RE = re.compile(r"^ws_(?P<table>[a-z_]+)_(?P<start>\d{14})_(?P<end>\d{14})$")
MAX_WINDOW_LINES = 50
MAX_AGG_ROWS = 100
MAX_TREND_ROWS = 150
MAX_HOURLY_SPAN = timedelta(days=31)

# What "errors_only" means per dataset; echoed into every envelope that applies it.
ERROR_FILTER = {
    "BGL": ("(l.severity IN ('ERROR', 'SEVERE', 'FATAL', 'FAILURE') OR l.is_anomaly)",
            "severity in (ERROR, SEVERE, FATAL, FAILURE) or a labelled alert (label != '-')"),
    "OpenStack": ("(l.severity IN ('ERROR', 'CRITICAL') OR l.http_status >= 500 OR l.is_anomaly)",
                  "severity in (ERROR, CRITICAL), HTTP status >= 500, or a line on an instance listed in anomaly_labels.txt"),
}

SAMPLE_COLUMNS = {
    "BGL": "l.line_id, l.event_time, l.node_id, l.component, l.severity, l.label, l.message",
    "OpenStack": ("l.line_id, l.event_time, l.component, l.severity, l.request_id, l.instance_id, "
                  "l.http_status, l.latency_sec, l.anomaly_label, l.message"),
}


# ----------------------------------------------------------------------------
# Validation helpers
# ----------------------------------------------------------------------------

def _dataset(value: str) -> str:
    if value not in DATASET_TABLES:
        raise ValueError(f"dataset must be one of {list(DATASET_TABLES)}, got {value!r}")
    return value


def _parse_time(value: Optional[str], name: str) -> Optional[datetime]:
    if value is None or value == "":
        return None
    try:
        ts = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(f"{name} must be ISO-8601 (e.g. 2005-06-03T00:00:00), got {value!r}") from None
    # Stored timestamps are naive. An explicit offset is converted to UTC rather than dropped.
    if ts.tzinfo is not None:
        ts = ts.astimezone(timezone.utc).replace(tzinfo=None)
    return ts


def _ts(value: datetime) -> str:
    # Timestamps are bound as ISO text and cast in SQL: SereneDB rejects binary-encoded
    # timestamp parameters in queries that join an inverted index relation.
    return value.isoformat(sep=" ")


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(int(value), high))


def _short_text(value: Optional[str], name: str, max_len: int = 64) -> Optional[str]:
    if value is None or value == "":
        return None
    if len(value) > max_len:
        raise ValueError(f"{name} longer than {max_len} characters")
    return value


class _Where:
    """Accumulates fixed SQL predicates and their bound values."""

    def __init__(self) -> None:
        self.parts: list[str] = []
        self.params: list[Any] = []

    def add(self, fragment: str, *params: Any) -> None:
        self.parts.append(fragment)
        self.params.extend(params)

    def sql(self) -> str:
        return ("WHERE " + "\n  AND ".join(self.parts)) if self.parts else ""


def _common_filters(dataset: str, start: Optional[datetime], end: Optional[datetime],
                    component: Optional[str] = None, severity: Optional[str] = None,
                    errors_only: bool = False) -> tuple[_Where, dict[str, Any]]:
    where = _Where()
    applied: dict[str, Any] = {}
    if start:
        where.add("l.event_time >= %s::TIMESTAMP", _ts(start))
    if end:
        where.add("l.event_time <= %s::TIMESTAMP", _ts(end))
    if component:
        where.add("lower(l.component) = lower(%s)", component)
        applied["component"] = component
    if severity:
        where.add("l.severity = %s", severity.upper())
        applied["severity"] = severity.upper()
    if errors_only:
        where.add(ERROR_FILTER[dataset][0])
        applied["errors_only"] = ERROR_FILTER[dataset][1]
    return where, applied


def _scope(eb: EvidenceBuilder, dataset: str, start: Optional[datetime], end: Optional[datetime],
           filters: dict[str, Any]) -> dict[str, Any]:
    return {
        "dataset": dataset,
        "table": DATASET_TABLES[dataset],
        "total_corpus_rows": eb.corpus_rows(dataset),
        "time_filter_start": start.isoformat() if start else None,
        "time_filter_end": end.isoformat() if end else None,
        "additional_filters": filters,
    }


# ----------------------------------------------------------------------------
# Corpus scope
# ----------------------------------------------------------------------------

def get_corpus_scope() -> dict[str, Any]:
    with EvidenceBuilder("get_corpus_scope") as eb:
        rows: list[dict[str, Any]] = []
        for dataset, table in DATASET_TABLES.items():
            overview = eb.query(f"{dataset} overview", f"""
                SELECT COUNT(*) AS total_rows,
                       MIN(event_time) AS first_event,
                       MAX(event_time) AS last_event,
                       ROUND(epoch(MAX(event_time)) / 86400.0 - epoch(MIN(event_time)) / 86400.0, 1) AS span_days,
                       COUNT(DISTINCT component) AS distinct_components,
                       COUNT(*) FILTER (WHERE is_anomaly) AS labelled_anomaly_lines
                FROM {table}
            """)[0]
            severities = eb.query(f"{dataset} severity distribution", f"""
                SELECT severity, COUNT(*) AS lines
                FROM {table}
                GROUP BY severity
                ORDER BY lines DESC
            """)
            rows.append({"dataset": dataset, "table": table, **overview,
                         "severity_distribution": {r["severity"]: r["lines"] for r in severities}})
        return eb.envelope(
            data_scope={"datasets": list(DATASET_TABLES)},
            rows=rows,
            notes=["The corpus contains log lines only. Revenue, SLA, customer impact and downtime "
                   "are not recorded and cannot be derived from it."],
        )


# ----------------------------------------------------------------------------
# Engineer: full-text search and nearby events
# ----------------------------------------------------------------------------

def search_logs(dataset: str, query_text: str, start_time: Optional[str] = None,
                end_time: Optional[str] = None, component: Optional[str] = None,
                severity: Optional[str] = None, mode: SearchMode = "all_terms",
                limit: int = 25) -> dict[str, Any]:
    dataset = _dataset(dataset)
    query_text = (query_text or "").strip()
    if not query_text or len(query_text) > 200:
        raise ValueError("query_text must be 1-200 characters")
    start, end = _parse_time(start_time, "start_time"), _parse_time(end_time, "end_time")
    component = _short_text(component, "component")
    severity = _short_text(severity, "severity")
    limit = _clamp(limit, 1, MAX_SEARCH_LIMIT)
    table = DATASET_TABLES[dataset]

    where, applied = _common_filters(dataset, start, end, component, severity)
    match = _Where()
    if mode == "substring":
        escaped = query_text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        match.add("l.message ILIKE %s ESCAPE '\\'", f"%{escaped}%")
        source = f"{table} l"
    else:
        # Tokens without letters or digits (":", "-") analyse to nothing and make ts_phrase fail.
        terms = list(dict.fromkeys(t for t in query_text.split() if re.search(r"\w", t)))
        if not terms:
            raise ValueError("query_text has no searchable words; use mode='substring' for punctuation")
        if len(terms) > MAX_SEARCH_TERMS and mode != "phrase":
            raise ValueError(f"at most {MAX_SEARCH_TERMS} terms for {mode}; use mode='phrase' or fewer words")
        if mode == "all_terms":
            for term in terms:
                match.add("i.message @@ ts_phrase(%s)", term)
        elif mode == "any_terms":
            # An OR of phrase predicates; the text is never parsed as a TSQUERY expression.
            match.add("(" + " OR ".join("i.message @@ ts_phrase(%s)" for _ in terms) + ")", *terms)
        elif mode == "phrase":
            match.add("i.message @@ ts_phrase(%s)", query_text)
        else:
            raise ValueError(f"unknown mode {mode!r}")
        index = f"{table}_idx"
        source = f"{index} i JOIN {table} l ON l.line_id = i.line_id"  # filled in below

    predicates = match.parts + where.parts
    sql = f"""
        SELECT {SAMPLE_COLUMNS[dataset]},
               COUNT(*) OVER () AS total_matching_count
        FROM {source}
        WHERE {' AND '.join(predicates)}
        ORDER BY l.event_time, l.line_id
        LIMIT %s
    """
    with EvidenceBuilder("search_logs") as eb:
        notes = ["Sample rows show examples only; use total_matching_count or the aggregation tools for frequency."]
        if mode != "substring":
            # @@ only resolves against an inverted index relation; join back for the filters.
            # A workspace index covering the whole window answers the same query faster.
            ws = _covering_workspace_index(eb, table, start, end)
            if ws:
                sql = sql.replace(f"FROM {table}_idx i", f"FROM {ws} i", 1)
                notes.append(f"Served by workspace index {ws}, which covers the requested time window.")
        rows = eb.query(f"{mode} search with exact total count", sql, match.params + where.params + [limit])
        total = int(rows[0]["total_matching_count"]) if rows else 0
        for r in rows:
            r.pop("total_matching_count", None)
        truncated = total > len(rows)
        return eb.envelope(
            data_scope=_scope(eb, dataset, start, end, {**applied, "query_text": query_text, "mode": mode}),
            rows=rows,
            total_matching_count=total,
            is_truncated=truncated,
            truncation_notice=(f"Showing {len(rows)} of {total:,} matching log lines (earliest first). "
                               "Use the summary tools for whole-corpus counts by component or time.")
            if truncated else None,
            notes=notes,
        )


def _covering_workspace_index(eb: EvidenceBuilder, table: str, start: Optional[datetime],
                              end: Optional[datetime]) -> Optional[str]:
    """Name of a ws_* partial index whose window contains [start, end], if any."""
    if not start or not end:
        return None
    rows = eb.query("find covering workspace index", """
        SELECT indexname FROM pg_indexes WHERE tablename = %s AND left(indexname, 3) = 'ws_'
    """, [table])
    for r in rows:
        m = WORKSPACE_NAME_RE.match(r["indexname"])
        if m and m["table"] == table:
            ws_start = datetime.strptime(m["start"], "%Y%m%d%H%M%S")
            ws_end = datetime.strptime(m["end"], "%Y%m%d%H%M%S")
            if ws_start <= start and end <= ws_end:
                return r["indexname"]
    return None


def get_nearby_events(dataset: str, anchor_line_id: int, window_lines: int = 15,
                      same_entity_only: bool = True, max_seconds: int = 3600) -> dict[str, Any]:
    dataset = _dataset(dataset)
    window_lines = _clamp(window_lines, 1, MAX_WINDOW_LINES)
    max_seconds = _clamp(max_seconds, 1, 7 * 86400)
    table = DATASET_TABLES[dataset]
    cols = SAMPLE_COLUMNS[dataset] + (", l.source_file" if dataset == "OpenStack" else "")

    with EvidenceBuilder("get_nearby_events") as eb:
        found = eb.query("anchor line", f"SELECT {cols} FROM {table} l WHERE l.line_id = %s", [anchor_line_id])
        if not found:
            raise ValueError(f"line_id {anchor_line_id} not found in {table}")
        anchor = found[0]
        a_time = datetime.fromisoformat(anchor["event_time"])
        lo, hi = a_time - timedelta(seconds=max_seconds), a_time + timedelta(seconds=max_seconds)
        notes: list[str] = []

        entity_col = entity_val = None
        if same_entity_only:
            if dataset == "BGL" and anchor.get("node_id"):
                entity_col, entity_val = "node_id", anchor["node_id"]
            elif dataset == "OpenStack":
                for col in ("instance_id", "request_id"):
                    if anchor.get(col):
                        entity_col, entity_val = col, anchor[col]
                        break
            if entity_col is None:
                notes.append("Anchor has no node/instance/request ID; fell back to adjacent lines.")

        if entity_col:
            # Same entity, ordered by time, bounded to +/- max_seconds around the anchor.
            before_sql = f"""
                SELECT {cols} FROM {table} l
                WHERE l.{entity_col} = %s AND l.event_time >= %s::TIMESTAMP
                  AND (l.event_time < %s::TIMESTAMP OR (l.event_time = %s::TIMESTAMP AND l.line_id < %s))
                ORDER BY l.event_time DESC, l.line_id DESC LIMIT %s
            """
            after_sql = f"""
                SELECT {cols} FROM {table} l
                WHERE l.{entity_col} = %s AND l.event_time <= %s::TIMESTAMP
                  AND (l.event_time > %s::TIMESTAMP OR (l.event_time = %s::TIMESTAMP AND l.line_id > %s))
                ORDER BY l.event_time, l.line_id LIMIT %s
            """
            before = eb.query(f"preceding events on same {entity_col}", before_sql,
                              [entity_val, _ts(lo), _ts(a_time), _ts(a_time), anchor_line_id, window_lines])
            after = eb.query(f"following events on same {entity_col}", after_sql,
                             [entity_val, _ts(hi), _ts(a_time), _ts(a_time), anchor_line_id, window_lines])
            scope_filters = {entity_col: entity_val, "max_seconds": max_seconds}
        else:
            # Adjacent lines in file order. OpenStack line_ids are contiguous per source file.
            same_file = "AND l.source_file = %s " if dataset == "OpenStack" else ""
            file_param = [anchor["source_file"]] if dataset == "OpenStack" else []
            window_sql = f"""
                SELECT {cols} FROM {table} l
                WHERE l.line_id BETWEEN %s AND %s AND l.line_id <> %s {same_file}
                ORDER BY l.line_id
            """
            rows = eb.query("adjacent lines", window_sql,
                            [anchor_line_id - window_lines, anchor_line_id + window_lines, anchor_line_id] + file_param)
            before = [r for r in rows if r["line_id"] < anchor_line_id][::-1]
            after = [r for r in rows if r["line_id"] > anchor_line_id]
            scope_filters = {"window_lines": window_lines}

        ordered = before[::-1] + [{**anchor, "is_anchor": True}] + after
        for r in ordered:
            r["relative_line_offset"] = r["line_id"] - anchor_line_id
            r["relative_time_sec"] = round((datetime.fromisoformat(r["event_time"]) - a_time).total_seconds(), 3)
            r.setdefault("is_anchor", False)
        if dataset == "BGL" and entity_col:
            notes.append("relative_line_offset counts global log lines; other nodes' lines in between are excluded.")

        return eb.envelope(
            data_scope=_scope(eb, dataset, None, None, {"anchor_line_id": anchor_line_id, **scope_filters}),
            rows=ordered,
            is_truncated=len(before) == window_lines or len(after) == window_lines,
            truncation_notice=f"Capped at {window_lines} events before and after the anchor."
            if len(before) == window_lines or len(after) == window_lines else None,
            summary={
                "events_before": len(before),
                "events_after": len(after),
                "severities_before": _count_by(before, "severity"),
                "severities_after": _count_by(after, "severity"),
            },
            notes=notes,
        )


def _count_by(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in rows:
        out[str(r.get(key))] = out.get(str(r.get(key)), 0) + 1
    return out


# ----------------------------------------------------------------------------
# Team Lead / Manager: whole-corpus aggregations
# ----------------------------------------------------------------------------

BURST_GAP_SEC = 1800  # lines on the same component/severity less than 30 min apart form one burst

# Severity weights for the priority score (FATAL/ERROR vs WARNING).
SEVERITY_WEIGHT_SQL = """CASE
            WHEN s.severity IN ('FATAL', 'FAILURE', 'CRITICAL') THEN 1.0
            WHEN s.severity = 'SEVERE' THEN 0.9
            WHEN s.severity = 'ERROR' THEN 0.8
            WHEN s.severity IN ('WARNING', 'WARN') THEN 0.4
            WHEN s.severity = 'INFO' THEN 0.1
            ELSE 0.2 END"""

PRIORITY_WEIGHTS = {"burst_count_30m": 0.30, "burst_growth": 0.15, "distinct_affected": 0.20,
                    "active_days_with_issue": 0.15, "severity_weight": 0.20}
UNKNOWN_NOT_IN_LOGS = ["financial_cost", "customer_impact", "total_downtime", "sla_penalties"]


def _periods(eb: EvidenceBuilder, dataset: str, start: Optional[datetime],
             end: Optional[datetime]) -> dict[str, datetime]:
    """Resolves the counting window and the current/previous comparison periods.

    Explicit window: current = window, previous = the equally long window just before it.
    No window: counts cover the whole corpus; current/previous = second/first half of its span."""
    if start is None or end is None:
        bounds = eb.query(f"{dataset} time span", f"SELECT MIN(event_time) AS lo, MAX(event_time) AS hi "
                                                   f"FROM {DATASET_TABLES[dataset]}")[0]
        lo, hi = datetime.fromisoformat(bounds["lo"]), datetime.fromisoformat(bounds["hi"])
    if start is None and end is None:
        mid = lo + (hi - lo) / 2
        return {"window_start": lo, "window_end": hi, "current_start": mid, "current_end": hi,
                "previous_start": lo, "previous_end": mid, "scan_start": lo}
    w_start, w_end = start or lo, end or hi
    length = w_end - w_start
    return {"window_start": w_start, "window_end": w_end, "current_start": w_start, "current_end": w_end,
            "previous_start": w_start - length, "previous_end": w_start, "scan_start": w_start - length}


def _burst_aggregation_sql(dataset: str, p: dict[str, datetime], errors_only: bool) -> tuple[str, list[Any]]:
    """Per component x severity: counts, distinct entities, 30-minute burst clusters (LAG),
    the noisiest node's share, and bursts in the current vs previous period. No ORDER BY,
    so callers can wrap it."""
    table = DATASET_TABLES[dataset]
    entity = "l.node_id" if dataset == "BGL" else "l.instance_id"
    extra_cols = "" if dataset == "BGL" else ", l.request_id, l.instance_id"
    extra_aggs = ("" if dataset == "BGL" else
                  "COUNT(DISTINCT request_id) FILTER (WHERE in_window) AS distinct_requests,\n"
                  "COUNT(DISTINCT instance_id) FILTER (WHERE in_window) AS distinct_instances,\n")
    error_filter = f"AND {ERROR_FILTER[dataset][0]}" if errors_only else ""
    sql = f"""
        WITH base AS (
            SELECT l.component, l.severity, {entity} AS entity, l.event_time, l.is_anomaly {extra_cols}
            FROM {table} l
            WHERE l.event_time >= %s::TIMESTAMP AND l.event_time <= %s::TIMESTAMP {error_filter}
        ), marked AS (
            SELECT *,
                   CASE WHEN prev_time IS NULL OR epoch(event_time) - epoch(prev_time) > {BURST_GAP_SEC}
                        THEN 1 ELSE 0 END AS burst_start,
                   event_time >= %s::TIMESTAMP AS in_window,
                   event_time >= %s::TIMESTAMP AND event_time <= %s::TIMESTAMP AS in_current,
                   event_time >= %s::TIMESTAMP AND event_time < %s::TIMESTAMP AS in_previous
            FROM (SELECT *, LAG(event_time) OVER (PARTITION BY component, severity ORDER BY event_time) AS prev_time
                  FROM base) lagged
        ), agg AS (
            SELECT component, severity,
                   COUNT(*) FILTER (WHERE in_window) AS matching_log_entries,
                   COUNT(DISTINCT entity) FILTER (WHERE in_window) AS distinct_affected_nodes,
                   {extra_aggs}
                   COUNT(*) FILTER (WHERE in_window AND is_anomaly) AS labelled_anomaly_lines,
                   COUNT(DISTINCT CAST(event_time AS DATE)) FILTER (WHERE in_window) AS active_days_with_issue,
                   MIN(event_time) FILTER (WHERE in_window) AS first_seen,
                   MAX(event_time) FILTER (WHERE in_window) AS last_seen,
                   CAST(COALESCE(SUM(burst_start) FILTER (WHERE in_window), 0) AS BIGINT) AS burst_count_30m,
                   CAST(COALESCE(SUM(burst_start) FILTER (WHERE in_current), 0) AS BIGINT) AS current_period_bursts,
                   CAST(COALESCE(SUM(burst_start) FILTER (WHERE in_previous), 0) AS BIGINT) AS previous_period_bursts
            FROM marked GROUP BY component, severity
        ), node_counts AS (
            SELECT component, severity, entity, COUNT(*) AS n,
                   ROW_NUMBER() OVER (PARTITION BY component, severity ORDER BY COUNT(*) DESC, entity) AS rk
            FROM marked WHERE in_window AND entity IS NOT NULL
            GROUP BY component, severity, entity
        )
        SELECT COALESCE(o.team_name, 'Unassigned') AS team_name,
               COALESCE(o.escalation_tier, 'Unassigned') AS escalation_tier,
               a.*,
               t.entity AS top_node,
               ROUND(100.0 * t.n / a.matching_log_entries, 2) AS top_node_share_pct,
               CASE WHEN a.previous_period_bursts > 0
                    THEN ROUND(100.0 * (a.current_period_bursts - a.previous_period_bursts) / a.previous_period_bursts, 1)
               END AS growth_pct
        FROM agg a
        LEFT JOIN node_counts t ON t.component IS NOT DISTINCT FROM a.component
                               AND t.severity IS NOT DISTINCT FROM a.severity AND t.rk = 1
        LEFT JOIN team_ownership o ON o.dataset = '{dataset}' AND o.component = a.component
        WHERE a.matching_log_entries > 0
    """
    params = [_ts(p["scan_start"]), _ts(p["window_end"]), _ts(p["window_start"]),
              _ts(p["current_start"]), _ts(p["current_end"]), _ts(p["previous_start"]), _ts(p["previous_end"])]
    return sql, params


def _period_scope(p: dict[str, datetime]) -> dict[str, str]:
    return {k: v.isoformat() for k, v in p.items() if k != "scan_start"}


AGGREGATION_NOTES = [
    "matching_log_entries counts log lines, not incidents: one faulty node can emit thousands of lines. "
    "burst_count_30m counts bursts (lines on the same component and severity less than 30 minutes apart "
    "form one burst); top_node_share_pct near 100 means a single-node fault, low values a widespread one.",
    "For OpenStack, distinct_affected_nodes and top_node refer to VM instances (instance_id).",
    "current_period_bursts vs previous_period_bursts: an explicit window is compared with the equally long "
    "window before it; without a window, the second half of the corpus span is compared with the first. "
    "growth_pct is null when the previous period had no bursts.",
    "BGL and OpenStack are separate systems with different logging volumes; compare within a dataset, not across.",
]


def summarize_by_team_and_component(dataset: Literal["BGL", "OpenStack", "BOTH"] = "BOTH",
                                    start_time: Optional[str] = None, end_time: Optional[str] = None,
                                    errors_only: bool = True, limit: int = 50) -> dict[str, Any]:
    datasets = list(DATASET_TABLES) if dataset == "BOTH" else [_dataset(dataset)]
    start, end = _parse_time(start_time, "start_time"), _parse_time(end_time, "end_time")
    if start and end and end <= start:
        raise ValueError("end_time must be after start_time")
    limit = _clamp(limit, 1, MAX_AGG_ROWS)

    with EvidenceBuilder("summarize_by_team_and_component") as eb:
        all_rows: list[dict[str, Any]] = []
        scopes = []
        for ds in datasets:
            periods = _periods(eb, ds, start, end)
            sql, params = _burst_aggregation_sql(ds, periods, errors_only)
            rows = eb.query(f"{ds} team/component aggregation with 30-min bursts and top-node share",
                            sql + "\nORDER BY matching_log_entries DESC, component, severity", params)
            all_rows += [{"dataset": ds, **r} for r in rows]
            applied = {"errors_only": ERROR_FILTER[ds][1]} if errors_only else {}
            scopes.append({**_scope(eb, ds, start, end, applied), "periods": _period_scope(periods)})

        by_team: dict[str, int] = {}
        for r in all_rows:
            key = f"{r['dataset']}: {r['team_name']}"
            by_team[key] = by_team.get(key, 0) + r["matching_log_entries"]
        all_rows.sort(key=lambda r: (-r["matching_log_entries"], r["dataset"], str(r["component"]), str(r["severity"])))

        return eb.envelope(
            data_scope={"datasets": scopes},
            rows=all_rows[:limit],
            total_matching_count=sum(r["matching_log_entries"] for r in all_rows),
            is_truncated=len(all_rows) > limit,
            truncation_notice=f"Showing top {limit} of {len(all_rows)} groups; summary covers all groups."
            if len(all_rows) > limit else None,
            summary={
                "groups": len(all_rows),
                "matching_log_entries_by_team": dict(sorted(by_team.items(), key=lambda kv: -kv[1])),
                "bursts_by_team": _sum_by_team(all_rows, "burst_count_30m"),
            },
            notes=AGGREGATION_NOTES,
        )


def _sum_by_team(rows: list[dict[str, Any]], field: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in rows:
        key = f"{r['dataset']}: {r['team_name']}"
        out[key] = out.get(key, 0) + int(r[field] or 0)
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def get_priority_ranking(dataset: Literal["BGL", "OpenStack", "BOTH"] = "BOTH", start_time: Optional[str] = None,
                         end_time: Optional[str] = None, limit: int = 15) -> dict[str, Any]:
    """Deterministic 0-100 priority score per component x severity (errors and alerts only):
    weighted percentile ranks of bursts, burst growth, breadth, persistence and severity."""
    datasets = list(DATASET_TABLES) if dataset == "BOTH" else [_dataset(dataset)]
    start, end = _parse_time(start_time, "start_time"), _parse_time(end_time, "end_time")
    if start and end and end <= start:
        raise ValueError("end_time must be after start_time")
    limit = _clamp(limit, 1, MAX_AGG_ROWS)
    w = PRIORITY_WEIGHTS

    with EvidenceBuilder("get_priority_ranking") as eb:
        all_rows: list[dict[str, Any]] = []
        scopes = []
        for ds in datasets:
            periods = _periods(eb, ds, start, end)
            agg_sql, params = _burst_aggregation_sql(ds, periods, errors_only=True)
            sql = f"""
                SELECT r.*,
                       ROUND(100.0 * ({w['burst_count_30m']} * r.pct_bursts + {w['burst_growth']} * r.pct_growth
                                      + {w['distinct_affected']} * r.pct_breadth + {w['active_days_with_issue']} * r.pct_days
                                      + {w['severity_weight']} * r.severity_weight), 1) AS priority_score
                FROM (
                    SELECT s.team_name, s.escalation_tier, s.component, s.severity,
                           s.matching_log_entries, s.burst_count_30m, s.current_period_bursts,
                           s.previous_period_bursts, s.growth_pct, s.distinct_affected_nodes,
                           s.active_days_with_issue, s.top_node, s.top_node_share_pct,
                           {SEVERITY_WEIGHT_SQL} AS severity_weight,
                           ROUND(PERCENT_RANK() OVER (ORDER BY s.burst_count_30m), 4) AS pct_bursts,
                           ROUND(PERCENT_RANK() OVER (ORDER BY (s.current_period_bursts - s.previous_period_bursts)
                                                      * 1.0 / GREATEST(s.previous_period_bursts, 1)), 4) AS pct_growth,
                           ROUND(PERCENT_RANK() OVER (ORDER BY s.distinct_affected_nodes), 4) AS pct_breadth,
                           ROUND(PERCENT_RANK() OVER (ORDER BY s.active_days_with_issue), 4) AS pct_days
                    FROM ({agg_sql}) s
                ) r
                ORDER BY priority_score DESC, r.burst_count_30m DESC, r.component, r.severity
            """
            rows = eb.query(f"{ds} weighted percentile priority score", sql, params)
            all_rows += [{"dataset": ds, **r} for r in rows]
            scopes.append({**_scope(eb, ds, start, end, {"errors_only": ERROR_FILTER[ds][1]}),
                           "periods": _period_scope(periods)})

        all_rows.sort(key=lambda r: (-r["priority_score"], -r["burst_count_30m"], r["dataset"],
                                     str(r["component"]), str(r["severity"])))
        for rank, r in enumerate(all_rows, 1):
            r["rank"] = rank
        return eb.envelope(
            data_scope={"datasets": scopes},
            rows=all_rows[:limit],
            total_matching_count=len(all_rows),
            is_truncated=len(all_rows) > limit,
            truncation_notice=f"Showing top {limit} of {len(all_rows)} ranked groups." if len(all_rows) > limit else None,
            summary={
                "weights": w,
                "formula": "priority_score = 100 * sum(weight_i * percentile_rank_i), percentile ranks computed "
                           "within each dataset with PERCENT_RANK(); severity_weight FATAL/FAILURE/CRITICAL 1.0, "
                           "SEVERE 0.9, ERROR 0.8, WARNING 0.4, INFO (labelled alerts) 0.1",
                "burst_growth_metric": "(current_period_bursts - previous_period_bursts) / max(previous_period_bursts, 1)",
            },
            notes=AGGREGATION_NOTES[:3] + [
                "The score ranks only what the logs measure. Business impact is not in the logs: see unknown_not_in_logs."],
            extra={"unknown_not_in_logs": UNKNOWN_NOT_IN_LOGS},
        )


def get_latency_outliers(start_time: Optional[str] = None, end_time: Optional[str] = None, k: float = 6.0,
                         min_excess_sec: float = 2.0, limit: int = 25, min_samples: int = 20) -> dict[str, Any]:
    """OpenStack operations slower than max(median + k*MAD, median + min_excess_sec) of their own
    pattern (component x operation), with baselines computed over the whole corpus."""
    start, end = _parse_time(start_time, "start_time"), _parse_time(end_time, "end_time")
    k = float(max(1.0, min(float(k), 50.0)))
    min_excess_sec = float(max(0.0, min(float(min_excess_sec), 600.0)))
    limit = _clamp(limit, 1, MAX_SEARCH_LIMIT)
    min_samples = _clamp(min_samples, 5, 10_000)
    where, applied = _common_filters("OpenStack", start, end)
    window = ("AND " + " AND ".join(where.parts)) if where.parts else ""

    baseline = """
        WITH stats AS (
            SELECT component, operation, COUNT(*) AS samples, median(duration_sec) AS median_sec
            FROM openstack_logs WHERE duration_sec IS NOT NULL
            GROUP BY component, operation HAVING COUNT(*) >= %s
        ), mad AS (
            SELECT l.component, l.operation, median(abs(l.duration_sec - s.median_sec)) AS mad_sec
            FROM openstack_logs l JOIN stats s ON s.component = l.component AND s.operation = l.operation
            WHERE l.duration_sec IS NOT NULL
            GROUP BY l.component, l.operation
        ), thresholds AS (
            SELECT s.component, s.operation, s.samples, s.median_sec, m.mad_sec,
                   GREATEST(s.median_sec + %s * m.mad_sec, s.median_sec + %s) AS threshold_sec
            FROM stats s JOIN mad m ON m.component = s.component AND m.operation = s.operation
        )"""
    base_params = [min_samples, k, min_excess_sec]
    hits_sql = f"""{baseline}
        SELECT l.line_id, l.event_time, l.component, l.operation, l.severity, l.request_id, l.instance_id,
               ROUND(l.duration_sec, 4) AS duration_sec, ROUND(t.median_sec, 4) AS baseline_median_sec,
               ROUND(t.mad_sec, 4) AS baseline_mad_sec, ROUND(t.threshold_sec, 4) AS threshold_sec,
               ROUND(l.duration_sec / NULLIF(t.median_sec, 0), 2) AS ratio_to_median,
               l.severity = 'INFO' AS silent_info_level, l.message,
               COUNT(*) OVER () AS total_matching_count
        FROM openstack_logs l JOIN thresholds t ON t.component = l.component AND t.operation = l.operation
        WHERE l.duration_sec > t.threshold_sec {window}
        ORDER BY ratio_to_median DESC NULLS LAST, l.line_id
        LIMIT %s
    """
    patterns_sql = f"""{baseline}
        SELECT t.component, t.operation, t.samples, ROUND(t.median_sec, 4) AS median_sec,
               ROUND(t.mad_sec, 4) AS mad_sec, ROUND(t.threshold_sec, 4) AS threshold_sec,
               COUNT(l.line_id) AS outliers, ROUND(MAX(l.duration_sec), 4) AS worst_sec
        FROM thresholds t
        LEFT JOIN openstack_logs l ON l.component = t.component AND l.operation = t.operation
                                  AND l.duration_sec > t.threshold_sec {window}
        GROUP BY 1, 2, 3, 4, 5, 6
        ORDER BY outliers DESC, t.component, t.operation
        LIMIT 30
    """
    with EvidenceBuilder("get_latency_outliers") as eb:
        rows = eb.query("MAD outliers per component x operation", hits_sql, base_params + where.params + [limit])
        total = int(rows[0]["total_matching_count"]) if rows else 0
        for r in rows:
            r.pop("total_matching_count")
        patterns = eb.query("per-pattern baseline and outlier counts", patterns_sql, base_params + where.params)
        return eb.envelope(
            data_scope=_scope(eb, "OpenStack", start, end, {**applied, "k": k, "min_excess_sec": min_excess_sec,
                                                            "min_samples": min_samples}),
            rows=rows,
            total_matching_count=total,
            is_truncated=total > len(rows),
            truncation_notice=f"Showing the {len(rows)} most extreme of {total:,} outliers." if total > len(rows) else None,
            summary={"rule": "duration_sec > max(median + k * MAD, median + min_excess_sec) per component x operation; "
                             "baseline over the whole corpus",
                     "patterns": patterns,
                     "silent_info_outliers_in_sample": sum(1 for r in rows if r["silent_info_level"])},
            notes=["duration_sec is the HTTP latency for nova-api requests and X in 'Took X seconds to <operation>' "
                   "for nova-compute (spawn, build, deallocate, destroy).",
                   "silent_info_level marks outliers logged at INFO: no error was raised for them."],
        )


def get_recurring_trends(dataset: str, bucket_size: BucketSize = "week",
                         start_time: Optional[str] = None, end_time: Optional[str] = None,
                         component: Optional[str] = None, severity: Optional[str] = None,
                         errors_only: bool = True) -> dict[str, Any]:
    dataset = _dataset(dataset)
    if bucket_size not in ("hour", "day", "week"):
        raise ValueError("bucket_size must be 'hour', 'day' or 'week'")
    start, end = _parse_time(start_time, "start_time"), _parse_time(end_time, "end_time")
    if bucket_size == "hour" and (start is None or end is None or end - start > MAX_HOURLY_SPAN):
        raise ValueError("hourly buckets need start_time and end_time at most 31 days apart; use 'day' or 'week'")
    component = _short_text(component, "component")
    severity = _short_text(severity, "severity")

    entity = "COUNT(DISTINCT l.node_id)" if dataset == "BGL" else "COUNT(DISTINCT l.instance_id)"
    entity_name = "affected_nodes" if dataset == "BGL" else "distinct_instances"
    where, applied = _common_filters(dataset, start, end, component, severity, errors_only)
    sql = f"""
        SELECT date_trunc(%s, l.event_time) AS time_bucket,
               COALESCE(o.team_name, 'Unassigned') AS team_name,
               l.component,
               COUNT(*) AS log_entry_count,
               {entity} AS {entity_name}
        FROM {DATASET_TABLES[dataset]} l
        LEFT JOIN team_ownership o ON o.dataset = '{dataset}' AND o.component = l.component
        {where.sql()}
        GROUP BY 1, 2, 3
        ORDER BY 1, 4 DESC
    """
    with EvidenceBuilder("get_recurring_trends") as eb:
        rows = eb.query(f"{bucket_size}ly trend by component", sql, [bucket_size] + where.params)
        summary = _trend_summary(rows)
        # Period-over-period change per component.
        prev: dict[str, int] = {}
        for r in rows:
            p = prev.get(r["component"])
            r["delta_pct_vs_prev_bucket"] = round(100.0 * (r["log_entry_count"] - p) / p, 1) if p else None
            prev[r["component"]] = r["log_entry_count"]

        return eb.envelope(
            data_scope=_scope(eb, dataset, start, end, {**applied, "bucket_size": bucket_size}),
            rows=rows[:MAX_TREND_ROWS],
            total_matching_count=sum(r["log_entry_count"] for r in rows),
            is_truncated=len(rows) > MAX_TREND_ROWS,
            truncation_notice=f"Showing first {MAX_TREND_ROWS} of {len(rows)} bucket rows; the per-component "
                              "summary covers all buckets. Narrow the time range or filter by component for detail."
            if len(rows) > MAX_TREND_ROWS else None,
            summary=summary,
            notes=["Buckets with zero matching lines are omitted from rows but counted as zero in the summary.",
                   "delta_pct_vs_prev_bucket compares with the component's previous non-empty bucket."],
        )


def get_30min_burst_summary(dataset: Dataset, start_time: str, end_time: str,
                            limit: int = 100) -> dict[str, Any]:
    """Fixed half-hour buckets on [start_time, end_time); timestamps with offsets normalize to UTC."""
    dataset = _dataset(dataset)
    start, end = _parse_time(start_time, "start_time"), _parse_time(end_time, "end_time")
    if start is None or end is None or start >= end:
        raise ValueError("start_time and end_time are required and start_time must be earlier")
    limit = _clamp(limit, 1, MAX_AGG_ROWS)
    table = DATASET_TABLES[dataset]
    bucket = "date_trunc('hour', event_time) + CASE WHEN EXTRACT(MINUTE FROM event_time) >= 30 " \
             "THEN INTERVAL '30 minutes' ELSE INTERVAL '0 minutes' END"
    with EvidenceBuilder("get_30min_burst_summary") as eb:
        rows = eb.query("fixed 30-minute log and anomaly buckets", f"""
            SELECT {bucket} AS window_start, component, severity, COUNT(*) AS log_lines,
                   COUNT(*) FILTER (WHERE is_anomaly) AS anomaly_lines
            FROM {table}
            WHERE event_time >= %s::TIMESTAMP AND event_time < %s::TIMESTAMP
            GROUP BY 1, 2, 3 ORDER BY 1, log_lines DESC, component, severity LIMIT %s
        """, [_ts(start), _ts(end), limit])
        return eb.envelope(data_scope={"dataset": dataset, "window_start_inclusive": start.isoformat(),
                                       "window_end_exclusive": end.isoformat(), "bucket_minutes": 30},
                           rows=rows, total_matching_count=sum(int(r["log_lines"]) for r in rows),
                           is_truncated=len(rows) >= limit,
                           summary={"groups_returned": len(rows), "log_lines_in_returned_groups": sum(int(r["log_lines"]) for r in rows)},
                           notes=["Buckets use fixed clock boundaries at :00 and :30; query range is start-inclusive/end-exclusive.",
                                  "Total is the sum for returned groups and may be incomplete when the group limit is reached."])


def _trend_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    buckets = sorted({r["time_bucket"] for r in rows})
    per_comp: dict[str, dict[str, int]] = {}
    teams: dict[str, str] = {}
    for r in rows:
        per_comp.setdefault(r["component"], {})[r["time_bucket"]] = r["log_entry_count"]
        teams[r["component"]] = r["team_name"]

    components = []
    for comp, series in per_comp.items():
        counts = [series.get(b, 0) for b in buckets]
        total = sum(counts)
        half = len(counts) // 2
        first, second = sum(counts[:half]), sum(counts[half:])
        peak_bucket = max(series, key=series.get)
        active = sum(1 for c in counts if c)
        peak_share = series[peak_bucket] / total
        persistence = active / len(buckets)
        if active <= 2:
            pattern = "isolated burst"
        elif peak_share >= 0.5 and persistence >= 0.5:
            pattern = "chronic, dominated by one burst"
        elif peak_share >= 0.5:
            pattern = "burst (concentrated in few buckets)"
        elif second > first * 1.25:
            pattern = "increasing"
        elif second < first * 0.8:
            pattern = "decreasing"
        else:
            pattern = "chronic / stable"
        components.append({
            "component": comp,
            "team_name": teams[comp],
            "total_log_entries": total,
            "active_buckets": active,
            "persistence_pct": round(100.0 * persistence, 1),
            "peak_bucket": peak_bucket,
            "peak_count": series[peak_bucket],
            "peak_share_pct": round(100.0 * peak_share, 1),
            "first_half_total": first,
            "second_half_total": second,
            "pattern": pattern,
        })
    components.sort(key=lambda c: c["total_log_entries"], reverse=True)
    return {
        "buckets_in_range": len(buckets),
        "first_bucket": buckets[0] if buckets else None,
        "last_bucket": buckets[-1] if buckets else None,
        "components": components,
        "pattern_rule": "isolated burst if active in <=2 buckets; if the peak bucket holds >=50% of the total: "
                        "'chronic, dominated by one burst' when active in >=50% of buckets, else 'burst'; otherwise "
                        "increasing/decreasing if the second half is >25% above / >20% below the first; else chronic/stable",
    }


def get_openstack_latency_and_errors(start_time: Optional[str] = None, end_time: Optional[str] = None,
                                     component: Optional[str] = None,
                                     breakdown: Literal["component", "route"] = "component") -> dict[str, Any]:
    start, end = _parse_time(start_time, "start_time"), _parse_time(end_time, "end_time")
    component = _short_text(component, "component")
    where, applied = _common_filters("OpenStack", start, end, component)
    if breakdown == "route":
        group_cols, group_by = "l.component, l.http_method, l.http_route,", "GROUP BY 1, 2, 3, 4"
        where.add("l.http_route IS NOT NULL")
    else:
        group_cols, group_by = "l.component,", "GROUP BY 1, 2"
    sql = f"""
        SELECT COALESCE(o.team_name, 'Unassigned') AS team_name,
               {group_cols}
               COUNT(*) AS total_log_lines,
               COUNT(l.http_status) AS http_requests,
               COUNT(DISTINCT l.request_id) AS distinct_requests,
               COUNT(DISTINCT l.instance_id) AS distinct_instances,
               COUNT(*) FILTER (WHERE l.severity IN ('ERROR', 'CRITICAL')) AS error_log_lines,
               COUNT(*) FILTER (WHERE l.severity = 'WARNING') AS warning_log_lines,
               COUNT(*) FILTER (WHERE l.http_status >= 500) AS http_5xx,
               COUNT(*) FILTER (WHERE l.http_status >= 400 AND l.http_status < 500) AS http_4xx,
               ROUND(100.0 * COUNT(*) FILTER (WHERE l.http_status >= 500) / NULLIF(COUNT(l.http_status), 0), 3) AS http_5xx_rate_pct,
               COUNT(*) FILTER (WHERE l.is_anomaly) AS labelled_anomaly_lines,
               COUNT(DISTINCT l.instance_id) FILTER (WHERE l.is_anomaly) AS anomalous_instances,
               ROUND(AVG(l.latency_sec), 4) AS avg_latency_sec,
               ROUND(quantile_cont(l.latency_sec, 0.5), 4) AS p50_latency_sec,
               ROUND(quantile_cont(l.latency_sec, 0.95), 4) AS p95_latency_sec,
               ROUND(quantile_cont(l.latency_sec, 0.99), 4) AS p99_latency_sec,
               ROUND(MAX(l.latency_sec), 4) AS max_latency_sec
        FROM openstack_logs l
        LEFT JOIN team_ownership o ON o.dataset = 'OpenStack' AND o.component = l.component
        {where.sql()}
        {group_by}
        ORDER BY http_5xx DESC, error_log_lines DESC, p99_latency_sec DESC NULLS LAST
        LIMIT {MAX_AGG_ROWS + 1}
    """
    with EvidenceBuilder("get_openstack_latency_and_errors") as eb:
        rows = eb.query(f"service health by {breakdown}", sql, where.params)
        truncated = len(rows) > MAX_AGG_ROWS
        return eb.envelope(
            data_scope=_scope(eb, "OpenStack", start, end, {**applied, "breakdown": breakdown}),
            rows=rows[:MAX_AGG_ROWS],
            is_truncated=truncated,
            truncation_notice=f"Showing first {MAX_AGG_ROWS} groups." if truncated else None,
            notes=["Latency comes from the 'time:' field of nova-api WSGI access lines, so latency percentiles "
                   "only cover HTTP requests (http_requests), not every log line.",
                   "Anomaly labels are per instance (anomaly_labels.txt); a labelled line belongs to an instance "
                   "with an injected failure, not necessarily an error line."],
        )
