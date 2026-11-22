# MOOOV design system 1.1.0

This is the interface language of MOOOV tools. It started in MOOOV_player and can be used in any other interface: a web app, a desktop app, a plugin, a Figma file.

The package is generated from `qt/ui_theme.py`, which is the one source of truth. Do not edit the files by hand. Change `ui_theme.py` and run `uv run python design-system/build.py`.

```
tokens/       tokens.json (W3C design tokens) · tokens.css (CSS variables) · tokens.js (ES module)
components/   components.css: buttons, fields, checkbox, menus, cards, badges for the web
icons/        32 SVG icons, 18×18 grid, colour = currentColor · sprite.svg with all of them
brand/        letter M and logo (yellow / black / white), app icon (SVG and PNG)
qt/           ui_theme.py: the same system for PySide6/PyQt (stylesheet, icons, helpers)
preview.html  every token and component on one page; open it in a browser
```

## Five rules

1. **One accent.** Brand yellow `#FFD02B` is the only colour in the interface. It marks what is active: Play, focus, the selected item, the playhead, the frame number and comments. Everything else is neutral grey.
2. **Neutral greys.** Pure grey with no blue tint, so it sits well next to the black of the logo. The interface is dark only.
3. **Circles and capsules only.** The shape comes from the "O" in the logo. Icon buttons are circles. Fields, lists and text buttons are capsules with `radius = height / 2`. Large blocks (cards, menus, text areas) use `radius-card` = 14. Nothing has a sharp or slightly rounded corner, except the letter M.
4. **Two sizes.** Primary controls are 28 px high, with 13 px text and 16 px icons. Secondary controls are 20 px, 11 px and 12 px (about 30% smaller). The primary button (Play) is a 38 px yellow circle, and there is one per screen.
5. **The brand stays quiet.** The letter M appears in three places only: the app icon, the empty screen (grey) and the corner of exported documents. It never goes on buttons or panels.

## Colour

| Token | Value | Use |
|---|---|---|
| `bg` | `#121212` | Window and viewer background |
| `surface` | `#1A1A1A` | Panels, timeline track |
| `control` | `#242424` | Buttons, fields, menus, tooltips |
| `hover` | `#2E2E2E` | Control on hover |
| `border` / `border-hi` | `#363636` / `#4D4D4D` | Border / border on hover |
| `text` / `dim` / `muted` | `#F0F0F0` / `#8C8C8C` / `#555555` | Main / secondary / inactive |
| `accent` | `#FFD02B` | Active, focus, selected, playhead |
| `on-accent` | `#000000` | Text and icons on yellow |
| `accent-hover` / `accent-pressed` | `#FFDB5C` / `#E6B91F` | Primary button states |
| `accent-tint` | `#3A3213` | Selected background, checked toggle, text selection |
| `accent-line` | `#7A6420` | Muted yellow border (selected card) |
| `note` / `on-note` | `#FFD02B` / `#000000` | Comment dots, badges, counters |
| `cache` | `#4FAE6E` | Cached frames line |
| `error` | `#FF5A5A` | Errors |

Drawing palette (user content, not interface): `#FF3D6E` `#FFD02B` `#3DDC84` `#33C3FF` `#FFFFFF` `#111111`.

## States
| | Background | Border | Text |
|---|---|---|---|
| Rest | `control` | `border` | `text` |
| Hover (button) | `hover` | `border` | `text` |
| Hover (field) | `control` | `border-hi` | `text` |
| Pressed | `surface` | `border` | `text` |
| Focus | `control` | `accent` | `text` |
| Checked / selected | `accent-tint` | `accent` | `text` |
| Disabled | `control` | `border` | `muted` |

## Type
- The system UI font: SF Pro on Mac, Segoe UI on Windows. On the web, `--mo-font`.
- Text is 13 px; secondary text is 11 px.
- Section titles are 11 px, weight 600, uppercase, letter spacing 0.5 px, colour `dim`.
- Numbers that change (frame, timecode, counters) use `tabular-nums`, so the layout does not jump.
- A field's width is set by its longest expected value, not by the current one.

## Icons
- They are drawn on an 18×18 grid in two styles: filled (transport: play, pause, prev, next, first, last, plus) and outline (1.6 px, round caps and joins).
- Colour is `currentColor`.
- Frame-step icons are shifted 0.7 px toward the tip, so they look centred in their circle.

| Name | Meaning |
|---|---|
| `play`, `pause`, `prev`, `next`, `first`, `last` | Transport |
| `mark-in`, `mark-out` | In / out points |
| `pen`, `arrow`, `rect`, `ellipse`, `image` | Drawing tools |
| `undo`, `trash`, `plus`, `more` | Actions |
| `zoom`, `info`, `pdf`, `list` | View, file info, PDF export, playlist |
| `chev-left`, `chev-right`, `chevron-down`, `chevron-up`, `check` | Navigation and form marks |
| `eye`, `eye-off`, `split`, `chev-up`, `chev-down`, `close` | Layers: visibility, split, order, remove |

## Logo
The letter M is two blocks with a V notch, 2:1 proportions and sharp corners. The logo is the M and a Play triangle. The gap between them equals the width of the M's leg (`mark.leg` = 0.277 of the letter's width). The triangle is as high as the M and as wide as 0.866 of that height. Use it yellow on dark, black on yellow, or white. Do not add outlines, gradients or shadows.

## Using it

**Web**
```html
<link rel="stylesheet" href="tokens/tokens.css">
<link rel="stylesheet" href="components/components.css">
<body class="mo">
  <button class="mo-btn mo-btn--primary" aria-label="Play"><svg><use href="icons/sprite.svg#mo-play"/></svg></button>
  <button class="mo-btn mo-btn--icon" aria-label="Next frame"><svg><use href="icons/sprite.svg#mo-next"/></svg></button>
  <input class="mo-field" placeholder="Search">
  <span class="mo-badge">3</span>
</body>
```

**JS / React / Tailwind**
```js
import mooov from "./tokens/tokens.js";  // mooov.accent, mooov.controlH, mooov.radiusCard …
```
For Tailwind, map `theme.extend.colors` to the CSS variables, for example `accent: "var(--mo-accent)"`.

**PySide6 / PyQt**
```python
import ui_theme as ui
ui.apply(app)                                   # palette + stylesheet + rounded popups
ui.icon_button("play", "Play"); ui.small(widget); ui.fit_combo(combo)
```

**Figma.** Import `tokens/tokens.json` with the Tokens Studio plugin, or as variables. It uses the W3C format. Drag the SVGs from `icons/` and `brand/`.

**Other platforms** (Swift, Kotlin, Unity, After Effects): take the values from `tokens.json`. It is the plain list.
