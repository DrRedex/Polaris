"""
prepare_logs.py

Normalizes raw Loghub BGL and OpenStack .log files into structured CSV files that
SereneDB loads with COPY (see sql/02_load_base_corpus.sql).

Usage:
    python scripts/prepare_logs.py                      # defaults: data/raw -> data/processed
    python scripts/prepare_logs.py --bgl data/raw/BGL_2k.log --openstack data/raw/OpenStack_2k.log

Output columns match the table definitions in sql/01_schema_and_views.sql exactly,
in the same order, so COPY ... (FORMAT CSV, HEADER TRUE) needs no column mapping.
"""

import argparse
import csv
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

BGL_COLUMNS = [
    "line_id", "label", "is_anomaly", "epoch_ts", "event_time", "date_day",
    "node_id", "subsystem", "component", "severity", "message",
]

OPENSTACK_COLUMNS = [
    "line_id", "source_file", "log_file", "event_time", "process_id", "severity",
    "component", "logger", "request_id", "user_id", "tenant_id", "instance_id",
    "http_method", "http_path", "http_route", "http_status", "response_bytes", "latency_sec",
    "anomaly_label", "is_anomaly", "message",
]

# <log_file> <date> <time> <pid> <level> <logger> [<context>] <content>
# Context and content are optional: some ERROR lines (oslo_service.periodic_task) end at the logger.
OPENSTACK_LINE_RE = re.compile(
    r"^(?P<log_file>\S+)\s+(?P<date>\d{4}-\d{2}-\d{2})\s+(?P<time>\d{2}:\d{2}:\d{2}(?:\.\d+)?)\s+"
    r"(?P<pid>\d+)\s+(?P<level>[A-Z]+)\s+(?P<logger>\S+)(?:\s+(?:\[(?P<ctx>[^\]]*)\]\s*)?(?P<content>.*))?$"
)
HTTP_RE = re.compile(
    r'"(?P<method>GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\s+(?P<path>\S+)\s+HTTP/[\d.]+"\s+'
    r"status:\s*(?P<status>\d+)\s+len:\s*(?P<len>\d+)\s+time:\s*(?P<time>[\d.]+)"
)
INSTANCE_RE = re.compile(r"\[instance:\s*(?P<id>[0-9a-f\-]{36})\]")
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
# Tenant IDs (32 hex) and instance UUIDs in URL paths, collapsed so routes group together.
PATH_ID_RE = re.compile(r"(?<=/)(?:[0-9a-f]{32}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})(?=/|$|\?)")
SERVICE_RE = re.compile(r"^(nova-[a-z]+)")

# Fallback when the log file name does not carry the service name.
LOGGER_TO_COMPONENT = [
    ("nova.osapi_compute", "nova-api"),
    ("nova.metadata", "nova-api"),
    ("nova.api", "nova-api"),
    ("nova.scheduler", "nova-scheduler"),
    ("nova.compute", "nova-compute"),
    ("nova.virt", "nova-compute"),
]


def _none_if_null(value: str) -> str:
    return "" if value in ("", "-", "NULL") else value


def parse_bgl(src: Path, dst: Path) -> int:
    """BGL line: <label> <epoch> <date> <node> <time> <node_repeat> <type> <component> <level> <content>"""
    rows = 0
    skipped = 0
    with src.open("r", encoding="utf-8", errors="replace") as fin, \
            dst.open("w", encoding="utf-8", newline="") as fout:
        writer = csv.writer(fout)
        writer.writerow(BGL_COLUMNS)
        for line_id, raw in enumerate(fin, start=1):
            parts = raw.rstrip("\r\n").split(" ", 9)
            if len(parts) < 9:
                skipped += 1
                continue
            parts += [""] * (10 - len(parts))
            label, epoch, _date, node, ts, _node_repeat, subsystem, component, level, content = parts

            try:
                epoch_ts = int(epoch)
            except ValueError:
                skipped += 1
                continue
            # The per-line timestamp has microsecond precision (cluster-local time);
            # fall back to the UTC epoch column if it is malformed.
            try:
                event_time = datetime.strptime(ts, "%Y-%m-%d-%H.%M.%S.%f")
            except ValueError:
                event_time = datetime.fromtimestamp(epoch_ts, tz=timezone.utc).replace(tzinfo=None)

            writer.writerow([
                line_id,
                label,
                "false" if label == "-" else "true",
                epoch_ts,
                event_time.isoformat(sep=" "),
                event_time.date().isoformat(),
                _none_if_null(node),
                _none_if_null(subsystem),
                _none_if_null(component),
                _none_if_null(level),
                content,
            ])
            rows += 1
    if skipped:
        print(f"  BGL: skipped {skipped} malformed lines", file=sys.stderr)
    return rows


def load_anomalous_instances(paths: list[Path]) -> set[str]:
    """anomaly_labels.txt lists the instance UUIDs that had failures injected."""
    ids: set[str] = set()
    for p in paths:
        ids.update(UUID_RE.findall(p.read_text(encoding="utf-8", errors="replace")))
    return ids


def parse_openstack(sources: list[Path], dst: Path, anomalous_instances: set[str]) -> int:
    rows = 0
    skipped = 0
    line_id = 0
    with dst.open("w", encoding="utf-8", newline="") as fout:
        writer = csv.writer(fout)
        writer.writerow(OPENSTACK_COLUMNS)
        for src in sources:
            with src.open("r", encoding="utf-8", errors="replace") as fin:
                for raw in fin:
                    m = OPENSTACK_LINE_RE.match(raw.rstrip("\r\n"))
                    if not m:
                        skipped += 1
                        continue
                    line_id += 1
                    logger = m["logger"]
                    content = m["content"] or ""

                    svc = SERVICE_RE.match(m["log_file"])
                    if svc:
                        component = svc.group(1)
                    else:
                        component = next((c for prefix, c in LOGGER_TO_COMPONENT if logger.startswith(prefix)), logger)

                    # Context is "req-<uuid> <user> <tenant> - - -" or "-"
                    ctx = (m["ctx"] or "").split()
                    request_id = next((t for t in ctx if t.startswith("req-")), "")
                    user_id = tenant_id = ""
                    if request_id and len(ctx) >= 3:
                        user_id, tenant_id = _none_if_null(ctx[1]), _none_if_null(ctx[2])

                    inst = INSTANCE_RE.search(content)
                    instance_id = inst["id"] if inst else ""

                    http = HTTP_RE.search(content)
                    is_anomaly = bool(instance_id) and instance_id in anomalous_instances

                    writer.writerow([
                        line_id,
                        src.name,
                        m["log_file"],
                        f"{m['date']} {m['time']}",
                        m["pid"],
                        m["level"],
                        component,
                        logger,
                        request_id,
                        user_id,
                        tenant_id,
                        instance_id,
                        http["method"] if http else "",
                        http["path"] if http else "",
                        PATH_ID_RE.sub("{id}", http["path"].split("?", 1)[0]) if http else "",
                        http["status"] if http else "",
                        http["len"] if http else "",
                        http["time"] if http else "",
                        "Anomaly" if is_anomaly else "Normal",
                        "true" if is_anomaly else "false",
                        content,
                    ])
                    rows += 1
    if skipped:
        print(f"  OpenStack: skipped {skipped} unparseable lines", file=sys.stderr)
    return rows


def find_bgl(raw_dir: Path) -> Path | None:
    for candidate in [raw_dir / "BGL" / "BGL.log", raw_dir / "BGL.log", raw_dir / "BGL_2k.log"]:
        if candidate.is_file():
            return candidate
    hits = sorted(raw_dir.rglob("BGL.log"))
    return hits[0] if hits else None


def find_openstack(raw_dir: Path) -> list[Path]:
    os_dir = raw_dir / "OpenStack"
    if os_dir.is_dir():
        logs = sorted(p for p in os_dir.rglob("*.log") if p.is_file())
        if logs:
            return logs
    sample = raw_dir / "OpenStack_2k.log"
    return [sample] if sample.is_file() else []


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--raw-dir", type=Path, default=Path("data/raw"))
    ap.add_argument("--out-dir", type=Path, default=Path("data/processed"))
    ap.add_argument("--bgl", type=Path, help="Path to BGL.log (default: discovered under --raw-dir)")
    ap.add_argument("--openstack", type=Path, nargs="+",
                    help="OpenStack .log files or a directory (default: discovered under --raw-dir)")
    ap.add_argument("--anomaly-labels", type=Path, nargs="*",
                    help="anomaly_labels.txt (default: discovered next to the OpenStack logs)")
    args = ap.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)

    bgl_src = args.bgl or find_bgl(args.raw_dir)
    if bgl_src and bgl_src.is_file():
        print(f"Parsing BGL: {bgl_src}")
        n = parse_bgl(bgl_src, args.out_dir / "bgl_logs.csv")
        print(f"  wrote {n:,} rows -> {args.out_dir / 'bgl_logs.csv'}")
    else:
        print("BGL log not found; run scripts/download_corpus.sh first", file=sys.stderr)
        return 1

    if args.openstack:
        os_sources = []
        for p in args.openstack:
            os_sources += sorted(p.rglob("*.log")) if p.is_dir() else [p]
    else:
        os_sources = find_openstack(args.raw_dir)
    if not os_sources:
        print("OpenStack logs not found; run scripts/download_corpus.sh first", file=sys.stderr)
        return 1

    label_files = args.anomaly_labels
    if label_files is None:
        label_files = sorted({f for s in os_sources for f in s.parent.rglob("anomaly_labels*.txt")})
    anomalous = load_anomalous_instances(label_files)
    print(f"Parsing OpenStack: {', '.join(s.name for s in os_sources)}")
    print(f"  {len(anomalous)} anomalous instance IDs from {[f.name for f in label_files] or 'no label file'}")
    n = parse_openstack(os_sources, args.out_dir / "openstack_logs.csv", anomalous)
    print(f"  wrote {n:,} rows -> {args.out_dir / 'openstack_logs.csv'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
