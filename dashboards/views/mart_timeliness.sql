-- GENERATED FROM domain pack payments@1.3.0 — DO NOT EDIT
-- view: mart_timeliness
WITH log AS (
  SELECT table_name, PARSE_DATE('%Y-%m', `partition`) AS partition_month,
    MAX(expected_row_count) AS expected_rows,
    SUM(IF(DATE(loaded_at) <= DATE '2025-10-15', row_count, 0)) AS loaded_rows,
    MAX(IF(DATE(loaded_at) <= DATE '2025-10-15', loaded_at, NULL)) AS last_loaded_at
  FROM `${project}.${prefix}_raw.meta_load_log`
  GROUP BY table_name, `partition`
)
SELECT DATE '2025-10-15' AS as_of, table_name, partition_month, expected_rows, loaded_rows,
  SAFE_DIVIDE(loaded_rows, expected_rows) AS completeness_pct, last_loaded_at,
  CASE
    WHEN loaded_rows = 0 THEN 'NOT_LOADED'
    WHEN SAFE_DIVIDE(loaded_rows, expected_rows) < 0.95 THEN 'INCOMPLETE'
    WHEN SAFE_DIVIDE(loaded_rows, expected_rows) < 0.99 THEN 'PARTIAL'
    ELSE 'COMPLETE'
  END AS status
FROM log
