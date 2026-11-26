-- GENERATED FROM domain pack payments@1.2.0 — DO NOT EDIT
-- view: mart_dq_status
WITH latest AS (
  SELECT * FROM `${project}.${prefix}_ops.ops_check_results`
  WHERE run_type = 'scan'
  QUALIFY DENSE_RANK() OVER (PARTITION BY as_of ORDER BY recorded_at DESC, run_id DESC) = 1
)
SELECT as_of, period_month, merchant_country, metric_id, metric_version, check_id, check_name,
  principle_refs, status, measured, threshold, run_type, run_id, pack_version
FROM latest
