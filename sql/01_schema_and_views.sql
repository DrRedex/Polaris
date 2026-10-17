-- ============================================================================
-- 01_schema_and_views.sql
-- Inspection views over the processed files, materialized core tables, and the
-- text search dictionary used by the full-text indexes.
--
-- {{DATA_DIR}} is substituted by scripts/load_corpus.py with the directory the
-- SereneDB *server* sees (/data inside the docker-compose container).
-- ============================================================================

-- Views read the processed files in place without materializing rows.
CREATE OR REPLACE VIEW v_bgl_raw AS
SELECT * FROM read_csv('{{DATA_DIR}}/processed/bgl_logs.csv', header = true);

CREATE OR REPLACE VIEW v_openstack_raw AS
SELECT * FROM read_csv('{{DATA_DIR}}/processed/openstack_logs.csv', header = true);

DESCRIBE SELECT * FROM v_bgl_raw;
DESCRIBE SELECT * FROM v_openstack_raw;

-- Re-runnable: drop dependents before tables.
DROP INDEX IF EXISTS bgl_logs_idx;
DROP INDEX IF EXISTS openstack_logs_idx;
DROP TABLE IF EXISTS bgl_logs;
DROP TABLE IF EXISTS openstack_logs;
DROP TEXT SEARCH DICTIONARY IF EXISTS log_text;

-- Lower-cased tokens, no stemming: log messages are full of identifiers and
-- error codes where stemming would merge unrelated terms.
CREATE TEXT SEARCH DICTIONARY log_text AS
    split_text(case := 'lower') | normalize_tokens('en_US.UTF-8', accent := false)
    WITH (frequency, position);

CREATE TABLE bgl_logs (
    line_id         BIGINT PRIMARY KEY,
    label           VARCHAR,        -- '-' for normal, else alert category (e.g. KERNDTLB)
    is_anomaly      BOOLEAN,
    epoch_ts        BIGINT,
    event_time      TIMESTAMP,
    date_day        DATE,
    node_id         VARCHAR,        -- e.g. R02-M1-N0-C:J12-U11
    subsystem       VARCHAR,        -- e.g. RAS
    component       VARCHAR,        -- KERNEL, APP, MMCS, DISCOVERY, LINKCARD, ...
    severity        VARCHAR,        -- INFO, WARNING, ERROR, SEVERE, FATAL, FAILURE
    message         VARCHAR
);

CREATE TABLE openstack_logs (
    line_id         BIGINT PRIMARY KEY,
    source_file     VARCHAR,        -- openstack_normal1.log, openstack_abnormal.log, ...
    log_file        VARCHAR,        -- original nova-*.log rotation name
    event_time      TIMESTAMP,
    process_id      INTEGER,
    severity        VARCHAR,        -- INFO, WARNING, ERROR
    component       VARCHAR,        -- nova-api, nova-compute, nova-scheduler
    logger          VARCHAR,        -- python logger, e.g. nova.compute.manager
    request_id      VARCHAR,        -- req-<uuid>
    user_id         VARCHAR,
    tenant_id       VARCHAR,
    instance_id     VARCHAR,
    http_method     VARCHAR,
    http_path       VARCHAR,
    http_route      VARCHAR,        -- http_path with tenant/instance IDs replaced by {id}
    http_status     INTEGER,
    response_bytes  BIGINT,
    latency_sec     DOUBLE,
    anomaly_label   VARCHAR,        -- 'Anomaly' if instance_id is in anomaly_labels.txt
    is_anomaly      BOOLEAN,
    message         VARCHAR
);
