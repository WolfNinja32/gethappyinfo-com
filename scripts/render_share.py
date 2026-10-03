#!/usr/bin/env python3
"""Render the daily share images: public/share-vertical.png + public/fb-share.png
+ public/art/<date>-portrait.png (1080x1920, drawn by scripts/render_portrait.py).

Reproducible port of the former manual bake (/workspace/regen_vertical_share.py
+ /workspace/letterbox_1080.py). Pixel logic is unchanged; what changed:

  * Input is public/joy.json (not /tmp/joy-live.json).
  * The stamp SVG is extracted at runtime from the inline <svg class="stamp-svg">
    in public/index.html (no /tmp/gh-stamp.svg dependency).
  * Fonts are pinned: Pillow loads TTFs from render/fonts/ by path, and cairosvg
    (stamp + postmark text) resolves fonts through a private fontconfig config
    that ONLY sees render/fonts/ — no system fonts, so CI and laptops match.
  * Output is 1080x1080 (card letterboxed on the table background). Both PNGs
    are written to temp files first and swapped in with os.replace() only after
    both rendered and passed sanity checks, so a failed render never leaves a
    half-written or mismatched image behind.
  * The dated portrait postcard public/art/<date>-portrait.png is rendered in
    the same pass and swapped in with the other two. If its text cannot fit
    (render_portrait.PortraitFitError) NOTHING is written — the same degrade
    path as an over-long share-vertical: previous images stay, the job fails.
    Only <date>'s portrait is ever written; other dates are never touched.

Usage:
  python scripts/render_share.py                    # writes public/share-vertical.png, public/fb-share.png, public/art/<date>-portrait.png
  python scripts/render_share.py --out-dir /tmp/x   # write elsewhere (dry run / CI artifact; portrait in /tmp/x/art/)
  python scripts/render_share.py --joy path/to/joy.json
  python scripts/render_share.py --fingerprint      # hash of render inputs (stdlib only)

Deps: render/requirements.txt (cairosvg, Pillow) + system libcairo2.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import random
import re
import sys
import tempfile
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FONT_DIR = REPO / "render" / "fonts"

# Rendering rules copied from the stock Debian/Ubuntu /etc/fonts/conf.d defaults
# (10-hinting-slight, 10-sub-pixel-none, 10-yes-antialias, 11-lcdfilter-default,
# 20-unhint-small-dejavu-sans-mono, 90-synthetic) so glyph hinting/advances match
# the original manual bake, which used the system fontconfig.
_FC_RULES = """
  <match target="pattern"><edit name="hintstyle" mode="append"><const>hintslight</const></edit></match>
  <match target="pattern"><edit name="rgba" mode="append"><const>none</const></edit></match>
  <match target="pattern"><edit name="antialias" mode="append"><bool>true</bool></edit></match>
  <match target="pattern"><edit name="lcdfilter" mode="append"><const>lcddefault</const></edit></match>
  <match target="font">
    <test name="family"><string>DejaVu Sans Mono</string></test>
    <test compare="less" name="pixelsize"><double>7.5</double></test>
    <edit name="hinting"><bool>false</bool></edit>
  </match>
  <match target="font">
    <test name="slant"><const>roman</const></test>
    <test target="pattern" name="slant" compare="not_eq"><const>roman</const></test>
    <edit name="matrix" mode="assign"><times><name>matrix</name>
      <matrix><double>1</double><double>0.2</double><double>0</double><double>1</double></matrix>
    </times></edit>
    <edit name="slant" mode="assign"><const>oblique</const></edit>
    <edit name="embeddedbitmap" mode="assign"><bool>false</bool></edit>
  </match>
  <match target="font">
    <test name="weight" compare="less_eq"><const>medium</const></test>
    <test target="pattern" name="weight" compare="more_eq"><const>bold</const></test>
    <edit name="embolden" mode="assign"><bool>true</bool></edit>
    <edit name="weight" mode="assign"><const>bold</const></edit>
  </match>
"""


def _pin_fontconfig() -> None:
    """Point fontconfig (used by cairo inside cairosvg) at render/fonts only.

    Must run before cairo initialises fontconfig, i.e. before cairosvg renders.
    """
    tmp = Path(tempfile.mkdtemp(prefix="gh-fc-"))
    conf = tmp / "fonts.conf"
    conf.write_text(
        '<?xml version="1.0"?>\n<!DOCTYPE fontconfig SYSTEM "fonts.dtd">\n<fontconfig>\n'
        f"  <dir>{FONT_DIR}</dir>\n  <cachedir>{tmp / 'cache'}</cachedir>\n"
        + _FC_RULES + "</fontconfig>\n",
        encoding="utf-8",
    )
    os.environ["FONTCONFIG_FILE"] = str(conf)
    os.environ.pop("FONTCONFIG_PATH", None)


Image = ImageDraw = ImageFilter = ImageFont = cairosvg = None  # loaded by _load_deps()


def _load_deps() -> None:
    """Import Pillow + cairosvg lazily (after pinning fontconfig) so that
    --fingerprint works in CI before the render deps are installed."""
    global Image, ImageDraw, ImageFilter, ImageFont, cairosvg
    if cairosvg is not None:
        return
    _pin_fontconfig()
    from PIL import Image as _I, ImageDraw as _D, ImageFilter as _F, ImageFont as _T
    import cairosvg as _c
    Image, ImageDraw, ImageFilter, ImageFont, cairosvg = _I, _D, _F, _T, _c


def fingerprint(joy: dict) -> str:
    """Hash of every joy.json field that affects the rendered image / FB text."""
    seed = joy.get("seed") or {}
    key = [joy.get("date"), joy.get("line"), joy.get("paragraph"),
           seed.get("sourceUrl"), seed.get("sourceTitle")]
    return hashlib.sha256(json.dumps(key, ensure_ascii=False).encode("utf-8")).hexdigest()

OUT_W = 1080
TABLE = (32, 48, 58)
PAPER = (239, 230, 210)
INK = (42, 58, 72)
INK_SOFT = (85, 112, 130)
OCEAN = (54, 85, 107)
POSTAL = (161, 58, 43)

SERIF = FONT_DIR / "LiberationSerif-Regular.ttf"
SERIF_B = FONT_DIR / "LiberationSerif-Bold.ttf"
SERIF_I = FONT_DIR / "LiberationSerif-Italic.ttf"
SERIF_BI = FONT_DIR / "LiberationSerif-BoldItalic.ttf"
MONO = FONT_DIR / "DejaVuSansMono.ttf"
CAVEAT = SERIF_I
REQUIRED_FONTS = [SERIF, SERIF_B, SERIF_I, SERIF_BI, MONO,
                  FONT_DIR / "Fraunces-VariableFont_SOFT,WONK,opsz,wght.ttf",
                  FONT_DIR / "Fraunces-Italic-VariableFont_SOFT,WONK,opsz,wght.ttf",
                  FONT_DIR / "IBMPlexMono-Regular.ttf", FONT_DIR / "IBMPlexMono-Medium.ttf"]

STAMP_RE = re.compile(r'<svg\s+class="stamp-svg"[^>]*>.*?</svg>', re.S)


def extract_stamp_svg(index_html: str) -> str:
    """Return the inline stamp <svg> from index.html as a standalone SVG document.

    Drops the page-only class / aria-hidden attributes; everything else is verbatim.
    """
    m = STAMP_RE.search(index_html)
    if not m:
        raise RuntimeError('public/index.html has no <svg class="stamp-svg"> element')
    svg = m.group(0)
    head_end = svg.index(">")
    head = svg[:head_end]
    head = re.sub(r'\s+class="stamp-svg"', "", head)
    head = re.sub(r'\s+aria-hidden="true"', "", head)
    return head + svg[head_end:]


def postmark_svg(pm_month: str, pm_day: str, pm_year: str) -> str:
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="280" height="280" viewBox="0 0 140 140">
  <defs><path id="pmCirc" d="M 70,70 m -52,0 a 52,52 0 1,1 104,0 a 52,52 0 1,1 -104,0"/></defs>
  <g fill="none" stroke="#a13a2b" stroke-width="1.4" opacity="0.85">
    <circle cx="70" cy="70" r="58"/><circle cx="70" cy="70" r="40"/>
  </g>
  <text fill="#a13a2b" opacity="0.85" font-family="DejaVu Sans Mono, monospace" font-size="7.5" letter-spacing="3">
    <textPath href="#pmCirc" startOffset="0">GETHAPPYINFO · DAILY JOY · GETHAPPYINFO · DAILY JOY ·</textPath>
  </text>
  <text x="70" y="65" text-anchor="middle" font-family="Liberation Serif, Georgia, serif" font-size="13" font-weight="600" fill="#a13a2b" opacity="0.92">{pm_month} {pm_day}</text>
  <text x="70" y="84" text-anchor="middle" font-family="DejaVu Sans Mono, monospace" font-size="9" fill="#a13a2b" opacity="0.85" letter-spacing="2">{pm_year}</text>
  <g stroke="#a13a2b" stroke-width="1.4" fill="none" opacity="0.7">
    <path d="M 18,108 Q 35,102 52,108 T 86,108 T 122,108"/>
    <path d="M 18,115 Q 35,109 52,115 T 86,115 T 122,115"/>
  </g>
</svg>'''


def F(path: Path, size: int):
    return ImageFont.truetype(str(path), size)


def _svg_png(svg: str, width: int) -> "Image.Image":
    data = cairosvg.svg2png(bytestring=svg.encode("utf-8"), output_width=width)
    return Image.open(io.BytesIO(data))


def render_card(joy: dict, stamp_svg: str) -> "Image.Image":
    """The tall 1080-wide card (formerly regen_vertical_share.py)."""
    line = joy["line"]
    paragraph = joy["paragraph"]
    seed = joy.get("seed") or {}
    has_seed = bool(seed.get("sourceUrl") and seed.get("sourceTitle"))
    date = joy["date"]
    d = datetime.strptime(date, "%Y-%m-%d")
    pm_month = d.strftime("%b").upper()
    pm_day = str(d.day)
    pm_year = str(d.year)
    edition = (d - datetime(2026, 5, 27)).days + 1

    stamp_img = _svg_png(stamp_svg, 160)
    pm_img = _svg_png(postmark_svg(pm_month, pm_day, pm_year), 200)

    side, top_m, bot_m = 44, 36, 40
    CARD_W = OUT_W - 2 * side
    SCRATCH_H = 1600
    card = Image.new("RGBA", (CARD_W, SCRATCH_H), PAPER + (255,))
    noise = Image.new("RGBA", (CARD_W, SCRATCH_H), (0, 0, 0, 0))
    nd = ImageDraw.Draw(noise)
    random.seed(7)
    for _ in range(1600):
        nd.point((random.randint(0, CARD_W - 1), random.randint(0, SCRATCH_H - 1)),
                 fill=(180, 150, 110, random.randint(4, 12)))
    card = Image.alpha_composite(card, noise)
    cd = ImageDraw.Draw(card)

    pad = 44

    # masthead
    cd.text((pad, 32), "POST · CARD", font=F(MONO, 13), fill=OCEAN)
    t1 = "Get Happy Info"
    f_title = F(SERIF_B, 42)
    cd.text((pad, 56), t1, font=f_title, fill=INK)
    tw = cd.textbbox((0, 0), t1, font=f_title)[2]
    cd.text((pad + tw - 2, 56), ".com", font=F(SERIF_BI, 42), fill=POSTAL)
    cd.text((pad, 110), "A daily postcard of good.", font=F(SERIF_I, 17), fill=INK_SOFT)

    stamp = stamp_img.convert("RGBA").resize((112, 132), Image.Resampling.LANCZOS)
    stamp = stamp.rotate(-2.5, expand=True, resample=Image.Resampling.BICUBIC)
    card.paste(stamp, (CARD_W - pad - stamp.width + 2, 24), stamp)
    pm = pm_img.convert("RGBA")
    pm.putalpha(pm.split()[-1].point(lambda v: int(v * 0.78)))
    pm = pm.rotate(-14, expand=True, resample=Image.Resampling.BICUBIC)
    card.paste(pm, (CARD_W - pad - pm.width + 24, 42), pm)

    cd = ImageDraw.Draw(card)
    rule_y = 156
    cd.line([(pad, rule_y), (CARD_W - pad, rule_y)], fill=OCEAN, width=2)
    cd.line([(pad, rule_y + 4), (CARD_W - pad, rule_y + 4)], fill=OCEAN + (90,), width=1)

    def wrap(text, font, max_w):
        words = text.split()
        lines, cur = [], ""
        for w in words:
            trial = (cur + " " + w).strip()
            if cd.textbbox((0, 0), trial, font=font)[2] <= max_w:
                cur = trial
            else:
                if cur:
                    lines.append(cur)
                cur = w
        if cur:
            lines.append(cur)
        return lines

    max_w = CARD_W - 2 * pad

    y = rule_y + 28
    cd.text((pad, y), "Dear friend,", font=F(SERIF_I, 22), fill=INK_SOFT)
    y += 38
    f_line = F(SERIF_BI, 28)
    for ln in wrap(line, f_line, max_w):
        cd.text((pad, y), ln, font=f_line, fill=INK)
        y += 38
    y += 22
    sig = F(CAVEAT, 26)
    cd.text((pad + 4, y), "Yours in joy,", font=sig, fill=POSTAL)
    cd.text((pad + 4, y + 30), "~ Get Happy", font=sig, fill=POSTAL)
    y += 88

    f_lab = F(MONO, 12)
    label = "A LITTLE MORE"
    bb = cd.textbbox((0, 0), label, font=f_lab)
    lw = bb[2] - bb[0]
    cx = CARD_W // 2
    x = pad
    while x < CARD_W - pad:
        x2 = min(x + 6, CARD_W - pad)
        if not (cx - lw // 2 - 14 < x < cx + lw // 2 + 14):
            cd.line([(x, y + 8), (x2, y + 8)], fill=OCEAN + (120,), width=1)
        x += 12
    cd.rectangle([cx - lw // 2 - 10, y, cx + lw // 2 + 10, y + 22], fill=PAPER)
    cd.text((cx - lw // 2, y), label, font=f_lab, fill=POSTAL)
    y += 40

    f_p = F(SERIF, 19)
    for ln in wrap(paragraph, f_p, max_w):
        cd.text((pad, y), ln, font=f_p, fill=INK)
        y += 27

    if has_seed:
        y += 22
        link = "Story source"
        f_link = F(MONO, 13)
        cd.text((pad, y), link, font=f_link, fill=POSTAL)
        bb = cd.textbbox((pad, y), link, font=f_link)
        cd.line([(bb[0], bb[3] + 1), (bb[2], bb[3] + 1)], fill=POSTAL, width=1)
        y = bb[3] + 8

    y += 28
    cd.line([(pad, y), (CARD_W - pad, y)], fill=OCEAN, width=2)
    cd.text((pad, y + 16), f"GETHAPPYINFO.COM · NO. {edition}", font=F(MONO, 12), fill=OCEAN)
    btn = "✉ pass it on ↗"
    f_btn = F(MONO, 12)
    bb = cd.textbbox((0, 0), btn, font=f_btn)
    bw, bh = bb[2] - bb[0] + 30, bb[3] - bb[1] + 16
    bx = CARD_W - pad - bw
    by = y + 10
    cd.rounded_rectangle([bx, by, bx + bw, by + bh], radius=3, fill=POSTAL)
    cd.text((bx + 14, by + 6), btn, font=f_btn, fill=PAPER)

    CARD_H = by + bh + 28
    card = card.crop((0, 0, CARD_W, CARD_H))
    cd = ImageDraw.Draw(card)
    tick = 12
    cd.line([(10, 10), (10 + tick, 10)], fill=POSTAL + (130,), width=1)
    cd.line([(10, 10), (10, 10 + tick)], fill=POSTAL + (130,), width=1)
    cd.line([(CARD_W - 10, CARD_H - 10), (CARD_W - 10 - tick, CARD_H - 10)], fill=POSTAL + (130,), width=1)
    cd.line([(CARD_W - 10, CARD_H - 10), (CARD_W - 10, CARD_H - 10 - tick)], fill=POSTAL + (130,), width=1)

    OUT_H = CARD_H + top_m + bot_m
    print(f"[render_share] content card {CARD_W}x{CARD_H}, canvas {OUT_W}x{OUT_H}")

    bg = Image.new("RGB", (OUT_W, OUT_H), TABLE)
    grad = Image.new("RGBA", (OUT_W, OUT_H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(grad)
    for i in range(36):
        a = int(16 * (1 - i / 36))
        gd.ellipse([-60 + i * 5, -40 + i * 3, OUT_W + 60 - i * 5, OUT_H + 30 - i * 3], fill=(58, 85, 100, a))
    canvas = Image.alpha_composite(bg.convert("RGBA"), grad)
    cx, cy = side, top_m
    shadow = Image.new("RGBA", (OUT_W, OUT_H), (0, 0, 0, 0))
    shadow.paste(Image.new("RGBA", (CARD_W, CARD_H), (0, 0, 0, 100)), (cx + 5, cy + 8))
    shadow = shadow.filter(ImageFilter.GaussianBlur(16))
    canvas = Image.alpha_composite(canvas, shadow)
    canvas.paste(card, (cx, cy), card)
    return canvas.convert("RGB")


def letterbox(src_rgb: "Image.Image", out: int = 1080) -> "Image.Image":
    """Center the card on a square table background (formerly letterbox_1080.py)."""
    src = src_rgb.convert("RGBA")
    canvas = Image.new("RGB", (out, out), TABLE)
    grad = Image.new("RGBA", (out, out), (0, 0, 0, 0))
    gd = ImageDraw.Draw(grad)
    for i in range(36):
        a = int(16 * (1 - i / 36))
        gd.ellipse([-60 + i * 5, -40 + i * 3, out + 60 - i * 5, out + 30 - i * 3], fill=(58, 85, 100, a))
    base = Image.alpha_composite(canvas.convert("RGBA"), grad)
    if src.width != out:
        scale = out / src.width
        src = src.resize((out, int(src.height * scale)), Image.Resampling.LANCZOS)
    if src.height > out:
        raise RuntimeError(f"card is taller than {out}px ({src.height}px); text too long to letterbox")
    y = (out - src.height) // 2
    base.paste(src, (0, y), src)
    return base.convert("RGB")


def render(joy_path: Path, index_path: Path, out_dir: Path) -> list[Path]:
    _load_deps()
    missing = [str(p) for p in REQUIRED_FONTS if not p.is_file()]
    if missing:
        raise RuntimeError(f"missing pinned fonts: {missing}")
    joy = json.loads(joy_path.read_text(encoding="utf-8"))
    for k in ("date", "line", "paragraph"):
        if not str(joy.get(k) or "").strip():
            raise RuntimeError(f"joy.json has no {k!r}")
    stamp_svg = extract_stamp_svg(index_path.read_text(encoding="utf-8"))

    img = letterbox(render_card(joy, stamp_svg))
    if img.size != (1080, 1080):
        raise RuntimeError(f"unexpected output size {img.size}")
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    data = buf.getvalue()
    if len(data) < 20_000:
        raise RuntimeError(f"rendered PNG suspiciously small ({len(data)} bytes)")

    # Portrait (raises PortraitFitError / RuntimeError before anything is written).
    import render_portrait
    portrait, (line_px, para_px) = render_portrait.render_portrait_png(joy, stamp_svg)

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "art").mkdir(exist_ok=True)
    portrait_path = out_dir / "art" / f"{joy['date']}-portrait.png"
    targets = [(out_dir / "share-vertical.png", data), (out_dir / "fb-share.png", data),
               (portrait_path, portrait)]
    tmps = []
    for t, payload in targets:
        tmp = t.with_name(f".{t.name}.tmp")
        tmp.write_bytes(payload)
        tmps.append(tmp)
    for tmp, (t, _) in zip(tmps, targets):
        os.replace(tmp, t)
    print(f"[render_share] {joy['date']}: wrote {targets[0][0]}, {targets[1][0]} "
          f"(1080x1080, {len(data)} bytes)")
    print(f"[render_share] {joy['date']}: wrote {portrait_path} (1080x1920, {len(portrait)} bytes; "
          f"line {line_px}px, paragraph {para_px}px)")
    return [t for t, _ in targets]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--joy", type=Path, default=REPO / "public" / "joy.json")
    ap.add_argument("--index", type=Path, default=REPO / "public" / "index.html")
    ap.add_argument("--out-dir", type=Path, default=REPO / "public")
    ap.add_argument("--fingerprint", action="store_true",
                    help="print a hash of the joy.json fields the render uses; render nothing")
    args = ap.parse_args(argv)
    if args.fingerprint:
        print(fingerprint(json.loads(args.joy.read_text(encoding="utf-8"))))
        return 0
    try:
        render(args.joy, args.index, args.out_dir)
    except Exception as e:  # surface in the Actions job summary, then fail
        msg = f"Share render failed: {type(e).__name__}: {e}"
        print(f"::error::{msg}", file=sys.stderr)
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            with open(summary, "a", encoding="utf-8") as fh:
                fh.write(f"> **⚠️ {msg}**\n\n")
        raise
    return 0


if __name__ == "__main__":
    sys.exit(main())
