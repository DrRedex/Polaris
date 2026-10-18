"""Provision least-privilege app roles and policy views (explicit administrator action)."""
from __future__ import annotations
import os, sys
from pathlib import Path
import psycopg
from psycopg import sql
from dotenv import load_dotenv
load_dotenv()
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))  # so `python scripts/setup_roles.py` can import src

def main():
    missing=[name for name in ("SERENEDB_ENGINEER_PASSWORD","SERENEDB_TEAM_LEAD_PASSWORD","SERENEDB_MANAGER_PASSWORD") if not os.getenv(name)]
    if missing:
        print("Configure passwords before role setup: "+", ".join(missing),file=sys.stderr); return 2
    from src.db import SERENEDB_DSN
    with psycopg.connect(SERENEDB_DSN,autocommit=True) as conn:
        for role, env in (("engineer_role","SERENEDB_ENGINEER_PASSWORD"),("team_lead_role","SERENEDB_TEAM_LEAD_PASSWORD"),("manager_role","SERENEDB_MANAGER_PASSWORD")):
            exists=conn.execute("SELECT 1 FROM pg_roles WHERE rolname=%s",(role,)).fetchone()
            if not exists:
                conn.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(sql.Identifier(role),sql.Literal(os.environ[env])))
            else:
                conn.execute(sql.SQL("ALTER ROLE {} LOGIN PASSWORD {}").format(sql.Identifier(role),sql.Literal(os.environ[env])))
        # Drop comment lines before splitting: comments may contain ';'.
        script="\n".join(line for line in (ROOT/"sql"/"05_row_level_security.sql").read_text(encoding="utf-8").splitlines()
                         if not line.strip().startswith("--"))
        for stmt in script.split(";"):
            clean=stmt.strip()
            if clean: conn.execute(clean)
    print("Role policy configured. Set SERENEDB_ENGINEER_DSN, SERENEDB_TEAM_LEAD_DSN, SERENEDB_MANAGER_DSN and SERENEDB_APP_ROLE per deployment.")
    return 0
if __name__=="__main__": raise SystemExit(main())
