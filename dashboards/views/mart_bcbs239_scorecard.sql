-- GENERATED FROM domain pack payments@1.2.0 — DO NOT EDIT
-- view: mart_bcbs239_scorecard
WITH latest AS (
  SELECT * FROM `${project}.${prefix}_ops.ops_check_results`
  WHERE run_type = 'scan'
  QUALIFY DENSE_RANK() OVER (PARTITION BY as_of ORDER BY recorded_at DESC, run_id DESC) = 1
),
by_principle AS (
  SELECT as_of, principle_no, status
  FROM latest, UNNEST(principle_refs) AS principle_no
)
SELECT as_of, principle_no,
  CASE principle_no
    WHEN 1 THEN 'Governance'
    WHEN 2 THEN 'Data architecture and IT infrastructure'
    WHEN 3 THEN 'Accuracy and integrity'
    WHEN 4 THEN 'Completeness'
    WHEN 5 THEN 'Timeliness'
    WHEN 6 THEN 'Adaptability'
    WHEN 7 THEN 'Accuracy (reporting)'
    WHEN 8 THEN 'Comprehensiveness'
    WHEN 9 THEN 'Clarity and usefulness'
    WHEN 10 THEN 'Frequency'
    WHEN 11 THEN 'Distribution'
  END AS principle_name,
  COUNT(*) AS checks_total,
  COUNTIF(status = 'PASS') AS pass,
  COUNTIF(status = 'WARN') AS warn,
  COUNTIF(status = 'FAIL') AS fail,
  SAFE_DIVIDE(COUNTIF(status = 'PASS'), COUNT(*)) AS pass_rate
FROM by_principle
GROUP BY as_of, principle_no
