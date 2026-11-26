# GENERATED FROM domain pack payments@1.2.0 — DO NOT EDIT
# content-sha256: 9b2a6a83136f39d4de2975d25b45d60b9137e425db762a0bef662b208e50f92d
view: metric_catalog {
  sql_table_name: `@{DATASET_PREFIX}_marts.mart_metric_catalog` ;;
  dimension: pk {
    primary_key: yes
    hidden: yes
    type: string
    sql: CONCAT(${TABLE}.metric_id, '|', ${TABLE}.version) ;;
  }

  dimension: metric_id {
    type: string
    sql: ${TABLE}.metric_id ;;
  }

  dimension: version {
    type: string
    sql: ${TABLE}.version ;;
  }

  dimension: name {
    type: string
    sql: ${TABLE}.name ;;
  }

  dimension: owner {
    type: string
    sql: ${TABLE}.owner ;;
  }

  dimension: status {
    type: string
    sql: ${TABLE}.status ;;
  }

  dimension: unit {
    type: string
    sql: ${TABLE}.unit ;;
  }

  dimension: has_independent_path {
    type: yesno
    sql: ${TABLE}.has_independent_path ;;
  }

  dimension: lineage_documented {
    type: yesno
    sql: ${TABLE}.lineage_documented ;;
  }

  dimension: reconciles_to {
    type: string
    sql: ${TABLE}.reconciles_to ;;
  }

  dimension: changed_at {
    type: date
    sql: ${TABLE}.changed_at ;;
  }

  measure: count {
    type: count
  }

  measure: lineage_coverage {
    type: number
    sql: AVG(CASE WHEN ${TABLE}.has_independent_path AND ${TABLE}.lineage_documented THEN 1 ELSE 0 END) ;;
    value_format_name: percent_0
    description: "Share of metrics with an independent path and documented lineage"
  }
}
