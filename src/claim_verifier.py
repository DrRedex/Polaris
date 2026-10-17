"""
claim_verifier.py

Deterministic, sentence-level verification of an agent answer against the evidence it cited.

Every tool result carries an evidence_id (EV-01, EV-02, ...). The role prompts require each
factual sentence, bullet or table row to cite its source as [EV-XX]. For each such unit:

  * every number is looked up in the cited evidence payload only (not in other evidence);
    rounding to the precision written, k/M/B and million/billion suffixes and percent-as-
    fraction are accepted; "(derived: ...)" figures and list ordinals are exempt
  * numbers in a unit without a citation are counted as unverified ("uncited")
  * verified_number_rate = verified numbers / checked numbers (1.0 when nothing to check)

For the Manager role, sentences that make business-impact claims ($, revenue, financial,
customer impact, SLA breach, downtime %) are flagged unless they state the data is absent,
or the term actually occurs in returned rows. mode="strip" removes them from cleaned_text.

No model calls, no database access: typically well under 5 ms for a full answer.
"""

import ast
import bisect
import operator
import re
import time
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from src.validator import ANSWER_NUMBER_RE, DERIVED_RE, SUFFIX, _evidence_numbers

CITATION_RE = re.compile(r"\[\s*(EV-\d+(?:\s*[,;/&]\s*(?:EV-)?\d+)*)[^\]]*\]", re.IGNORECASE)
EV_ID_RE = re.compile(r"EV-?(\d+)", re.IGNORECASE)
TABLE_SEPARATOR_RE = re.compile(r"^\s*\|?\s*:?-{3,}")
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z(\[*_`\"'])")
# Dates written in prose ("May 17, 2017", "17 May 2017") are not quantities.
_MONTHS = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
PROSE_DATE_RE = re.compile(rf"\b{_MONTHS}\s+\d{{1,2}}(?:st|nd|rd|th)?(?:,?\s+\d{{4}})?\b"
                           rf"|\b\d{{1,2}}(?:st|nd|rd|th)?\s+{_MONTHS}(?:\s+\d{{4}})?\b", re.IGNORECASE)
DERIVED_NUMBER_RE = re.compile(r"(?<![\w.])\d+(?:\.\d+)?")
LIST_MARKER_RE = re.compile(r"^\s*(?:#+\s*)?(?:\d+[.)]|[-*+])\s+")

FORBIDDEN_CONCEPTS = {
    "currency ($)": re.compile(r"\$\s?\d|\$\s?[\d.,]+\s?[kmb]|[€£]\s?\d"),
    "revenue": re.compile(r"\brevenue\b", re.IGNORECASE),
    "financial": re.compile(r"\bfinancial(?:ly)?\b", re.IGNORECASE),
    "customer impact": re.compile(r"\bcustomer[s']*\s+(?:impact|affected|impacted|churn)\b", re.IGNORECASE),
    "SLA breach": re.compile(r"\bSLA\s+(?:breach|penalt|violation)", re.IGNORECASE),
    "downtime %": re.compile(r"\bdowntime\b[^.]{0,30}\d+(?:\.\d+)?\s*%|\d+(?:\.\d+)?\s*%[^.]{0,30}\bdowntime\b"
                             r"|\b(?:hours?|minutes?)\s+of\s+downtime\b|\bdowntime\s+of\s+\d", re.IGNORECASE),
}
# Headings whose bullets list what is NOT known: their sentences are disclaimers, not claims.
DISCLAIMER_HEADING_RE = re.compile(r"not establish|do(?:es)? not|cannot|unknown|limitation|scope|boundar|excluded|"
                                   r"not in (?:the )?logs|out of scope", re.IGNORECASE)
HEADING_RE = re.compile(r"^\s*#{1,6}\s+(.*)")
# One cheap scan decides whether a sentence needs the individual concept patterns at all.
FORBIDDEN_PREFILTER_RE = re.compile(r"[$\u20ac\u00a3]|revenue|financ|customer|\bsla\b|downtime", re.IGNORECASE)
NEGATION_RE = re.compile(r"\b(?:not|no|never|cannot|can't|excluded|exclude|isn't|aren't|absent|without|"
                         r"unavailable|none|unknown|outside)\b|not_in_logs", re.IGNORECASE)


class NumberCheck(BaseModel):
    number: str
    verified: bool
    cited: list[str]
    reason: str  # "matched", "derived", "uncited", "not_in_cited_evidence", "unknown_evidence_id"
    sentence: str


class ClaimReport(BaseModel):
    passed: bool
    no_numeric_claims: bool = Field(False, description="True when the answer states no checkable numbers")
    verified_number_rate: float = Field(description="verified numbers / checked numbers, 1.0 when none")
    numbers_checked: int
    numbers_verified: int
    sentences_checked: int
    cited_sentences: int
    uncited_numeric_sentences: int
    unknown_evidence_ids: list[str]
    unverified_numbers: list[str]
    forbidden_claims: list[str]
    stripped_sentences: list[str]
    checks: list[NumberCheck]
    cleaned_text: str
    elapsed_ms: float

    def feedback(self) -> str:
        parts = []
        bad = [c for c in self.checks if not c.verified]
        if bad:
            details = "; ".join(f"{c.number} ({c.reason.replace('_', ' ')}"
                                + (f" {', '.join(c.cited)}" if c.cited else "") + ")" for c in bad[:15])
            parts.append(f"These figures are not supported by the evidence they cite: {details}. Cite the "
                         "[EV-XX] that contains each figure, mark computed values as (derived: ...), or remove them.")
        if self.unknown_evidence_ids:
            parts.append(f"These evidence IDs do not exist: {', '.join(self.unknown_evidence_ids)}.")
        if self.forbidden_claims:
            parts.append("These sentences make business-impact claims the logs cannot support: "
                         + " | ".join(self.forbidden_claims) + ". Remove them.")
        return " ".join(parts)


# Sorted number arrays per evidence item, keyed by (evidence_id, id(payload)) so a ledger
# that is verified several times (e.g. before and after a revision) is only walked once.
_number_cache: dict[tuple[str, int], tuple[Any, list[float]]] = {}


def _numbers_for(evidence_id: str, payload: Any) -> list[float]:
    key = (evidence_id, id(payload))
    hit = _number_cache.get(key)
    if hit is not None and hit[0] is payload:
        return hit[1]
    numbers = sorted(set(_evidence_numbers([payload])))
    if len(_number_cache) > 512:
        _number_cache.clear()
    _number_cache[key] = (payload, numbers)
    return numbers


_ARITH_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv}
_ARITH_CHARS_RE = re.compile(r"[^0-9.+\-*/()\u00d7\u00f7]+")


def _arith(node: ast.AST) -> float:
    """Evaluates numbers and + - * / only; anything else raises ValueError (no eval())."""
    if isinstance(node, ast.Expression):
        return _arith(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)
    if isinstance(node, ast.BinOp) and type(node.op) in _ARITH_OPS:
        return _ARITH_OPS[type(node.op)](_arith(node.left), _arith(node.right))
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return -_arith(node.operand)
    raise ValueError("unsupported expression")


def _derived_values(unit: str) -> list[float]:
    """Numbers written in a unit's (derived: ...) notes plus the results of their arithmetic,
    e.g. '(derived: 720 seconds / 60 seconds per minute)' yields 720, 60 and 12."""
    values: list[float] = []
    for note in DERIVED_RE.findall(unit):
        values.extend(float(n) for n in DERIVED_NUMBER_RE.findall(note))
        body = note.split(":", 1)[-1].rstrip()
        body = body[:-1] if body.endswith(")") else body  # drop the "(derived: ...)" wrapper's own paren
        for segment in re.split(r"[=\u2248,;]", body):
            expr = _ARITH_CHARS_RE.sub(" ", segment.replace(",", "")).replace("\u00d7", "*").replace("\u00f7", "/")
            expr = re.sub(r"\s+", " ", expr).strip()
            if not re.search(r"\d\s*[-+*/]\s*[\d(]", expr) or len(expr) > 120:
                continue
            try:
                values.append(_arith(ast.parse(expr.replace(" ", ""), mode="eval")))
            except (ValueError, SyntaxError, ZeroDivisionError, RecursionError):
                continue
    return sorted(values)


def index_evidence(evidence_id: str, payload: Any) -> None:
    """Pre-computes an evidence item's numbers when the tool result arrives, so verifying an
    answer later only does lookups."""
    _numbers_for(evidence_id, payload)


def _contains(numbers: list[float], target: float, tolerance: float) -> bool:
    i = bisect.bisect_left(numbers, target - tolerance)
    return i < len(numbers) and numbers[i] <= target + tolerance


def _number_ok(match: re.Match, numbers: list[float]) -> bool:
    whole, frac, suffix = match.group(1), match.group(2) or "", (match.group(3) or "").lower()
    value = float(whole.replace(",", "") + frac)
    decimals = len(frac) - 1 if frac else 0
    scale = SUFFIX.get(suffix, 1.0)
    if _contains(numbers, value * scale, 0.5 * 10 ** (-decimals) * scale + 1e-9):
        return True
    if suffix == "%":  # 12.5% may be stored as the fraction 0.125
        return _contains(numbers, value / 100, 0.5 * 10 ** (-(decimals + 2)) + 1e-12)
    return False


def _normalize_id(raw: str) -> str:
    return f"EV-{int(raw):02d}"


def _units(text: str) -> list[tuple[str, bool, bool, str]]:
    """Splits the answer into checkable units: table rows and sentences.
    Returns (unit, is_table_row, under_disclaimer_heading, containing_line)."""
    units = []
    lines = text.splitlines()
    disclaimer = False
    for i, line in enumerate(lines):
        heading = HEADING_RE.match(line)
        if heading:
            disclaimer = bool(DISCLAIMER_HEADING_RE.search(heading.group(1)))
        if not line.strip() or TABLE_SEPARATOR_RE.match(line):
            continue
        if line.lstrip().startswith("|"):
            # A header row (followed by |---|) holds column labels such as "Bursts (30 min)", not claims.
            if i + 1 < len(lines) and TABLE_SEPARATOR_RE.match(lines[i + 1]):
                continue
            units.append((line, True, disclaimer, line))
        else:
            units.extend((s, False, disclaimer, line) for s in SENTENCE_SPLIT_RE.split(line) if s.strip())
    return units


def _row_strings(ledger: dict[str, Any]) -> str:
    """Lower-cased text of all returned rows, to allow concepts that genuinely occur in data."""
    out = []
    for payload in ledger.values():
        for row in (payload or {}).get("rows", []) or []:
            out.extend(str(v).lower() for v in row.values() if isinstance(v, str))
    return "\n".join(out)


def verify_response_claims(response_text: str, evidence_ledger: dict[str, Any], role: str,
                           mode: Literal["warn", "strip"] = "warn") -> ClaimReport:
    """Verifies every number against the evidence it cites; flags (or strips) Manager business claims."""
    t0 = time.perf_counter()
    ledger = {_normalize_id(EV_ID_RE.search(k).group(1)) if EV_ID_RE.search(k) else k: v
              for k, v in (evidence_ledger or {}).items()}
    role_name = getattr(role, "value", role)
    checks: list[NumberCheck] = []
    unknown_ids: set[str] = set()
    forbidden: list[str] = []
    cited_units = uncited_numeric = 0
    table_citations: list[str] = []  # a table caption's citation covers its rows
    rows_text: Optional[str] = None
    units = _units(response_text or "")

    for unit, is_row, under_disclaimer, line in units:
        cited = [_normalize_id(n) for group in CITATION_RE.findall(unit) for n in re.findall(r"\d+", group)]
        if not cited and not is_row:
            # A citation at the end of a bullet or paragraph line covers all its sentences.
            cited = [_normalize_id(n) for group in CITATION_RE.findall(line) for n in re.findall(r"\d+", group)]
        cited = list(dict.fromkeys(cited))
        # Numbers stated in the text and shown as the result of a (derived: ...) calculation.
        derived_values = _derived_values(unit) if "(derived" in unit.lower() else []
        if not is_row:
            table_citations = cited or table_citations if unit.rstrip().endswith(":") else cited
        elif not cited:
            cited = table_citations
        if cited:
            cited_units += 1

        body = PROSE_DATE_RE.sub(" ", DERIVED_RE.sub(" ", CITATION_RE.sub(" ", unit)))
        body = LIST_MARKER_RE.sub(" ", body)
        numeric = False
        for m in ANSWER_NUMBER_RE.finditer(body):
            if not m.group(2) and not m.group(3) and float(m.group(1).replace(",", "")) <= 10:
                continue  # ordinals and small counts
            numeric = True
            token = m.group(0).strip()
            if derived_values and _number_ok(m, derived_values):
                checks.append(NumberCheck(number=token, verified=True, cited=cited, reason="derived",
                                          sentence=unit.strip()[:200]))
                continue
            if not cited:
                checks.append(NumberCheck(number=token, verified=False, cited=[], reason="uncited", sentence=unit.strip()[:200]))
                continue
            known = [c for c in cited if c in ledger]
            unknown_ids.update(c for c in cited if c not in ledger)
            if not known:
                checks.append(NumberCheck(number=token, verified=False, cited=cited, reason="unknown_evidence_id",
                                          sentence=unit.strip()[:200]))
                continue
            ok = any(_number_ok(m, _numbers_for(c, ledger[c])) for c in known)
            checks.append(NumberCheck(number=token, verified=ok, cited=cited,
                                      reason="matched" if ok else "not_in_cited_evidence", sentence=unit.strip()[:200]))
        if numeric and not cited:
            uncited_numeric += 1

        if role_name == "Manager" and FORBIDDEN_PREFILTER_RE.search(unit):
            hits = [name for name, rx in FORBIDDEN_CONCEPTS.items() if rx.search(unit)]
            if hits and not NEGATION_RE.search(unit) and not under_disclaimer:
                if rows_text is None:
                    rows_text = _row_strings(ledger)
                # Allowed only if the concept's own words occur in returned data rows.
                if not all(re.sub(r"[^a-z ]", "", h.lower()).strip() and
                           re.sub(r"[^a-z ]", "", h.lower()).strip() in rows_text for h in hits):
                    forbidden.append(unit.strip()[:300])

    stripped: list[str] = []
    cleaned = response_text or ""
    if mode == "strip" and forbidden:
        for sentence in forbidden:
            if sentence in cleaned:
                cleaned = cleaned.replace(sentence, "").replace("  ", " ")
                stripped.append(sentence)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)

    verified = sum(c.verified for c in checks)
    rate = round(verified / len(checks), 4) if checks else 1.0
    remaining_forbidden = [f for f in forbidden if f not in stripped]
    return ClaimReport(
        passed=rate == 1.0 and not remaining_forbidden and not unknown_ids,
        no_numeric_claims=not checks,
        verified_number_rate=rate,
        numbers_checked=len(checks),
        numbers_verified=verified,
        sentences_checked=len(units),
        cited_sentences=cited_units,
        uncited_numeric_sentences=uncited_numeric,
        unknown_evidence_ids=sorted(unknown_ids),
        unverified_numbers=list(dict.fromkeys(c.number for c in checks if not c.verified)),
        forbidden_claims=forbidden,
        stripped_sentences=stripped,
        checks=checks,
        cleaned_text=cleaned,
        elapsed_ms=round((time.perf_counter() - t0) * 1000, 3),
    )
