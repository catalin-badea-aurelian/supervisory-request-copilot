-- GENERATED FROM domain pack payments@1.2.0 — DO NOT EDIT
-- view: mart_gate_decisions
SELECT request_id, decision_id, gate, decision, actor, role, ts,
  ARRAY_LENGTH(acknowledged_warnings) AS acknowledged_warnings
FROM `${project}.${prefix}_ops.ops_gate_decisions`
