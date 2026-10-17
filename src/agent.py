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
import os
import time
from typing import Any, Callable, Coroutine, Optional

from dotenv import load_dotenv
from pydantic import BaseModel

from src.mcp_server import mcp
from src.prompts import Role, system_prompt
from src.validator import ValidationReport, validate_answer

load_dotenv()

MAX_TOOL_ROUNDS = 8


class ToolCallRecord(BaseModel):
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
    validation: ValidationReport
    revised_after_validation: bool
    usage: dict[str, int]


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
    client = client or _client()
    model = model or os.getenv("OPENAI_MODEL", "gpt-4o")
    emit = on_event or (lambda kind, data: None)
    tools = _openai_tools()
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt(role)},
        {"role": "user", "content": question},
    ]
    records: list[ToolCallRecord] = []
    usage = {"prompt_tokens": 0, "completion_tokens": 0}

    def complete(tool_choice: str) -> Any:
        resp = client.chat.completions.create(model=model, messages=messages, tools=tools, tool_choice=tool_choice)
        if getattr(resp, "usage", None):
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
                envelope, error = call_tool(tc.function.name, args)
                elapsed = (time.perf_counter() - t0) * 1000
            record = ToolCallRecord(name=tc.function.name, arguments=args, envelope=envelope,
                                    error=error, duration_ms=round(elapsed, 1))
            records.append(record)
            emit("tool_result", record.model_dump())
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": json.dumps(envelope, default=str) if envelope is not None else f"ERROR: {error}",
            })

    _drop_workspace_indexes_created(records)
    envelopes = [r.envelope for r in records if r.envelope]
    report = validate_answer(role, answer, envelopes)
    revised = False
    if not report.passed:
        emit("revising", report.model_dump())
        messages.append({"role": "assistant", "content": answer})
        messages.append({"role": "user", "content": "Revise your answer. " + report.feedback()
                         + " Keep the same sections. Do not call tools."})
        answer = complete("none").content or answer
        report = validate_answer(role, answer, envelopes)
        revised = True

    return AgentResult(role=role, question=question, model=model, answer=answer, tool_calls=records,
                       validation=report, revised_after_validation=revised, usage=usage)


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
