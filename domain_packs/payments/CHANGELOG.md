# Domain pack `payments` — changelog

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
