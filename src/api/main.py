"""FastAPI application for Polaris log investigations and PDF exports."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import List

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response

from src.api.pdf import render_report
from src.api.schemas import (
    AlertItem,
    ComponentErrorItem,
    InvestigationRequest,
    InvestigationResponse,
    PDFExportRequest,
    RoleType,
    TimeseriesResponse,
)
from src.api.service import ROLE_NAMES, alerts, component_error_counts, investigate, timeseries
from src.db import RoleConfigError, check_connection

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    health = await asyncio.to_thread(check_connection)
    if health.get("status") != "connected":
        raise RuntimeError(f"SereneDB startup health check failed: {health.get('error', 'unknown error')}")
    yield


app = FastAPI(title="Polaris Log Intelligence API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    result = await asyncio.to_thread(check_connection)
    if result.get("status") != "connected":
        raise HTTPException(status_code=503, detail="SereneDB is unavailable")
    return result


def _role_not_configured(role: str, exc: RoleConfigError) -> HTTPException:
    logger.error("Polaris role %s has no database login: %s", role, exc)
    return HTTPException(status_code=503, detail=f"{ROLE_NAMES[role]} database login is not configured (set {exc.env_name}).")


@app.post("/api/investigate", response_model=InvestigationResponse)
async def create_investigation(request: InvestigationRequest) -> InvestigationResponse:
    try:
        return await investigate(request)
    except RoleConfigError as exc:
        raise _role_not_configured(request.role, exc) from exc
    except Exception as exc:
        logger.exception("Polaris investigation failed")
        raise HTTPException(status_code=502, detail="Investigation failed. Check API configuration and server logs.") from exc


async def _role_metric(fn, role: str, label: str):
    try:
        return await asyncio.to_thread(fn, role)
    except RoleConfigError as exc:
        raise _role_not_configured(role, exc) from exc
    except Exception as exc:
        logger.exception("Polaris %s failed", label)
        raise HTTPException(status_code=502, detail=f"{label.capitalize()} unavailable. Check server logs.") from exc


@app.get("/api/metrics/components", response_model=List[ComponentErrorItem])
async def get_component_metrics(role: RoleType) -> List[ComponentErrorItem]:
    return await _role_metric(component_error_counts, role, "component metrics")


@app.get("/api/metrics/timeseries", response_model=TimeseriesResponse)
async def get_timeseries(role: RoleType) -> TimeseriesResponse:
    return await _role_metric(timeseries, role, "timeseries")


@app.get("/api/alerts", response_model=List[AlertItem])
async def get_alerts(role: RoleType) -> List[AlertItem]:
    return await _role_metric(alerts, role, "alerts")


@app.post("/api/reports/pdf")
async def create_pdf_report(request: PDFExportRequest) -> Response:
    try:
        content = await asyncio.to_thread(render_report, request)
    except Exception as exc:
        logger.exception("Polaris PDF generation failed")
        raise HTTPException(status_code=500, detail="PDF generation failed.") from exc
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="polaris-report.pdf"'},
    )
