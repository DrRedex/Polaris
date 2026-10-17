# Worked investigation

Generated 2026-10-17 21:20 by `scripts/run_worked_investigation.py`. Same agent, same SereneDB corpus, three roles. Every tool call below lists the exact SQL and parameters, and each statement was re-executed to confirm it reproduces.

## Engineer

**Question:** Investigate the BGL KERNEL FATAL 'data TLB error interrupt' failures: what failed, on which nodes, and what happened just before and after a representative occurrence?

*Evidence-only run (no LLM).*

### Evidence

#### 1. `search_logs`

Arguments: `{"dataset": "BGL", "query_text": "data TLB error interrupt", "mode": "phrase", "component": "KERNEL", "severity": "FATAL", "limit": 10}`

- total_matching_count: 152734, returned: 10, truncated: True
- reproduced on re-execution: yes

*phrase search with exact total count* (112.0 ms, 10 rows)

```sql
SELECT l.line_id, l.event_time, l.node_id, l.component, l.severity, l.label, l.message, COUNT(*) OVER () AS total_matching_count FROM bgl_logs_idx i JOIN bgl_logs l ON l.line_id = i.line_id WHERE i.message @@ ts_phrase(%s) AND l.component = %s AND l.severity = %s ORDER BY l.event_time, l.line_id LIMIT %s
```
Parameters: `["data TLB error interrupt", "KERNEL", "FATAL", 10]`

| line_id | event_time | node_id | component | severity | label | message |
|---|---|---|---|---|---|---|
| 17742 | 2005-06-05T00:08:13.410695 | R20-M0-N2-C:J10-U11 | KERNEL | FATAL | KERNDTLB | data TLB error interrupt |
| 17745 | 2005-06-05T00:08:13.577322 | R20-M0-N2-C:J14-U01 | KERNEL | FATAL | KERNDTLB | data TLB error interrupt |
| 17758 | 2005-06-05T00:08:13.959099 | R20-M0-ND-C:J13-U11 | KERNEL | FATAL | KERNDTLB | data TLB error interrupt |
| 17809 | 2005-06-05T00:08:15.474037 | R20-M0-N9-C:J14-U01 | KERNEL | FATAL | KERNDTLB | data TLB error interrupt |
| 17855 | 2005-06-05T00:08:16.806826 | R20-M1-N2-C:J17-U11 | KERNEL | FATAL | KERNDTLB | data TLB error interrupt |

#### 2. `get_nearby_events`

Arguments: `{"dataset": "BGL", "anchor_line_id": 17742, "window_lines": 10, "same_entity_only": true}`

- total_matching_count: None, returned: 3, truncated: False
- reproduced on re-execution: yes

*anchor line* (4.3 ms, 1 rows)

```sql
SELECT l.line_id, l.event_time, l.node_id, l.component, l.severity, l.label, l.message FROM bgl_logs l WHERE l.line_id = %s
```
Parameters: `[17742]`

*preceding events on same node_id* (21.6 ms, 0 rows)

```sql
SELECT l.line_id, l.event_time, l.node_id, l.component, l.severity, l.label, l.message FROM bgl_logs l WHERE l.node_id = %s AND l.event_time >= %s::TIMESTAMP AND (l.event_time < %s::TIMESTAMP OR (l.event_time = %s::TIMESTAMP AND l.line_id < %s)) ORDER BY l.event_time DESC, l.line_id DESC LIMIT %s
```
Parameters: `["R20-M0-N2-C:J10-U11", "2005-06-04 23:08:13.410695", "2005-06-05 00:08:13.410695", "2005-06-05 00:08:13.410695", 17742, 10]`

*following events on same node_id* (14.2 ms, 2 rows)

```sql
SELECT l.line_id, l.event_time, l.node_id, l.component, l.severity, l.label, l.message FROM bgl_logs l WHERE l.node_id = %s AND l.event_time <= %s::TIMESTAMP AND (l.event_time > %s::TIMESTAMP OR (l.event_time = %s::TIMESTAMP AND l.line_id > %s)) ORDER BY l.event_time, l.line_id LIMIT %s
```
Parameters: `["R20-M0-N2-C:J10-U11", "2005-06-05 01:08:13.410695", "2005-06-05 00:08:13.410695", "2005-06-05 00:08:13.410695", 17742, 10]`

| line_id | event_time | node_id | component | severity | label | message | is_anchor |
|---|---|---|---|---|---|---|---|
| 17742 | 2005-06-05T00:08:13.410695 | R20-M0-N2-C:J10-U11 | KERNEL | FATAL | KERNDTLB | data TLB error interrupt | True |
| 21839 | 2005-06-05T00:35:57.659828 | R20-M0-N2-C:J10-U11 | KERNEL | INFO | - | generating core.3757 | False |
| 25992 | 2005-06-05T00:47:57.650765 | R20-M0-N2-C:J10-U11 | KERNEL | INFO | - | generating core.3757 | False |

<details><summary>Summary</summary>

```json
{
 "events_before": 0,
 "events_after": 2,
 "severities_before": {},
 "severities_after": {
  "INFO": 2
 }
}
```
</details>

## Team Lead

**Question:** Which error patterns recur across BGL and OpenStack, how often, and which teams should investigate what first?

*Evidence-only run (no LLM).*

### Evidence

#### 1. `summarize_by_team_and_component`

Arguments: `{"dataset": "BOTH", "errors_only": true, "limit": 25}`

- total_matching_count: 988775, returned: 17, truncated: False
- reproduced on re-execution: yes

*BGL team/component aggregation* (205.9 ms, 12 rows)

```sql
SELECT COALESCE(o.team_name, 'Unassigned') AS team_name, COALESCE(o.escalation_tier, 'Unassigned') AS escalation_tier, l.component, l.severity, COUNT(*) AS matching_log_entries, COUNT(DISTINCT l.node_id) AS distinct_affected_nodes, COUNT(*) FILTER (WHERE l.is_anomaly) AS labelled_anomaly_lines, COUNT(DISTINCT CAST(l.event_time AS DATE)) AS active_days_with_issue, MIN(l.event_time) AS first_seen, MAX(l.event_time) AS last_seen FROM bgl_logs l LEFT JOIN team_ownership o ON o.dataset = 'BGL' AND o.component = l.component WHERE (l.severity IN ('ERROR', 'SEVERE', 'FATAL', 'FAILURE') OR l.is_anomaly) GROUP BY 1, 2, 3, 4 ORDER BY matching_log_entries DESC
```
Parameters: `[]`

*OpenStack team/component aggregation* (28.1 ms, 5 rows)

```sql
SELECT COALESCE(o.team_name, 'Unassigned') AS team_name, COALESCE(o.escalation_tier, 'Unassigned') AS escalation_tier, l.component, l.severity, COUNT(*) AS matching_log_entries, COUNT(DISTINCT l.request_id) AS distinct_requests, COUNT(DISTINCT l.instance_id) AS distinct_instances, COUNT(*) FILTER (WHERE l.is_anomaly) AS labelled_anomaly_lines, COUNT(DISTINCT CAST(l.event_time AS DATE)) AS active_days_with_issue, MIN(l.event_time) AS first_seen, MAX(l.event_time) AS last_seen FROM openstack_logs l LEFT JOIN team_ownership o ON o.dataset = 'OpenStack' AND o.component = l.component WHERE (l.severity IN ('ERROR', 'CRITICAL') OR l.http_status >= 500 OR l.is_anomaly) GROUP BY 1, 2, 3, 4 ORDER BY matching_log_entries DESC
```
Parameters: `[]`

| dataset | team_name | escalation_tier | component | severity | matching_log_entries | distinct_affected_nodes | labelled_anomaly_lines |
|---|---|---|---|---|---|---|---|
| BGL | Core Systems & OS Team | Tier-1-Critical | KERNEL | FATAL | 626450 | 31467 | 284344 |
| BGL | Compute Runtime Team | Tier-2-High | APP | FATAL | 228536 | 1034 | 63845 |
| BGL | Cluster Control Plane Team | Tier-1-Critical | MMCS | ERROR | 88529 | 0 | 0 |
| BGL | Hardware Topology Team | Tier-2-High | DISCOVERY | ERROR | 23825 | 2003 | 0 |
| BGL | Hardware Topology Team | Tier-2-High | DISCOVERY | SEVERE | 18324 | 2006 | 0 |

<details><summary>Summary</summary>

```json
{
 "groups": 17,
 "matching_log_entries_by_team": {
  "BGL: Core Systems & OS Team": 626450,
  "BGL: Compute Runtime Team": 228536,
  "BGL: Cluster Control Plane Team": 88581,
  "BGL: Hardware Topology Team": 43022,
  "BGL: Observability & Telemetry Team": 1681,
  "OpenStack: Hypervisor & Compute Team": 292,
  "BGL: High-Speed Interconnect Team": 207,
  "OpenStack: Cloud API Gateway Team": 6
 }
}
```
</details>

#### 2. `get_recurring_trends`

Arguments: `{"dataset": "BGL", "bucket_size": "week", "errors_only": true}`

- total_matching_count: 988477, returned: 147, truncated: False
- reproduced on re-execution: yes

*weekly trend by component* (141.9 ms, 147 rows)

```sql
SELECT date_trunc(%s, l.event_time) AS time_bucket, COALESCE(o.team_name, 'Unassigned') AS team_name, l.component, COUNT(*) AS log_entry_count, COUNT(DISTINCT l.node_id) AS affected_nodes FROM bgl_logs l LEFT JOIN team_ownership o ON o.dataset = 'BGL' AND o.component = l.component WHERE (l.severity IN ('ERROR', 'SEVERE', 'FATAL', 'FAILURE') OR l.is_anomaly) GROUP BY 1, 2, 3 ORDER BY 1, 4 DESC
```
Parameters: `["week"]`

| time_bucket | team_name | component | log_entry_count | affected_nodes | delta_pct_vs_prev_bucket |
|---|---|---|---|---|---|
| 2005-05-30T00:00:00 | Core Systems & OS Team | KERNEL | 3854 | 467 | None |
| 2005-05-30T00:00:00 | Compute Runtime Team | APP | 2408 | 512 | None |
| 2005-06-06T00:00:00 | Core Systems & OS Team | KERNEL | 152521 | 35 | 3857.5 |
| 2005-06-06T00:00:00 | Compute Runtime Team | APP | 2240 | 480 | -7.0 |
| 2005-06-06T00:00:00 | Hardware Topology Team | DISCOVERY | 30 | 15 | None |

<details><summary>Summary</summary>

```json
{
 "buckets_in_range": 32,
 "first_bucket": "2005-05-30T00:00:00",
 "last_bucket": "2006-01-02T00:00:00",
 "components": [
  {
   "component": "KERNEL",
   "team_name": "Core Systems & OS Team",
   "total_log_entries": 626450,
   "active_buckets": 32,
   "persistence_pct": 100.0,
   "peak_bucket": "2005-06-13T00:00:00",
   "peak_count": 377594,
   "peak_share_pct": 60.3,
   "first_half_total": 593424,
   "second_half_total": 33026,
   "pattern": "burst (concentrated in few buckets)"
  },
  {
   "component": "APP",
   "team_name": "Compute Runtime Team",
   "total_log_entries": 228536,
   "active_buckets": 32,
   "persistence_pct": 100.0,
   "peak_bucket": "2005-08-08T00:00:00",
   "peak_count": 29224,
   "peak_share_pct": 12.8,
   "first_half_total": 89716,
   "second_half_total": 138820,
   "pattern": "increasing"
  },
  {
   "component": "MMCS",
   "team_name": "Cluster Control Plane Team",
   "total_log_entries": 88532,
   "active_buckets": 14,
   "persistence_pct": 43.8,
   "peak_bucket": "2005-09-19T00:00:00",
   "peak_count": 68012,
   "peak_share_pct": 76.8,
   "first_half_total": 20335,
   "second_half_total": 68197,
   "pattern": "burst (concentrated in few buckets)"
  },
  {
   "component": "DISCOVERY",
   "team_name": "Hardware Topology Team",
   "total_log_entries": 42149,
   "active_buckets": 27,
   "persistence_pct": 84.4,
   "peak_bucket": "2005-08-01T00:00:00",
   "peak_count": 21911,
   "peak_share_pct": 52.0,
   "first_half_total": 36798,
   "second_half_total": 5351,
   "pattern": "burst (concentrated in few buckets)"
  },
  {
   "component": "MONITOR",
   "team_name": "Observability & Telemetry Team",
   "total_log_entries": 1681,
   "active_buckets": 4,
   "persistence_pct": 12.5,
   "peak_bucket": "2005-11-07T00:00:00",
   "peak_count": 1330,
   "peak_share_pct": 79.1,
   "first_half_total": 2,
   "second_half_total": 1679,
   "pattern": "burst (concentrated in few buckets)"
  },
  {
   "component": "HARDWARE",
   "team_name": "Hardware Topology Team",
   "total_log_entries": 873,
   "active_buckets": 14,
   "persistence_pct": 43.8,
   "peak_bucket": "2005-08-01T00:00:00",
   "peak_count": 449,
   "peak_share_pct": 51.4,
   "first_half_total": 746,
   "second_half_total": 127,
   "pattern": "burst (concentrated in few buckets)"
  },
  {
   "component": "LINKCARD",
   "team_name": "High-Speed Interconnect Team",
   "total_log_entries": 206,
   "active_buckets": 10,
   "persistence_pct": 31.2,
   "peak_bucket": "2005-08-29T00:00:00",
   "peak_count": 146,
   "peak_share_pct": 70.9,
   "first_half_total": 194,
   "second_half_total": 12,
   "pattern": "burst (concentrated in few buckets)"
  },
  {
   "component": "BGLMASTER",
   "team_name": "Cluster Control Plane Team",
   "total_log_entries": 49,
   "active_buckets": 13,
   "persistence_pct": 40.6,
   "peak_bucket": "2005-11-28T00:00:00",
   "peak_count": 14,
   "peak_share_pct": 28.6,
   "first_half_total": 29,
   "second_half_total": 20,
   "pattern": "decreasing"
  },
  {
   "component": "SERV_NET",
   "team_name": "High-Speed Interconnect Team",
   "total_log_entries": 1,
   "active_buckets": 1,
   "persistence_pct": 3.1,
   "peak_bucket": "2005-08-01T00:00:00",
   "peak_count": 1,
   "peak_share_pct": 100.0,
   "first_half_total": 1,
   "second_half_total": 0,
   "pattern": "burst (concentrated in few buckets)"
  }
 ],
 "pattern_rule": "burst if active in <=2 buckets or peak bucket >= 50% of total; else increasing/decreasing if second half is >25% above / >20% below first half; otherwise chronic/stable"
}
```
</details>

#### 3. `get_recurring_trends`

Arguments: `{"dataset": "OpenStack", "bucket_size": "day", "errors_only": true}`

- total_matching_count: 298, returned: 5, truncated: False
- reproduced on re-execution: yes

*dayly trend by component* (28.4 ms, 5 rows)

```sql
SELECT date_trunc(%s, l.event_time) AS time_bucket, COALESCE(o.team_name, 'Unassigned') AS team_name, l.component, COUNT(*) AS log_entry_count, COUNT(DISTINCT l.instance_id) AS distinct_instances FROM openstack_logs l LEFT JOIN team_ownership o ON o.dataset = 'OpenStack' AND o.component = l.component WHERE (l.severity IN ('ERROR', 'CRITICAL') OR l.http_status >= 500 OR l.is_anomaly) GROUP BY 1, 2, 3 ORDER BY 1, 4 DESC
```
Parameters: `["day"]`

| time_bucket | team_name | component | log_entry_count | distinct_instances | delta_pct_vs_prev_bucket |
|---|---|---|---|---|---|
| 2017-05-14T00:00:00 | Hypervisor & Compute Team | nova-compute | 100 | 4 | None |
| 2017-05-16T00:00:00 | Hypervisor & Compute Team | nova-compute | 144 | 0 | 44.0 |
| 2017-05-16T00:00:00 | Cloud API Gateway Team | nova-api | 3 | 0 | None |
| 2017-05-17T00:00:00 | Hypervisor & Compute Team | nova-compute | 48 | 0 | -66.7 |
| 2017-05-17T00:00:00 | Cloud API Gateway Team | nova-api | 3 | 0 | 0.0 |

<details><summary>Summary</summary>

```json
{
 "buckets_in_range": 3,
 "first_bucket": "2017-05-14T00:00:00",
 "last_bucket": "2017-05-17T00:00:00",
 "components": [
  {
   "component": "nova-compute",
   "team_name": "Hypervisor & Compute Team",
   "total_log_entries": 292,
   "active_buckets": 3,
   "persistence_pct": 100.0,
   "peak_bucket": "2017-05-16T00:00:00",
   "peak_count": 144,
   "peak_share_pct": 49.3,
   "first_half_total": 100,
   "second_half_total": 192,
   "pattern": "increasing"
  },
  {
   "component": "nova-api",
   "team_name": "Cloud API Gateway Team",
   "total_log_entries": 6,
   "active_buckets": 2,
   "persistence_pct": 66.7,
   "peak_bucket": "2017-05-16T00:00:00",
   "peak_count": 3,
   "peak_share_pct": 50.0,
   "first_half_total": 0,
   "second_half_total": 6,
   "pattern": "burst (concentrated in few buckets)"
  }
 ],
 "pattern_rule": "burst if active in <=2 buckets or peak bucket >= 50% of total; else increasing/decreasing if second half is >25% above / >20% below first half; otherwise chronic/stable"
}
```
</details>

## Manager

**Question:** Which observed problems across both platforms deserve attention first, and what should happen next?

*Evidence-only run (no LLM).*

### Evidence

#### 1. `get_corpus_scope`

Arguments: `{}`

- total_matching_count: None, returned: 2, truncated: False
- reproduced on re-execution: yes

*BGL overview* (18.2 ms, 1 rows)

```sql
SELECT COUNT(*) AS total_rows, MIN(event_time) AS first_event, MAX(event_time) AS last_event, ROUND(epoch(MAX(event_time)) / 86400.0 - epoch(MIN(event_time)) / 86400.0, 1) AS span_days, COUNT(DISTINCT component) AS distinct_components, COUNT(*) FILTER (WHERE is_anomaly) AS labelled_anomaly_lines FROM bgl_logs
```
Parameters: `[]`

*BGL severity distribution* (15.0 ms, 10 rows)

```sql
SELECT severity, COUNT(*) AS lines FROM bgl_logs GROUP BY severity ORDER BY lines DESC
```
Parameters: `[]`

*OpenStack overview* (7.3 ms, 1 rows)

```sql
SELECT COUNT(*) AS total_rows, MIN(event_time) AS first_event, MAX(event_time) AS last_event, ROUND(epoch(MAX(event_time)) / 86400.0 - epoch(MIN(event_time)) / 86400.0, 1) AS span_days, COUNT(DISTINCT component) AS distinct_components, COUNT(*) FILTER (WHERE is_anomaly) AS labelled_anomaly_lines FROM openstack_logs
```
Parameters: `[]`

*OpenStack severity distribution* (6.0 ms, 4 rows)

```sql
SELECT severity, COUNT(*) AS lines FROM openstack_logs GROUP BY severity ORDER BY lines DESC
```
Parameters: `[]`

| dataset | table | total_rows | first_event | last_event | span_days | distinct_components | labelled_anomaly_lines |
|---|---|---|---|---|---|---|---|
| BGL | bgl_logs | 4747963 | 2005-06-03T15:42:50.363779 | 2006-01-04T08:00:05.233639 | 214.7 | 14 | 348460 |
| OpenStack | openstack_logs | 207820 | 2017-05-14T19:39:01.445000 | 2017-05-17T12:02:35.320000 | 2.7 | 3 | 100 |

#### 2. `summarize_by_team_and_component`

Arguments: `{"dataset": "BOTH", "errors_only": true, "limit": 15}`

- total_matching_count: 988775, returned: 15, truncated: True
- reproduced on re-execution: yes

*BGL team/component aggregation* (203.2 ms, 12 rows)

```sql
SELECT COALESCE(o.team_name, 'Unassigned') AS team_name, COALESCE(o.escalation_tier, 'Unassigned') AS escalation_tier, l.component, l.severity, COUNT(*) AS matching_log_entries, COUNT(DISTINCT l.node_id) AS distinct_affected_nodes, COUNT(*) FILTER (WHERE l.is_anomaly) AS labelled_anomaly_lines, COUNT(DISTINCT CAST(l.event_time AS DATE)) AS active_days_with_issue, MIN(l.event_time) AS first_seen, MAX(l.event_time) AS last_seen FROM bgl_logs l LEFT JOIN team_ownership o ON o.dataset = 'BGL' AND o.component = l.component WHERE (l.severity IN ('ERROR', 'SEVERE', 'FATAL', 'FAILURE') OR l.is_anomaly) GROUP BY 1, 2, 3, 4 ORDER BY matching_log_entries DESC
```
Parameters: `[]`

*OpenStack team/component aggregation* (26.3 ms, 5 rows)

```sql
SELECT COALESCE(o.team_name, 'Unassigned') AS team_name, COALESCE(o.escalation_tier, 'Unassigned') AS escalation_tier, l.component, l.severity, COUNT(*) AS matching_log_entries, COUNT(DISTINCT l.request_id) AS distinct_requests, COUNT(DISTINCT l.instance_id) AS distinct_instances, COUNT(*) FILTER (WHERE l.is_anomaly) AS labelled_anomaly_lines, COUNT(DISTINCT CAST(l.event_time AS DATE)) AS active_days_with_issue, MIN(l.event_time) AS first_seen, MAX(l.event_time) AS last_seen FROM openstack_logs l LEFT JOIN team_ownership o ON o.dataset = 'OpenStack' AND o.component = l.component WHERE (l.severity IN ('ERROR', 'CRITICAL') OR l.http_status >= 500 OR l.is_anomaly) GROUP BY 1, 2, 3, 4 ORDER BY matching_log_entries DESC
```
Parameters: `[]`

| dataset | team_name | escalation_tier | component | severity | matching_log_entries | distinct_affected_nodes | labelled_anomaly_lines |
|---|---|---|---|---|---|---|---|
| BGL | Core Systems & OS Team | Tier-1-Critical | KERNEL | FATAL | 626450 | 31467 | 284344 |
| BGL | Compute Runtime Team | Tier-2-High | APP | FATAL | 228536 | 1034 | 63845 |
| BGL | Cluster Control Plane Team | Tier-1-Critical | MMCS | ERROR | 88529 | 0 | 0 |
| BGL | Hardware Topology Team | Tier-2-High | DISCOVERY | ERROR | 23825 | 2003 | 0 |
| BGL | Hardware Topology Team | Tier-2-High | DISCOVERY | SEVERE | 18324 | 2006 | 0 |

<details><summary>Summary</summary>

```json
{
 "groups": 17,
 "matching_log_entries_by_team": {
  "BGL: Core Systems & OS Team": 626450,
  "BGL: Compute Runtime Team": 228536,
  "BGL: Cluster Control Plane Team": 88581,
  "BGL: Hardware Topology Team": 43022,
  "BGL: Observability & Telemetry Team": 1681,
  "OpenStack: Hypervisor & Compute Team": 292,
  "BGL: High-Speed Interconnect Team": 207,
  "OpenStack: Cloud API Gateway Team": 6
 }
}
```
</details>

#### 3. `get_openstack_latency_and_errors`

Arguments: `{}`

- total_matching_count: None, returned: 3, truncated: False
- reproduced on re-execution: yes

*service health by component* (107.6 ms, 3 rows)

```sql
SELECT COALESCE(o.team_name, 'Unassigned') AS team_name, l.component, COUNT(*) AS total_log_lines, COUNT(l.http_status) AS http_requests, COUNT(DISTINCT l.request_id) AS distinct_requests, COUNT(DISTINCT l.instance_id) AS distinct_instances, COUNT(*) FILTER (WHERE l.severity IN ('ERROR', 'CRITICAL')) AS error_log_lines, COUNT(*) FILTER (WHERE l.severity = 'WARNING') AS warning_log_lines, COUNT(*) FILTER (WHERE l.http_status >= 500) AS http_5xx, COUNT(*) FILTER (WHERE l.http_status >= 400 AND l.http_status < 500) AS http_4xx, ROUND(100.0 * COUNT(*) FILTER (WHERE l.http_status >= 500) / NULLIF(COUNT(l.http_status), 0), 3) AS http_5xx_rate_pct, COUNT(*) FILTER (WHERE l.is_anomaly) AS labelled_anomaly_lines, COUNT(DISTINCT l.instance_id) FILTER (WHERE l.is_anomaly) AS anomalous_instances, ROUND(AVG(l.latency_sec), 4) AS avg_latency_sec, ROUND(quantile_cont(l.latency_sec, 0.5), 4) AS p50_latency_sec, ROUND(quantile_cont(l.latency_sec, 0.95), 4) AS p95_latency_sec, ROUND(quantile_cont(l.latency_sec, 0.99), 4) AS p99_latency_sec, ROUND(MAX(l.latency_sec), 4) AS max_latency_sec FROM openstack_logs l LEFT JOIN team_ownership o ON o.dataset = 'OpenStack' AND o.component = l.component GROUP BY 1, 2 ORDER BY http_5xx DESC, error_log_lines DESC, p99_latency_sec DESC NULLS LAST LIMIT 101
```
Parameters: `[]`

| team_name | component | total_log_lines | http_requests | distinct_requests | distinct_instances | error_log_lines | warning_log_lines |
|---|---|---|---|---|---|---|---|
| Cloud API Gateway Team | nova-api | 102868 | 98733 | 89535 | 0 | 4 | 2 |
| Hypervisor & Compute Team | nova-compute | 104086 | 0 | 4134 | 2069 | 192 | 3116 |
| Placement & Scheduling Team | nova-scheduler | 866 | 0 | 866 | 0 | 0 | 0 |
