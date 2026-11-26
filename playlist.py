"""Плейлисты: списки сборок слева, чтобы смотреть всё в одном плеере и не копаться по папкам.

Хранятся в playlists.json в папке данных приложения. Каждая строка — путь к видео,
кадру секвенции или папке. Превью и счётчики комментариев считаются в фоне
(превью кэшируются на диске)."""
import hashlib
import json
import os
import sys

from PySide6 import QtCore, QtGui, QtWidgets

import ui_theme as ui


def data_dir():
    d = os.environ.get("MOOOV_DATA_DIR") or QtCore.QStandardPaths.writableLocation(
        QtCore.QStandardPaths.AppDataLocation)
    os.makedirs(d, exist_ok=True)
    return d


def thumbs_dir():
    d = os.environ.get("MOOOV_DATA_DIR")
    d = os.path.join(d, "thumbs") if d else os.path.join(
        QtCore.QStandardPaths.writableLocation(QtCore.QStandardPaths.CacheLocation), "thumbs")
    os.makedirs(d, exist_ok=True)
    return d


def norm(p):
    return os.path.normcase(os.path.abspath(p))


class Playlists:
    """{"current": k, "lists": [{"name": str, "items": [path, ...]}]}"""

    def __init__(self):
        self.path = os.path.join(data_dir(), "playlists.json")
        self.lists = []
        self.current = 0
        try:
            with open(self.path, encoding="utf-8") as f:
                d = json.load(f)
            self.lists = [{"name": str(x.get("name") or "Playlist"), "items": [str(p) for p in x.get("items", [])]}
                          for x in d.get("lists", [])]
            self.current = int(d.get("current", 0))
        except (OSError, ValueError, TypeError, AttributeError):
            pass
        if not self.lists:
            self.lists = [{"name": "Playlist 1", "items": []}]
        self.current = max(0, min(self.current, len(self.lists) - 1))

    @property
    def items(self):
        return self.lists[self.current]["items"]

    @property
    def name(self):
        return self.lists[self.current]["name"]

    def save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"current": self.current, "lists": self.lists}, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)

    def new(self, name):
        self.lists.append({"name": name, "items": []})
        self.current = len(self.lists) - 1

    def free_name(self):
        names = {x["name"] for x in self.lists}
        k = len(self.lists) + 1
        while f"Playlist {k}" in names:
            k += 1
        return f"Playlist {k}"


# --------------------------------------------------------------------------- фон: превью + сведения


class _Bridge(QtCore.QObject):
    done = QtCore.Signal(str, object, object)  # путь, QImage|None, meta


class _Job(QtCore.QRunnable):
    def __init__(self, path, probe, bridge):
        super().__init__()
        self.path, self.probe, self.bridge = path, probe, bridge

    def run(self):
        try:
            img, meta = self.probe(self.path)
        except Exception as e:  # битый файл не должен ронять плеер
            img, meta = None, {"error": str(e) or "cannot open"}
        self.bridge.done.emit(self.path, img, meta)


def thumb_key(path):
    try:
        st = os.stat(path)
        sig = f"{norm(path)}|{st.st_mtime_ns}|{st.st_size}"
    except OSError:
        sig = norm(path)
    return os.path.join(thumbs_dir(), hashlib.sha1(sig.encode("utf-8")).hexdigest() + ".jpg")


# --------------------------------------------------------------------------- список


ROW_H = 62
THUMB = QtCore.QSize(88, 50)
PATH_ROLE = QtCore.Qt.UserRole


class RowDelegate(QtWidgets.QStyledItemDelegate):
    def __init__(self, panel):
        super().__init__(panel)
        self.panel = panel

    def sizeHint(self, opt, idx):
        return QtCore.QSize(opt.rect.width(), ROW_H)

    def paint(self, p, opt, idx):
        path = idx.data(PATH_ROLE)
        meta = self.panel.meta.get(path, {})
        row = idx.row()
        r = QtCore.QRectF(opt.rect).adjusted(6, 2, -6, -2)
        cur = self.panel.is_current(path)
        hover = bool(opt.state & QtWidgets.QStyle.State_MouseOver)
        p.save()
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        if cur:
            p.setPen(QtGui.QPen(QtGui.QColor(ui.ACCENT_LINE), 1))
            p.setBrush(QtGui.QColor(ui.ACCENT_TINT))
            p.drawRoundedRect(r, 12, 12)
        elif hover:
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(QtGui.QColor(ui.CONTROL))
            p.drawRoundedRect(r, 12, 12)

        f = QtGui.QFont(opt.font)
        # номер в списке
        f.setPixelSize(12)
        f.setBold(cur)
        p.setFont(f)
        p.setPen(QtGui.QColor(ui.ACCENT if cur else ui.DIM))
        nr = QtCore.QRectF(r.left(), r.top(), 26, r.height())
        p.drawText(nr, QtCore.Qt.AlignCenter, str(row + 1))

        # превью со скруглёнными углами
        tr = QtCore.QRectF(nr.right() + 2, r.center().y() - THUMB.height() / 2, THUMB.width(), THUMB.height())
        clip = QtGui.QPainterPath()
        clip.addRoundedRect(tr, 7, 7)
        p.setClipPath(clip)
        p.fillRect(tr, QtGui.QColor(ui.SURFACE))
        img = self.panel.thumbs.get(path)
        if img is not None:
            s = max(tr.width() / img.width(), tr.height() / img.height())  # заполнить, лишнее обрезать
            iw, ih = img.width() * s, img.height() * s
            p.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
            p.drawImage(QtCore.QRectF(tr.center().x() - iw / 2, tr.center().y() - ih / 2, iw, ih), img)
        elif meta.get("error"):
            p.setPen(QtGui.QColor(ui.ERROR))
            p.drawText(tr, QtCore.Qt.AlignCenter, "!")
        p.setClipping(False)

        # имя и вторая строка
        x = tr.right() + 10
        badge_w = 0
        count = meta.get("comments", 0)
        if count:
            fb = QtGui.QFont(opt.font)
            fb.setPixelSize(10)
            fb.setBold(True)
            txt = str(count)
            bw = max(18.0, QtGui.QFontMetricsF(fb).horizontalAdvance(txt) + 10)
            br = QtCore.QRectF(r.right() - 8 - bw, r.top() + 9, bw, 16)
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(QtGui.QColor(ui.NOTE))
            p.drawRoundedRect(br, 8, 8)
            p.setFont(fb)
            p.setPen(QtGui.QColor(ui.ON_NOTE))
            p.drawText(br, QtCore.Qt.AlignCenter, txt)
            badge_w = bw + 8
        tw = r.right() - 8 - badge_w - x
        f.setPixelSize(13)
        f.setBold(False)
        p.setFont(f)
        fm = QtGui.QFontMetricsF(f)
        name = meta.get("name") or os.path.basename(path.rstrip("/\\")) or path
        p.setPen(QtGui.QColor(ui.TEXT))
        p.drawText(QtCore.QRectF(x, r.top() + 8, tw, 18), QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter,
                   fm.elidedText(name, QtCore.Qt.ElideMiddle, tw))
        f.setPixelSize(11)
        p.setFont(f)
        fm2 = QtGui.QFontMetricsF(f)
        if meta.get("error"):
            sub, col = "File not found" if not os.path.exists(path) else "Cannot open", ui.ERROR
        else:
            sub, col = meta.get("sub", ""), ui.DIM
        p.setPen(QtGui.QColor(col))
        p.drawText(QtCore.QRectF(x, r.top() + 28, r.right() - 8 - x, 16), QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter,
                   fm2.elidedText(sub, QtCore.Qt.ElideRight, r.right() - 8 - x))
        p.restore()


class PlaylistView(QtWidgets.QListWidget):
    files_dropped = QtCore.Signal(list, int)  # пути, позиция вставки

    def __init__(self):
        super().__init__()
        self.setDragDropMode(QtWidgets.QAbstractItemView.InternalMove)
        self.setDefaultDropAction(QtCore.Qt.MoveAction)
        self.setDropIndicatorShown(True)
        self.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.setFocusPolicy(QtCore.Qt.NoFocus)  # стрелки и пробел остаются плееру
        self.setMouseTracking(True)
        self.setUniformItemSizes(True)
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollMode(QtWidgets.QAbstractItemView.ScrollPerPixel)
        self.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.setStyleSheet(f"QListWidget {{ background: transparent; border: none; outline: none; }}"
                           f" QListWidget::item {{ border: none; }}"
                           f" QListWidget::item:selected {{ background: transparent; }}")

    @staticmethod
    def _urls(e):
        md = e.mimeData()
        return [u.toLocalFile() for u in md.urls() if u.isLocalFile()] if md.hasUrls() else []

    def _row_at(self, pos):
        it = self.itemAt(pos)
        if it is None:
            return self.count()
        r = self.visualItemRect(it)
        return self.row(it) + (1 if pos.y() > r.center().y() else 0)

    def mimeData(self, items):
        """Строку можно утащить из списка: на таймлайн (слоем или в монтаж) или на окно плеера."""
        md = super().mimeData(items)
        md.setUrls([QtCore.QUrl.fromLocalFile(it.data(PATH_ROLE)) for it in items if it.data(PATH_ROLE)])
        return md

    def startDrag(self, actions):
        # копирование разрешено: брошенная на плеер строка остаётся в плейлисте
        super().startDrag(actions | QtCore.Qt.CopyAction)

    def dragEnterEvent(self, e):
        if e.source() is not self and self._urls(e):
            e.acceptProposedAction()
            return
        super().dragEnterEvent(e)

    def dragMoveEvent(self, e):
        if e.source() is not self and self._urls(e):
            e.acceptProposedAction()
            return
        super().dragMoveEvent(e)

    def dropEvent(self, e):
        if e.source() is not self and self._urls(e):
            e.acceptProposedAction()
            self.files_dropped.emit(self._urls(e), self._row_at(e.position().toPoint()))
            return
        super().dropEvent(e)


class PlaylistPanel(QtWidgets.QFrame):
    """Левая панель: выбор плейлиста, поиск, ‹ › по сборкам, список с превью."""
    open_requested = QtCore.Signal(str)
    timeline_requested = QtCore.Signal(list)  # открыть все как монтаж
    add_to_timeline = QtCore.Signal(str)      # дописать в конец текущего таймлайна
    add_files_requested = QtCore.Signal()
    add_current_requested = QtCore.Signal()

    def __init__(self, probe):
        super().__init__()
        self.setObjectName("playlistPanel")
        self.setFixedWidth(320)
        self.setStyleSheet(f"#playlistPanel {{ background: {ui.SURFACE}; border-right: 1px solid {ui.BORDER}; }}")
        self.store = Playlists()
        self.probe = probe
        self.meta, self.thumbs = {}, {}
        self.pending = set()
        self.current_path = None
        self.current_sidecar = None
        self.key_fn = norm  # ключ «тот же клип» (плеер подставляет свой: кадры одной секвенции — один клип)
        self.pool = QtCore.QThreadPool(self)
        self.pool.setMaxThreadCount(2)
        self.bridge = _Bridge()
        self.bridge.done.connect(self._probed)

        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(10, 10, 10, 6)
        v.setSpacing(8)
        top = QtWidgets.QHBoxLayout()
        top.setSpacing(4)
        self.combo = QtWidgets.QComboBox()
        self.combo.setToolTip("Playlist")
        self.combo.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self.combo.setMinimumWidth(60)
        self.combo.activated.connect(self._switch)
        self.btn_more = ui.icon_button("more", "Playlist actions")
        self.btn_more.clicked.connect(self._more_menu)
        self.btn_search = ui.icon_button("zoom", "Search", checkable=True)
        self.btn_search.toggled.connect(self._toggle_search)
        self.btn_prev = ui.icon_button("chev_left", "Previous in playlist (Page Up)")
        self.btn_next = ui.icon_button("chev_right", "Next in playlist (Page Down)")
        self.btn_prev.clicked.connect(lambda: self.step(-1))
        self.btn_next.clicked.connect(lambda: self.step(1))
        for w in (self.combo, self.btn_more, self.btn_search, self.btn_prev, self.btn_next):
            top.addWidget(w)
        v.addLayout(top)
        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText("Search in playlist")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)
        self.search.hide()
        v.addWidget(self.search)

        self.view = PlaylistView()
        self.view.setItemDelegate(RowDelegate(self))
        self.view.itemClicked.connect(lambda it: self.open_requested.emit(it.data(PATH_ROLE)))
        self.view.model().rowsMoved.connect(self._order_changed)
        self.view.files_dropped.connect(self.insert_paths)
        self.view.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.view.customContextMenuRequested.connect(self._row_menu)
        v.addWidget(self.view, 1)

        self.hint = QtWidgets.QLabel("Drop videos, sequences or folders here\nto build a playlist for the review")
        self.hint.setAlignment(QtCore.Qt.AlignCenter)
        self.hint.setStyleSheet(f"color: {ui.DIM}; font-size: 12px;")
        v.addWidget(self.hint, 1)
        self.footer = QtWidgets.QLabel()
        self.footer.setStyleSheet(f"color: {ui.MUTED}; font-size: 11px;")
        v.addWidget(self.footer)
        self.setAcceptDrops(True)  # бросить на пустое место панели — тоже добавить
        self.reload()

    # ---- данные
    def reload(self):
        self.combo.blockSignals(True)
        self.combo.clear()
        self.combo.addItems([x["name"] for x in self.store.lists])
        self.combo.setCurrentIndex(self.store.current)
        self.combo.blockSignals(False)
        self.view.clear()
        for p in self.store.items:
            it = QtWidgets.QListWidgetItem()
            it.setData(PATH_ROLE, p)
            it.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable | QtCore.Qt.ItemIsDragEnabled)
            it.setSizeHint(QtCore.QSize(10, ROW_H))
            self.view.addItem(it)
            self._request(p)
        self._filter(self.search.text())
        self._update_state()

    def _update_state(self):
        n = len(self.store.items)
        self.view.setVisible(n > 0)
        self.hint.setVisible(n == 0)
        k = self.current_index()
        self.footer.setText(f"{k + 1} of {n}" if k is not None else (f"{n} item" + ("s" if n != 1 else "")) if n else "")
        self.btn_prev.setEnabled(n > 0)
        self.btn_next.setEnabled(n > 0)

    def _save(self):
        try:
            self.store.save()
        except OSError as e:
            QtWidgets.QMessageBox.warning(self, "MOOOV_player", f"Could not save playlists: {e}")

    def _request(self, p, force=False):
        if (p in self.meta and not force) or p in self.pending:
            return
        self.pending.add(p)
        self.pool.start(_Job(p, self.probe, self.bridge))

    def _probed(self, p, img, meta):
        self.pending.discard(p)
        self.meta[p] = meta or {}
        if img is not None and not img.isNull():
            self.thumbs[p] = img
        self.view.viewport().update()
        self._update_state()

    def refresh_item(self, path):
        """Пересчитать сведения (например, число комментариев) для пути."""
        for p in list(self.store.items):
            if norm(p) == norm(path):
                self._request(p, force=True)

    def set_comment_count(self, sidecar, count):
        hit = False
        for p in self.store.items:
            m = self.meta.get(p)
            if m and m.get("sidecar") and norm(m["sidecar"]) == norm(sidecar):
                m["comments"] = count
                hit = True
        if hit:
            self.view.viewport().update()

    def is_current(self, p):
        sc = self.meta.get(p, {}).get("sidecar")
        if self.current_sidecar and sc:
            return norm(sc) == self.current_sidecar
        return self.current_path is not None and norm(p) == self.current_path

    def current_index(self):
        return next((k for k, p in enumerate(self.store.items) if self.is_current(p)), None)

    def set_current(self, sidecar, path):
        self.current_sidecar = norm(sidecar) if sidecar else None
        self.current_path = norm(path) if path else None
        self.view.viewport().update()
        k = self.current_index()
        if k is not None:
            self.view.scrollToItem(self.view.item(k))
        self._update_state()

    # ---- правка списка
    def insert_paths(self, paths, row=None):
        have = {self.key_fn(p) for p in self.store.items}
        new = []
        for p in paths:
            k = self.key_fn(p) if p else None
            if k and k not in have:
                have.add(k)
                new.append(p)
        if not new:
            return 0
        row = len(self.store.items) if row is None else max(0, min(row, len(self.store.items)))
        self.store.items[row:row] = new
        self._save()
        self.reload()
        return len(new)

    def remove(self, row):
        if 0 <= row < len(self.store.items):
            del self.store.items[row]
            self._save()
            self.reload()

    def _order_changed(self, *_):
        self.store.lists[self.store.current]["items"] = [self.view.item(k).data(PATH_ROLE) for k in range(self.view.count())]
        self._save()
        self.view.viewport().update()
        self._update_state()

    def step(self, d):
        items = self.store.items
        if not items:
            return
        k = self.current_index()
        k = (0 if d > 0 else len(items) - 1) if k is None else (k + d) % len(items)
        self.open_requested.emit(items[k])

    # ---- поиск
    def _toggle_search(self, on):
        self.search.setVisible(on)
        if on:
            self.search.setFocus()
        else:
            self.search.clear()

    def _filter(self, text):
        t = text.strip().lower()
        for k in range(self.view.count()):
            it = self.view.item(k)
            p = it.data(PATH_ROLE)
            name = (self.meta.get(p, {}).get("name") or os.path.basename(p)).lower()
            it.setHidden(bool(t) and t not in name and t not in p.lower())

    # ---- плейлисты
    def _switch(self, k):
        if k != self.store.current:
            self.store.current = k
            self._save()
            self.reload()

    def _more_menu(self):
        m = QtWidgets.QMenu(self)
        m.addAction("Add Files…", self.add_files_requested.emit)
        m.addAction("Add What's Playing", self.add_current_requested.emit)
        m.addSeparator()
        m.addAction("New Playlist", self.new_playlist)
        m.addAction("Rename Playlist…", self.rename_playlist)
        a = m.addAction("Play All on Timeline", lambda: self.timeline_requested.emit(list(self.store.items)))
        a.setEnabled(len(self.store.items) > 1)
        m.addSeparator()
        a = m.addAction("Clear Playlist", self.clear_playlist)
        a.setEnabled(bool(self.store.items))
        a = m.addAction("Delete Playlist", self.delete_playlist)
        a.setEnabled(len(self.store.lists) > 1)
        m.exec(self.btn_more.mapToGlobal(QtCore.QPoint(0, self.btn_more.height() + 4)))

    def new_playlist(self):
        name, ok = QtWidgets.QInputDialog.getText(self, "New Playlist", "Name:", text=self.store.free_name())
        if ok and name.strip():
            self.store.new(name.strip())
            self._save()
            self.reload()

    def rename_playlist(self):
        name, ok = QtWidgets.QInputDialog.getText(self, "Rename Playlist", "Name:", text=self.store.name)
        if ok and name.strip():
            self.store.lists[self.store.current]["name"] = name.strip()
            self._save()
            self.reload()

    def clear_playlist(self):
        if QtWidgets.QMessageBox.question(self, "MOOOV_player", f"Remove all items from “{self.store.name}”?\n"
                                          "The files themselves stay on disk.") == QtWidgets.QMessageBox.Yes:
            self.store.items.clear()
            self._save()
            self.reload()

    def delete_playlist(self):
        if len(self.store.lists) < 2:
            return
        if QtWidgets.QMessageBox.question(self, "MOOOV_player", f"Delete playlist “{self.store.name}”?\n"
                                          "The files themselves stay on disk.") == QtWidgets.QMessageBox.Yes:
            del self.store.lists[self.store.current]
            self.store.current = max(0, self.store.current - 1)
            self._save()
            self.reload()

    def _row_menu(self, pos):
        it = self.view.itemAt(pos)
        if it is None:
            return
        row, path = self.view.row(it), it.data(PATH_ROLE)
        m = QtWidgets.QMenu(self)
        m.addAction("Open", lambda: self.open_requested.emit(path))
        m.addAction("Add to Timeline", lambda: self.add_to_timeline.emit(path))
        reveal = "Reveal in Finder" if sys.platform == "darwin" else "Show in Explorer"
        m.addAction(reveal, lambda: QtGui.QDesktopServices.openUrl(
            QtCore.QUrl.fromLocalFile(path if os.path.isdir(path) else os.path.dirname(path))))
        m.addSeparator()
        m.addAction("Remove from Playlist", lambda: self.remove(row))
        m.exec(self.view.viewport().mapToGlobal(pos))

    # бросили на панель мимо списка
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls() and e.source() is not self.view:  # своя строка мимо списка — не дублировать
            e.acceptProposedAction()

    def dropEvent(self, e):
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if paths:
            e.acceptProposedAction()
            self.insert_paths(paths)
