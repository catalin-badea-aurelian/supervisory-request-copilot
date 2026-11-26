"""verify-iam: prove least privilege on the live BigQuery project (GCP_SETUP section 8).

Each check prints PASS or FAIL with the evidence (the error BigQuery returned, or what the
guarded layer did). The exit code is non-zero if any check fails.

  copilot-cannot-write-sources   spb-copilot is denied writing spb_raw, spb_group, spb_control
  loader-cannot-read-ops         spb-loader is denied reading spb_ops
  policy-rejects-ddl-dml         DDL/DML is rejected by the SQL policy; no job is submitted
  dry-run-rejects-bytes          a query over the byte limit is rejected at dry run; no job runs
  snapshots                      spb-copilot creates, queries and drops a snapshot with expiry
  snapshots-residual             (after step D) spb-copilot may create an ordinary table in
                                 spb_snapshots (residual, ADR 014), but cannot write data into
                                 it or delete it

The write probes are harmless even if a permission were wrongly granted: they append zero
rows (`SELECT ... WHERE FALSE`) or touch only probe tables that this script removes as the
owner afterwards.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from copilot.config import Settings  # noqa: E402
from copilot.data.bigquery_backend import BigQueryExecutor, source_resolver  # noqa: E402
from copilot.data.bigquery_clients import job_labels, make_client  # noqa: E402
from copilot.data.guarded import BytesLimitExceeded, GuardedBackend  # noqa: E402
from copilot.data.policy import PolicyViolation, SqlPolicy  # noqa: E402
from copilot.data.session import profile_tables  # noqa: E402
from copilot.data.snapshots import (  # noqa: E402
    BigQuerySnapshot,
    create_bigquery_snapshot,
    snapshot_table_name,
)
from copilot.packs.compiler import MetricRequest, compile_metric  # noqa: E402
from copilot.packs.loader import load_pack  # noqa: E402

RID = "verify-iam"
STAMP = datetime.now(UTC).strftime("%Y%m%d%H%M%S")


@dataclass
class Result:
    check: str
    ok: bool | None  # None = inconclusive
    evidence: str


class SpyExecutor:
    """Wraps the real executor and counts what reaches BigQuery."""

    def __init__(self, inner: BigQueryExecutor) -> None:
        self.inner, self.dialect = inner, inner.dialect
        self.dry_runs = 0
        self.runs = 0

    def dry_run(self, sql: str, params: dict[str, Any], labels: dict[str, str]) -> int | None:
        self.dry_runs += 1
        return self.inner.dry_run(sql, params, labels)

    def run(self, sql: str, params: dict[str, Any], labels: dict[str, str], timeout_s: float):
        self.runs += 1
        return self.inner.run(sql, params, labels, timeout_s)

    def describe(self, table_ref: str) -> list[dict[str, str]]:
        return self.inner.describe(table_ref)


def _denied(fn: Callable[[], Any]) -> tuple[bool | None, str]:
    """True if BigQuery denied (403), None if inconclusive (404), False if it succeeded."""
    from google.api_core.exceptions import Forbidden, NotFound

    try:
        fn()
    except Forbidden as e:
        return True, f"403 {e.message[:110]}"
    except NotFound as e:
        return None, f"404 (inconclusive) {e.message[:100]}"
    return False, "the call SUCCEEDED"


def _zero_row_append(client: Any, settings: Settings, dest: str) -> None:
    from google.cloud import bigquery

    cfg = bigquery.QueryJobConfig(
        destination=dest,
        write_disposition="WRITE_APPEND",
        maximum_bytes_billed=settings.max_bytes_billed,
        labels=job_labels("verify-iam", RID),
    )
    client.query(f"SELECT * FROM `{dest}` WHERE FALSE", job_config=cfg).result()


def check_copilot_cannot_write_sources(settings: Settings, copilot: Any) -> list[Result]:
    p, pre = settings.gcp_project_id, settings.dataset_prefix
    targets = {
        "raw": f"{p}.{pre}_raw.src_merchants",
        "group": f"{p}.{pre}_group.grp_merchant_monthly",
        "control": f"{p}.{pre}_control.ewi_thresholds",
    }
    out = []
    for layer, dest in targets.items():
        ok, ev = _denied(lambda d=dest: _zero_row_append(copilot, settings, d))
        out.append(Result(f"copilot-cannot-write-sources ({layer}, append)", ok, ev))

        def create(layer: str = layer) -> None:
            from google.cloud import bigquery

            t = bigquery.Table(
                f"{p}.{pre}_{layer}._verify_iam_probe_{STAMP}",
                schema=[bigquery.SchemaField("x", "INT64")],
            )
            t.expires = datetime.now(UTC) + timedelta(hours=1)
            copilot.create_table(t)

        ok, ev = _denied(create)
        out.append(Result(f"copilot-cannot-write-sources ({layer}, create table)", ok, ev))
    return out


def check_loader_cannot_read_ops(settings: Settings, loader: Any) -> list[Result]:
    ds = f"{settings.gcp_project_id}.{settings.dataset_prefix}_ops"
    ok1, ev1 = _denied(lambda: list(loader.list_tables(ds)))
    ok2, ev2 = _denied(
        lambda: loader.query(
            f"SELECT table_name FROM `{ds}.INFORMATION_SCHEMA.TABLES`",
            job_config=_labelled(settings),
        ).result()
    )
    return [
        Result("loader-cannot-read-ops (list tables)", ok1, ev1),
        Result("loader-cannot-read-ops (query)", ok2, ev2),
    ]


def _labelled(settings: Settings) -> Any:
    from google.cloud import bigquery

    return bigquery.QueryJobConfig(
        maximum_bytes_billed=settings.max_bytes_billed, labels=job_labels("verify-iam", RID)
    )


def _guarded(settings: Settings, pack: Any, spy: SpyExecutor, max_bytes: int) -> GuardedBackend:
    resolver = source_resolver(settings, pack)
    policy = SqlPolicy("bigquery", [resolver(t) for t in profile_tables(pack, "metric")])
    return GuardedBackend(
        spy,
        policy,
        component="verify-iam",
        request_id=RID,
        snapshot_id="live",
        max_bytes=max_bytes,
        timeout_s=settings.query_timeout_s,
        labels_fn=job_labels,
    )


def check_policy_rejects_ddl_dml(settings: Settings, pack: Any, copilot: Any) -> list[Result]:
    t = source_resolver(settings, pack)("src_merchants")
    statements = {
        "DROP": f"DROP TABLE {t}",
        "INSERT": f"INSERT INTO {t} (merchant_id) VALUES ('x')",
        "UPDATE": f"UPDATE {t} SET status = 'x' WHERE TRUE",
        "DELETE": f"DELETE FROM {t} WHERE TRUE",
        "MERGE": f"MERGE {t} AS a USING {t} AS b ON FALSE WHEN NOT MATCHED THEN INSERT ROW",
        "CREATE": f"CREATE TABLE `{settings.gcp_project_id}.{settings.dataset_prefix}_raw.x` "
        "AS SELECT 1 AS a",
        "EXPORT": f"EXPORT DATA OPTIONS (uri='gs://b/x*.csv', format='CSV') AS SELECT * FROM {t}",
        "SCRIPT": f"SELECT 1; DROP TABLE {t}",
    }
    out = []
    for name, sql in statements.items():
        spy = SpyExecutor(BigQueryExecutor(settings, copilot))
        g = _guarded(settings, pack, spy, settings.max_bytes_billed)
        try:
            g.query(sql)
            out.append(Result(f"policy-rejects-ddl-dml ({name})", False, "statement was executed"))
        except PolicyViolation as e:
            reached = spy.dry_runs + spy.runs
            out.append(
                Result(
                    f"policy-rejects-ddl-dml ({name})",
                    reached == 0,
                    f"PolicyViolation {e.code}; calls reaching BigQuery: {reached}",
                )
            )
    return out


def check_dry_run_rejects_bytes(settings: Settings, pack: Any, copilot: Any) -> list[Result]:
    from datetime import date

    req = MetricRequest(
        "settled_volume_eur",
        date(2024, 10, 1),
        date(2025, 9, 30),
        date(2025, 10, 15),
        "month",
        ("period", "merchant_country"),
    )
    q = compile_metric(pack, req, "independent", "bigquery", source_resolver(settings, pack))
    limit = 10 * 1024 * 1024
    spy = SpyExecutor(BigQueryExecutor(settings, copilot))
    g = _guarded(settings, pack, spy, limit)
    try:
        g.query(q.sql, q.params)
        return [Result("dry-run-rejects-bytes", False, f"query ran under a {limit:,}-byte limit")]
    except BytesLimitExceeded as e:
        ok = spy.dry_runs == 1 and spy.runs == 0
        return [
            Result(
                "dry-run-rejects-bytes",
                ok,
                f"{e}; dry runs {spy.dry_runs}, executed jobs {spy.runs}",
            )
        ]


def check_snapshots(settings: Settings, pack: Any, copilot: Any) -> list[Result]:
    from google.cloud import bigquery

    rid = f"verify_iam_{STAMP}"
    snap = BigQuerySnapshot.plan(
        settings.gcp_project_id,
        settings.dataset_prefix,
        pack,
        rid,
        datetime.now(UTC) - timedelta(minutes=1),
        expiration_days=1,
    )
    one = BigQuerySnapshot(
        snap.project,
        snap.dataset,
        snap.run_ts,
        snap.expires_at,
        {"src_merchants": snap.mapping["src_merchants"]},
        {"src_merchants": snap.sources["src_merchants"]},
        snap.snapshot_id,
    )
    out = []
    try:
        create_bigquery_snapshot(copilot, one, job_labels("snapshot", RID))
        out.append(Result("snapshots (create with expiration)", True, one.fq("src_merchants")))
    except Exception as e:  # noqa: BLE001
        return [
            Result("snapshots (create with expiration)", False, f"{type(e).__name__}: {e}"[:200])
        ]
    try:
        n = list(
            copilot.query(
                f"SELECT COUNT(*) AS n FROM `{one.fq('src_merchants')}`",
                job_config=_labelled(settings),
            ).result()
        )[0]["n"]
        out.append(Result("snapshots (query)", n > 0, f"{n} rows"))
    except Exception as e:  # noqa: BLE001
        out.append(Result("snapshots (query)", False, f"{type(e).__name__}: {e}"[:200]))
    try:
        cfg = bigquery.QueryJobConfig(labels=job_labels("snapshot", RID))
        copilot.query(f"DROP SNAPSHOT TABLE `{one.fq('src_merchants')}`", job_config=cfg).result()
        out.append(Result("snapshots (drop)", True, "dropped"))
    except Exception as e:  # noqa: BLE001
        out.append(Result("snapshots (drop)", False, f"{type(e).__name__}: {e}"[:200]))
    return out


def check_snapshots_residual(settings: Settings, copilot: Any, owner: Any) -> list[Result]:
    from google.cloud import bigquery

    name = f"{settings.gcp_project_id}.{settings.dataset_prefix}_snapshots." + snapshot_table_name(
        "_verify_iam_ordinary", STAMP
    )
    out = []
    t = bigquery.Table(name, schema=[bigquery.SchemaField("x", "INT64")])
    try:
        copilot.create_table(t)
        out.append(
            Result(
                "snapshots-residual (create ordinary table: allowed, ADR 014)",
                True,
                f"created {name}",
            )
        )
    except Exception as e:  # noqa: BLE001
        out.append(
            Result(
                "snapshots-residual (create ordinary table)",
                None,
                f"not allowed ({type(e).__name__}); residual checks skipped",
            )
        )
        return out
    try:
        ok, ev = _denied(lambda: _zero_row_append(copilot, settings, name))
        out.append(Result("snapshots-residual (cannot append data)", ok, ev))
        ok, ev = _denied(
            lambda: copilot.query(
                f"INSERT INTO `{name}` (x) VALUES (1)", job_config=_labelled(settings)
            ).result()
        )
        out.append(Result("snapshots-residual (cannot insert with DML)", ok, ev))
        ok, ev = _denied(lambda: copilot.delete_table(name))
        out.append(Result("snapshots-residual (cannot delete the table)", ok, ev))
    finally:
        owner.delete_table(name, not_found_ok=True)  # clean-up as the project owner
    return out


CHECKS = [
    "copilot-cannot-write-sources",
    "loader-cannot-read-ops",
    "policy-rejects-ddl-dml",
    "dry-run-rejects-bytes",
    "snapshots",
    "snapshots-residual",
]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="verify-iam")
    ap.add_argument("--only", nargs="+", choices=CHECKS, help="run only these checks")
    ap.add_argument(
        "--after-step-d",
        action="store_true",
        help="include snapshots-residual (only meaningful after ADR 014 step D)",
    )
    args = ap.parse_args(argv)
    settings = Settings.from_env()
    if not settings.gcp_project_id:
        raise SystemExit("GCP_PROJECT_ID is not configured")
    selected = args.only or [c for c in CHECKS if c != "snapshots-residual" or args.after_step_d]
    pack = load_pack("payments")
    copilot = make_client(settings, impersonate=settings.copilot_sa)
    results: list[Result] = []
    if "copilot-cannot-write-sources" in selected:
        results += check_copilot_cannot_write_sources(settings, copilot)
    if "loader-cannot-read-ops" in selected:
        results += check_loader_cannot_read_ops(
            settings, make_client(settings, impersonate=settings.loader_sa)
        )
    if "policy-rejects-ddl-dml" in selected:
        results += check_policy_rejects_ddl_dml(settings, pack, copilot)
    if "dry-run-rejects-bytes" in selected:
        results += check_dry_run_rejects_bytes(settings, pack, copilot)
    if "snapshots" in selected:
        results += check_snapshots(settings, pack, copilot)
    if "snapshots-residual" in selected:
        results += check_snapshots_residual(settings, copilot, make_client(settings))

    width = max(len(r.check) for r in results)
    for r in results:
        mark = {True: "PASS", False: "FAIL", None: "INCONCLUSIVE"}[r.ok]
        print(f"{mark:<12} {r.check:<{width}}  {r.evidence}")
    failed = [r for r in results if r.ok is not True]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
