#!/usr/bin/env python3
"""Render the daily 1200x630 landscape postcard: public/art/<date>-landscape.png.

Same brand look as the site's postcard at DESKTOP width (public/index.html
without the @media (max-width: 620px) overrides): paper card on the dark
"table" background, a header row (POST · CARD kicker, Get Happy Info.com
wordmark, subtitle | the stamp with that day's postmark), the double ocean
rule, then the two-column .letter grid (1fr | 1.5px dashed divider | 1.2fr):

  left  "a note to you" / Dear friend, / the line / Yours in joy, ~ Get Happy
  right "a little more" / the story paragraph / Source: <title> · <domain>

and the footer: GETHAPPYINFO.COM · NO. NNN | ✉ PASS IT ON ↗ badge.

Reuses render_portrait.py / render_share.py: pinned fonts in render/fonts
(Fraunces with per-element opsz/wght, IBM Plex Mono, DejaVu for ✉/↗), the site
palette, the stamp SVG extracted from index.html at runtime, the postmark SVG,
and the portrait's brand-red handling (rotations in premultiplied alpha, alpha
fills blended onto the card, stamp/postmark rendered at native size) so thin
postal-red strokes stay red instead of picking up dark fringes.

Source line: plain text (a PNG link is not clickable) built ONLY from
joy.seed.sourceTitle and/or the host of joy.seed.sourceUrl (http/https, "www."
dropped): "Source: <title> · <domain>". Omitted when neither exists; never
invented. It wraps (never truncated) and counts toward the fit.

Auto-fit (nothing is ever cut off or ellipsised):
  * right column: the story paragraph steps down PARA_SIZES (re-wrapping each
    time) to PARA_MIN;
  * left column: the line then steps down LINE_SIZES to LINE_MIN only if the
    note column itself overflows (the line never drops below LINE_MIN, which is
    larger than any paragraph size).
  If either column still does not fit, LandscapeFitError is raised and NOTHING
  is written. The nightly workflow treats that as the landscape failure path:
  no landscape file, no Landscape: line in /fb/today.txt, a job-summary
  warning, everything else (share-vertical, fb-share, portrait, reel, today.txt)
  unaffected. This script is a separate workflow step for exactly that reason;
  scripts/render_share.py never imports it.

Safe margins: every text/stamp element lies inside the card's inner box
(SAFE_X0..SAFE_X1, SAFE_Y0..SAFE_Y1, i.e. >= 70px from the left/right canvas
edges and >= 36px from top/bottom), so 1.91:1 link-preview crops and rounded
corners never clip anything.

Usage:
  python scripts/render_landscape.py                    # writes public/art/<date>-landscape.png
  python scripts/render_landscape.py --out-dir /tmp/x   # writes /tmp/x/art/<date>-landscape.png
  python scripts/render_landscape.py --out /tmp/l.png   # explicit output path
  python scripts/render_landscape.py --joy path/to/joy.json
Exit 0 on success; on any failure prints a ::warning:: (and a GITHUB_STEP_SUMMARY
note when set), writes nothing, exits 1.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import random
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import render_share as rs  # noqa: E402
import render_portrait as rp  # noqa: E402

W, H = 1200, 630
CARD_X0, CARD_Y0 = 34, 22
CARD_X1, CARD_Y1 = W - 34, H - 22                     # card 1132 x 586
CARD_W, CARD_H = CARD_X1 - CARD_X0, CARD_Y1 - CARD_Y0
PAD_X, PAD_TOP, PAD_BOTTOM = 40, 18, 14
# inner (safe) box, canvas coordinates: all text, stamp and postmark stay inside
SAFE_X0, SAFE_X1 = CARD_X0 + PAD_X - 4, CARD_X1 - PAD_X + 4 + 8   # postmark may poke 8px into the pad (site: right -34px)
SAFE_Y0, SAFE_Y1 = CARD_Y0 + 8, CARD_Y1 - PAD_BOTTOM

PARA_SIZES = (21, 20, 19, 18, 17, 16)    # story paragraph; last = legible minimum at 1200 wide
PARA_MIN = PARA_SIZES[-1]
LINE_SIZES = (34, 32, 30, 28, 26)        # the line; never below LINE_MIN
LINE_MIN = LINE_SIZES[-1]
SRC_SIZE = 13

PAPER, INK, INK_SOFT, OCEAN, POSTAL, TABLE = rp.PAPER, rp.INK, rp.INK_SOFT, rp.OCEAN, rp.POSTAL, rp.TABLE
KRAFT = rp.KRAFT
fraunces, plex = rp.fraunces, rp.plex

# ---- layout (card coordinates) -----------------------------------------------
X0, X1 = PAD_X, CARD_W - PAD_X                       # text box 40 .. 1092 (1052 wide)
TEXT_W = X1 - X0
COL_GAP, DIV_W = 28, 2                               # .letter gap 1.85rem, 1.5px divider
LEFT_W = int((TEXT_W - 2 * COL_GAP - DIV_W) / 2.2)   # 1fr
RIGHT_W = TEXT_W - 2 * COL_GAP - DIV_W - LEFT_W      # 1.2fr
LEFT_X = X0
DIV_X = LEFT_X + LEFT_W + COL_GAP
RIGHT_X = DIV_X + DIV_W + COL_GAP

HEADER_H = 116          # kicker + title + subtitle (stamp beside), down to the rule
RULE_TO_BODY = 24       # header padding/margin below the double rule
FOOTER_H = 50           # rule + edition + button
BODY_TO_FOOTER = 16
LABEL_H, LABEL_GAP = 18, 12
GREETING_H = 30
LINE_GAP = 12
SIG_H = 58
SRC_GAP, SRC_LH = 10, 18

RULE_Y = PAD_TOP + HEADER_H
BODY_TOP = RULE_Y + RULE_TO_BODY
FOOTER_Y = CARD_H - PAD_BOTTOM - FOOTER_H
BODY_AVAIL = FOOTER_Y - BODY_TO_FOOTER - BODY_TOP


class LandscapeFitError(RuntimeError):
    """The text cannot fit the landscape card at the minimum sizes."""


def edition_no(date: str) -> int:
    return (datetime.strptime(date, "%Y-%m-%d") - datetime(2026, 5, 27)).days + 1


def source_text(joy: dict) -> str | None:
    """'Source: <title> · <domain>' from joy.seed only; None when absent."""
    seed = joy.get("seed") or {}
    title = " ".join(str(seed.get("sourceTitle") or "").split())
    domain = ""
    url = str(seed.get("sourceUrl") or "").strip()
    if url:
        try:
            p = urlparse(url)
            if p.scheme in ("http", "https") and p.hostname:
                domain = p.hostname.lower()
                if domain.startswith("www."):
                    domain = domain[4:]
        except ValueError:
            domain = ""
    parts = [x for x in (title, domain) if x]
    return "Source: " + " · ".join(parts) if parts else None


def _wrap(draw, text, font, max_w):
    try:
        return rp.wrap(draw, text, font, max_w)
    except rp.PortraitFitError as e:
        raise LandscapeFitError(str(e)) from None


def _left_plan(draw, joy, ls):
    f_line = fraunces(ls, 500, 72, italic=True)
    lh = round(ls * 1.3)
    lines = _wrap(draw, joy["line"], f_line, LEFT_W)
    h = LABEL_H + LABEL_GAP + GREETING_H + len(lines) * lh + LINE_GAP + SIG_H
    return dict(line_size=ls, f_line=f_line, line_lh=lh, line_lines=lines, left_h=h)


def _right_plan(draw, joy, ps, src_lines):
    f_para = fraunces(ps, 400, 18)
    lh = round(ps * 1.55)
    lines = _wrap(draw, joy["paragraph"], f_para, RIGHT_W)
    h = LABEL_H + LABEL_GAP + len(lines) * lh
    if src_lines:
        h += SRC_GAP + len(src_lines) * SRC_LH
    return dict(para_size=ps, f_para=f_para, para_lh=lh, para_lines=lines, right_h=h)


def fit(joy: dict) -> dict:
    """Largest sizes that fit: paragraph steps down first (right column), the
    line only when the note column overflows. Raises LandscapeFitError."""
    rs._load_deps()
    for k in ("date", "line", "paragraph"):
        if not str(joy.get(k) or "").strip():
            raise LandscapeFitError(f"joy has no {k!r}")
    probe = rs.ImageDraw.Draw(rs.Image.new("RGB", (8, 8)))
    src = source_text(joy)
    f_src = plex(SRC_SIZE)
    src_lines = _wrap(probe, src, f_src, RIGHT_W) if src else []
    right = None
    for ps in PARA_SIZES:
        right = _right_plan(probe, joy, ps, src_lines)
        if right["right_h"] <= BODY_AVAIL:
            break
    else:
        raise LandscapeFitError(
            f"{joy.get('date')}: story does not fit the landscape card even at paragraph {PARA_MIN}px "
            f"({len(right['para_lines'])} lines, column {right['right_h']}px > {BODY_AVAIL}px; "
            f"paragraph {len(joy['paragraph'])} chars)")
    left = None
    for ls in LINE_SIZES:
        left = _left_plan(probe, joy, ls)
        if left["left_h"] <= BODY_AVAIL:
            break
    else:
        raise LandscapeFitError(
            f"{joy.get('date')}: the line does not fit the landscape card even at {LINE_MIN}px "
            f"({len(left['line_lines'])} lines, column {left['left_h']}px > {BODY_AVAIL}px)")
    plan = dict(left, **right, src=src, src_lines=src_lines, f_src=f_src)
    plan["height"] = max(plan["left_h"], plan["right_h"])
    return plan


def _section_label(cd, y, text, x0, x1, boxes):
    f = plex(13)
    sp = 13 * 0.28
    tw = rp._tw(cd, text, f, sp)
    gap = 10
    ly = y + 9
    # .section-label: text left, rules on both sides (flex 1 each); ::before is
    # flex:1 too, so the text sits centred between two equal rules
    cx = (x0 + x1) / 2
    cd.line([(x0, ly), (cx - tw / 2 - gap, ly)], fill=POSTAL + (102,), width=1)
    cd.line([(cx + tw / 2 + gap, ly), (x1, ly)], fill=POSTAL + (102,), width=1)
    rp._text_spaced(cd, (cx - tw / 2, y), text, f, POSTAL, sp)
    boxes.append(("label", x0, y, x1, y + LABEL_H))


def render_landscape(joy: dict, stamp_svg: str) -> "rs.Image.Image":
    """Return the 1200x630 RGB landscape; raises LandscapeFitError on overflow."""
    plan = fit(joy)
    Image, ImageDraw, ImageFilter = rs.Image, rs.ImageDraw, rs.ImageFilter
    d = datetime.strptime(joy["date"], "%Y-%m-%d")
    boxes: list[tuple] = []          # (what, x0, y0, x1, y1) in card coordinates

    # -- background: table + soft radial glow ---------------------------------
    bg = Image.new("RGBA", (W, H), TABLE + (255,))
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    for i in range(40):
        a = int(18 * (1 - i / 40))
        gd.ellipse([-200 + i * 10, -160 + i * 5, W + 200 - i * 10, int(H * 0.75) - i * 4],
                   fill=(58, 85, 100, a))
    bg = Image.alpha_composite(bg, glow.filter(ImageFilter.GaussianBlur(30)))

    # -- paper card (kraft tint top-left, faint red bottom-right, grain) -------
    card = Image.new("RGBA", (CARD_W, CARD_H), PAPER + (255,))
    tint = Image.new("RGBA", (CARD_W, CARD_H), (0, 0, 0, 0))
    td = ImageDraw.Draw(tint)
    td.ellipse([-CARD_W * 0.25, -CARD_H * 0.35, CARD_W * 0.45, CARD_H * 0.5], fill=KRAFT + (60,))
    td.ellipse([CARD_W * 0.6, CARD_H * 0.6, CARD_W * 1.15, CARD_H * 1.3], fill=POSTAL + (14,))
    card = Image.alpha_composite(card, tint.filter(ImageFilter.GaussianBlur(100)))
    noise = Image.new("RGBA", (CARD_W, CARD_H), (0, 0, 0, 0))
    nd = ImageDraw.Draw(noise)
    random.seed(7)
    for _ in range(5000):
        nd.point((random.randint(0, CARD_W - 1), random.randint(0, CARD_H - 1)),
                 fill=(180, 150, 110, random.randint(4, 14)))
    card = Image.alpha_composite(card, noise)
    cd = ImageDraw.Draw(card, "RGBA")

    # corner ticks (.postcard::before/::after)
    t, o, tick_c = 18, 14, POSTAL + (140,)
    cd.line([(o, o), (o + t, o)], fill=tick_c, width=1)
    cd.line([(o, o), (o, o + t)], fill=tick_c, width=1)
    cd.line([(CARD_W - o, CARD_H - o), (CARD_W - o - t, CARD_H - o)], fill=tick_c, width=1)
    cd.line([(CARD_W - o, CARD_H - o), (CARD_W - o, CARD_H - o - t)], fill=tick_c, width=1)

    # -- header row: masthead | stamp ------------------------------------------
    y = PAD_TOP
    rp._text_spaced(cd, (X0, y + 6), "POST · CARD", plex(14, medium=True), OCEAN, 14 * 0.32)
    f_title = fraunces(46, 600, 144)
    f_title_i = fraunces(46, 500, 144, italic=True)
    ty = y + 32
    cd.text((X0, ty), "Get Happy Info", font=f_title, fill=INK)
    tw = cd.textlength("Get Happy Info", font=f_title)
    cd.text((X0 + tw, ty), ".com", font=f_title_i, fill=POSTAL)
    title_right = X0 + tw + cd.textlength(".com", font=f_title_i)
    cd.text((X0, ty + 56), "A daily postcard of good.", font=fraunces(19, 400, 14, italic=True), fill=INK_SOFT)
    boxes.append(("masthead", X0, y, title_right, ty + 84))

    st_w = 96                                              # .philately 112x132, scaled
    stamp = rs._svg_png(stamp_svg, st_w).convert("RGBA")  # native size: no resampling halos
    st_h = stamp.height
    shadow = Image.new("RGBA", (st_w + 20, st_h + 20), (0, 0, 0, 0))
    shadow.paste(Image.new("RGBA", (st_w - 8, st_h - 8), (0, 0, 0, 55)), (13, 15))
    shadow = shadow.filter(ImageFilter.GaussianBlur(3))
    stamp_layer = Image.alpha_composite(Image.new("RGBA", shadow.size, (0, 0, 0, 0)), shadow)
    stamp_layer.alpha_composite(stamp, dest=(10, 10))
    stamp_layer = rp._rotate(stamp_layer, -2.5)            # .philately rotate(2.5deg)
    pm_size = 120                                          # .postmark 140px on a 112px stamp
    sx = X1 - stamp_layer.width - 22                        # leave room for the postmark's right overhang
    sy = PAD_TOP - 14
    if title_right > sx - 12:
        raise LandscapeFitError("wordmark collides with the stamp")
    card.alpha_composite(stamp_layer, dest=(int(sx), int(sy)))
    pm = rs._svg_png(rs.postmark_svg(d.strftime("%b").upper(), str(d.day), str(d.year)), pm_size).convert("RGBA")
    pm.putalpha(pm.split()[-1].point(lambda v: int(v * 0.9)))
    pm = rp._rotate(pm, -14)
    # centre over the stamp's lower-right like the site (.postmark top 42 / right
    # -34 on the 112x132 stamp = centre at 68% / 85%), nudged up a little so the
    # ring stays clear of the right column's text
    pcx, pcy = sx + 10 + 0.70 * st_w, sy + 10 + 0.78 * st_h
    pmx = min(int(pcx - pm.width / 2), CARD_W - PAD_X + 8 - pm.width)
    pmy = int(pcy - pm.height / 2)
    card.alpha_composite(pm, dest=(pmx, pmy))
    sb = stamp_layer.split()[-1].point(lambda v: 255 if v > 24 else 0).getbbox()   # visible stamp (not its soft shadow pad)
    pb = pm.split()[-1].point(lambda v: 255 if v > 24 else 0).getbbox()
    boxes.append(("stamp", int(sx) + sb[0], int(sy) + sb[1], int(sx) + sb[2], int(sy) + sb[3]))
    boxes.append(("postmark", pmx + pb[0], pmy + pb[1], pmx + pb[2], pmy + pb[3]))
    cd = ImageDraw.Draw(card, "RGBA")

    cd.line([(X0, RULE_Y), (X1, RULE_Y)], fill=OCEAN, width=2)            # border-bottom 1.5px
    cd.line([(X0, RULE_Y + 5), (X1, RULE_Y + 5)], fill=OCEAN + (90,), width=1)  # ::after, 35%

    # -- body: two columns, top-aligned (align-items: start) -------------------
    slack = BODY_AVAIL - plan["height"]
    if slack < 0:
        raise LandscapeFitError("internal: plan does not fit")
    top = BODY_TOP + min(slack // 2, 16)

    # left: the note
    y = top
    _section_label(cd, y, "A NOTE TO YOU", LEFT_X, LEFT_X + LEFT_W, boxes)
    y += LABEL_H + LABEL_GAP
    cd.text((LEFT_X, y), "Dear friend,", font=fraunces(21, 400, 24, italic=True), fill=INK_SOFT)
    y += GREETING_H
    for ln in plan["line_lines"]:
        cd.text((LEFT_X, y), ln, font=plan["f_line"], fill=INK)
        boxes.append(("line", LEFT_X, y, LEFT_X + cd.textlength(ln, font=plan["f_line"]), y + plan["line_lh"]))
        y += plan["line_lh"]
    y += LINE_GAP
    sig = Image.new("RGBA", (340, 70), (0, 0, 0, 0))
    sd = ImageDraw.Draw(sig, "RGBA")
    sd.text((4, 2), "Yours in joy,", font=fraunces(23, 400, 36, italic=True), fill=POSTAL)
    sd.text((4, 31), "~ Get Happy", font=fraunces(24, 600, 36, italic=True), fill=POSTAL)
    sig = rp._rotate(sig, 2.5)                                             # .signature rotate(-2.5deg)
    card.alpha_composite(sig, dest=(int(LEFT_X + 2), int(y - 6)))
    cd = ImageDraw.Draw(card, "RGBA")
    boxes.append(("signature", LEFT_X + 2, y - 6, LEFT_X + 2 + 200, y - 6 + SIG_H))
    left_bottom = y + SIG_H

    # right: a little more
    y = top
    _section_label(cd, y, "A LITTLE MORE", RIGHT_X, RIGHT_X + RIGHT_W, boxes)
    y += LABEL_H + LABEL_GAP
    for ln in plan["para_lines"]:
        cd.text((RIGHT_X, y), ln, font=plan["f_para"], fill=INK)
        boxes.append(("para", RIGHT_X, y, RIGHT_X + cd.textlength(ln, font=plan["f_para"]), y + plan["para_lh"]))
        y += plan["para_lh"]
    if plan["src_lines"]:
        y += SRC_GAP
        for ln in plan["src_lines"]:
            cd.text((RIGHT_X, y + 1), ln, font=plan["f_src"], fill=INK_SOFT)
            boxes.append(("source", RIGHT_X, y, RIGHT_X + cd.textlength(ln, font=plan["f_src"]), y + SRC_LH))
            y += SRC_LH
    right_bottom = y
    body_bottom = max(left_bottom, right_bottom)
    if body_bottom > FOOTER_Y - BODY_TO_FOOTER:
        raise LandscapeFitError(f"internal: body overflowed into footer ({body_bottom} > {FOOTER_Y - BODY_TO_FOOTER})")

    # vertical dashed divider (align-self: stretch; 4px on / 4px off, 45%)
    dy = top
    while dy < body_bottom:
        cd.line([(DIV_X, dy), (DIV_X, min(dy + 4, body_bottom))], fill=OCEAN + (115,), width=DIV_W)
        dy += 8

    # -- footer -----------------------------------------------------------------
    cd.line([(X0, FOOTER_Y), (X1, FOOTER_Y)], fill=OCEAN, width=2)
    f_ft = plex(14)
    ed = f"GETHAPPYINFO.COM · NO. {edition_no(joy['date'])}"
    rp._text_spaced(cd, (X0, FOOTER_Y + 22), ed, f_ft, OCEAN, 14 * 0.22)
    btn = "✉ PASS IT ON ↗"
    f_btn = rs.F(rs.MONO, 14)  # DejaVu has ✉/↗; Plex Mono does not
    bw = int(rp._tw(cd, btn, f_btn, 14 * 0.18)) + 32
    bh = 36
    bx, by = X1 - bw, FOOTER_Y + 13
    cd.rounded_rectangle([bx, by, bx + bw, by + bh], radius=2, fill=POSTAL)
    rp._text_spaced(cd, (bx + 16, by + 9), btn, f_btn, PAPER, 14 * 0.18)
    boxes.append(("edition", X0, FOOTER_Y + 18, X0 + rp._tw(cd, ed, f_ft, 14 * 0.22), FOOTER_Y + 42))
    boxes.append(("button", bx, by, bx + bw, by + bh))

    # -- composite: card shadow ---------------------------------------------------
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sh = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sh.paste(Image.new("RGBA", (CARD_W - 24, CARD_H - 12), (0, 0, 0, 150)), (CARD_X0 + 12, CARD_Y0 + 14))
    sh = sh.filter(ImageFilter.GaussianBlur(14))
    layer.paste(card, (CARD_X0, CARD_Y0), card)
    out = Image.alpha_composite(Image.alpha_composite(bg, sh), layer).convert("RGB")
    out.info["landscape_fit"] = (plan["line_size"], plan["para_size"])
    out.info["landscape_boxes"] = [(w, x0 + CARD_X0, y0 + CARD_Y0, x1 + CARD_X0, y1 + CARD_Y0)
                                   for (w, x0, y0, x1, y1) in boxes]
    out.info["landscape_plan"] = {k: plan[k] for k in ("line_lines", "para_lines", "src_lines", "src",
                                                     "line_size", "para_size", "height")}
    return out


def render_landscape_png(joy: dict, stamp_svg: str) -> tuple[bytes, tuple[int, int]]:
    img = render_landscape(joy, stamp_svg)
    if img.size != (W, H):
        raise RuntimeError(f"unexpected landscape size {img.size}")
    sizes = img.info["landscape_fit"]
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    data = buf.getvalue()
    if len(data) < 30_000:
        raise RuntimeError(f"rendered landscape PNG suspiciously small ({len(data)} bytes)")
    return data, sizes


def render(joy_path: Path, index_path: Path, out: Path) -> tuple[Path, tuple[int, int]]:
    """Render and write `out` atomically (temp file + os.replace). Nothing is
    written unless the full render and sanity checks passed."""
    rs._load_deps()
    missing = [str(p) for p in rs.REQUIRED_FONTS if not p.is_file()]
    if missing:
        raise RuntimeError(f"missing pinned fonts: {missing}")
    joy = json.loads(joy_path.read_text(encoding="utf-8"))
    data, sizes = render_landscape_png(joy, rs.extract_stamp_svg(index_path.read_text(encoding="utf-8")))
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(f".{out.name}.tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, out)
    finally:
        if tmp.exists():
            tmp.unlink()
    return out, sizes


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--joy", type=Path, default=rs.REPO / "public" / "joy.json")
    ap.add_argument("--index", type=Path, default=rs.REPO / "public" / "index.html")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--out-dir", type=Path, default=None,
                   help="writes <out-dir>/art/<date>-landscape.png (default: public/)")
    g.add_argument("--out", type=Path, default=None, help="explicit output PNG path")
    args = ap.parse_args(argv)
    date = "?"
    try:
        date = json.loads(args.joy.read_text(encoding="utf-8")).get("date") or "?"
        out = args.out or (args.out_dir or rs.REPO / "public") / "art" / f"{date}-landscape.png"
        path, (ls, ps) = render(args.joy, args.index, out)
    except Exception as e:  # landscape is optional: warn, write nothing, exit 1
        msg = f"Landscape postcard skipped for {date}: {type(e).__name__}: {e}"
        print(f"::warning::{msg}", file=sys.stderr)
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            with open(summary, "a", encoding="utf-8") as fh:
                fh.write(f"> **⚠️ {msg}** (no landscape file, no Landscape: line today; "
                         "share images, portrait, reel and today.txt are unaffected)\n\n")
        return 1
    print(f"[render_landscape] {date}: wrote {path} (1200x630, line {ls}px, paragraph {ps}px)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
