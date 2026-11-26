"""Generated LookML (offline; no Looker): coverage, formulas, metadata, headers, references."""

from __future__ import annotations

import re

import sqlglot
import yaml

from copilot.lookml.generate import (
    DISCLAIMER,
    NAMED_FORMATS,
    VALUE_FORMAT,
    body_of,
    generate,
    ratio_sql,
)
from copilot.lookml.write import LOOKER_DIR
from copilot.marts.views import NON_ADDITIVE, UNIT_MULTIPLIER
from copilot.packs.compiler import MetricRequest, compile_metric

FIELD_RE = re.compile(r"^  (dimension|measure|dimension_group): (\w+) \{(.*?)^  \}", re.M | re.S)


def _views(files):
    out = {}
    for path, text in files.items():
        if path.startswith("views/"):
            name = re.search(r"^view: (\w+) \{", text, re.M).group(1)
            out[name] = {m.group(2): (m.group(1), m.group(3)) for m in FIELD_RE.finditer(text)}
    return out


def _field_names(fields):
    names = set()
    for name, (kind, body) in fields.items():
        if kind == "dimension_group":
            tf = re.search(r"timeframes: \[(.*?)\]", body).group(1)
            names |= {f"{name}_{t.strip()}" for t in tf.split(",")}
        else:
            names.add(name)
    return names


def _prop(body, key):
    m = re.search(rf"^\s*{key}: (.*?)(?: ;;)?$", body, re.M)
    return m.group(1) if m else None


def test_committed_project_equals_the_generator(pack):
    files = generate(pack)
    on_disk = {
        p.relative_to(LOOKER_DIR).as_posix()
        for p in LOOKER_DIR.rglob("*")
        if p.is_file() and "refinements" not in p.parts
    }
    assert on_disk == set(files)
    for rel, text in files.items():
        assert (LOOKER_DIR / rel).read_text("utf-8") == text, f"{rel} differs; run make lookml"


def test_every_file_has_the_header_and_an_unchanged_hash(pack):
    import hashlib

    for rel in generate(pack):
        text = (LOOKER_DIR / rel).read_text("utf-8")
        assert text.startswith(
            f"# GENERATED FROM domain pack payments@{pack.pack.version} — DO NOT EDIT\n"
        ), rel
        declared, body = body_of(text)
        assert declared == hashlib.sha256(body.encode("utf-8")).hexdigest(), f"{rel} hand-edited"


def test_every_glossary_metric_has_one_measure_with_its_metadata(pack):
    views = _views(generate(pack))
    found = {}
    for vname, fields in views.items():
        for name, (kind, body) in fields.items():
            tags = _prop(body, "tags")
            if kind == "measure" and tags and "metric_id:" in tags:
                mid = re.search(r'"metric_id:(\w+)"', tags).group(1)
                assert mid not in found, f"{mid} has two measures"
                found[mid] = (vname, name, body)
    approved = {m for m, d in pack.metrics.items() if d.status == "approved"}
    assert set(found) == approved
    for mid, (vname, name, body) in found.items():
        m = pack.metrics[mid]
        assert name == mid
        assert f'"metric_version:{m.version}"' in _prop(body, "tags")
        assert _prop(body, "description").startswith(f'"{mid} v{m.version} | ')
        assert _prop(body, "value_format_name") == VALUE_FORMAT[m.unit]
        assert vname == ("ewi" if mid in NON_ADDITIVE else "ewi_components")


def test_ratio_formulas_match_the_templates(pack):
    fields = _views(generate(pack))["ewi_components"]
    for mid, m in pack.metrics.items():
        if mid in NON_ADDITIVE or m.numerator is None:
            continue
        kind, body = fields[mid]
        assert kind == "measure" and _prop(body, "type") == "number"
        assert _prop(body, "sql") == ratio_sql(m)
        assert f"${{{m.numerator}}}" in body and f"${{{m.denominator}}}" in body
        # the template's multiplier is the one the measure uses
        r = pack.pack.data_range
        q = compile_metric(
            pack,
            MetricRequest(mid, r["start"], r["end"], pack.pack.default_as_of),
            "independent",
            "bigquery",
            lambda n: f"`p.d.{n}`",
        )
        sel = next(
            s for s in sqlglot.parse_one(q.sql, read="bigquery").selects if s.alias_or_name == mid
        )
        mults = [
            int(lit.this)
            for mul in sel.find_all(sqlglot.exp.Mul)
            for lit in mul.find_all(sqlglot.exp.Literal)
            if lit.is_int
        ]
        expected = UNIT_MULTIPLIER[m.unit]
        assert (mults or [1]) == [expected], (mid, mults)
        if mid != "avg_ticket_eur":  # AVG(amount) = SUM / COUNT, the same thing
            assert "SAFE_DIVIDE" in sel.sql(dialect="bigquery")


def test_only_allowed_dimensions_are_exposed(pack):
    fields = _views(generate(pack))["ewi_components"]
    dims = {
        "period" if k == "dimension_group" else n
        for n, (k, _) in fields.items()
        if k in ("dimension", "dimension_group") and n != "pk"
    }
    for mid in pack.metrics:
        if mid not in NON_ADDITIVE:
            assert dims <= set(pack.metrics[mid].allowed_dimensions), mid
    assert "payment_method" not in dims


def test_helper_measures_are_hidden(pack):
    fields = _views(generate(pack))["ewi_components"]
    for name, (kind, body) in fields.items():
        if kind == "measure" and name not in pack.metrics:
            assert "hidden: yes" in body, name


def test_model_explores_and_formats(pack):
    files = generate(pack)
    model = files["spb_oversight.model.lkml"]
    explores = re.findall(r"^explore: (\w+) \{", model, re.M)
    assert explores == [
        "ewi_components",
        "ewi",
        "dq_status",
        "bcbs239",
        "timeliness",
        "metric_catalog",
        "requests",
    ]
    assert model.count(f'description: "{DISCLAIMER}"') == len(explores)
    for name in NAMED_FORMATS:
        assert f"named_value_format: {name} {{" in model
    used = set(re.findall(r"value_format_name: (\w+)", "".join(files.values())))
    builtin = {"decimal_0", "decimal_2", "decimal_4", "percent_0", "percent_1", "percent_2"}
    assert used <= builtin | set(NAMED_FORMATS) | {"eur_2"}


def test_no_project_id_or_secrets_in_the_project(pack):
    text = "".join(generate(pack).values())
    assert "@{GCP_PROJECT}" not in text and "iam.gserviceaccount" not in text
    assert all(
        "`@{DATASET_PREFIX}_marts.mart_" in t
        for p, t in generate(pack).items()
        if p.startswith("views/")
    )


def test_dashboard_fields_exist(pack):
    files = generate(pack)
    views = {v: _field_names(f) for v, f in _views(files).items()}
    views["requests"] |= set()  # joined explore: gate_decisions fields are reachable too
    explore_views = {e: {e} for e in views}
    explore_views["requests"] = {"requests", "gate_decisions"}
    names = []
    for rel, text in files.items():
        if not rel.startswith("dashboards/"):
            continue
        dash = yaml.safe_load(body_of(text)[1])[0]
        names.append(dash["dashboard"])
        filters = {f["name"] for f in dash["filters"]}
        for f in dash["filters"]:
            v, field = f["field"].split(".")
            assert field in views[v], (rel, f["field"])
        for el in dash["elements"]:
            if el["type"] == "text":
                assert DISCLAIMER in el["body_text"]
                continue
            allowed = explore_views[el["explore"]]
            refs = list(el["fields"]) + list(el.get("pivots", [])) + list(el.get("filters", {}))
            refs += [s.split()[0] for s in el.get("sorts", [])] + list(
                el.get("listen", {}).values()
            )
            for ref in refs:
                v, field = ref.split(".")
                assert v in allowed and field in views[v], (rel, el["name"], ref)
            assert set(el.get("listen", {})) <= filters, (rel, el["name"])
        assert any(el["type"] == "text" for el in dash["elements"])
    assert sorted(names) == ["data_risk", "request_operations", "risk_overview"]
