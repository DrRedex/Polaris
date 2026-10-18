# Making Sense of Logs: The Right Insights for the Right People

National AI Hackathon 2026 (SchubKI) · SereneDB challenge

> **Evidence status:** The latest verified local and remote runs are recorded below. Older performance figures elsewhere in this document are historical repository claims and were not re-measured in this run. See [docs/PERFORMANCE_AND_AWARDS.md](PERFORMANCE_AND_AWARDS.md) for older timestamped runs and explicit unavailable/skipped states.

One AI agent, one log corpus, three audiences. An **Engineer** asks what failed and what happened around it, a **Team Lead** asks which problems recur and who owns them, and a **Manager** asks what deserves attention first. Each gets a different investigation, different evidence and different recommendations, not just a shorter or longer answer.

Millions of log lines don't fit in an LLM context window, so the work is split:

- **SereneDB** does all searching, counting, percentiles and trend aggregation over the full 4.96M-line corpus (most tool calls take 30–350 ms; the slowest benchmarked is ~860 ms).
- **The agent** receives only compact evidence envelopes. Each one carries the exact SQL, the bound parameters, the data scope, exact whole-corpus counts and truncation flags. Every number in an answer can therefore be traced to a query that actually ran.

```
 Loghub BGL (4,747,963 lines)    Loghub OpenStack (207,820 lines)    S3 OTel Parquet (50B rows)
            │ scripts/prepare_logs.py ──► CSV                                │ HTTP range reads
            ▼                                                                ▼
 ┌──────────────────────────────── SereneDB 26.10 (Docker) ─────────────────────────────────┐
 │ bgl_logs · openstack_logs (PK line_id) · team_ownership · inverted full-text indexes      │
 │ read_parquet() over S3, footer statistics, row-group pruning                              │
 └───────────────────────────────────────────┬──────────────────────────────────────────────┘
                                             │ fixed, parameterized SQL only (no LLM-written SQL)
 ┌──────────────────────── src/queries.py · src/extensions.py ───────────────────────────────┐
 │ EvidenceEnvelope: executed SQL + params + timing + scope + exact totals + truncation      │
 └───────────────────────────────────────────┬──────────────────────────────────────────────┘
                                             │
 ┌──────────────── MCP server (src/mcp_server.py, stdio / SSE / streamable HTTP) ────────────┐
 │ 12 typed, bounded tools                                                                    │
 └───────────────┬───────────────────────────────────────────────┬──────────────────────────┘
                 │                                               │
     Claude Code / Claude Desktop                 OpenAI agent (src/agent.py) + grounding validator
                                                  ├── Streamlit 3-role UI (ui/streamlit_app.py)
                                                  ├── Rich CLI (ui/cli_demo.py)
                                                  └── Worked investigation (scripts/run_worked_investigation.py)
```

## Quick start (clean checkout)

Requires Docker Desktop, [uv](https://docs.astral.sh/uv/) and Git Bash on Windows (for the download script).

```bash
git clone <repo-url> making-sense-of-logs && cd making-sense-of-logs
cp .env.example .env                      # then put your OpenAI key in .env (optional, see below)

# 1. Dependencies + SereneDB
uv sync
docker compose up -d serenedb

# 2. Data: full corpora (~770 MB download, ~3 min parse, ~30 s load)
bash scripts/download_corpus.sh           # or: --sample for the 2,000-line samples
uv run python -I scripts/prepare_logs.py
uv run python scripts/load_corpus.py

# 3. Use it
uv run streamlit run ui/streamlit_app.py           # three-role web UI on http://localhost:8501
uv run uvicorn src.api.main:app --host 0.0.0.0 --port 8000 # Polaris FastAPI for the Next.js dashboard
uv run python ui/cli_demo.py --role all            # terminal demo
uv run python scripts/run_worked_investigation.py  # regenerates docs/worked_investigation.md
```

The Polaris dashboard in `frontend/` uses `POST /api/investigate` and `POST /api/reports/pdf` on this API. It defaults to `http://localhost:8000`; set `NEXT_PUBLIC_POLARIS_API_URL` in `frontend/.env.local` to change the API origin. The API checks SereneDB connectivity on startup and uses `OPENAI_API_KEY` (and optionally `OPENAI_MODEL`) for SQL generation and structured role responses. The configured role DSNs (`SERENEDB_ENGINEER_DSN`, `SERENEDB_TEAM_LEAD_DSN`, and `SERENEDB_MANAGER_DSN`) must be present for their respective role investigations.

With `make`, these steps are `make setup`, `make ingest`, `make ui`, `make cli` and `make demo`.

**Without an OpenAI key**, everything except the LLM answer still works. The UI's *Show evidence only* button, `cli_demo.py --evidence-only` and `run_worked_investigation.py --evidence-only` run each role's standard tool sequence and show the SQL evidence.

### Connect Claude Code or Claude Desktop (no OpenAI key needed)

```bash
claude mcp add making-sense-of-logs -- uv --directory /path/to/making-sense-of-logs run python -m src.mcp_server
```

Claude Desktop (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "making-sense-of-logs": {
      "command": "uv",
      "args": ["--directory", "/path/to/making-sense-of-logs", "run", "python", "-m", "src.mcp_server"]
    }
  }
}
```

The server's instructions tell the client the evidence rules and the per-role tool flows. Run `python -m src.mcp_server --transport streamable-http` to serve it over HTTP on port 8000.

**Codex CLI:** from the repository root, register the stdio server with `codex mcp add schubki-serenedb-logs -- uv run python -m src.mcp_server`. `.mcp.json` is a portable client-discovery file; Codex CLI stores its own MCP entries in the user's `config.toml`.

## Tools

| Tool | Roles | What it does | Bounds |
|---|---|---|---|
| `get_corpus_scope` | Manager | Rows, time span, severity distribution per dataset | – |
| `search_logs` | Engineer | Full-text search (`all_terms`, `any_terms`, `phrase` via the inverted index; `substring` scan) with time, component (case-insensitive) and severity filters; ISO times with an offset are converted to UTC. Returns the exact `total_matching_count` plus the earliest matches | `limit` 1–100 |
| `get_nearby_events` | Engineer | Events before and after an anchor line: on the same node (BGL) or instance/request (OpenStack) ordered by time, or physically adjacent lines | 1–50 each side, ≤7 days |
| `summarize_by_team_and_component` | Team Lead, Manager | Whole-corpus counts by team × component × severity: log lines, distinct nodes/instances, active days, first/last seen, **`burst_count_30m`** (lines <30 min apart = one burst, via `LAG`), **`top_node_share_pct`** (one faulty node vs widespread), **`current_period_bursts` vs `previous_period_bursts`, `growth_pct`** | ≤100 groups; summary covers all |
| `get_priority_ranking` | Manager | Deterministic 0–100 `priority_score` = weighted `PERCENT_RANK()`s of bursts (0.30), burst growth (0.15), breadth (0.20), active days (0.15) and severity (0.20), every factor returned; envelope lists `unknown_not_in_logs` | ≤100 groups |
| `get_latency_outliers` | Engineer, Team Lead | Per component × operation median + MAD over the whole corpus; returns calls above `max(median + k·MAD, median + min_excess_sec)` with `ratio_to_median`, request/instance IDs and `silent_info_level` | ≤100 rows |
| `get_recurring_trends` | Team Lead, Manager | Hour/day/week buckets per component, with per-component persistence, peak and pattern (burst / chronic / increasing / decreasing) | hourly ≤31 days; ≤150 rows |
| `get_openstack_latency_and_errors` | All | Per service or HTTP route: requests, 4xx/5xx, 5xx rate, ERROR/WARNING lines, anomalous instances, AVG/P50/P95/P99/MAX latency | ≤100 groups |
| `manage_workspace_index` | All (optional) | Agent-managed partial full-text index over one time window: create / benchmark / list / drop | ≤3 indexes |
| `resolve_semantic_schema` | All | Semantic KNN over a glossary of the real schema: confidence-scored columns, each table's time/id/join columns, the recommended parameterized tool, and a sandbox prompt for writing SQL. Runs no SQL | `k` 1–10 |
| `execute_guarded_readonly_sql` | All (fallback) | Runs one LLM-written `SELECT` after deterministic guardrails, as a SELECT-only role, returning `executed_sql`, `knn_schema_matches`, exact total and truncation | LIMIT ≤50, 30 s timeout |
| `query_remote_otel_parquet` | All (optional) | Zero-download SQL on the 50B-row OTel Parquet benchmark on S3: footer overview, service summary, Body search | window ≤30 s; 150 s timeout |

Tool arguments are typed and range-checked (Pydantic, enforced by the MCP layer), and every value is bound as a SQL parameter. The eight parameterized tools are the primary path; the LLM writes SQL only through the guarded fallback below.

### Semantic schema grounding and guarded SQL (fallback)

For questions none of the parameterized tools can express, a two-step pipeline lets the agent write its own query safely.

**Step 1: semantic grounding** (`src/semantic_layer.py`, tool `resolve_semantic_schema`).
- A glossary describes 25 real columns across `bgl_logs`, `openstack_logs` and `team_ownership`, each with a `recommended_mcp_tool`.
- The glossary is embedded once with `all-MiniLM-L6-v2`; the model is lazy-loaded, cached for the process, and warmed up in the background when the MCP server starts.
- Each question is matched by exact cosine-similarity KNN in NumPy (about 20 ms after warm-up). Every matched table's time, id and join columns are always added.
- Example: "Customers keep complaining the cloud API is sluggish and times out" matches `openstack_logs.latency_sec`, `http_route` and `http_status` (top confidence ~0.38), and recommends `get_openstack_latency_and_errors`. Most vague questions end here: the agent calls that tool rather than writing SQL.

**Step 2: deterministic guardrails** (`src/guarded_sql_agent.py`, tool `execute_guarded_readonly_sql`). The candidate SQL is checked before it reaches the database:
1. Markdown fences and comments are stripped, but string literals are preserved.
2. Internal `;` is rejected (one statement only).
3. Write and DDL keywords are rejected, but only outside string literals, so `message ILIKE '%delete%'` is allowed.
4. The query must start with `SELECT` or `WITH`.
5. The query is parsed with sqlglot, and only `bgl_logs`, `openstack_logs`, `team_ownership` and their `*_idx` full-text indexes may appear. Table functions are rejected: in SereneDB a plain `SELECT * FROM read_csv('/etc/passwd')` reads server files, which a keyword filter alone would allow. System catalogs, other schemas, file-path `FROM '…'` and CTEs that shadow a real table are rejected too.
6. A top-level `LIMIT` is added (default 50) or clamped (max 50).

The query then runs as `log_reader`, a SELECT-only database role created by `load_corpus.py`, with a 30 s timeout. The database itself therefore refuses writes even if a check were bypassed. The envelope adds an exact `total_matching_count` (a `COUNT(*)` over the query without its LIMIT) and `is_truncated`.

### Integrated pipeline, role policy, and performance reporting

The four-step entry point is `src.pipeline_orchestrator.run_full_intelligence_pipeline`. It requires an explicit SQL provider (`OPENAI_API_KEY`) or an injected candidate SQL string; it does not invent SQL when configuration is missing. Role-specific production deployments should provision database roles and views with `uv run python scripts/setup_roles.py`, configure the Engineer/Team Lead/Manager DSNs, and set `SERENEDB_APP_ROLE` for each MCP process. This policy uses database grants and aggregate views; native row-level policies are not claimed. Manager denial is only verified when the integration test has a real `SERENEDB_MANAGER_DSN`.

The root `.mcp.json` launches the server from the repository with `uv run python -m src.mcp_server`. Safe benchmark defaults record skipped live work; use `uv run python scripts/benchmark_indexing.py --live` for the local reversible workspace benchmark or `uv run python scripts/benchmark_huge_datasets.py --live --partitions 1` for one bounded remote OTel partition. Multi-partition scans additionally require `--confirm-multpartition`. `make benchmarks` runs the safe defaults and focused tests.

#### Latest verified benchmark run (2026-10-18)

Runs used SereneDB 26.10.1 (PostgreSQL 18.3 wire protocol) with the loaded BGL and OpenStack corpora. In PowerShell, both benchmark scripts were run with `--output $benchOutput`, where `$benchOutput = Join-Path $env:TEMP 'polaris-benchmark-run.md'`; this kept their JSON output outside the repository. Role DSNs were configured, but access-control denial was not tested; per-role model usage, exact engine rows scanned, and OTel cost were unavailable (no scan counters or `OTEL_SCAN_PRICE_USD_PER_GB`).

- **Local partial-index run** (`uv run python scripts/benchmark_indexing.py --live --output $benchOutput`): BGL, `2005-06-03T00:00:00` through `2005-06-17T00:00:00`, 10 sequential samples per arm for each of three queries, no cache flush. The `machine check` full-text count averaged **65.74 ms unindexed** and **22.93 ms indexed** (median 68.98 / 21.94 ms), a **2.87×** speed-up and **42.81 ms** saved per query. Index creation took **892 ms**, cleanup **73 ms**; estimated break-even is **21 queries** excluding cleanup or **23** including cleanup. Nearby-context and component-count queries do not use this full-text index and were slower in the indexed arm; those differences are not index savings. The 50-row context sample had no message truncation or deduplication.
- **Remote OTel run** (`uv run python scripts/benchmark_huge_datasets.py --live --partitions 1 --output $benchOutput`): one public 1B-row Parquet partition, one 30-second window on 2025-09-23. The query returned **17,463,418** matching rows, read **29 of 1,624** row groups, and estimated **44,158,833 selected-column bytes**. Scale summary elapsed time was **24.72 s**; the complete benchmark including the bounded trace smoke check took **149.99 s**. Engineer trace lookup returned **3 spans**; Team Lead summary and Manager aggregation completed (2,416,801 error/fatal lines across 13 services). These measurements cover one partition, not the entire 50B-row dataset.
- **Focused tests:** `uv run python -m pytest -q tests/test_pipeline_steps_1_to_4.py tests/test_semantic_and_guardrails.py tests/test_extensions.py` completed with **43 passed, 5 skipped** in 25.81 s. The equivalent `uv run pytest` entrypoint failed before test collection because its Windows launcher could not canonicalize the script path; invoking pytest as a Python module succeeded.

The Streamlit page has a *Semantic schema & guarded SQL* panel that shows the KNN confidence table, the sandbox prompt, an optional OpenAI-generated candidate, and the executed SQL.

### Evidence envelope

Every tool returns the same structure (abridged from a real call):

```json
{
  "tool_name": "search_logs",
  "data_scope": {"dataset": "BGL", "table": "bgl_logs", "total_corpus_rows": 4747963,
                 "time_filter_start": null, "time_filter_end": null,
                 "additional_filters": {"component": "KERNEL", "severity": "FATAL",
                                        "query_text": "data TLB error interrupt", "mode": "phrase"}},
  "executed_queries": [{"label": "phrase search with exact total count",
                        "sql": "SELECT l.line_id, ... , COUNT(*) OVER () AS total_matching_count FROM bgl_logs_idx i JOIN bgl_logs l ON l.line_id = i.line_id WHERE i.message @@ ts_phrase(%s) AND l.component = %s AND l.severity = %s ORDER BY l.event_time, l.line_id LIMIT %s",
                        "bound_parameters": ["data TLB error interrupt", "KERNEL", "FATAL", 25],
                        "execution_time_ms": 127.0, "result_rows": 25}],
  "total_matching_count": 152734,
  "returned_count": 25,
  "is_truncated": true,
  "truncation_notice": "Showing 25 of 152,734 matching log lines (earliest first). Use the summary tools for whole-corpus counts by component or time.",
  "summary": null,
  "notes": ["Sample rows show examples only; use total_matching_count or the aggregation tools for frequency."],
  "rows": ["..."]
}
```

## How the three roles differ

All roles share one agent (`src/agent.py`), the same tools and the same corpus. `src/prompts.py` changes the question, the preferred evidence and the shape of the recommendation. Every answer must contain **Observed facts** (each cited as `[tool: field=value]`), **Hypotheses**, **What the logs do not establish** and the role's recommended next steps.

| | Engineer | Team Lead | Manager |
|---|---|---|---|
| Core question | What failed, where, and what happened just before and after? | Which problems recur, how often, who owns them? | What deserves attention first, and what next? |
| Evidence | `search_logs` → `get_nearby_events` (same node), latency for OpenStack | `summarize_by_team_and_component` → `get_recurring_trends` → 5 representative lines | `get_corpus_scope` → summary (BOTH) → trends → service health |
| Output | Timeline, technical hypothesis, concrete diagnostic checks | Frequency/trend table, ownership, ranked team queue | Top-3 priority matrix, actions per team, scope disclaimer |

**Evidence IDs and claim verifier** (`src/claim_verifier.py`).
- Every tool result carries an `evidence_id`: `EV-01`, `EV-02`, … per question in the agent, per process in the MCP server.
- Prompts require every factual sentence, bullet or table row to cite its source, e.g. `… 626,450 lines [EV-02].`
- `verify_response_claims(text, ledger, role)` checks each number against **the evidence it cites**, not just any evidence, and reports a deterministic `verified_number_rate`. It accepts rounding, k/M/B and million/billion suffixes, and percentages stored as fractions.
- `(derived: …)` figures are exempt, and their arithmetic is evaluated with a restricted AST evaluator (no `eval`). Prose dates, list ordinals and table header labels are skipped, and a citation at the end of a bullet covers the whole bullet.
- For the Manager, sentences claiming $, revenue, financial, customer impact, SLA breach or downtime % are flagged. Sentences that state the data is absent, and bullets under headings like *What the logs do not establish*, are allowed. Failing answers get one revision round; anything still unsupported is stripped.
- Each role's required sections are checked too.
- Verification takes about 1–2 ms per answer, because each evidence item's numbers are indexed when the tool result arrives.

**Earlier grounding validator** (`src/validator.py`). After each answer, every number is checked against the numbers in the evidence the agent actually received. Rounding to the precision shown, `k/M/B` suffixes and percent-as-fraction are allowed, and `(derived: a/b = x%)` figures are exempt. For the Manager, sentences making revenue, cost, SLA, customer-impact or downtime claims are rejected, unless they state that such data is absent. On failure the agent gets one revision round with the specific problems listed. The UI shows the result of this check next to every answer.

## Worked investigation (measured on the full corpus)

`scripts/run_worked_investigation.py` writes the complete transcript with every SQL statement to [`docs/worked_investigation.md`](worked_investigation.md), and re-executes each statement to confirm it reproduces. These are the figures the tools return:

- **Engineer.** `search_logs("data TLB error interrupt", phrase, KERNEL, FATAL)` matches **152,734** lines (the BGL `KERNDTLB` alert category). `get_nearby_events` on a representative line shows the same-node sequence around it.
- **Team Lead.** `summarize_by_team_and_component(BOTH, errors_only)` gives **988,775** error/alert lines.
  - BGL `KERNEL` FATAL: **626,450** lines on **31,467** distinct nodes over **179** active days (Core Systems & OS Team).
  - `APP` FATAL: **228,536** lines on only **1,034** nodes (Compute Runtime Team).
  - `MMCS` ERROR: **88,529** lines with no node attribution, on **19** days.

  Weekly trends classify KERNEL as *chronic, dominated by one burst* (active in 100% of weeks; the week of 2005-06-13 holds 60.3%), APP as *increasing*, and MMCS as a *burst*.
- **Manager.** OpenStack `nova-compute` has **192** ERROR lines and all **100** labelled-anomaly lines (**4** anomalous instances). `nova-api` served **98,733** requests with **2** HTTP 5xx, P50 **0.26 s** and P99 **0.51 s**. Scope: 4,747,963 BGL lines over 214.7 days and 207,820 OpenStack lines over 2.7 days.

### Bursts, priority and outliers (historical full-corpus measurements)

- **KERNEL FATAL:** 626,450 lines are only **501 bursts**; the noisiest node holds 24.3% of the lines; bursts fell 17.2% in the second half of the span.
- **APP FATAL:** 228,536 lines spread over 1,034 nodes (top node 0.13%, so widespread), and bursts **grew 49.6%**.
- **`get_priority_ranking(BOTH)`:** KERNEL FATAL 91.8, APP FATAL 86.4, DISCOVERY SEVERE 77.1, DISCOVERY ERROR 69.2, OpenStack nova-compute ERROR 64.8.
- **`get_latency_outliers`:** finds **6 VM spawns** up to **50.7 s** against a 19.7 s median (MAD 0.42 s). All were logged at **INFO**, so no error-based query would surface them. The spawn, build, destroy and deallocate timings come from nova-compute `Took X seconds to …` lines, parsed into `operation` / `duration_sec`.
- **Latency:** the burst aggregation adds a window sort. The error summary takes ~1.0 s, a 1-week window 57 ms, and all 4.7M lines ~2.9 s. Priority ranking takes ~0.7 s and outliers ~0.18 s.

## Optional extensions

**Agent-managed workspace index.** `manage_workspace_index("benchmark", "BGL", "2005-06-03", "2005-06-17", "error")` builds a partial inverted index (`CREATE INDEX … USING inverted … WHERE event_time BETWEEN …`). Historical repository measurements (not re-run here) reported 482 ms creation and a 12× speed-up; the latest repeatable run and its configuration are reported above.

While the index exists, `search_logs` automatically uses it for full-text searches whose time window lies inside it (the envelope notes this). The OpenAI agent drops any index it created at the end of each question; index names are derived from the validated window, never supplied.

**50 billion rows without downloading them.** `query_remote_otel_parquet` reads the public OpenTelemetry benchmark (`part_000…part_049.parquet`, 1B rows each) in place:

| Action | Historical repository measurement (not re-run here) |
|---|---|
| `overview`, 1 file | 1,000,000,000 rows, 1,624 row groups, time range: **1.4 s** (footer only) |
| `overview`, 50 files | **50,000,000,000** rows, 81,209 row groups, a full day: **~115 s** (footers only, batched) |
| `service_summary`, 2 s window | reads **2 of 1,624** row groups (99.88% skipped), ~3 MB instead of 2.5 GB, 1.1M rows in **~1 s** |

The files are sorted by `Timestamp`, so SereneDB fetches only row groups whose min/max statistics overlap the window. The tool reports row groups and estimated bytes read versus the whole file.

## Tests

```bash
uv run pytest -q              # 81 offline tests: query layer on DuckDB, claim verifier, agent loop, bursts/priority/outliers, KNN, SQL guardrails
uv run pytest -q -m serenedb  # 18 live tests against the loaded SereneDB (incl. extensions, guarded SQL, read-only role)
```

The offline suite runs the repo's real `sql/` files and query templates on DuckDB, a near-identical dialect, against fixture logs. Expected values are computed directly from the fixture files. Full-text modes need SereneDB and are covered by the live suite.

## Design notes and known limitations

- **Index publish step.** After the bulk load, `sql/02` runs `VACUUM (REFRESH_TABLE)`, `VACUUM (COMPACT_TABLE)` and `VACUUM (RECOMPUTE_STATS_TABLE)` on both tables. All rows are then searchable, segments are merged and statistics are fresh before the first query. A bare `VACUUM;` is a documented no-op on SereneDB tables, so it isn't used.
- **Full-text search** uses SereneDB's `@@` operator, which only resolves against an inverted-index relation. `search_logs` therefore reads `FROM <table>_idx` and joins back to the table on `line_id` for time, component and severity filters.
- **Timestamps are bound as ISO text with `::TIMESTAMP`.** SereneDB 26.10.1 rejects binary-encoded timestamp parameters in queries that join an inverted index.
- **Remote Parquet is queried with `read_parquet('<url>')` directly.** A time filter on a view over it was not pushed down (it scanned the whole file).
- **BGL timestamps** are the cluster-local timestamps in the log (microsecond precision); the UTC epoch is kept in `epoch_ts`. BGL has no event templates in the raw log, so "patterns" are the labelled alert categories, components and messages.
- **OpenStack anomaly labels are per instance** (`anomaly_labels.txt` lists 4 instances with injected faults). A labelled line belongs to an affected instance; it is not necessarily an error line.
- **OpenStack latency** comes from the `time:` field of nova-api WSGI access lines, so the percentiles cover HTTP requests only.
- **Log lines are not incidents.** One noisy node can emit thousands of lines, so summaries always pair line counts with distinct nodes/instances and active days.
- **The ownership table is fictional** (`sql/03_team_ownership.sql`). Components without an owner show as `Unassigned`.
- **The logs contain no business data.** Revenue, SLA, customer impact and downtime are absent, which the Manager prompt and validator enforce.
- **The local SereneDB password** defaults to `serenedb`, and the port is bound to `127.0.0.1` only. Change `SERENEDB_PASSWORD` before the first `docker compose up` if the machine is shared.

## Repository layout

```
src/        db.py (connection + EvidenceEnvelope) · queries.py (core tools) · extensions.py (workspace index, remote Parquet)
            semantic_layer.py (KNN schema grounding) · guarded_sql_agent.py (sandbox prompt, SQL guardrails, read-only execution)
            mcp_server.py · agent.py (OpenAI loop) · prompts.py · validator.py · workflows.py (no-LLM tool sequences)
sql/        01 schema · 02 load + inverted indexes · 03 team ownership · 04 remote OTel views (optional)
scripts/    download_corpus.sh · prepare_logs.py · load_corpus.py · run_worked_investigation.py
ui/         streamlit_app.py · cli_demo.py
tests/      offline (DuckDB) and live (-m serenedb) suites, small Loghub fixtures
docs/       worked_investigation.md (generated)
```

## Data and citations

Log data from [Loghub](https://github.com/logpai/loghub) (Zenodo record 8196385). Please cite:

- Jieming Zhu, Shilin He, Pinjia He, Jinyang Liu, Michael R. Lyu. *Loghub: A Large Collection of System Log Datasets for AI-driven Log Analytics.* IEEE ISSRE, 2023.
- Adam Oliner, Jon Stearley. *What Supercomputers Say: A Study of Five System Logs.* IEEE/IFIP DSN, 2007. (BGL)
- Min Du, Feifei Li, Guineng Zheng, Vivek Srikumar. *DeepLog: Anomaly Detection and Diagnosis from System Logs through Deep Learning.* ACM CCS, 2017. (OpenStack)

The OpenTelemetry benchmark is the public SereneDB `text_bench` dataset on S3.
