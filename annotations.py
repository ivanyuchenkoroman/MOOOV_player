"""Рисунки и комментарии поверх кадров: модель, хранение (JSON рядом с медиа), отрисовка, экспорт PDF."""
import base64
import json
import math
import os
import time
import uuid
from dataclasses import asdict, dataclass, field

from PySide6 import QtCore, QtGui

import ui_theme as ui

COLORS = ["#ff3d6e", "#ffd02b", "#3ddc84", "#33c3ff", "#ffffff", "#111111"]
BADGE = QtGui.QColor(ui.NOTE)


@dataclass
class Stroke:
    tool: str  # pen | arrow | rect | ellipse | image
    color: str
    width: float  # доля высоты кадра
    pts: list  # [[x, y], ...] в долях ширины/высоты кадра; у image — два угла [лево-верх, право-низ]
    data: str = ""  # image: сама картинка (PNG/JPG в base64), хранится прямо в .mooov.json
    alpha: float = 1.0  # непрозрачность штриха / картинки (0.25 … 1)


REF_MAX = 1600  # длинная сторона референса в комментарии — хватает для экрана и PDF, JSON не раздувается
_IMG_CACHE = {}


def image_stroke(img, center, frame_size, height=0.4):
    """Картинка-референс как штрих: ужата до REF_MAX, по центру center (доли кадра), высотой height кадра."""
    if max(img.width(), img.height()) > REF_MAX:
        img = img.scaled(REF_MAX, REF_MAX, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
    ba = QtCore.QByteArray()
    buf = QtCore.QBuffer(ba)
    buf.open(QtCore.QIODevice.WriteOnly)
    img.save(buf, "PNG" if img.hasAlphaChannel() else "JPG", -1 if img.hasAlphaChannel() else 90)
    fw, fh = frame_size
    h = height
    w = h * (img.width() / img.height()) * fh / fw
    if w > 0.9:  # очень широкая картинка — вписываем по ширине
        h, w = h * 0.9 / w, 0.9
    cx = min(max(center[0], w / 2), 1 - w / 2)
    cy = min(max(center[1], h / 2), 1 - h / 2)
    return Stroke("image", "#ffffff", 0.0, [[cx - w / 2, cy - h / 2], [cx + w / 2, cy + h / 2]],
                  base64.b64encode(bytes(ba)).decode("ascii"))


def stroke_image(s):
    img = _IMG_CACHE.get(s.data)
    if img is None:
        img = QtGui.QImage.fromData(base64.b64decode(s.data)) if s.data else QtGui.QImage()
        if len(_IMG_CACHE) > 64:
            _IMG_CACHE.clear()
        _IMG_CACHE[s.data] = img
    return img


def image_rect(s, rect):
    a, b = _map(rect, s.pts[0]), _map(rect, s.pts[-1])
    return QtCore.QRectF(a, b).normalized()


@dataclass
class Note:
    frame: int  # индекс кадра в клипе
    number: int  # номер кадра (из имени файла / 1-based для видео)
    text: str = ""
    author: str = ""
    strokes: list = field(default_factory=list)
    created: float = field(default_factory=time.time)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])

    def is_empty(self):
        return not self.strokes and not self.text.strip()


class NoteStore:
    def __init__(self, path):
        self.path = path
        self.notes = []
        if path and os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                for d in data.get("notes", []):
                    d["strokes"] = [Stroke(**s) for s in d.get("strokes", [])]
                    self.notes.append(Note(**d))
            except Exception as e:  # битый файл не должен ронять плеер
                print("MOOOV_player: cannot read", path, e)

    def ordered(self):
        return sorted(self.notes, key=lambda n: (n.frame, n.created))

    def numbered(self):
        return list(enumerate(self.ordered(), 1))

    def near(self, frame, ghost):
        """[(note, opacity, num)] для заметок в пределах ±ghost кадров."""
        out = []
        for num, n in self.numbered():
            d = abs(n.frame - frame)
            if d <= ghost and n.strokes:
                out.append((n, 1.0 - d / (ghost + 1), num))
        out.sort(key=lambda t: t[1])  # ближние рисуем последними (сверху)
        return out

    def frames(self):
        return {n.frame for n in self.notes}

    def save(self):
        if not self.path:
            return
        notes = [n for n in self.notes if not n.is_empty()]
        if not notes and not os.path.exists(self.path):
            return  # не плодим пустые .mooov.json рядом с каждым открытым файлом
        data = {"version": 1, "notes": [asdict(n) for n in notes]}
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)


class CompositeStore(NoteStore):
    """Комментарии монтажа: на таймлайне — общие номера кадров, а хранятся в файле каждого клипа
    (в его собственных кадрах). Так после разборки монтажа у клипа остаются его комментарии."""

    def __init__(self, comp):
        self.path = None
        self.comp = comp
        self.stores = [NoteStore(c.sidecar) for c in comp.clips]
        self.notes = []
        for off, st in zip(comp.offsets, self.stores):
            for n in st.notes:
                n.frame += off
                n.number = n.frame + 1
                self.notes.append(n)

    def save(self):
        from dataclasses import replace
        per = [[] for _ in self.stores]
        for n in self.notes:
            if n.is_empty():
                continue
            k, j = self.comp.locate(n.frame)
            per[k].append(replace(n, frame=j, number=self.comp.clips[k].numbers[j]))
        for st, notes in zip(self.stores, per):
            st.notes = notes
            st.save()


# --------------------------------------------------------------------------- отрисовка


def _map(rect, q):
    return QtCore.QPointF(rect.x() + q[0] * rect.width(), rect.y() + q[1] * rect.height())


def paint_stroke(p, s, rect, opacity=1.0):
    col = QtGui.QColor(s.color)
    col.setAlphaF(max(0.0, min(1.0, opacity * s.alpha)))
    w = max(1.0, s.width * rect.height())
    p.setPen(QtGui.QPen(col, w, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap, QtCore.Qt.RoundJoin))
    p.setBrush(QtCore.Qt.NoBrush)
    pts = [_map(rect, q) for q in s.pts]
    if not pts:
        return
    if s.tool == "image":
        r = image_rect(s, rect)
        p.save()
        p.setOpacity(p.opacity() * max(0.0, min(1.0, opacity * s.alpha)))
        p.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
        img = stroke_image(s)
        if not img.isNull():
            p.drawImage(r, img)
        p.setPen(QtGui.QPen(QtGui.QColor(0, 0, 0, 140), 1))  # тонкая рамка — картинка не сливается с кадром
        p.drawRect(r)
        p.restore()
        return
    if s.tool == "pen":
        if len(pts) == 1:
            p.drawPoint(pts[0])
            return
        path = QtGui.QPainterPath(pts[0])
        for a, b in zip(pts[1:-1], pts[2:]):  # сглаживание через середины отрезков
            path.quadTo(a, (a + b) / 2)
        path.lineTo(pts[-1])
        p.drawPath(path)
        return
    a, b = pts[0], pts[-1]
    if s.tool == "rect":
        p.drawRect(QtCore.QRectF(a, b).normalized())
    elif s.tool == "ellipse":
        p.drawEllipse(QtCore.QRectF(a, b).normalized())
    elif s.tool == "arrow":
        p.drawLine(a, b)
        ang = math.atan2(b.y() - a.y(), b.x() - a.x())
        ln = min(w * 4 + 10, math.hypot(b.x() - a.x(), b.y() - a.y()) * 0.6)
        for da in (math.radians(150), -math.radians(150)):
            p.drawLine(b, b + QtCore.QPointF(math.cos(ang + da) * ln, math.sin(ang + da) * ln))


def paint_badge(p, num, center, r, opacity=1.0, bold=False):
    col = QtGui.QColor(BADGE)
    col.setAlphaF(opacity)
    ring = QtGui.QColor(0, 0, 0, int(170 * opacity))  # тонкое тёмное кольцо — читается и на светлом кадре
    p.setPen(QtGui.QPen(ring, max(1.0, r * 0.12)))
    p.setBrush(col)
    p.drawEllipse(center, r, r)
    f = p.font()
    f.setPixelSize(max(6, int(r * 1.1)))
    f.setBold(bold)
    p.setFont(f)
    tc = QtGui.QColor(ui.ON_NOTE)
    tc.setAlphaF(opacity)
    p.setPen(tc)
    p.drawText(QtCore.QRectF(center.x() - r, center.y() - r, 2 * r, 2 * r), QtCore.Qt.AlignCenter, str(num))


def paint_note(p, note, rect, opacity, num, badge_r=11, badge_op=1.0):
    """Рисует штрихи заметки и номер у начала первого штриха; возвращает центр номера (или None)."""
    p.setRenderHint(QtGui.QPainter.Antialiasing, True)
    for s in note.strokes:
        paint_stroke(p, s, rect, opacity)
    if note.strokes and note.strokes[0].pts and badge_r:
        q = note.strokes[0].pts[0]
        c = _map(rect, q) + QtCore.QPointF(-badge_r * 1.6, -badge_r * 1.6)
        paint_badge(p, num, c, badge_r, opacity * badge_op)
        return c
    return None


def paint_matte(p, t, ratio, alpha):
    """Каше: затемнение вне формата ratio внутри кадра t; при прозрачности — линия по границе."""
    if not ratio or t.height() <= 0:
        return
    ar = t.width() / t.height()
    if abs(ar - ratio) < 1e-3:
        return
    if ar > ratio:  # кадр шире формата — шторки слева/справа
        iw = t.height() * ratio
        inner = QtCore.QRectF(t.center().x() - iw / 2, t.top(), iw, t.height())
    else:  # кадр уже — шторки сверху/снизу
        ih = t.width() / ratio
        inner = QtCore.QRectF(t.left(), t.center().y() - ih / 2, t.width(), ih)
    path = QtGui.QPainterPath()
    path.addRect(t)
    path.addRect(inner)
    path.setFillRule(QtCore.Qt.OddEvenFill)
    p.fillPath(path, QtGui.QColor(0, 0, 0, int(255 * alpha)))
    if alpha < 1:
        pen = QtGui.QPen(QtGui.QColor(255, 255, 255, 110), 1)
        pen.setCosmetic(True)
        p.setPen(pen)
        p.setBrush(QtCore.Qt.NoBrush)
        p.drawRect(inner)


# --------------------------------------------------------------------------- PDF


def paint_logo(p, r, color):
    """Лого приложения: M и треугольник Play справа (как на стартовом экране)."""
    h = r.height()
    p.setRenderHint(QtGui.QPainter.Antialiasing, True)
    p.fillPath(ui.mark_path(QtCore.QRectF(r.left(), r.top(), h * 2, h)), color)
    tx = r.left() + h * 2 + h * 2 * ui.MARK_LEG  # отступ = ширина ножки M
    tri = QtGui.QPainterPath()
    tri.addPolygon(QtGui.QPolygonF([QtCore.QPointF(tx, r.top()), QtCore.QPointF(tx + h * 0.866, r.center().y()),
                                    QtCore.QPointF(tx, r.bottom())]))
    tri.closeSubpath()
    p.fillPath(tri, color)


def created_str(note):
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(note.created))


def export_pdf(path, items, get_image, file_label, timecode, progress=None, matte=None, matte_alpha=1.0,
               file_for=None):
    """items: [(num, note)]; get_image(frame) -> QImage; timecode(frame) -> str.
    Одна страница A4 (альбом) на комментарий: шапка + кадр с рисунком."""
    w = QtGui.QPdfWriter(path)
    w.setTitle(f"Comments — {file_label}")
    w.setCreator("MOOOV_player")
    w.setResolution(150)
    w.setPageLayout(QtGui.QPageLayout(QtGui.QPageSize(QtGui.QPageSize.A4), QtGui.QPageLayout.Landscape,
                                      QtCore.QMarginsF(0, 0, 0, 0)))
    p = QtGui.QPainter(w)
    W, H = w.width(), w.height()
    m = W * 0.025
    bg, fg, dim, muted = (QtGui.QColor(c) for c in (ui.SURFACE, ui.TEXT, ui.DIM, ui.MUTED))

    def font(px, bold=False, weight=None):
        f = QtGui.QFont()
        f.setPixelSize(int(px))
        if weight is not None:
            f.setWeight(weight)
        else:
            f.setBold(bold)
        return f

    cap_h = H * 0.03              # капсула с номером кадра
    D = cap_h                     # кружок с номером — соразмерно лого справа и капсуле
    x0 = m + D + m * 0.4          # колонка текста справа от номера
    tw = W - x0 - m
    for k, (num, note) in enumerate(items):
        if progress and not progress(k):
            break
        if k:
            w.newPage()
        p.fillRect(QtCore.QRectF(0, 0, W, H), bg)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)

        # 1. номер комментария — жёлтый круг слева, вне кадра
        paint_badge(p, num, QtCore.QPointF(m + D / 2, m + cap_h / 2), D / 2, bold=True)

        # 2. строка: капсула «Frame N · таймкод» + тихие автор / дата / файл
        cy = m  # капсула, кружок и лого на одной линии
        fcap = font(cap_h * 0.5, weight=QtGui.QFont.DemiBold)
        fsub = font(cap_h * 0.47)
        fmc, fms = QtGui.QFontMetricsF(fcap), QtGui.QFontMetricsF(fsub)
        t1, t2 = f"Frame {note.number}", timecode(note.frame)
        gap = cap_h * 0.45
        cw = cap_h * 0.6 + fmc.horizontalAdvance(t1) + gap + fms.horizontalAdvance(t2) + cap_h * 0.6
        cap = QtCore.QRectF(x0, cy, cw, cap_h)
        p.setPen(QtGui.QPen(QtGui.QColor(ui.BORDER), max(1, H * 0.0012)))
        p.setBrush(QtGui.QColor(ui.CONTROL))
        p.drawRoundedRect(cap, cap_h / 2, cap_h / 2)
        p.setFont(fcap)
        p.setPen(fg)
        r = QtCore.QRectF(cap.left() + cap_h * 0.6, cap.top(), cw, cap_h)
        p.drawText(r, QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, t1)
        p.setFont(fsub)
        p.setPen(dim)
        r.setLeft(r.left() + fmc.horizontalAdvance(t1) + gap)
        p.drawText(r, QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, t2)
        fname = file_for(note.frame) if file_for else file_label
        meta = "   ·   ".join(x for x in (note.author, created_str(note), fname) if x)
        # лого приложения (M + Play) справа вверху, по линии капсулы
        lh = cap_h * 0.7
        lw = lh * 2 + lh * 2 * ui.MARK_LEG + lh * 0.866
        paint_logo(p, QtCore.QRectF(W - m - lw, cap.center().y() - lh / 2, lw, lh), QtGui.QColor(ui.BRAND_YELLOW))
        mr = QtCore.QRectF(cap.right() + cap_h * 0.6, cap.top(), W - m - lw - cap_h * 1.2 - cap.right(), cap_h)
        p.setPen(dim)
        p.drawText(mr, QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter,
                   fms.elidedText(meta, QtCore.Qt.ElideMiddle, mr.width()))

        # 3. сам комментарий — самый крупный и яркий
        text = note.text.strip()
        ty = cap.bottom() + H * 0.018
        maxh = H * 0.26
        px = H * 0.034
        while True:
            ft = font(px, weight=QtGui.QFont.Medium)
            tr = QtGui.QFontMetricsF(ft).boundingRect(QtCore.QRectF(0, 0, tw, 10 * H), QtCore.Qt.TextWordWrap,
                                                      text or "Drawing, no text")
            if tr.height() <= maxh or px <= H * 0.016:
                break
            px *= 0.9
        p.setFont(ft)
        p.setPen(fg if text else muted)
        rt = QtCore.QRectF(x0, ty, tw, min(tr.height(), maxh) + 2)
        p.drawText(rt, QtCore.Qt.TextWordWrap, text or "Drawing, no text")
        bottom = max(rt.bottom(), m + D)

        # кадр со скруглёнными углами, рисунок без номера (номер уже в шапке)
        top = bottom + H * 0.035
        area = QtCore.QRectF(x0, top, tw, H - top - m)  # кадр по левому краю комментария
        img = get_image(note.frame)
        if img is not None and not img.isNull():
            sc = min(area.width() / img.width(), area.height() / img.height())
            iw, ih = img.width() * sc, img.height() * sc
            rect = QtCore.QRectF(area.left(), area.top(), iw, ih)
            clip = QtGui.QPainterPath()
            rad = H * 0.012
            clip.addRoundedRect(rect, rad, rad)
            p.setClipPath(clip)
            p.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
            p.drawImage(rect, img)
            paint_matte(p, rect, matte, matte_alpha)
            paint_note(p, note, rect, 1.0, num, badge_r=0)
            p.setClipping(False)
    p.end()
