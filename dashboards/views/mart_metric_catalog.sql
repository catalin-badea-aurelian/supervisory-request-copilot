-- GENERATED FROM domain pack payments@1.2.0 — DO NOT EDIT
-- view: mart_metric_catalog
SELECT metric_id, version, name, owner, status, unit, has_independent_path,
  lineage_documented, reconciles_to, changed_at, pack_version
FROM `${project}.${prefix}_ops.ops_metric_catalog`
