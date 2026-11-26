# GENERATED FROM domain pack payments@1.3.0 — DO NOT EDIT
# content-sha256: f7baaed3009e64b741da8a12a25d8a7f648d2016f3ade020c22b01d8e1d2d074
view: bcbs239 {
  sql_table_name: `@{DATASET_PREFIX}_marts.mart_bcbs239_scorecard` ;;
  dimension: pk {
    primary_key: yes
    hidden: yes
    type: string
    sql: CONCAT(CAST(${TABLE}.as_of AS STRING), '|', CAST(${TABLE}.principle_no AS STRING)) ;;
  }

  dimension: as_of {
    type: date
    sql: ${TABLE}.as_of ;;
  }

  dimension: principle_no {
    type: number
    sql: ${TABLE}.principle_no ;;
  }

  dimension: principle_name {
    type: string
    sql: CONCAT('P', CAST(${TABLE}.principle_no AS STRING), ' ', ${TABLE}.principle_name) ;;
  }

  measure: checks_total {
    type: sum
    sql: ${TABLE}.checks_total ;;
    value_format_name: decimal_0
  }

  measure: pass {
    type: sum
    sql: ${TABLE}.pass ;;
    value_format_name: decimal_0
  }

  measure: warn {
    type: sum
    sql: ${TABLE}.warn ;;
    value_format_name: decimal_0
  }

  measure: fail {
    type: sum
    sql: ${TABLE}.fail ;;
    value_format_name: decimal_0
  }

  measure: pass_rate {
    type: number
    sql: SAFE_DIVIDE(${pass}, ${checks_total}) ;;
    value_format_name: percent_1
    description: "Share of check results (metric x check x month x country) that pass"
  }
}
