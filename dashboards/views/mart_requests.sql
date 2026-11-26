-- GENERATED FROM domain pack payments@1.2.0 — DO NOT EDIT
-- view: mart_requests
SELECT request_id, title, received_at, deadline, delivered_at, status,
  CASE
    WHEN deadline IS NULL THEN NULL
    WHEN delivered_at IS NULL THEN FALSE
    ELSE DATE(delivered_at) <= deadline
  END AS on_time,
  g2_escalations, accepted_findings, est_cost_eur, duration_s, llm_calls, llm_replayed,
  tokens_in, tokens_out, bytes_processed, backend, pack_version
FROM `${project}.${prefix}_ops.ops_runs`
