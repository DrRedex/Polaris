"""
streamlit_app.py

Three-role web UI: one agent, one corpus, different questions and evidence per role.
Every answer is shown next to the exact SQL, parameters and results it was built from.

    streamlit run ui/streamlit_app.py
"""

import json
import os
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import db  # noqa: E402
from src.agent import call_tool, run_agent  # noqa: E402
from src.prompts import DEFAULT_QUESTIONS, ROLE_PROMPTS, Role  # noqa: E402
from src.workflows import baseline_evidence  # noqa: E402
from src.pipeline_orchestrator import run_full_intelligence_pipeline  # noqa: E402

st.set_page_config(page_title="Making Sense of Logs", page_icon="🔍", layout="wide")

if "history" not in st.session_state:
    st.session_state.history = []  # list of dicts: role, question, kind ("agent" | "evidence"), payload


@st.cache_data(ttl=300, show_spinner=False)
def corpus_scope() -> list[dict]:
    from src.queries import get_corpus_scope
    return get_corpus_scope()["rows"]


def render_knn(matches: list[dict]) -> None:
    st.caption("Semantic schema grounding (KNN, all-MiniLM-L6-v2 cosine similarity)")
    st.dataframe(pd.DataFrame([{
        "confidence": m["confidence_score"], "match": m["match_type"],
        "table.column": f"{m['schema_match']['table']}.{m['schema_match']['column']}",
        "type": m["schema_match"]["type"], "recommended tool": m["schema_match"]["recommended_mcp_tool"],
    } for m in matches]), width="stretch", hide_index=True,
        column_config={"confidence": st.column_config.ProgressColumn(min_value=0.0, max_value=1.0, format="%.3f")})


def render_envelope(env: dict) -> None:
    if env.get("unknown_not_in_logs"):
        st.warning("Not measurable from these logs: " + ", ".join(env["unknown_not_in_logs"]))
    if env.get("knn_schema_matches"):
        render_knn(env["knn_schema_matches"])
    if env.get("executed_sql"):
        st.caption("Guarded SQL as executed (read-only role, LIMIT enforced)")
        st.code(env["executed_sql"], language="sql")
    cols = st.columns(4)
    cols[0].metric("Total matching", "—" if env["total_matching_count"] is None else f"{env['total_matching_count']:,}")
    cols[1].metric("Returned rows", env["returned_count"])
    cols[2].metric("Truncated", "yes" if env["is_truncated"] else "no")
    cols[3].metric("Query time", f"{sum(q['execution_time_ms'] for q in env['executed_queries']):.0f} ms")
    if env.get("truncation_notice"):
        st.caption(env["truncation_notice"])
    for q in env["executed_queries"]:
        st.markdown(f"**{q['label']}** · {q['execution_time_ms']} ms · {q['result_rows']} rows")
        st.code(q["sql"], language="sql")
        st.caption(f"Bound parameters: {json.dumps(q['bound_parameters'])}")
    st.caption("Data scope")
    st.json(env["data_scope"], expanded=False)
    if env.get("summary"):
        st.caption("Summary (computed over the full result)")
        st.json(env["summary"], expanded=False)
    if env["rows"]:
        st.dataframe(pd.DataFrame(env["rows"]), width="stretch", hide_index=True)
    for note in env.get("notes", []):
        st.caption(f"ℹ️ {note}")


def render_claims(v, revised: bool) -> None:
    """Deterministic claim verification: every number checked against the evidence it cites."""
    cols = st.columns(4)
    cols[0].metric("Verified numbers", f"{v.verified_number_rate:.0%}", f"{v.numbers_verified}/{v.numbers_checked}",
                   delta_color="off")
    cols[1].metric("Cited sentences", f"{v.cited_sentences}/{v.sentences_checked}")
    cols[2].metric("Uncited numeric", v.uncited_numeric_sentences)
    cols[3].metric("Verifier time", f"{v.elapsed_ms:.1f} ms")
    if v.no_numeric_claims:
        st.info("No numeric claims to verify in this answer.")
    elif v.passed:
        st.success("Claim check passed: every number is in the evidence it cites"
                   + (" (after one revision)" if revised else ""))
    else:
        st.warning("Claim check found issues" + (" after one revision" if revised else ""))
    if v.stripped_sentences:
        st.error("Removed unsupported business-impact sentences: " + " | ".join(v.stripped_sentences))
    elif v.forbidden_claims:
        st.error("Business-impact claims not supported by the logs: " + " | ".join(v.forbidden_claims))
    bad = [c for c in v.checks if not c.verified]
    if bad:
        st.dataframe(pd.DataFrame([{"number": c.number, "reason": c.reason, "cited": ", ".join(c.cited),
                                    "sentence": c.sentence} for c in bad]), width="stretch", hide_index=True)


def render_tool_calls(calls: list[dict]) -> None:
    st.subheader(f"Executed SQL & evidence ({len(calls)} tool calls)")
    for i, call in enumerate(calls, 1):
        badge = f"[{call['evidence_id']}] " if call.get("evidence_id") else ""
        label = f"{badge}{call['name']}({', '.join(f'{k}={v!r}' for k, v in call['arguments'].items())})"
        with st.expander(label + ("  ⚠️ error" if call.get("error") else "")):
            if call.get("error"):
                st.error(call["error"])
            else:
                render_envelope(call["envelope"])


def render_entry(entry: dict) -> None:
    st.markdown(f"**{entry['role'].value}** · {entry['question']}")
    if entry["kind"] == "pipeline":
        st.json(entry["payload"])
    elif entry["kind"] == "agent":
        result = entry["payload"]
        render_claims(result.validation, result.revised_after_validation)
        if result.missing_sections:
            st.warning("Missing required sections: " + "; ".join(result.missing_sections))
        st.markdown(result.answer)
        token_label = (f"{result.usage.get('prompt_tokens', 0):,} prompt / {result.usage.get('completion_tokens', 0):,} completion tokens"
                       if getattr(result, "usage_available", False) else "token usage unavailable from provider")
        st.caption(f"Model {result.model} · {token_label} · {result.elapsed_ms:.0f} ms end-to-end")
        render_tool_calls([c.model_dump() for c in result.tool_calls])
    else:
        st.info("Evidence only (no LLM): the role's standard tool sequence.")
        render_tool_calls(entry["payload"])


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
st.sidebar.title("🔍 Making Sense of Logs")
status = db.check_connection()
if status["status"] == "connected":
    st.sidebar.success("SereneDB connected")
    try:
        for row in corpus_scope():
            st.sidebar.caption(f"**{row['dataset']}**: {row['total_rows']:,} lines · {row['span_days']} days")
    except Exception as e:
        st.sidebar.warning(f"Corpus not loaded: {e}")
else:
    st.sidebar.error("SereneDB offline")
    st.sidebar.caption("Run `docker compose up -d serenedb`, then `python scripts/load_corpus.py`.")

st.sidebar.divider()
model = st.sidebar.text_input("OpenAI model", os.getenv("OPENAI_MODEL", "gpt-4o"))
has_key = bool(os.getenv("OPENAI_API_KEY"))
st.sidebar.caption("OpenAI key: " + ("loaded from environment" if has_key else "missing; set OPENAI_API_KEY in .env"))
if st.sidebar.button("Clear history"):
    st.session_state.history = []

# ---------------------------------------------------------------------------
def render_investigation_page():
    # Main
    # ---------------------------------------------------------------------------
    st.title("Making Sense of Logs")
    st.caption("The right insights for the right people: one agent, one SereneDB corpus, three roles.")

    role = Role(st.radio("Role", [r.value for r in Role], horizontal=True))
    st.markdown(f"*Core question: {ROLE_PROMPTS[role]['question']}*")
    question = st.text_area("Question", DEFAULT_QUESTIONS[role], key=f"q_{role.value}", height=80)

    c1, c2, c3 = st.columns([1, 1, 1])
    ask = c1.button("Ask the agent", type="primary", disabled=not has_key or status["status"] != "connected")
    evidence_only = c2.button("Show evidence only (no LLM)", disabled=status["status"] != "connected")
    pipeline_ask = c3.button("Run four-step pipeline", disabled=not has_key or status["status"] != "connected")

    if ask:
        with st.status("Agent working…", expanded=True) as box:
            def on_event(kind: str, data: dict) -> None:
                if kind == "tool_call":
                    box.write(f"🔧 `{data['name']}` {json.dumps(data['arguments'])}")
                elif kind == "tool_result":
                    env = data.get("envelope") or {}
                    box.write(f"   ↳ {'error: ' + data['error'] if data.get('error') else str(env.get('returned_count')) + ' rows'}"
                              f" in {data['duration_ms']} ms")
                elif kind == "revising":
                    box.write("✏️ Revising answer after grounding check")
            try:
                result = run_agent(role, question, model=model, on_event=on_event)
                st.session_state.history.insert(0, {"role": role, "question": question, "kind": "agent", "payload": result})
                box.update(label="Done", state="complete", expanded=False)
            except Exception as e:
                box.update(label="Failed", state="error")
                st.error(str(e))

    if evidence_only:
        with st.spinner("Running tools…"):
            calls = baseline_evidence(role)
        st.session_state.history.insert(0, {"role": role, "question": "Standard evidence", "kind": "evidence",
                                            "payload": calls})

    if pipeline_ask:
        with st.spinner("Running schema grounding, guarded SQL, evidence retrieval, and audit…"):
            result = run_full_intelligence_pipeline(question, role.value)
        st.session_state.history.insert(0, {"role": role, "question": question, "kind": "pipeline", "payload": result})

    with st.expander("🧭 Semantic schema & guarded SQL (fallback when no tool fits)"):
        st.caption("Step 1 maps a vague question to SereneDB columns and the best parameterized tool. "
                   "Step 2 runs one LLM-written SELECT only after deterministic guardrails, as a read-only role.")
        sem_q = st.text_input("Question", "Customers say the cloud API is sluggish and timing out", key="sem_q")
        if st.button("1. Resolve schema (KNN)", key="sem_resolve"):
            with st.spinner("Embedding question (first call loads the model)…"):
                env, err = call_tool("resolve_semantic_schema", {"user_query": sem_q, "k": 4})
            st.session_state.sem_env, st.session_state.sem_err = env, err
        if st.session_state.get("sem_err"):
            st.error(st.session_state.sem_err)
        sem_env = st.session_state.get("sem_env")
        if sem_env:
            tools = sem_env["summary"]["recommended_tools"]
            st.success(f"Recommended tool: `{tools[0]['tool']}` (score {tools[0]['score']})" if tools else "No tool match")
            render_knn(sem_env["knn_schema_matches"])
            with st.popover("Sandbox prompt"):
                st.code(sem_env["sandbox_prompt"], language="markdown")
            if st.button("Generate candidate SQL with OpenAI", disabled=not has_key, key="sem_gen"):
                from src.guarded_sql_agent import generate_candidate_sql
                try:
                    st.session_state.sem_sql = generate_candidate_sql(sem_env["sandbox_prompt"], model=model)
                except Exception as e:
                    st.error(str(e))
        sem_sql = st.text_area("Candidate SQL (untrusted)", st.session_state.get(
            "sem_sql", "SELECT http_route, COUNT(*) AS requests, ROUND(quantile_cont(latency_sec, 0.95), 3) AS p95\n"
                       "FROM openstack_logs WHERE latency_sec IS NOT NULL GROUP BY http_route ORDER BY p95 DESC"),
            height=120, key=f"sem_sql_box_{hash(st.session_state.get('sem_sql', ''))}")
        if st.button("2. Validate & run guarded SQL", disabled=(status["status"] != "connected" or role == Role.MANAGER), key="sem_run"):
            if role == Role.MANAGER:
                st.error("Managers cannot run raw-row SQL. Use the aggregate summary tools.")
            else:
                env, err = call_tool("execute_guarded_readonly_sql", {"user_query": sem_q, "candidate_sql": sem_sql})
                if err:
                    st.error(f"Blocked: {err}")
                else:
                    st.session_state.history.insert(0, {"role": role, "question": f"Guarded SQL: {sem_q}", "kind": "evidence",
                                                        "payload": [{"name": "execute_guarded_readonly_sql",
                                                                     "arguments": {"user_query": sem_q}, "envelope": env,
                                                                     "error": None}]})

    if st.session_state.history:
        latest, *older = st.session_state.history
        render_entry(latest)
        if older:
            st.divider()
            st.subheader("Earlier answers")
            # Tabs, not expanders: Streamlit does not allow the tool-call expanders to nest.
            older = older[:6]
            for tab, entry in zip(st.tabs([f"{e['role'].value} #{i + 2}" for i, e in enumerate(older)]), older):
                with tab:
                    render_entry(entry)

investigation_tab, performance_tab = st.tabs(["Investigation", "\u26a1 Performance & Special Awards"])
with investigation_tab:
    render_investigation_page()

with performance_tab:
    st.subheader("Performance & Special Awards")
    st.caption("Every result is marked measured, unavailable, skipped, or failed. Remote scans are opt-in.")
    report_path = Path(__file__).resolve().parents[1] / "docs" / "PERFORMANCE_AND_AWARDS.md"
    if report_path.exists():
        st.markdown(report_path.read_text(encoding="utf-8"))
    else:
        st.info("No benchmark report exists yet. Run `make benchmarks` for safe status records.")

    st.markdown("#### Remote OpenTelemetry Parquet runner")
    st.warning("Reads public S3 objects. One partition is the safe default; multi-partition reads can incur network and compute cost.")
    partition_count = st.number_input("Partitions to query (part_000 prefix)", min_value=1, max_value=50, value=1, step=1)
    confirm_many = st.checkbox("Confirm a multi-partition query", disabled=partition_count == 1)
    if st.button("Run remote scale summary", disabled=(partition_count > 1 and not confirm_many)):
        try:
            from src.extensions import query_otel_scale_summary
            result = query_otel_scale_summary(int(partition_count), confirm_multpartition=confirm_many)
            st.json(result)
        except Exception as exc:
            st.error(f"Remote query failed: {exc}")

    st.markdown("#### Database role policy")
    role_config = {name: bool(os.getenv(env)) for name, env in {
        "Engineer DSN": "SERENEDB_ENGINEER_DSN", "Team Lead DSN": "SERENEDB_TEAM_LEAD_DSN",
        "Manager DSN": "SERENEDB_MANAGER_DSN"}.items()}
    st.json({"status": "configured" if all(role_config.values()) else "unavailable",
             "role_dsns_present": role_config,
             "verification": "Configure all role DSNs and run the opt-in database denial test.",
             "native_row_level_security": "Not claimed; policy uses grants and aggregate views."})

    st.markdown("#### Latest answer efficiency")
    latest_payload = st.session_state.history[0]["payload"] if st.session_state.history else None
    latest_calls = getattr(latest_payload, "tool_calls", []) if latest_payload is not None else []
    role_runs = []
    for entry in st.session_state.history:
        payload = entry.get("payload")
        if hasattr(payload, "elapsed_ms"):
            role_runs.append({"role": payload.role.value, "model": payload.model,
                              "end_to_end_latency_ms": payload.elapsed_ms,
                              "latency_stages": "tool calls, model requests, and any claim-validation revision",
                              "token_usage": payload.usage if payload.usage_available else None,
                              "token_usage_status": "measured from provider" if payload.usage_available else "unavailable",
                              "estimated_cost_usd": payload.estimated_cost_usd,
                              "cost_status": "estimated from explicit configured token prices" if payload.estimated_cost_usd is not None else payload.cost_unavailable_reason})
    if role_runs:
        st.json({"per_role_runs": role_runs})
    if latest_calls:
        metrics = [{"duration_ms": call.duration_ms, "status": call.error or "success",
                    "efficiency_telemetry": (call.envelope or {}).get("efficiency_telemetry")}
                   for call in latest_calls]
        st.json({"per_tool_metrics": metrics})
    if not role_runs:
        st.info("Per-role latency, token use, and cost will appear after a role investigation.")
