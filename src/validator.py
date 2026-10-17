"""
validator.py

Post-checks an agent answer against the evidence it actually retrieved:
  1. Every number in the answer must match a number in some tool result (allowing for
     rounding to the precision shown and k/M/B suffixes). Figures marked
     "(derived: ...)" and small list ordinals are exempt.
  2. Manager answers must not make revenue / cost / SLA / customer-impact / downtime
     claims. Sentences that state such things are *not* recorded are allowed.
"""

import json
import re
from typing import Any

from pydantic import BaseModel

from src.prompts import Role

# A number not glued to letters, '-' or '.' on the left (skips p95, Tier-1, R02-M1, 0x1b),
# with an optional k/M/B suffix or percent sign.
ANSWER_NUMBER_RE = re.compile(r"(?<![\w.\-:/])(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?\s?([kKmMbB]\b|%)?(?![\w\-:])")
EVIDENCE_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
DERIVED_RE = re.compile(r"\(derived:[^)]*\)", re.IGNORECASE)
CITATION_RE = re.compile(r"\[[a-z_]+:[^\]]*\]")
SUFFIX = {"k": 1e3, "m": 1e6, "b": 1e9}

FORBIDDEN_RE = re.compile(
    r"[$€£]\s?\d|\b(?:usd|eur|dollars?|euros?|revenue|profit|financial (?:loss|impact)|cost of|costs? \w+ (?:per|an?) "
    r"|sla (?:breach|penalt|violation)|penalt(?:y|ies)|downtime of|hours? of downtime|uptime of|lost sales"
    r"|customers? (?:affected|impacted|churn|complaints?)|customer impact|business impact of \d)",
    re.IGNORECASE)
NEGATION_RE = re.compile(r"\b(?:not|no|never|cannot|can't|excluded|exclude|isn't|aren't|absent|without|unavailable|none)\b",
                         re.IGNORECASE)


class ValidationReport(BaseModel):
    passed: bool
    numbers_checked: int
    unverified_numbers: list[str]
    forbidden_claims: list[str]

    def feedback(self) -> str:
        parts = []
        if self.unverified_numbers:
            parts.append("These figures do not match any tool result: " + ", ".join(self.unverified_numbers)
                         + ". Cite the exact tool figure, mark computed values as (derived: ...), or remove them.")
        if self.forbidden_claims:
            parts.append("These sentences make business-impact claims that the logs cannot support: "
                         + " | ".join(self.forbidden_claims) + ". Remove them.")
        return " ".join(parts)


def _evidence_numbers(envelopes: list[Any]) -> list[float]:
    text = json.dumps(envelopes, default=str)
    return [float(n) for n in EVIDENCE_NUMBER_RE.findall(text)]


def _matches(value: float, decimals: int, scale: float, evidence: list[float]) -> bool:
    # Accept any evidence value that rounds to what was written, e.g. "4.7M" matches
    # 4747963 and "60.8%" matches 60.81. Percentages are also checked as fractions.
    tolerance = 0.5 * 10 ** (-decimals) * scale + 1e-9
    target = value * scale
    return any(abs(e - target) <= tolerance for e in evidence)


def validate_answer(role: Role, answer: str, envelopes: list[Any]) -> ValidationReport:
    evidence = _evidence_numbers(envelopes)
    scrubbed = DERIVED_RE.sub(" ", answer)
    # Strip headings like "## 1." and list markers so ordinals are not counted as claims.
    scrubbed = re.sub(r"^\s*(?:#+\s*)?\d+[.)]\s", " ", scrubbed, flags=re.MULTILINE)

    unverified: list[str] = []
    checked = 0
    for m in ANSWER_NUMBER_RE.finditer(scrubbed):
        whole, frac, suffix = m.group(1), m.group(2) or "", (m.group(3) or "").lower()
        value = float(whole.replace(",", "") + frac)
        decimals = len(frac) - 1 if frac else 0
        if not frac and not suffix and value <= 10:
            continue  # ordinals, "top 3", small counts are not worth flagging
        checked += 1
        scale = SUFFIX.get(suffix, 1.0)
        ok = _matches(value, decimals, scale, evidence)
        if not ok and suffix == "%":
            ok = _matches(value / 100, decimals + 2, 1.0, evidence)
        if not ok:
            unverified.append(m.group(0).strip())

    forbidden: list[str] = []
    if role == Role.MANAGER:
        for sentence in re.split(r"(?<=[.!?])\s+|\n+", CITATION_RE.sub("", answer)):
            if FORBIDDEN_RE.search(sentence) and not NEGATION_RE.search(sentence):
                forbidden.append(sentence.strip()[:200])

    unverified = list(dict.fromkeys(unverified))
    return ValidationReport(
        passed=not unverified and not forbidden,
        numbers_checked=checked,
        unverified_numbers=unverified,
        forbidden_claims=forbidden,
    )
