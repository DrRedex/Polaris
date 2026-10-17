# Making Sense of Logs: The Right Insights for the Right People

National AI Hackathon 2026 (SchubKI) · SereneDB challenge

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
 │ 8 typed, bounded tools                                                                    │
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
uv run python ui/cli_demo.py --role all            # terminal demo
uv run python scripts/run_worked_investigation.py  # regenerates docs/worked_investigation.md
```

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

## Tools

| Tool | Roles | What it does | Bounds |
|---|---|---|---|
| `get_corpus_scope` | Manager | Rows, time span, severity distribution per dataset | – |
| `search_logs` | Engineer | Full-text search (`all_terms`, `any_terms`, `phrase` via the inverted index; `substring` scan) with time, component (case-insensitive) and severity filters; ISO times with an offset are converted to UTC. Returns the exact `total_matching_count` plus the earliest matches | `limit` 1–100 |
| `get_nearby_events` | Engineer | Events before and after an anchor line: on the same node (BGL) or instance/request (OpenStack) ordered by time, or physically adjacent lines | 1–50 each side, ≤7 days |
| `summarize_by_team_and_component` | Team Lead, Manager | Whole-corpus counts by team × component × severity: log lines, distinct nodes/requests/instances, active days, first/last seen | ≤100 groups; summary covers all |
| `get_recurring_trends` | Team Lead, Manager | Hour/day/week buckets per component, with per-component persistence, peak and pattern (burst / chronic / increasing / decreasing) | hourly ≤31 days; ≤150 rows |
| `get_openstack_latency_and_errors` | All | Per service or HTTP route: requests, 4xx/5xx, 5xx rate, ERROR/WARNING lines, anomalous instances, AVG/P50/P95/P99/MAX latency | ≤100 groups |
| `manage_workspace_index` | All (optional) | Agent-managed partial full-text index over one time window: create / benchmark / list / drop | ≤3 indexes |
| `query_remote_otel_parquet` | All (optional) | Zero-download SQL on the 50B-row OTel Parquet benchmark on S3: footer overview, service summary, Body search | window ≤30 s; 150 s timeout |

Tool arguments are typed and range-checked (Pydantic, enforced by the MCP layer), and every value is bound as a SQL parameter. The LLM never writes SQL.

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

**Grounding validator** (`src/validator.py`). After each answer, every number is checked against the numbers in the evidence the agent actually received. Rounding to the precision shown, `k/M/B` suffixes and percent-as-fraction are allowed, and `(derived: a/b = x%)` figures are exempt. For the Manager, sentences making revenue, cost, SLA, customer-impact or downtime claims are rejected, unless they state that such data is absent. On failure the agent gets one revision round with the specific problems listed. The UI shows the result of this check next to every answer.

## Worked investigation (measured on the full corpus)

`scripts/run_worked_investigation.py` writes the complete transcript with every SQL statement to [`docs/worked_investigation.md`](docs/worked_investigation.md), and re-executes each statement to confirm it reproduces. These are the figures the tools return:

- **Engineer.** `search_logs("data TLB error interrupt", phrase, KERNEL, FATAL)` matches **152,734** lines (the BGL `KERNDTLB` alert category). `get_nearby_events` on a representative line shows the same-node sequence around it.
- **Team Lead.** `summarize_by_team_and_component(BOTH, errors_only)` gives **988,775** error/alert lines.
  - BGL `KERNEL` FATAL: **626,450** lines on **31,467** distinct nodes over **179** active days (Core Systems & OS Team).
  - `APP` FATAL: **228,536** lines on only **1,034** nodes (Compute Runtime Team).
  - `MMCS` ERROR: **88,529** lines with no node attribution, on **19** days.

  Weekly trends classify KERNEL as *chronic, dominated by one burst* (active in 100% of weeks; the week of 2005-06-13 holds 60.3%), APP as *increasing*, and MMCS as a *burst*.
- **Manager.** OpenStack `nova-compute` has **192** ERROR lines and all **100** labelled-anomaly lines (**4** anomalous instances). `nova-api` served **98,733** requests with **2** HTTP 5xx, P50 **0.26 s** and P99 **0.51 s**. Scope: 4,747,963 BGL lines over 214.7 days and 207,820 OpenStack lines over 2.7 days.

## Optional extensions

**Agent-managed workspace index.** `manage_workspace_index("benchmark", "BGL", "2005-06-03", "2005-06-17", "error")` builds a partial inverted index (`CREATE INDEX … USING inverted … WHERE event_time BETWEEN …`) over the 836,927 lines in that window. Measured result:
- Creation takes **482 ms**.
- The same full-text count drops from **85.3 ms to 7.1 ms**, a 12× speed-up, with identical results (216,310).
- Break-even is **7** queries.

While the index exists, `search_logs` automatically uses it for full-text searches whose time window lies inside it (the envelope notes this). The OpenAI agent drops any index it created at the end of each question; index names are derived from the validated window, never supplied.

**50 billion rows without downloading them.** `query_remote_otel_parquet` reads the public OpenTelemetry benchmark (`part_000…part_049.parquet`, 1B rows each) in place:

| Action | Measured |
|---|---|
| `overview`, 1 file | 1,000,000,000 rows, 1,624 row groups, time range: **1.4 s** (footer only) |
| `overview`, 50 files | **50,000,000,000** rows, 81,209 row groups, a full day: **~115 s** (footers only, batched) |
| `service_summary`, 2 s window | reads **2 of 1,624** row groups (99.88% skipped), ~3 MB instead of 2.5 GB, 1.1M rows in **~1 s** |

The files are sorted by `Timestamp`, so SereneDB fetches only row groups whose min/max statistics overlap the window. The tool reports row groups and estimated bytes read versus the whole file.

## Tests

```bash
uv run pytest -q              # 36 offline tests: query layer on DuckDB, validator, agent loop with a scripted OpenAI client
uv run pytest -q -m serenedb  # 12 live tests against the loaded SereneDB (incl. both extensions)
```

The offline suite runs the repo's real `sql/` files and query templates on DuckDB, a near-identical dialect, against fixture logs. Expected values are computed directly from the fixture files. Full-text modes need SereneDB and are covered by the live suite.

## Design notes and known limitations

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
