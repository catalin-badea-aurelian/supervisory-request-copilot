# GENERATED FROM domain pack payments@1.3.0 — DO NOT EDIT
# content-sha256: 033f03d4c5aff4eed90c10be093547336e7690ebd26fbc3b23e6cd56658536e2
view: gate_decisions {
  sql_table_name: `@{DATASET_PREFIX}_marts.mart_gate_decisions` ;;
  dimension: decision_id {
    primary_key: yes
    type: string
    sql: ${TABLE}.decision_id ;;
  }

  dimension: request_id {
    type: string
    sql: ${TABLE}.request_id ;;
  }

  dimension: request_title {
    type: string
    sql: ${TABLE}.request_title ;;
  }

  dimension: request_status {
    type: string
    sql: ${TABLE}.request_status ;;
  }

  dimension: gate {
    type: string
    sql: ${TABLE}.gate ;;
  }

  dimension: decision {
    type: string
    sql: ${TABLE}.decision ;;
  }

  dimension: actor {
    type: string
    sql: ${TABLE}.actor ;;
  }

  dimension: role {
    type: string
    sql: ${TABLE}.role ;;
  }

  dimension_group: decided {
    type: time
    timeframes: [raw, time, date, month]
    sql: ${TABLE}.ts ;;
  }

  dimension: acknowledged_warnings {
    type: number
    sql: ${TABLE}.acknowledged_warnings ;;
  }

  measure: count {
    type: count
  }
}
