# Performance & Special Awards Evidence

This file is appended to by benchmark scripts. A skipped or failed run is retained as such; it is not a measurement.

## Metric definitions

- `sql_execution_ms` is client round-trip query execution time recorded by psycopg, not server-only time.
- `rows_scanned` is unavailable unless SereneDB exposes a reliable execution counter. Table/source row totals are not scan counts.
- `compression_ratio` means `rows_scanned / rows_returned`; it is null when either input is unavailable or zero.
- `message_chars_truncated` sums original message characters removed by truncating each output message to 300 characters.
- Break-even is the smallest positive integer `q` satisfying `q × (avg_unindexed_ms - avg_indexed_ms) >= setup_ms`. Lifecycle break-even adds cleanup time. Nonpositive savings means not reached.
- Cloud cost estimates require a configured price per byte and reliable scanned-byte measurement. Without both, cost is unavailable.
- OTel `total_rows_queried` is the number of rows matching the selected 30-second window and optional filters; `total_source_rows_from_footer` is the partition row total from footer metadata. Neither value is a rows-scanned metric.

## Corrections to earlier timestamped runs

- The partial indexing run at `2026-10-17T19:11:31Z` recorded a cleanup-reporting exception after the temporary index had been dropped. Its measurements are retained as a partial run; use later successful runs for break-even results.
- The successful indexing run at `2026-10-17T19:11:48Z` labeled its nearby-context query, but that version selected the first 20 rows in the time window rather than rows around a line anchor. Do not interpret that sample as nearby-context timing. The corrected anchored query is recorded in later runs.
- The OTel run at `2026-10-17T19:12:03Z` completed its bounded summary query, but its role smoke checks were structural only and did not perform trace correlation. Use the later OTel record for the measured trace-timeline smoke check.

## Current environment status

The local read-only discovery pass on 2026-10-18 found SereneDB 26.10.1 (PostgreSQL 18.3 wire-compatible), the three expected log/ownership tables, and the `log_reader` role. The requested Engineer, Team Lead, and Manager login roles and their DSNs were absent during that inspection. This is environment discovery, not a benchmark run. No 50-billion-row query or scan is implied.

Use `make benchmarks` for safe skipped-state entries. Live local indexing requires `uv run python scripts/benchmark_indexing.py --live`; remote OTel requires `uv run python scripts/benchmark_huge_datasets.py --live --partitions 1`. More than one remote partition additionally requires `--confirm-multpartition`.

## Benchmark run 2026-10-17T19:11:00.142828+00:00

Status: **skipped**

```json
{
  "status": "skipped",
  "reason": "Live indexing benchmark requires --live.",
  "timestamp": "2026-10-17T19:11:00.142828+00:00"
}
```

## OTel benchmark run 2026-10-17T19:11:00.350007+00:00

```json
{
  "status": "skipped",
  "reason": "Remote scan requires --live.",
  "partitions_requested": 1,
  "timestamp": "2026-10-17T19:11:00.350007+00:00"
}
```

## Benchmark run 2026-10-17T19:11:31.803582+00:00

Status: **partial**

```json
{
  "status": "partial",
  "timestamp": "2026-10-17T19:11:31.803582+00:00",
  "live_database": true,
  "window": [
    "2005-06-03T00:00:00",
    "2005-06-17T00:00:00"
  ],
  "query_runs_per_arm": 10,
  "query_definitions": [
    {
      "name": "full_text_machine_check",
      "sql": "SELECT COUNT(*) AS n FROM bgl_logs_idx i JOIN bgl_logs l ON l.line_id=i.line_id WHERE i.message @@ ts_phrase(%s) AND l.event_time >= %s::TIMESTAMP AND l.event_time < %s::TIMESTAMP",
      "parameters": [
        "machine check",
        "2005-06-03T00:00:00",
        "2005-06-17T00:00:00"
      ]
    },
    {
      "name": "nearby_context",
      "sql": "SELECT line_id,event_time,node_id,component,severity FROM bgl_logs WHERE event_time >= %s::TIMESTAMP AND event_time < %s::TIMESTAMP ORDER BY event_time LIMIT 20",
      "parameters": [
        "2005-06-03T00:00:00",
        "2005-06-17T00:00:00"
      ]
    },
    {
      "name": "component_counts",
      "sql": "SELECT component,COUNT(*) AS n FROM bgl_logs WHERE event_time >= %s::TIMESTAMP AND event_time < %s::TIMESTAMP GROUP BY component ORDER BY n DESC",
      "parameters": [
        "2005-06-03T00:00:00",
        "2005-06-17T00:00:00"
      ]
    }
  ],
  "index_name": "ws_bgl_logs_20050603000000_20050617000000",
  "index_creation_time_ms": 291.3,
  "cleanup_time_ms": 0.0,
  "setup_only_formula": "ceil(creation_ms / max(avg_unindexed_ms - avg_indexed_ms, 0))",
  "lifecycle_formula": "ceil((creation_ms + cleanup_ms) / saving_ms)",
  "results": {
    "full_text_machine_check": {
      "avg_unindexed_query_ms": 53.59,
      "avg_indexed_query_ms": 4.13,
      "time_saved_per_query_ms": 49.46,
      "speedup_factor": 12.975786924939468,
      "setup_only_breakeven_query_count": 6,
      "lifecycle_breakeven_query_count": 6,
      "raw_ms": {
        "unindexed": [
          85.9,
          64.8,
          54.7,
          38.2,
          56.9,
          53.3,
          49.7,
          48.8,
          34.9,
          48.7
        ],
        "indexed": [
          5.3,
          4.1,
          3.9,
          4.4,
          3.8,
          5.3,
          3.7,
          3.7,
          3.7,
          3.4
        ]
      }
    },
    "nearby_context": {
      "avg_unindexed_query_ms": 6.5,
      "avg_indexed_query_ms": 5.17,
      "time_saved_per_query_ms": 1.33,
      "speedup_factor": 1.2572533849129595,
      "setup_only_breakeven_query_count": 220,
      "lifecycle_breakeven_query_count": 220,
      "raw_ms": {
        "unindexed": [
          10.8,
          6.8,
          6.3,
          6.5,
          5.7,
          7.5,
          5.5,
          5.1,
          5.7,
          5.1
        ],
        "indexed": [
          5.3,
          5.2,
          4.9,
          5.5,
          5.9,
          7.7,
          4.3,
          4.2,
          4.3,
          4.4
        ]
      }
    },
    "component_counts": {
      "avg_unindexed_query_ms": 5.36,
      "avg_indexed_query_ms": 5.05,
      "time_saved_per_query_ms": 0.3100000000000005,
      "speedup_factor": 1.0613861386138614,
      "setup_only_breakeven_query_count": 940,
      "lifecycle_breakeven_query_count": 940,
      "raw_ms": {
        "unindexed": [
          8.4,
          5.4,
          5.7,
          5.0,
          5.0,
          6.2,
          4.7,
          4.6,
          4.2,
          4.4
        ],
        "indexed": [
          5.3,
          5.3,
          5.4,
          5.2,
          4.7,
          6.6,
          4.1,
          4.4,
          4.6,
          4.9
        ]
      }
    }
  },
  "errors": [
    "cleanup: 'NoneType' object does not support item assignment"
  ]
}
```

## Benchmark run 2026-10-17T19:11:48.414419+00:00

Status: **success**

```json
{
  "status": "success",
  "timestamp": "2026-10-17T19:11:48.414419+00:00",
  "live_database": true,
  "window": [
    "2005-06-03T00:00:00",
    "2005-06-17T00:00:00"
  ],
  "query_runs_per_arm": 10,
  "query_definitions": [
    {
      "name": "full_text_machine_check",
      "sql": "SELECT COUNT(*) AS n FROM bgl_logs_idx i JOIN bgl_logs l ON l.line_id=i.line_id WHERE i.message @@ ts_phrase(%s) AND l.event_time >= %s::TIMESTAMP AND l.event_time < %s::TIMESTAMP",
      "parameters": [
        "machine check",
        "2005-06-03T00:00:00",
        "2005-06-17T00:00:00"
      ]
    },
    {
      "name": "nearby_context",
      "sql": "SELECT line_id,event_time,node_id,component,severity FROM bgl_logs WHERE event_time >= %s::TIMESTAMP AND event_time < %s::TIMESTAMP ORDER BY event_time LIMIT 20",
      "parameters": [
        "2005-06-03T00:00:00",
        "2005-06-17T00:00:00"
      ]
    },
    {
      "name": "component_counts",
      "sql": "SELECT component,COUNT(*) AS n FROM bgl_logs WHERE event_time >= %s::TIMESTAMP AND event_time < %s::TIMESTAMP GROUP BY component ORDER BY n DESC",
      "parameters": [
        "2005-06-03T00:00:00",
        "2005-06-17T00:00:00"
      ]
    }
  ],
  "index_name": "ws_bgl_logs_20050603000000_20050617000000",
  "index_creation_time_ms": 249.92700000000002,
  "cleanup_time_ms": 25.33,
  "setup_only_formula": "ceil(creation_ms / max(avg_unindexed_ms - avg_indexed_ms, 0))",
  "lifecycle_formula": "ceil((creation_ms + cleanup_ms) / saving_ms)",
  "results": {
    "full_text_machine_check": {
      "avg_unindexed_query_ms": 45.3023,
      "avg_indexed_query_ms": 4.036,
      "time_saved_per_query_ms": 41.2663,
      "speedup_factor": 11.224554013875126,
      "setup_only_breakeven_query_count": 7,
      "lifecycle_breakeven_query_count": 7,
      "raw_ms": {
        "unindexed": [
          45.366,
          44.886,
          45.963,
          47.001,
          51.596,
          36.783,
          42.499,
          47.072,
          42.348,
          49.509
        ],
        "indexed": [
          4.768,
          4.185,
          4.599,
          4.261,
          4.051,
          4.883,
          3.378,
          3.217,
          3.345,
          3.673
        ]
      }
    },
    "nearby_context": {
      "avg_unindexed_query_ms": 5.9495,
      "avg_indexed_query_ms": 5.3972999999999995,
      "time_saved_per_query_ms": 0.5522,
      "speedup_factor": 1.1023104144664926,
      "setup_only_breakeven_query_count": 453,
      "lifecycle_breakeven_query_count": 499,
      "raw_ms": {
        "unindexed": [
          6.681,
          6.635,
          5.828,
          6.365,
          6.125,
          7.752,
          5.552,
          4.539,
          5.326,
          4.692
        ],
        "indexed": [
          5.755,
          5.894,
          5.692,
          6.01,
          5.314,
          7.064,
          4.76,
          4.597,
          4.569,
          4.318
        ]
      }
    },
    "component_counts": {
      "avg_unindexed_query_ms": 5.3152,
      "avg_indexed_query_ms": 4.8846,
      "time_saved_per_query_ms": 0.4306000000000001,
      "speedup_factor": 1.0881546083609712,
      "setup_only_breakeven_query_count": 581,
      "lifecycle_breakeven_query_count": 640,
      "raw_ms": {
        "unindexed": [
          6.47,
          5.043,
          5.195,
          5.72,
          5.098,
          6.02,
          5.972,
          4.591,
          4.505,
          4.538
        ],
        "indexed": [
          5.056,
          4.656,
          5.11,
          5.302,
          5.387,
          6.269,
          4.065,
          4.223,
          4.413,
          4.365
        ]
      }
    }
  },
  "errors": []
}
```

## OTel benchmark run 2026-10-17T19:12:03.209572+00:00

```json
{
  "status": "success",
  "partitions": [
    "https://public-pme.s3.eu-west-3.amazonaws.com/text_bench/part_000.parquet"
  ],
  "filters": {
    "service": null,
    "severity": null
  },
  "distribution": [
    {
      "service_name": "cart",
      "severity": "info",
      "log_lines": 4778335
    },
    {
      "service_name": "frontend",
      "severity": "info",
      "log_lines": 3498086
    },
    {
      "service_name": "cart",
      "severity": "information",
      "log_lines": 2312217
    },
    {
      "service_name": "frontend",
      "severity": "error",
      "log_lines": 2141241
    },
    {
      "service_name": "email",
      "severity": "info",
      "log_lines": 1128683
    },
    {
      "service_name": "shipping",
      "severity": "info",
      "log_lines": 448799
    },
    {
      "service_name": "recommendation",
      "severity": "info",
      "log_lines": 420363
    },
    {
      "service_name": "recommendation",
      "severity": "trace",
      "log_lines": 419927
    },
    {
      "service_name": "quote",
      "severity": "info",
      "log_lines": 417043
    },
    {
      "service_name": "checkout",
      "severity": "info",
      "log_lines": 416537
    },
    {
      "service_name": "payment",
      "severity": "info",
      "log_lines": 329828
    },
    {
      "service_name": "frontend",
      "severity": "trace",
      "log_lines": 277158
    },
    {
      "service_name": "currency",
      "severity": "info",
      "log_lines": 256793
    },
    {
      "service_name": "payment",
      "severity": "error",
      "log_lines": 231212
    },
    {
      "service_name": "email",
      "severity": "trace",
      "log_lines": 216964
    },
    {
      "service_name": "email",
      "severity": "error",
      "log_lines": 39575
    },
    {
      "service_name": "payment",
      "severity": "trace",
      "log_lines": 20607
    },
    {
      "service_name": "kafka",
      "severity": "info",
      "log_lines": 19669
    },
    {
      "service_name": "checkout",
      "severity": "warn",
      "log_lines": 19530
    },
    {
      "service_name": "fraud-detection",
      "severity": "trace",
      "log_lines": 12398
    },
    {
      "service_name": "fraud-detection",
      "severity": "info",
      "log_lines": 12248
    },
    {
      "service_name": "",
      "severity": "info",
      "log_lines": 11339
    },
    {
      "service_name": "accounting",
      "severity": "info",
      "log_lines": 11302
    },
    {
      "service_name": "accounting",
      "severity": "information",
      "log_lines": 5696
    },
    {
      "service_name": "ad",
      "severity": "info",
      "log_lines": 4598
    },
    {
      "service_name": "ad",
      "severity": "trace",
      "log_lines": 4228
    },
    {
      "service_name": "currency",
      "severity": "error",
      "log_lines": 3582
    },
    {
      "service_name": "product-catalog",
      "severity": "info",
      "log_lines": 3110
    },
    {
      "service_name": "valkey-cart",
      "severity": "info",
      "log_lines": 448
    },
    {
      "service_name": "",
      "severity": "fatal",
      "log_lines": 325
    },
    {
      "service_name": "checkout",
      "severity": "error",
      "log_lines": 320
    },
    {
      "service_name": "quote",
      "severity": "error",
      "log_lines": 310
    },
    {
      "service_name": "recommendation",
      "severity": "warn",
      "log_lines": 239
    },
    {
      "service_name": "quote",
      "severity": "trace",
      "log_lines": 200
    },
    {
      "service_name": "accounting",
      "severity": "error",
      "log_lines": 99
    },
    {
      "service_name": "cart",
      "severity": "trace",
      "log_lines": 84
    },
    {
      "service_name": "",
      "severity": "error",
      "log_lines": 65
    },
    {
      "service_name": "shipping",
      "severity": "trace",
      "log_lines": 49
    },
    {
      "service_name": "product-catalog",
      "severity": "trace",
      "log_lines": 46
    },
    {
      "service_name": "cart",
      "severity": "error",
      "log_lines": 42
    },
    {
      "service_name": "checkout",
      "severity": "trace",
      "log_lines": 23
    },
    {
      "service_name": "ad",
      "severity": "warn",
      "log_lines": 20
    },
    {
      "service_name": "ad",
      "severity": "error",
      "log_lines": 16
    },
    {
      "service_name": "",
      "severity": "warn",
      "log_lines": 13
    },
    {
      "service_name": "load-generator",
      "severity": "warn",
      "log_lines": 13
    },
    {
      "service_name": "kafka",
      "severity": "warn",
      "log_lines": 10
    },
    {
      "service_name": "kafka",
      "severity": "error",
      "log_lines": 10
    },
    {
      "service_name": "accounting",
      "severity": "trace",
      "log_lines": 9
    },
    {
      "service_name": "fraud-detection",
      "severity": "warn",
      "log_lines": 5
    },
    {
      "service_name": "product-catalog",
      "severity": "error",
      "log_lines": 3
    },
    {
      "service_name": "fraud-detection",
      "severity": "error",
      "log_lines": 1
    }
  ],
  "total_rows_queried": 17463418,
  "elapsed_ms": 18267.0,
  "rows_scanned": null,
  "rows_scanned_reason": "Engine scan counters unavailable.",
  "estimated_cloud_scan_cost_usd": null,
  "estimated_cloud_scan_cost_reason": "No configured unit price and reliable scanned-byte telemetry.",
  "completed": true,
  "client_elapsed_ms": 18267.0,
  "timestamp": "2026-10-17T19:12:03.209572+00:00",
  "role_actionability_smoke": {
    "engineer": {
      "status": "structure_only",
      "summary_keys": [
        "status",
        "partitions",
        "filters",
        "distribution",
        "total_rows_queried",
        "elapsed_ms",
        "rows_scanned",
        "rows_scanned_reason",
        "estimated_cloud_scan_cost_usd",
        "estimated_cloud_scan_cost_reason",
        "completed",
        "client_elapsed_ms",
        "timestamp"
      ]
    },
    "team_lead": {
      "status": "structure_only",
      "summary_keys": [
        "status",
        "partitions",
        "filters",
        "distribution",
        "total_rows_queried",
        "elapsed_ms",
        "rows_scanned",
        "rows_scanned_reason",
        "estimated_cloud_scan_cost_usd",
        "estimated_cloud_scan_cost_reason",
        "completed",
        "client_elapsed_ms",
        "timestamp",
        "role_actionability_smoke"
      ]
    },
    "manager": {
      "status": "structure_only",
      "summary_keys": [
        "status",
        "partitions",
        "filters",
        "distribution",
        "total_rows_queried",
        "elapsed_ms",
        "rows_scanned",
        "rows_scanned_reason",
        "estimated_cloud_scan_cost_usd",
        "estimated_cloud_scan_cost_reason",
        "completed",
        "client_elapsed_ms",
        "timestamp",
        "role_actionability_smoke"
      ]
    }
  }
}
```

## Benchmark run 2026-10-17T19:17:03.119850+00:00

Status: **success**

```json
{
  "status": "success",
  "timestamp": "2026-10-17T19:17:03.119850+00:00",
  "live_database": true,
  "serenedb_version": "PostgreSQL 18.3 (SereneDB 26.10.1)",
  "dataset": "BGL",
  "corpus_scope": [
    {
      "dataset": "BGL",
      "table": "bgl_logs",
      "total_rows": 4747963,
      "first_event": "2005-06-03T15:42:50.363779",
      "last_event": "2006-01-04T08:00:05.233639",
      "span_days": 214.7,
      "distinct_components": 14,
      "labelled_anomaly_lines": 348460,
      "severity_distribution": {
        "INFO": 3735813,
        "FATAL": 855195,
        "ERROR": 112355,
        "WARNING": 23357,
        "SEVERE": 19213,
        "FAILURE": 1714,
        "Kill": 306,
        "single": 4,
        "microseconds": 4,
        "0x00544eb8,": 2
      }
    },
    {
      "dataset": "OpenStack",
      "table": "openstack_logs",
      "total_rows": 207820,
      "first_event": "2017-05-14T19:39:01.445000",
      "last_event": "2017-05-17T12:02:35.320000",
      "span_days": 2.7,
      "distinct_components": 3,
      "labelled_anomaly_lines": 100,
      "severity_distribution": {
        "INFO": 204506,
        "WARNING": 3118,
        "ERROR": 194,
        "CRITICAL": 2
      }
    }
  ],
  "anchor_line_id_for_nearby_context": 1,
  "window": [
    "2005-06-03T00:00:00",
    "2005-06-17T00:00:00"
  ],
  "query_runs_per_arm": 10,
  "cache_conditions": "Sequential execution; no cache flush was requested. First samples may be cold; later samples are warm.",
  "raw_sample_count_per_query_arm": {
    "full_text_machine_check": {
      "unindexed": 10,
      "indexed": 10
    },
    "nearby_context": {
      "unindexed": 10,
      "indexed": 10
    },
    "component_counts": {
      "unindexed": 10,
      "indexed": 10
    }
  },
  "query_definitions": [
    {
      "name": "full_text_machine_check",
      "sql": "SELECT COUNT(*) AS n FROM bgl_logs_idx i JOIN bgl_logs l ON l.line_id=i.line_id WHERE i.message @@ ts_phrase(%s) AND l.event_time >= %s::TIMESTAMP AND l.event_time < %s::TIMESTAMP",
      "parameters": [
        "machine check",
        "2005-06-03T00:00:00",
        "2005-06-17T00:00:00"
      ]
    },
    {
      "name": "nearby_context",
      "sql": "SELECT line_id,event_time,node_id,component,severity FROM bgl_logs WHERE line_id BETWEEN %s AND %s AND event_time >= %s::TIMESTAMP AND event_time < %s::TIMESTAMP ORDER BY line_id",
      "parameters": [
        0,
        11,
        "2005-06-03T00:00:00",
        "2005-06-17T00:00:00"
      ]
    },
    {
      "name": "component_counts",
      "sql": "SELECT component,COUNT(*) AS n FROM bgl_logs WHERE event_time >= %s::TIMESTAMP AND event_time < %s::TIMESTAMP GROUP BY component ORDER BY n DESC",
      "parameters": [
        "2005-06-03T00:00:00",
        "2005-06-17T00:00:00"
      ]
    }
  ],
  "index_name": "ws_bgl_logs_20050603000000_20050617000000",
  "index_creation_time_ms": 253.402,
  "cleanup_time_ms": 24.244999999999997,
  "setup_only_formula": "ceil(creation_ms / max(avg_unindexed_ms - avg_indexed_ms, 0))",
  "lifecycle_formula": "ceil((creation_ms + cleanup_ms) / saving_ms)",
  "results": {
    "full_text_machine_check": {
      "avg_unindexed_query_ms": 48.429,
      "median_unindexed_query_ms": 49.010999999999996,
      "avg_indexed_query_ms": 7.4639,
      "median_indexed_query_ms": 7.514,
      "time_saved_per_query_ms": 40.9651,
      "speedup_factor": 6.48843098112247,
      "setup_only_breakeven_query_count": 7,
      "lifecycle_breakeven_query_count": 7,
      "raw_ms": {
        "unindexed": [
          53.893,
          60.132,
          38.313,
          47.445,
          48.771,
          49.455,
          49.251,
          53.947,
          36.551,
          46.532
        ],
        "indexed": [
          7.432,
          7.675,
          8.478,
          7.596,
          7.929,
          7.82,
          6.91,
          6.495,
          7.245,
          7.059
        ]
      }
    },
    "nearby_context": {
      "avg_unindexed_query_ms": 4.4632,
      "median_unindexed_query_ms": 4.473,
      "avg_indexed_query_ms": 4.0464,
      "median_indexed_query_ms": 3.9205,
      "time_saved_per_query_ms": 0.4167999999999994,
      "speedup_factor": 1.1030051403716883,
      "setup_only_breakeven_query_count": 608,
      "lifecycle_breakeven_query_count": 667,
      "raw_ms": {
        "unindexed": [
          5.978,
          4.704,
          4.794,
          4.407,
          4.539,
          6.285,
          3.203,
          3.314,
          3.755,
          3.653
        ],
        "indexed": [
          4.205,
          4.04,
          3.801,
          4.3,
          4.728,
          5.739,
          3.708,
          3.295,
          3.249,
          3.399
        ]
      }
    },
    "component_counts": {
      "avg_unindexed_query_ms": 5.2652,
      "median_unindexed_query_ms": 5.170999999999999,
      "avg_indexed_query_ms": 4.8041,
      "median_indexed_query_ms": 4.9725,
      "time_saved_per_query_ms": 0.46110000000000007,
      "speedup_factor": 1.0959805166420349,
      "setup_only_breakeven_query_count": 550,
      "lifecycle_breakeven_query_count": 603,
      "raw_ms": {
        "unindexed": [
          6.622,
          5.407,
          4.991,
          5.031,
          5.394,
          6.756,
          5.311,
          4.562,
          4.309,
          4.269
        ],
        "indexed": [
          4.942,
          5.068,
          5.003,
          5.151,
          5.098,
          6.142,
          4.083,
          4.029,
          4.176,
          4.349
        ]
      }
    }
  },
  "errors": []
}
```

## Benchmark run 2026-10-17T19:17:15.768941+00:00

Status: **success**

```json
{
  "status": "success",
  "timestamp": "2026-10-17T19:17:15.768941+00:00",
  "live_database": true,
  "serenedb_version": "PostgreSQL 18.3 (SereneDB 26.10.1)",
  "dataset": "BGL",
  "corpus_scope": [
    {
      "dataset": "BGL",
      "table": "bgl_logs",
      "total_rows": 4747963,
      "first_event": "2005-06-03T15:42:50.363779",
      "last_event": "2006-01-04T08:00:05.233639",
      "span_days": 214.7,
      "distinct_components": 14,
      "labelled_anomaly_lines": 348460,
      "severity_distribution": {
        "INFO": 3735813,
        "FATAL": 855195,
        "ERROR": 112355,
        "WARNING": 23357,
        "SEVERE": 19213,
        "FAILURE": 1714,
        "Kill": 306,
        "single": 4,
        "microseconds": 4,
        "0x00544eb8,": 2
      }
    },
    {
      "dataset": "OpenStack",
      "table": "openstack_logs",
      "total_rows": 207820,
      "first_event": "2017-05-14T19:39:01.445000",
      "last_event": "2017-05-17T12:02:35.320000",
      "span_days": 2.7,
      "distinct_components": 3,
      "labelled_anomaly_lines": 100,
      "severity_distribution": {
        "INFO": 204506,
        "WARNING": 3118,
        "ERROR": 194,
        "CRITICAL": 2
      }
    }
  ],
  "anchor_line_id_for_nearby_context": 1,
  "window": [
    "2005-06-03T00:00:00",
    "2005-06-17T00:00:00"
  ],
  "query_runs_per_arm": 10,
  "cache_conditions": "Sequential execution; no cache flush was requested. First samples may be cold; later samples are warm.",
  "raw_sample_count_per_query_arm": {
    "full_text_machine_check": {
      "unindexed": 10,
      "indexed": 10
    },
    "nearby_context": {
      "unindexed": 10,
      "indexed": 10
    },
    "component_counts": {
      "unindexed": 10,
      "indexed": 10
    }
  },
  "query_definitions": [
    {
      "name": "full_text_machine_check",
      "sql": "SELECT COUNT(*) AS n FROM bgl_logs_idx i JOIN bgl_logs l ON l.line_id=i.line_id WHERE i.message @@ ts_phrase(%s) AND l.event_time >= %s::TIMESTAMP AND l.event_time < %s::TIMESTAMP",
      "parameters": [
        "machine check",
        "2005-06-03T00:00:00",
        "2005-06-17T00:00:00"
      ]
    },
    {
      "name": "nearby_context",
      "sql": "SELECT line_id,event_time,node_id,component,severity FROM bgl_logs WHERE line_id BETWEEN %s AND %s AND event_time >= %s::TIMESTAMP AND event_time < %s::TIMESTAMP ORDER BY line_id",
      "parameters": [
        0,
        11,
        "2005-06-03T00:00:00",
        "2005-06-17T00:00:00"
      ]
    },
    {
      "name": "component_counts",
      "sql": "SELECT component,COUNT(*) AS n FROM bgl_logs WHERE event_time >= %s::TIMESTAMP AND event_time < %s::TIMESTAMP GROUP BY component ORDER BY n DESC",
      "parameters": [
        "2005-06-03T00:00:00",
        "2005-06-17T00:00:00"
      ]
    }
  ],
  "index_name": "ws_bgl_logs_20050603000000_20050617000000",
  "index_creation_time_ms": 235.726,
  "cleanup_time_ms": 39.105000000000004,
  "setup_only_formula": "ceil(creation_ms / max(avg_unindexed_ms - avg_indexed_ms, 0))",
  "lifecycle_formula": "ceil((creation_ms + cleanup_ms) / saving_ms)",
  "results": {
    "full_text_machine_check": {
      "avg_unindexed_query_ms": 49.6124,
      "median_unindexed_query_ms": 48.081500000000005,
      "avg_indexed_query_ms": 7.5532,
      "median_indexed_query_ms": 7.132,
      "time_saved_per_query_ms": 42.059200000000004,
      "speedup_factor": 6.568394852512842,
      "setup_only_breakeven_query_count": 6,
      "lifecycle_breakeven_query_count": 7,
      "break_even_unavailable_reason": null,
      "raw_ms": {
        "unindexed": [
          60.9,
          54.384,
          56.801,
          47.773,
          46.852,
          48.753,
          41.008,
          48.39,
          45.219,
          46.044
        ],
        "indexed": [
          8.698,
          7.7,
          7.574,
          7.113,
          6.972,
          10.283,
          6.684,
          6.693,
          6.664,
          7.151
        ]
      }
    },
    "nearby_context": {
      "avg_unindexed_query_ms": 4.7298,
      "median_unindexed_query_ms": 4.5184999999999995,
      "avg_indexed_query_ms": 4.2778,
      "median_indexed_query_ms": 4.2925,
      "time_saved_per_query_ms": 0.45199999999999996,
      "speedup_factor": 1.1056617887699285,
      "setup_only_breakeven_query_count": null,
      "lifecycle_breakeven_query_count": null,
      "break_even_unavailable_reason": "The partial full-text index does not target this query; timing differences are not treated as index savings.",
      "raw_ms": {
        "unindexed": [
          4.561,
          4.083,
          4.688,
          4.63,
          4.476,
          6.147,
          3.731,
          3.947,
          6.871,
          4.164
        ],
        "indexed": [
          5.274,
          4.748,
          4.366,
          4.67,
          4.219,
          5.565,
          3.515,
          3.515,
          3.56,
          3.346
        ]
      }
    },
    "component_counts": {
      "avg_unindexed_query_ms": 5.3546,
      "median_unindexed_query_ms": 5.2515,
      "avg_indexed_query_ms": 5.1483,
      "median_indexed_query_ms": 5.140499999999999,
      "time_saved_per_query_ms": 0.2062999999999997,
      "speedup_factor": 1.0400714799059882,
      "setup_only_breakeven_query_count": null,
      "lifecycle_breakeven_query_count": null,
      "break_even_unavailable_reason": "The partial full-text index does not target this query; timing differences are not treated as index savings.",
      "raw_ms": {
        "unindexed": [
          6.205,
          5.429,
          5.917,
          5.188,
          5.315,
          6.968,
          4.712,
          4.372,
          4.924,
          4.516
        ],
        "indexed": [
          5.445,
          5.812,
          5.116,
          5.318,
          5.165,
          7.21,
          4.487,
          4.289,
          4.39,
          4.251
        ]
      }
    }
  },
  "errors": []
}
```

## OTel benchmark run 2026-10-17T19:17:21.974143+00:00

```json
{
  "status": "success",
  "partitions": [
    "https://public-pme.s3.eu-west-3.amazonaws.com/text_bench/part_000.parquet"
  ],
  "filters": {
    "service": null,
    "severity": null
  },
  "query_windows": [
    {
      "source": "https://public-pme.s3.eu-west-3.amazonaws.com/text_bench/part_000.parquet",
      "part": 0,
      "window_start": "2025-09-23 00:00:00+00",
      "window_end": "2025-09-23 00:00:30+00",
      "additional_filters": {}
    }
  ],
  "distribution": [
    {
      "service_name": "cart",
      "severity": "info",
      "log_lines": 4778335
    },
    {
      "service_name": "frontend",
      "severity": "info",
      "log_lines": 3498086
    },
    {
      "service_name": "cart",
      "severity": "information",
      "log_lines": 2312217
    },
    {
      "service_name": "frontend",
      "severity": "error",
      "log_lines": 2141241
    },
    {
      "service_name": "email",
      "severity": "info",
      "log_lines": 1128683
    },
    {
      "service_name": "shipping",
      "severity": "info",
      "log_lines": 448799
    },
    {
      "service_name": "recommendation",
      "severity": "info",
      "log_lines": 420363
    },
    {
      "service_name": "recommendation",
      "severity": "trace",
      "log_lines": 419927
    },
    {
      "service_name": "quote",
      "severity": "info",
      "log_lines": 417043
    },
    {
      "service_name": "checkout",
      "severity": "info",
      "log_lines": 416537
    },
    {
      "service_name": "payment",
      "severity": "info",
      "log_lines": 329828
    },
    {
      "service_name": "frontend",
      "severity": "trace",
      "log_lines": 277158
    },
    {
      "service_name": "currency",
      "severity": "info",
      "log_lines": 256793
    },
    {
      "service_name": "payment",
      "severity": "error",
      "log_lines": 231212
    },
    {
      "service_name": "email",
      "severity": "trace",
      "log_lines": 216964
    },
    {
      "service_name": "email",
      "severity": "error",
      "log_lines": 39575
    },
    {
      "service_name": "payment",
      "severity": "trace",
      "log_lines": 20607
    },
    {
      "service_name": "kafka",
      "severity": "info",
      "log_lines": 19669
    },
    {
      "service_name": "checkout",
      "severity": "warn",
      "log_lines": 19530
    },
    {
      "service_name": "fraud-detection",
      "severity": "trace",
      "log_lines": 12398
    },
    {
      "service_name": "fraud-detection",
      "severity": "info",
      "log_lines": 12248
    },
    {
      "service_name": "",
      "severity": "info",
      "log_lines": 11339
    },
    {
      "service_name": "accounting",
      "severity": "info",
      "log_lines": 11302
    },
    {
      "service_name": "accounting",
      "severity": "information",
      "log_lines": 5696
    },
    {
      "service_name": "ad",
      "severity": "info",
      "log_lines": 4598
    },
    {
      "service_name": "ad",
      "severity": "trace",
      "log_lines": 4228
    },
    {
      "service_name": "currency",
      "severity": "error",
      "log_lines": 3582
    },
    {
      "service_name": "product-catalog",
      "severity": "info",
      "log_lines": 3110
    },
    {
      "service_name": "valkey-cart",
      "severity": "info",
      "log_lines": 448
    },
    {
      "service_name": "",
      "severity": "fatal",
      "log_lines": 325
    },
    {
      "service_name": "checkout",
      "severity": "error",
      "log_lines": 320
    },
    {
      "service_name": "quote",
      "severity": "error",
      "log_lines": 310
    },
    {
      "service_name": "recommendation",
      "severity": "warn",
      "log_lines": 239
    },
    {
      "service_name": "quote",
      "severity": "trace",
      "log_lines": 200
    },
    {
      "service_name": "accounting",
      "severity": "error",
      "log_lines": 99
    },
    {
      "service_name": "cart",
      "severity": "trace",
      "log_lines": 84
    },
    {
      "service_name": "",
      "severity": "error",
      "log_lines": 65
    },
    {
      "service_name": "shipping",
      "severity": "trace",
      "log_lines": 49
    },
    {
      "service_name": "product-catalog",
      "severity": "trace",
      "log_lines": 46
    },
    {
      "service_name": "cart",
      "severity": "error",
      "log_lines": 42
    },
    {
      "service_name": "checkout",
      "severity": "trace",
      "log_lines": 23
    },
    {
      "service_name": "ad",
      "severity": "warn",
      "log_lines": 20
    },
    {
      "service_name": "ad",
      "severity": "error",
      "log_lines": 16
    },
    {
      "service_name": "load-generator",
      "severity": "warn",
      "log_lines": 13
    },
    {
      "service_name": "",
      "severity": "warn",
      "log_lines": 13
    },
    {
      "service_name": "kafka",
      "severity": "warn",
      "log_lines": 10
    },
    {
      "service_name": "kafka",
      "severity": "error",
      "log_lines": 10
    },
    {
      "service_name": "accounting",
      "severity": "trace",
      "log_lines": 9
    },
    {
      "service_name": "fraud-detection",
      "severity": "warn",
      "log_lines": 5
    },
    {
      "service_name": "product-catalog",
      "severity": "error",
      "log_lines": 3
    },
    {
      "service_name": "fraud-detection",
      "severity": "error",
      "log_lines": 1
    }
  ],
  "total_rows_queried": 17463418,
  "matching_rows_in_selected_window": 17463418,
  "total_source_rows_from_footer": 1000000000,
  "partition_pruning_metadata": [
    {
      "partition": 0,
      "rows_in_file_from_footer": 1000000000,
      "rows_in_read_row_groups_from_footer": 17866752,
      "row_groups_in_file": 1624,
      "row_groups_read": 29,
      "column_bytes_read_estimate": 44158833
    }
  ],
  "elapsed_ms": 2643.0,
  "rows_scanned": null,
  "rows_scanned_reason": "Engine scan counters unavailable.",
  "estimated_cloud_scan_cost_usd": null,
  "estimated_cloud_scan_cost_reason": "No configured unit price and reliable scanned-byte telemetry.",
  "completed": true,
  "client_elapsed_ms": 2643.0,
  "timestamp": "2026-10-17T19:17:21.974143+00:00",
  "serenedb_version": "PostgreSQL 18.3 (SereneDB 26.10.1)",
  "role_actionability_smoke": {
    "engineer": {
      "status": "measured_timeline",
      "trace_id": "0ce994d55ca67ef4890f49805d0f379a",
      "spans_returned": 3,
      "evidence_id": "E-5378860d2ffe"
    },
    "team_lead": {
      "status": "measured_summary",
      "error_groups": [
        {
          "service_name": "frontend",
          "severity": "error",
          "log_lines": 2141241
        },
        {
          "service_name": "payment",
          "severity": "error",
          "log_lines": 231212
        },
        {
          "service_name": "email",
          "severity": "error",
          "log_lines": 39575
        },
        {
          "service_name": "currency",
          "severity": "error",
          "log_lines": 3582
        },
        {
          "service_name": "",
          "severity": "fatal",
          "log_lines": 325
        }
      ]
    },
    "manager": {
      "status": "measured_aggregate",
      "error_log_lines": 2416801,
      "affected_services": 13
    }
  }
}
```

## OTel benchmark run 2026-10-17T19:19:44.576776+00:00

```json
{
  "status": "success",
  "partitions": [
    "https://public-pme.s3.eu-west-3.amazonaws.com/text_bench/part_000.parquet"
  ],
  "filters": {
    "service": null,
    "severity": null
  },
  "query_windows": [
    {
      "source": "https://public-pme.s3.eu-west-3.amazonaws.com/text_bench/part_000.parquet",
      "part": 0,
      "window_start": "2025-09-23 00:00:00+00",
      "window_end": "2025-09-23 00:00:30+00",
      "additional_filters": {}
    }
  ],
  "distribution": [
    {
      "service_name": "cart",
      "severity": "info",
      "log_lines": 4778335
    },
    {
      "service_name": "frontend",
      "severity": "info",
      "log_lines": 3498086
    },
    {
      "service_name": "cart",
      "severity": "information",
      "log_lines": 2312217
    },
    {
      "service_name": "frontend",
      "severity": "error",
      "log_lines": 2141241
    },
    {
      "service_name": "email",
      "severity": "info",
      "log_lines": 1128683
    },
    {
      "service_name": "shipping",
      "severity": "info",
      "log_lines": 448799
    },
    {
      "service_name": "recommendation",
      "severity": "info",
      "log_lines": 420363
    },
    {
      "service_name": "recommendation",
      "severity": "trace",
      "log_lines": 419927
    },
    {
      "service_name": "quote",
      "severity": "info",
      "log_lines": 417043
    },
    {
      "service_name": "checkout",
      "severity": "info",
      "log_lines": 416537
    },
    {
      "service_name": "payment",
      "severity": "info",
      "log_lines": 329828
    },
    {
      "service_name": "frontend",
      "severity": "trace",
      "log_lines": 277158
    },
    {
      "service_name": "currency",
      "severity": "info",
      "log_lines": 256793
    },
    {
      "service_name": "payment",
      "severity": "error",
      "log_lines": 231212
    },
    {
      "service_name": "email",
      "severity": "trace",
      "log_lines": 216964
    },
    {
      "service_name": "email",
      "severity": "error",
      "log_lines": 39575
    },
    {
      "service_name": "payment",
      "severity": "trace",
      "log_lines": 20607
    },
    {
      "service_name": "kafka",
      "severity": "info",
      "log_lines": 19669
    },
    {
      "service_name": "checkout",
      "severity": "warn",
      "log_lines": 19530
    },
    {
      "service_name": "fraud-detection",
      "severity": "trace",
      "log_lines": 12398
    },
    {
      "service_name": "fraud-detection",
      "severity": "info",
      "log_lines": 12248
    },
    {
      "service_name": "",
      "severity": "info",
      "log_lines": 11339
    },
    {
      "service_name": "accounting",
      "severity": "info",
      "log_lines": 11302
    },
    {
      "service_name": "accounting",
      "severity": "information",
      "log_lines": 5696
    },
    {
      "service_name": "ad",
      "severity": "info",
      "log_lines": 4598
    },
    {
      "service_name": "ad",
      "severity": "trace",
      "log_lines": 4228
    },
    {
      "service_name": "currency",
      "severity": "error",
      "log_lines": 3582
    },
    {
      "service_name": "product-catalog",
      "severity": "info",
      "log_lines": 3110
    },
    {
      "service_name": "valkey-cart",
      "severity": "info",
      "log_lines": 448
    },
    {
      "service_name": "",
      "severity": "fatal",
      "log_lines": 325
    },
    {
      "service_name": "checkout",
      "severity": "error",
      "log_lines": 320
    },
    {
      "service_name": "quote",
      "severity": "error",
      "log_lines": 310
    },
    {
      "service_name": "recommendation",
      "severity": "warn",
      "log_lines": 239
    },
    {
      "service_name": "quote",
      "severity": "trace",
      "log_lines": 200
    },
    {
      "service_name": "accounting",
      "severity": "error",
      "log_lines": 99
    },
    {
      "service_name": "cart",
      "severity": "trace",
      "log_lines": 84
    },
    {
      "service_name": "",
      "severity": "error",
      "log_lines": 65
    },
    {
      "service_name": "shipping",
      "severity": "trace",
      "log_lines": 49
    },
    {
      "service_name": "product-catalog",
      "severity": "trace",
      "log_lines": 46
    },
    {
      "service_name": "cart",
      "severity": "error",
      "log_lines": 42
    },
    {
      "service_name": "checkout",
      "severity": "trace",
      "log_lines": 23
    },
    {
      "service_name": "ad",
      "severity": "warn",
      "log_lines": 20
    },
    {
      "service_name": "ad",
      "severity": "error",
      "log_lines": 16
    },
    {
      "service_name": "load-generator",
      "severity": "warn",
      "log_lines": 13
    },
    {
      "service_name": "",
      "severity": "warn",
      "log_lines": 13
    },
    {
      "service_name": "kafka",
      "severity": "warn",
      "log_lines": 10
    },
    {
      "service_name": "kafka",
      "severity": "error",
      "log_lines": 10
    },
    {
      "service_name": "accounting",
      "severity": "trace",
      "log_lines": 9
    },
    {
      "service_name": "fraud-detection",
      "severity": "warn",
      "log_lines": 5
    },
    {
      "service_name": "product-catalog",
      "severity": "error",
      "log_lines": 3
    },
    {
      "service_name": "fraud-detection",
      "severity": "error",
      "log_lines": 1
    }
  ],
  "total_rows_queried": 17463418,
  "matching_rows_in_selected_window": 17463418,
  "total_source_rows_from_footer": 1000000000,
  "partition_pruning_metadata": [
    {
      "partition": 0,
      "rows_in_file_from_footer": 1000000000,
      "rows_in_read_row_groups_from_footer": 17866752,
      "row_groups_in_file": 1624,
      "row_groups_read": 29,
      "column_bytes_read_estimate": 44158833
    }
  ],
  "elapsed_ms": 2604.1,
  "rows_scanned": null,
  "rows_scanned_reason": "Engine scan counters unavailable.",
  "estimated_cloud_scan_cost_usd": null,
  "estimated_cloud_scan_cost_reason": "No configured unit price and reliable scanned-byte telemetry.",
  "completed": true,
  "scale_summary_elapsed_ms": 2604.1,
  "timestamp": "2026-10-17T19:19:44.576776+00:00",
  "serenedb_version": "PostgreSQL 18.3 (SereneDB 26.10.1)",
  "role_actionability_smoke": {
    "engineer": {
      "status": "measured_timeline",
      "trace_id": "0ce994d55ca67ef4890f49805d0f379a",
      "spans_returned": 3,
      "evidence_id": "E-07258cc2b368"
    },
    "team_lead": {
      "status": "measured_summary",
      "error_groups": [
        {
          "service_name": "frontend",
          "severity": "error",
          "log_lines": 2141241
        },
        {
          "service_name": "payment",
          "severity": "error",
          "log_lines": 231212
        },
        {
          "service_name": "email",
          "severity": "error",
          "log_lines": 39575
        },
        {
          "service_name": "currency",
          "severity": "error",
          "log_lines": 3582
        },
        {
          "service_name": "",
          "severity": "fatal",
          "log_lines": 325
        }
      ]
    },
    "manager": {
      "status": "measured_aggregate",
      "error_log_lines": 2416801,
      "affected_services": 13
    }
  },
  "benchmark_wall_time_ms": 5304.1
}
```

## Benchmark run 2026-10-17T19:31:52.707870+00:00

Status: **success**

```json
{
  "status": "success",
  "timestamp": "2026-10-17T19:31:52.707870+00:00",
  "live_database": true,
  "serenedb_version": "PostgreSQL 18.3 (SereneDB 26.10.1)",
  "dataset": "BGL",
  "corpus_scope": [
    {
      "dataset": "BGL",
      "table": "bgl_logs",
      "total_rows": 4747963,
      "first_event": "2005-06-03T15:42:50.363779",
      "last_event": "2006-01-04T08:00:05.233639",
      "span_days": 214.7,
      "distinct_components": 14,
      "labelled_anomaly_lines": 348460,
      "severity_distribution": {
        "INFO": 3735813,
        "FATAL": 855195,
        "ERROR": 112355,
        "WARNING": 23357,
        "SEVERE": 19213,
        "FAILURE": 1714,
        "Kill": 306,
        "single": 4,
        "microseconds": 4,
        "0x00544eb8,": 2
      }
    },
    {
      "dataset": "OpenStack",
      "table": "openstack_logs",
      "total_rows": 207820,
      "first_event": "2017-05-14T19:39:01.445000",
      "last_event": "2017-05-17T12:02:35.320000",
      "span_days": 2.7,
      "distinct_components": 3,
      "labelled_anomaly_lines": 100,
      "severity_distribution": {
        "INFO": 204506,
        "WARNING": 3118,
        "ERROR": 194,
        "CRITICAL": 2
      }
    }
  ],
  "anchor_line_id_for_nearby_context": 1,
  "window": [
    "2005-06-03T00:00:00",
    "2005-06-17T00:00:00"
  ],
  "query_runs_per_arm": 10,
  "context_compression": {
    "status": "measured",
    "rows_returned": 50,
    "rows_scanned": null,
    "rows_scanned_reason": "Engine scan counters are not exposed.",
    "message_chars_truncated": 0,
    "deduplication": {
      "enabled": true,
      "runs_folded": 0,
      "input_rows": 50,
      "output_rows": 50
    },
    "compression_ratio": null,
    "compression_ratio_unavailable_reason": "Reliable rows_scanned is unavailable."
  },
  "rls_verification": {
    "status": "unavailable",
    "role_dsns_configured": {
      "engineer": false,
      "team_lead": false,
      "manager": false
    },
    "reason": "Actual Manager raw-data denial requires pytest -q -m serenedb with SERENEDB_MANAGER_DSN."
  },
  "per_role_model_usage": {
    "status": "unavailable",
    "reason": "Per-role model calls were not included in this database benchmark."
  },
  "cache_conditions": "Sequential execution; no cache flush was requested. First samples may be cold; later samples are warm.",
  "raw_sample_count_per_query_arm": {
    "full_text_machine_check": {
      "unindexed": 10,
      "indexed": 10
    },
    "nearby_context": {
      "unindexed": 10,
      "indexed": 10
    },
    "component_counts": {
      "unindexed": 10,
      "indexed": 10
    }
  },
  "query_definitions": [
    {
      "name": "full_text_machine_check",
      "sql": "SELECT COUNT(*) AS n FROM bgl_logs_idx i JOIN bgl_logs l ON l.line_id=i.line_id WHERE i.message @@ ts_phrase(%s) AND l.event_time >= %s::TIMESTAMP AND l.event_time < %s::TIMESTAMP",
      "parameters": [
        "machine check",
        "2005-06-03T00:00:00",
        "2005-06-17T00:00:00"
      ]
    },
    {
      "name": "nearby_context",
      "sql": "SELECT line_id,event_time,node_id,component,severity FROM bgl_logs WHERE line_id BETWEEN %s AND %s AND event_time >= %s::TIMESTAMP AND event_time < %s::TIMESTAMP ORDER BY line_id",
      "parameters": [
        0,
        11,
        "2005-06-03T00:00:00",
        "2005-06-17T00:00:00"
      ]
    },
    {
      "name": "component_counts",
      "sql": "SELECT component,COUNT(*) AS n FROM bgl_logs WHERE event_time >= %s::TIMESTAMP AND event_time < %s::TIMESTAMP GROUP BY component ORDER BY n DESC",
      "parameters": [
        "2005-06-03T00:00:00",
        "2005-06-17T00:00:00"
      ]
    }
  ],
  "index_name": "ws_bgl_logs_20050603000000_20050617000000",
  "index_creation_time_ms": 275.65700000000004,
  "cleanup_time_ms": 38.468,
  "setup_only_formula": "ceil(creation_ms / max(avg_unindexed_ms - avg_indexed_ms, 0))",
  "lifecycle_formula": "ceil((creation_ms + cleanup_ms) / saving_ms)",
  "results": {
    "full_text_machine_check": {
      "avg_unindexed_query_ms": 45.0235,
      "median_unindexed_query_ms": 43.1375,
      "avg_indexed_query_ms": 8.3738,
      "median_indexed_query_ms": 8.332,
      "time_saved_per_query_ms": 36.649699999999996,
      "speedup_factor": 5.376710692875397,
      "setup_only_breakeven_query_count": 8,
      "lifecycle_breakeven_query_count": 9,
      "break_even_unavailable_reason": null,
      "raw_ms": {
        "unindexed": [
          52.651,
          47.628,
          41.163,
          43.102,
          45.342,
          43.173,
          42.595,
          43.032,
          42.765,
          48.784
        ],
        "indexed": [
          9.764,
          8.522,
          7.791,
          9.001,
          8.629,
          10.5,
          6.826,
          7.341,
          8.142,
          7.222
        ]
      }
    },
    "nearby_context": {
      "avg_unindexed_query_ms": 4.7039,
      "median_unindexed_query_ms": 4.7195,
      "avg_indexed_query_ms": 4.5225,
      "median_indexed_query_ms": 4.7805,
      "time_saved_per_query_ms": 0.1814,
      "speedup_factor": 1.0401105583195136,
      "setup_only_breakeven_query_count": null,
      "lifecycle_breakeven_query_count": null,
      "break_even_unavailable_reason": "The partial full-text index does not target this query; timing differences are not treated as index savings.",
      "raw_ms": {
        "unindexed": [
          4.976,
          5.013,
          6.143,
          4.892,
          4.547,
          5.861,
          3.612,
          3.664,
          4.039,
          4.292
        ],
        "indexed": [
          4.777,
          4.831,
          4.805,
          4.784,
          5.494,
          5.676,
          3.614,
          4.224,
          3.254,
          3.766
        ]
      }
    },
    "component_counts": {
      "avg_unindexed_query_ms": 5.6982,
      "median_unindexed_query_ms": 5.759,
      "avg_indexed_query_ms": 5.4549,
      "median_indexed_query_ms": 5.4254999999999995,
      "time_saved_per_query_ms": 0.24329999999999963,
      "speedup_factor": 1.0446021008634438,
      "setup_only_breakeven_query_count": null,
      "lifecycle_breakeven_query_count": null,
      "break_even_unavailable_reason": "The partial full-text index does not target this query; timing differences are not treated as index savings.",
      "raw_ms": {
        "unindexed": [
          6.202,
          5.666,
          6.102,
          5.478,
          6.012,
          7.072,
          5.852,
          4.92,
          4.727,
          4.951
        ],
        "indexed": [
          5.844,
          5.523,
          5.49,
          5.667,
          5.152,
          6.749,
          5.039,
          4.603,
          5.121,
          5.361
        ]
      }
    }
  },
  "errors": []
}
```

## Benchmark run 2026-10-17T19:31:58.627464+00:00

Status: **skipped**

```json
{
  "status": "skipped",
  "reason": "Live indexing benchmark requires --live.",
  "timestamp": "2026-10-17T19:31:58.627464+00:00"
}
```

## OTel benchmark run 2026-10-17T19:31:58.827803+00:00

```json
{
  "status": "skipped",
  "reason": "Remote scan requires --live.",
  "partitions_requested": 1,
  "timestamp": "2026-10-17T19:31:58.827803+00:00"
}
```
