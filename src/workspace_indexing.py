"""Validated public workspace-index lifecycle API over SereneDB's partial inverted indexes."""
from __future__ import annotations
import re
import threading
from datetime import datetime, timezone
from typing import Literal

from src.extensions import manage_workspace_index
from src.queries import _parse_time

_ID_RE = re.compile(r"^ws_[A-Za-z0-9_]{1,60}$")
_registry: dict[str, dict] = {}
_lock = threading.RLock()


def create_time_range_index(dataset: Literal["BGL", "OpenStack"], start_time: str, end_time: str,
                            index_name: str) -> dict:
    if dataset not in {"BGL", "OpenStack"}:
        raise ValueError("dataset must be BGL or OpenStack")
    if not isinstance(index_name, str) or not _ID_RE.fullmatch(index_name):
        raise ValueError("index_name must be a simple ws_ identifier")
    start, end = _parse_time(start_time, "start_time"), _parse_time(end_time, "end_time")
    if start is None or end is None or start >= end:
        raise ValueError("start_time and end_time are required and start_time must be earlier")
    expected = f"ws_{'bgl_logs' if dataset == 'BGL' else 'openstack_logs'}_{start:%Y%m%d%H%M%S}_{end:%Y%m%d%H%M%S}"
    if index_name != expected:
        raise ValueError(f"index_name must match the derived safe name: {expected}")
    with _lock:
        if index_name in _registry:
            raise ValueError("workspace index is already registered")
    existing = manage_workspace_index("list").get("rows", [])
    if any(row.get("index_name") == index_name for row in existing):
        raise ValueError("refusing to take ownership of a pre-existing, unregistered index")
    result = manage_workspace_index("create", dataset, start_time, end_time)
    actual = result["rows"][0]["index_name"]
    if index_name != actual:
        raise ValueError(f"index_name must match the derived safe name: {actual}")
    with _lock:
        _registry[index_name] = {"dataset": dataset, "start_time": start_time, "end_time": end_time,
                                 "created_at": datetime.now(timezone.utc).isoformat(), "created_here": True}
    return result


def drop_time_range_index(index_name: str) -> dict:
    if not isinstance(index_name, str) or not _ID_RE.fullmatch(index_name):
        raise ValueError("invalid workspace index identifier")
    with _lock:
        record = _registry.get(index_name)
    if record is None:
        raise ValueError("refusing to drop an index not created and registered by this process")
    result = manage_workspace_index("drop", record["dataset"], record["start_time"], record["end_time"])
    with _lock:
        _registry.pop(index_name, None)
    result["summary"] = result.get("summary") or {}
    result["summary"]["cleanup_time_ms"] = sum(q["execution_time_ms"] for q in result.get("executed_queries", []))
    return result


def registered_workspaces() -> dict[str, dict]:
    with _lock:
        return {k: dict(v) for k, v in _registry.items()}
