"""
load_corpus.py

Runs sql/01..03 against SereneDB to build the schema, bulk-load the processed CSVs
and create the ownership table. Run scripts/prepare_logs.py first.

    python scripts/load_corpus.py

Environment:
    SERENEDB_DSN       connection string (default postgresql://postgres@localhost:7890/postgres)
    SERENEDB_DATA_DIR  path to the repo's data/ directory *as the SereneDB server sees it*
                       (default /data, matching the docker-compose volume mount)
    SERENEDB_READER_PASSWORD  password of the SELECT-only role log_reader used for guarded
                       LLM-written SQL (default log_reader)
"""

import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.db import READER_ROLE, get_connection  # noqa: E402

SQL_FILES = ["01_schema_and_views.sql", "02_load_base_corpus.sql", "03_team_ownership.sql"]
READER_TABLES = ["bgl_logs", "openstack_logs", "team_ownership"]


def ensure_reader_role(conn) -> None:
    """SELECT-only login role for execute_guarded_readonly_sql. Re-granted on every load
    because the tables are recreated. SereneDB grants one table per statement."""
    password = os.getenv("SERENEDB_READER_PASSWORD", "log_reader").replace("'", "''")
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM pg_roles WHERE rolname = %s", [READER_ROLE])
        verb = "ALTER" if cur.fetchone()["n"] else "CREATE"
        cur.execute(f"{verb} ROLE {READER_ROLE} {'LOGIN ' if verb == 'CREATE' else ''}PASSWORD '{password}'")
        for table in READER_TABLES:
            cur.execute(f"GRANT SELECT ON {table} TO {READER_ROLE}")
    print(f"== role {READER_ROLE}: {'created' if verb == 'CREATE' else 'updated'}, "
          f"SELECT granted on {', '.join(READER_TABLES)}")


def split_statements(sql: str) -> list[str]:
    # Statements in sql/ end with ';' at end of line and contain no ';' inside literals.
    sql = re.sub(r"--[^\n]*", "", sql)
    return [s.strip() for s in re.split(r";\s*(?:\n|$)", sql) if s.strip()]


def main() -> int:
    data_dir = os.getenv("SERENEDB_DATA_DIR", "/data").rstrip("/")
    with get_connection(autocommit=True) as conn:
        for name in SQL_FILES:
            sql = (ROOT / "sql" / name).read_text(encoding="utf-8").replace("{{DATA_DIR}}", data_dir)
            print(f"== {name}")
            for stmt in split_statements(sql):
                head = " ".join(stmt.split())[:90]
                t0 = time.perf_counter()
                with conn.cursor() as cur:
                    cur.execute(stmt)
                    if stmt.upper().startswith("DESCRIBE"):
                        for row in cur.fetchall():
                            print("     ", {k: row[k] for k in list(row)[:2]})
                print(f"  {(time.perf_counter() - t0) * 1000:8.0f} ms  {head}")

        ensure_reader_role(conn)
        with conn.cursor() as cur:
            for table in ("bgl_logs", "openstack_logs", "team_ownership"):
                cur.execute(f"SELECT COUNT(*) AS n FROM {table}")
                print(f"{table}: {cur.fetchone()['n']:,} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
