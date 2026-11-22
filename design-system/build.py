"""Собирает переносимый пакет дизайн-системы MOOOV из ui_theme.py (единственный источник правды).

    uv run python design-system/build.py

Пишет рядом: tokens/ (json, css, js), icons/ (svg + sprite), brand/ (svg + png), qt/ui_theme.py.
Иконки не перерисованы руками: тот же код _draw() пишется в SVG через рекордер, поэтому совпадают 1:1.
"""
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from PySide6 import QtCore, QtGui  # noqa: E402

import annotations as ann  # noqa: E402
import ui_theme as ui  # noqa: E402

VERSION = "1.1.0"

# ------------------------------------------------------------------ токены

COLOR = [  # (имя, значение, где)
    ("brand-yellow", ui.BRAND_YELLOW, "Brand yellow. The only accent in the interface."),
    ("brand-black", ui.BRAND_BLACK, "Brand black. Text and icons on yellow."),
    ("bg", ui.BG, "Window and viewer background."),
    ("surface", ui.SURFACE, "Panels (draw bar, comments), timeline track."),
    ("control", ui.CONTROL, "Buttons, fields, menus, tooltips."),
    ("hover", ui.HOVER, "Control on hover."),
    ("border", ui.BORDER, "Control border."),
    ("border-hi", ui.BORDER_HI, "Border on hover, scrollbar handle."),
    ("text", ui.TEXT, "Main text and icons."),
    ("dim", ui.DIM, "Secondary text, section titles, chevrons."),
    ("muted", ui.MUTED, "Inactive: disabled icons, placeholders, watermarks."),
    ("accent", ui.ACCENT, "Play, focus, selected, playhead, frame number."),
    ("on-accent", ui.ON_ACCENT, "Text and icons on accent."),
    ("accent-hover", ui.ACCENT_HOVER, "Primary button on hover."),
    ("accent-pressed", ui.ACCENT_PRESSED, "Primary button pressed."),
    ("accent-tint", ui.ACCENT_TINT, "Selected item background, text selection, checked toggle."),
    ("accent-line", ui.ACCENT_LINE, "Muted yellow border."),
    ("note", ui.NOTE, "Comments: timeline dots, numbers on drawings, counters."),
    ("on-note", ui.ON_NOTE, "Text on comment badges."),
    ("cache", ui.CACHE, "Cached frames line on the timeline."),
    ("error", ui.ERROR, "Errors."),
]
SIZE = [
    ("control-h", ui.H, "Primary control height."),
    ("control-h-small", ui.HS, "Secondary control height (~30% smaller)."),
    ("control-h-primary", ui.HP, "Primary button (Play) diameter."),
    ("radius", ui.RADIUS, "Capsule radius of primary controls (H/2)."),
    ("radius-small", ui.RADIUS_S, "Capsule radius of secondary controls (HS/2)."),
    ("radius-card", ui.RADIUS_CARD, "Cards, panels, text areas."),
    ("radius-menu", 12, "Menus and dropdown lists."),
    ("radius-menu-item", 11, "Highlighted menu item."),
    ("radius-tooltip", 10, "Tooltips."),
    ("icon", ui.ICON, "Icon size in primary controls."),
    ("icon-small", ui.ICON_S, "Icon size in secondary controls."),
    ("icon-grid", 18, "Icon drawing grid."),
    ("pad-x", 14, "Text button side padding."),
    ("field-pad-x", 12, "Field / list side padding."),
    ("field-pad-x-small", 9, "Secondary field side padding."),
    ("popup-gap", 6, "Gap between a control and its popup."),
]
FONT = [
    ("size", ui.FONT_PX, "Base text."),
    ("size-small", ui.FONT_PX_S, "Secondary controls, section titles."),
]
FONT_STACK = '-apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", system-ui, sans-serif'
DRAW = [(f"draw-{i + 1}", c) for i, c in enumerate(ann.COLORS)]


def tokens_json():
    t = {
        "$description": f"MOOOV design system {VERSION}. Generated from ui_theme.py, do not edit by hand.",
        "color": {k: {"$type": "color", "$value": v.upper(), "$description": d} for k, v, d in COLOR},
        "size": {k: {"$type": "dimension", "$value": f"{v}px", "$description": d} for k, v, d in SIZE},
        "font": {
            "family": {"$type": "fontFamily", "$value": FONT_STACK,
                       "$description": "System UI font: SF Pro on Mac, Segoe UI on Windows."},
            **{k: {"$type": "dimension", "$value": f"{v}px", "$description": d} for k, v, d in FONT},
            "weight-strong": {"$type": "fontWeight", "$value": 600, "$description": "Frame number, section titles."},
            "tracking-label": {"$type": "dimension", "$value": "0.5px", "$description": "Uppercase section titles."},
            "numeric": {"$type": "string", "$value": "tabular-nums",
                        "$description": "Counters and timecode use fixed-width digits."},
        },
        "draw": {k: {"$type": "color", "$value": v.upper(),
                     "$description": "Drawing palette preset (user content, not interface)."} for k, v in DRAW},
        "mark": {"leg": {"$type": "number", "$value": ui.MARK_LEG,
                         "$description": "Width of the M leg as a share of the letter width; also the M-to-triangle gap."}},
    }
    return json.dumps(t, indent=2, ensure_ascii=False) + "\n"


def tokens_css():
    out = [f"/* MOOOV design system {VERSION}: tokens. Generated from ui_theme.py, do not edit by hand. */", ":root {"]
    out += [f"  --mo-{k}: {v.lower()};" for k, v, _ in COLOR]
    out += [f"  --mo-{k}: {v}px;" for k, v, _ in SIZE]
    out += [f"  --mo-font: {FONT_STACK};", f"  --mo-font-size: {ui.FONT_PX}px;", f"  --mo-font-size-small: {ui.FONT_PX_S}px;"]
    out += [f"  --mo-{k}: {v};" for k, v in DRAW]
    out += [f"  --mo-mark-leg: {ui.MARK_LEG};", "  color-scheme: dark;", "}"]
    return "\n".join(out) + "\n"


def tokens_js():
    flat = {**{k: v.lower() for k, v, _ in COLOR}, **{k: v for k, v, _ in SIZE},
            "fontSize": ui.FONT_PX, "fontSizeSmall": ui.FONT_PX_S, "font": FONT_STACK,
            "draw": [c for _, c in DRAW], "markLeg": ui.MARK_LEG}
    camel = {"".join(w if i == 0 else w.capitalize() for i, w in enumerate(k.split("-"))): v for k, v in flat.items()}
    return (f"// MOOOV design system {VERSION}: tokens. Generated from ui_theme.py, do not edit by hand.\n"
            f"export const mooov = {json.dumps(camel, indent=2, ensure_ascii=False)};\nexport default mooov;\n")


# ------------------------------------------------------------------ иконки: QPainter → SVG


def _f(x):
    return f"{x:.2f}".rstrip("0").rstrip(".")


class SvgRecorder:
    """Подменяет QPainter для ui._draw(): записывает фигуры как SVG в currentColor."""

    def __init__(self):
        self.out, self.stroke = [], None
        self._brush = QtGui.QBrush(QtGui.QColor("#000"))

    def brush(self):
        return self._brush

    def setPen(self, pen):
        self.stroke = pen.widthF() if isinstance(pen, QtGui.QPen) and pen.style() != QtCore.Qt.NoPen else None

    def setBrush(self, b):
        self.fill = not (isinstance(b, QtCore.Qt.BrushStyle) and b == QtCore.Qt.NoBrush)

    fill = True

    def _attrs(self):
        if self.stroke:
            return (f'fill="none" stroke="currentColor" stroke-width="{_f(self.stroke)}" '
                    f'stroke-linecap="round" stroke-linejoin="round"')
        return 'fill="currentColor"'

    def drawPolygon(self, poly):
        pts = " ".join(f"{_f(p.x())},{_f(p.y())}" for p in poly)
        self.out.append(f'<polygon points="{pts}" {self._attrs()}/>')

    def drawPolyline(self, poly):
        pts = " ".join(f"{_f(p.x())},{_f(p.y())}" for p in poly)
        self.out.append(f'<polyline points="{pts}" {self._attrs()}/>')

    def drawRoundedRect(self, r, rx, ry):
        self.out.append(f'<rect x="{_f(r.x())}" y="{_f(r.y())}" width="{_f(r.width())}" height="{_f(r.height())}" '
                        f'rx="{_f(rx)}" {self._attrs()}/>')

    def drawEllipse(self, a, rx=None, ry=None):
        if rx is None:
            c, rx, ry = a.center(), a.width() / 2, a.height() / 2
        else:
            c = a
        self.out.append(f'<ellipse cx="{_f(c.x())}" cy="{_f(c.y())}" rx="{_f(rx)}" ry="{_f(ry)}" {self._attrs()}/>')

    def drawLine(self, a, b):
        self.out.append(f'<line x1="{_f(a.x())}" y1="{_f(a.y())}" x2="{_f(b.x())}" y2="{_f(b.y())}" {self._attrs()}/>')

    def drawPoint(self, p):
        r = (self.stroke or 1.6) / 2
        self.out.append(f'<circle cx="{_f(p.x())}" cy="{_f(p.y())}" r="{_f(r)}" fill="currentColor"/>')

    def drawPath(self, path):
        d, i = [], 0
        while i < path.elementCount():
            e = path.elementAt(i)
            if e.type == QtGui.QPainterPath.MoveToElement:
                d.append(f"M{_f(e.x)} {_f(e.y)}")
            elif e.type == QtGui.QPainterPath.LineToElement:
                d.append(f"L{_f(e.x)} {_f(e.y)}")
            elif e.type == QtGui.QPainterPath.CurveToElement:
                c2, end = path.elementAt(i + 1), path.elementAt(i + 2)
                d.append(f"C{_f(e.x)} {_f(e.y)} {_f(c2.x)} {_f(c2.y)} {_f(end.x)} {_f(end.y)}")
                i += 2
            i += 1
        first, last = path.elementAt(0), path.elementAt(path.elementCount() - 1)
        if path.elementCount() > 2 and (first.x, first.y) == (last.x, last.y):
            d.append("Z")
        self.out.append(f'<path d="{" ".join(d)}" {self._attrs()}/>')


ICONS = ["play", "pause", "prev", "next", "first", "last", "plus", "pen", "arrow", "rect", "ellipse", "image",
         "undo", "trash", "zoom", "mark_in", "mark_out", "info", "pdf", "chev_left", "chev_right", "more", "list", "eye", "eye_off", "split", "chev_up", "chev_down", "close"]
# иконки, которые в Qt-стиле нарисованы отдельно (QSS), — тоже отдаём
EXTRA_ICONS = {
    "chevron_down": '<path d="M5.4 7.3 L9 10.9 L12.6 7.3" fill="none" stroke="currentColor" stroke-width="1.9" '
                    'stroke-linecap="round" stroke-linejoin="round"/>',
    "chevron_up": '<path d="M5.4 10.7 L9 7.1 L12.6 10.7" fill="none" stroke="currentColor" stroke-width="1.9" '
                  'stroke-linecap="round" stroke-linejoin="round"/>',
    "check": '<path d="M3.75 9.3 L7.5 12.9 L14.4 5.1" fill="none" stroke="currentColor" stroke-width="2.4" '
             'stroke-linecap="round" stroke-linejoin="round"/>',
}


def icon_body(name):
    if name in EXTRA_ICONS:
        return EXTRA_ICONS[name]
    rec = SvgRecorder()
    ui._draw(rec, name)
    return "".join(rec.out)


def icon_svg(name):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 18 18" '
            f'color="{ui.TEXT}">{icon_body(name)}</svg>\n')


# ------------------------------------------------------------------ бренд

M_PTS = [(0, 0), (0.219, 0), (0.5, 0.409), (0.781, 0), (1, 0), (1, 1), (0.723, 1), (0.723, 0.591),
         (0.5, 0.903), (0.277, 0.591), (0.277, 1), (0, 1)]


def m_points(x0, y0, w):
    h = w / 2
    return " ".join(f"{_f(x0 + x * w)},{_f(y0 + y * h)}" for x, y in M_PTS)


def mark_svg(color=ui.BRAND_YELLOW):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="200" height="100" viewBox="0 0 200 100">'
            f'<polygon points="{m_points(0, 0, 200)}" fill="{color}"/></svg>\n')


def logo_svg(color=ui.BRAND_YELLOW):
    h = 100
    tx = h * 2 + h * 2 * ui.MARK_LEG
    w = tx + h * 0.866
    tri = f"{_f(tx)},0 {_f(tx + h * 0.866)},{_f(h / 2)} {_f(tx)},{h}"
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{_f(w)}" height="{h}" viewBox="0 0 {_f(w)} {h}">'
            f'<polygon points="{m_points(0, 0, 200)}" fill="{color}"/><polygon points="{tri}" fill="{color}"/></svg>\n')


def app_icon_svg():
    s, m = 512, 512 * 0.08
    inner = s - 2 * m
    pad = s * 0.2
    mw = min(inner - 2 * pad, (inner - 2 * pad) * 2)  # вписываем M 2:1 в квадрат
    mh = mw / 2
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{s}" height="{s}" viewBox="0 0 {s} {s}">'
            f'<rect x="{_f(m)}" y="{_f(m)}" width="{_f(inner)}" height="{_f(inner)}" rx="{_f(s * 0.2)}" '
            f'fill="{ui.BRAND_YELLOW}"/><polygon points="{m_points(s / 2 - mw / 2, s / 2 - mh / 2, mw)}" '
            f'fill="{ui.BRAND_BLACK}"/></svg>\n')


# ------------------------------------------------------------------ preview.html


def preview_html(names):
    ic = lambda n: f'<svg viewBox="0 0 18 18" aria-hidden="true">{icon_body(n)}</svg>'
    sw = "".join(f'<div class="sw"><i style="background:{v}"></i><b>{k}</b><code>{v.upper()}</code><small>{d}</small></div>'
                 for k, v, d in COLOR)
    draw = "".join(f'<span class="mo-swatch{" is-active" if i == 1 else ""}" style="background:{c}"></span>'
                   for i, (_, c) in enumerate(DRAW))
    icons = "".join(f'<figure>{ic(n)}<figcaption>{n.replace("_", "-")}</figcaption></figure>' for n in names)
    sizes = "".join(f"<tr><td><code>{k}</code></td><td>{v}px</td><td>{d}</td></tr>" for k, v, d in SIZE)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>MOOOV design system</title>
<link rel="stylesheet" href="tokens/tokens.css"><link rel="stylesheet" href="components/components.css">
<style>
body {{ margin: 0; }}
main {{ max-width: 1040px; margin: 0 auto; padding: 40px max(16px, 3vw) 80px; display: grid; gap: 48px; }}
header {{ display: flex; align-items: center; gap: 20px; flex-wrap: wrap; }}
header img {{ height: 40px; }}
header p {{ margin: 0; color: var(--mo-dim); max-width: 60ch; }}
section {{ display: grid; gap: 16px; }}
.grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 10px; }}
.sw {{ display: grid; grid-template-columns: 36px 1fr; column-gap: 12px; align-items: center; padding: 10px;
      background: var(--mo-surface); border-radius: var(--mo-radius-card); }}
.sw i {{ grid-row: span 3; width: 36px; height: 36px; border-radius: 50%; border: 1px solid var(--mo-border-hi); }}
.sw code {{ color: var(--mo-dim); font-size: 11px; }} .sw small {{ color: var(--mo-muted); font-size: 11px; }}
.row {{ display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }}
.panel {{ background: var(--mo-surface); border-radius: var(--mo-radius-card); padding: 16px; display: grid; gap: 14px; }}
.icons {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(92px, 1fr)); gap: 8px; }}
.icons figure {{ margin: 0; display: grid; justify-items: center; gap: 8px; padding: 14px 4px 10px;
                background: var(--mo-surface); border-radius: var(--mo-radius-card); }}
.icons svg {{ width: 36px; height: 36px; }} .icons figcaption {{ font-size: 11px; color: var(--mo-dim); }}
table {{ border-collapse: collapse; width: 100%; font-variant-numeric: tabular-nums; }}
td {{ padding: 6px 10px 6px 0; border-bottom: 1px solid var(--mo-border); vertical-align: top; }}
td:nth-child(3) {{ color: var(--mo-dim); }}
.brand {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 10px; }}
.brand div {{ display: grid; place-items: center; height: 140px; border-radius: var(--mo-radius-card); }}
.brand img {{ max-height: 56px; max-width: 70%; }}
.timeline {{ position: relative; height: 28px; background: var(--mo-surface); border-radius: 14px; overflow: hidden; }}
.timeline .cache {{ position: absolute; left: 0; bottom: 0; height: 2px; width: 62%; background: var(--mo-cache); }}
.timeline .dot {{ position: absolute; top: 10px; width: 8px; height: 8px; border-radius: 50%; background: var(--mo-note); }}
.timeline .ph {{ position: absolute; top: 0; bottom: 0; left: 41%; width: 2px; background: var(--mo-accent); }}
</style></head>
<body class="mo"><main>
<header><img src="brand/logo-yellow.svg" alt="MOOOV logo"><div><h1 style="margin:0;font-size:22px">MOOOV design system {VERSION}</h1>
<p>One yellow accent, neutral greys, circles and capsules only. Generated from qt/ui_theme.py.</p></div></header>

<section><div class="mo-section-title">Controls</div>
<div class="panel">
<div class="row">
<button class="mo-btn mo-btn--icon" title="First frame">{ic("first")}</button>
<button class="mo-btn mo-btn--icon" title="Previous frame">{ic("prev")}</button>
<button class="mo-btn mo-btn--primary" title="Play">{ic("play")}</button>
<button class="mo-btn mo-btn--icon" title="Next frame">{ic("next")}</button>
<button class="mo-btn mo-btn--icon" title="Last frame">{ic("last")}</button>
<input class="mo-field mo-frame" value="1042" size="5" aria-label="Frame">
<select class="mo-select" aria-label="Speed"><option>1×</option><option>0.5×</option><option>2×</option></select>
<button class="mo-btn">{ic("pdf")}Export PDF</button>
<button class="mo-btn" disabled>Disabled</button>
</div>
<div class="timeline"><span class="cache"></span><span class="dot" style="left:18%"></span><span class="dot" style="left:55%"></span><span class="ph"></span></div>
<div class="row"><span class="mo-playhead">00:00:43:10</span><span class="mo-badge">3</span><span class="mo-badge">12</span>
<span class="mo-tooltip">Next frame (→)</span></div>
</div>
<div class="panel mo-small">
<div class="mo-section-title">Secondary size</div>
<div class="row">
<button class="mo-btn mo-btn--icon" title="Pen" aria-pressed="true">{ic("pen")}</button>
<button class="mo-btn mo-btn--icon" title="Arrow">{ic("arrow")}</button>
<button class="mo-btn mo-btn--icon" title="Rectangle">{ic("rect")}</button>
<button class="mo-btn mo-btn--icon" title="Ellipse">{ic("ellipse")}</button>
<button class="mo-btn mo-btn--icon" title="Reference image">{ic("image")}</button>
{draw}
<input class="mo-field" value="size 6" size="7" aria-label="Size">
<input class="mo-field" value="opacity 100%" size="11" aria-label="Opacity">
<label class="mo-check"><input type="checkbox" checked> Ghost</label>
<button class="mo-btn mo-btn--icon" title="Undo">{ic("undo")}</button>
<button class="mo-btn mo-btn--icon" title="Clear">{ic("trash")}</button>
</div></div>
<div class="row" style="align-items:flex-start">
<div class="mo-menu"><div class="mo-menu-item">Open… <kbd>Ctrl+O</kbd></div><div class="mo-menu-item is-active">Compare with… <kbd>Ctrl+Shift+C</kbd></div>
<div class="mo-menu-sep"></div><div class="mo-menu-item">Export PDF <kbd>Ctrl+E</kbd></div></div>
<div class="mo-card" style="max-width:300px"><div class="row"><span class="mo-badge">1</span><b>HAT_091_0205</b><span class="mo-dim">fr 1042</span></div>
<p style="margin:8px 0 0">Edge of the matte flickers on the left shoulder.</p></div>
<div class="mo-card is-selected" style="max-width:300px"><div class="row"><span class="mo-badge">2</span><b>Selected</b></div>
<p style="margin:8px 0 0" class="mo-dim">accent-tint background, accent-line border.</p></div>
<label class="mo-check"><input type="checkbox"> Loop</label>
</div>
</section>

<section><div class="mo-section-title">Colour</div><div class="grid">{sw}</div></section>
<section><div class="mo-section-title">Icons · 18×18 · currentColor</div><div class="icons">{icons}</div></section>
<section><div class="mo-section-title">Brand</div><div class="brand">
<div style="background:var(--mo-bg);border:1px solid var(--mo-border)"><img src="brand/logo-yellow.svg" alt="Logo, yellow"></div>
<div style="background:var(--mo-brand-yellow)"><img src="brand/logo-black.svg" alt="Logo, black"></div>
<div style="background:var(--mo-surface)"><img src="brand/app-icon.svg" alt="App icon" style="max-height:100px"></div>
<div style="background:var(--mo-bg);border:1px solid var(--mo-border)"><img src="brand/mark-white.svg" alt="Letter M" style="opacity:.25"></div>
</div></section>
<section><div class="mo-section-title">Sizes</div><div style="overflow-x:auto"><table>{sizes}</table></div></section>
</main></body></html>
"""


def inline(html):
    """preview.html — один самодостаточный файл: CSS и картинки внутри, открывается откуда угодно."""
    import base64
    import re
    read = lambda rel: open(os.path.join(HERE, rel), encoding="utf-8").read()
    html = re.sub(r'<link rel="stylesheet" href="([^"]+)">', lambda m: f"<style>\n{read(m.group(1))}</style>", html)
    return re.sub(r'src="(brand/[^"]+\.svg)"', lambda m: 'src="data:image/svg+xml;base64,'
                  + base64.b64encode(read(m.group(1)).encode()).decode() + '"', html)


# ------------------------------------------------------------------ сборка


def write(rel, text):
    p = os.path.join(HERE, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return p


def main():
    app = QtGui.QGuiApplication.instance() or QtGui.QGuiApplication([])  # noqa: F841
    write("tokens/tokens.json", tokens_json())
    write("tokens/tokens.css", tokens_css())
    write("tokens/tokens.js", tokens_js())

    names = ICONS + list(EXTRA_ICONS)
    shutil.rmtree(os.path.join(HERE, "icons"), ignore_errors=True)
    for n in names:
        write(f"icons/{n.replace('_', '-')}.svg", icon_svg(n))
    sprite = "".join(f'<symbol id="mo-{n.replace("_", "-")}" viewBox="0 0 18 18">{icon_body(n)}</symbol>' for n in names)
    write("icons/sprite.svg", f'<svg xmlns="http://www.w3.org/2000/svg" style="display:none">{sprite}</svg>\n')

    write("brand/mark-yellow.svg", mark_svg(ui.BRAND_YELLOW))
    write("brand/mark-black.svg", mark_svg(ui.BRAND_BLACK))
    write("brand/mark-white.svg", mark_svg("#FFFFFF"))
    write("brand/logo-yellow.svg", logo_svg(ui.BRAND_YELLOW))
    write("brand/logo-black.svg", logo_svg(ui.BRAND_BLACK))
    write("brand/logo-white.svg", logo_svg("#FFFFFF"))
    write("brand/app-icon.svg", app_icon_svg())
    for size in (1024, 256):
        ui.app_icon().pixmap(size, size).toImage().save(os.path.join(HERE, f"brand/app-icon-{size}.png"))

    write("preview.html", inline(preview_html(names)))
    os.makedirs(os.path.join(HERE, "qt"), exist_ok=True)
    shutil.copy(os.path.join(ROOT, "ui_theme.py"), os.path.join(HERE, "qt", "ui_theme.py"))
    print(f"ok: {len(names)} icons, {len(COLOR)} colours, {len(SIZE)} sizes")


if __name__ == "__main__":
    main()
