"""
Test setup. By default the query layer runs against an in-process DuckDB loaded with the
fixtures in tests/fixtures, using the repo's own sql/ files. DuckDB speaks a near-identical
dialect, so this catches SQL and logic errors without a server. SereneDB-only statements
(text search dictionary, inverted index, VACUUM) are skipped, so full-text modes are
covered only by the live tests: pytest -m serenedb
"""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
sys.path.insert(0, str(ROOT))

SERENEDB_ONLY = ("CREATE TEXT SEARCH", "DROP TEXT SEARCH", "CREATE INDEX", "DROP INDEX", "VACUUM")


def pytest_configure(config):
    config.addinivalue_line("markers", "serenedb: needs a running SereneDB loaded via scripts/load_corpus.py")


def pytest_collection_modifyitems(config, items):
    if "serenedb" in (config.getoption("-m") or ""):
        return
    skip = pytest.mark.skip(reason="live SereneDB test; run with -m serenedb")
    for item in items:
        if "serenedb" in item.keywords:
            item.add_marker(skip)


class _DuckCursor:
    def __init__(self, con):
        self.c = con.cursor()
        self.description = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass

    def execute(self, sql, params=()):
        self.c.execute(sql.replace("%s", "?"), list(params))
        self.description = self.c.description

    def fetchall(self):
        cols = [d[0] for d in self.description]
        return [dict(zip(cols, r)) for r in self.c.fetchall()]

    def fetchone(self):
        rows = self.fetchall()
        return rows[0] if rows else None


class _DuckConn:
    def __init__(self, con):
        self.con = con

    def cursor(self):
        return _DuckCursor(self.con)

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass


@pytest.fixture(scope="session")
def processed_dir(tmp_path_factory):
    out = tmp_path_factory.mktemp("data")
    subprocess.run([sys.executable, "-I", str(ROOT / "scripts" / "prepare_logs.py"),
                    "--bgl", str(FIXTURES / "BGL_sample.log"),
                    "--openstack", str(FIXTURES / "OpenStack_sample.log"),
                    "--anomaly-labels", str(FIXTURES / "anomaly_labels.txt"),
                    "--out-dir", str(out / "processed")], check=True, capture_output=True)
    return out


@pytest.fixture(scope="session")
def duck(processed_dir):
    duckdb = pytest.importorskip("duckdb")
    from scripts.load_corpus import split_statements

    con = duckdb.connect()
    data_dir = processed_dir.as_posix()
    for name in ["01_schema_and_views.sql", "02_load_base_corpus.sql", "03_team_ownership.sql"]:
        sql = (ROOT / "sql" / name).read_text(encoding="utf-8").replace("{{DATA_DIR}}", data_dir)
        for stmt in split_statements(sql):
            if not stmt.upper().startswith(SERENEDB_ONLY):
                con.execute(stmt)
    return con


@pytest.fixture
def db(duck, monkeypatch):
    """Routes src.db connections to the DuckDB fixture."""
    import src.db as dbmod
    monkeypatch.setattr(dbmod, "get_connection", lambda autocommit=True, dsn=None: _DuckConn(duck))
    monkeypatch.setattr(dbmod, "_corpus_rows_cache", {})
    return duck
