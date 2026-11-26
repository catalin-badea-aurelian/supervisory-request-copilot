# GENERATED FROM domain pack payments@1.2.0 — DO NOT EDIT
# content-sha256: 98315205f80d6ae288e70aca7b21e0d126647bb1d1d8cb1fafcb366b228f0388
view: timeliness {
  sql_table_name: `@{DATASET_PREFIX}_marts.mart_timeliness` ;;
  dimension: pk {
    primary_key: yes
    hidden: yes
    type: string
    sql: CONCAT(${TABLE}.table_name, '|', CAST(${TABLE}.partition_month AS STRING)) ;;
  }

  dimension: as_of {
    type: date
    sql: ${TABLE}.as_of ;;
  }

  dimension: table_name {
    type: string
    sql: ${TABLE}.table_name ;;
  }

  dimension_group: partition {
    type: time
    timeframes: [month, quarter, year]
    datatype: date
    convert_tz: no
    sql: ${TABLE}.partition_month ;;
  }

  dimension: expected_rows {
    type: number
    sql: ${TABLE}.expected_rows ;;
  }

  dimension: loaded_rows {
    type: number
    sql: ${TABLE}.loaded_rows ;;
  }

  dimension: completeness_pct {
    type: number
    sql: ${TABLE}.completeness_pct ;;
  }

  dimension_group: last_loaded {
    type: time
    timeframes: [raw, time, date, month]
    sql: ${TABLE}.last_loaded_at ;;
  }

  dimension: status {
    type: string
    sql: ${TABLE}.status ;;
  }

  measure: count {
    type: count
  }

  measure: not_complete_count {
    type: count
    filters: [status: "-COMPLETE"]
    description: "Partitions incomplete, partial or not loaded at the as-of date"
  }
}
