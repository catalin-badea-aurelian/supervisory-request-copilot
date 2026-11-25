# CLAUDE.md — Supervisory Request Copilot

## What this project is

A portfolio-grade, end-to-end demo of an **agentic workflow for second-line (2LoD) risk data oversight** in a fictional EU payments institution, **Synthetic Payments Bank (SPB)**.

It takes a supervisory-style data request ("the supervisor wants X by Friday"), clarifies it until it is unambiguous, produces the figures from governed metric definitions, **independently validates** them, reviews the deliverable against the **BCBS 239** principles using RAG, and hands a complete **evidence pack** to a human for sign-off. Every step is logged; every result can be re-run. An oversight dashboard in **Looker**, generated from the same governed definitions, shows risk and data-quality indicators side by side.

Before writing code, read:

1. `docs/PROJECT_SPEC.md` — what we build and why (scenarios, requirements, milestones, evals)
2. `docs/ARCHITECTURE.md` — how we build it (components, contracts, controls, decisions)
3. `docs/GCP_SETUP.md` — the BigQuery and Looker showcase environment (identities, datasets, cost guardrails, trial lifecycle)
4. `docs/DASHBOARD_SPEC.md` — the LookML model and the oversight dashboards

When in doubt about scope: **fewer features working perfectly beats more features half-working.**

## Non-negotiable design principles

1. **Deterministic orchestration.** The workflow graph is code. LLMs work only inside bounded steps. An LLM may return a routing signal (e.g. `undefined_metric=true`), but code validates it and decides the transition.
2. **Maker–checker.** The agent that produces figures never validates them. Validation is deterministic Python/SQL. An LLM may only *explain* a failed check in plain language.
3. **Numbers never come from an LLM.** Narrative text is written with placeholders (`{{ result.total_rate }}`) that code fills from query results.
4. **Human in the loop at defined gates:** G1 spec approval, G2 escalation on any failed check, G3 final sign-off, GD approval of a new metric definition. Nothing is "delivered" without a G3 decision recorded in the audit log.
5. **Template-first SQL.** Metrics are compiled from the governed glossary in the domain pack. Free-form SQL is allowed only with `non_template=true`, which raises a WARN and requires explicit acknowledgement at G3.
6. **No undefined metrics.** If a request needs a metric that is not in the glossary, stop and run the metric-proposal flow. Never improvise a definition.
7. **Traceable and rerunnable.** Every run writes an append-only, hash-chained audit log and a run manifest. `copilot rerun <request_id>` must reproduce identical result hashes.
8. **Least privilege per agent.** Each agent gets only the tools listed in `docs/ARCHITECTURE.md` §13. All data access is read-only and enforced in code, not in prompts.
9. **One source of definitions.** SQL templates, dashboard marts and the LookML semantic layer are all **generated from the domain pack**. Generated files are never hand-edited; manual adjustments go into LookML refinement files.

## Hard constraints

- **Synthetic data only.** No real institution names, no real customer data, no PII.
- **Fictional entity only.** Never name or imply any real company as the owner or user of this system. The entity is "Synthetic Payments Bank (SPB)"; the authority is "the supervisor".
- **No third-party documents in the repo.** `scripts/fetch_corpus.py` downloads regulatory PDFs at runtime into `corpus/` (gitignored). The README and evidence packs quote only short excerpts, always with source and paragraph reference.
- **No secrets in the repo.** Use `.env` (gitignored) and keep `.env.example` current. MCP server configuration references environment variables only; it never contains values.
- **Defect manifest is test-only.** The planted-defect configuration and `tests/fixtures/defects_expected.yaml` must never be readable by agents, tools, or prompts.
- **Two run modes, both must keep working.**
  - **Showcase mode (primary): BigQuery + Looker**, location `EU`, partitioned tables, least-privilege identities, snapshots, LookML model and dashboards.
  - **Local mode: DuckDB**, so anyone can clone and run the copilot without a cloud account (no dashboard in this mode).
- **No service account keys for the code.** The code uses Application Default Credentials with service-account impersonation. Never create, download, or commit JSON keys. If the Looker → BigQuery connection requires a key, it lives only inside Looker and the exception is recorded in an ADR.
- **Looker API credentials** (client id and secret for the MCP Toolbox) live only in your local environment.
- **Looker trial is time-boxed (30 days).** Everything needed to rebuild it lives in Git; the instance must be deleted before day 30 (see `docs/GCP_SETUP.md` §12).
- **Every BigQuery job** sets `maximum_bytes_billed` and job labels (`request_id`, `component`, lowercase).

## Stack

| Concern | Choice |
|---|---|
| Language / env | Python 3.11+, `uv` |
| Quality | `ruff` (lint + format), `mypy` (lenient), `pytest`, pre-commit, GitHub Actions |
| Orchestration | LangGraph `StateGraph`, `interrupt()` for human gates, SQLite checkpointer |
| LLM | Anthropic Python SDK behind one gateway module; model IDs from config only |
| Contracts | Pydantic v2 for every agent input/output |
| Data | BigQuery via `google-cloud-bigquery` (showcase, EU location), DuckDB (local mode), `sqlglot` for SQL policy + dialect transpilation |
| Semantic layer + BI | **Looker (Google Cloud core, trial)**: LookML views, explores and **LookML dashboards generated from the domain pack**, versioned in `looker/` |
| BI automation | MCP Toolbox for Databases (Looker source), used by Claude Code to validate, query and prototype dashboards |
| BI fallback (optional) | Looker Studio on the same `spb_marts` views, for a public view-only link |
| MCP (own server) | Official Python MCP SDK (FastMCP) |
| RAG | `pypdf` ingestion, `sentence-transformers` embeddings + `rank-bm25`, flat local index |
| Output | Jinja2 → Markdown + HTML evidence packs |
| CLI | Typer + Rich |

Configuration (`.env.example`):

```
ANTHROPIC_API_KEY=
COPILOT_MODEL_MAIN=claude-sonnet-5-5
COPILOT_MODEL_FAST=claude-haiku-5-5
COPILOT_BACKEND=bigquery          # showcase; use duckdb for local mode
COPILOT_DUCKDB_PATH=./data/spb.duckdb
GCP_PROJECT_ID=
BQ_LOCATION=EU
BQ_DATASET_PREFIX=spb             # spb_raw, spb_group, spb_control, spb_snapshots, spb_ops, spb_marts
COPILOT_SA=spb-copilot@<project>.iam.gserviceaccount.com
LOADER_SA=spb-loader@<project>.iam.gserviceaccount.com
COPILOT_MAX_BYTES_BILLED=1000000000
COPILOT_EMBEDDING_MODEL=BAAI/bge-small-en-v1.5
LOOKER_BASE_URL=https://<instance>.looker.app
LOOKER_CLIENT_ID=
LOOKER_CLIENT_SECRET=
LOOKER_VERIFY_SSL=true
LOOKER_PROJECT=spb_oversight
LOOKML_REMOTE=git@github.com:<you>/spb-oversight-lookml.git
```

## Commands (keep these working)

```
make setup          # uv sync + pre-commit install
make bq-setup       # create EU datasets and dataset-level access (run once, as project owner)
make data           # generate synthetic data (seed 42); TARGET=bigquery (default in showcase) or duckdb
make data-clean     # same, without planted defects (used for false-positive tests)
make views          # generate mart views from the domain pack and deploy them to spb_marts
make lookml         # generate LookML (views, explores, model, dashboards) from the domain pack into looker/
make lookml-publish # push looker/ to the Looker project repo (git subtree)
make lookml-check   # validate the LookML project and run the dashboard consistency test through Looker
make verify-iam     # prove least privilege: denied writes, denied reads, SQL policy, byte limit
make corpus         # fetch + index the BCBS 239 corpus
make test           # pytest (no network, no LLM calls)
make eval           # evaluation harness (calls the LLM API) -> evals/REPORT.md
make demo           # the demo scenarios end to end, with scripted human answers
copilot ask "..."   # interactive run, human gates in the terminal
copilot resume <id> # resume a paused run (e.g. waiting at G3)
copilot rerun <id>  # reproduce a past run and compare hashes
copilot audit verify <id>
copilot dq scan --as-of 2025-10-15   # Level 2: run the check catalogue across metrics x months x countries
copilot ops export  # push run telemetry, check results and gate decisions to spb_ops
copilot mcp serve   # start the governed-data MCP server (usable from Claude Desktop)
```

## Development environment

- The primary development machine runs **Windows** (PowerShell, VS Code). CI runs on Linux.
- Line endings are LF in the repository and the working copy (`.gitattributes`).
- `make` is not installed on Windows by default. In M0, either document how to install GNU Make on Windows, or provide a cross-platform task runner that exposes the same command names listed above. Do not let the Windows setup change the command names in this file.
- Paths in code use `pathlib`; no hard-coded separators.

## Build order

Follow the milestones in `docs/PROJECT_SPEC.md` §9 **in order**. Do not start a milestone until the previous one meets its acceptance criteria and `make test` is green.

The validation engine (M2) is built and fully tested **before** any LLM agent exists.

The MVP is **M0–M5 plus M8** (Looker oversight dashboard). M8 can start as soon as M2 is done, because the dashboards read the marts and the DQ scan output, not the agents.

**Looker trial timing:** do everything in M8 that does not need an instance first (mart views, LookML generation, unit tests on the generated LookML). Ask the user to create the trial instance only then, so the 30-day window is used for validation, iteration and the live demo.

**Dashboard workflow in M8:**
1. Generate LookML from the domain pack (`make lookml`) and publish it to the Looker project repo.
2. Validate the project through the Looker MCP tools or the Looker API; fix errors in the generator, never in generated files.
3. Prototype or adjust tiles interactively with the MCP dashboard tools if useful. Then export the result as LookML (`.dashboard.lookml`) and make the generator or a refinement file reproduce it. **LookML dashboards in Git are the source of truth**; user-defined dashboards are disposable.
4. Run the consistency test through Looker queries (FR-D6).

## Coding conventions

- Small, typed functions; I/O only in adapters (`data/`, `llm/`, `audit/`, `looker/`).
- Every check in `src/copilot/validation/checks/` declares: `id`, `name`, BCBS 239 principle refs, default severity, thresholds read from the domain pack, a pure function, and unit tests against clean and defective fixtures.
- Prompts live in `src/copilot/prompts/*.md` with a front-matter `id` and `version`; both are written to the audit log on every call.
- All LLM outputs are parsed into Pydantic models. On validation failure: one retry with the error message, then escalate to a human gate. Never coerce silently.
- Generated files (SQL views, LookML) start with a `GENERATED FROM domain pack <id>@<version> — DO NOT EDIT` header.
- Use the audit module for anything that matters; no `print` debugging left in code.
- Unit tests never call the LLM API or Looker; use recorded fixtures or fakes. Evals and `make lookml-check` call external services and run only explicitly.
- Regenerate `examples/` (sample evidence packs, audit logs) from real runs whenever output formats change; README snippets must match real output.

## Definition of done (whole project)

- Showcase: data, marts and ops tables live in BigQuery (EU). The LookML project validates with zero errors. The three oversight dashboards exist as LookML dashboards in Git and render in the Looker instance. Screenshots are in `dashboards/screenshots/` and a 2-minute walkthrough video is linked in the README.
- Dashboard figures equal copilot results for the same slices (automated consistency test through Looker).
- The Looker trial instance is deleted before day 30; rebuilding the LookML project from Git alone is tested once (a fresh project in the same instance, `docs/GCP_SETUP.md` §12.9).
- Local mode: on a clean machine with only `ANTHROPIC_API_KEY` set, `git clone && make setup data corpus demo` completes with `COPILOT_BACKEND=duckdb`.
- Scenarios S1–S4 behave exactly as specified in `docs/PROJECT_SPEC.md` §6.
- Evals meet the targets in `docs/PROJECT_SPEC.md` §10: 100% planted-defect detection, 0 false FAILs on clean data, 100% valid citations, identical hashes on rerun.
- README contains: architecture diagram, quickstart, scenario walkthroughs, dashboard screenshots and video, the controls → BCBS 239 mapping, eval results, limitations, and the disclaimer.
