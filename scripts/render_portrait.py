#!/usr/bin/env python3
"""Render the daily 1080x1920 portrait postcard: public/art/<date>-portrait.png.

Same brand look as the site's postcard at phone width (the single-column
layout from public/index.html's @media (max-width: 620px) rules): paper card
on the dark "table" background, POST · CARD kicker, Get Happy Info.com
wordmark, the index.html stamp with that day's postmark, "a note to you" /
Dear friend / the line / Yours in joy ~ Get Happy, then "a little more" + the
story paragraph and the footer.

Fonts are the site's own faces, pinned in render/fonts: Fraunces (variable;
opsz/wght set per element like page CSS) and IBM Plex Mono. The stamp and
postmark come from scripts/render_share.py (stamp SVG extracted from
public/index.html at runtime, rendered through the pinned fontconfig).

Reel/Story safe zones (1080x1920): nothing but background within 250px of the
top, 350px of the bottom or 120px of the right edge; the card sits in
x 64..960, y 250..1570. (No "Story source" link: it is not clickable in a PNG.)

Auto-fit: the story paragraph starts at PARA_SIZES[0] and shrinks one step at a
time (re-wrapping each time) down to PARA_MIN. The line stays large (it only
steps down LINE_SIZES, which never goes below 46px). If the text still does not
fit at the minimum, PortraitFitError is raised and nothing is written —
render_share.py then fails the whole share render (previous images kept).

This module draws; render_share.render() calls render_portrait() and writes
the file atomically together with share-vertical.png / fb-share.png.

Standalone (no repo writes unless --out is inside public/):
  python scripts/render_portrait.py --joy public/joy.json --out /tmp/p.png
"""
from __future__ import annotations

import argparse
import io
import json
import random
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import render_share as rs  # noqa: E402

W, H = 1080, 1920
SAFE_TOP, SAFE_BOTTOM, SAFE_RIGHT, SAFE_LEFT = 250, 350, 120, 64
CARD_X0, CARD_X1 = SAFE_LEFT, W - SAFE_RIGHT          # 64 .. 960
CARD_Y0, CARD_Y1 = SAFE_TOP, H - SAFE_BOTTOM          # 250 .. 1570
PAD_X, PAD_TOP, PAD_BOTTOM = 46, 40, 34

PARA_SIZES = (36, 34, 32, 30, 28)   # px at 1080 wide; last = minimum readable
PARA_MIN = PARA_SIZES[-1]
LINE_SIZES = (56, 52, 48, 46)       # the line stays large

FRAUNCES = rs.FONT_DIR / "Fraunces-VariableFont_SOFT,WONK,opsz,wght.ttf"
FRAUNCES_I = rs.FONT_DIR / "Fraunces-Italic-VariableFont_SOFT,WONK,opsz,wght.ttf"
PLEX = rs.FONT_DIR / "IBMPlexMono-Regular.ttf"
PLEX_M = rs.FONT_DIR / "IBMPlexMono-Medium.ttf"

PAPER, INK, INK_SOFT, OCEAN, POSTAL, TABLE = rs.PAPER, rs.INK, rs.INK_SOFT, rs.OCEAN, rs.POSTAL, rs.TABLE
KRAFT = (196, 168, 132)


class PortraitFitError(RuntimeError):
    """The text cannot fit the portrait card at the minimum paragraph size."""


_font_cache: dict = {}


def fraunces(size: int, wght: int = 400, opsz: int | None = None, italic: bool = False):
    key = ("F", size, wght, opsz, italic)
    if key not in _font_cache:
        f = rs.ImageFont.truetype(str(FRAUNCES_I if italic else FRAUNCES), size,
                                  layout_engine=rs.ImageFont.Layout.BASIC)
        # axes: opsz, wght, SOFT, WONK (Google Fonts serves SOFT=0, WONK=0)
        f.set_variation_by_axes([max(9, min(144, opsz or size)), wght, 0, 0])
        _font_cache[key] = f
    return _font_cache[key]


def plex(size: int, medium: bool = False):
    key = ("P", size, medium)
    if key not in _font_cache:
        _font_cache[key] = rs.ImageFont.truetype(str(PLEX_M if medium else PLEX), size,
                                                 layout_engine=rs.ImageFont.Layout.BASIC)
    return _font_cache[key]


def _tw(draw, text, font, spacing=0.0):
    w = draw.textlength(text, font=font)
    return w + spacing * max(0, len(text) - 1)


def _text_spaced(draw, xy, text, font, fill, spacing):
    """Letter-spaced text (CSS letter-spacing); returns width."""
    x, y = xy
    for ch in text:
        draw.text((x, y), ch, font=font, fill=fill)
        x += draw.textlength(ch, font=font) + spacing
    return x - xy[0] - spacing


def _rotate(img, deg: float):
    """Rotate RGBA in premultiplied space so antialiased edges don't pick up
    the black of fully transparent pixels (keeps thin postmark strokes red)."""
    return img.convert("RGBa").rotate(deg, expand=True, resample=rs.Image.Resampling.BICUBIC).convert("RGBA")


def wrap(draw, text: str, font, max_w: float) -> list[str]:
    words = text.split()
    lines, cur = [], ""
    for w in words:
        trial = (cur + " " + w).strip()
        if draw.textlength(trial, font=font) <= max_w:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    for ln in lines:  # a single unbreakable word wider than the card = overflow
        if draw.textlength(ln, font=font) > max_w:
            raise PortraitFitError(f"word too wide for the card: {ln[:40]!r}")
    return lines


# ---- layout -----------------------------------------------------------------

CARD_W = CARD_X1 - CARD_X0
CARD_H = CARD_Y1 - CARD_Y0
TEXT_W = CARD_W - 2 * PAD_X
HEADER_H = 206          # kicker + title + subtitle + stamp, down to the rule
FOOTER_H = 88           # rule + edition + pass-it-on button
LABEL_H = 30            # section label row
GAP_SECTION = 24
LABEL_GAP = 18          # label row -> first text
GREETING_H = 46
LINE_GAP = 22
SIG_H = 92


def _plan(draw, joy: dict, line_size: int, para_size: int) -> dict:
    """Measure the body at the given sizes; returns the plan incl. total height."""
    f_line = fraunces(line_size, 500, 72, italic=True)
    f_para = fraunces(para_size, 400, 18)
    line_lh = round(line_size * 1.3)
    para_lh = round(para_size * 1.55)
    line_lines = wrap(draw, joy["line"], f_line, TEXT_W)
    para_lines = wrap(draw, joy["paragraph"], f_para, TEXT_W)
    h = 0
    h += LABEL_H + LABEL_GAP                # "a note to you"
    h += GREETING_H                         # Dear friend,
    h += len(line_lines) * line_lh + LINE_GAP
    h += SIG_H                              # signature (2 lines, rotated)
    h += GAP_SECTION + 2 + GAP_SECTION      # dashed divider
    h += LABEL_H + LABEL_GAP                # "a little more"
    h += len(para_lines) * para_lh
    return dict(f_line=f_line, f_para=f_para, line_lh=line_lh, para_lh=para_lh,
                line_lines=line_lines, para_lines=para_lines, height=h,
                line_size=line_size, para_size=para_size)


BODY_AVAIL = CARD_H - PAD_TOP - HEADER_H - 30 - FOOTER_H - PAD_BOTTOM - 20


def fit(joy: dict) -> dict:
    """Pick the largest sizes that fit: the paragraph shrinks first (all steps),
    then the line steps down; raises PortraitFitError if nothing fits."""
    rs._load_deps()
    probe = rs.ImageDraw.Draw(rs.Image.new("RGB", (8, 8)))
    last = None
    for ls in LINE_SIZES:
        for ps in PARA_SIZES:
            plan = _plan(probe, joy, ls, ps)
            last = plan
            if plan["height"] <= BODY_AVAIL:
                return plan
    raise PortraitFitError(
        f"{joy.get('date')}: text does not fit the portrait card even at line {LINE_SIZES[-1]}px / "
        f"paragraph {PARA_MIN}px ({len(last['para_lines'])} paragraph lines, body "
        f"{last['height']}px > {BODY_AVAIL}px available; paragraph {len(joy['paragraph'])} chars)")


def _section_label(cd, y, text, x0, x1):
    f = plex(21, medium=False)
    sp = 21 * 0.28
    tw = _tw(cd, text, f, sp)
    cx = (x0 + x1) / 2
    gap = 14
    ly = y + 15
    cd.line([(x0, ly), (cx - tw / 2 - gap, ly)], fill=POSTAL + (102,), width=2)
    cd.line([(cx + tw / 2 + gap, ly), (x1, ly)], fill=POSTAL + (102,), width=2)
    _text_spaced(cd, (cx - tw / 2, y), text, f, POSTAL, sp)


def render_portrait(joy: dict, stamp_svg: str) -> "rs.Image.Image":
    """Return the 1080x1920 RGB portrait; raises PortraitFitError on overflow."""
    plan = fit(joy)
    Image, ImageDraw, ImageFilter = rs.Image, rs.ImageDraw, rs.ImageFilter
    d = datetime.strptime(joy["date"], "%Y-%m-%d")
    edition = (d - datetime(2026, 5, 27)).days + 1

    # -- background: table + soft radial glow (body background) --------------
    bg = Image.new("RGBA", (W, H), TABLE + (255,))
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    for i in range(48):
        a = int(18 * (1 - i / 48))
        gd.ellipse([-260 + i * 9, -120 + i * 9, W + 260 - i * 9, int(H * 0.62) - i * 6],
                   fill=(58, 85, 100, a))
    bg = Image.alpha_composite(bg, glow.filter(ImageFilter.GaussianBlur(30)))

    # -- paper card -----------------------------------------------------------
    card = Image.new("RGBA", (CARD_W, CARD_H), PAPER + (255,))
    tint = Image.new("RGBA", (CARD_W, CARD_H), (0, 0, 0, 0))
    td = ImageDraw.Draw(tint)
    td.ellipse([-CARD_W * 0.35, -CARD_H * 0.2, CARD_W * 0.7, CARD_H * 0.35], fill=KRAFT + (60,))
    td.ellipse([CARD_W * 0.45, CARD_H * 0.7, CARD_W * 1.3, CARD_H * 1.2], fill=POSTAL + (14,))
    card = Image.alpha_composite(card, tint.filter(ImageFilter.GaussianBlur(120)))
    noise = Image.new("RGBA", (CARD_W, CARD_H), (0, 0, 0, 0))
    nd = ImageDraw.Draw(noise)
    random.seed(7)
    for _ in range(9000):
        nd.point((random.randint(0, CARD_W - 1), random.randint(0, CARD_H - 1)),
                 fill=(180, 150, 110, random.randint(4, 14)))
    card = Image.alpha_composite(card, noise)
    cd = ImageDraw.Draw(card, "RGBA")  # blend alpha fills, never punch holes
    x0, x1 = PAD_X, CARD_W - PAD_X

    # corner ticks (.postcard::before/::after)
    t, o, tick_c = 22, 16, POSTAL + (140,)
    cd.line([(o, o), (o + t, o)], fill=tick_c, width=2)
    cd.line([(o, o), (o, o + t)], fill=tick_c, width=2)
    cd.line([(CARD_W - o, CARD_H - o), (CARD_W - o - t, CARD_H - o)], fill=tick_c, width=2)
    cd.line([(CARD_W - o, CARD_H - o), (CARD_W - o, CARD_H - o - t)], fill=tick_c, width=2)

    # -- header ---------------------------------------------------------------
    y = PAD_TOP
    _text_spaced(cd, (x0, y + 6), "POST · CARD", plex(20, medium=True), OCEAN, 20 * 0.32)
    f_title = fraunces(62, 600, 144)
    f_title_i = fraunces(62, 500, 144, italic=True)
    ty = y + 40
    cd.text((x0, ty), "Get Happy Info", font=f_title, fill=INK)
    tw = cd.textlength("Get Happy Info", font=f_title)
    cd.text((x0 + tw, ty), ".com", font=f_title_i, fill=POSTAL)
    title_right = x0 + tw + cd.textlength(".com", font=f_title_i)
    cd.text((x0, ty + 80), "A daily postcard of good.", font=fraunces(28, 400, 14, italic=True), fill=INK_SOFT)

    # stamp (rotate 2.5deg like .philately) + postmark (rotate -14deg, 78% opacity)
    st_w, st_h = 150, 177
    stamp = rs._svg_png(stamp_svg, st_w).convert("RGBA")  # native size: no RGBA resampling halos
    st_h = stamp.height
    shadow = Image.new("RGBA", (st_w + 20, st_h + 20), (0, 0, 0, 0))
    shadow.paste(Image.new("RGBA", (st_w - 12, st_h - 12), (0, 0, 0, 60)), (16, 18))
    shadow = shadow.filter(ImageFilter.GaussianBlur(4))
    stamp_layer = Image.new("RGBA", (st_w + 20, st_h + 20), (0, 0, 0, 0))
    stamp_layer = Image.alpha_composite(stamp_layer, shadow)
    stamp_layer.alpha_composite(stamp, dest=(10, 10))
    stamp_layer = _rotate(stamp_layer, -2.5)
    sx = x1 - stamp_layer.width + 18
    sy = y - 16
    if title_right > sx - 8:
        raise PortraitFitError("wordmark collides with the stamp")
    card.alpha_composite(stamp_layer, dest=(int(sx), int(sy)))
    pm_svg = rs.postmark_svg(d.strftime("%b").upper(), str(d.day), str(d.year))
    pm_size = 164
    pm = rs._svg_png(pm_svg, pm_size).convert("RGBA")
    pm.putalpha(pm.split()[-1].point(lambda v: int(v * 0.9)))
    pm = _rotate(pm, -14)
    # .postmark: top 42px, right -34px of the 112px stamp box (scaled), kept inside the card
    # centre over the stamp's lower-right like the site (.postmark top 42 / right -34
    # on the 112x132 stamp), nudged up so the ring clears the "a note to you" row
    pcx, pcy = sx + 10 + 0.74 * st_w, sy + 10 + 0.84 * st_h
    pmx = min(int(pcx - pm.width / 2), CARD_W - pm.width)
    pmy = int(pcy - pm.height / 2)
    card.alpha_composite(pm, dest=(int(pmx), int(pmy)))
    cd = ImageDraw.Draw(card, "RGBA")  # blend alpha fills, never punch holes

    rule_y = PAD_TOP + HEADER_H
    cd.line([(x0, rule_y), (x1, rule_y)], fill=OCEAN, width=3)
    cd.line([(x0, rule_y + 8), (x1, rule_y + 8)], fill=OCEAN + (90,), width=1)

    # -- body: vertically centred between header rule and footer -------------
    body_top = rule_y + 30
    footer_y = CARD_H - PAD_BOTTOM - FOOTER_H
    slack = (footer_y - 20) - body_top - plan["height"]
    if slack < 0:
        raise PortraitFitError("internal: plan does not fit")
    y = body_top + min(slack // 2, 60)

    _section_label(cd, y, "A NOTE TO YOU", x0, x1)
    y += LABEL_H + LABEL_GAP
    cd.text((x0, y), "Dear friend,", font=fraunces(32, 400, 24, italic=True), fill=INK_SOFT)
    y += GREETING_H
    for ln in plan["line_lines"]:
        cd.text((x0, y), ln, font=plan["f_line"], fill=INK)
        y += plan["line_lh"]
    y += LINE_GAP
    # signature, rotated -2.5deg (.signature)
    sig = Image.new("RGBA", (520, 100), (0, 0, 0, 0))
    sd = ImageDraw.Draw(sig)
    sd.text((6, 2), "Yours in joy,", font=fraunces(34, 400, 36, italic=True), fill=POSTAL)
    sd.text((6, 46), "~ Get Happy", font=fraunces(36, 600, 36, italic=True), fill=POSTAL)
    sig = _rotate(sig, 2.5)
    card.alpha_composite(sig, dest=(int(x0 + 2), int(y - 8)))
    cd = ImageDraw.Draw(card, "RGBA")  # blend alpha fills, never punch holes
    y += SIG_H

    # dashed divider (mobile: 60% wide, horizontal)
    y += GAP_SECTION
    dx, dx1 = x0, x0 + int((x1 - x0) * 0.6)
    while dx < dx1:
        cd.line([(dx, y), (min(dx + 8, dx1), y)], fill=OCEAN + (115,), width=2)
        dx += 16
    y += 2 + GAP_SECTION

    _section_label(cd, y, "A LITTLE MORE", x0, x1)
    y += LABEL_H + LABEL_GAP
    for ln in plan["para_lines"]:
        cd.text((x0, y), ln, font=plan["f_para"], fill=INK)
        y += plan["para_lh"]
    if y > footer_y - 20:
        raise PortraitFitError(f"internal: body overflowed into footer ({y} > {footer_y - 20})")

    # -- footer ---------------------------------------------------------------
    cd.line([(x0, footer_y), (x1, footer_y)], fill=OCEAN, width=3)
    f_ft = plex(19)
    _text_spaced(cd, (x0, footer_y + 36), f"GETHAPPYINFO.COM · NO. {edition}", f_ft, OCEAN, 19 * 0.22)
    btn = "✉ PASS IT ON ↗"
    f_btn = rs.F(rs.MONO, 19)  # DejaVu has ✉/↗; Plex Mono does not
    bw = int(_tw(cd, btn, f_btn, 19 * 0.18)) + 40
    bh = 50
    bx, by = x1 - bw, footer_y + 22
    cd.rounded_rectangle([bx, by, bx + bw, by + bh], radius=3, fill=POSTAL)
    _text_spaced(cd, (bx + 20, by + 13), btn, f_btn, PAPER, 19 * 0.18)

    # -- composite: card shadow, slight rest rotation like the site ----------
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sh = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sh.paste(Image.new("RGBA", (CARD_W - 20, CARD_H - 10), (0, 0, 0, 150)), (CARD_X0 + 10, CARD_Y0 + 22))
    sh = sh.filter(ImageFilter.GaussianBlur(26))
    layer.paste(card, (CARD_X0, CARD_Y0), card)
    out = Image.alpha_composite(bg, sh)
    out = Image.alpha_composite(out, layer)
    out = out.convert("RGB")
    out.info["portrait_fit"] = (plan["line_size"], plan["para_size"])
    return out


def render_portrait_png(joy: dict, stamp_svg: str) -> tuple[bytes, tuple[int, int]]:
    img = render_portrait(joy, stamp_svg)
    if img.size != (W, H):
        raise RuntimeError(f"unexpected portrait size {img.size}")
    sizes = img.info["portrait_fit"]
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    data = buf.getvalue()
    if len(data) < 50_000:
        raise RuntimeError(f"rendered portrait PNG suspiciously small ({len(data)} bytes)")
    return data, sizes


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--joy", type=Path, default=rs.REPO / "public" / "joy.json")
    ap.add_argument("--index", type=Path, default=rs.REPO / "public" / "index.html")
    ap.add_argument("--out", type=Path, required=True, help="output PNG path")
    args = ap.parse_args(argv)
    rs._load_deps()
    joy = json.loads(args.joy.read_text(encoding="utf-8"))
    data, (ls, ps) = render_portrait_png(joy, rs.extract_stamp_svg(args.index.read_text(encoding="utf-8")))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(data)
    print(f"[render_portrait] {joy['date']}: wrote {args.out} (line {ls}px, paragraph {ps}px)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
