"""ReportLab renderer for exported Polaris investigations."""

from __future__ import annotations

from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
    Preformatted,
    KeepTogether,
)

from src.api.schemas import PDFExportRequest


def render_report(request: PDFExportRequest) -> bytes:
    output = BytesIO()
    document = SimpleDocTemplate(
        output,
        pagesize=letter,
        rightMargin=0.65 * inch,
        leftMargin=0.65 * inch,
        topMargin=0.62 * inch,
        bottomMargin=0.62 * inch,
        title="Polaris Log Investigation",
        author="Polaris Log Intelligence",
    )
    base = getSampleStyleSheet()
    title_style = ParagraphStyle("PolarisTitle", parent=base["Title"], textColor=colors.HexColor("#172554"), alignment=TA_LEFT)
    heading_style = ParagraphStyle("PolarisHeading", parent=base["Heading2"], textColor=colors.HexColor("#1e3a8a"), spaceBefore=12, spaceAfter=6)
    body_style = ParagraphStyle("PolarisBody", parent=base["BodyText"], leading=14, spaceAfter=5)
    small_style = ParagraphStyle("PolarisSmall", parent=base["BodyText"], fontSize=8, leading=10, wordWrap="CJK")
    story = [
        Paragraph("Polaris Log Investigation", title_style),
        Paragraph(f"Role: {escape(request.role)}", body_style),
        Paragraph(f"Question: {escape(request.question)}", body_style),
        Paragraph(
            f"Time window: {escape(request.window.get('from', 'start'))} to {escape(request.window.get('to', 'end'))}"
            if request.window else "Time window: full corpus (no time filter)", body_style,
        ),
        Spacer(1, 8),
        Paragraph(escape(request.content.title), heading_style),
        Paragraph(escape(request.content.sub), body_style),
    ]

    story.append(Paragraph("At a glance", heading_style))
    stats = [["Measure", "Value", "Note"]]
    stats.extend([[s.label, f"{s.value} {s.unit}".strip(), s.note] for s in request.content.stats])
    stats_table = Table(stats, colWidths=[1.55 * inch, 1.15 * inch, 4.45 * inch], repeatRows=1, hAlign="LEFT")
    stats_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#dbeafe")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#172554")),
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#cbd5e1")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.extend([stats_table, Paragraph("Observed facts", heading_style)])
    for finding in request.content.facts:
        citations = " ".join(f"[{n}]" for n in finding.cites)
        story.append(Paragraph(f"{escape(finding.text)} {escape(citations)}", body_style))

    story.append(Paragraph("Hypotheses", heading_style))
    for finding in request.content.hyps:
        citations = " ".join(f"[{n}]" for n in finding.cites)
        story.append(Paragraph(f"<b>{escape(finding.tag)}:</b> {escape(finding.text)} {escape(citations)}", body_style))

    story.append(Paragraph("Recommended actions", heading_style))
    for action in request.content.actions:
        story.append(Paragraph(f"• {escape(action)}", body_style))

    cited = set(request.citedRows)
    cited.update(n for finding in [*request.content.facts, *request.content.hyps] for n in finding.cites)
    cited_evidence = [row for row in request.evidence if row.n in cited]
    story.append(Paragraph("Cited log evidence", heading_style))
    if cited_evidence:
        for row in cited_evidence:
            story.append(KeepTogether([
                Paragraph(f"<b>[{row.n}] {escape(row.ts)} — {escape(row.sev)} — {escape(row.comp)}</b>", body_style),
                Paragraph(f"Trace: {escape(row.trace)}", small_style),
                Paragraph(escape(row.body), body_style),
                Spacer(1, 4),
            ]))
    else:
        story.append(Paragraph("No log rows were cited by the analysis.", body_style))

    story.append(Paragraph("Executed SereneDB queries", heading_style))
    if request.queries:
        for query in request.queries:
            story.append(Paragraph(f"<b>{escape(query.title)}</b> — {escape(query.meta)}", body_style))
            story.append(Preformatted(query.sql, small_style, maxLineLength=100))
            story.append(Spacer(1, 7))
    else:
        story.append(Paragraph("No SQL query details were returned.", body_style))

    document.build(story)
    return output.getvalue()
