# GENERATED FROM domain pack payments@1.2.0 — DO NOT EDIT
# content-sha256: f5d38e170fd98dfda2edd21127464de6d0ca3f5d0ef10c8b4ea19c70fdbbd28f
view: ewi_components {
  sql_table_name: `@{DATASET_PREFIX}_marts.mart_ewi_components` ;;
  dimension: pk {
    primary_key: yes
    hidden: yes
    type: string
    sql: CONCAT(COALESCE(CAST(${TABLE}.period_month AS STRING), ''), '|', COALESCE(CAST(${TABLE}.merchant_country AS STRING), ''), '|', COALESCE(CAST(${TABLE}.mcc_group AS STRING), '')) ;;
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

  dimension: mcc_group {
    type: string
    sql: ${TABLE}.mcc_group ;;
  }

  measure: chargeback_amount_eur {
    type: sum
    sql: ${TABLE}.chargeback_amount_eur ;;
    value_format_name: eur_0
    description: "chargeback_amount_eur v1.1.0 | Value of chargebacks raised on settled transactions, attributed to the month of the original transaction."
    tags: ["metric_id:chargeback_amount_eur", "metric_version:1.1.0"]
  }

  measure: settled_volume_eur {
    type: sum
    sql: ${TABLE}.settled_volume_eur ;;
    value_format_name: eur_0
    description: "settled_volume_eur v1.0.0 | Sum of the amounts (EUR) of settled transactions. A transaction is settled when its final status is settled or refunded; authorised-only transactions are excluded."
    tags: ["metric_id:settled_volume_eur", "metric_version:1.0.0"]
  }

  measure: transaction_count {
    type: sum
    sql: ${TABLE}.transaction_count ;;
    value_format_name: decimal_0
    description: "transaction_count v1.0.0 | Count of settled transactions (final status settled or refunded)."
    tags: ["metric_id:transaction_count", "metric_version:1.0.0"]
  }

  measure: chargeback_count {
    hidden: yes
    type: sum
    sql: ${TABLE}.chargeback_count ;;
    value_format_name: decimal_0
    description: "Component chargeback_count (helper, not a glossary metric)"
  }

  measure: fraud_loss_eur {
    hidden: yes
    type: sum
    sql: ${TABLE}.fraud_loss_eur ;;
    value_format_name: eur_0
    description: "Component fraud_loss_eur (helper, not a glossary metric)"
  }

  measure: refunded_amount_eur {
    hidden: yes
    type: sum
    sql: ${TABLE}.refunded_amount_eur ;;
    value_format_name: eur_0
    description: "Component refunded_amount_eur (helper, not a glossary metric)"
  }

  measure: avg_ticket_eur {
    type: number
    sql: SAFE_DIVIDE(${settled_volume_eur}, ${transaction_count}) ;;
    value_format_name: eur_0
    description: "avg_ticket_eur v1.0.0 | settled_volume_eur divided by transaction_count."
    tags: ["metric_id:avg_ticket_eur", "metric_version:1.0.0"]
  }

  measure: chargeback_rate_count {
    type: number
    sql: SAFE_DIVIDE(${chargeback_count}, ${transaction_count}) * 1000 ;;
    value_format_name: per_mille_2
    description: "chargeback_rate_count v1.1.0 | Number of chargebacks raised on settled transactions per 1,000 settled transactions, attributed to the month of the original transaction."
    tags: ["metric_id:chargeback_rate_count", "metric_version:1.1.0"]
  }

  measure: chargeback_rate_value {
    type: number
    sql: SAFE_DIVIDE(${chargeback_amount_eur}, ${settled_volume_eur}) ;;
    value_format_name: percent_2
    description: "chargeback_rate_value v1.1.0 | Value of chargebacks raised on settled transactions divided by the value of settled transactions, attributed to the month of the original transaction."
    tags: ["metric_id:chargeback_rate_value", "metric_version:1.1.0"]
  }

  measure: fraud_loss_rate_bps {
    type: number
    sql: SAFE_DIVIDE(${fraud_loss_eur}, ${settled_volume_eur}) * 10000 ;;
    value_format_name: bps_1
    description: "fraud_loss_rate_bps v1.0.0 | Confirmed fraud losses (EUR) on settled transactions divided by settled_volume_eur, times 10,000, attributed to the month of the original transaction."
    tags: ["metric_id:fraud_loss_rate_bps", "metric_version:1.0.0"]
  }

  measure: refund_rate_value {
    type: number
    sql: SAFE_DIVIDE(${refunded_amount_eur}, ${settled_volume_eur}) ;;
    value_format_name: percent_2
    description: "refund_rate_value v1.0.0 | Value of refunded transactions divided by settled_volume_eur, attributed to the month of the original transaction."
    tags: ["metric_id:refund_rate_value", "metric_version:1.0.0"]
  }
}
