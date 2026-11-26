"""Isometric 3D flow animation for the Supervisory Request Copilot (`poe flow-media`).

Subcommands (run `python scripts/flow_animation.py --help`):
  full OUT.mp4              the whole timeline, for the README GIF
  web OUT.mp4 SEGS.json     the walkthrough video: each step's reveal, with segment times
  segments                  print the segment timings the walkthrough uses
  preview T [T ...]         still frames at the given times, into --out-dir
  build                     regenerate docs/img/flow.gif, docs/walkthrough/flow.mp4 and
                            flow.webm, and the timings embedded in docs/walkthrough/index.html

Needs Pillow (the `docs` dependency group), ffmpeg on the PATH, and the Inter fonts
(--font-dir, default /usr/share/fonts/opentype/inter/) plus DejaVu Sans Mono (--mono-font).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

W, H, S = 1280, 720, 2  # output size, supersampling
FPS = 30
FONT_DIR = "/usr/share/fonts/opentype/inter/"
MONO_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"


def inter(weight: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(os.path.join(FONT_DIR, f"Inter-{weight}.otf"), size)


def font(weight: str, size: int) -> ImageFont.FreeTypeFont:
    return inter(weight, size * S)


BG = (13, 17, 28)
GRID = (30, 38, 56)
COL = {
    "llm": (139, 92, 246),
    "code": (59, 130, 246),
    "gate": (245, 158, 11),
    "data": (16, 185, 129),
    "pub": (20, 184, 166),
    "req": (148, 163, 184),
}
TW, TH = 112, 56  # iso tile size (1x)
OX, OY = 425, 192


def iso(gx, gy, z=0):
    return ((OX + (gx - gy) * TW / 2) * S, (OY + (gx + gy) * TH / 2 - z) * S)


# id, gx, gy, kind, label1, label2, height, caption
NODES = [
    (
        "req",
        0,
        0,
        "req",
        "Request",
        "supervisor",
        28,
        "A supervisor sends an ambiguous data request",
    ),
    (
        "intake",
        2,
        0,
        "llm",
        "Intake",
        "agent · Claude",
        44,
        "Intake agent asks clarifying questions and builds a typed request spec",
    ),
    ("g1", 4, 0, "gate", "G1", "human gate", 34, "Gate G1: a human approves the spec"),
    (
        "analyst",
        6,
        0,
        "llm",
        "Analyst",
        "agent · Claude",
        44,
        "Analyst agent picks the plan; it never writes the numbers",
    ),
    (
        "sql",
        8,
        0,
        "code",
        "Governed SQL",
        "from glossary",
        40,
        "SQL is compiled from the versioned glossary; no free SQL",
    ),
    (
        "bq",
        8,
        2,
        "data",
        "BigQuery",
        "EU · snapshots",
        62,
        "Runs on GCP BigQuery (EU) with byte caps, snapshots and keyless IAM",
    ),
    (
        "check",
        6,
        2,
        "code",
        "Checker",
        "13 checks · code",
        44,
        "Independent code checker: reconciliation, completeness, dual-path recompute",
    ),
    ("g2", 4, 2, "gate", "G2", "human gate", 34, "Gate G2: a human decides on every FAIL"),
    (
        "evid",
        2,
        2,
        "code",
        "Evidence",
        "+ audit log",
        40,
        "Evidence pack and hash-chained audit log; every run can be re-run",
    ),
    (
        "g3",
        0,
        2,
        "gate",
        "G3",
        "sign-off",
        34,
        "Gate G3: human sign-off before anything is delivered",
    ),
    (
        "dash",
        0,
        4,
        "llm",
        "Dashboard",
        "agent-built",
        44,
        "Agent builds the monitoring dashboard; every tile is reconciled",
    ),
    (
        "gd",
        2,
        4,
        "gate",
        "GD",
        "approval",
        34,
        "Gate GD: a human approves the dashboard before publishing",
    ),
    (
        "pages",
        4,
        4,
        "pub",
        "GitHub Pages",
        "via Actions",
        36,
        "GitHub Actions publishes only the approved page",
    ),
]
SIDE = [
    ("mcp", 8, 4, "code", "MCP server", "no SQL tool", 40),
    ("asst", 6, 4, "llm", "AI assistant", "self-service", 36),
]
SIDE_CAPTION = "Governed MCP server: AI assistants get the same definitions, with no SQL tool"
BYID = {n[0]: n for n in NODES + SIDE}


def shade(c, k):
    return tuple(max(0, min(255, int(v * k))) for v in c)


def mix(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def box(d, gx, gy, h, col, glow=0.0, half=0.40):
    """Draw an iso box centred on (gx,gy) with footprint half*tile."""

    def pts(z: float) -> list[tuple[float, float]]:
        return [
            iso(gx - half, gy - half, z),
            iso(gx + half, gy - half, z),
            iso(gx + half, gy + half, z),
            iso(gx - half, gy + half, z),
        ]

    b, t = pts(0), pts(h)
    top = mix(shade(col, 1.15), (255, 255, 255), 0.25 * glow)
    d.polygon([t[3], t[2], b[2], b[3]], fill=shade(col, 0.62))  # left face
    d.polygon([t[1], t[2], b[2], b[1]], fill=shade(col, 0.82))  # right face
    d.polygon(t, fill=top)
    d.line(t + [t[0]], fill=shade(col, 1.35), width=S)
    return t


def text_c(d, xy, s, f, fill):
    w = d.textlength(s, font=f)
    d.text((xy[0] - w / 2, xy[1]), s, font=f, fill=fill)


@dataclass(frozen=True)
class Fonts:
    pt: ImageFont.FreeTypeFont
    p: ImageFont.FreeTypeFont
    pb: ImageFont.FreeTypeFont
    ph: ImageFont.FreeTypeFont
    mono: ImageFont.FreeTypeFont
    title: ImageFont.FreeTypeFont
    sub: ImageFont.FreeTypeFont
    lab: ImageFont.FreeTypeFont
    lab2: ImageFont.FreeTypeFont
    cap: ImageFont.FreeTypeFont
    leg: ImageFont.FreeTypeFont
    step: ImageFont.FreeTypeFont


@lru_cache(maxsize=1)
def fonts() -> Fonts:
    """Built on first use, so --font-dir and --mono-font apply."""
    return Fonts(
        pt=font("Bold", 14),
        p=font("Regular", 13),
        pb=font("SemiBold", 13),
        ph=font("SemiBold", 11),
        mono=ImageFont.truetype(MONO_PATH, 12 * S),
        title=font("Bold", 30),
        sub=font("Medium", 16),
        lab=font("SemiBold", 15),
        lab2=font("Regular", 12),
        cap=font("SemiBold", 22),
        leg=font("Medium", 13),
        step=font("Bold", 14),
    )


def styles() -> dict[str, tuple[tuple[int, int, int], ImageFont.FreeTypeFont]]:
    fn = fonts()
    return {
        "q": ((148, 163, 184), fn.p),
        "a": ((241, 245, 249), fn.pb),
        "ok": ((74, 222, 128), fn.pb),
        "fail": ((248, 113, 113), fn.pb),
        "warn": ((251, 191, 36), fn.pb),
        "code": ((147, 197, 253), fn.mono),
        "dim": ((120, 132, 152), fn.p),
        "row": ((226, 232, 240), fn.mono),
        "hdr": ((125, 211, 252), fn.ph),
    }


EX = {
    "req": [
        ("hdr", "THE REQUEST"),
        ("a", "“Chargeback rate by merchant"),
        ("a", "country, July–August 2025,"),
        ("a", "monthly. Needed by Friday.”"),
    ],
    "intake": [
        ("hdr", "CLARIFYING QUESTIONS"),
        ("q", "Value- or count-based rate?"),
        ("a", "→ Value-based"),
        ("q", "Which as-of date?"),
        ("a", "→ 2025-10-15"),
        ("q", "All countries in scope?"),
        ("a", "→ Yes, no subset"),
    ],
    "g1": [
        ("hdr", "CONFIRMED SPEC"),
        ("code", "metric  chargeback_rate_value"),
        ("code", "grain   month × country"),
        ("code", "period  2025-07 .. 2025-08"),
        ("code", "as_of   2025-10-15"),
        ("ok", "✓ Approved by the requester"),
    ],
    "analyst": [
        ("hdr", "QUERY PLAN"),
        ("a", "Governed template v1.1.0"),
        ("q", "Source: group data product"),
        ("q", "(preferred path)"),
        ("dim", "The agent chooses; code computes."),
    ],
    "sql": [
        ("hdr", "COMPILED FROM THE GLOSSARY"),
        ("code", "SUM(chargeback_amount_eur)"),
        ("code", "/ SUM(settled_volume_eur)"),
        ("code", "GROUP BY month, country"),
        ("dim", "No free SQL; parser-enforced policy"),
    ],
    "bq": [
        ("hdr", "EXECUTION"),
        ("a", "BigQuery, EU region"),
        ("q", "Snapshot taken, byte cap applied"),
        ("q", "Result hash stored for rerun"),
    ],
    "check": [
        ("hdr", "13 CHECKS (CODE)"),
        ("fail", "✗ V-PK-01  duplicate rows ES Aug"),
        ("fail", "✗ V-REC-01 does not reconcile"),
        ("fail", "✗ V-DUAL-01 recompute differs"),
        ("ok", "✓ completeness, timeliness,"),
        ("ok", "   definition, schema, SQL ..."),
    ],
    "g2": [
        ("hdr", "HUMAN DECISION"),
        ("a", "Deliver from the independent"),
        ("a", "path; keep FAILs as a"),
        ("a", "documented limitation"),
        ("ok", "✓ Rerun: V-REC-01 now PASS"),
        ("dim", "Accepted FAILs stay visible"),
    ],
    "evid": [
        ("hdr", "EVIDENCE"),
        ("q", "Failure explainer (Claude)"),
        ("q", "explains the ES duplicates;"),
        ("q", "number guard checks every figure"),
        ("a", "Hash-chained audit log"),
        ("a", "Rerun gives identical hashes"),
    ],
    "g3": [
        ("hdr", "DELIVERED ANSWER"),
        ("row", "month    ctry  rate"),
        ("row", "2025-07  ES    0.37%"),
        ("row", "2025-08  ES    0.35%"),
        ("row", "2025-08  FR    0.31%"),
        ("row", "2025-08  DE    0.26%"),
        ("warn", "! 2 WARNs acknowledged"),
        ("ok", "✓ Signed off: DELIVERED"),
    ],
    "dash": [
        ("hdr", "MONITORING"),
        ("q", "Same governed definitions feed"),
        ("q", "the early-warning dashboard"),
        ("fail", "IT Jun-25: 1.23%  RED"),
        ("dim", "threshold 0.90%"),
    ],
    "gd": [
        ("hdr", "BEFORE PUBLISHING"),
        ("ok", "✓ 25/25 tiles reconciled"),
        ("ok", "✓ Human approval, with reason"),
        ("dim", "Logged in the audit trail"),
    ],
    "pages": [
        ("hdr", "PUBLISHED"),
        ("a", "Only the approved page"),
        ("a", "goes live on GitHub Pages"),
        ("dim", "CI holds no credentials"),
    ],
    "side": [
        ("hdr", "SELF-SERVICE VIA MCP"),
        ("q", "AI assistant asks:"),
        ("a", "“Chargeback rate, Italy,"),
        ("a", "June 2025?”"),
        ("code", "run_metric → 1.23%"),
        ("dim", "Same definition, no SQL tool"),
    ],
}
PX0, PY0, PX1, PY1 = 952, 150, 1252, 470


def draw_panel(d, t, k, phase):
    fn, sty_map = fonts(), styles()
    if phase == "flow" and k >= 0:
        key = NODES[k][0]
        t0 = INTRO + k * STEP + (TRAVEL if k > 0 else 0)
    elif phase == "side":
        key = "side"
        t0 = SIDE_T
    else:
        return
    a = ease((t - t0) / 0.4)
    if a <= 0:
        return
    d.rounded_rectangle(
        [PX0 * S, PY0 * S, PX1 * S, PY1 * S],
        radius=12 * S,
        fill=(19, 25, 40),
        outline=(51, 65, 85),
        width=S,
    )
    d.text(((PX0 + 18) * S, (PY0 + 14) * S), "Worked example", font=fn.pt, fill=(241, 245, 249))
    d.text(((PX0 + 140) * S, (PY0 + 16) * S), "synthetic data", font=fn.ph, fill=(100, 116, 139))
    d.line(
        [(PX0 + 18) * S, (PY0 + 40) * S, (PX1 - 18) * S, (PY0 + 40) * S], fill=(51, 65, 85), width=S
    )
    y = PY0 + 54
    for i, (sty, txt) in enumerate(EX[key]):
        ai = ease((t - t0 - 0.12 * i) / 0.35)
        if ai <= 0:
            break
        col, f = sty_map[sty]
        col = mix((19, 25, 40), col, ai)
        if sty == "hdr" and i > 0:
            y += 6
        d.text(((PX0 + 18) * S, y * S), txt, font=f, fill=col)
        y += 26 if sty != "hdr" else 24


def ease(t):
    t = max(0, min(1, t))
    return t * t * (3 - 2 * t)


# ---------- timeline ----------
INTRO, TRAVEL, REVEAL, HOLD = 3.0, 0.75, 1.6, 5.0
STEP = TRAVEL + REVEAL + HOLD
SIDE_MOVE = 2.6
SIDE_T = INTRO + STEP * len(NODES)
OUTRO_T = SIDE_T + SIDE_MOVE + HOLD
END = OUTRO_T + 1.0 + HOLD


def state(t):
    """Return (active index, travel progress 0..1 to the active node, phase)."""
    if t < INTRO:
        return -1, 0, "intro"
    if t < SIDE_T:
        k = int((t - INTRO) // STEP)
        local = (t - INTRO) - k * STEP
        return k, ease(local / TRAVEL) if k > 0 else 1.0, "flow"
    if t < OUTRO_T:
        return len(NODES) - 1, 1.0, "side"
    return len(NODES) - 1, 1.0, "outro"


def frame(t):
    fn = fonts()
    im = Image.new("RGB", (W * S, H * S), BG)
    d = ImageDraw.Draw(im)
    k, prog, phase = state(t)

    # ground grid
    for i in range(-1, 11):
        d.line([iso(i, -1), iso(i, 5.2)], fill=GRID, width=S)
    for j in range(-1, 6):
        d.line([iso(-1, j), iso(10, j)], fill=GRID, width=S)

    # edges
    path = [(n[1], n[2]) for n in NODES]
    for i in range(len(path) - 1):
        a, b = path[i], path[i + 1]
        done = phase != "flow" or i < k - 1 or (i == k - 1 and prog >= 1)
        partial = phase == "flow" and i == k - 1
        d.line([iso(*a), iso(*b)], fill=(55, 65, 90), width=3 * S)
        if done or partial:
            pt = prog if partial and not done else 1
            e = (a[0] + (b[0] - a[0]) * pt, a[1] + (b[1] - a[1]) * pt)
            d.line([iso(*a), iso(*e)], fill=(125, 211, 252), width=4 * S)
    # side edges (dashed)
    side_on = phase in ("side", "outro")
    for a, b in [("bq", "mcp"), ("mcp", "asst")]:
        A, B = BYID[a], BYID[b]
        for s in range(10):
            if s % 2:
                continue
            p0 = (A[1] + (B[1] - A[1]) * s / 10, A[2] + (B[2] - A[2]) * s / 10)
            p1 = (A[1] + (B[1] - A[1]) * (s + 1) / 10, A[2] + (B[2] - A[2]) * (s + 1) / 10)
            d.line(
                [iso(*p0), iso(*p1)], fill=(125, 211, 252) if side_on else (55, 65, 90), width=3 * S
            )

    # nodes, painter's order
    labels = []
    allnodes = [(n, i) for i, n in enumerate(NODES)] + [(n, None) for n in SIDE]
    for n, i in sorted(allnodes, key=lambda x: x[0][1] + x[0][2]):
        nid, gx, gy, kind, l1, l2, h = n[:7]
        reached = i is not None and (phase != "flow" or i < k or (i == k and prog >= 1))
        is_side = i is None
        lit = reached or (is_side and side_on)
        active = (i == k and phase == "flow" and prog >= 1) or (is_side and phase == "side")
        pulse = 0.5 + 0.5 * math.sin(t * 7) if active else 0
        col = COL[kind] if lit else mix(COL[kind], BG, 0.62)
        hh = h + (10 * ease(min(1, (t % 1000))) if active else 0)
        top = box(d, gx, gy, hh, col, glow=pulse * 0.6)
        cx = sum(p[0] for p in top) / 4
        cy = min(p[1] for p in top)
        labels.append((n, i, cx, cy, lit, reached))
    for n, _i, cx, cy, lit, reached in labels:
        nid, gx, gy, kind, l1, l2, h = n[:7]
        wpill = max(d.textlength(l1, font=fn.lab), d.textlength(l2, font=fn.lab2)) + 16 * S
        d.rounded_rectangle(
            [cx - wpill / 2, cy - 48 * S, cx + wpill / 2, cy - 8 * S],
            radius=6 * S,
            fill=(13, 17, 28),
        )
        lc = (240, 244, 252) if lit else (110, 120, 140)
        text_c(d, (cx, cy - 46 * S), l1, fn.lab, lc)
        text_c(d, (cx, cy - 27 * S), l2, fn.lab2, (170, 180, 200) if lit else (90, 98, 115))
        # gate approved tick
        if kind == "gate" and reached:
            d.ellipse([cx + 34 * S, cy - 50 * S, cx + 54 * S, cy - 30 * S], fill=(34, 197, 94))
            d.line(
                [
                    (cx + 38 * S, cy - 40 * S),
                    (cx + 43 * S, cy - 35 * S),
                    (cx + 50 * S, cy - 46 * S),
                ],
                fill=(255, 255, 255),
                width=3 * S,
            )
        # checker mini tiles
        if nid == "check" and reached:
            cols = [(34, 197, 94)] * 9 + [(245, 158, 11)] * 2 + [(239, 68, 68)] * 2
            for j, c in enumerate(cols):
                x = cx - 52 * S + (j % 13) * 8 * S
                d.rectangle([x, cy - 6 * S, x + 6 * S, cy - 1 * S], fill=c)

    # travelling packet
    if phase == "flow" and k > 0 and prog < 1:
        a, b = path[k - 1], path[k]
        p = (a[0] + (b[0] - a[0]) * prog, a[1] + (b[1] - a[1]) * prog)
        x, y = iso(*p, 18)
        for r, c in [
            (22, (56, 189, 248, 50)),
            (14, (125, 211, 252, 120)),
            (8, (224, 242, 254, 255)),
        ]:
            d.ellipse(
                [x - r * S, y - r * S, x + r * S, y + r * S],
                fill=c[:3] if r == 8 else mix(c[:3], BG, 1 - c[3] / 255),
            )
    if phase == "side" and t - SIDE_T < SIDE_MOVE:
        st = min(1.0, (t - SIDE_T) / SIDE_MOVE)
        A, B, C = BYID["bq"], BYID["mcp"], BYID["asst"]
        if st < 0.5:
            u = ease(st / 0.5)
            p = (A[1] + (B[1] - A[1]) * u, A[2] + (B[2] - A[2]) * u)
        else:
            u = ease((st - 0.5) / 0.5)
            p = (B[1] + (C[1] - B[1]) * u, B[2] + (C[2] - B[2]) * u)
        x, y = iso(*p, 18)
        d.ellipse([x - 9 * S, y - 9 * S, x + 9 * S, y + 9 * S], fill=(224, 242, 254))

    draw_panel(d, t, k, phase)

    # header
    d.text((40 * S, 28 * S), "Supervisory Request Copilot", font=fn.title, fill=(240, 244, 252))
    d.text(
        (40 * S, 66 * S),
        "Governed agentic AI for second-line risk data oversight",
        font=fn.sub,
        fill=(148, 163, 184),
    )
    # legend
    lx, ly = 1010 * S, 30 * S
    for i, (kname, lab) in enumerate(
        [
            ("llm", "LLM agent (Claude)"),
            ("code", "Deterministic code"),
            ("gate", "Human gate"),
            ("data", "Data (BigQuery)"),
            ("pub", "Publish"),
        ]
    ):
        y = ly + i * 20 * S
        d.rounded_rectangle([lx, y + 3 * S, lx + 12 * S, y + 15 * S], radius=3 * S, fill=COL[kname])
        d.text((lx + 20 * S, y), lab, font=fn.leg, fill=(203, 213, 225))

    # caption bar
    cap, stepno = "", ""
    if phase == "flow" and k >= 0:
        cap, stepno = NODES[k][7], f"{k + 1}/{len(NODES)}"
    elif phase == "side":
        cap, stepno = SIDE_CAPTION, "+"
    elif phase == "intro":
        cap = "From an ambiguous request to an approved, published answer"
    if cap:
        y0 = (H - 78) * S
        d.rectangle([0, y0, W * S, H * S], fill=(20, 26, 42))
        d.line([0, y0, W * S, y0], fill=(51, 65, 85), width=S)
        x = 40 * S
        if stepno:
            d.rounded_rectangle(
                [x, y0 + 24 * S, x + 52 * S, y0 + 52 * S], radius=8 * S, fill=(30, 58, 95)
            )
            text_c(d, (x + 26 * S, y0 + 30 * S), stepno, fn.step, (186, 230, 253))
            x += 70 * S
        d.text((x, y0 + 24 * S), cap, font=fn.cap, fill=(241, 245, 249))

    im = im.resize((W, H), Image.LANCZOS)

    # intro / outro overlays
    if phase == "intro":
        a = 1 - ease((t - 1.6) / 1.4) if t > 1.6 else 1
        ov = Image.new("RGB", (W, H), BG)
        od = ImageDraw.Draw(ov)
        fb, fs = inter("Bold", 46), inter("Medium", 20)
        for s_, f_, y_, c_ in [
            ("Supervisory Request Copilot", fb, 290, (240, 244, 252)),
            ("Agent-built, human-approved, BCBS 239 by design", fs, 360, (148, 163, 184)),
        ]:
            w_ = od.textlength(s_, font=f_)
            od.text(((W - w_) / 2, y_), s_, font=f_, fill=c_)
        im = Image.blend(im, ov, a)
    if phase == "outro":
        a = ease((t - OUTRO_T) / 0.8)
        ov = Image.new("RGB", (W, H), BG)
        od = ImageDraw.Draw(ov)
        fb, fm, fs = inter("Bold", 34), inter("SemiBold", 22), inter("Medium", 17)
        lines = [
            ("LLMs draft and explain", fb, 230, (196, 181, 253)),
            ("Code computes and checks", fb, 285, (147, 197, 253)),
            ("Humans own every gate", fb, 340, (252, 211, 77)),
            (
                "BigQuery · LangGraph · Claude · MCP · GitHub Actions · Pages",
                fs,
                430,
                (148, 163, 184),
            ),
            (
                "github.com/catalin-badea-aurelian/supervisory-request-copilot",
                fm,
                480,
                (125, 211, 252),
            ),
        ]
        for s_, f_, y_, c_ in lines:
            w_ = od.textlength(s_, font=f_)
            od.text(((W - w_) / 2, y_), s_, font=f_, fill=c_)
        im = Image.blend(im, ov, a)
    return im


def segments():
    segs = []
    for k in range(len(NODES)):
        start = 0.0 if k == 0 else INTRO + k * STEP
        hold = INTRO + k * STEP + (TRAVEL if k > 0 else 0) + REVEAL
        segs.append({"label": NODES[k][4], "start": round(start, 3), "hold": round(hold, 3)})
    segs.append(
        {
            "label": "MCP self-service",
            "start": round(SIDE_T, 3),
            "hold": round(SIDE_T + SIDE_MOVE, 3),
        }
    )
    segs.append({"label": "Summary", "start": round(OUTRO_T, 3), "hold": round(OUTRO_T + 1.0, 3)})
    return segs


FFMPEG_COMMON = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24"]


def encode_mp4(out: str, crf: str, extra: list[str]) -> subprocess.Popen[bytes]:
    cmd = [
        *FFMPEG_COMMON, "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
        "-c:v", "libx264", "-crf", crf, "-preset", "veryfast", *extra, "-pix_fmt", "yuv420p", out,
    ]  # fmt: skip
    return subprocess.Popen(cmd, stdin=subprocess.PIPE)


def render_full(out: str) -> int:
    """The whole timeline (README GIF source)."""
    p = encode_mp4(out, "18", [])
    assert p.stdin is not None
    for i in range(int(END * FPS)):
        p.stdin.write(frame(i / FPS).tobytes())
    p.stdin.close()
    return p.wait()


def render_web(out: str, segs_json: str) -> int:
    """The walkthrough video: each step's travel and reveal, back to back, with their times."""
    p = encode_mp4(out, "20", ["-g", "30", "-movflags", "+faststart"])
    assert p.stdin is not None
    segs: list[dict[str, Any]] = []
    cur = 0
    for sg in segments():
        n = int(round((sg["hold"] - sg["start"]) * FPS)) + 1
        for i in range(n):
            p.stdin.write(frame(sg["start"] + i / FPS).tobytes())
        segs.append(
            {
                "label": sg["label"],
                "start": round(cur / FPS, 4),
                "hold": round((cur + n - 1) / FPS, 4),
            }
        )
        cur += n
    p.stdin.close()
    rc = p.wait()
    with open(segs_json, "w") as fh:
        json.dump(segs, fh)
    return rc


def embed_segments(html: Path, segs: list[dict[str, Any]]) -> None:
    """Keep the timings in index.html in sync with the video that was just rendered."""
    text = html.read_text("utf-8")
    new, n = re.subn(
        r"const SEGS = \[.*?\];", lambda _: f"const SEGS = {json.dumps(segs)};", text, count=1
    )
    if n != 1:
        raise SystemExit(f"{html}: no `const SEGS = [...]` line to update")
    html.write_text(new, "utf-8", newline="\n")


def run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True)


def build(args: argparse.Namespace) -> int:
    for tool in ("ffmpeg",):
        if shutil.which(tool) is None:
            raise SystemExit(f"{tool} is not on the PATH")
    gif, walk = Path(args.gif), Path(args.walkthrough_dir)
    walk.mkdir(parents=True, exist_ok=True)
    gif.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(args.work_dir)
    tmp.mkdir(parents=True, exist_ok=True)
    full, segs_json = tmp / "flow_full.mp4", tmp / "segments.json"
    if render_full(str(full)) or render_web(str(walk / "flow.mp4"), str(segs_json)):
        return 1
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(walk / "flow.mp4"), "-c:v", "libvpx-vp9",
         "-crf", "33", "-b:v", "0", "-row-mt", "1", "-an", str(walk / "flow.webm")])  # fmt: skip
    palette = tmp / "palette.png"
    vf = "fps=10,scale=960:-1:flags=lanczos"
    run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(full),
            "-vf",
            f"{vf},palettegen",
            str(palette),
        ]
    )
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(full), "-i", str(palette),
         "-lavfi", f"{vf}[x];[x][1:v]paletteuse", "-loop", "0", str(gif)])  # fmt: skip
    with open(segs_json) as fh:
        embed_segments(walk / "index.html", json.load(fh))
    print(f"wrote {gif}, {walk / 'flow.mp4'}, {walk / 'flow.webm'} and the timings in index.html")
    return 0


def main(argv: list[str] | None = None) -> int:
    global FONT_DIR, MONO_PATH
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--font-dir", default=os.environ.get("FLOW_FONT_DIR", FONT_DIR))
    ap.add_argument("--mono-font", default=os.environ.get("FLOW_MONO_FONT", MONO_PATH))
    sub_ = ap.add_subparsers(dest="cmd", required=True)
    f = sub_.add_parser("full", help="whole timeline to an MP4")
    f.add_argument("out")
    w = sub_.add_parser("web", help="walkthrough MP4 plus segment timings (JSON)")
    w.add_argument("out")
    w.add_argument("segments_json")
    sub_.add_parser("segments", help="print the segment timings as JSON")
    pv = sub_.add_parser("preview", help="still frames at the given times (seconds)")
    pv.add_argument("--out-dir", required=True, help="directory for prev_<t>.jpg")
    pv.add_argument("times", nargs="+", type=float)
    b = sub_.add_parser("build", help="regenerate gif, mp4, webm and the timings in index.html")
    b.add_argument("--gif", default="docs/img/flow.gif")
    b.add_argument("--walkthrough-dir", default="docs/walkthrough")
    b.add_argument("--work-dir", default=".flow-work", help="scratch directory (not committed)")
    args = ap.parse_args(argv)
    FONT_DIR, MONO_PATH = args.font_dir, args.mono_font
    fonts.cache_clear()
    if args.cmd == "full":
        return render_full(args.out)
    if args.cmd == "web":
        return render_web(args.out, args.segments_json)
    if args.cmd == "segments":
        print(json.dumps(segments()))
        return 0
    if args.cmd == "preview":
        out = Path(args.out_dir)
        out.mkdir(parents=True, exist_ok=True)
        for t in args.times:
            frame(t).save(out / f"prev_{t:05.1f}.jpg", quality=90)
        return 0
    return build(args)


if __name__ == "__main__":
    sys.exit(main())
