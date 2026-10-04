# Pinned fonts for the daily share render

Used by `scripts/render_share.py` and `scripts/render_portrait.py`. Not served (outside `public/`).

| File | Used for | License |
|---|---|---|
| LiberationSerif-{Regular,Bold,Italic,BoldItalic}.ttf (2.1.5) | card text (Pillow), postmark date (cairosvg) | SIL OFL 1.1 — LICENSE-Liberation.txt |
| DejaVuSansMono.ttf (2.37) | labels/footer (Pillow), postmark ring + year (cairosvg) | Bitstream Vera / DejaVu — LICENSE-DejaVu.txt |
| Fraunces-*-VariableFont…ttf | stamp "GET HAPPY" / "1 joy" (cairosvg, from the index.html stamp SVG); all serif text on the 1080x1920 portrait (Pillow, opsz/wght set per element) | SIL OFL 1.1 — LICENSE-Fraunces-OFL.txt |
| IBMPlexMono-{Regular,Medium}.ttf | portrait kicker/labels/footer (Pillow); stamp "INFO.COM" (cairo's toy API asks for weight *medium*, so Medium is the face actually used) | SIL OFL 1.1 — LICENSE-IBMPlexMono-OFL.txt |

The renderer points fontconfig at this folder only (plus the stock Debian/Ubuntu
hinting rules), so output does not depend on whatever fonts the machine has.
Changing, adding or removing a file here changes the rendered image.
