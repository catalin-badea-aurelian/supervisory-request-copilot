"""Snapshot role for the copilot identity (M3, ADR 014): print, and on request apply, IAM steps.

Every step is a separate, explicit action. Without --apply this prints the exact commands for
the chosen step and changes nothing. With --apply it runs exactly those commands as you (the
project owner, through the installed Google Cloud SDK), and nothing else.

  python scripts/iam_snapshot_role.py --step a            # print step A
  python scripts/iam_snapshot_role.py --step a --apply    # create the custom role
  python scripts/iam_snapshot_role.py --step b --apply    # bind it on spb_snapshots only
  python scripts/iam_snapshot_role.py --step d --apply    # remove WRITER on spb_snapshots
  python scripts/iam_snapshot_role.py --rollback          # print the rollback commands

Step C (the probe) is `python scripts/verify_iam.py --only snapshots`.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # repo root, for `infra`

from copilot.config import Settings  # noqa: E402
from infra.bigquery import spec  # noqa: E402


def _sdk(tool: str) -> str:
    found = shutil.which(tool) or shutil.which(f"{tool}.cmd")
    if found:
        return found
    local = Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Cloud SDK/google-cloud-sdk/bin"
    for name in (f"{tool}.cmd", tool):
        if (local / name).is_file():
            return str(local / name)
    raise SystemExit(f"{tool} not found: install the Google Cloud SDK or put it on PATH")


def commands(settings: Settings, step: str) -> list[list[str]]:
    p = settings.gcp_project_id
    member = f"serviceAccount:{settings.copilot_sa}"
    role = f"projects/{p}/roles/{spec.SNAPSHOT_ROLE_ID}"
    dataset = f"{p}:{spec.dataset_id(settings.dataset_prefix, 'snapshots')}"
    if step == "a":
        return [
            [
                "gcloud",
                "iam",
                "roles",
                "create",
                spec.SNAPSHOT_ROLE_ID,
                f"--project={p}",
                f"--title={spec.SNAPSHOT_ROLE_TITLE}",
                f"--description={spec.SNAPSHOT_ROLE_DESCRIPTION}",
                "--permissions=" + ",".join(spec.SNAPSHOT_ROLE_PERMISSIONS),
                "--stage=GA",
            ]
        ]
    if step == "b":
        return [["bq", "add-iam-policy-binding", f"--member={member}", f"--role={role}", dataset]]
    if step == "rollback":
        return [
            ["bq", "remove-iam-policy-binding", f"--member={member}", f"--role={role}", dataset],
            ["gcloud", "iam", "roles", "delete", spec.SNAPSHOT_ROLE_ID, f"--project={p}"],
        ]
    raise ValueError(step)


def step_d_plan(settings: Settings) -> str:
    ds = spec.dataset_id(settings.dataset_prefix, "snapshots")
    return (
        f"Remove the dataset access entry  role=WRITER  userByEmail={settings.copilot_sa}\n"
        f"from {settings.gcp_project_id}:{ds} (no other entry is touched).\n"
        "Rollback: re-add the same entry (python scripts/bq_setup.py --apply re-adds it only if "
        "ACCESS['snapshots'] still lists it)."
    )


def apply_step_d(settings: Settings) -> None:
    from copilot.data.bigquery_clients import make_client

    client = make_client(settings)  # the owner (developer ADC), not a service account
    ds_id = f"{settings.gcp_project_id}.{spec.dataset_id(settings.dataset_prefix, 'snapshots')}"
    ds = client.get_dataset(ds_id)
    target = settings.copilot_sa.lower()
    keep = [
        e
        for e in ds.access_entries
        if not (e.role == "WRITER" and str(e.entity_id).lower() == target)
    ]
    removed = len(ds.access_entries) - len(keep)
    if removed != 1:
        raise SystemExit(f"expected exactly one WRITER entry for {target}, found {removed}")
    ds.access_entries = keep
    client.update_dataset(ds, ["access_entries"])
    print(f"removed WRITER for {target} on {ds_id}")


def show(cmd: list[str]) -> str:
    return " ".join(f'"{c}"' if " " in c else c for c in cmd)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="iam_snapshot_role")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--step", choices=["a", "b", "d"])
    g.add_argument("--rollback", action="store_true")
    ap.add_argument("--apply", action="store_true", help="execute (default: print only)")
    args = ap.parse_args(argv)
    settings = Settings.from_env()
    if not settings.gcp_project_id or not settings.copilot_sa or "<" in settings.copilot_sa:
        raise SystemExit("GCP_PROJECT_ID and COPILOT_SA must be set in .env")
    if args.rollback:
        for c in commands(settings, "rollback"):
            print(show(c))
        print("\n(printed only; run them yourself if you decide to roll back)")
        return 0
    if args.step == "d":
        print(step_d_plan(settings))
        if args.apply:
            apply_step_d(settings)
        else:
            print("\nDRY RUN: nothing changed. Re-run with --apply to execute.")
        return 0
    cmds = commands(settings, args.step)
    for c in cmds:
        print(show(c))
    if not args.apply:
        print("\nDRY RUN: nothing changed. Re-run with --apply to execute.")
        return 0
    for c in cmds:
        exe = _sdk(c[0])
        result = subprocess.run([exe, *c[1:]], check=False)
        if result.returncode != 0:
            print(f"step {args.step} failed (exit {result.returncode}); nothing else was run")
            return result.returncode
    print(f"step {args.step} applied")
    return 0


if __name__ == "__main__":
    sys.exit(main())
