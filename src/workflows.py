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
        ("get_latency_outliers", {"limit": 15}),
    ]


def _manager() -> list[tuple[str, dict[str, Any]]]:
    return [
        ("get_corpus_scope", {}),
        ("get_priority_ranking", {"dataset": "BOTH", "limit": 10}),
        ("get_openstack_latency_and_errors", {}),
    ]


PLANS: dict[Role, Callable[[], list[tuple[str, dict[str, Any]]]]] = {
    Role.ENGINEER: _engineer,
    Role.TEAM_LEAD: _team_lead,
    Role.MANAGER: _manager,
}


def baseline_evidence(role: Role) -> list[dict[str, Any]]:
    """Runs the role's fixed tool sequence; the Engineer plan follows up on the first hit.
    Evidence IDs are renumbered per run (EV-01, EV-02, ...) as in the agent."""
    results = []

    def run(name: str, args: dict[str, Any]) -> Any:
        envelope, error = call_tool(name, args)
        evidence_id = None
        if envelope is not None:
            evidence_id = f"EV-{sum(1 for r in results if r['envelope']) + 1:02d}"
            envelope = {**envelope, "evidence_id": evidence_id}
        results.append({"evidence_id": evidence_id, "name": name, "arguments": args, "envelope": envelope,
                        "error": error})
        return envelope

    for name, args in PLANS[role]():
        envelope = run(name, args)
        if role == Role.ENGINEER and name == "search_logs" and envelope and envelope["rows"]:
            run("get_nearby_events", {"dataset": "BGL", "anchor_line_id": envelope["rows"][0]["line_id"],
                                      "window_lines": 10, "same_entity_only": True})
    return results
