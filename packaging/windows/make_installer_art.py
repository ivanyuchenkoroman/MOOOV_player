"""Картинки установщика Windows (Inno Setup) в стиле плеера: тёмный фон, жёлтый лого M▶.

    uv run python packaging/windows/make_installer_art.py

wizard_large.png — слева на первой и последней странице (164×314 при 100%, рисуем ×2);
wizard_small.png — справа вверху на остальных страницах (55×55, рисуем ×2).
"""
import os

from PySide6 import QtCore, QtGui, QtSvg

HERE = os.path.dirname(os.path.abspath(__file__))
LOGO = os.path.join(HERE, "..", "..", "design-system", "brand", "logo-yellow.svg")
BG, TEXT, DIM = "#121212", "#f0f0f0", "#8c8c8c"


def font(px, weight=QtGui.QFont.Normal):
    f = QtGui.QFont("Segoe UI")
    f.setPixelSize(px)
    f.setWeight(weight)
    return f


def logo(p, rect):
    """Лого по центру rect с сохранением пропорций 342:100."""
    r = QtSvg.QSvgRenderer(LOGO)
    w = min(rect.width(), rect.height() * 3.42)
    h = w / 3.42
    r.render(p, QtCore.QRectF(rect.center().x() - w / 2, rect.center().y() - h / 2, w, h))


def large():
    W, H = 328, 628
    img = QtGui.QImage(W, H, QtGui.QImage.Format_ARGB32)
    img.fill(QtGui.QColor(BG))
    p = QtGui.QPainter(img)
    p.setRenderHints(QtGui.QPainter.Antialiasing | QtGui.QPainter.TextAntialiasing)
    logo(p, QtCore.QRectF(64, 200, W - 128, 80))
    p.setPen(QtGui.QColor(TEXT))
    p.setFont(font(30, QtGui.QFont.DemiBold))
    p.drawText(QtCore.QRectF(0, 300, W, 44), QtCore.Qt.AlignCenter, "MOOOV_player")
    p.setPen(QtGui.QColor(DIM))
    p.setFont(font(19))
    p.drawText(QtCore.QRectF(0, 344, W, 30), QtCore.Qt.AlignCenter, "VFX review player")
    p.setFont(font(18))
    p.drawText(QtCore.QRectF(0, H - 64, W, 30), QtCore.Qt.AlignCenter, "mooov.team")
    p.end()
    img.save(os.path.join(HERE, "wizard_large.png"))


def small():
    S = 110
    img = QtGui.QImage(S, S, QtGui.QImage.Format_ARGB32)
    img.fill(QtCore.Qt.transparent)
    p = QtGui.QPainter(img)
    p.setRenderHint(QtGui.QPainter.Antialiasing)
    logo(p, QtCore.QRectF(4, 4, S - 8, S - 8))
    p.end()
    img.save(os.path.join(HERE, "wizard_small.png"))


if __name__ == "__main__":
    app = QtGui.QGuiApplication([])
    large()
    small()
    print("wizard_large.png, wizard_small.png")
