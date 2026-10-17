"""Agent loop tests with a scripted OpenAI client (no network, no API key)."""

import json
from types import SimpleNamespace

from src.agent import run_agent
from src.prompts import Role


def _msg(content=None, calls=()):
    tool_calls = [SimpleNamespace(id=f"call_{i}", function=SimpleNamespace(name=n, arguments=json.dumps(a)))
                  for i, (n, a) in enumerate(calls)] or None
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=tool_calls))],
                           usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5))


def _full(role, first_section_text):
    """A stub answer containing every section the role's output contract requires."""
    from src.prompts import required_sections
    sections = required_sections(role)
    return "\n".join([f"## {sections[0]}", first_section_text] + [f"## {s}\nNone." for s in sections[1:]])


def _client(create):
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


def test_tool_loop_and_validation(db):
    replies = [_msg(calls=[("search_logs", {"dataset": "BGL", "query_text": "parity error",
                                            "mode": "substring", "limit": 2})])]

    def create(**kwargs):
        if replies:
            return replies.pop(0)
        # Answer with the real count the tool returned.
        n = json.loads(kwargs["messages"][-1]["content"])["total_matching_count"]
        return _msg(content=_full(Role.ENGINEER, f"Parity errors: {n} lines [EV-01]."))

    result = run_agent(Role.ENGINEER, "parity errors?", client=_client(create), model="test-model")
    assert [c.name for c in result.tool_calls] == ["search_logs"]
    assert result.tool_calls[0].envelope["is_truncated"]
    assert result.validation.passed and not result.revised_after_validation
    assert result.validation.verified_number_rate == 1.0
    assert result.tool_calls[0].evidence_id == "EV-01" and set(result.evidence_ledger) == {"EV-01"}
    assert result.usage["prompt_tokens"] == 20


def test_tool_error_is_returned_to_model(db):
    seen = {}
    replies = [_msg(calls=[("get_nearby_events", {"dataset": "BGL", "anchor_line_id": 10**9})])]

    def create(**kwargs):
        if replies:
            return replies.pop(0)
        seen.setdefault("tool_message", kwargs["messages"][-1]["content"])
        return _msg(content=_full(Role.ENGINEER, "The anchor line does not exist."))

    client = _client(create)
    result = run_agent(Role.ENGINEER, "context?", client=client, model="test-model")
    assert result.tool_calls[0].error and "not found" in seen["tool_message"]


def test_limit_validation_error_reported(db):
    replies = [_msg(calls=[("search_logs", {"dataset": "BGL", "query_text": "x", "limit": 500})]),
               _msg(content=_full(Role.ENGINEER, "Could not search."))]
    client = _client(lambda **kw: replies.pop(0))
    result = run_agent(Role.ENGINEER, "x", client=client, model="test-model")
    assert "less than or equal to 100" in result.tool_calls[0].error


def test_manager_revision_on_forbidden_claim(db):
    replies = [_msg(content=_full(Role.MANAGER, "This cost the company millions in revenue losses.")),
               _msg(content=_full(Role.MANAGER, "Revenue impact is not recorded in the logs."))]
    client = _client(lambda **kw: replies.pop(0))
    result = run_agent(Role.MANAGER, "priorities?", client=client, model="test-model")
    assert result.revised_after_validation and result.validation.passed


def test_workspace_indexes_created_by_a_run_are_dropped(monkeypatch):
    import src.agent as agent
    from src.agent import ToolCallRecord
    calls = []
    monkeypatch.setattr(agent, "call_tool", lambda name, args: calls.append((name, args)) or ({}, None))
    scope = {"index_name": "ws_bgl_logs_20050603000000_20050610000000", "dataset": "BGL",
             "time_filter_start": "2005-06-03T00:00:00", "time_filter_end": "2005-06-10T00:00:00"}
    created = ToolCallRecord(name="manage_workspace_index", arguments={"action": "create"}, duration_ms=1,
                             envelope={"data_scope": scope, "rows": [{"already_existed": False}], "summary": None})
    reused = ToolCallRecord(name="manage_workspace_index", arguments={"action": "create"}, duration_ms=1,
                            envelope={"data_scope": {**scope, "index_name": "ws_other"},
                                      "rows": [{"already_existed": True}], "summary": None})
    agent._drop_workspace_indexes_created([created, reused])
    assert calls == [("manage_workspace_index", {"action": "drop", "dataset": "BGL",
                                                 "start_time": "2005-06-03T00:00:00",
                                                 "end_time": "2005-06-10T00:00:00"})]


def test_missing_sections_trigger_one_revision(db):
    from src.prompts import missing_sections, required_sections
    full = "\n".join(f"## {s}\nText." for s in required_sections(Role.ENGINEER))
    assert missing_sections(Role.ENGINEER, full) == []
    replies = [_msg(content="## Observed facts\nSomething failed."), _msg(content=full)]
    result = run_agent(Role.ENGINEER, "x", client=_client(lambda **kw: replies.pop(0)), model="test-model")
    assert result.revised_after_validation and result.missing_sections == []
