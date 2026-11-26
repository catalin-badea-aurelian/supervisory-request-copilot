-- GENERATED FROM domain pack payments@1.3.0 — DO NOT EDIT
-- view: mart_requests
WITH per_check AS (
  SELECT run_id, metric_id, check_id,
    CASE MAX(CASE status WHEN 'FAIL' THEN 3 WHEN 'WARN' THEN 2 ELSE 1 END)
      WHEN 3 THEN 'FAIL' WHEN 2 THEN 'WARN' ELSE 'PASS' END AS status
  FROM `${project}.${prefix}_ops.ops_check_results`
  WHERE run_type = 'request'
  GROUP BY run_id, metric_id, check_id
),
counts AS (
  SELECT run_id,
    COUNTIF(status = 'PASS') AS checks_pass,
    COUNTIF(status = 'WARN') AS checks_warn,
    COUNTIF(status = 'FAIL') AS checks_fail
  FROM per_check
  GROUP BY run_id
)
SELECT r.request_id, r.title, r.received_at, r.deadline, r.delivered_at, r.status,
  CASE
    WHEN r.deadline IS NULL THEN NULL
    WHEN r.delivered_at IS NULL THEN FALSE
    ELSE DATE(r.delivered_at) <= r.deadline
  END AS on_time,
  r.g2_escalations, r.accepted_findings, r.est_cost_eur, r.duration_s, r.llm_calls,
  r.llm_replayed, r.tokens_in, r.tokens_out, r.bytes_processed, r.backend, r.pack_version,
  COALESCE(c.checks_pass, 0) AS checks_pass,
  COALESCE(c.checks_warn, 0) AS checks_warn,
  COALESCE(c.checks_fail, 0) AS checks_fail
FROM `${project}.${prefix}_ops.ops_runs` AS r
LEFT JOIN counts AS c ON c.run_id = r.request_id
