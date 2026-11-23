"""Картинка для превью ссылок (GitHub social preview, мессенджеры): 1280×640, стиль плеера.

    uv run python packaging/make_social_preview.py   →  docs/social-preview.png
"""
import os

from PySide6 import QtCore, QtGui, QtSvg

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
LOGO = os.path.join(ROOT, "design-system", "brand", "logo-yellow.svg")
SHOT = os.path.join(ROOT, "docs", "player.png")
BG, TEXT, DIM, YELLOW = "#121212", "#f0f0f0", "#8c8c8c", "#FFD02B"


def font(px, weight=QtGui.QFont.Normal):
    f = QtGui.QFont("Segoe UI")
    f.setPixelSize(px)
    f.setWeight(weight)
    return f


def main():
    W, H = 1280, 640
    img = QtGui.QImage(W, H, QtGui.QImage.Format_RGB32)
    img.fill(QtGui.QColor(BG))
    p = QtGui.QPainter(img)
    p.setRenderHints(QtGui.QPainter.Antialiasing | QtGui.QPainter.SmoothPixmapTransform | QtGui.QPainter.TextAntialiasing)
    # справа — окно плеера, уходит за край и тает к левому краю
    shot = QtGui.QImage(SHOT)
    if not shot.isNull():
        cut = int(shot.width() * 0.0175)  # светлый заголовок окна Windows — срезаем
        shot = shot.copy(0, cut, shot.width(), shot.height() - cut)
        sw = 900
        sh = shot.height() * sw / shot.width()
        r = QtCore.QRectF(640, (H - sh) / 2 + 40, sw, sh)
        path = QtGui.QPainterPath()
        path.addRoundedRect(r, 14, 14)
        p.save()
        p.setClipPath(path)
        p.drawImage(r, shot)
        p.restore()
        p.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 28), 1.5))
        p.drawPath(path)
        g = QtGui.QLinearGradient(600, 0, 940, 0)
        g.setColorAt(0, QtGui.QColor(BG))
        c = QtGui.QColor(BG)
        c.setAlpha(0)
        g.setColorAt(1, c)
        p.fillRect(QtCore.QRectF(600, 0, 340, H), g)
    # слева — лого, название, подпись
    x = 84
    QtSvg.QSvgRenderer(LOGO).render(p, QtCore.QRectF(x, 170, 205, 60))
    p.setPen(QtGui.QColor(TEXT))
    p.setFont(font(76, QtGui.QFont.DemiBold))
    p.drawText(QtCore.QRectF(x - 4, 250, 700, 100), QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, "MOOOV_player")
    p.setPen(QtGui.QColor(DIM))
    p.setFont(font(30))
    p.drawText(QtCore.QRectF(x, 352, 640, 44), QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, "Fast VFX review player")
    # плашка «Mac · Windows» и сайт
    p.setFont(font(24, QtGui.QFont.DemiBold))
    t = "Mac  ·  Windows"
    tw = QtGui.QFontMetricsF(p.font()).horizontalAdvance(t)
    pill = QtCore.QRectF(x, 430, tw + 44, 50)
    p.setPen(QtCore.Qt.NoPen)
    p.setBrush(QtGui.QColor(YELLOW))
    p.drawRoundedRect(pill, 25, 25)
    p.setPen(QtGui.QColor(BG))
    p.drawText(pill, QtCore.Qt.AlignCenter, t)
    p.setPen(QtGui.QColor(DIM))
    p.setFont(font(24))
    p.drawText(QtCore.QRectF(x, 560, 500, 40), QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, "mooov.team")
    p.end()
    out = os.path.join(ROOT, "docs", "social-preview.png")
    img.save(out)
    print(out)


if __name__ == "__main__":
    app = QtGui.QGuiApplication([])
    main()
