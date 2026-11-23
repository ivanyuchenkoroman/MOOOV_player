"""Единая тема MOOOV_player: Fusion + тёмная палитра, одинаковые размеры контролов, векторные иконки."""
import math
import os
import sys
import tempfile

from PySide6 import QtCore, QtGui, QtWidgets

# ============================================================== MOOOV design system — токены
# Бренд: жёлтый #FFD02B + чёрный. В интерфейсе бренд не «кричит»: жёлтый — единственный акцент
# (всё активное: Play, фокус, выбранное, плейхед), буква M — только иконка, пустой экран и PDF.
# Форма из логотипа: «O» — капсула → капсулы у плейхеда, Play и бейджей; M — острые углы.

BRAND_YELLOW = "#FFD02B"
BRAND_BLACK = "#000000"

# нейтральные — чистый серый без синевы, чтобы дружить с чёрным логотипа
BG = "#121212"        # фон окна, вьюер
SURFACE = "#1a1a1a"   # панели (рисование, комментарии), дорожка таймлайна
CONTROL = "#242424"   # кнопки, поля
HOVER = "#2e2e2e"
BORDER = "#363636"
BORDER_HI = "#4d4d4d"  # рамка при наведении
TEXT = "#f0f0f0"
DIM = "#8c8c8c"       # вторичный текст
MUTED = "#555555"     # неактивное, водяные знаки

ACCENT = BRAND_YELLOW  # единственный акцент
ON_ACCENT = BRAND_BLACK  # текст/иконки на жёлтом
ACCENT_TINT = "#3a3213"  # жёлтый ~20% на фоне: выбранное, выделение
ACCENT_LINE = "#7a6420"  # приглушённая жёлтая рамка
ACCENT_HOVER = "#ffdb5c"  # Play при наведении
ACCENT_PRESSED = "#e6b91f"  # Play нажата

NOTE = BRAND_YELLOW   # комментарии: точки на таймлайне, номера, счётчик — тоже жёлтые
ON_NOTE = BRAND_BLACK
CACHE = "#4fae6e"     # кэш на таймлайне
ERROR = "#ff5a5a"

H = 28        # высота основных контролов
HS = 20       # вторичные контролы (~30% меньше)
HP = 38       # главная кнопка (Play) — самый большой круг
FONT_PX_S = 11
ICON_S = 12
RADIUS = H // 2      # всё — капсулы и круги (мотив «O» из логотипа)
RADIUS_S = HS // 2
RADIUS_CARD = 14      # крупные блоки: карточки, плашки, поля текста
FONT_PX = 13
ICON = 16

_ASSETS = os.path.join(tempfile.gettempdir(), "moooov_player_ui")


def _asset(name, svg):
    os.makedirs(_ASSETS, exist_ok=True)
    p = os.path.join(_ASSETS, name)
    if not os.path.exists(p) or open(p).read() != svg:
        with open(p, "w") as f:
            f.write(svg)
    return p.replace("\\", "/")


def stylesheet():
    chev = _asset("chevron.svg", f'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" viewBox="0 0 10 10">'
                                 f'<path d="M2 3.5 L5 6.5 L8 3.5" fill="none" stroke="{DIM}" stroke-width="1.6" '
                                 f'stroke-linecap="round" stroke-linejoin="round"/></svg>')
    chev_up = _asset("chevron_up.svg", f'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" viewBox="0 0 10 10">'
                                       f'<path d="M2 6.5 L5 3.5 L8 6.5" fill="none" stroke="{DIM}" stroke-width="1.6" '
                                       f'stroke-linecap="round" stroke-linejoin="round"/></svg>')
    check = _asset("check.svg", '<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12">'
                                '<path d="M2.5 6.2 L5 8.6 L9.6 3.4" fill="none" stroke="#000000" stroke-width="2" '
                                'stroke-linecap="round" stroke-linejoin="round"/></svg>')
    inner = H - 2  # минус рамка
    # кружок галочки по диаметру равен высоте соседних кнопок (рамка 1 + отступ внутри)
    H_IND, R_IND = H - 2 - 10, H // 2
    HS_IND, RS_IND = HS - 2 - 6, HS // 2
    return f"""
* {{ font-size: {FONT_PX}px; }}
QToolTip {{ background: {CONTROL}; color: {TEXT}; border: 1px solid {BORDER}; border-radius: 10px; padding: 4px 8px; }}
QLabel {{ background: transparent; }}

QPushButton {{
    background: {CONTROL}; color: {TEXT}; border: 1px solid {BORDER}; border-radius: {RADIUS}px;
    min-height: {inner}px; max-height: {inner}px; padding: 0 14px;
}}
QPushButton:hover {{ background: {HOVER}; }}
QPushButton:pressed {{ background: {SURFACE}; }}
QPushButton:checked {{ background: {ACCENT_TINT}; border-color: {ACCENT}; }}
QPushButton[iconOnly="true"] {{ min-width: {inner}px; max-width: {inner}px; padding: 0; }}  /* круг */

QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit {{
    background: {CONTROL}; color: {TEXT}; border: 1px solid {BORDER}; border-radius: {RADIUS}px;
    min-height: {inner}px; max-height: {inner}px; padding: 0 12px;
    selection-background-color: {ACCENT_TINT};
}}
QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover, QLineEdit:hover {{ border-color: {BORDER_HI}; }}
QSpinBox:focus, QDoubleSpinBox:focus, QLineEdit:focus {{ border-color: {ACCENT}; }}
QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{ width: 0px; border: none; subcontrol-origin: border; }}
QAbstractSpinBox::up-arrow, QAbstractSpinBox::down-arrow {{ width: 0px; height: 0px; }}
QAbstractSpinBox[steppers="true"] {{ padding-right: 22px; }}
QAbstractSpinBox[steppers="true"]::up-button, QAbstractSpinBox[steppers="true"]::down-button {{
    subcontrol-origin: border; width: 20px; height: {H // 2 - 1}px; border: none; background: transparent;
}}
QAbstractSpinBox[steppers="true"]::up-button {{ subcontrol-position: top right; border-top-right-radius: {RADIUS - 1}px; margin-top: 1px; margin-right: 1px; }}
QAbstractSpinBox[steppers="true"]::down-button {{ subcontrol-position: bottom right; border-bottom-right-radius: {RADIUS - 1}px; margin-bottom: 1px; margin-right: 1px; }}
QAbstractSpinBox[steppers="true"]::up-button:hover, QAbstractSpinBox[steppers="true"]::down-button:hover {{ background: {HOVER}; }}
QAbstractSpinBox[steppers="true"]::up-arrow {{ image: url({chev_up}); width: 9px; height: 9px; }}
QAbstractSpinBox[steppers="true"]::down-arrow {{ image: url({chev}); width: 9px; height: 9px; }}
QComboBox {{ padding-right: 28px; combobox-popup: 0; }}
QComboBox::drop-down {{ subcontrol-origin: padding; subcontrol-position: center right; width: 24px; border: none; }}
QComboBox::down-arrow {{ image: url({chev}); width: 10px; height: 10px; }}
QComboBox QAbstractItemView {{
    background: {CONTROL}; color: {TEXT}; border: 1px solid {BORDER}; border-radius: 12px; padding: 4px; outline: 0;
    selection-background-color: {ACCENT_TINT}; selection-color: {TEXT};
}}
QComboBox QAbstractItemView::item {{ min-height: 24px; padding: 0 {ITEM_PAD}px; border-radius: 12px; }}
QComboBoxPrivateContainer {{ background: transparent; border: none; padding: 0; margin: 0; }}

QCheckBox {{ spacing: 7px; color: {TEXT}; min-height: {H}px; }}
QCheckBox::indicator {{ width: {H_IND}px; height: {H_IND}px; padding: 5px; border: 1px solid {BORDER}; border-radius: {R_IND}px; background: {CONTROL}; }}
QCheckBox::indicator:hover {{ border-color: {BORDER_HI}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; image: url({check}); }}

QTextEdit, QPlainTextEdit {{ background: {CONTROL}; color: {TEXT}; border: 1px solid {BORDER}; border-radius: {RADIUS_CARD}px; }}

QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 8px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {BORDER_HI}; border-radius: 2px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{ height: 0; background: none; }}

QSlider::groove:horizontal {{ height: 4px; background: {BORDER_HI}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
QSlider::handle:horizontal {{ width: 14px; height: 14px; margin: -5px 0; border-radius: 7px; background: {TEXT}; }}
QSlider::handle:horizontal:hover {{ background: {ACCENT}; }}

QMenuBar {{ background: {BG}; }}
QMenu {{ background: {CONTROL}; border: 1px solid {BORDER}; border-radius: 12px; padding: 6px; }}
QMenu::item {{ padding: 5px 18px; border-radius: 11px; }}
QMenu::item:selected {{ background: {ACCENT_TINT}; }}

/* вторичные контролы: на ~30% меньше основных */
*[small="true"] {{ font-size: {FONT_PX_S}px; }}
QPushButton[small="true"], QComboBox[small="true"], QAbstractSpinBox[small="true"] {{
    min-height: {HS - 2}px; max-height: {HS - 2}px; border-radius: {RADIUS_S}px;
}}
QPushButton[small="true"][iconOnly="true"] {{ min-width: {HS - 2}px; max-width: {HS - 2}px; }}
QComboBox[small="true"] {{ padding: 0 2px 0 9px; }}
QComboBox[small="true"]::drop-down {{ width: 15px; }}
QComboBox[small="true"]::down-arrow {{ width: 8px; height: 8px; }}
QAbstractSpinBox[small="true"] {{ padding: 0 8px; }}
QAbstractSpinBox[small="true"][steppers="true"] {{ padding-right: 17px; }}
QAbstractSpinBox[small="true"][steppers="true"]::up-button,
QAbstractSpinBox[small="true"][steppers="true"]::down-button {{ width: 15px; height: {HS // 2 - 1}px; }}
QAbstractSpinBox[small="true"][steppers="true"]::up-button {{ border-top-right-radius: {RADIUS_S - 1}px; }}
QAbstractSpinBox[small="true"][steppers="true"]::down-button {{ border-bottom-right-radius: {RADIUS_S - 1}px; }}
QAbstractSpinBox[small="true"][steppers="true"]::up-arrow,
QAbstractSpinBox[small="true"][steppers="true"]::down-arrow {{ width: 7px; height: 7px; }}
QCheckBox[small="true"] {{ min-height: {HS}px; spacing: 5px; }}
QCheckBox[small="true"]::indicator {{ width: {HS_IND}px; height: {HS_IND}px; padding: 3px; border-radius: {RS_IND}px; }}
QPushButton[primary="true"] {{
    min-width: {HP - 2}px; max-width: {HP - 2}px; min-height: {HP - 2}px; max-height: {HP - 2}px;
    background: {ACCENT}; border-color: {ACCENT}; border-radius: {HP // 2}px; padding: 0;
}}
QPushButton[primary="true"]:hover {{ background: {ACCENT_HOVER}; border-color: {ACCENT_HOVER}; }}
QPushButton[primary="true"]:pressed {{ background: {ACCENT_PRESSED}; }}

#drawBar, #notesPanel, #layersPanel {{ background: {SURFACE}; }}
#frameSpin {{ color: {ACCENT}; font-weight: 600; padding: 0; }}  /* без отступа справа — число ровно по центру */
#dimLabel {{ color: {DIM}; }}
#sectionTitle {{ color: {DIM}; font-size: 11px; font-weight: 600; letter-spacing: 0.5px; }}
"""


def apply(app):
    if app.property("mooov_theme"):
        return
    app.setProperty("mooov_theme", True)
    app.setStyle("Fusion")
    if sys.platform == "win32":
        # Segoe UI с жёстким хинтингом выглядит рублено (особенно мелкий и повёрнутый текст).
        # Segoe UI Variable (Windows 11) ближе к SF на Маке; без хинтинга — мягче, как на Маке.
        fams = set(QtGui.QFontDatabase.families())
        f = QtGui.QFont(app.font())
        for fam in ("Segoe UI Variable Text", "Segoe UI Variable", "Segoe UI"):
            if fam in fams:
                f.setFamily(fam)
                break
        f.setHintingPreference(QtGui.QFont.PreferNoHinting)
        f.setStyleStrategy(QtGui.QFont.PreferAntialias)
        app.setFont(f)
    pal = QtGui.QPalette()
    for role, c in ((QtGui.QPalette.Window, BG), (QtGui.QPalette.WindowText, TEXT), (QtGui.QPalette.Base, CONTROL),
                    (QtGui.QPalette.AlternateBase, SURFACE), (QtGui.QPalette.Text, TEXT), (QtGui.QPalette.Button, CONTROL),
                    (QtGui.QPalette.ButtonText, TEXT), (QtGui.QPalette.Highlight, ACCENT_TINT),
                    (QtGui.QPalette.HighlightedText, TEXT), (QtGui.QPalette.ToolTipBase, CONTROL),
                    (QtGui.QPalette.ToolTipText, TEXT), (QtGui.QPalette.PlaceholderText, MUTED)):
        pal.setColor(role, QtGui.QColor(c))
    app.setPalette(pal)
    app.setStyleSheet(stylesheet())
    app._popup_fix = _RoundPopups(app)
    app.installEventFilter(app._popup_fix)
    # Windows: Qt по умолчанию «выкатывает» списки и меню. Анимация идёт в стандартной позиции (снизу),
    # и только потом _place ставит список над кнопкой — список медленный и прыгает. Выключаем, как на Маке.
    for fx in (QtCore.Qt.UI_AnimateCombo, QtCore.Qt.UI_AnimateMenu, QtCore.Qt.UI_FadeMenu):
        app.setEffectEnabled(fx, False)


ITEM_PAD = 10  # отступ текста в пунктах выпадающих списков


def warm_popups(root):
    """Окна выпадающих списков создаются при первом открытии — на Windows это ~0.25 с задержки
    на каждом списке. Создаём их заранее, пока плеер простаивает после запуска."""
    for combo in root.findChildren(QtWidgets.QComboBox):
        cont = combo.view().parentWidget()
        if cont is not None:
            cont.ensurePolished()
            cont.winId()


class _RoundPopups(QtCore.QObject):
    """Выпадающие списки и меню скруглены в стиле, но окно под ними прямоугольное — по углам торчит
    серый квадрат. Делаем окно таких попапов прозрачным и без системной рамки/тени."""

    def eventFilter(self, obj, e):
        if e.type() == QtCore.QEvent.Polish and isinstance(obj, QtWidgets.QWidget) and obj.isWindow():
            if isinstance(obj, QtWidgets.QMenu) or obj.metaObject().className() == "QComboBoxPrivateContainer":
                if not obj.testAttribute(QtCore.Qt.WA_TranslucentBackground):
                    obj.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
                    obj.setWindowFlags(obj.windowFlags() | QtCore.Qt.FramelessWindowHint
                                       | QtCore.Qt.NoDropShadowWindowHint)
                    if isinstance(obj, QtWidgets.QFrame):
                        obj.setFrameShape(QtWidgets.QFrame.NoFrame)
        elif (e.type() == QtCore.QEvent.Show and isinstance(obj, QtWidgets.QWidget)
              and obj.metaObject().className() == "QComboBoxPrivateContainer"):
            self._place(obj)
        return False

    POPUP_GAP = 6

    def _place(self, cont):
        """Список — ровно над (или под) кнопкой с одинаковым зазором, текст пунктов — по одной
        вертикали с текстом на кнопке."""
        combo = cont.parentWidget()
        while combo is not None and not isinstance(combo, QtWidgets.QComboBox):
            combo = combo.parentWidget()
        view = cont.findChild(QtWidgets.QAbstractItemView)
        if combo is None or view is None:
            return
        opt = QtWidgets.QStyleOptionComboBox()
        combo.initStyleOption(opt)
        field = combo.style().subControlRect(QtWidgets.QStyle.CC_ComboBox, opt,
                                             QtWidgets.QStyle.SC_ComboBoxEditField, combo)
        inset = view.mapTo(cont, QtCore.QPoint(0, 0)).x() + view.contentsMargins().left() + ITEM_PAD
        g = QtCore.QRect(cont.geometry())
        g.setWidth(max(g.width(), combo.width() + inset - field.x()))
        top_left = combo.mapToGlobal(QtCore.QPoint(field.x() - inset, 0))
        screen = (combo.screen() or QtGui.QGuiApplication.primaryScreen()).availableGeometry()
        below = combo.mapToGlobal(QtCore.QPoint(0, combo.height())).y() + self.POPUP_GAP
        above = top_left.y() - self.POPUP_GAP - g.height()
        y = above if above >= screen.top() or below + g.height() > screen.bottom() else below
        x = max(screen.left(), min(top_left.x(), screen.right() - g.width()))
        g.moveTo(x, max(screen.top(), y))
        cont.setGeometry(g)


def fit_combo(combo):
    """Ширина выпадающего списка — по выбранному значению (а не по самому длинному пункту).
    Раскрытый список — по самому длинному. Пересчитывается при каждой смене значения."""
    combo.ensurePolished()  # шрифт из стиля (обычный / small)
    pad = (9 + 2) if combo.property("small") else (12 + 28)

    def refit(*_):
        fm = combo.fontMetrics()
        w = fm.horizontalAdvance(combo.currentText()) + pad + 19  # +19: рамка и отступ иконки у Qt
        longest = max((fm.horizontalAdvance(combo.itemText(i)) for i in range(combo.count())), default=0)
        combo.setFixedWidth(w)
        combo.view().setMinimumWidth(longest + pad + 35)

    refit()
    if not combo.property("_fit"):
        combo.setProperty("_fit", True)
        combo.currentIndexChanged.connect(refit)


def steppers(spin):
    """Числовое поле со стрелками ▲▼ справа (как у выпадающих списков)."""
    spin.setProperty("steppers", True)
    spin.setButtonSymbols(QtWidgets.QAbstractSpinBox.UpDownArrows)
    spin.style().unpolish(spin)
    spin.style().polish(spin)


def tabular(widget):
    """Цифры одинаковой ширины, чтобы текст не «прыгал» при смене чисел."""
    f = widget.font()
    try:
        f.setFeature(QtGui.QFont.Tag("tnum"), 1)
    except Exception:
        pass
    widget.setFont(f)


def fit_label(label, sample):
    label.ensurePolished()
    label.setFixedWidth(label.fontMetrics().horizontalAdvance(sample) + 6)


def fit_spin(spin, sample):
    """Ширина числового поля по самому длинному ожидаемому значению."""
    spin.ensurePolished()
    sm = bool(spin.property("small"))
    extra = (16 if sm else 22) if spin.property("steppers") else 0
    spin.setFixedWidth(spin.fontMetrics().horizontalAdvance(sample) + (20 if sm else 28) + extra)


def fit_spin_live(spin, sample):
    """Числовое поле без пустоты: текст слева с отступом, стрелки сразу за самым длинным значением (sample).
    Ширина постоянная — поле не прыгает при смене значения."""
    spin.ensurePolished()
    spin.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
    small = bool(spin.property("small"))
    left = 9 if small else 13  # как у остальных полей и списков
    spin.lineEdit().setTextMargins(left, 0, 0, 0)
    arrows = (17 if small else 21) if spin.property("steppers") else left
    spin.setFixedWidth(left + spin.fontMetrics().horizontalAdvance(sample) + 3 + arrows)


def small(*widgets):
    """Вторичный контрол: меньше по высоте, шрифту и иконке."""
    for w in widgets:
        w.setProperty("small", True)
        if isinstance(w, QtWidgets.QPushButton) and not w.icon().isNull():
            w.setIconSize(QtCore.QSize(ICON_S, ICON_S))
        w.style().unpolish(w)
        w.style().polish(w)
        w.ensurePolished()


def surface(widget, name):
    widget.setObjectName(name)
    widget.setAttribute(QtCore.Qt.WA_StyledBackground, True)
    return widget


def icon_button(name, tip, checkable=False, repeat=False):
    b = QtWidgets.QPushButton()
    b.setProperty("iconOnly", True)
    b.setIcon(icon(name))
    b.setIconSize(QtCore.QSize(ICON, ICON))
    b.setToolTip(tip)
    b.setCheckable(checkable)
    b.setFocusPolicy(QtCore.Qt.NoFocus)
    if repeat:
        b.setAutoRepeat(True)
        b.setAutoRepeatDelay(350)
        b.setAutoRepeatInterval(40)
    return b


def text_button(text, tip, icon_name=None):
    b = QtWidgets.QPushButton(text)
    b.setToolTip(tip)
    b.setFocusPolicy(QtCore.Qt.NoFocus)
    if icon_name:
        b.setIcon(icon(icon_name))
        b.setIconSize(QtCore.QSize(ICON - 2, ICON - 2))
    return b


# --------------------------------------------------------------------------- иконки (рисуются кодом, 18×18)


def _draw(p, name):
    P = QtCore.QPointF
    fill = lambda pts: p.drawPolygon(QtGui.QPolygonF([P(*q) for q in pts]))
    bar = lambda x, y0=4.5, y1=13.5: p.drawRoundedRect(QtCore.QRectF(x, y0, 2, y1 - y0), 0.8, 0.8)
    if name == "play":
        fill([(6, 4), (14, 9), (6, 14)])
    elif name == "pause":
        p.drawRoundedRect(QtCore.QRectF(5, 4.5, 3, 9), 1, 1)
        p.drawRoundedRect(QtCore.QRectF(10, 4.5, 3, 9), 1, 1)
    elif name == "prev":  # кадр назад: ◀| — оптически по центру: «вес» (палочка и основание) справа,
        # поэтому группу сдвигаем к острию на 0.7 от геометрического центра
        fill([(10.3, 4.5), (10.3, 13.5), (3.3, 9)])
        bar(11.3)
    elif name == "next":  # кадр вперёд: |▶ — зеркально
        bar(4.7)
        fill([(7.7, 4.5), (7.7, 13.5), (14.7, 9)])
    elif name == "first":  # |◀◀
        bar(2.5)
        fill([(10, 4.5), (10, 13.5), (4.5, 9)])
        fill([(15.5, 4.5), (15.5, 13.5), (10, 9)])
    elif name == "last":  # ▶▶|
        fill([(2.5, 4.5), (2.5, 13.5), (8, 9)])
        fill([(8, 4.5), (8, 13.5), (13.5, 9)])
        bar(13.5)
    elif name == "plus":
        p.drawRoundedRect(QtCore.QRectF(8.2, 3.5, 1.6, 11), 0.8, 0.8)
        p.drawRoundedRect(QtCore.QRectF(3.5, 8.2, 11, 1.6), 0.8, 0.8)
    else:  # контурные
        pen = QtGui.QPen(p.brush().color(), 1.6, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap, QtCore.Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(QtCore.Qt.NoBrush)
        if name == "pen":
            path = QtGui.QPainterPath(P(4, 14))
            path.lineTo(5, 10.5)
            path.lineTo(12, 3.5)
            path.lineTo(14.5, 6)
            path.lineTo(7.5, 13)
            path.closeSubpath()
            p.drawPath(path)
            p.drawLine(P(10.5, 5), P(13, 7.5))
        elif name == "arrow":
            p.drawLine(P(4, 14), P(14, 4))
            p.drawLine(P(14, 4), P(8, 4))
            p.drawLine(P(14, 4), P(14, 10))
        elif name == "rect":
            p.drawRoundedRect(QtCore.QRectF(3, 5, 12, 8), 1, 1)
        elif name == "ellipse":
            p.drawEllipse(QtCore.QRectF(3, 4.5, 12, 9))
        elif name == "undo":
            path = QtGui.QPainterPath(P(5, 7.5))
            path.lineTo(11, 7.5)
            path.cubicTo(P(15, 7.5), P(15, 14), P(11, 14))
            path.lineTo(7, 14)
            p.drawPath(path)
            p.drawLine(P(5, 7.5), P(8, 4.5))
            p.drawLine(P(5, 7.5), P(8, 10.5))
        elif name == "trash":
            p.drawLine(P(3.5, 5.5), P(14.5, 5.5))
            p.drawLine(P(7.5, 5.5), P(7.5, 3.5))
            p.drawLine(P(7.5, 3.5), P(10.5, 3.5))
            p.drawLine(P(10.5, 3.5), P(10.5, 5.5))
            path = QtGui.QPainterPath(P(5, 5.5))
            path.lineTo(5.8, 14.5)
            path.lineTo(12.2, 14.5)
            path.lineTo(13, 5.5)
            p.drawPath(path)
        elif name == "zoom":  # лупа
            p.drawEllipse(QtCore.QRectF(3, 3, 9, 9))
            p.drawLine(P(10.8, 10.8), P(15, 15))
        elif name == "mark_in":  # [▸ — точка входа
            p.drawPolyline(QtGui.QPolygonF([P(7, 3.5), P(4, 3.5), P(4, 14.5), P(7, 14.5)]))
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(pen.color())
            fill([(8.5, 5.5), (8.5, 12.5), (14, 9)])
        elif name == "mark_out":  # ◂] — точка выхода
            p.drawPolyline(QtGui.QPolygonF([P(11, 3.5), P(14, 3.5), P(14, 14.5), P(11, 14.5)]))
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(pen.color())
            fill([(9.5, 5.5), (9.5, 12.5), (4, 9)])
        elif name == "info":
            p.drawEllipse(QtCore.QRectF(2.5, 2.5, 13, 13))
            p.drawLine(P(9, 8.2), P(9, 12.8))
            p.drawPoint(P(9, 5.6))
        elif name == "pdf":
            path = QtGui.QPainterPath(P(5, 3))
            path.lineTo(10.5, 3)
            path.lineTo(13.5, 6)
            path.lineTo(13.5, 15)
            path.lineTo(5, 15)
            path.closeSubpath()
            p.drawPath(path)
            p.drawLine(P(10.5, 3), P(10.5, 6))
            p.drawLine(P(10.5, 6), P(13.5, 6))
            p.drawLine(P(7, 9.5), P(11.5, 9.5))
            p.drawLine(P(7, 12), P(11.5, 12))
        elif name == "chev_left":
            p.drawLine(P(11, 4), P(6, 9))
            p.drawLine(P(6, 9), P(11, 14))
        elif name == "chev_right":
            p.drawLine(P(7, 4), P(12, 9))
            p.drawLine(P(12, 9), P(7, 14))
        elif name == "more":  # ⋯
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(pen.color())
            for x in (4.5, 9, 13.5):
                p.drawEllipse(P(x, 9), 1.4, 1.4)
        elif name == "image":  # референс-картинка: рамка, гора и солнце
            p.drawRoundedRect(QtCore.QRectF(2.5, 3.5, 13, 11), 2, 2)
            path = QtGui.QPainterPath(P(3, 13.5))
            path.lineTo(7.5, 8.5)
            path.lineTo(10.5, 11.5)
            path.lineTo(12, 10)
            path.lineTo(15, 13)
            p.drawPath(path)
            p.drawEllipse(P(11.8, 6.8), 1.3, 1.3)
        elif name in ("eye", "eye_off"):  # видимость слоя
            path = QtGui.QPainterPath(P(2, 9))
            path.quadTo(P(9, 1.5), P(16, 9))
            path.quadTo(P(9, 16.5), P(2, 9))
            p.drawPath(path)
            p.drawEllipse(P(9, 9), 2.3, 2.3)
            if name == "eye_off":
                p.drawLine(P(3.5, 15), P(14.5, 3))
        elif name == "split":  # шторка: круг, левая половина залита
            p.drawEllipse(QtCore.QRectF(2.5, 2.5, 13, 13))
            half = QtGui.QPainterPath(P(9, 2.5))
            half.arcTo(QtCore.QRectF(2.5, 2.5, 13, 13), 90, 180)
            half.closeSubpath()
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(pen.color())
            p.drawPath(half)
        elif name == "chev_up":
            p.drawLine(P(4.5, 11.5), P(9, 7))
            p.drawLine(P(9, 7), P(13.5, 11.5))
        elif name == "chev_down":
            p.drawLine(P(4.5, 6.5), P(9, 11))
            p.drawLine(P(9, 11), P(13.5, 6.5))
        elif name == "close":
            p.drawLine(P(5, 5), P(13, 13))
            p.drawLine(P(13, 5), P(5, 13))
        elif name == "adjust":  # настройки вида слоя: солнце — яркость, прозрачность, режим
            p.drawEllipse(P(9, 9), 3.2, 3.2)
            for a in range(8):
                r = math.radians(a * 45)
                p.drawLine(P(9 + 5.4 * math.cos(r), 9 + 5.4 * math.sin(r)), P(9 + 7 * math.cos(r), 9 + 7 * math.sin(r)))
        elif name == "list":  # плейлист: строки с точками
            for y in (5, 9, 13):
                p.drawLine(P(7, y), P(15, y))
                p.drawPoint(P(3.5, y))


def icon(name, color=TEXT):
    ic = QtGui.QIcon()
    for mode, col in ((QtGui.QIcon.Normal, color), (QtGui.QIcon.Disabled, MUTED)):
        for scale in (1, 2, 3):
            pm = QtGui.QPixmap(18 * scale, 18 * scale)
            pm.fill(QtCore.Qt.transparent)
            pm.setDevicePixelRatio(scale)
            p = QtGui.QPainter(pm)
            p.setRenderHint(QtGui.QPainter.Antialiasing)
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(QtGui.QColor(col))
            _draw(p, name)
            p.end()
            ic.addPixmap(pm, mode)
    return ic


# ============================================================== буква M из логотипа


MARK_LEG = 0.277  # ширина ножки M в долях ширины буквы — это же отступ до треугольника Play в логотипе


def mark_path(rect):
    """Контур буквы M логотипа MOOOV (пропорции 2:1, острые углы, V-вырез) вписанный в rect."""
    pts = [(0, 0), (0.219, 0), (0.5, 0.409), (0.781, 0), (1, 0), (1, 1), (0.723, 1), (0.723, 0.591),
           (0.5, 0.903), (0.277, 0.591), (0.277, 1), (0, 1)]
    w = min(rect.width(), rect.height() * 2.0)
    h = w / 2.0
    x0, y0 = rect.center().x() - w / 2, rect.center().y() - h / 2
    path = QtGui.QPainterPath()
    path.addPolygon(QtGui.QPolygonF([QtCore.QPointF(x0 + x * w, y0 + y * h) for x, y in pts]))
    path.closeSubpath()
    return path


def app_icon():
    """Иконка приложения: жёлтый скруглённый квадрат с чёрной M."""
    ic = QtGui.QIcon()
    for size in (16, 32, 64, 128, 256, 512):
        pm = QtGui.QPixmap(size, size)
        pm.fill(QtCore.Qt.transparent)
        p = QtGui.QPainter(pm)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        m = size * 0.08
        r = QtCore.QRectF(m, m, size - 2 * m, size - 2 * m)
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(QtGui.QColor(BRAND_YELLOW))
        p.drawRoundedRect(r, size * 0.2, size * 0.2)
        p.fillPath(mark_path(r.adjusted(size * 0.2, size * 0.2, -size * 0.2, -size * 0.2)), QtGui.QColor(BRAND_BLACK))
        p.end()
        ic.addPixmap(pm)
    return ic
