/**
 * Typed client for the Polaris FastAPI backend (src/api/main.py).
 * Shapes mirror src/api/schemas.py, so responses are used as-is.
 */

export const API_BASE = (
  process.env.NEXT_PUBLIC_API_URL ||
  process.env.NEXT_PUBLIC_POLARIS_API_URL ||
  'http://127.0.0.1:8000'
).replace(/\/$/, '');

/* ---- types ---- */

export type RoleKey = 'ENG' | 'LEAD' | 'MGR';
export type Severity = 'ERROR' | 'WARN' | 'INFO';

export interface Stat {
  label: string;
  value: string;
  unit: string;
  note: string;
  explain: string;
}
export interface Finding {
  text: string;
  /** 1-based `EvidenceRow.n` values. */
  cites: number[];
}
export interface Hypothesis extends Finding {
  tag: string;
}
export interface RoleContent {
  title: string;
  sub: string;
  stats: Stat[];
  facts: Finding[];
  hyps: Hypothesis[];
  actions: string[];
}
export interface EvidenceRow {
  n: number;
  ts: string;
  trace: string;
  sev: Severity;
  comp: string;
  body: string;
  /** 1-based index into `InvestigationResult.queries` of the query that returned this row (0 = unknown). */
  q?: number;
}
export interface ExecutedQuery {
  title: string;
  /** Pre-formatted by the backend, e.g. "12 rows, 34.5 ms". */
  meta: string;
  sql: string;
}
export interface InvestigationResult {
  content: RoleContent;
  evidence: EvidenceRow[];
  queries: ExecutedQuery[];
}
export interface ComponentMetric {
  name: string;
  /** ERROR + CRITICAL lines. */
  n: number;
  warnings: number;
  note: string;
}
export interface TimeseriesPoint {
  /** ISO timestamp without zone, e.g. "2017-05-16T03:00:00". */
  ts: string;
  val: number;
  anomaly: boolean;
}
export interface TimeseriesTableRow {
  name: string;
  val: number | null;
  note: string;
}
export interface Timeseries {
  title: string;
  unit: string;
  /** Bucket width; a larger jump between points is a gap in the corpus. 0 when there is no series. */
  step_minutes: number;
  points: TimeseriesPoint[];
  baseline: number | null;
  threshold: number | null;
  peak: number | null;
  note: string;
  /** Manager only: one corpus-wide value per component instead of a series. */
  table: TimeseriesTableRow[];
}
export interface AlertItem {
  sev: Severity;
  title: string;
  detail: string;
  /** Short "MM-DD HH:MM", or "" for corpus-wide alerts. */
  time: string;
  /** Question for "Ask Polaris about this". */
  q: string;
}

export interface InvestigationRequest {
  role: RoleKey;
  question: string;
  time_window_start?: string;
  time_window_end?: string;
}
export interface PdfReportRequest extends InvestigationResult {
  role: RoleKey;
  question: string;
  /** `{ from, to }`, or `{}` for the full corpus. */
  window: { from?: string; to?: string };
  citedRows: number[];
}

/* ---- errors ---- */

export class ApiError extends Error {
  readonly status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

/** FastAPI sends `detail` as a string (HTTPException) or an array of issues (422). */
function detailMessage(body: unknown, fallback: string): string {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    const parts = detail
      .map((d) => (d && typeof d === 'object' && 'msg' in d ? String((d as { msg: unknown }).msg) : ''))
      .filter(Boolean);
    if (parts.length) return parts.join('; ');
  }
  return fallback;
}

async function send(path: string, init: RequestInit = {}): Promise<Response> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, init);
  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError') throw err;
    throw new ApiError(`Cannot reach the Polaris API at ${API_BASE}. Is the backend running?`, 0);
  }
  if (!res.ok) {
    const body: unknown = await res.json().catch(() => null);
    throw new ApiError(detailMessage(body, `Request failed (HTTP ${res.status}).`), res.status);
  }
  return res;
}

function postJson(path: string, payload: unknown, signal?: AbortSignal): Promise<Response> {
  return send(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
    signal,
  });
}

/* ---- endpoints ---- */

export async function postInvestigation(req: InvestigationRequest, signal?: AbortSignal): Promise<InvestigationResult> {
  return (await postJson('/api/investigate', req, signal)).json();
}

export async function getComponentMetrics(role: RoleKey, signal?: AbortSignal): Promise<ComponentMetric[]> {
  return (await send(`/api/metrics/components?role=${encodeURIComponent(role)}`, { signal })).json();
}

export async function getTimeseries(role: RoleKey, signal?: AbortSignal): Promise<Timeseries> {
  return (await send(`/api/metrics/timeseries?role=${encodeURIComponent(role)}`, { signal })).json();
}

export async function getAlerts(role: RoleKey, signal?: AbortSignal): Promise<AlertItem[]> {
  return (await send(`/api/alerts?role=${encodeURIComponent(role)}`, { signal })).json();
}

export async function postPdfReport(req: PdfReportRequest, signal?: AbortSignal): Promise<Blob> {
  return (await postJson('/api/reports/pdf', req, signal)).blob();
}

/** Saves a Blob through a temporary anchor. */
export function downloadBlob(blob: Blob, filename: string): void {
  const url = window.URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.URL.revokeObjectURL(url);
}
