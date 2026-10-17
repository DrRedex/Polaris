-- ============================================================================
-- 02_load_base_corpus.sql
-- Bulk-loads the processed CSVs and builds the inverted (full-text) indexes.
-- ============================================================================

COPY bgl_logs FROM '{{DATA_DIR}}/processed/bgl_logs.csv' (FORMAT CSV, HEADER TRUE);
COPY openstack_logs FROM '{{DATA_DIR}}/processed/openstack_logs.csv' (FORMAT CSV, HEADER TRUE);

-- @@ only resolves against an inverted index relation, so search_logs reads
-- FROM <table>_idx and joins back to the base table on line_id for filters.
CREATE INDEX bgl_logs_idx ON bgl_logs
    USING inverted (line_id, message log_text);

CREATE INDEX openstack_logs_idx ON openstack_logs
    USING inverted (line_id, message log_text);

-- Publish the freshly loaded rows to the indexes immediately.
VACUUM (REFRESH_TABLE) bgl_logs;
VACUUM (REFRESH_TABLE) openstack_logs;
