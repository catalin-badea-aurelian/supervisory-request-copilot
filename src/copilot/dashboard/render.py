"""Renderer: spec + figures -> one self-contained HTML file (Plotly embedded, no CDN).

Every number on the page comes from `tile_figures`, the same function the reconciliation
runs on the reference. Status is always written as text as well as colour.
"""

from __future__ import annotations

import html
import json
from typing import Any

import plotly.graph_objects as go
from plotly.offline import get_plotlyjs

from copilot.dashboard.data import ALL, Data, classify
from copilot.dashboard.figures import tile_figures
from copilot.dashboard.spec import DashboardSpec, Tile
from copilot.packs.models import DomainPack

RAG = {"GREEN": "#2e7d32", "AMBER": "#f9a825", "RED": "#c62828", "NONE": "#9e9e9e"}
STATUS = {"PASS": RAG["GREEN"], "WARN": RAG["AMBER"], "FAIL": RAG["RED"]}
PLOT_CONFIG = {"displayModeBar": False, "responsive": True}

CSS = """
body{font-family:system-ui,Segoe UI,Arial,sans-serif;margin:0;color:#1b1f24;background:#f5f6f8}
.banner{background:#1b1f24;color:#fff;padding:8px 20px;font-size:14px}
h1{font-size:20px;margin:16px 20px 4px}
nav{margin:8px 20px;display:flex;gap:6px}
nav button{border:1px solid #c5cad1;background:#fff;padding:8px 14px;cursor:pointer;font-size:14px}
nav button.on{background:#1b1f24;color:#fff}
.page{display:none;padding:0 20px 20px}.page.on{display:block}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(420px,1fr));gap:14px}
.tile{background:#fff;border:1px solid #dde1e6;border-radius:6px;padding:12px;overflow:auto}
.tile.kpi{min-height:0}
.tile h3{font-size:14px;margin:0 0 2px}
.cap{font-size:12px;color:#5b6572;margin:0 0 8px}
.big{font-size:30px;font-weight:600}
.pill{display:inline-block;padding:2px 8px;border-radius:10px;color:#fff;font-size:12px;
margin-left:8px}
table{border-collapse:collapse;font-size:12px;width:100%}
th,td{border:1px solid #e3e6ea;padding:3px 6px;text-align:left}
.wide{grid-column:1/-1}
footer{padding:12px 20px;font-size:12px;color:#5b6572;border-top:1px solid #dde1e6}
code{font-size:11px}
"""

JS = """
function show(id){document.querySelectorAll('.page').forEach(p=>p.classList.toggle('on',p.id===id));
document.querySelectorAll('nav button').forEach(b=>b.classList.toggle('on',b.dataset.p===id));
window.dispatchEvent(new Event('resize'));}
document.querySelectorAll('nav button').forEach(b=>b.onclick=()=>show(b.dataset.p));
"""


def fmt(pack: DomainPack, metric_id: str, v: float) -> str:
    unit = pack.metrics[metric_id].unit
    if unit == "ratio":
        return f"{v * 100:.2f}%"
    if unit == "per_mille":
        return f"{v:.2f}‰"
    if unit == "bps":
        return f"{v:.1f} bps"
    return f"€{v:,.0f}"


def _e(x: Any) -> str:
    return html.escape(str(x))


def _split(figs: dict[str, Any]) -> list[tuple[str, str, Any]]:
    return [(*k.split("|", 1), v) for k, v in figs.items()]


def _rag(t: Tile, v: float) -> str:
    th = t.thresholds
    return classify(v, th.amber, th.red, th.direction) if th else "NONE"


def _plot(tid: str, fig: go.Figure) -> str:
    fig.update_layout(margin=dict(l=40, r=10, t=10, b=40), height=320, template="simple_white")
    spec = fig.to_json()
    return (
        f'<div id="plot-{tid}"></div><script>(function(){{var f={spec};'
        f'Plotly.newPlot("plot-{tid}",f.data,f.layout,{json.dumps(PLOT_CONFIG)});}})();</script>'
    )


def _kpi(t: Tile, figs: dict[str, Any], pack: DomainPack) -> str:
    if t.data == "metric_value":
        assert t.metric_id
        items = sorted(figs.items())
        month, v = items[-1][0].split("|")[0], items[-1][1]
        text = fmt(pack, t.metric_id, v)
        rag = _rag(t, v)
        pill = f'<span class="pill" style="background:{RAG[rag]}">{rag if rag != "NONE" else ""}</span>'
        prev = items[0][1] if len(items) > 1 else None
        delta = f"{(v - prev) / abs(prev) * 100:+.1f}% vs previous month" if prev else ""
        return (
            f'<div class="big">{_e(text)}{pill if rag != "NONE" else ""}</div>'
            f'<div class="cap">{_e(month[:7])} · {_e(delta)}</div>'
        )
    if t.data == "requests":
        v = figs["value"]
        m = t.params["measure"]
        text = (
            "n/a"
            if v is None
            else f"{v * 100:.0f}%"
            if m == "on_time_rate"
            else f"{v:,.2f}"
            if m in ("median_duration_hours", "avg_cost_eur")
            else f"{v:,}"
        )
        return f'<div class="big">{_e(text)}</div>'
    if t.data == "dq_cells":
        return f'<div class="big">{figs["count"]}</div><div class="cap">cells (metric × month × country)</div>'
    if t.data == "timeliness":
        return f'<div class="big">{len(figs)}</div><div class="cap">partitions</div>'
    if t.data == "lineage":
        pct = figs["covered"] / figs["total"] * 100 if figs["total"] else 0
        return f'<div class="big">{pct:.0f}%</div><div class="cap">{figs["covered"]} of {figs["total"]} metrics</div>'
    return ""


def _heatmap(t: Tile, figs: dict[str, Any], pack: DomainPack) -> str:
    rows = _split(figs)
    months = sorted({r[0] for r in rows})
    countries = sorted({r[1] for r in rows})
    val = {(m, c): v for m, c, v in rows}
    z, text = [], []
    for c in countries:
        z.append(
            [
                {"GREEN": 0, "AMBER": 1, "RED": 2, "NONE": 0}[_rag(t, val[(m, c)])]
                if (m, c) in val
                else None
                for m in months
            ]
        )
        text.append(
            [
                f"{fmt(pack, t.metric_id or '', val[(m, c)])}<br>{_rag(t, val[(m, c)])}"
                if (m, c) in val
                else ""
                for m in months
            ]
        )
    fig = go.Figure(
        go.Heatmap(
            z=z,
            x=[m[:7] for m in months],
            y=countries,
            text=text,
            texttemplate="%{text}",
            colorscale=[[0, RAG["GREEN"]], [0.5, RAG["AMBER"]], [1, RAG["RED"]]],
            zmin=0,
            zmax=2,
            showscale=False,
            hoverinfo="text",
        )
    )
    fig.update_layout(height=340)
    return _plot(t.id, fig)


def _lines(t: Tile, figs: dict[str, Any], pack: DomainPack) -> str:
    rows = _split(figs)
    series: dict[str, list[tuple[str, float]]] = {}
    for m, c, v in rows:
        series.setdefault(c, []).append((m, v))
    fig = go.Figure()
    for name, pts in sorted(series.items()):
        pts.sort()
        fig.add_trace(
            go.Scatter(
                x=[p[0][:7] for p in pts], y=[p[1] for p in pts], name=name, mode="lines+markers"
            )
        )
    if t.thresholds:
        fig.add_hline(
            y=t.thresholds.amber, line_dash="dash", line_color=RAG["AMBER"], annotation_text="amber"
        )
        fig.add_hline(
            y=t.thresholds.red, line_dash="dash", line_color=RAG["RED"], annotation_text="red"
        )
    return _plot(t.id, fig)


def _bar(t: Tile, figs: dict[str, Any], pack: DomainPack) -> str:
    fig = go.Figure()
    if t.data == "metric_value":
        rows = sorted(_split(figs), key=lambda r: r[1])
        fig.add_trace(
            go.Bar(
                x=[r[1] for r in rows],
                y=[r[2] for r in rows],
                marker_color=[RAG[_rag(t, r[2])] for r in rows],
                text=[fmt(pack, t.metric_id or "", r[2]) for r in rows],
            )
        )
        if t.thresholds:
            fig.add_hline(
                y=t.thresholds.amber,
                line_dash="dash",
                line_color=RAG["AMBER"],
                annotation_text="amber",
            )
            fig.add_hline(
                y=t.thresholds.red, line_dash="dash", line_color=RAG["RED"], annotation_text="red"
            )
    elif t.data == "xx_volume":
        items = sorted(figs.items())
        fig.add_trace(
            go.Bar(x=[k[:7] for k, _ in items], y=[v for _, v in items], marker_color=RAG["NONE"])
        )
    elif t.data == "principles":
        items = sorted(figs.items(), key=lambda kv: int(kv[0][1:]))
        fig.add_trace(
            go.Bar(
                x=[k for k, _ in items],
                y=[v * 100 for _, v in items],
                marker_color=[
                    RAG["GREEN"] if v >= 0.99 else RAG["AMBER"] if v >= 0.95 else RAG["RED"]
                    for _, v in items
                ],
                text=[f"{v * 100:.1f}%" for _, v in items],
            )
        )
        fig.update_yaxes(title="pass rate (%)", range=[0, 100])
    else:  # gates
        by_gate: dict[str, dict[str, int]] = {}
        for g, dec, n in _split(figs):
            by_gate.setdefault(g, {})[dec] = n
        for dec in sorted({d for v in by_gate.values() for d in v}):
            fig.add_trace(
                go.Bar(
                    name=dec, x=sorted(by_gate), y=[by_gate[g].get(dec, 0) for g in sorted(by_gate)]
                )
            )
        fig.update_layout(barmode="stack")
    return _plot(t.id, fig)


def _table(t: Tile, figs: dict[str, Any], pack: DomainPack) -> str:
    if t.data == "breaches":
        rows = sorted(figs.items(), key=lambda kv: (kv[1]["rag"] != "RED", kv[0].split("|")[1]))
        body = "".join(
            f"<tr><td>{_e(pack.metrics[k.split('|')[0]].name)}</td><td>{_e(k.split('|')[2])}</td>"
            f"<td>{_e(k.split('|')[1][:7])}</td><td>{_e(fmt(pack, k.split('|')[0], v['value']))}</td>"
            f"<td style='color:#fff;background:{RAG[v['rag']]}'>{v['rag']}</td></tr>"
            for k, v in rows
        )
        head = "<tr><th>metric</th><th>country</th><th>month</th><th>value</th><th>RAG</th></tr>"
    elif t.data == "dq_cells":
        rows = sorted(figs.items(), key=lambda kv: (kv[0].split("|")[2], kv[0]))
        body = "".join(
            f"<tr><td>{_e(k.split('|')[0])}</td><td>{_e(k.split('|')[1])}</td>"
            f"<td>{_e(k.split('|')[2][:7])}</td><td>{n} countries</td><td>FAIL</td></tr>"
            for k, n in rows
        )
        head = "<tr><th>metric</th><th>check</th><th>month</th><th>scope</th><th>status</th></tr>"
    elif t.data == "timeliness":
        body = "".join(
            f"<tr><td>{_e(k.split('|')[0])}</td><td>{_e(k.split('|')[1][:7])}</td><td>{s}</td></tr>"
            for k, s in sorted(figs.items())
        )
        head = "<tr><th>table</th><th>month</th><th>status</th></tr>"
    else:  # requests
        body = "".join(
            f"<tr><td>{_e(k)}</td><td>{_e(v['status'])}</td><td>{v['g2_escalations']}</td>"
            f"<td>{'n/a' if v['on_time'] is None else 'yes' if v['on_time'] else 'no'}</td></tr>"
            for k, v in sorted(figs.items())
        )
        head = "<tr><th>request</th><th>status</th><th>G2 escalations</th><th>on time</th></tr>"
    return f"<table>{head}{body or '<tr><td>none</td></tr>'}</table>"


def _matrix(t: Tile, figs: dict[str, Any]) -> str:
    rows = _split(figs)
    checks = sorted({r[0] for r in rows})
    months = sorted({r[1] for r in rows})
    val = {(c, m): s for c, m, s in rows}
    head = "<tr><th>check</th>" + "".join(f"<th>{_e(m[:7])}</th>" for m in months) + "</tr>"
    body = ""
    for c in checks:
        cells = ""
        for m in months:
            s = val.get((c, m), "")
            cells += f"<td style='color:#fff;background:{STATUS.get(s, '#fff')}'>{s}</td>"
        body += f"<tr><td>{_e(c)}</td>{cells}</tr>"
    return f"<table>{head}{body}</table>"


def render_tile(t: Tile, d: Data, pack: DomainPack) -> str:
    figs = tile_figures(t, d, pack)
    if t.kind == "kpi":
        inner = _kpi(t, figs, pack)
    elif t.kind == "heatmap":
        inner = _heatmap(t, figs, pack)
    elif t.kind == "line":
        inner = _lines(t, figs, pack)
    elif t.kind == "bar":
        inner = _bar(t, figs, pack)
    elif t.kind == "matrix":
        inner = _matrix(t, figs)
    else:
        inner = _table(t, figs, pack)
    wide = " wide" if t.kind in ("heatmap", "matrix", "table") else ""
    cap = f'<p class="cap">{_e(t.caption)}</p>' if t.caption else ""
    return (
        f'<section class="tile {t.kind}{wide}" id="tile-{t.id}" data-tile="{t.id}">'
        f"<h3>{_e(t.title)}</h3>{cap}{inner}</section>"
    )


def render_html(
    spec: DashboardSpec, d: Data, pack: DomainPack, *, spec_sha256: str, data_sha256: str
) -> str:
    nav = "".join(
        f'<button data-p="{p.id}" class="{"on" if i == 0 else ""}">{_e(p.title)}</button>'
        for i, p in enumerate(spec.pages)
    )
    pages = "".join(
        f'<div class="page{" on" if i == 0 else ""}" id="{p.id}"><div class="grid">'
        + "".join(render_tile(t, d, pack) for t in p.tiles)
        + "</div></div>"
        for i, p in enumerate(spec.pages)
    )
    footer = (
        f"Dashboard <code>{_e(spec.id)}</code> · scan run <code>{_e(spec.scan_run_id)}</code> · "
        f"as-of {_e(spec.as_of)} · pack {_e(spec.pack_id)}@{_e(spec.pack_version)} · "
        f"captions: {_e(spec.caption_source)}<br>spec sha256 <code>{spec_sha256}</code> · "
        f"data sha256 <code>{data_sha256}</code>"
    )
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{_e(spec.title)}</title><style>{CSS}</style>"
        f"<script>{get_plotlyjs()}</script></head><body>"
        f"<div class='banner'>{_e(spec.disclaimer)}</div><h1>{_e(spec.title)}</h1>"
        f"<nav>{nav}</nav>{pages}<footer>{footer}</footer><script>{JS}</script></body></html>\n"
    )


__all__ = ["render_html", "render_tile", "fmt", "ALL"]
