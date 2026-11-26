-- GENERATED FROM domain pack payments@1.2.0 — DO NOT EDIT
-- view: mart_ewi_monthly
WITH v AS (
  SELECT period AS period_month, merchant_country, 'active_merchants' AS metric_id, '1.0.0' AS metric_version, CAST(active_merchants AS FLOAT64) AS value
  FROM (
/* active_merchants (independent path). BigQuery Standard SQL; transpiled to DuckDB by the pack compiler. */ WITH base AS (SELECT t.merchant_id, DATE(t.txn_ts) AS period_date, COALESCE(m.merchant_country, 'XX') AS merchant_country, m.mcc_group, t.payment_method, t.status, t.amount_eur FROM `${project}.${prefix}_raw.src_transactions` AS t LEFT JOIN `${project}.${prefix}_raw.src_merchants` AS m ON m.merchant_id = t.merchant_id WHERE TRUE AND t.txn_ts >= TIMESTAMP(DATE '2024-10-01') AND t.txn_ts < TIMESTAMP(DATE_ADD(DATE '2025-09-30', INTERVAL '1' DAY)) AND DATE(t._loaded_at) <= DATE '2025-10-15' AND t.status IN ('settled', 'refunded')) SELECT period, merchant_country, COUNT(*) AS active_merchants FROM (SELECT DISTINCT DATE_TRUNC(period_date, MONTH) AS period, merchant_country, merchant_id FROM base WHERE TRUE) AS pairs GROUP BY period, merchant_country
  ) AS m_active_merchants
  UNION ALL
  SELECT period AS period_month, merchant_country, 'chargeback_rate_value' AS metric_id, '1.1.0' AS metric_version, CAST(chargeback_rate_value AS FLOAT64) AS value
  FROM (
/* chargeback_rate_value (independent path). BigQuery Standard SQL; transpiled to DuckDB by the pack compiler. */ WITH settled AS (SELECT t.txn_id, t.merchant_id, DATE(t.txn_ts) AS period_date, COALESCE(m.merchant_country, 'XX') AS merchant_country, m.mcc_group, t.payment_method, t.amount_eur FROM `${project}.${prefix}_raw.src_transactions` AS t LEFT JOIN `${project}.${prefix}_raw.src_merchants` AS m ON m.merchant_id = t.merchant_id WHERE TRUE AND t.txn_ts >= TIMESTAMP(DATE '2024-10-01') AND t.txn_ts < TIMESTAMP(DATE_ADD(DATE '2025-09-30', INTERVAL '1' DAY)) AND DATE(t._loaded_at) <= DATE '2025-10-15' AND t.status IN ('settled', 'refunded')), cb AS (SELECT txn_id, COUNT(*) AS cb_count, SUM(amount_eur) AS cb_amount_eur FROM `${project}.${prefix}_raw.src_chargebacks` WHERE DATE(_loaded_at) <= DATE '2025-10-15' GROUP BY txn_id), base AS (SELECT s.period_date, s.merchant_country, s.mcc_group, s.payment_method, s.amount_eur, COALESCE(c.cb_count, 0) AS cb_count, COALESCE(c.cb_amount_eur, 0) AS cb_amount_eur FROM settled AS s LEFT JOIN cb AS c ON c.txn_id = s.txn_id) SELECT DATE_TRUNC(period_date, MONTH) AS period, merchant_country, SUM(cb_amount_eur) AS chargeback_amount_eur, SUM(amount_eur) AS settled_volume_eur, SAFE_DIVIDE(CAST(SUM(cb_amount_eur) AS FLOAT64), CAST(SUM(amount_eur) AS FLOAT64)) AS chargeback_rate_value FROM base WHERE TRUE GROUP BY period, merchant_country
  ) AS m_chargeback_rate_value
  UNION ALL
  SELECT period AS period_month, merchant_country, 'fraud_loss_rate_bps' AS metric_id, '1.0.0' AS metric_version, CAST(fraud_loss_rate_bps AS FLOAT64) AS value
  FROM (
/* fraud_loss_rate_bps (independent path). BigQuery Standard SQL; transpiled to DuckDB by the pack compiler. */ WITH facts AS (SELECT DATE(t.txn_ts) AS period_date, COALESCE(m.merchant_country, 'XX') AS merchant_country, m.mcc_group, t.payment_method, t.amount_eur AS volume_eur, CAST(0 AS NUMERIC) AS loss_eur FROM `${project}.${prefix}_raw.src_transactions` AS t LEFT JOIN `${project}.${prefix}_raw.src_merchants` AS m ON m.merchant_id = t.merchant_id WHERE TRUE AND t.txn_ts >= TIMESTAMP(DATE '2024-10-01') AND t.txn_ts < TIMESTAMP(DATE_ADD(DATE '2025-09-30', INTERVAL '1' DAY)) AND DATE(t._loaded_at) <= DATE '2025-10-15' AND t.status IN ('settled', 'refunded') UNION ALL SELECT DATE(t.txn_ts), COALESCE(m.merchant_country, 'XX'), m.mcc_group, t.payment_method, CAST(0 AS NUMERIC), f.loss_eur FROM `${project}.${prefix}_raw.src_fraud_losses` AS f JOIN `${project}.${prefix}_raw.src_transactions` AS t ON t.txn_id = f.txn_id LEFT JOIN `${project}.${prefix}_raw.src_merchants` AS m ON m.merchant_id = t.merchant_id WHERE DATE(f._loaded_at) <= DATE '2025-10-15' AND t.txn_ts >= TIMESTAMP(DATE '2024-10-01') AND t.txn_ts < TIMESTAMP(DATE_ADD(DATE '2025-09-30', INTERVAL '1' DAY)) AND DATE(t._loaded_at) <= DATE '2025-10-15' AND t.status IN ('settled', 'refunded')) SELECT DATE_TRUNC(period_date, MONTH) AS period, merchant_country, SUM(loss_eur) AS fraud_loss_eur, SUM(volume_eur) AS settled_volume_eur, SAFE_DIVIDE(CAST(SUM(loss_eur) AS FLOAT64), CAST(SUM(volume_eur) AS FLOAT64)) * 10000 AS fraud_loss_rate_bps FROM facts WHERE TRUE GROUP BY period, merchant_country
  ) AS m_fraud_loss_rate_bps
  UNION ALL
  SELECT period AS period_month, merchant_country, 'refund_rate_value' AS metric_id, '1.0.0' AS metric_version, CAST(refund_rate_value AS FLOAT64) AS value
  FROM (
/* refund_rate_value (independent path). BigQuery Standard SQL; transpiled to DuckDB by the pack compiler. */ WITH facts AS (SELECT DATE(t.txn_ts) AS period_date, COALESCE(m.merchant_country, 'XX') AS merchant_country, m.mcc_group, t.payment_method, t.amount_eur AS volume_eur, CAST(0 AS NUMERIC) AS refund_eur FROM `${project}.${prefix}_raw.src_transactions` AS t LEFT JOIN `${project}.${prefix}_raw.src_merchants` AS m ON m.merchant_id = t.merchant_id WHERE TRUE AND t.txn_ts >= TIMESTAMP(DATE '2024-10-01') AND t.txn_ts < TIMESTAMP(DATE_ADD(DATE '2025-09-30', INTERVAL '1' DAY)) AND DATE(t._loaded_at) <= DATE '2025-10-15' AND t.status IN ('settled', 'refunded') UNION ALL SELECT DATE(t.txn_ts), COALESCE(m.merchant_country, 'XX'), m.mcc_group, t.payment_method, CAST(0 AS NUMERIC), t.amount_eur FROM `${project}.${prefix}_raw.src_transactions` AS t LEFT JOIN `${project}.${prefix}_raw.src_merchants` AS m ON m.merchant_id = t.merchant_id WHERE TRUE AND t.txn_ts >= TIMESTAMP(DATE '2024-10-01') AND t.txn_ts < TIMESTAMP(DATE_ADD(DATE '2025-09-30', INTERVAL '1' DAY)) AND DATE(t._loaded_at) <= DATE '2025-10-15' AND t.status IN ('settled', 'refunded') AND t.status = 'refunded') SELECT DATE_TRUNC(period_date, MONTH) AS period, merchant_country, SUM(refund_eur) AS refunded_amount_eur, SUM(volume_eur) AS settled_volume_eur, SAFE_DIVIDE(CAST(SUM(refund_eur) AS FLOAT64), CAST(SUM(volume_eur) AS FLOAT64)) AS refund_rate_value FROM facts WHERE TRUE GROUP BY period, merchant_country
  ) AS m_refund_rate_value
  UNION ALL
  SELECT period AS period_month, merchant_country, 'top10_merchant_concentration' AS metric_id, '1.0.0' AS metric_version, CAST(top10_merchant_concentration AS FLOAT64) AS value
  FROM (
/* top10_merchant_concentration (independent path). BigQuery Standard SQL; transpiled to DuckDB by the pack compiler. */ WITH volumes AS (SELECT t.merchant_id, DATE(t.txn_ts) AS period_date, COALESCE(m.merchant_country, 'XX') AS merchant_country, m.mcc_group, t.amount_eur FROM `${project}.${prefix}_raw.src_merchants` AS m RIGHT JOIN `${project}.${prefix}_raw.src_transactions` AS t ON t.merchant_id = m.merchant_id WHERE TRUE AND t.txn_ts >= TIMESTAMP(DATE '2024-10-01') AND t.txn_ts < TIMESTAMP(DATE_ADD(DATE '2025-09-30', INTERVAL '1' DAY)) AND DATE(t._loaded_at) <= DATE '2025-10-15' AND t.status IN ('settled', 'refunded')), mv AS (SELECT DATE_TRUNC(period_date, MONTH) AS period, merchant_country, merchant_id, SUM(amount_eur) AS vol FROM volumes AS base WHERE TRUE GROUP BY period, merchant_country, merchant_id), ranked AS (SELECT *, COUNT(*) OVER (PARTITION BY period, merchant_country ORDER BY vol DESC, merchant_id ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS rn FROM mv) SELECT period, merchant_country, SUM(CASE WHEN rn <= 10 THEN vol ELSE 0 END) AS top10_volume_eur, SUM(vol) AS settled_volume_eur, SAFE_DIVIDE(CAST(SUM(CASE WHEN rn <= 10 THEN vol ELSE 0 END) AS FLOAT64), CAST(SUM(vol) AS FLOAT64)) AS top10_merchant_concentration FROM ranked GROUP BY period, merchant_country
  ) AS m_top10_merchant_concentration
),
scored AS (
  SELECT v.period_month, v.merchant_country, v.metric_id, v.metric_version, v.value,
    t.amber, t.red, t.direction,
    CASE
      WHEN t.metric_id IS NULL OR v.value IS NULL THEN 'NOT_ASSESSED'
      WHEN t.direction = 'higher_is_worse' AND v.value >= t.red THEN 'RED'
      WHEN t.direction = 'higher_is_worse' AND v.value >= t.amber THEN 'AMBER'
      WHEN t.direction = 'lower_is_worse' AND v.value <= t.red THEN 'RED'
      WHEN t.direction = 'lower_is_worse' AND v.value <= t.amber THEN 'AMBER'
      ELSE 'GREEN'
    END AS rag_status,
    SAFE_DIVIDE(
      v.value - LAG(v.value) OVER (
        PARTITION BY v.metric_id, v.merchant_country ORDER BY v.period_month),
      LAG(v.value) OVER (PARTITION BY v.metric_id, v.merchant_country ORDER BY v.period_month)
    ) AS mom_change
  FROM v
  LEFT JOIN `${project}.${prefix}_control.ewi_thresholds` AS t ON t.metric_id = v.metric_id
  WHERE v.merchant_country <> 'XX'
)
SELECT * FROM scored
