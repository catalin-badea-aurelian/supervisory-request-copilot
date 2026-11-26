# ADR 016: Presentation layer = agent-generated dashboard-as-code; LookML kept as a deploy-ready artefact

Status: accepted (M8b)

## Context

The plan assumed a Looker (Google Cloud core) trial as the presentation layer. The trial is not available: free-trial billing projects have no Looker quota. A hand-built Looker Studio report would work, but it needs a human to click through every tile, which is the opposite of the project's idea: the agent produces the work, the human reviews it at a gate.

## Decision

1. **Semantic layer unchanged.** Definitions live in the domain pack. Mart views (`spb_marts`) and the LookML project are generated from it. LookML stays in `looker/`, unit-tested offline, ready to deploy if an instance ever exists. Looker becomes optional; its steps stay documented (`docs/GCP_SETUP.md` section 12). `make lookml-publish` and `make lookml-check` stay unimplemented and say so ("requires a Looker instance").
2. **Presentation = dashboard-as-code.** `copilot dashboard build [--scan]` generates `dashboards/specs/<id>.yaml` (pages, tiles, metric ids, dimensions, filters, pack thresholds) deterministically from the pack and DASHBOARD_SPEC. A renderer turns spec plus data read from `spb_marts` (BigQuery, or DuckDB locally) into one self-contained HTML file (Plotly embedded, no CDN, three tabs, synthetic-data banner, footer with run id, spec hash and data snapshot hash).
3. **The model writes text only.** Titles and captions may come from the gateway (prompt `dashboard_captions`, replay by default, number guard: no number that is not in its input). Without a recording the template captions are used, and the spec records `caption_source`.
4. **Maker–checker for the dashboard.** Before review, every tile's figures are recomputed from independent sources: governed templates through the guarded session, the scan's audit log, the run folders. Any mismatch sets the dashboard to BLOCKED and gate GD refuses to approve it.
5. **Gate GD.** `copilot gate GD <id> --approve|--reject --reason ...` is logged in the dashboard's hash-chained audit log. Only an approved dashboard is copied to `docs/dashboard/` (with `evidence.md` and `manifest.json`), and a GitHub Pages workflow publishes that folder. CI needs no credentials: the data is baked into the approved HTML.
6. **GD naming.** GD was defined as the approval of a new metric definition. It now names the governance decision for either subject; the audit payload carries `subject` (`dashboard` here).

## Consequences

- Looker Studio is not used, so no logic lives in a BI tool. Ratios are computed only in the marts and in the figures code, and both are reconciled.
- Looker's own access question (`spb-looker` reading the marts) stays open until an instance exists; the service account is kept for that.
- The published page embeds the Plotly bundle (about 5 MB). Acceptable for a static page.
- A dashboard is only as fresh as its scan: the reconciliation compares the marts (latest scan) with the scan run the dashboard was built for, so a newer scan blocks it until rebuilt.

## V-VAR-01 retune (pack 1.3.0), recorded here because the dashboard exposed it

The variance check warned in almost every month on the Data risk page. Cause on the default dataset:

- `chargeback_rate_count`: 1 to 20 chargebacks per country-month; sampling noise alone is 1/sqrt(n), so no relative threshold separates signal from noise. The check was removed from this metric (the same events are watched by `chargeback_rate_value` and `chargeback_amount_eur`).
- December peak and January fall move volume, count and chargeback amount by 25-30 % in every country: threshold 0.25 to 0.35.
- `refund_rate_value`: moves up to 48 % on small monthly counts: threshold 0.25 to 0.60.

R1 (IT, June and July) and the August and September defects are still flagged; no FAIL changed. See the pack CHANGELOG.
