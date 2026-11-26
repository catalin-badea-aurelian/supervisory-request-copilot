# Domain pack `payments` — changelog

## 1.3.0 — variance check tuned to the data's real noise

The variance check V-VAR-01 (WARN only) warned in almost every month, which buried the
movements that deserve an explanation. Diagnosis on the default dataset, per cause:

- `chargeback_rate_count`: a per-mille rate over single- and low-double-digit monthly chargeback
  counts (BE, IE, LU: 1 to 20 a month). Sampling noise alone is 1/sqrt(n), so 25-70 % month-on-month
  moves are normal and one reached 11x. A relative threshold cannot separate signal from noise
  here, so the check is removed from this metric. The same events are watched by
  `chargeback_rate_value` and `chargeback_amount_eur`, which stay in V-VAR-01.
- `settled_volume_eur`, `transaction_count`, `chargeback_amount_eur`: the December peak and
  January fall move every country by 25-30 % (seasonality). Threshold 0.25 -> 0.35. Single-country
  jumps and the broad late-summer drop are still flagged.
- `refund_rate_value`: moves of up to 48 % on small monthly refund counts. Threshold 0.25 -> 0.60.
- Unchanged at 0.25: `chargeback_rate_value`, `fraud_loss_rate_bps`, `avg_ticket_eur`,
  `active_merchants`, `top10_merchant_concentration`.
- No metric definition changes; metric versions are unchanged. See ADR 016.

## 1.2.0 — checker-only sources

- Sources may declare `checker_only: true`: readable by the validation engine only, never by
  metric templates, the Analyst or the governed MCP server. `meta_load_log` is checker-only
  (it stays in the raw layer, where the loader writes it). Control-layer sources are always
  checker-only; the pack loader rejects a metric that reads one.
- No metric definition changes; metric versions are unchanged.

## 1.1.0 — validation metadata

- Sources declare `primary_key`, and where present `period_column` / `period_column_type` and
  `country_column`, for the key-uniqueness check (V-PK-01).
- `chargeback_rate_value`: the pre-computed `cb_rate` column declares its glossary formula
  (`cb_amount_eur / settled_volume_eur`) for the definition-conformity check (V-DEF-01).
- Chargeback metrics (`chargeback_amount_eur`, `chargeback_rate_value`, `chargeback_rate_count`,
  now 1.1.0) declare `maturity_days: 60` and the WARN-only maturity check V-MAT-01: chargebacks
  are raised weeks after the transaction month they are attributed to.
- Default tolerance for V-DEF-01: absolute 1e-9.

## 1.0.0 — initial pack

- Ten glossary metrics, each with a primary and an independent SQL template.
- Early-warning thresholds (fictional) for four metrics; they are seeded into `spb_control.ewi_thresholds`.
- `new_merchant_volume_share` is intentionally absent (scenario S3).

### Documented limitation: independence of the second path

`grp_merchant_monthly` (parent-group product) only holds volume, count and chargeback
components. For chargeback, volume, count and average-ticket metrics the independent path
recomputes from raw sources, so it is fully independent of the group product.

For `fraud_loss_rate_bps`, `refund_rate_value`, `active_merchants` and
`top10_merchant_concentration` the group product cannot serve the metric, so **both** the
preferred and the independent template read the raw tables. They are written with
different query structures, but they share the same source tables, so a defect in the raw
data would not be caught by V-DUAL-01 for these metrics. This is a weaker form of
independence and is recorded in each metric file under `limitations`.
