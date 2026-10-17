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

-- Index publish step. Inverted indexes are eventually consistent and compact in the
-- background, so results right after a bulk load could depend on timing. These make the
-- post-load state deterministic before any query runs:
--   REFRESH_TABLE         publish every loaded row to readers
--   COMPACT_TABLE         merge index segments now instead of in the background
--   RECOMPUTE_STATS_TABLE recompute the statistics used for scoring and planning
-- (A bare "VACUUM;" is a documented no-op on SereneDB tables, so it is not used here.)
VACUUM (REFRESH_TABLE) bgl_logs;
VACUUM (REFRESH_TABLE) openstack_logs;
VACUUM (COMPACT_TABLE) bgl_logs;
VACUUM (COMPACT_TABLE) openstack_logs;
VACUUM (RECOMPUTE_STATS_TABLE) bgl_logs;
VACUUM (RECOMPUTE_STATS_TABLE) openstack_logs;
