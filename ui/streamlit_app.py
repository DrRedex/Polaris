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
from src.agent import run_agent  # noqa: E402
from src.prompts import DEFAULT_QUESTIONS, ROLE_PROMPTS, Role  # noqa: E402
from src.workflows import baseline_evidence  # noqa: E402

st.set_page_config(page_title="Making Sense of Logs", page_icon="🔍", layout="wide")

if "history" not in st.session_state:
    st.session_state.history = []  # list of dicts: role, question, kind ("agent" | "evidence"), payload


@st.cache_data(ttl=300, show_spinner=False)
def corpus_scope() -> list[dict]:
    from src.queries import get_corpus_scope
    return get_corpus_scope()["rows"]


def render_envelope(env: dict) -> None:
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


def render_tool_calls(calls: list[dict]) -> None:
    st.subheader(f"Executed SQL & evidence ({len(calls)} tool calls)")
    for i, call in enumerate(calls, 1):
        label = f"{i}. {call['name']}({', '.join(f'{k}={v!r}' for k, v in call['arguments'].items())})"
        with st.expander(label + ("  ⚠️ error" if call.get("error") else "")):
            if call.get("error"):
                st.error(call["error"])
            else:
                render_envelope(call["envelope"])


def render_entry(entry: dict) -> None:
    st.markdown(f"**{entry['role'].value}** · {entry['question']}")
    if entry["kind"] == "agent":
        result = entry["payload"]
        v = result.validation
        if v.passed:
            st.success(f"Grounding check passed: {v.numbers_checked} figures matched tool results"
                       + (" (after one revision)" if result.revised_after_validation else ""))
        else:
            st.warning("Grounding check found issues: "
                       + "; ".join(([f"unverified figures {v.unverified_numbers}"] if v.unverified_numbers else [])
                                   + ([f"business-impact claims {v.forbidden_claims}"] if v.forbidden_claims else [])))
        st.markdown(result.answer)
        st.caption(f"Model {result.model} · {result.usage.get('prompt_tokens', 0):,} prompt / "
                   f"{result.usage.get('completion_tokens', 0):,} completion tokens")
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
# Main
# ---------------------------------------------------------------------------
st.title("Making Sense of Logs")
st.caption("The right insights for the right people: one agent, one SereneDB corpus, three roles.")

role = Role(st.radio("Role", [r.value for r in Role], horizontal=True))
st.markdown(f"*Core question: {ROLE_PROMPTS[role]['question']}*")
question = st.text_area("Question", DEFAULT_QUESTIONS[role], key=f"q_{role.value}", height=80)

c1, c2 = st.columns([1, 1])
ask = c1.button("Ask the agent", type="primary", disabled=not has_key or status["status"] != "connected")
evidence_only = c2.button("Show evidence only (no LLM)", disabled=status["status"] != "connected")

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
