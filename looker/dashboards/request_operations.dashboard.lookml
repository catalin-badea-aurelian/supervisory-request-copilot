# GENERATED FROM domain pack payments@1.3.0 — DO NOT EDIT
# content-sha256: 27cdfa288c21f5cffc6a9bff0c28d49e7e0ce59db254936b7fa03ea535c4643f
- dashboard: request_operations
  title: Request operations
  layout: newspaper
  preferred_viewer: dashboards-next
  description: How the oversight team handles supervisory requests. Synthetic data — fictional entity.
  filters:
  - name: period
    title: Received
    type: field_filter
    default_value: ''
    allow_multiple_values: true
    required: false
    model: spb_oversight
    explore: requests
    field: requests.received_date
  elements:
  - name: disclaimer
    type: text
    title_text: ''
    body_text: Synthetic data — fictional entity. Demonstration only. Merchant country does not apply
      to requests.
    row: 0
    col: 0
    width: 24
    height: 2
  - name: requests
    title: Requests
    model: spb_oversight
    explore: requests
    type: single_value
    fields:
    - requests.count
    listen: &id001
      period: requests.received_date
    row: 2
    col: 0
    width: 4
    height: 4
  - name: on_time
    title: Delivered on time
    model: spb_oversight
    explore: requests
    type: single_value
    fields:
    - requests.on_time_rate
    listen: *id001
    row: 2
    col: 4
    width: 4
    height: 4
  - name: median_hours
    title: Median time to delivery (hours)
    model: spb_oversight
    explore: requests
    type: single_value
    fields:
    - requests.median_duration_hours
    listen: *id001
    row: 2
    col: 8
    width: 4
    height: 4
  - name: g2
    title: G2 escalations
    model: spb_oversight
    explore: requests
    type: single_value
    fields:
    - requests.total_g2_escalations
    listen: *id001
    row: 2
    col: 12
    width: 4
    height: 4
  - name: accepted
    title: Accepted findings open
    model: spb_oversight
    explore: requests
    type: single_value
    fields:
    - requests.total_accepted_findings
    listen: *id001
    row: 2
    col: 16
    width: 4
    height: 4
  - name: cost
    title: Average cost per run
    model: spb_oversight
    explore: requests
    type: single_value
    fields:
    - requests.avg_cost_eur
    listen: *id001
    row: 2
    col: 20
    width: 4
    height: 4
  - name: request_table
    title: Requests
    model: spb_oversight
    explore: requests
    type: looker_grid
    fields:
    - requests.request_id
    - requests.title
    - requests.status
    - requests.deadline
    - requests.delivered_time
    - requests.g2_escalations
    sorts:
    - requests.request_id desc
    listen: *id001
    row: 6
    col: 0
    width: 14
    height: 7
  - name: gates
    title: Gate decisions by gate and outcome
    model: spb_oversight
    explore: requests
    type: looker_bar
    fields:
    - gate_decisions.gate
    - gate_decisions.decision
    - gate_decisions.count
    pivots:
    - gate_decisions.decision
    sorts:
    - gate_decisions.gate
    listen: *id001
    row: 6
    col: 14
    width: 10
    height: 7
