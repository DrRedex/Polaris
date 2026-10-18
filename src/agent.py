"""
agent.py

OpenAI tool-calling loop over the same tools the MCP server exposes. Tool schemas
are read from the MCP server, and calls go through mcp.call_tool, so argument
validation (types, limits) is identical for Claude Desktop/Code and this agent.

    from src.agent import run_agent
    result = run_agent(Role.ENGINEER, "Why did node R02-M1-N0 fail?")

Environment: OPENAI_API_KEY, OPENAI_MODEL (default gpt-4o), loaded from .env.
"""

import asyncio
import concurrent.futures
import json
import math
import os
import time
from typing import Any, Callable, Coroutine, Optional

from dotenv import load_dotenv
from pydantic import BaseModel

from src.mcp_server import mcp
from src.prompts import Role, missing_sections, system_prompt
from src.claim_verifier import ClaimReport, index_evidence, verify_response_claims
from src.db import to_llm_evidence_view

load_dotenv()

MAX_TOOL_ROUNDS = 8


class ToolCallRecord(BaseModel):
    evidence_id: Optional[str] = None
    name: str
    arguments: dict[str, Any]
    envelope: Optional[dict[str, Any]] = None
    error: Optional[str] = None
    duration_ms: float


class AgentResult(BaseModel):
    role: Role
    question: str
    model: str
    answer: str
    tool_calls: list[ToolCallRecord]
    validation: ClaimReport
    missing_sections: list[str] = []
    evidence_ledger: dict[str, dict[str, Any]]
    revised_after_validation: bool
    usage: dict[str, int]
    usage_available: bool = False
    elapsed_ms: float
    estimated_cost_usd: Optional[float] = None
    cost_unavailable_reason: Optional[str] = None


def _run(coro: Coroutine[Any, Any, Any]) -> Any:
    """asyncio.run that also works when the caller already has a running event loop
    (Jupyter, async web frameworks): the coroutine then runs on a worker thread."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


def _openai_tools() -> list[dict[str, Any]]:
    tools = _run(mcp.list_tools())
    return [{
        "type": "function",
        "function": {"name": t.name, "description": t.description or "", "parameters": t.input_schema},
    } for t in tools]


def call_tool(name: str, arguments: dict[str, Any]) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    """Runs one tool through the MCP server; returns (envelope, error_message)."""
    try:
        result = _run(mcp.call_tool(name, arguments))
    except Exception as e:  # validation or query errors are reported back to the model
        return None, str(e.__cause__ or e)
    if result.is_error:
        return None, " ".join(getattr(c, "text", "") for c in result.content)
    if result.structured_content is not None:
        return result.structured_content, None
    return json.loads(result.content[0].text), None


def _client():
    from openai import OpenAI
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is not set. Copy .env.example to .env and add your key.")
    return OpenAI()


def run_agent(role: Role, question: str, *, client: Any = None, model: Optional[str] = None,
              on_event: Optional[Callable[[str, dict[str, Any]], None]] = None) -> AgentResult:
    """Answers one question for one role. on_event(kind, data) reports progress
    ("tool_call", "tool_result", "revising") for live UIs."""
    started = time.perf_counter()
    client = client or _client()
    model = model or os.getenv("OPENAI_MODEL", "gpt-4o")
    emit = on_event or (lambda kind, data: None)
    tools = _openai_tools()
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt(role)},
        {"role": "user", "content": question},
    ]
    records: list[ToolCallRecord] = []
    ledger: dict[str, dict[str, Any]] = {}
    usage = {"prompt_tokens": 0, "completion_tokens": 0}
    usage_available = False

    def complete(tool_choice: str) -> Any:
        nonlocal usage_available
        resp = client.chat.completions.create(model=model, messages=messages, tools=tools, tool_choice=tool_choice)
        if getattr(resp, "usage", None):
            usage_available = True
            usage["prompt_tokens"] += resp.usage.prompt_tokens or 0
            usage["completion_tokens"] += resp.usage.completion_tokens or 0
        return resp.choices[0].message

    answer = ""
    for round_no in range(MAX_TOOL_ROUNDS + 1):
        # After the last allowed round, force a written answer from the evidence so far.
        msg = complete("auto" if round_no < MAX_TOOL_ROUNDS else "none")
        if not msg.tool_calls:
            answer = msg.content or ""
            break
        messages.append({
            "role": "assistant",
            "content": msg.content,
            "tool_calls": [{"id": tc.id, "type": "function",
                            "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                           for tc in msg.tool_calls],
        })
        for tc in msg.tool_calls:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError as e:
                args, envelope, error, elapsed = {}, None, f"arguments were not valid JSON: {e}", 0.0
            else:
                emit("tool_call", {"name": tc.function.name, "arguments": args})
                t0 = time.perf_counter()
                manager_forbidden = {"search_logs", "get_nearby_events", "get_latency_outliers",
                                     "execute_guarded_readonly_sql", "correlate_otel_trace"}
                is_remote_body = tc.function.name == "query_remote_otel_parquet" and args.get("action") == "search_body"
                if role == Role.MANAGER and (tc.function.name in manager_forbidden or is_remote_body):
                    envelope, error = None, "Manager role cannot access raw log rows, message payloads, or trace details. Use aggregate tools."
                else:
                    envelope, error = call_tool(tc.function.name, args)
                elapsed = (time.perf_counter() - t0) * 1000
            evidence_id = None
            if envelope is not None:
                # Evidence IDs are numbered per question so answers cite EV-01, EV-02, ...
                evidence_id = f"EV-{len(ledger) + 1:02d}"
                envelope = {"evidence_id": evidence_id, **{k: v for k, v in envelope.items() if k != "evidence_id"}}
                ledger[evidence_id] = envelope
                index_evidence(evidence_id, envelope)
            record = ToolCallRecord(evidence_id=evidence_id, name=tc.function.name, arguments=args,
                                    envelope=envelope, error=error, duration_ms=round(elapsed, 1))
            records.append(record)
            emit("tool_result", record.model_dump())
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": json.dumps(to_llm_evidence_view(envelope), default=str) if envelope is not None else f"ERROR: {error}",
            })

    _drop_workspace_indexes_created(records)
    report = verify_response_claims(answer, ledger, role.value)
    missing = missing_sections(role, answer)
    revised = False
    # Without any evidence (e.g. every tool failed) there are no figures to ask for.
    if not report.passed or (report.no_numeric_claims and ledger) or missing:
        emit("revising", {**report.model_dump(exclude={"checks", "cleaned_text"}), "missing_sections": missing})
        problems = [report.feedback()] if not report.passed else []
        if missing:
            problems.append("Your answer is missing these required sections: " + "; ".join(missing) + ".")
        if report.no_numeric_claims and ledger:
            problems.append("State the key measured figures (counts, bursts, nodes, scores) from the evidence, "
                            "each with its [EV-XX].")
        messages.append({"role": "assistant", "content": answer})
        messages.append({"role": "user", "content": "Revise your answer. " + " ".join(problems)
                         + " Use exactly the required sections and keep the [EV-XX] citations. Do not call tools."})
        answer = complete("none").content or answer
        report = verify_response_claims(answer, ledger, role.value)
        missing = missing_sections(role, answer)
        revised = True
    if role == Role.MANAGER and report.forbidden_claims:
        # Last resort: unsupported business-impact sentences are removed, not shown.
        report = verify_response_claims(answer, ledger, role.value, mode="strip")
        answer = report.cleaned_text

    price_in = os.getenv("OPENAI_INPUT_PRICE_PER_1M")
    price_out = os.getenv("OPENAI_OUTPUT_PRICE_PER_1M")
    cost = None
    reason = None
    if usage_available and price_in is not None and price_out is not None:
        try:
            input_price, output_price = float(price_in), float(price_out)
            if not math.isfinite(input_price) or not math.isfinite(output_price) or input_price < 0 or output_price < 0:
                raise ValueError
            cost = (usage["prompt_tokens"] * input_price + usage["completion_tokens"] * output_price) / 1_000_000
        except ValueError:
            reason = "Configured token prices must be numeric USD per 1M tokens."
    else:
        reason = "Provider usage metadata or explicit input/output price configuration is unavailable."
    return AgentResult(role=role, question=question, model=model, answer=answer, tool_calls=records,
                       validation=report, missing_sections=missing, evidence_ledger=ledger,
                       revised_after_validation=revised, usage=usage, usage_available=usage_available,
                       elapsed_ms=round((time.perf_counter()-started)*1000, 2),
                       estimated_cost_usd=cost, cost_unavailable_reason=reason)


def _drop_workspace_indexes_created(records: list[ToolCallRecord]) -> None:
    """Each question is one investigation: drop workspace indexes this run created and did not drop."""
    created, dropped = {}, set()
    for r in records:
        if r.name != "manage_workspace_index" or not r.envelope:
            continue
        scope = r.envelope["data_scope"]
        if r.arguments.get("action") == "drop":
            dropped.add(scope.get("index_name"))
        elif (r.envelope.get("summary") or {}).get("created_in_this_call") or any(
                row.get("already_existed") is False for row in r.envelope["rows"]):
            created[scope["index_name"]] = {"action": "drop", "dataset": scope["dataset"],
                                           "start_time": scope["time_filter_start"],
                                           "end_time": scope["time_filter_end"]}
    for name, args in created.items():
        if name not in dropped:
            call_tool("manage_workspace_index", args)
