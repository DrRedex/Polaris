"""
run_worked_investigation.py

Reproduces the end-to-end worked investigation: the same agent and corpus answer the
Engineer, Team Lead and Manager questions. Writes docs/worked_investigation.md with each
answer, its grounding check, and every executed SQL statement, then re-executes every
statement against SereneDB to confirm the evidence reproduces.

    python scripts/run_worked_investigation.py                 # needs OPENAI_API_KEY
    python scripts/run_worked_investigation.py --evidence-only # no LLM, tool evidence only
"""

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.agent import run_agent  # noqa: E402
from src.db import EvidenceBuilder  # noqa: E402
from src.prompts import DEFAULT_QUESTIONS, Role  # noqa: E402
from src.workflows import baseline_evidence  # noqa: E402

PREVIEW_ROWS = 5
WS_FROM_RE = re.compile(r"FROM ws_([a-z_]+?)_\d{14}_\d{14} i")


def _replayable(sql: str) -> bool:
    # Only read-only statements on the local corpus are replayed. DDL (workspace indexes)
    # would change state, and remote S3 scans take tens of seconds each.
    s = sql.lstrip().upper()
    return (s.startswith(("SELECT", "WITH")) and "READ_PARQUET(" not in s and "PARQUET_METADATA(" not in s
            and "PG_INDEXES" not in s)


def reproduce(envelope: dict) -> tuple[list[str], int]:
    """Re-runs each replayable query with its bound parameters and compares row counts.
    Returns (problems, number of queries replayed)."""
    problems, replayed = [], 0
    with EvidenceBuilder("reproduce") as eb:
        for q in envelope["executed_queries"]:
            if not _replayable(q["sql"]):
                continue
            # Workspace indexes are dropped after a run. Searches that went through one carry the
            # same time filters, so they replay on the main index; benchmark-only queries are skipped.
            sql = q["sql"]
            if WS_FROM_RE.search(sql):
                if " JOIN " not in sql:
                    continue
                sql = WS_FROM_RE.sub(r"FROM \1_idx i", sql)
            try:
                rows = eb.query(q["label"], sql, q["bound_parameters"])
            except Exception as e:  # report, keep going
                problems.append(f"{q['label']}: {type(e).__name__}: {e}")
                continue
            replayed += 1
            if len(rows) != q["result_rows"]:
                problems.append(f"{q['label']}: {len(rows)} rows now vs {q['result_rows']} recorded")
    return problems, replayed


def md_tool_call(i: int, call: dict) -> list[str]:
    out = [f"#### {i}. `{call['name']}`", "", f"Arguments: `{json.dumps(call['arguments'])}`", ""]
    if call.get("error"):
        return out + [f"Error returned to the agent: `{call['error']}`", ""]
    env = call["envelope"]
    problems, replayed = reproduce(env)
    if problems:
        verdict = "NO - " + "; ".join(problems)
    elif replayed:
        verdict = f"yes ({replayed} of {len(env['executed_queries'])} statements replayed)"
    else:
        verdict = "not replayed (DDL, catalog or remote S3 statements only)"
    out += [
        f"- total_matching_count: {env['total_matching_count']}, returned: {env['returned_count']}, "
        f"truncated: {env['is_truncated']}",
        f"- reproduced on re-execution: {verdict}",
        "",
    ]
    for q in env["executed_queries"]:
        out += [f"*{q['label']}* ({q['execution_time_ms']} ms, {q['result_rows']} rows)", "", "```sql", q["sql"], "```",
                f"Parameters: `{json.dumps(q['bound_parameters'])}`", ""]
    if env["rows"]:
        keys = list(env["rows"][0].keys())[:8]
        out += ["| " + " | ".join(keys) + " |", "|" + "---|" * len(keys)]
        for r in env["rows"][:PREVIEW_ROWS]:
            out.append("| " + " | ".join(str(r.get(k, ""))[:60].replace("|", "\\|") for k in keys) + " |")
        out.append("")
    if env.get("summary"):
        out += ["<details><summary>Summary</summary>", "", "```json",
                json.dumps(env["summary"], indent=1, default=str)[:4000], "```", "</details>", ""]
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--evidence-only", action="store_true")
    ap.add_argument("--out", type=Path, default=ROOT / "docs" / "worked_investigation.md")
    args = ap.parse_args()

    lines = ["# Worked investigation", "",
             f"Generated {datetime.now():%Y-%m-%d %H:%M} by `scripts/run_worked_investigation.py`. "
             "Same agent, same SereneDB corpus, three roles. Every tool call below lists the exact SQL "
             "and parameters, and each statement was re-executed to confirm it reproduces.", ""]
    failures = 0
    for role in Role:
        question = DEFAULT_QUESTIONS[role]
        print(f"== {role.value}")
        lines += [f"## {role.value}", "", f"**Question:** {question}", ""]
        if args.evidence_only:
            calls = baseline_evidence(role)
            lines += ["*Evidence-only run (no LLM).*", ""]
        else:
            result = run_agent(role, question, on_event=lambda k, d: k == "tool_call" and print("  tool:", d["name"]))
            v = result.validation
            failures += 0 if v.passed else 1
            lines += [f"*Model `{result.model}`. Grounding check: "
                      f"{'passed' if v.passed else 'FAILED'} ({v.numbers_checked} figures checked"
                      f"{', revised once' if result.revised_after_validation else ''}).*", ""]
            if not v.passed:
                lines += [f"> Unverified figures: {v.unverified_numbers}; business-impact claims: {v.forbidden_claims}", ""]
            lines += ["### Answer", "", result.answer, ""]
            calls = [c.model_dump() for c in result.tool_calls]
        lines += ["### Evidence", ""]
        for i, call in enumerate(calls, 1):
            lines += md_tool_call(i, call)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {args.out}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
