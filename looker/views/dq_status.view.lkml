# GENERATED FROM domain pack payments@1.3.0 — DO NOT EDIT
# content-sha256: d1c45b34e2183f5b4f5ac2d6af715555818a93019f3dd2ce227b37065aa6067d
view: dq_status {
  sql_table_name: `@{DATASET_PREFIX}_marts.mart_dq_status` ;;
  dimension: pk {
    primary_key: yes
    hidden: yes
    type: string
    sql: CONCAT(${TABLE}.run_id, '|', ${TABLE}.metric_id, '|', ${TABLE}.check_id, '|', CAST(${TABLE}.period_month AS STRING), '|', ${TABLE}.merchant_country) ;;
  }

  dimension_group: period {
    type: time
    timeframes: [month, quarter, year]
    datatype: date
    convert_tz: no
    sql: ${TABLE}.period_month ;;
  }

  dimension: as_of {
    type: date
    sql: ${TABLE}.as_of ;;
  }

  dimension: merchant_country {
    type: string
    sql: ${TABLE}.merchant_country ;;
  }

  dimension: metric_id {
    type: string
    sql: ${TABLE}.metric_id ;;
  }

  dimension: metric_version {
    type: string
    sql: ${TABLE}.metric_version ;;
  }

  dimension: check_id {
    type: string
    sql: ${TABLE}.check_id ;;
  }

  dimension: check_name {
    type: string
    sql: ${TABLE}.check_name ;;
  }

  dimension: principle_refs {
    type: string
    label: "BCBS 239 principles"
    sql: ARRAY_TO_STRING(ARRAY(SELECT CAST(p AS STRING) FROM UNNEST(${TABLE}.principle_refs) AS p), ', ') ;;
  }

  dimension: status {
    type: string
    sql: ${TABLE}.status ;;
  }

  dimension: measured {
    type: string
    sql: ${TABLE}.measured ;;
  }

  dimension: threshold {
    type: string
    sql: ${TABLE}.threshold ;;
  }

  dimension: run_id {
    type: string
    sql: ${TABLE}.run_id ;;
  }

  measure: count {
    type: count
  }

  measure: fail_count {
    type: count
    filters: [status: "FAIL"]
  }

  measure: warn_count {
    type: count
    filters: [status: "WARN"]
  }

  measure: not_pass_count {
    type: count
    filters: [status: "-PASS"]
  }

  measure: worst_status {
    type: string
    sql: CASE MAX(CASE ${TABLE}.status WHEN 'FAIL' THEN 2 WHEN 'WARN' THEN 1 ELSE 0 END) WHEN 2 THEN 'FAIL' WHEN 1 THEN 'WARN' ELSE 'PASS' END ;;
    description: "Worst status in the cell (FAIL > WARN > PASS)"
  }
}
