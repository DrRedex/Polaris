"""
workflows.py

Deterministic per-role tool sequences (no LLM). They show exactly which evidence each
role relies on, let the UI work without an API key, and give the worked investigation
a fixed baseline to compare the agent's answers against.
"""

from typing import Any, Callable

from src.agent import call_tool
from src.prompts import Role


def _engineer() -> list[tuple[str, dict[str, Any]]]:
    return [("search_logs", {"dataset": "BGL", "query_text": "data TLB error interrupt", "mode": "phrase",
                             "component": "KERNEL", "severity": "FATAL", "limit": 10})]


def _team_lead() -> list[tuple[str, dict[str, Any]]]:
    return [
        ("summarize_by_team_and_component", {"dataset": "BOTH", "errors_only": True, "limit": 25}),
        ("get_recurring_trends", {"dataset": "BGL", "bucket_size": "week", "errors_only": True}),
        ("get_recurring_trends", {"dataset": "OpenStack", "bucket_size": "day", "errors_only": True}),
    ]


def _manager() -> list[tuple[str, dict[str, Any]]]:
    return [
        ("get_corpus_scope", {}),
        ("summarize_by_team_and_component", {"dataset": "BOTH", "errors_only": True, "limit": 15}),
        ("get_openstack_latency_and_errors", {}),
    ]


PLANS: dict[Role, Callable[[], list[tuple[str, dict[str, Any]]]]] = {
    Role.ENGINEER: _engineer,
    Role.TEAM_LEAD: _team_lead,
    Role.MANAGER: _manager,
}


def baseline_evidence(role: Role) -> list[dict[str, Any]]:
    """Runs the role's fixed tool sequence; the Engineer plan follows up on the first hit."""
    results = []
    for name, args in PLANS[role]():
        envelope, error = call_tool(name, args)
        results.append({"name": name, "arguments": args, "envelope": envelope, "error": error})
        if role == Role.ENGINEER and name == "search_logs" and envelope and envelope["rows"]:
            follow = {"dataset": "BGL", "anchor_line_id": envelope["rows"][0]["line_id"],
                      "window_lines": 10, "same_entity_only": True}
            env2, err2 = call_tool("get_nearby_events", follow)
            results.append({"name": "get_nearby_events", "arguments": follow, "envelope": env2, "error": err2})
    return results
