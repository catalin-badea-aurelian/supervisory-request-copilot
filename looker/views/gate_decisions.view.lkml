# GENERATED FROM domain pack payments@1.2.0 — DO NOT EDIT
# content-sha256: bd17a34a4fe643a2695666c3b8f9f68a8fb2e764d2d4ec53d014e0ccb199b39a
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
