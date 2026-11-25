# GCP Setup — BigQuery Showcase Environment

This guide creates the Google Cloud environment for the showcase mode: an isolated project, EU datasets, least-privilege identities without keys, cost guardrails (sections 1–11), and the time-boxed Looker trial (section 12). The BigQuery part takes about 30–45 minutes; Looker about 45 minutes more.

**Order matters:** set up BigQuery now; create the Looker trial only when the mart views and the generated LookML are ready (milestone M8a), because the 30-day clock starts at creation.

> Commands use `PROJECT` as a placeholder. Run them from your own machine with the Google Cloud CLI installed (https://cloud.google.com/sdk/docs/install).

---

## 1. Create a dedicated project

A separate project keeps the demo isolated and easy to delete.

```bash
gcloud auth login
gcloud projects create PROJECT --name="SPB Oversight Demo"
gcloud config set project PROJECT
```

Project ids are global; add a suffix, for example `spb-oversight-demo-4821`.

## 2. Billing and budget

1. In the console, open **Billing** and link a billing account to the project. New Google Cloud accounts may be eligible for free-trial credits; check in the console.
2. Why not sandbox mode: the BigQuery sandbox has restrictions (such as default table expiration and features that are not available) that get in the way of snapshots, batch loads, and the managed MCP server.
   **Check before `make bq-setup`:** the project must really be linked to the billing account (Billing → project list). Without it BigQuery silently runs in sandbox mode: every table and partition gets a 60-day expiration, and our 2024–2025 partitions are deleted the moment they are loaded (the load job reports success and the tables are empty). The expiration cannot be removed until billing is linked ("Billing has not been enabled for this project"). `make bq-setup` clears the expirations once billing is enabled, and stops with that message otherwise.
3. Expected cost at this data size is close to zero, because BigQuery has a free monthly usage tier for storage and queries. Check the current limits on the BigQuery pricing page.
4. Create a budget: **Billing → Budgets & alerts → Create budget**, scope = this project, amount = €10, alert thresholds at 50%, 90%, 100%. Budgets send alerts; they do not stop spending. The hard limit per query is `COPILOT_MAX_BYTES_BILLED`.

## 3. Enable APIs

```bash
gcloud services enable \
  bigquery.googleapis.com \
  bigquerystorage.googleapis.com \
  iam.googleapis.com \
  iamcredentials.googleapis.com
```

## 4. Service accounts (no keys)

```bash
gcloud iam service-accounts create spb-loader  --display-name="SPB data loader"
gcloud iam service-accounts create spb-copilot --display-name="SPB copilot (read-only + ops)"
gcloud iam service-accounts create spb-looker  --display-name="SPB Looker connection (marts read-only)"

PROJECT=$(gcloud config get-value project)
LOADER="spb-loader@${PROJECT}.iam.gserviceaccount.com"
COPILOT="spb-copilot@${PROJECT}.iam.gserviceaccount.com"
LOOKERSA="spb-looker@${PROJECT}.iam.gserviceaccount.com"
ME="you@example.com"   # your Google account

# All three identities may run BigQuery jobs in the project
gcloud projects add-iam-policy-binding "$PROJECT" --member="serviceAccount:${LOADER}"   --role="roles/bigquery.jobUser"
gcloud projects add-iam-policy-binding "$PROJECT" --member="serviceAccount:${COPILOT}"  --role="roles/bigquery.jobUser"
gcloud projects add-iam-policy-binding "$PROJECT" --member="serviceAccount:${LOOKERSA}" --role="roles/bigquery.jobUser"

# You may impersonate them (no JSON keys needed)
gcloud iam service-accounts add-iam-policy-binding "$LOADER"  --member="user:${ME}" --role="roles/iam.serviceAccountTokenCreator"
gcloud iam service-accounts add-iam-policy-binding "$COPILOT" --member="user:${ME}" --role="roles/iam.serviceAccountTokenCreator"
```

Data access is granted **per dataset** in step 6, not at project level.

Never create or download a service account key. The repository must never contain credentials.

## 5. Application Default Credentials

```bash
gcloud auth application-default login
gcloud auth application-default set-quota-project "$PROJECT"
```

Your user credentials are only the **source** identity. The code impersonates:

- `LOADER_SA` for `make data` (writes source, group and control datasets)
- `COPILOT_SA` for everything the copilot does (reads data, writes snapshots and ops)

Only `make bq-setup` and `make views` run as you, the project owner.

Fill `.env`:

```
GCP_PROJECT_ID=<PROJECT>
BQ_LOCATION=EU
BQ_DATASET_PREFIX=spb
LOADER_SA=spb-loader@<PROJECT>.iam.gserviceaccount.com
COPILOT_SA=spb-copilot@<PROJECT>.iam.gserviceaccount.com
COPILOT_BACKEND=bigquery
```

## 6. Datasets, access and tables

```bash
make bq-setup
```

This creates the six datasets in the `EU` location and applies dataset-level access exactly as in `docs/ARCHITECTURE.md` §7.4:

| Dataset | spb-loader | spb-copilot | spb-looker |
|---|---|---|---|
| `spb_raw` | WRITER | READER | — |
| `spb_group` | WRITER | READER | — |
| `spb_control` | WRITER | READER | — |
| `spb_snapshots` | — | WRITER | — |
| `spb_ops` | — | WRITER | READER |
| `spb_marts` | — | READER | READER |

Marts are views over the source datasets. For `spb-looker` to query them without reading the sources directly, `make bq-setup` registers `spb_marts` as an **authorized dataset** on `spb_raw`, `spb_group` and `spb_control`. Verify this pattern against current BigQuery documentation when implementing.

It also creates the tables with their physical design: `src_transactions` partitioned by day and clustered, with a mandatory partition filter.

## 7. Load data and deploy views

```bash
make data TARGET=bigquery     # as spb-loader
make views                    # as you; generated SQL committed under dashboards/views/
copilot dq scan --as-of 2025-10-15   # as spb-copilot; fills spb_ops for the dashboard
```

Check in the console: all datasets in `EU`; `src_transactions` shows partitioning and clustering; `spb_marts` contains the views.

## 8. Verify least privilege

```bash
make verify-iam
```

Expected results:

- The copilot identity **cannot** write to `spb_raw` (permission denied from BigQuery).
- The loader identity **cannot** read `spb_ops`.
- A DDL or DML statement sent through the copilot is rejected by the SQL policy **before** reaching BigQuery.
- A query exceeding `COPILOT_MAX_BYTES_BILLED` is rejected at dry-run.

These are good screenshots for the README: they show the controls actually work.

## 9. Optional: Google's managed BigQuery MCP server

To contrast **ungoverned SQL access** with the project's **governed metric access** (scenario S0), connect Google's managed BigQuery MCP server to an MCP client such as Claude Desktop, next to `copilot mcp serve`.

- Documentation: https://docs.cloud.google.com/bigquery/docs/use-bigquery-mcp
- Endpoint: `https://bigquery.googleapis.com/mcp`
- Use a read-only identity and grant only the roles the documentation lists for calling MCP tools and running queries.

## 10. Optional: Looker Studio public fallback

Only if you want a public link after the Looker trial ends.

1. Open Looker Studio, then **Create → Data source → BigQuery** → your project → `spb_marts` → one data source per view.
2. Credentials: owner's credentials, so viewers of a shared link can see the data. Acceptable here because the data is synthetic and the views are small.
3. Set data freshness to 12 hours to limit repeated queries.
4. Rebuild the key tiles from `docs/DASHBOARD_SPEC.md`, then share as **Anyone with the link → Viewer**.

## 11. Cost hygiene and clean-up

- Snapshots expire automatically after 90 days.
- Ops tables are tiny; the transaction table is the only sizeable one (well under the free storage tier).
- `INFORMATION_SCHEMA.JOBS` with the `request_id` label shows the cost of each copilot run.
- To remove everything:

```bash
gcloud projects delete "$PROJECT"
```

## 12. Looker (Google Cloud core) trial

### 12.1 What the trial gives you

- A 30-day instance with the features of the Standard edition, limited to 1 Developer, 1 Standard and 5 Viewer users.
- One trial instance per Google Cloud project.
- **After 30 days the instance converts automatically to a paid Standard instance** unless you delete it. Deleting the instance cancels the trial.
- Looker itself is not charged during the trial; BigQuery queries it runs are billed as usual (small at this data size).

Put a calendar reminder at **day 25** to collect the evidence (12.7) and delete the instance (12.8).

### 12.2 Before you begin

```bash
gcloud services enable looker.googleapis.com servicenetworking.googleapis.com
```

1. Make sure you have the Looker Admin role on the project (as project owner you do).
2. Create an **OAuth client**, which Looker uses to sign you in:
   - **APIs & Services → OAuth consent screen**: configure it (external, testing mode, add your own account as a test user).
   - **APIs & Services → Credentials → Create credentials → OAuth client ID → Web application.**
   - After the instance exists, add its redirect URI to the client, as the Looker documentation describes.
3. Keep the client secret out of the repo.

### 12.3 Create the instance

Console: **Looker** product page → **30-DAY TRIAL** → instance name, an **EU region**, OAuth client id and secret → **Create**.

Or with gcloud:

```bash
gcloud looker instances create spb-looker-trial \
  --oauth-client-id=CLIENT_ID \
  --oauth-client-secret=CLIENT_SECRET \
  --region=<EU region from the Looker locations page> \
  --edition="core-trial"
```

Creation takes a while. Note the instance URL; it becomes `LOOKER_BASE_URL` in `.env`.

### 12.4 Connect Looker to BigQuery

In Looker: **Admin → Database → Connections → Add connection**

- Dialect: Google BigQuery Standard SQL
- Billing project: your project; default dataset: `spb_marts`
- Identity: `spb-looker` (read-only on `spb_marts` and `spb_ops`). Prefer a keyless authentication option if the connection offers one. If the only option is a service account key, create it for `spb-looker` only, upload it to Looker, delete the local copy, and record the exception in an ADR. It never goes into the repo.
- Set a maximum billing limit on the connection if available.
- Test the connection.

### 12.5 LookML project and Git

1. Create an empty GitHub repository, for example `spb-oversight-lookml` (private is fine).
2. In Looker: **Develop → Projects → New LookML project** `spb_oversight`, blank project.
3. Configure Git for the project: Looker shows a deploy key; add it to the GitHub repository's deploy keys **with write access**.
4. From the main repository:

```bash
make lookml          # generate looker/ from the domain pack
make lookml-publish  # git subtree push of looker/ to LOOKML_REMOTE
```

5. In Looker (development mode): pull from remote → **Validate LookML** → deploy to production.

### 12.6 MCP Toolbox for Claude Code

1. Create API credentials for your Looker user (in the user's settings in **Admin → Users**). Put client id and secret in your local `.env` only.
2. Install the MCP Toolbox for Databases binary, following https://googleapis.github.io/genai-toolbox/how-to/connect-ide/looker_mcp
3. Register it as an MCP server for Claude Code in the project's `.mcp.json`, using **environment variable references only**:

```json
{
  "mcpServers": {
    "looker": {
      "command": "/path/to/toolbox",
      "args": ["--stdio", "--prebuilt", "looker"],
      "env": {
        "LOOKER_BASE_URL": "${LOOKER_BASE_URL}",
        "LOOKER_CLIENT_ID": "${LOOKER_CLIENT_ID}",
        "LOOKER_CLIENT_SECRET": "${LOOKER_CLIENT_SECRET}",
        "LOOKER_VERIFY_SSL": "true"
      }
    }
  }
}
```

Check the exact arguments against the guide. Then ask Claude Code to list the Looker tools; you should see tools such as `get_models`, `query`, `make_dashboard`, `add_dashboard_element` and the LookML project file tools.

The same server can be added to Claude Desktop for the Level 1 demo (scenario S0).

### 12.7 Collect the evidence (by day 25)

- One screenshot per dashboard at 1600 px width into `dashboards/screenshots/`
- A 2-minute walkthrough video (risk event vs data event, hover on a definition, one MCP question), linked from the README
- Confirm that `looker/` in the main repo and the LookML project repo are in sync

### 12.8 Delete the instance (before day 30)

```bash
gcloud looker instances delete spb-looker-trial --region=<region>
```

Then confirm in the console that no Looker instance remains in the project, and check billing a few days later.

### 12.9 Rebuild test

Before deleting, create a second LookML project in the same instance directly from the LookML repo and validate it. This proves the semantic layer and dashboards can be rebuilt from Git alone.
