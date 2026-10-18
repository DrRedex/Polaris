from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


RoleType = Literal["ENG", "LEAD", "MGR"]
SeverityType = Literal["ERROR", "WARN", "INFO"]


class StatModel(BaseModel):
    label: str
    value: str
    unit: str
    note: str
    explain: str


class FindingModel(BaseModel):
    text: str
    cites: List[int]


class HypothesisModel(FindingModel):
    tag: str


class RoleContentResponse(BaseModel):
    title: str
    sub: str
    stats: List[StatModel]
    facts: List[FindingModel]
    hyps: List[HypothesisModel]
    actions: List[str]


class EvidenceRowModel(BaseModel):
    n: int
    ts: str
    trace: str
    sev: SeverityType
    comp: str
    body: str
    # 1-based index into InvestigationResponse.queries of the query that returned this row (0 = unknown).
    q: int = 0


class ExecutedQueryModel(BaseModel):
    title: str
    meta: str
    sql: str


class InvestigationRequest(BaseModel):
    role: RoleType
    question: str = Field(min_length=1, max_length=4000)
    # No default window: the OpenStack corpus spans 2017-05-14..17 with gaps, so a fixed hour can be empty.
    time_window_start: Optional[str] = None
    time_window_end: Optional[str] = None

    @model_validator(mode="after")
    def validate_time_window(self):
        from datetime import datetime

        parsed = []
        for value in (self.time_window_start, self.time_window_end):
            if value is None:
                parsed.append(None)
                continue
            try:
                parsed.append(datetime.fromisoformat(value.replace("Z", "+00:00")))
            except ValueError as exc:
                raise ValueError("time window values must be ISO-8601 timestamps") from exc
        if all(value is not None for value in parsed) and parsed[0] >= parsed[1]:
            raise ValueError("time_window_start must be earlier than time_window_end")
        return self


class InvestigationResponse(BaseModel):
    content: RoleContentResponse
    evidence: List[EvidenceRowModel]
    queries: List[ExecutedQueryModel]


class TimeseriesPoint(BaseModel):
    ts: str
    val: float
    anomaly: bool


class TimeseriesTableRow(BaseModel):
    name: str
    val: Optional[float] = None
    note: str


class TimeseriesResponse(BaseModel):
    title: str
    unit: str
    # Bucket width; a jump larger than this between points is a gap in the corpus.
    step_minutes: int
    points: List[TimeseriesPoint]
    baseline: Optional[float] = None
    threshold: Optional[float] = None
    peak: Optional[float] = None
    note: str
    # Used when the role has no time column (Manager): one value per component instead of a series.
    table: List[TimeseriesTableRow] = []


class ComponentErrorItem(BaseModel):
    name: str
    # ERROR + CRITICAL lines.
    n: int
    warnings: int = 0
    note: str


class AlertItem(BaseModel):
    sev: SeverityType
    title: str
    detail: str
    time: str
    q: str


class PDFExportRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    role: RoleType
    question: str = Field(min_length=1, max_length=4000)
    window: dict[str, str]
    citedRows: List[int]
    content: RoleContentResponse
    evidence: List[EvidenceRowModel]
    queries: List[ExecutedQueryModel]

    @model_validator(mode="after")
    def validate_citations(self):
        available = {row.n for row in self.evidence}
        missing = set(self.citedRows) - available
        if missing:
            raise ValueError(f"citedRows contains unknown evidence row(s): {sorted(missing)}")
        for finding in [*self.content.facts, *self.content.hyps]:
            unknown = set(finding.cites) - available
            if unknown:
                raise ValueError(f"finding cites unknown evidence row(s): {sorted(unknown)}")
        return self
