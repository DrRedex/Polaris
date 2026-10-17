# Datasets Directory

Populated by the scripts; nothing here is committed.

```bash
./scripts/download_corpus.sh            # full BGL + OpenStack from Zenodo -> data/raw/
./scripts/download_corpus.sh --sample   # 2,000-line Loghub samples (quick smoke test)
python scripts/prepare_logs.py          # data/raw/ -> data/processed/{bgl_logs,openstack_logs}.csv
python scripts/load_corpus.py           # COPY into SereneDB, build full-text indexes
```

- `raw/` – downloaded archives and extracted `.log` files
  - BGL: https://zenodo.org/records/8196385/files/BGL.zip?download=1 (709 MB)
  - OpenStack: https://zenodo.org/records/8196385/files/OpenStack.tar.gz?download=1 (58.6 MB)
- `processed/` – structured CSVs. The docker-compose SereneDB container sees this directory
  as `/data/processed`; for a non-Docker server set `SERENEDB_DATA_DIR` to the absolute
  path of this `data/` directory before running `load_corpus.py`.
