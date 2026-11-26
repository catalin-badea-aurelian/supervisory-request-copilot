# GENERATED FROM domain pack payments@1.3.0 — DO NOT EDIT
# content-sha256: 20416301f122107291b79c2d247ca651d4e17c3cd704fcaf64029277fd507428
- dashboard: data_risk
  title: Data risk (BCBS 239)
  layout: newspaper
  preferred_viewer: dashboards-next
  description: Can the data behind the indicators be trusted? Synthetic data — fictional entity.
  filters:
  - name: period
    title: Period
    type: field_filter
    default_value: 12 months
    allow_multiple_values: true
    required: false
    model: spb_oversight
    explore: dq_status
    field: dq_status.period_month
  - name: country
    title: Merchant country
    type: field_filter
    default_value: ''
    allow_multiple_values: true
    required: false
    model: spb_oversight
    explore: dq_status
    field: dq_status.merchant_country
  elements:
  - name: disclaimer
    type: text
    title_text: ''
    body_text: Synthetic data — fictional entity. Demonstration only. Latest data-quality scan per as-of
      date.
    row: 0
    col: 0
    width: 24
    height: 2
  - name: rec_breaks
    title: Open reconciliation breaks
    model: spb_oversight
    explore: dq_status
    type: single_value
    fields:
    - dq_status.fail_count
    filters:
      dq_status.check_id: V-REC-01
    listen: &id001
      period: dq_status.period_month
      country: dq_status.merchant_country
    row: 2
    col: 0
    width: 4
    height: 4
  - name: xx_volume
    title: Unallocated volume (XX)
    model: spb_oversight
    explore: ewi_components
    type: single_value
    fields:
    - ewi_components.settled_volume_eur
    filters:
      ewi_components.merchant_country: XX
    listen:
      period: ewi_components.period_month
    row: 2
    col: 4
    width: 4
    height: 4
  - name: completeness
    title: Completeness issues
    model: spb_oversight
    explore: dq_status
    type: single_value
    fields:
    - dq_status.not_pass_count
    filters:
      dq_status.check_id: V-COMP-01,V-COMP-02
    listen: *id001
    row: 2
    col: 8
    width: 4
    height: 4
  - name: late_partitions
    title: Incomplete or late partitions
    model: spb_oversight
    explore: timeliness
    type: single_value
    fields:
    - timeliness.not_complete_count
    listen:
      period: timeliness.partition_month
    row: 2
    col: 12
    width: 4
    height: 4
  - name: def_conformity
    title: Definition conformity failures
    model: spb_oversight
    explore: dq_status
    type: single_value
    fields:
    - dq_status.fail_count
    filters:
      dq_status.check_id: V-DEF-01
    listen: *id001
    row: 2
    col: 16
    width: 4
    height: 4
  - name: lineage
    title: Lineage coverage
    model: spb_oversight
    explore: metric_catalog
    type: single_value
    fields:
    - metric_catalog.lineage_coverage
    row: 2
    col: 20
    width: 4
    height: 4
  - name: xx_by_month
    title: Unallocated volume (XX) by month
    model: spb_oversight
    explore: ewi_components
    type: looker_column
    fields:
    - ewi_components.period_month
    - ewi_components.settled_volume_eur
    filters:
      ewi_components.merchant_country: XX
    sorts:
    - ewi_components.period_month
    listen:
      period: ewi_components.period_month
    row: 6
    col: 0
    width: 8
    height: 6
  - name: check_matrix
    title: Check status by month (worst status per cell)
    model: spb_oversight
    explore: dq_status
    type: looker_grid
    fields:
    - dq_status.check_id
    - dq_status.period_month
    - dq_status.worst_status
    pivots:
    - dq_status.period_month
    sorts:
    - dq_status.period_month
    - dq_status.check_id
    listen: *id001
    row: 6
    col: 8
    width: 16
    height: 6
  - name: pass_rate_principle
    title: Pass rate by BCBS 239 principle
    model: spb_oversight
    explore: bcbs239
    type: looker_bar
    fields:
    - bcbs239.principle_name
    - bcbs239.pass_rate
    sorts:
    - bcbs239.principle_name
    row: 12
    col: 0
    width: 8
    height: 6
  - name: timeliness_table
    title: Timeliness by table and month
    model: spb_oversight
    explore: timeliness
    type: looker_grid
    fields:
    - timeliness.table_name
    - timeliness.partition_month
    - timeliness.expected_rows
    - timeliness.loaded_rows
    - timeliness.completeness_pct
    - timeliness.status
    sorts:
    - timeliness.table_name
    - timeliness.partition_month desc
    listen:
      period: timeliness.partition_month
    row: 12
    col: 8
    width: 16
    height: 6
  - name: findings
    title: Finding details
    model: spb_oversight
    explore: dq_status
    type: looker_grid
    fields:
    - dq_status.metric_id
    - dq_status.check_id
    - dq_status.period_month
    - dq_status.merchant_country
    - dq_status.status
    - dq_status.measured
    - dq_status.threshold
    filters:
      dq_status.status: -PASS
    sorts:
    - dq_status.period_month desc
    - dq_status.check_id
    listen: *id001
    row: 18
    col: 0
    width: 24
    height: 8
