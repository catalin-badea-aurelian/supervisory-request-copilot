-- GENERATED FROM domain pack payments@1.3.0 — DO NOT EDIT
-- view: mart_gate_decisions
SELECT g.request_id, r.title AS request_title, r.status AS request_status,
  g.decision_id, g.gate, g.decision, g.actor, g.role, g.ts,
  ARRAY_LENGTH(g.acknowledged_warnings) AS acknowledged_warnings
FROM `${project}.${prefix}_ops.ops_gate_decisions` AS g
LEFT JOIN `${project}.${prefix}_ops.ops_runs` AS r ON r.request_id = g.request_id
