"""Иконки приложения из дизайн-системы: packaging/MOOOV_player.icns (Mac) и .ico (Windows)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from PySide6 import QtCore, QtGui  # noqa: E402

import ui_theme as ui  # noqa: E402

app = QtGui.QGuiApplication([])
here = os.path.dirname(os.path.abspath(__file__))
pm = ui.app_icon().pixmap(1024, 1024)
for ext, size in (("icns", 1024), ("ico", 256), ("png", 1024)):
    img = pm.toImage().scaled(size, size, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
    path = os.path.join(here, f"MOOOV_player.{ext}")
    ok = img.save(path, ext.upper())
    print(path, "ok" if ok else "FAILED")
