-- ============================================================================
-- 04_remote_otel_parquet.sql  (optional; not run by scripts/load_corpus.py)
-- Zero-download views over the public 50-billion-row OpenTelemetry benchmark:
-- 50 Parquet files x ~1B rows, sorted by Timestamp, ~30 minutes of one day each.
--
-- Convenient for ad-hoc exploration in psql. The query_remote_otel_parquet tool
-- does NOT query through these views: with SereneDB 26.10.1 a Timestamp filter on
-- the view is not pushed down to read_parquet, so it scans the whole file. Calling
-- read_parquet('<url>') directly keeps row-group pruning, which reads ~0.1% of a file
-- for a 2-second window.
-- ============================================================================

-- Slice 1: part_000, first ~30 minutes (1 billion rows)
CREATE OR REPLACE VIEW otel_logs_1b AS
SELECT * FROM read_parquet('https://public-pme.s3.eu-west-3.amazonaws.com/text_bench/part_000.parquet');

-- Slice 2: part_000..part_009, first ~5 hours (10 billion rows)
CREATE OR REPLACE VIEW otel_logs_10b AS
SELECT * FROM read_parquet([
    'https://public-pme.s3.eu-west-3.amazonaws.com/text_bench/part_000.parquet',
    'https://public-pme.s3.eu-west-3.amazonaws.com/text_bench/part_001.parquet',
    'https://public-pme.s3.eu-west-3.amazonaws.com/text_bench/part_002.parquet',
    'https://public-pme.s3.eu-west-3.amazonaws.com/text_bench/part_003.parquet',
    'https://public-pme.s3.eu-west-3.amazonaws.com/text_bench/part_004.parquet',
    'https://public-pme.s3.eu-west-3.amazonaws.com/text_bench/part_005.parquet',
    'https://public-pme.s3.eu-west-3.amazonaws.com/text_bench/part_006.parquet',
    'https://public-pme.s3.eu-west-3.amazonaws.com/text_bench/part_007.parquet',
    'https://public-pme.s3.eu-west-3.amazonaws.com/text_bench/part_008.parquet',
    'https://public-pme.s3.eu-west-3.amazonaws.com/text_bench/part_009.parquet'
]);

-- Metadata-only checks (footer reads, no row data):
-- SELECT COUNT(*) FROM read_parquet('https://public-pme.s3.eu-west-3.amazonaws.com/text_bench/part_000.parquet');
-- SELECT row_group_id, stats_min, stats_max, row_group_num_rows
-- FROM parquet_metadata('https://public-pme.s3.eu-west-3.amazonaws.com/text_bench/part_000.parquet')
-- WHERE path_in_schema = 'Timestamp' ORDER BY row_group_id LIMIT 5;
