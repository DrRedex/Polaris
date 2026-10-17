"""
cli_demo.py

Terminal demo: runs the agent for one role (or all three) and shows each tool call,
its SQL and result size as it happens, then the grounded answer.

    python ui/cli_demo.py --role Engineer
    python ui/cli_demo.py --role all
    python ui/cli_demo.py --role Manager --question "What should we fix first?"
    python ui/cli_demo.py --role "Team Lead" --evidence-only      # no LLM
"""

import argparse
import json
import sys
from pathlib import Path

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.agent import run_agent  # noqa: E402
from src.prompts import DEFAULT_QUESTIONS, Role  # noqa: E402
from src.workflows import baseline_evidence  # noqa: E402

# Windows consoles and pipes default to cp1252, which cannot encode the status icons.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
console = Console()


def show_envelope(env: dict, show_sql: bool) -> None:
    total = env["total_matching_count"]
    if env.get("unknown_not_in_logs"):
        console.print(f"   [yellow]not in logs: {', '.join(env['unknown_not_in_logs'])}[/yellow]")
    console.print(f"   [dim]returned {env['returned_count']} rows"
                  + (f" of {total:,}" if total is not None else "")
                  + (" (truncated)" if env["is_truncated"] else "") + "[/dim]")
    if show_sql:
        for q in env["executed_queries"]:
            console.print(Syntax(q["sql"], "sql", word_wrap=True, background_color="default"))
            console.print(f"   [dim]params={json.dumps(q['bound_parameters'])} · {q['execution_time_ms']} ms[/dim]")


def run_role(role: Role, question: str, show_sql: bool, evidence_only: bool) -> None:
    console.rule(f"[bold]{role.value}")
    console.print(f"[italic]{question}[/italic]\n")
    if evidence_only:
        for call in baseline_evidence(role):
            console.print(f"🔧 [bold]{call.get('evidence_id') or '--'}[/bold] [cyan]{call['name']}[/cyan] "
                          f"{json.dumps(call['arguments'])}")
            if call["error"]:
                console.print(f"   [red]{call['error']}[/red]")
            else:
                show_envelope(call["envelope"], show_sql)
        return

    def on_event(kind: str, data: dict) -> None:
        if kind == "tool_call":
            console.print(f"🔧 [cyan]{data['name']}[/cyan] {json.dumps(data['arguments'])}")
        elif kind == "tool_result":
            console.print(f"   [bold]{data.get('evidence_id') or '--'}[/bold]")
            if data.get("error"):
                console.print(f"   [red]error: {data['error']}[/red]")
            else:
                show_envelope(data["envelope"], show_sql)
        elif kind == "revising":
            console.print("[yellow]✏️  grounding check failed; revising answer[/yellow]")

    with console.status("thinking…"):
        result = run_agent(role, question, on_event=on_event)
    v = result.validation
    style = "green" if v.passed else "yellow"
    console.print(Panel(Markdown(result.answer), title=f"{role.value} answer", border_style=style))
    table = Table(show_header=False, box=None)
    table.add_row("Claim check", f"[{style}]{'passed' if v.passed else 'issues'}[/{style}] "
                  f"verified_number_rate={v.verified_number_rate:.2%} ({v.numbers_verified}/{v.numbers_checked}), "
                  f"{v.cited_sentences}/{v.sentences_checked} sentences cited, {v.elapsed_ms:.1f} ms")
    if v.stripped_sentences:
        table.add_row("Stripped", " | ".join(v.stripped_sentences))
    if v.unverified_numbers:
        table.add_row("Unverified figures", ", ".join(v.unverified_numbers))
    if v.forbidden_claims:
        table.add_row("Business-impact claims", " | ".join(v.forbidden_claims))
    table.add_row("Tool calls", str(len(result.tool_calls)))
    table.add_row("Tokens", f"{result.usage['prompt_tokens']:,} in / {result.usage['completion_tokens']:,} out")
    console.print(table)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--role", default="all", help="Engineer, 'Team Lead', Manager or all")
    ap.add_argument("--question", help="Override the role's default question")
    ap.add_argument("--sql", action="store_true", help="Print every executed SQL statement")
    ap.add_argument("--evidence-only", action="store_true", help="Run the role's tool sequence without the LLM")
    args = ap.parse_args()

    roles = list(Role) if args.role.lower() == "all" else [Role(args.role)]
    for role in roles:
        run_role(role, args.question or DEFAULT_QUESTIONS[role], args.sql, args.evidence_only)


if __name__ == "__main__":
    main()
