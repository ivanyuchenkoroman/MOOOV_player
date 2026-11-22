# MOOOV_player: design system

Internal UI language for MOOOV tools. It is based on the MOOOV logo, but the brand stays quiet in the interface.
All tokens are in [ui_theme.py](ui_theme.py). The code takes colours and sizes only from there, with no hex values in place.

## Brand
| Token | Value | Where it is used |
|---|---|---|
| `BRAND_YELLOW` | `#FFD02B` | The only accent colour in the interface |
| `BRAND_BLACK` | `#000000` | Text and icons on yellow (`ON_ACCENT`) |

The letter **M** from the logo (`ui.mark_path()`): two blocks with a V-shaped notch, 2:1 proportions, sharp corners.
It appears only in three places:
- the app icon: a yellow rounded square with a black M (`ui.app_icon()`);
- the empty viewer screen: a quiet grey M above the hint;
- the PDF: a small yellow M in the bottom-right corner of the page.

The shape motif comes from the **O** in the logo, a capsule. It is used for the playhead tag, the Play button and the counter badges.

## Colour
| Token | Value | Purpose |
|---|---|---|
| `BG` | `#121212` | Window and viewer background |
| `SURFACE` | `#1a1a1a` | Panels, timeline track |
| `CONTROL` | `#242424` | Buttons, fields |
| `HOVER` | `#2e2e2e` | Hover |
| `BORDER` / `BORDER_HI` | `#363636` / `#4d4d4d` | Border / border on hover |
| `TEXT` / `DIM` / `MUTED` | `#f0f0f0` / `#8c8c8c` / `#555555` | Main / secondary / inactive text |
| `ACCENT` | = `BRAND_YELLOW` | Play, focus, selected, playhead, frame number |
| `ACCENT_TINT` | `#3a3213` | Background of the selected item, text selection |
| `ACCENT_LINE` | `#7a6420` | Muted yellow border |
| `NOTE` | = `BRAND_YELLOW` | Comments: timeline dots, numbers on drawings, counter (black text, `ON_NOTE`) |
| `CACHE` | `#4fae6e` | Cache line on the timeline |
| `ERROR` | `#ff5a5a` | Errors |

The greys are pure neutral, with no blue tint, so they sit well next to the black of the logo.
Yellow is the only colour in the interface. It marks what is active and where the comments are. Colour inside drawings is chosen by the user and is not part of the interface.

## Shape: circles and capsules only
The whole interface is built from the "O" in the logo. There are no sharp or slightly rounded corners.
- Icon buttons are **circles** (diameter = control height).
- Fields, lists, text buttons, the timeline track and the Play button are **capsules** (`RADIUS = H/2`, `RADIUS_S = HS/2`).
- Checkboxes are round. Colour swatches and comment counters are circles.
- Large blocks (comment cards, text fields, info panel, menus) use `RADIUS_CARD = 14`.
- The only sharp shape is the letter M itself.

## Sizes
- Primary controls: `H = 28` px, font 13 px, icons 16 px, capsule.
- Secondary controls (`ui.small()`): `HS = 20` px, font 11 px, icons 12 px, capsule. That is about 30% smaller.
- Primary button (`primary="true"`): yellow capsule, black icon. Use one per screen (Play).

## Components
- `ui.icon_button(name, tip, checkable)`: icon button. Checked state: `ACCENT_TINT` background with an `ACCENT` border.
- `ui.text_button(text, tip, icon)`: text button.
- `ui.small(*widgets)`: makes a control secondary.
- `ui.steppers(spin)`: ▲▼ arrows in a number field.
- `ui.fit_combo`, `ui.fit_spin`, `ui.fit_label`: widths sized to content, so the bar does not jump.
- `ui.tabular(widget)`: fixed-width digits for counters and timecode.
- `ui.icon(name, color)`: vector icons drawn in code, 18×18.
