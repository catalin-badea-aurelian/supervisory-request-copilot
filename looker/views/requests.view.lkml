# GENERATED FROM domain pack payments@1.3.0 — DO NOT EDIT
# content-sha256: 649bd23325abf024e218c5862ed3b11ffbfeb17011088afcb90209600fd37548
view: requests {
  sql_table_name: `@{DATASET_PREFIX}_marts.mart_requests` ;;
  dimension: request_id {
    primary_key: yes
    type: string
    sql: ${TABLE}.request_id ;;
  }

  dimension: title {
    type: string
    sql: ${TABLE}.title ;;
  }

  dimension_group: received {
    type: time
    timeframes: [raw, time, date, month]
    sql: ${TABLE}.received_at ;;
  }

  dimension: deadline {
    type: date
    sql: ${TABLE}.deadline ;;
  }

  dimension_group: delivered {
    type: time
    timeframes: [raw, time, date, month]
    sql: ${TABLE}.delivered_at ;;
  }

  dimension: status {
    type: string
    sql: ${TABLE}.status ;;
  }

  dimension: on_time {
    type: yesno
    sql: ${TABLE}.on_time ;;
  }

  dimension: g2_escalations {
    type: number
    sql: ${TABLE}.g2_escalations ;;
  }

  dimension: accepted_findings {
    type: number
    sql: ${TABLE}.accepted_findings ;;
  }

  dimension: est_cost_eur {
    type: number
    sql: ${TABLE}.est_cost_eur ;;
  }

  dimension: duration_s {
    type: number
    sql: ${TABLE}.duration_s ;;
  }

  dimension: llm_replayed {
    type: yesno
    sql: ${TABLE}.llm_replayed ;;
  }

  dimension: checks_pass {
    type: number
    sql: ${TABLE}.checks_pass ;;
  }

  dimension: checks_warn {
    type: number
    sql: ${TABLE}.checks_warn ;;
  }

  dimension: checks_fail {
    type: number
    sql: ${TABLE}.checks_fail ;;
  }

  measure: count {
    type: count
  }

  measure: on_time_rate {
    type: average
    sql: CASE WHEN ${TABLE}.on_time THEN 1 WHEN NOT ${TABLE}.on_time THEN 0 END ;;
    value_format_name: percent_0
    description: "Delivered by the deadline, among requests that have one"
  }

  measure: median_duration_hours {
    type: median
    sql: ${TABLE}.duration_s / 3600 ;;
    value_format_name: decimal_2
  }

  measure: total_g2_escalations {
    type: sum
    sql: ${TABLE}.g2_escalations ;;
    value_format_name: decimal_0
  }

  measure: total_accepted_findings {
    type: sum
    sql: ${TABLE}.accepted_findings ;;
    value_format_name: decimal_0
  }

  measure: avg_cost_eur {
    type: average
    sql: ${TABLE}.est_cost_eur ;;
    value_format_name: eur_2
    description: "Estimated LLM cost per run (replayed runs are estimates; no API spend)"
  }
}
