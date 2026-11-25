"""M2 acceptance on DuckDB: clean data gives zero FAILs, and each defect triggers exactly its
expected checks with no collateral FAILs. R1 triggers no FAIL. Chargeback censoring is WARN only.
"""

from __future__ import annotations

import pytest

from tests.validation.acceptance import (
    ALLOWED_COLLATERAL_WARNS,
    CLEAN_WARNS,
    expected,
    fails_by_month,
    findings,
    ids,
    in_defect_slice,
    run_suite,
)

DEFECTS = ["D1", "D2", "D3", "D4"]
CHARGEBACK_METRICS = {"chargeback_amount_eur", "chargeback_rate_value", "chargeback_rate_count"}


@pytest.fixture(scope="module")
def suite(small_dataset, pack):
    cache: dict = {}

    def get(name: str):
        if name not in cache:
            cache[name] = run_suite(pack, small_dataset(name).backend)
        return cache[name]

    return get


def test_clean_data_gives_zero_fails(suite) -> None:
    fs = findings(suite("clean"))
    assert ids(fs, "FAIL") == set(), [f for f in fs if f.status == "FAIL"]
    assert ids(fs, "WARN") <= CLEAN_WARNS


@pytest.mark.parametrize("defect", DEFECTS)
def test_each_defect_triggers_exactly_its_expected_checks(suite, defect, expected_defects) -> None:
    exp_fail, exp_warn = expected(expected_defects, defect)
    fs = findings(suite(defect))
    assert ids(fs, "FAIL") == exp_fail
    outside = [
        f for f in fs if f.status == "FAIL" and not in_defect_slice(expected_defects, defect, f.s)
    ]
    assert not outside, outside
    raised = ids(fs, "FAIL") | ids(fs, "WARN")
    assert exp_warn <= raised
    assert ids(fs, "WARN") <= exp_fail | exp_warn | ALLOWED_COLLATERAL_WARNS


def test_d1_fails_on_every_metric_served_by_the_group_product(suite, pack) -> None:
    fs = [f for f in findings(suite("D1")) if f.shape == "sweep" and f.status == "FAIL"]
    group_served = {
        m.id for m in pack.metrics.values() if m.preferred_source == "grp_merchant_monthly"
    }
    pk = {f.metric_id for f in fs if f.check_id == "V-PK-01"}
    assert pk == group_served
    assert {f.s["source"] for f in fs if f.check_id == "V-PK-01"} == {"grp_merchant_monthly"}
    rec = {f.metric_id for f in fs if f.check_id == "V-REC-01"}
    assert rec == {m.id for m in pack.metrics.values() if m.reconciles_to}


def test_s2_shape_catches_d1_on_es_august(suite) -> None:
    fs = [f for f in findings(suite("D1")) if f.shape == "s2" and f.status == "FAIL"]
    assert {f.check_id for f in fs} == {"V-PK-01", "V-REC-01", "V-DUAL-01"}
    assert {(f.s["period"], f.s["merchant_country"]) for f in fs} == {("2025-08-01", "ES")}


def test_d2_gives_no_collateral_dual_path_fail(suite) -> None:
    rep = suite("D2")
    duals = [c for r in rep.values() for c in r.checks if c.check_id == "V-DUAL-01"]
    assert duals and all(c.status == "PASS" for c in duals)


def test_d2_reconciliation_reports_an_explicit_unallocated_line(suite, pack) -> None:
    rec = [c for c in suite("D2")["sweep"].checks if c.check_id == "V-REC-01"]
    assert rec and all(c.status == "WARN" for c in rec)
    for c in rec:
        unalloc = [line for line in c.measured["lines"] if line["line"] == "unallocated"]
        assert {line["period"] for line in unalloc} == {"2025-09-01"}
        measures = pack.metrics[c.metric_id].reconciles_to.measures
        assert sorted(line["measure"] for line in unalloc) == sorted(measures)
        assert all(line["result"] > 0 and line["ledger"] == 0 for line in unalloc)
        assert {
            "period": "2025-09-01",
            "line": "unallocated",
            "status": "WARN",
        } in c.affected_slices


def test_combined_defects_fail_exactly_per_month(suite, expected_defects) -> None:
    by_month = fails_by_month(findings(suite("all")))
    d1, _ = expected(expected_defects, "D1")
    sept = set().union(*(expected(expected_defects, d)[0] for d in ("D2", "D3", "D4")))
    assert by_month == {"2025-08": d1, "2025-09": sept}


def test_r1_is_a_risk_event_not_a_data_event(suite) -> None:
    fs = [f for f in findings(suite("clean")) if f.shape == "r1"]
    assert ids(fs, "FAIL") == set()
    assert ids(fs, "WARN") <= {"V-VAR-01"}


def test_chargeback_censoring_is_warn_never_fail(suite) -> None:
    for name in ("clean", "D1", "D2", "D3", "D4", "all"):
        sweep = suite(name)["sweep"]
        mat = {c.metric_id: c for c in sweep.checks if c.check_id == "V-MAT-01"}
        assert set(mat) == CHARGEBACK_METRICS
        for c in mat.values():
            assert c.status == "WARN"
            assert [s["period"] for s in c.affected_slices] == ["2025-08-01", "2025-09-01"]
    clean = suite("clean")
    cb_fails = [
        c
        for r in clean.values()
        for c in r.checks
        if c.metric_id in CHARGEBACK_METRICS and c.status == "FAIL"
    ]
    assert cb_fails == []


def test_s1_on_clean_data_passes_or_warns_only(suite) -> None:
    rep = suite("clean")["s1"]
    assert rep.fails() == []
    prior = [c for c in rep.checks if c.check_id == "V-PRIOR-01"]
    assert prior and prior[0].status == "PASS" and prior[0].measured["compared"] > 0


@pytest.mark.slow
def test_full_scale_clean_and_combined(full_clean, full_default, pack, expected_defects) -> None:
    clean = findings(run_suite(pack, full_clean.backend))
    assert ids(clean, "FAIL") == set()
    assert ids([f for f in clean if f.shape == "r1"], "WARN") <= {"V-VAR-01"}
    by_month = fails_by_month(findings(run_suite(pack, full_default.backend, only=("sweep",))))
    d1, _ = expected(expected_defects, "D1")
    sept = set().union(*(expected(expected_defects, d)[0] for d in ("D2", "D3", "D4")))
    assert by_month == {"2025-08": d1, "2025-09": sept}
