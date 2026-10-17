# One-command targets. Windows without make: run the commands shown under each target.
PY ?= uv run python

.PHONY: setup db sample ingest mcp ui cli demo test test-live clean

setup:            ## install dependencies and start SereneDB
	uv sync
	docker compose up -d serenedb

db:               ## start only the SereneDB container
	docker compose up -d serenedb

sample:           ## quick smoke test: 2k-line samples
	bash scripts/download_corpus.sh --sample
	$(PY) -I scripts/prepare_logs.py
	$(PY) scripts/load_corpus.py

ingest:           ## full corpora (~770 MB download, ~4 min)
	bash scripts/download_corpus.sh
	$(PY) -I scripts/prepare_logs.py
	$(PY) scripts/load_corpus.py

mcp:              ## MCP server on stdio (Claude Code / Desktop)
	$(PY) -m src.mcp_server

ui:               ## Streamlit three-role UI on http://localhost:8501
	uv run streamlit run ui/streamlit_app.py

cli:              ## terminal demo of all three roles
	$(PY) ui/cli_demo.py --role all

demo:             ## regenerate docs/worked_investigation.md
	$(PY) scripts/run_worked_investigation.py

test:             ## offline tests (DuckDB stand-in, no server or API key)
	uv run pytest -q

test-live:        ## tests against the running, loaded SereneDB
	uv run pytest -q -m serenedb

clean:
	rm -rf data/processed .pytest_cache
