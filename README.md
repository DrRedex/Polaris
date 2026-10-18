# 🧭 Polaris: AI Log Analysis

**Ask your logs a question in plain English and get an answer where every number links back to the SQL that produced it.** Polaris searches millions of log lines in SereneDB and turns the evidence into role-specific insights for engineers, team leads and managers.

> Built by Faiz and Rhea for the **SchubKI National AI Hackathon 2026** (log processing track).

---

## ✨ Features

- **🎯 Answers tailored to who's asking.** Engineers get failure timelines and nearby events. Team leads get recurring patterns and team ownership. Managers get a ranked priority list.
- **🔍 Evidence behind every number.** Findings cite evidence IDs that link to the executed SQL, parameters and result scope. A claim verifier checks cited numbers against that evidence.
- **⚡ Investigation across the whole corpus.** SereneDB handles searching, counting, percentiles and trend aggregation; the agent receives compact evidence instead of raw logs.
- **🛡️ Guarded investigation.** Semantic schema search helps route questions to the right tools. AI-generated SQL is checked by sqlglot and executed through a read-only database role.

> **Verified benchmarks (2026-10-18):** On a 14-day BGL window, a temporary full-text index reduced the `machine check` query average from 65.74 ms to 22.93 ms (2.87×); estimated break-even was 23 queries including cleanup. A one-partition OTel run returned 17.46M rows in 24.72 s while reading 29/1,624 row groups (~44.2 MB estimated selected-column data). Focused tests: 43 passed, 5 skipped. See [docs/TECHNICAL.md](docs/TECHNICAL.md) for configuration, limitations and evidence details.

---

## 🚀 Quick Start

These steps start the Next.js judge dashboard and its FastAPI backend using the small sample corpus. For the full datasets, use the optional full-corpus command below. On Windows, run commands in **Git Bash**.

### Prerequisites

- Git and Git Bash on Windows
- [Docker Desktop](https://www.docker.com/products/docker-desktop/) running
- [uv](https://docs.astral.sh/uv/), which installs and manages the Python environment
- [Node.js 20.9 or newer](https://nodejs.org/) and npm
- An OpenAI API key for generated investigations

### 1. Clone the repository and create configuration

```bash
git clone https://github.com/DrRedex/making-sense-of-logs.git
cd making-sense-of-logs
cp .env.example .env
notepad .env
```

In `.env`, set `OPENAI_API_KEY` to your key. For a local judge setup, add these role passwords and DSNs (the passwords must match in each DSN):

```dotenv
SERENEDB_ENGINEER_PASSWORD=judge_engineer
SERENEDB_TEAM_LEAD_PASSWORD=judge_team_lead
SERENEDB_MANAGER_PASSWORD=judge_manager
SERENEDB_ENGINEER_DSN=postgresql://engineer_role:judge_engineer@127.0.0.1:7890/postgres
SERENEDB_TEAM_LEAD_DSN=postgresql://team_lead_role:judge_team_lead@127.0.0.1:7890/postgres
SERENEDB_MANAGER_DSN=postgresql://manager_role:judge_manager@127.0.0.1:7890/postgres
```

Save the file and close Notepad. These example passwords are for a local evaluation environment; use different secrets for any shared deployment.

### 2. Install Python dependencies and start SereneDB

```bash
uv sync
docker compose -f docker-compose.yaml up -d serenedb
```

Wait for SereneDB to finish starting before loading data. You can check its container with:

```bash
docker compose -f docker-compose.yaml ps
```

### 3. Download and load sample logs

The sample option downloads 2,000 lines from each dataset and is intended for a quick evaluation:

```bash
bash scripts/download_corpus.sh --sample
uv run python -I scripts/prepare_logs.py
uv run python scripts/load_corpus.py
```

To load the full corpora instead, use `bash scripts/download_corpus.sh` in place of the sample download command. It downloads roughly 770 MB; then run the same two Python commands above.

### 4. Create the three dashboard database roles

From the repository root, run:

```bash
uv run python scripts/setup_roles.py
```

This provisions the configured Engineer, Team Lead and Manager roles in the local SereneDB instance.

### 5. Start the API and frontend

Keep each server running in its own Git Bash terminal. In **Terminal 1**, from the repository root, start the API:

```bash
uv run uvicorn src.api.main:app --host 127.0.0.1 --port 8000
```

In **Terminal 2**, start from the repository root and run:

```bash
cd frontend
npm ci
npm run dev
```

Open **http://localhost:3000**. The API health endpoint is **http://127.0.0.1:8000/health**. Keep SereneDB, the API and the frontend running while evaluating the dashboard.

### Optional: Streamlit dashboard

From a third terminal at the repository root, run:

```bash
uv run streamlit run ui/streamlit_app.py
```

Open **http://localhost:8501**. The **Show evidence only (no LLM)** option displays SQL evidence without requiring an OpenAI key.

---

## 💻 Usage

Choose the **Engineer** role and ask:

```text
Customers say the cloud API is sluggish and timing out. Which services are affected, and how bad is the latency?
```

Polaris grounds the question in the log schema, selects relevant investigations, and asks SereneDB to compute request counts, error rates and latency percentiles. The agent returns findings with evidence citations, then separates hypotheses and recommended next steps from what the logs establish.

Try these questions too:

- “Show every trace that mentions a lock timeout.”
- “Which instances failed to spawn, and why?”
- “Which observed problems deserve attention first?” (Manager)

---

## 🏗️ Architecture / Tech Stack

```
 Loghub BGL + OpenStack logs ──▶ SereneDB (full-text indexes, SQL aggregation)
                                        │ parameterized SQL → evidence envelopes
                                        ▼
                     Python backend: MCP server · guarded SQL agent · claim verifier
                                        │
         Streamlit dashboard ── FastAPI ──▶ Next.js frontend    Claude Code / Desktop (MCP)
```

**Frontend:** Next.js 16, React 19, Tailwind CSS 4, Framer Motion and Lucide icons; Streamlit dashboard.

**Backend:** Python 3.11+, FastAPI and Uvicorn; MCP server with typed log tools; SereneDB 26.10 queried through psycopg.

**AI and evidence:** OpenAI (`gpt-4o` by default), Pydantic AI, sentence-transformers semantic schema search, sqlglot SQL guardrails, read-only database access and claim verification.

**Data:** [Loghub](https://github.com/logpai/loghub) BGL and OpenStack logs.

📖 For the full tool reference, guardrail design, benchmarks and citations, see **[docs/TECHNICAL.md](docs/TECHNICAL.md)**.
