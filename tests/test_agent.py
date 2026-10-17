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
        return _msg(content=f"## Observed facts\nParity errors: {n} lines [search_logs: total_matching_count={n}].")

    result = run_agent(Role.ENGINEER, "parity errors?", client=_client(create), model="test-model")
    assert [c.name for c in result.tool_calls] == ["search_logs"]
    assert result.tool_calls[0].envelope["is_truncated"]
    assert result.validation.passed and not result.revised_after_validation
    assert result.usage["prompt_tokens"] == 20


def test_tool_error_is_returned_to_model(db):
    seen = {}
    replies = [_msg(calls=[("get_nearby_events", {"dataset": "BGL", "anchor_line_id": 10**9})])]

    def create(**kwargs):
        if replies:
            return replies.pop(0)
        seen["tool_message"] = kwargs["messages"][-1]["content"]
        return _msg(content="The anchor line does not exist.")

    client = _client(create)
    result = run_agent(Role.ENGINEER, "context?", client=client, model="test-model")
    assert result.tool_calls[0].error and "not found" in seen["tool_message"]


def test_limit_validation_error_reported(db):
    replies = [_msg(calls=[("search_logs", {"dataset": "BGL", "query_text": "x", "limit": 500})]),
               _msg(content="Could not search.")]
    client = _client(lambda **kw: replies.pop(0))
    result = run_agent(Role.ENGINEER, "x", client=client, model="test-model")
    assert "less than or equal to 100" in result.tool_calls[0].error


def test_manager_revision_on_forbidden_claim(db):
    replies = [_msg(content="This cost the company millions in revenue losses."),
               _msg(content="Revenue impact is not recorded in the logs.")]
    client = _client(lambda **kw: replies.pop(0))
    result = run_agent(Role.MANAGER, "priorities?", client=client, model="test-model")
    assert result.revised_after_validation and result.validation.passed
