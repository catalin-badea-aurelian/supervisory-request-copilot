# GENERATED FROM domain pack payments@1.3.0 — DO NOT EDIT
# content-sha256: df5a0d29a9ab7c2d55c49a561ac49e8c54a1bc31b3787158c05cca51454a3370
view: ewi {
  sql_table_name: `@{DATASET_PREFIX}_marts.mart_ewi_monthly` ;;
  dimension: pk {
    primary_key: yes
    hidden: yes
    type: string
    sql: CONCAT(${TABLE}.metric_id, '|', CAST(${TABLE}.period_month AS STRING), '|', ${TABLE}.merchant_country) ;;
  }

  dimension_group: period {
    type: time
    timeframes: [month, quarter, year]
    datatype: date
    convert_tz: no
    sql: ${TABLE}.period_month ;;
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

  dimension: direction {
    type: string
    sql: ${TABLE}.direction ;;
  }

  dimension: amber {
    type: number
    sql: ${TABLE}.amber ;;
  }

  dimension: red {
    type: number
    sql: ${TABLE}.red ;;
  }

  dimension: rag_status {
    type: string
    description: "RAG from ewi_thresholds; NOT_ASSESSED where no threshold or value"
    sql: ${TABLE}.rag_status ;;
  }

  dimension: rag_rank {
    type: number
    label: "Severity"
    sql: CASE ${TABLE}.rag_status WHEN 'RED' THEN 3 WHEN 'AMBER' THEN 2 WHEN 'GREEN' THEN 1 ELSE 0 END ;;
  }

  dimension: mom_change {
    type: number
    description: "Relative change against the previous month"
    sql: ${TABLE}.mom_change ;;
  }

  measure: value {
    type: max
    sql: ${TABLE}.value ;;
    value_format_name: decimal_4
    description: "Pre-computed at month x country (independent path). Filter on one metric_id; do not aggregate across countries or months."
  }

  measure: breaches {
    type: count
    filters: [rag_status: "AMBER,RED"]
    description: "Month x country cells at AMBER or RED"
  }

  measure: active_merchants {
    type: max
    sql: CASE WHEN ${TABLE}.metric_id = 'active_merchants' THEN ${TABLE}.value END ;;
    value_format_name: decimal_0
    description: "active_merchants v1.0.0 | Number of distinct merchants with at least one settled transaction in the period. Not additive across dimensions. (month x country only; not re-aggregatable)"
    tags: ["metric_id:active_merchants", "metric_version:1.0.0"]
  }

  measure: top10_merchant_concentration {
    type: max
    sql: CASE WHEN ${TABLE}.metric_id = 'top10_merchant_concentration' THEN ${TABLE}.value END ;;
    value_format_name: percent_2
    description: "top10_merchant_concentration v1.0.0 | Share of settled_volume_eur, within each slice, that comes from the 10 largest merchants of that slice (ties broken by merchant_id). (month x country only; not re-aggregatable)"
    tags: ["metric_id:top10_merchant_concentration", "metric_version:1.0.0"]
  }
}
