# GENERATED FROM domain pack payments@1.3.0 — DO NOT EDIT
# content-sha256: 3925d73b32978a50acbc84f8990c0537460bd12d9c6c113b4862213642ffeb24
- dashboard: risk_overview
  title: Risk overview
  layout: newspaper
  preferred_viewer: dashboards-next
  description: Independent early-warning indicators. Synthetic data — fictional entity.
  filters:
  - name: period
    title: Period
    type: field_filter
    default_value: 12 months
    allow_multiple_values: true
    required: false
    model: spb_oversight
    explore: ewi_components
    field: ewi_components.period_month
  - name: country
    title: Merchant country
    type: field_filter
    default_value: ''
    allow_multiple_values: true
    required: false
    model: spb_oversight
    explore: ewi_components
    field: ewi_components.merchant_country
  - name: mcc
    title: MCC group
    type: field_filter
    default_value: ''
    allow_multiple_values: true
    required: false
    model: spb_oversight
    explore: ewi_components
    field: ewi_components.mcc_group
  elements:
  - name: disclaimer
    type: text
    title_text: ''
    body_text: Synthetic data — fictional entity. Demonstration only. Figures computed by the oversight
      team from raw sources (independent path). Hover a value for the metric definition and version. Country
      RAG excludes XX (unknown country), which is shown on Data risk.
    row: 0
    col: 0
    width: 24
    height: 2
  - name: cb_rate_latest
    title: Chargeback rate (value), latest month
    model: spb_oversight
    explore: ewi_components
    type: single_value
    fields:
    - ewi_components.period_month
    - ewi_components.chargeback_rate_value
    sorts:
    - ewi_components.period_month desc
    limit: 2
    show_comparison: true
    comparison_type: change
    comparison_label: vs previous month
    listen: &id001
      period: ewi_components.period_month
      country: ewi_components.merchant_country
      mcc: ewi_components.mcc_group
    row: 2
    col: 0
    width: 6
    height: 4
  - name: fraud_latest
    title: Fraud loss rate, latest month
    model: spb_oversight
    explore: ewi_components
    type: single_value
    fields:
    - ewi_components.period_month
    - ewi_components.fraud_loss_rate_bps
    sorts:
    - ewi_components.period_month desc
    limit: 2
    show_comparison: true
    comparison_type: change
    comparison_label: vs previous month
    listen: *id001
    row: 2
    col: 6
    width: 6
    height: 4
  - name: refund_latest
    title: Refund rate, latest month
    model: spb_oversight
    explore: ewi_components
    type: single_value
    fields:
    - ewi_components.period_month
    - ewi_components.refund_rate_value
    sorts:
    - ewi_components.period_month desc
    limit: 2
    show_comparison: true
    comparison_type: change
    comparison_label: vs previous month
    listen: *id001
    row: 2
    col: 12
    width: 6
    height: 4
  - name: top10_by_country
    title: Top-10 merchant concentration by country, latest month
    model: spb_oversight
    explore: ewi
    type: looker_grid
    fields:
    - ewi.merchant_country
    - ewi.top10_merchant_concentration
    filters:
      ewi.period_month: 1 months ago for 1 months
    sorts:
    - ewi.top10_merchant_concentration desc
    listen:
      country: ewi.merchant_country
    row: 2
    col: 18
    width: 6
    height: 4
  - name: cb_rate_heatmap
    title: Chargeback rate (value) by country and month
    model: spb_oversight
    explore: ewi
    type: looker_grid
    fields:
    - ewi.merchant_country
    - ewi.period_month
    - ewi.value
    pivots:
    - ewi.period_month
    filters:
      ewi.metric_id: chargeback_rate_value
    sorts:
    - ewi.period_month
    - ewi.merchant_country
    value_format: 0.00%
    conditional_formatting:
    - type: greater than
      value:
      - 0.009
      background_color: '#E8A0A0'
      font_color: '#000000'
      bold: true
      fields: null
    - type: between
      value:
      - 0.006
      - 0.009
      background_color: '#F2D49B'
      font_color: '#000000'
      bold: false
      fields: null
    listen: &id002
      period: ewi.period_month
      country: ewi.merchant_country
    row: 6
    col: 0
    width: 24
    height: 6
  - name: volume_by_month
    title: Settled volume by month and country
    model: spb_oversight
    explore: ewi_components
    type: looker_line
    fields:
    - ewi_components.period_month
    - ewi_components.merchant_country
    - ewi_components.settled_volume_eur
    pivots:
    - ewi_components.merchant_country
    sorts:
    - ewi_components.period_month
    listen: *id001
    row: 12
    col: 0
    width: 12
    height: 7
  - name: breaches
    title: Threshold breaches
    model: spb_oversight
    explore: ewi
    type: looker_grid
    fields:
    - ewi.rag_rank
    - ewi.rag_status
    - ewi.metric_id
    - ewi.merchant_country
    - ewi.period_month
    - ewi.value
    - ewi.amber
    - ewi.red
    filters:
      ewi.rag_status: AMBER,RED
    sorts:
    - ewi.rag_rank desc
    - ewi.period_month desc
    listen: *id002
    row: 12
    col: 12
    width: 12
    height: 7
