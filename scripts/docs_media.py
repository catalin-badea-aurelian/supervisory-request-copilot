"""`poe docs-media`: README media from the approved dashboard, reproducibly.

Reads docs/dashboard/index.html and evidence.md (written by gate GD) and writes
- docs/img/risk_overview.png, data_risk.png, request_ops.png: one screenshot per tab, 1440x900
- docs/img/demo.gif: title card, the three tabs with a caption bar, the gate GD evidence card,
  a closing card (about 19 s, well under 8 MB)

Needs the docs dependency group and a Chromium build:
    uv sync --group docs && python -m playwright install chromium
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
DASH = ROOT / "docs" / "dashboard"
IMG = ROOT / "docs" / "img"
SHOTS = {  # tab id in the page -> file name
    "risk_overview": "risk_overview.png",
    "data_risk": "data_risk.png",
    "request_operations": "request_ops.png",
}
CAPTIONS = {
    "risk_overview": "Risk overview: chargeback rate by country and month, RAG from pack limits",
    "data_risk": "Data risk (BCBS 239): check status by month, reconciliation, timeliness",
    "request_operations": "Request operations: runs, escalations, gate decisions",
}
W, H = 1280, 720
BG, FG, MUTED, ACCENT = (27, 31, 36), (255, 255, 255), (170, 178, 189), (46, 125, 50)
FONT_FILES = ["arial.ttf", "segoeui.ttf", "DejaVuSans.ttf", "LiberationSans-Regular.ttf"]
BOLD_FILES = ["arialbd.ttf", "segoeuib.ttf", "DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf"]


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for name in BOLD_FILES if bold else FONT_FILES:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def screenshots() -> dict[str, Path]:
    from playwright.sync_api import sync_playwright

    IMG.mkdir(parents=True, exist_ok=True)
    out: dict[str, Path] = {}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.goto((DASH / "index.html").as_uri())
        page.wait_for_selector(".js-plotly-plot", timeout=30_000)
        for tab, name in SHOTS.items():
            page.click(f'nav button[data-p="{tab}"]')
            page.wait_for_timeout(1500)  # let Plotly finish resizing
            path = IMG / name
            page.screenshot(path=str(path))
            out[tab] = path
        browser.close()
    return out


def evidence() -> dict[str, str]:
    text = (DASH / "evidence.md").read_text("utf-8")

    def grab(pattern: str) -> str:
        m = re.search(pattern, text)
        if not m:
            raise SystemExit(f"evidence.md: missing {pattern!r}; approve the dashboard at gate GD")
        return m.group(1).strip()

    gd = grab(r"Gate GD: \*\*(\w+)\*\* by ([^\n]+?), reason:")
    if gd != "approve":
        raise SystemExit("the dashboard is not approved at gate GD")
    who = grab(r"Gate GD: \*\*\w+\*\* by ([^\n]+?), reason:")
    reason = grab(r"reason: ([^\n]+)")
    rows = re.findall(r"^\| \S+ \| \d+ \| (\w+) \|$", text, re.M)
    return {
        "approver": who,
        "reason": reason,
        "tiles": f"{sum(r == 'OK' for r in rows)}/{len(rows)}",
        "spec": grab(r"Spec sha256: `([0-9a-f]{64})`"),
        "data": grab(r"Data sha256: `([0-9a-f]{64})`"),
    }


def wrap(draw: ImageDraw.ImageDraw, text: str, f, width: int) -> list[str]:
    lines, cur = [], ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if draw.textlength(trial, font=f) <= width:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    return lines + [cur] if cur else lines


def card(title: str, lines: list[tuple[str, str]], accent: bool = False) -> Image.Image:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, 14, H], fill=ACCENT if accent else (90, 100, 112))
    y = 150 if len(lines) < 4 else 90
    for line in wrap(d, title, font(46, True), W - 200):
        d.text((90, y), line, font=font(46, True), fill=FG)
        y += 62
    y += 24
    for label, value in lines:
        if label:
            d.text((90, y), label, font=font(24), fill=MUTED)
            y += 32
        for part in wrap(d, value, font(30), W - 200):
            d.text((90, y), part, font=font(30), fill=FG)
            y += 40
        y += 14
    d.text(
        (90, H - 60),
        "Synthetic data — fictional entity. Demonstration only.",
        font=font(20),
        fill=MUTED,
    )
    return img


def tab_frame(shot: Path, caption: str) -> Image.Image:
    img = Image.new("RGB", (W, H), BG)
    s = Image.open(shot).convert("RGB")
    s = s.resize((W, round(s.height * W / s.width)))
    img.paste(s.crop((0, 0, W, H - 64)), (0, 0))
    d = ImageDraw.Draw(img)
    d.rectangle([0, H - 64, W, H], fill=BG)
    d.text((28, H - 46), caption, font=font(24), fill=FG)
    return img


def gif(shots: dict[str, Path], ev: dict[str, str]) -> Path:
    frames: list[tuple[Image.Image, int]] = [
        (
            card(
                "Supervisory Request Copilot — agent-built, human-approved",
                [("", "A governed workflow for second-line risk data oversight")],
            ),
            3000,
        )
    ]
    frames += [(tab_frame(shots[t], CAPTIONS[t]), 3200) for t in SHOTS]
    frames.append(
        (
            card(
                "Gate GD: dashboard approved by a human",
                [
                    ("Approver", ev["approver"]),
                    ("Reason", ev["reason"]),
                    ("Tiles reconciled against independent sources", ev["tiles"]),
                    ("Spec sha256", ev["spec"][:32] + "…"),
                    ("Data sha256", ev["data"][:32] + "…"),
                ],
                accent=True,
            ),
            4500,
        )
    )
    frames.append(
        (
            card(
                "Numbers from governed SQL · checker is code · humans own every gate",
                [("", "Nothing is published without a recorded approval.")],
            ),
            3200,
        )
    )
    quant = [im.quantize(colors=128, method=Image.Quantize.MEDIANCUT) for im, _ in frames]
    path = IMG / "demo.gif"
    quant[0].save(
        path,
        save_all=True,
        append_images=quant[1:],
        duration=[ms for _, ms in frames],
        loop=0,
        optimize=True,
    )
    return path


def main() -> int:
    if not (DASH / "index.html").is_file():
        print("docs/dashboard/index.html is missing: approve a dashboard at gate GD first")
        return 1
    ev = evidence()
    shots = screenshots()
    path = gif(shots, ev)
    size = path.stat().st_size / 1e6
    print(f"wrote {', '.join(p.relative_to(ROOT).as_posix() for p in shots.values())}")
    print(f"wrote {path.relative_to(ROOT).as_posix()} ({size:.1f} MB)")
    if size > 8:
        print("demo.gif is over 8 MB")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
