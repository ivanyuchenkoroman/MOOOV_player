#!/usr/bin/env python3
"""MOOOV_player — простой и быстрый плеер для видео и секвенций (EXR, PNG, JPG, TIFF...).

Запуск:  uv run MOOOV_player.py [файл_или_папка] [--cache-gb 6]
"""
import os

os.environ.setdefault("OPENCV_IO_ENABLE_OPENEXR", "1")

import argparse
import io
import math
import re
import sys
import threading
import time
from collections import deque

import cv2
import numpy as np
from PySide6 import QtCore, QtGui, QtSvg, QtWidgets
from PySide6.QtOpenGLWidgets import QOpenGLWidget

import annotations as ann
import playlist as pl
import ui_theme as ui

try:
    import OpenEXR  # запасной ридер для EXR, которые не открывает OpenCV
except Exception:  # pragma: no cover
    OpenEXR = None
try:
    import PyOpenColorIO as OCIO  # показ через ACES / конфиг студии ($OCIO)
except Exception:  # pragma: no cover
    OCIO = None

cv2.setNumThreads(1)  # параллелим сами, по кадрам

IMG_EXT = {".exr", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".hdr", ".webp"}
VID_EXT = {".mov", ".mp4", ".m4v", ".mkv", ".avi", ".webm", ".mxf", ".gif", ".mpg", ".mts"}

# --------------------------------------------------------------------------- sources


def find_sequence(path):
    """Файл -> список кадров секвенции (путь, номер кадра)."""
    d, name = os.path.split(os.path.abspath(path))
    m = re.match(r"^(.*?)(\d+)(\.[^.]+)$", name)
    if not m:
        return [(path, 0)]
    pre, _, ext = m.groups()
    rx = re.compile("^" + re.escape(pre) + r"(\d+)" + re.escape(ext) + "$", re.I)
    items = []
    for f in os.listdir(d):
        mm = rx.match(f)
        if mm:
            items.append((int(mm.group(1)), os.path.join(d, f)))
    items.sort()
    return [(p, n) for n, p in items] or [(path, 0)]


def _exr_src(src):
    """Путь или байты файла (кадр, докачанный по сети) — для OpenEXR.File."""
    return io.BytesIO(src) if isinstance(src, (bytes, bytearray)) else src


def read_exr_fallback(path):
    with OpenEXR.File(_exr_src(path)) as f:
        ch = f.channels()
        for key in ("RGBA", "RGB"):
            if key in ch:
                return np.ascontiguousarray(ch[key].pixels).astype(np.float32)
        # первый попавшийся канал
        first = next(iter(ch.values()))
        return np.ascontiguousarray(first.pixels).astype(np.float32)


BASE_LAYER = "RGBA"  # основной слой EXR (каналы R G B A без префикса)
_CH_ORDER = {"r": 0, "red": 0, "x": 0, "u": 0, "g": 1, "green": 1, "y": 1, "v": 1,
             "b": 2, "blue": 2, "z": 2, "w": 2, "a": 3, "alpha": 3}


def _exr_groups(names):
    """Каналы EXR по слоям: {'diffuse': ['diffuse.R', ...], 'Z': ['Z'], 'RGBA': ['R', 'G', 'B', 'A']}.
    Слой — всё до последней точки; одиночные каналы без префикса (Z, depth) — каждый свой слой."""
    groups = {}
    for nm in names:
        if "." in nm:
            key = nm.rsplit(".", 1)[0]
        elif nm.lower() in ("r", "g", "b", "a", "y"):
            key = BASE_LAYER
        else:
            key = nm
        groups.setdefault(key, []).append(nm)
    return groups


def _exr_sorted(chans):
    return sorted(chans, key=lambda c: (_CH_ORDER.get(c.rsplit(".", 1)[-1].lower(), 9), c))


_LAYERS = {}


def exr_layers(path):
    """Имена слоёв EXR (запоминаются — меню каналов не лезет каждый раз в сеть)."""
    if path not in _LAYERS:
        _LAYERS[path] = _exr_layers(path)
    return list(_LAYERS[path])


def _exr_layers(path):
    """Имена слоёв EXR: основной первым, дальше по алфавиту. [] — если файл не EXR или не читается."""
    if OpenEXR is None or not path.lower().endswith(".exr"):
        return []
    try:
        with OpenEXR.File(path, separate_channels=True, header_only=True) as f:  # только заголовок — быстро
            names = [c.name for part in f.parts for c in part.header.get("channels", [])]
    except Exception:
        return []
    g = _exr_groups(names)
    out = []
    for k, chans in g.items():
        if k == BASE_LAYER:
            continue
        odd = [c for c in chans if not _std_chan(c)]  # кастомные каналы (маски: masks.hair, masks.sky …)
        if len(chans) == 1 or not odd:
            out.append(k)
            continue
        if len(odd) < len(chans):
            out.append(k)  # стандартная часть слоя (R G B A) — как обычно
        out += odd  # каждая кастомная маска — отдельным пунктом, серым
    return ([BASE_LAYER] if BASE_LAYER in g else []) + sorted(out, key=str.lower)


def _std_chan(c):
    """Канал с обычным суффиксом (R G B A, X Y Z, U V W …) — собирается в цвет вместе с соседями."""
    return c.rsplit(".", 1)[-1].lower() in _CH_ORDER


def read_exr_layer(path, layer):
    """Кадр одного слоя EXR (AOV) как float32: 1 канал — серый, 2 — RG + пустой B, 3–4 — RGB(A)."""
    with OpenEXR.File(_exr_src(path), separate_channels=True) as f:
        ch = {}
        for part in f.parts:
            ch.update(part.channels)
        if layer in ch:  # один канал по полному имени (маска) — серым
            return np.ascontiguousarray(np.asarray(ch[layer].pixels, dtype=np.float32))
        chans = _exr_groups(ch).get(layer)
        if not chans:
            return None
        chans = [c for c in chans if _std_chan(c)] or chans  # кастомные маски слоя — отдельными пунктами
        chans = _exr_sorted(chans)
        planes = [np.asarray(ch[c].pixels, dtype=np.float32) for c in chans[:4]]
    if len(chans) > 4 or (len(chans) == 4 and chans[3].rsplit(".", 1)[-1].lower() not in ("a", "alpha")):
        planes = planes[:3]  # 4 канала, но 4-й не альфа — показываем первые три
    if len(planes) == 1:
        return planes[0]
    if len(planes) == 2:
        planes.append(np.zeros_like(planes[0]))
    return np.ascontiguousarray(np.dstack(planes))


def read_image(path, layer=None, data=None):
    """Кадр как numpy. layer — слой EXR (AOV); если его нет в файле — основной.
    data — байты файла, если он уже докачан по сети (тогда диск/сеть не трогаем)."""
    src = path if data is None else data
    if layer and layer != BASE_LAYER and OpenEXR is not None and path.lower().endswith(".exr"):
        try:
            a = read_exr_layer(src, layer)
        except Exception:
            a = None
        if a is not None:
            return a
    if data is None:
        a = cv2.imread(path, cv2.IMREAD_UNCHANGED)
    else:
        a = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_UNCHANGED)
    if a is None and path.lower().endswith(".exr") and OpenEXR is not None:
        return read_exr_fallback(src)  # уже RGB(A)
    if a is None:
        raise IOError("cannot read " + path)
    if a.ndim == 3:
        if a.shape[2] == 3:
            a = cv2.cvtColor(a, cv2.COLOR_BGR2RGB)
        elif a.shape[2] == 4:
            a = cv2.cvtColor(a, cv2.COLOR_BGRA2RGBA)
    return a


FORMAT_NAMES = {".exr": "OpenEXR", ".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG", ".tif": "TIFF",
                ".tiff": "TIFF", ".bmp": "BMP", ".hdr": "Radiance HDR", ".webp": "WebP"}
DTYPE_BITS = {"uint8": "8-bit", "uint16": "16-bit", "float16": "16-bit half float", "float32": "32-bit float",
              "uint32": "32-bit uint"}


def human_size(b):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if b < 1024 or unit == "TB":
            return f"{b:.0f} {unit}" if unit == "B" else f"{b:.1f} {unit}"
        b /= 1024


def channel_names(c):
    return {1: "Gray", 2: "Gray+A", 3: "RGB", 4: "RGBA"}.get(c, f"{c} ch")


def exr_details(path):
    """(компрессия, битность, каналы) из EXR; None если не вышло."""
    if OpenEXR is None:
        return None
    try:
        with OpenEXR.File(_exr_src(path)) as f:
            comp =str(f.header().get("compression", "")).split(".")[-1].replace("_COMPRESSION", "")
            chans = f.channels()
            dt = next(iter(chans.values())).pixels.dtype
            return comp, DTYPE_BITS.get(str(dt), str(dt)), ", ".join(chans.keys())
    except Exception:
        return None


def ffprobe_info(path):
    import json
    import shutil
    import subprocess
    exe = shutil.which("ffprobe") or os.path.expanduser("~/.local/bin/ffprobe")
    if not os.path.exists(exe):
        return None
    try:
        out = subprocess.run([exe, "-v", "error", "-select_streams", "v:0", "-show_entries",
                              "stream=codec_name,codec_long_name,profile,pix_fmt,bits_per_raw_sample,width,height:"
                              "format=format_long_name,size", "-of", "json", path],
                             capture_output=True, text=True, timeout=10,
                             # на Windows без этого на миг выскакивает чёрное окно консоли
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return json.loads(out.stdout)
    except Exception:
        return None


MOD = "⌘" if sys.platform == "darwin" else "Ctrl+"
# режимы наложения слоёв (как в композе) → режимы QPainter
BLEND_MODES = {
    "Normal": QtGui.QPainter.CompositionMode_SourceOver,
    "Add": QtGui.QPainter.CompositionMode_Plus,
    "Screen": QtGui.QPainter.CompositionMode_Screen,
    "Multiply": QtGui.QPainter.CompositionMode_Multiply,
    "Overlay": QtGui.QPainter.CompositionMode_Overlay,
    "Soft Light": QtGui.QPainter.CompositionMode_SoftLight,
    "Lighten": QtGui.QPainter.CompositionMode_Lighten,
    "Darken": QtGui.QPainter.CompositionMode_Darken,
    "Difference": QtGui.QPainter.CompositionMode_Difference,
}  # подписи сочетаний клавиш под систему
APP_VERSION = "1.1.0"
AUTHOR = "Ivaniuchenko Roman"
SITE = "mooov.team"
SITE_URL = "https://mooov.team"
SKETCH_TEXT = ann.SKETCH_TEXT  # подпись рисунка без текста (сам текст пустой — печатаешь сразу своё)
CREDITS = f"Developed by {AUTHOR} for {SITE}"
SPEEDS = (0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 4.0)
MAX_DISPLAY_FPS = 60  # быстрее экран не показывает — на 2×/4× пропускаем кадры, время остаётся точным


def seq_size(items):
    """Размер секвенции одним листингом папки (по сети stat на каждый файл — минута на 200 кадров)."""
    want = {os.path.normcase(os.path.basename(p)) for p, _ in items}
    dirs = {os.path.dirname(p) for p, _ in items}
    total = 0
    for d in dirs:
        try:
            with os.scandir(d) as it:
                for e in it:
                    if os.path.normcase(e.name) in want and e.is_file():
                        total += e.stat().st_size
        except OSError:
            pass
    return total


_REMOTE = {}


def is_remote(path):
    """Файл на сетевом диске / шаре: такие кадры сначала докачиваем кусками (можно приостановить),
    потом декодируем уже из кэша системы — смена канала / AOV не качает файлы заново."""
    p = os.path.abspath(path)
    if p.startswith(("\\\\", "//")):
        return True
    root = os.path.splitdrive(p)[0] if sys.platform == "win32" else "/".join(p.split("/")[:3])
    if root not in _REMOTE:
        r = False
        if sys.platform == "win32":
            try:
                import ctypes
                r = ctypes.windll.kernel32.GetDriveTypeW(root + "\\") == 4  # DRIVE_REMOTE
            except Exception:
                pass
        else:
            r = p.startswith(("/Volumes/", "/mnt/", "/net/"))
        _REMOTE[root] = r
    return _REMOTE[root]


# потоков чтения: больше — только толкотня за GIL; по сети упираемся в канал, хватает 4
LOCAL_THREADS = max(2, min(8, (os.cpu_count() or 4) - 2))
REMOTE_THREADS = 4


class SequenceSource:
    threads = LOCAL_THREADS

    def __init__(self, path):
        self.items = find_sequence(path)
        self.n = len(self.items)
        self.remote = is_remote(self.items[0][0])
        self.threads = REMOTE_THREADS if self.remote else LOCAL_THREADS
        self.fps = 24.0
        first, last = self.items[0][1], self.items[-1][1]
        base = os.path.basename(self.items[0][0])
        self.name = base if self.n == 1 else f"{base}  [{first}-{last}]"
        self.linear = self.items[0][0].lower().endswith((".exr", ".hdr"))
        self.numbers = [num for _, num in self.items]
        # файл заметок рядом с секвенцией: shot_v01.####.exr -> shot_v01.mooov.json
        p0 = self.items[0][0]
        d, b = os.path.split(p0)
        stem = re.sub(r"[._-]?\d+\.[^.]+$", "", b) if self.n > 1 else os.path.splitext(b)[0]
        self.sidecar = os.path.join(d, (stem or "sequence") + ".mooov.json")
        self.file_label = os.path.basename(p0) if self.n == 1 else re.sub(r"\d+(?=\.[^.]+$)", lambda m: "#" * len(m.group()), b)

    def label(self, i):
        return str(self.items[i][1])

    def frame_file(self, i):
        return os.path.basename(self.items[i][0])

    def info(self):
        first = self.items[0][0]
        ext = os.path.splitext(first)[1].lower()
        with open(first, "rb") as fh:  # байты читаем сами: OpenEXR по сети держит GIL и морозит интерфейс
            data = fh.read()
        a = read_image(first, data=data)
        h, w = a.shape[:2]
        ch = 1 if a.ndim == 2 else a.shape[2]
        fmt = FORMAT_NAMES.get(ext, ext[1:].upper())
        bits = DTYPE_BITS.get(str(a.dtype), str(a.dtype))
        chans = channel_names(ch)
        if ext == ".exr":
            d = exr_details(data)
            if d:
                fmt += f" · {d[0]}"
                bits, chans = d[1], d[2]
        lo, hi = self.numbers[0], self.numbers[-1]
        missing = (hi - lo + 1) - self.n
        size = seq_size(self.items)
        return {"name": self.file_label, "path": os.path.dirname(first), "format": fmt,
                "w": w, "h": h, "bits": f"{bits} · {chans}",
                "range": f"{lo}–{hi}" + (f"   ({missing} missing)" if missing > 0 else ""),
                "size": human_size(size)}

    def read(self, i, layer=None, data=None):
        return read_image(self.items[i][0], layer, data)

    def remote_path(self, i):
        """Путь кадра, если он по сети (его надо сначала докачать), иначе None."""
        return self.items[i][0] if self.remote else None

    def color_space(self, i=0):
        """Пространство ACES секвенции по заголовку первого кадра (один раз)."""
        if not hasattr(self, "_cs"):
            self._cs = exr_aces_space(self.items[0][0])
        return self._cs


class VideoSource:
    threads = 1  # декодирование последовательное

    def __init__(self, path):
        self.path = path
        self.cap = cv2.VideoCapture(path, cv2.CAP_FFMPEG)
        if not self.cap.isOpened():
            raise IOError("cannot open video " + path)
        self.fps = self.cap.get(cv2.CAP_PROP_FPS) or 24.0
        self.n = max(1, int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT)))
        self.next_idx = 0
        self.lock = threading.Lock()
        self.name = os.path.basename(path)
        self.linear = False
        self.numbers = list(range(1, self.n + 1))
        self.sidecar = path + ".mooov.json"
        self.file_label = self.name

    def label(self, i):
        return str(i + 1)

    def frame_file(self, i):
        return self.name

    def info(self):
        w = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fmt, bits = os.path.splitext(self.path)[1][1:].upper(), "8-bit"
        pr = ffprobe_info(self.path)
        if pr and pr.get("streams"):
            st = pr["streams"][0]
            codec = st.get("codec_long_name", st.get("codec_name", "")).split(" / ")[0]
            prof = st.get("profile")
            fmt = f"{pr.get('format', {}).get('format_long_name', fmt)} · {codec}" + (f" {prof}" if prof else "")
            b = st.get("bits_per_raw_sample")
            pix = st.get("pix_fmt", "")
            if not b:
                b = "10" if "10" in pix else "12" if "12" in pix else "8"
            bits = f"{b}-bit · {pix}"
        else:
            fourcc = int(self.cap.get(cv2.CAP_PROP_FOURCC))
            codec = "".join(chr((fourcc >> 8 * k) & 0xFF) for k in range(4)).strip()
            if codec:
                fmt += f" · {codec}"
        return {"name": self.name, "path": os.path.dirname(os.path.abspath(self.path)), "format": fmt,
                "w": w, "h": h, "bits": bits, "range": f"1–{self.n}",
                "size": human_size(os.path.getsize(self.path))}

    def read(self, i, layer=None):
        with self.lock:
            if i != self.next_idx:
                self.cap.set(cv2.CAP_PROP_POS_FRAMES, i)
            ok, a = self.cap.read()
            self.next_idx = i + 1
            if not ok:
                raise IOError(f"frame {i} failed to decode")
            return cv2.cvtColor(a, cv2.COLOR_BGR2RGB)


class CompositeSource:
    """Монтаж: несколько клипов подряд на одном таймлайне. Кадры нумеруются 1..N сквозь все клипы."""

    def __init__(self, clips):
        import bisect
        self._bisect = bisect.bisect_right
        self.clips = list(clips)
        self.offsets, o = [], 0
        for c in self.clips:
            self.offsets.append(o)
            o += c.n
        self.n = o
        self.fps = self.clips[0].fps
        self.threads = max(getattr(c, "threads", 1) for c in self.clips)
        self.linear = False
        self.numbers = list(range(1, self.n + 1))
        self.sidecar = None  # комментарии хранятся в файлах самих клипов
        self.name = "  +  ".join(c.file_label for c in self.clips)
        self.file_label = f"Edit ({len(self.clips)} clips)"

    def locate(self, i):
        """Глобальный кадр -> (номер клипа, кадр внутри клипа)."""
        k = max(0, self._bisect(self.offsets, i) - 1)
        return k, i - self.offsets[k]

    def read(self, i, layer=None, data=None):
        k, j = self.locate(i)
        return self.clips[k].read(j, layer) if data is None else self.clips[k].read(j, layer, data)

    def remote_path(self, i):
        k, j = self.locate(i)
        c = self.clips[k]
        return c.remote_path(j) if hasattr(c, "remote_path") else None

    def color_space(self, i):
        c = self.clips[self.locate(i)[0]]
        return c.color_space(0) if hasattr(c, "color_space") else None

    def is_linear(self, i):
        return self.clips[self.locate(i)[0]].linear

    def label(self, i):
        return str(i + 1)

    def frame_file(self, i):
        k, j = self.locate(i)
        return self.clips[k].frame_file(j)

    def source_key(self):
        return [c.sidecar for c in self.clips]


def open_source(path):
    if os.path.isdir(path):
        files = sorted(f for f in os.listdir(path) if os.path.splitext(f)[1].lower() in IMG_EXT)
        if not files:
            raise IOError("no images in this folder")
        path = os.path.join(path, files[0])
    ext = os.path.splitext(path)[1].lower()
    if ext in VID_EXT:
        return VideoSource(path)
    return SequenceSource(path)


def src_path(src):
    """Путь, по которому источник можно открыть снова (для плейлиста)."""
    return src.path if hasattr(src, "path") else src.items[0][0]


def media_key(path):
    """Один клип — один ключ: любой кадр секвенции даёт тот же ключ, что и её папка."""
    p = os.path.normcase(os.path.abspath(path))
    if os.path.isdir(p):
        files = sorted(f for f in os.listdir(p) if os.path.splitext(f)[1].lower() in IMG_EXT)
        if not files:
            return p
        p = os.path.join(p, files[0])
    if os.path.splitext(p)[1].lower() in IMG_EXT:
        d, b = os.path.split(p)
        return os.path.join(d, re.sub(r"\d+(?=\.[^.]+$)", "#", b))
    return p


def human_dur(sec):
    return f"{sec:.1f} s" if sec < 60 else f"{int(sec // 60)}:{int(sec % 60):02d}"


def probe_media(path):
    """Для плейлиста (в фоне): превью среднего кадра, длительность, число комментариев."""
    src = open_source(path)
    try:
        key = pl.thumb_key(path)
        img = QtGui.QImage(key) if os.path.exists(key) else QtGui.QImage()
        if img.isNull():
            a = src.read(src.n // 2)  # середина: первый кадр часто слейт
            big = a.shape[0] > 1200
            q = to_qimage(a, (0.0, "RGB", "sRGB", big), src.linear)
            img = q.scaled(pl.THUMB * 2, QtCore.Qt.KeepAspectRatioByExpanding, QtCore.Qt.SmoothTransformation)
            img.save(key, "JPG", 88)
        dur = human_dur(src.n / (src.fps or 24.0))
        if isinstance(src, SequenceSource) and src.n > 1:
            sub = f"{src.numbers[0]}–{src.numbers[-1]}  ·  {src.n} fr  ·  {dur}"
        else:
            sub = f"{src.n} fr  ·  {dur}  ·  {src.fps:g} fps"
        store = ann.NoteStore(src.sidecar)
        return img, {"name": src.file_label, "sub": sub, "sidecar": src.sidecar,
                     "comments": sum(1 for n in store.notes if not n.is_empty())}
    finally:
        cap = getattr(src, "cap", None)
        if cap is not None:
            cap.release()


# --------------------------------------------------------------------------- display transform

_SRGB_LUT = None


def srgb_lut():
    global _SRGB_LUT
    if _SRGB_LUT is None:
        x = np.linspace(0, 1, 4096, dtype=np.float64)
        y = np.where(x <= 0.0031308, x * 12.92, 1.055 * np.power(x, 1 / 2.4) - 0.055)
        _SRGB_LUT = np.clip(np.round(y * 255), 0, 255).astype(np.uint8)
    return _SRGB_LUT


# ---- OCIO: встроенные конфиги ACES (файлы не нужны) + конфиг студии из переменной $OCIO
OCIO_ACES1 = "ocio://studio-config-v2.2.0_aces-v1.3_ocio-v2.4"  # ACES 1.3 — как в Nuke / Resolve по умолчанию


def exr_aces_space(path):
    """'ACES2065-1' / 'ACEScg' по заголовку EXR; None — не понять (тогда ACEScg)."""
    if OpenEXR is None or not path.lower().endswith(".exr"):
        return None
    try:  # без with: внутри него OpenEXR 3.5 отдаёт chromaticities = None
        h = dict(OpenEXR.File(path, header_only=True).header())
    except Exception:
        return None
    ch = h.get("chromaticities")
    if ch is not None:
        try:
            rx, ry, gx, gy = (float(v) for v in tuple(ch)[:4])
        except Exception:
            rx = ry = gx = gy = -1
        if abs(rx - 0.7347) < 2e-3 and abs(ry - 0.2653) < 2e-3 and abs(gx) < 2e-3 and abs(gy - 1) < 2e-3:
            return "ACES2065-1"  # AP0 — так пишет Write в Nuke с ACES-контейнером
        if abs(rx - 0.713) < 2e-3 and abs(ry - 0.293) < 2e-3:
            return "ACEScg"  # AP1
    if h.get("acesImageContainerFlag"):
        return "ACES2065-1"
    return None
_OCIO_LOCK = threading.Lock()
_OCIO_CFG, _OCIO_PROC, _OCIO_VIEWS = {}, {}, None


def _ocio_config(path):
    if path not in _OCIO_CFG:
        _OCIO_CFG[path] = OCIO.Config.CreateFromFile(path)
    return _OCIO_CFG[path]


def ocio_views():
    """Режимы показа через OCIO: подпись -> (конфиг, дисплей, вид). ACES 1 / ACES 2 / без тонмаппинга
    для мониторов sRGB и Rec.709, плюс виды дисплея по умолчанию из конфига студии ($OCIO)."""
    global _OCIO_VIEWS
    if _OCIO_VIEWS is None:
        v = {}
        if OCIO is not None:
            # один пункт = вход EXR -> монитор, через ACES 1.0 SDR Video (как вьювер Nuke в ACES 1.x)
            for disp, tag in (("sRGB - Display", "sRGB"), ("Rec.1886 Rec.709 - Display", "Rec.709")):
                for inp in ("ACEScg", "ACES2065-1"):
                    v[f"{inp} → {tag}"] = (OCIO_ACES1, disp, "ACES 1.0 - SDR Video", inp)
            env = os.environ.get("OCIO")
            if env:
                try:
                    c = _ocio_config(env)
                    d = c.getDefaultDisplay()
                    for vw in c.getViews(d):
                        v[f"OCIO · {vw}"] = (env, d, vw, "scene_linear")
                except Exception:
                    pass
        _OCIO_VIEWS = v
    return _OCIO_VIEWS


def ocio_processor(view, src=None):
    """CPU-процессор «вход -> дисплей». Точный ACES на 2K — 0.5–1.8 с на кадр, поэтому запекаем его
    в 3D LUT 65³ на логарифмическом шейпере ACEScct (как RV / Nuke): ~85 мс, ошибка в среднем < 0.5/255."""
    key = (view, src)
    with _OCIO_LOCK:
        if key in _OCIO_PROC:
            return _OCIO_PROC[key]
        cfg, disp, vw, name = ocio_views()[view]
        c = _ocio_config(cfg)
        if c.getColorSpace(name) is None:
            name = "scene_linear"  # в конфиге студии такого нет — его рабочее линейное
        if c.getColorSpace("ACEScct") is not None:
            n = 65
            g = np.linspace(0, 1, n, dtype=np.float32)
            r_, g_, b_ = np.meshgrid(g, g, g, indexing="ij")  # порядок Lut3D OCIO: синий меняется быстрее всех
            grid = np.ascontiguousarray(np.stack([r_, g_, b_], -1).reshape(-1, 3))
            c.getProcessor(OCIO.DisplayViewTransform(src="ACEScct", display=disp, view=vw)
                           ).getDefaultCPUProcessor().applyRGB(grid)
            lut = OCIO.Lut3DTransform(gridSize=n)
            lut.setData(grid.ravel())
            lut.setInterpolation(OCIO.INTERP_TETRAHEDRAL)
            proc = c.getProcessor(OCIO.GroupTransform([OCIO.ColorSpaceTransform(src=name, dst="ACEScct"), lut]))
        else:
            proc = c.getProcessor(OCIO.DisplayViewTransform(src=name, display=disp, view=vw))
        cpu = _OCIO_PROC[key] = proc.getDefaultCPUProcessor()
        return cpu


# каше (маска формата кадра): подпись -> соотношение сторон
MATTES = {"No matte": None, "2.39:1": 2.39, "2.35:1": 2.35, "2:1": 2.0, "1.85:1": 1.85,
          "16:9": 16 / 9, "4:3": 4 / 3, "1:1": 1.0, "4:5": 0.8, "9:16": 9 / 16}

CHANNELS = {"RGB": None, "R": 0, "G": 1, "B": 2, "A": 3, "Luma": "L"}


def to_qimage(a, params, linear, alpha=False):
    """numpy RGB(A) -> QImage для показа (exposure, канал, sRGB, half-res).
    alpha=True (слои): у RGBA-кадров альфа сохраняется — слой ложится поверх только своими пикселями."""
    exposure, channel, view, half = params[:4]
    if a.ndim == 2:
        a = a[:, :, None]
    if half:
        h, w = a.shape[:2]
        a = cv2.resize(a, (max(1, w // 2), max(1, h // 2)), interpolation=cv2.INTER_AREA)
        if a.ndim == 2:
            a = a[:, :, None]
    c = a.shape[2]
    sel = CHANNELS[channel]
    gray = sel is not None
    if sel is None:
        x = a[:, :, :3] if c >= 3 else np.repeat(a[:, :, :1], 3, axis=2)
    elif sel == "L":
        rgb = a[:, :, :3] if c >= 3 else np.repeat(a[:, :, :1], 3, axis=2)
        x = (rgb[:, :, 0] * 0.2126 + rgb[:, :, 1] * 0.7152 + rgb[:, :, 2] * 0.0722).astype(a.dtype)
    elif sel == 3:
        x = a[:, :, 3] if c == 4 else np.full(a.shape[:2], 1 if a.dtype.kind == "f" else np.iinfo(a.dtype).max, a.dtype)
    else:
        x = a[:, :, sel if c > sel else 0]

    is_float = a.dtype.kind == "f"
    ocio = view in ocio_views() and is_float and linear
    use_srgb = is_float and linear and view != "Linear" and sel != 3  # OCIO-вид: отдельные каналы — как в sRGB
    if ocio and sel is None:  # RGB через OCIO: экспозиция в сцене, потом вход -> дисплей
        f = np.array(x, dtype=np.float32, order="C")
        if exposure:
            f *= 2.0 ** exposure
        try:
            ocio_processor(view).applyRGB(f)
        except Exception:  # pragma: no cover — сломанный конфиг: хотя бы sRGB
            np.clip(f, 0, 1, out=f)
            f = srgb_lut()[(f * 4095).astype(np.uint16)].astype(np.float32) / 255
        np.clip(f, 0, 1, out=f)
        out = (f * 255 + 0.5).astype(np.uint8)
    elif a.dtype == np.uint8 and exposure == 0:
        out = x
    else:
        f = x.astype(np.float32)
        if a.dtype == np.uint8:
            f *= 1 / 255.0
        elif a.dtype == np.uint16:
            f *= 1 / 65535.0
        if exposure:
            f *= 2.0 ** exposure
        np.clip(f, 0, 1, out=f)
        if use_srgb:
            out = srgb_lut()[(f * 4095).astype(np.uint16)]
        else:
            out = (f * 255 + 0.5).astype(np.uint8)
    fmt = QtGui.QImage.Format_Grayscale8 if gray else QtGui.QImage.Format_RGB888
    if alpha and sel is None and c == 4:  # альфа — без экспозиции и sRGB
        al = a[:, :, 3]
        if al.dtype != np.uint8:
            al = (np.clip(al.astype(np.float32) * (1 / 65535.0 if al.dtype == np.uint16 else 1.0), 0, 1) * 255 + 0.5
                  ).astype(np.uint8)
        out = np.dstack([out, al])
        fmt = QtGui.QImage.Format_RGBA8888
    out = np.ascontiguousarray(out)
    h, w = out.shape[:2]
    return QtGui.QImage(out.data, w, h, out.strides[0], fmt).copy()


# --------------------------------------------------------------------------- loader / cache


class Bridge(QtCore.QObject):
    ready = QtCore.Signal(int)


class Loader:
    """Пул потоков, который грузит кадры от плейхеда в кэш с лимитом по памяти: в основном вперёд,
    плюс BACK_SEC секунд назад, чтобы можно было чуть отмотать без ожидания.
    Сетевые кадры — в два шага: файл докачивается кусками (warm), потом декодируется.
    На паузе кадр под плейхедом важнее всего: пока его нет, остальные докачки стоят."""
    playing = False  # Player ставит на Play / Stop: при проигрывании качаем всеми потоками подряд
    BACK_SEC = 2.0  # сколько секунд позади плейхеда держать в кэше

    def __init__(self, source, budget_bytes, params, alpha=False):
        self.src = source
        self.alpha = alpha
        self.n = source.n
        self.budget = budget_bytes
        self.params = params
        self.cond = threading.Condition()
        self.cache = {}  # idx -> QImage | Exception
        self.used = 0
        self.inflight = set()
        self.raw = {}  # сетевые кадры: idx -> байты файла (AOV / канал / EV меняем без повторной закачки)
        self.raw_used = 0
        self.partial = {}  # недокачанные (уступили кадру под плейхедом): idx -> куски
        self.playhead = 0
        self.gen = 0
        self.frame_bytes = 0
        self.alive = True
        self.bridge = Bridge()
        self.threads = [threading.Thread(target=self._work, daemon=True) for _ in range(source.threads)]
        for t in self.threads:
            t.start()

    # ---- api (GUI thread)
    def get(self, i):
        with self.cond:
            return self.cache.get(i)

    def has(self, i):
        with self.cond:
            return i in self.cache

    def cached(self):
        with self.cond:
            return list(self.cache.keys())

    def set_playhead(self, i):
        with self.cond:
            self.playhead = i
            self.cond.notify_all()

    def set_params(self, params):
        with self.cond:
            self.params = params
            self.gen += 1
            self.cache.clear()
            self.used = 0
            self.cond.notify_all()

    def set_budget(self, budget_bytes):
        with self.cond:
            self.budget = budget_bytes
            self._evict()
            self._evict_raw()
            self.cond.notify_all()

    def close(self):
        with self.cond:
            self.alive = False
            self.cond.notify_all()

    # ---- workers
    def _window(self):
        if not self.frame_bytes:
            return len(self.threads) * 2
        return max(2, min(self.n, int(self.budget // self.frame_bytes)))

    def _wanted(self):
        """Кадры, которые держим, по важности: секунда вперёд, BACK_SEC назад, дальше вперёд.
        Назад — не больше трети окна, чтобы вперёд для проигрывания хватало всегда."""
        w, n, ph = self._window(), self.n, self.playhead
        fps = getattr(self.src, "fps", 24) or 24
        back = max(0, min(int(round(self.BACK_SEC * fps)), w // 3, n - w if n > w else n - 1))
        fwd = max(1, w - back)
        first = min(fwd, max(len(self.threads) * 2, int(round(fps))))
        out = [(ph + k) % n for k in range(first)]
        out += [(ph - k) % n for k in range(1, back + 1)]
        out += [(ph + k) % n for k in range(first, fwd)]
        return out

    def _next_job(self):
        for f in self._wanted():
            if f not in self.cache and f not in self.inflight:
                return f
        return None

    def _evict(self):
        if self.used <= self.budget:
            return
        rank = {f: k for k, f in enumerate(self._wanted())}
        ph, n = self.playhead, self.n
        while self.used > self.budget and self.cache:
            # сначала то, что не нужно (дальше всего впереди), потом — наименее важное из нужного
            victim = max(self.cache, key=lambda f: (f not in rank, rank.get(f, (f - ph) % n)))
            img = self.cache.pop(victim)
            if isinstance(img, QtGui.QImage):
                self.used -= img.sizeInBytes()

    def _urgent(self):
        """На паузе кадр под плейхедом ещё не готов — весь канал ему."""
        return not Loader.playing and self.playhead not in self.cache

    def _evict_raw(self):
        """Байты сетевых кадров держим в половине бюджета памяти; дальние от плейхеда — вон."""
        ph, n = self.playhead, self.n
        while self.raw_used > self.budget // 2 and len(self.raw) > 1:
            victim = max(self.raw, key=lambda f: (f - ph) % n)
            self.raw_used -= len(self.raw.pop(victim))
        while len(self.partial) > 16:
            self.partial.pop(max(self.partial, key=lambda f: (f - ph) % n))

    def _fetch(self, f, path):
        """Докачать файл кадра в память кусками по 1 МБ (GIL при этом свободен — интерфейс не стоит).
        Пока ждём кадр под плейхедом — стоим; если его некому взять — уступаем поток,
        докачанное запоминаем и потом продолжаем с того же места.
        Байты файла; b"" — не читается (ошибку покажет декодер); None — уступили / закрыто."""
        with self.cond:
            chunks = self.partial.pop(f, [])
        try:
            with open(path, "rb", buffering=0) as fh:
                if chunks:
                    fh.seek(sum(len(c) for c in chunks))
                while True:
                    with self.cond:
                        while self.alive and f != self.playhead and self._urgent():
                            if self.playhead not in self.inflight:
                                self.partial[f] = chunks
                                return None
                            self.cond.wait(0.5)
                        if not self.alive:
                            return None
                    b = fh.read(1 << 20)
                    if not b:
                        break
                    chunks.append(b)
        except OSError:
            return b""
        data = b"".join(chunks)
        with self.cond:
            self.raw[f] = data
            self.raw_used += len(data)
            self._evict_raw()
        return data

    def _work(self):
        while True:
            with self.cond:
                while self.alive and (f := self._next_job()) is None:
                    self.cond.wait()
                if not self.alive:
                    return
                self.inflight.add(f)
                data = self.raw.get(f)
            path = None if data is not None else getattr(self.src, "remote_path", lambda _: None)(f)
            if path:
                data = self._fetch(f, path)
                if data is None:
                    with self.cond:
                        self.inflight.discard(f)
                        self.cond.notify_all()
                    continue
            with self.cond:  # параметры — после докачки: AOV могли сменить, пока файл шёл
                gen, params = self.gen, self.params
            try:
                lin = self.src.is_linear(f) if hasattr(self.src, "is_linear") else self.src.linear
                layer = params[4] if len(params) > 4 else None
                a = self.src.read(f, layer, data) if data else (self.src.read(f, layer) if layer else self.src.read(f))
                img = to_qimage(a, params, lin, self.alpha)
            except Exception as e:  # битый / отсутствующий кадр
                img = e
            with self.cond:
                self.inflight.discard(f)
                self.cond.notify_all()  # кадр плейхеда готов — отпускаем остальные докачки
                if gen != self.gen or not self.alive:
                    continue
                self.cache[f] = img
                if isinstance(img, QtGui.QImage):
                    self.used += img.sizeInBytes()
                    self.frame_bytes = img.sizeInBytes()
                    self._evict()
            self.bridge.ready.emit(f)


# --------------------------------------------------------------------------- widgets


def paint_info_box(p, rows, origin, font, max_w, bg_alpha=105, draw=True):
    """Плашка инфо (как на кадре): подписи слева, значения справа; Name — жирным, Path — жёлтым.
    Возвращает (рамка плашки, рамка пути или None). draw=False — только посчитать размер."""
    f = QtGui.QFont(font)
    f.setPixelSize(12)
    fb = QtGui.QFont(f)
    fb.setBold(True)
    fm, fmb = QtGui.QFontMetricsF(f), QtGui.QFontMetricsF(fb)
    pad, gap, lh = 10, 12, fm.height() + 3
    lw = max(fm.horizontalAdvance(k) for k, _ in rows)
    maxv = max(120, min(520, max_w - 2 * pad - lw - gap))
    vals = []
    for k, v in rows:
        fnt = fmb if k == "Name" else fm
        mode = QtCore.Qt.ElideMiddle if k in ("Path", "Name", "File") else QtCore.Qt.ElideRight
        vals.append(fnt.elidedText(v, mode, maxv))
    vw = max(max(fmb.horizontalAdvance(v), fm.horizontalAdvance(v)) for v in vals)
    box = QtCore.QRectF(origin.x(), origin.y(), 2 * pad + lw + gap + vw, 2 * pad + lh * len(vals) - 3)
    path_rect = None
    y = box.top() + pad
    if draw:
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        p.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 14), 1))
        bgc = QtGui.QColor(ui.BG)
        bgc.setAlpha(bg_alpha)
        p.setBrush(bgc)
        p.drawRoundedRect(box.adjusted(0.5, 0.5, -0.5, -0.5), ui.RADIUS_CARD, ui.RADIUS_CARD)
        p.setOpacity(0.75)  # данные приглушены — 75% видимости
    for (k, _), v in zip(rows, vals):
        vr = QtCore.QRectF(box.left() + pad + lw + gap, y, vw, lh)
        if k == "Path":
            path_rect = QtCore.QRectF(vr.left(), y, fm.horizontalAdvance(v), lh)
        if draw:
            p.setFont(f)
            p.setPen(QtGui.QColor(ui.DIM))
            p.drawText(QtCore.QRectF(box.left() + pad, y, lw, lh), QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, k)
            if k == "Name":
                p.setFont(fb)
            p.setPen(QtGui.QColor(ui.ACCENT if k == "Path" else ui.TEXT))
            p.drawText(vr, QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, v)
        y += lh
    if draw:
        p.setOpacity(1.0)
    return box, path_rect


class InfoPopup(QtWidgets.QWidget):
    """Плашка инфо слоя — всплывает над кнопкой ⓘ на полоске, в том же виде, что инфо кадра.
    Клик мимо или по той же кнопке — закрывается; клик по пути — папка с файлом."""
    closed = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent, QtCore.Qt.Popup | QtCore.Qt.FramelessWindowHint | QtCore.Qt.NoDropShadowWindowHint)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground)
        self.setAttribute(QtCore.Qt.WA_NoMouseReplay)  # клик по той же ⓘ только закрывает
        self.setMouseTracking(True)
        self.rows, self.path, self._path_rect = [], "", None

    def show_rows(self, rows, path, anchor):
        """anchor — глобальная точка над кнопкой: левый нижний угол плашки."""
        self.rows, self.path = rows, path
        box, _ = paint_info_box(None, rows, QtCore.QPointF(0, 0), self.font(), 640, draw=False)
        self.resize(int(box.width()) + 1, int(box.height()) + 1)
        scr = (QtGui.QGuiApplication.screenAt(anchor) or QtGui.QGuiApplication.primaryScreen()).availableGeometry()
        x = max(scr.left() + 4, min(anchor.x(), scr.right() - self.width() - 4))
        y = anchor.y() - self.height()
        if y < scr.top() + 4:
            y = anchor.y() + 24  # над кнопкой не влезло — под ней
        self.move(x, y)
        self.show()

    def paintEvent(self, e):
        p = QtGui.QPainter(self)
        _, self._path_rect = paint_info_box(p, self.rows, QtCore.QPointF(0, 0), self.font(), 640, bg_alpha=235)
        p.end()

    def mouseMoveEvent(self, e):
        on = bool(self.path and self._path_rect and self._path_rect.contains(e.position()))
        self.setCursor(QtCore.Qt.PointingHandCursor if on else QtCore.Qt.ArrowCursor)
        self.setToolTip("Show in folder" if on else "")

    def mousePressEvent(self, e):
        if self.path and self._path_rect and self._path_rect.contains(e.position()):
            ui.open_url(QtCore.QUrl.fromLocalFile(self.path))
        self.close()

    def hideEvent(self, e):
        super().hideEvent(e)
        self.closed.emit()


def popup_above(menu, anchor):
    """Меню над точкой anchor (верх кнопки); не влезает сверху — Qt сам опустит."""
    menu.ensurePolished()
    h = menu.sizeHint().height()
    menu.exec(QtCore.QPoint(anchor.x(), anchor.y() - h))


def clear_focus():
    """Снять фокус с полей ввода, чтобы горячие клавиши снова шли плееру."""
    fw = QtWidgets.QApplication.focusWidget()
    if fw is not None:
        fw.clearFocus()


def notes_html(md):
    """Простой Markdown (# ## - [text](url)) → HTML в стиле приложения."""
    import html as _h
    import re

    def inline(t):
        t = _h.escape(t)
        return re.sub(r"\[([^\]]+)\]\(([^)]+)\)",
                      lambda m: f"<a href='{m.group(2)}' style='color:{ui.ACCENT}; text-decoration:none'>{m.group(1)}</a>", t)
    out, in_list = [], False
    for line in md.splitlines():
        if line.startswith("- "):
            if not in_list:
                out.append("<ul style='margin:0 0 0 -18px'>")
                in_list = True
            out.append(f"<li style='margin-bottom:4px'>{inline(line[2:])}</li>")
            continue
        if in_list:
            out.append("</ul>")
            in_list = False
        if line.startswith("## "):
            out.append(f"<p style='font-size:14px; font-weight:600; color:{ui.ACCENT}; margin:18px 0 6px 0'>{inline(line[3:])}</p>")
        elif line.startswith("# "):
            out.append(f"<p style='font-size:22px; font-weight:700; color:{ui.TEXT}; margin:0 0 8px 0'>{inline(line[2:])}</p>")
        elif line.strip():
            out.append(f"<p style='color:{ui.DIM}; margin:0 0 6px 0'>{inline(line)}</p>")
    if in_list:
        out.append("</ul>")
    return "".join(out)


class NoteBubble(QtWidgets.QFrame):
    """Редактор комментария прямо на кадре, у его номера.
    Enter — сохранить, Shift+Enter — новая строка, Esc — отменить правку."""
    changed = QtCore.Signal(object)
    done = QtCore.Signal(object)
    entered = QtCore.Signal(object)  # закончили Enter-ом — комментарий готов, новые штрихи уже не в него
    W = 300

    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName("noteBubble")
        self.note = None
        self.orig = ""
        self.setStyleSheet(
            f"#noteBubble {{ background: rgba(18,18,18,215); border: 2px solid {ui.ACCENT};"
            f" border-radius: {ui.RADIUS_CARD}px; }}"
            "QLabel { background: transparent; border: none; }")
        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(12, 9, 12, 8)
        v.setSpacing(4)
        self.head = QtWidgets.QLabel()
        self.head.setStyleSheet(f"color:{ui.NOTE}; font-size:12px; font-weight:600;")
        self.edit = QtWidgets.QTextEdit()
        self.edit.setAcceptRichText(False)
        self.edit.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.edit.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        self.edit.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.edit.setPlaceholderText("Type a comment…")
        self.edit.setStyleSheet(f"background: transparent; border: none; padding: 0; color: {ui.TEXT}; font-size: 14px;")
        self.edit.document().setDocumentMargin(1)
        key = f"<span style='color:{ui.TEXT}; font-weight:600'>{{}}</span>"
        self.hint = QtWidgets.QLabel("&nbsp;&nbsp;·&nbsp;&nbsp;".join(
            f"{key.format(k)} {t}" for k, t in (("Enter", "save"), ("Shift+Enter", "new line"), ("Esc", "cancel"))))
        self.hint.setTextFormat(QtCore.Qt.RichText)
        self.hint.setStyleSheet(f"color:{ui.DIM}; font-size:11px;")
        for w in (self.head, self.edit, self.hint):
            v.addWidget(w)
        self.setFixedWidth(self.W)
        self.edit.installEventFilter(self)
        self.edit.textChanged.connect(self._changed)
        self.hide()

    def open(self, note, num):
        if self.note is not None and self.note is not note:
            self.close_edit()
        self.note, self.orig = note, note.text
        self.head.setText("  ·  ".join(x for x in (f"{num}", note.author, f"frame {note.number}") if x))
        self.edit.blockSignals(True)
        self.edit.setPlainText(note.text)
        self.edit.blockSignals(False)
        self.show()
        self.raise_()
        self._fit()
        self.edit.setFocus()
        self.edit.moveCursor(QtGui.QTextCursor.End)

    def _fit(self):
        doc = self.edit.document()
        doc.setTextWidth(self.W - 24)
        self.edit.setFixedHeight(int(max(20, min(220, doc.size().height() + 2))))
        self.adjustSize()

    def _changed(self):
        if self.note is None:
            return
        self.note.text = self.edit.toPlainText()
        self._fit()
        self.changed.emit(self.note)

    def close_edit(self, cancel=False):
        n = self.note
        if n is None:
            return
        self.note = None  # до hide(): уход фокуса не должен закрывать второй раз
        if cancel and n.text != self.orig:
            n.text = self.orig
            self.changed.emit(n)
        self.hide()
        self.done.emit(n)

    def eventFilter(self, obj, e):
        if obj is self.edit and self.note is not None:
            if e.type() == QtCore.QEvent.KeyPress:
                enter = e.key() in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter)
                if e.key() == QtCore.Qt.Key_Escape:
                    self.close_edit(cancel=True)
                    return True
                if enter and not e.modifiers() & QtCore.Qt.ShiftModifier:
                    n = self.note
                    self.close_edit()
                    self.entered.emit(n)
                    return True
            elif e.type() == QtCore.QEvent.FocusOut:
                self.close_edit()
        return False


STICKER_EXT = (".svg", ".png", ".webp", ".jpg", ".jpeg")


def sticker_dirs():
    """Набор плеера (внутри сборки / рядом с кодом) и своя папка пользователя — в неё можно докладывать."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    mine = os.path.join(QtCore.QStandardPaths.writableLocation(QtCore.QStandardPaths.AppDataLocation), "stickers")
    return [os.path.join(base, "stickers"), mine]


def list_stickers():
    """[(имя, путь)] всех стикеров: сначала набор плеера, потом свои."""
    out, seen = [], set()
    for d in sticker_dirs():
        try:
            names = sorted(os.listdir(d), key=str.lower)
        except OSError:
            continue
        for f in names:
            stem, ext = os.path.splitext(f)
            if ext.lower() in STICKER_EXT and stem.lower() not in seen:
                seen.add(stem.lower())
                out.append((stem.replace("_", " "), os.path.join(d, f)))
    return out


_STICKER_IMG = {}


def sticker_image(path, px=512):
    """Стикер картинкой с прозрачным фоном, длинная сторона — px (SVG рисуем в этом размере, чётко)."""
    key = (path, px)
    if key not in _STICKER_IMG:
        if path.lower().endswith(".svg"):
            r = QtSvg.QSvgRenderer(path)
            sz = r.defaultSize()
            if not r.isValid() or sz.isEmpty():
                return None
            k = px / max(sz.width(), sz.height())
            img = QtGui.QImage(max(1, round(sz.width() * k)), max(1, round(sz.height() * k)),
                               QtGui.QImage.Format_ARGB32_Premultiplied)
            img.fill(QtCore.Qt.transparent)
            p = QtGui.QPainter(img)
            p.setRenderHints(QtGui.QPainter.Antialiasing | QtGui.QPainter.SmoothPixmapTransform)
            r.render(p)
            p.end()
        else:
            img = QtGui.QImage(path)
            if img.isNull():
                return None
            img = img.scaled(px, px, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
        _STICKER_IMG[key] = img.convertToFormat(QtGui.QImage.Format_ARGB32)
    return _STICKER_IMG[key]


class StickerPicker(QtWidgets.QFrame):
    """Всплывающая сетка стикеров под кнопкой-звездой."""
    picked = QtCore.Signal(str)
    COLS, CELL = 5, 64

    def __init__(self, parent, current=None):
        super().__init__(parent, QtCore.Qt.Popup | QtCore.Qt.FramelessWindowHint)
        self.setAttribute(QtCore.Qt.WA_DeleteOnClose)
        self.setObjectName("stickerPicker")
        self.setStyleSheet(f"#stickerPicker {{ background: {ui.SURFACE}; border: 1px solid {ui.BORDER_HI};"
                           f" border-radius: {ui.RADIUS_CARD}px; }}"
                           f" QToolButton {{ background: transparent; border: 1px solid transparent; border-radius: 10px; }}"
                           f" QToolButton:hover {{ background: {ui.CONTROL}; }}"
                           f" QToolButton:checked {{ background: {ui.ACCENT_TINT}; border-color: {ui.ACCENT_LINE}; }}")
        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(10, 10, 10, 8)
        v.setSpacing(6)
        grid = QtWidgets.QGridLayout()
        grid.setSpacing(4)
        items = list_stickers()
        for k, (name, path) in enumerate(items):
            img = sticker_image(path, 128)
            if img is None:
                continue
            b = QtWidgets.QToolButton()
            b.setCheckable(True)
            b.setChecked(path == current)
            b.setIcon(QtGui.QIcon(QtGui.QPixmap.fromImage(img)))
            b.setIconSize(QtCore.QSize(self.CELL - 12, self.CELL - 12))
            b.setFixedSize(self.CELL, self.CELL)
            b.setToolTip(name)
            b.setFocusPolicy(QtCore.Qt.NoFocus)
            b.clicked.connect(lambda _=False, p_=path: (self.picked.emit(p_), self.close()))
            grid.addWidget(b, k // self.COLS, k % self.COLS)
        v.addLayout(grid)
        if not items:
            v.addWidget(QtWidgets.QLabel("No stickers yet"))
        more = QtWidgets.QPushButton("Open my stickers folder…")
        ui.small(more)
        more.setToolTip("Put SVG or PNG files there — they show up here next time")
        more.clicked.connect(self._open_folder)
        v.addWidget(more)

    def _open_folder(self):
        d = sticker_dirs()[1]
        os.makedirs(d, exist_ok=True)
        ui.open_url(QtCore.QUrl.fromLocalFile(d))
        self.close()


class WinTab:
    """Нажим пера через Wintab (драйвер Wacom) напрямую. Qt 6 на Windows берёт перо только через
    Windows Ink; если в настройках Wacom Windows Ink выключен (так часто делают ради Photoshop/Nuke),
    Qt видит перо как мышь и нажима нет. Тогда читаем очередь пакетов Wintab сами."""
    PK_BUTTONS, PK_NORMAL_PRESSURE = 0x0040, 0x0400
    WTI_DEFSYSCTX, WTI_DEVICES, DVC_NPRESSURE = 4, 100, 15

    def __init__(self, hwnd):
        import ctypes
        from ctypes import wintypes as W
        self.ct = ctypes
        self.ctx = None
        self.max_p = 0
        dll = ctypes.WinDLL(os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "wintab32.dll"))
        self.dll = dll

        class LOGCONTEXTW(ctypes.Structure):
            _fields_ = [("lcName", W.WCHAR * 40)] + [(n, W.UINT) for n in (
                "lcOptions", "lcStatus", "lcLocks", "lcMsgBase", "lcDevice", "lcPktRate")] + [(n, W.DWORD) for n in (
                "lcPktData", "lcPktMode", "lcMoveMask", "lcBtnDnMask", "lcBtnUpMask")] + [(n, W.LONG) for n in (
                "lcInOrgX", "lcInOrgY", "lcInOrgZ", "lcInExtX", "lcInExtY", "lcInExtZ",
                "lcOutOrgX", "lcOutOrgY", "lcOutOrgZ", "lcOutExtX", "lcOutExtY", "lcOutExtZ")] + [
                (n, W.DWORD) for n in ("lcSensX", "lcSensY", "lcSensZ")] + [("lcSysMode", W.BOOL)] + [
                (n, ctypes.c_int) for n in ("lcSysOrgX", "lcSysOrgY", "lcSysExtX", "lcSysExtY")] + [
                (n, W.DWORD) for n in ("lcSysSensX", "lcSysSensY")]

        class AXIS(ctypes.Structure):
            _fields_ = [("axMin", W.LONG), ("axMax", W.LONG), ("axUnits", W.UINT), ("axResolution", W.DWORD)]

        class PACKET(ctypes.Structure):  # поля в порядке битов PK_*: кнопки, затем нажим
            _fields_ = [("buttons", W.DWORD), ("pressure", W.UINT)]

        self.PACKET = PACKET
        dll.WTInfoW.argtypes = [W.UINT, W.UINT, ctypes.c_void_p]
        dll.WTInfoW.restype = W.UINT
        dll.WTOpenW.argtypes = [W.HWND, ctypes.c_void_p, W.BOOL]
        dll.WTOpenW.restype = W.HANDLE
        dll.WTPacketsGet.argtypes = [W.HANDLE, ctypes.c_int, ctypes.c_void_p]
        dll.WTPacketsGet.restype = ctypes.c_int
        dll.WTQueueSizeSet.argtypes = [W.HANDLE, ctypes.c_int]
        dll.WTOverlap.argtypes = [W.HANDLE, W.BOOL]
        dll.WTClose.argtypes = [W.HANDLE]
        if not dll.WTInfoW(0, 0, None):
            raise OSError("wintab: no tablet service")
        ax = AXIS()
        dll.WTInfoW(self.WTI_DEVICES, self.DVC_NPRESSURE, ctypes.byref(ax))
        self.max_p = max(1, ax.axMax)
        lc = LOGCONTEXTW()
        dll.WTInfoW(self.WTI_DEFSYSCTX, 0, ctypes.byref(lc))
        lc.lcName = "MOOOV_player"
        lc.lcPktData = self.PK_BUTTONS | self.PK_NORMAL_PRESSURE
        lc.lcPktMode = 0
        lc.lcMoveMask = self.PK_BUTTONS | self.PK_NORMAL_PRESSURE
        lc.lcBtnUpMask = lc.lcBtnDnMask
        self.ctx = dll.WTOpenW(hwnd, ctypes.byref(lc), True)
        if not self.ctx:
            raise OSError("wintab: WTOpen failed")
        for n in (256, 128, 64, 32):
            if dll.WTQueueSizeSet(self.ctx, n):
                break
        self.buf = (PACKET * 256)()

    def pressure(self):
        """Нажим последнего пакета 0..1, None — пакетов нет (мышь, перо далеко)."""
        if not self.ctx:
            return None
        self.dll.WTOverlap(self.ctx, True)  # контекст наверх — пакеты идут нам
        last = None
        while True:  # выбрать очередь до конца — нужен самый свежий пакет
            n = self.dll.WTPacketsGet(self.ctx, len(self.buf), self.buf)
            if n <= 0:
                break
            last = self.buf[n - 1].pressure
            if n < len(self.buf):
                break
        if not last:
            return None
        return min(1.0, last / self.max_p)

    def flush(self):
        if self.ctx:
            self.dll.WTPacketsGet(self.ctx, len(self.buf), None)

    def close(self):
        if self.ctx:
            self.dll.WTClose(self.ctx)
            self.ctx = None


_WINTAB = None  # WinTab | False (нет драйвера) | None (не пробовали)


def wintab(widget):
    global _WINTAB
    if _WINTAB is None:
        _WINTAB = False
        if sys.platform == "win32" and os.environ.get("MOOOV_WINTAB", "1") != "0":
            try:
                _WINTAB = WinTab(int(widget.window().winId()))
            except Exception as e:
                print("MOOOV_player: wintab off:", e)
    return _WINTAB or None


class Viewer(QOpenGLWidget):
    stroke_done = QtCore.Signal(object)
    erased = QtCore.Signal(object, object)  # ластик задел штрих: (нота, штрих)
    erase_done = QtCore.Signal()  # ластик отпустили — всё стёртое за одно движение отменяется разом
    badge_clicked = QtCore.Signal(object)  # клик по номеру комментария на кадре
    image_requested = QtCore.Signal(object)  # инструмент Image: клик по пустому месту — добавить картинку
    image_edited = QtCore.Signal(object)  # картинку подвинули / отмасштабировали (заметка)
    HANDLE = 7  # радиус, за который хватаются ручки масштаба у выбранной картинки
    HANDLE_DRAW = 4.0  # а рисуются они меньше — аккуратные кружки
    BADGE_R = 8  # номер на кадре — маленький и полупрозрачный, ярче при наведении

    def __init__(self):
        super().__init__()
        self.img = None
        self.error = None
        self.fit = True
        self.zoom = 1.0  # пикселей изображения на физический пиксель экрана
        self.pan = QtCore.QPointF(0, 0)
        self.smooth = True
        self._drag = None
        # рисование
        self.tool = None  # None | pen | arrow | rect | ellipse | image
        self.sel_img = None  # выбранная картинка-референс (Stroke)
        # слои поверх кадра (Layer: img, opacity, mode, visible, split) снизу вверх; шторка — для слоёв со split
        # (точка на линии в долях кадра + угол в градусах, 0 — вертикаль)
        self.layers = []
        self.base_name = ""
        self.base_look = None  # Layer-вид A (видимость, режим, прозрачность) или None — как есть
        self._comp = None  # (ключ, QImage) — собранный кадр, пересобираем только при изменениях
        self.wipe = [0.5, 0.5]
        self.wipe_ang = 0.0
        self._wipe_drag = None  # "move" | "rotate"
        self._rot_sign = -1  # какую из двух ручек поворота тянут
        self._wipe_hover = False  # мышь над ручкой — она непрозрачная
        self._img_drag = None  # (режим, нота, штрих, старт, исходные углы)
        self.color = ann.COLORS[0]
        self.stroke_w = 5 / 1080
        self.alpha = 1.0  # непрозрачность новых штрихов
        self.overlay = []  # [(note, opacity, num)]
        self.matte = None  # соотношение сторон каше или None
        self.matte_alpha = 1.0
        self.live = None
        self._pen_p = None  # нажим пера (Wacom и др.), пока перо касается; None — мышь
        self._wt_p = None  # то же через Wintab, если Qt нажима не даёт
        self._erasing = False  # ластик прижат
        self._eraser_pos = None  # где кружок ластика (под мышью)
        self._nav = None  # ("pan" | "zoom", точка, исходный масштаб) — Shift/Ctrl + тянуть
        self._last_wp = None
        self.info = None  # [(label, value)] — плашка с информацией о файле
        self.info_path = None  # папка, открывается кликом по строке Path
        self._path_rect = None
        self._site_rect = None  # ссылка mooov.team на пустом экране
        self._badges = []  # [(центр, заметка, номер)] — для наведения
        self.hover_note = None
        self.setMouseTracking(True)
        self.setMinimumSize(320, 180)
        self.setFocusPolicy(QtCore.Qt.NoFocus)
        self.bubble = NoteBubble(self)  # редактор комментария на кадре

    def set_tool(self, tool):
        self.tool = tool
        self._eraser_pos = None
        if tool not in ("image", "sticker"):
            self.sel_img = None
        self.setCursor(QtCore.Qt.CrossCursor if tool else QtCore.Qt.ArrowCursor)
        self.update()

    # ---- слои: сборка кадра и шторка
    def set_layers(self, layers, base_name=None, base_look=None):
        self.layers = layers
        self.base_look = base_look
        if base_name is not None:
            self.base_name = base_name
        self.update()

    def split_layer(self):
        """Верхний видимый слой со шторкой (его имя — справа вверху) или None."""
        return next((L for L in reversed(self.layers) if L.visible and L.split and L.img is not None), None)

    def frame_image(self):
        """Кадр A со всеми видимыми слоями поверх (в пикселях A). Без слоёв — сам кадр."""
        live = [L for L in self.layers if L.visible and L.img is not None and L.opacity > 0]
        B = self.base_look
        look = (B.visible, B.opacity, B.mode) if B else (True, 1.0, "Normal")
        if (not live and look == (True, 1.0, "Normal")) or self.img is None:
            return self.img
        a = self.img
        key = (a.cacheKey(), look, tuple((L.img.cacheKey(), L.opacity, L.mode, L.split) for L in live),
               (tuple(self.wipe), self.wipe_ang) if any(L.split for L in live) else None)
        if self._comp and self._comp[0] == key:
            return self._comp[1]
        W, H = a.width(), a.height()
        out = QtGui.QImage(W, H, QtGui.QImage.Format_ARGB32_Premultiplied)
        out.fill(QtCore.Qt.transparent)  # под A — фон вьюера
        p = QtGui.QPainter(out)
        if look[0] and look[1] > 0:
            p.setOpacity(look[1])
            p.setCompositionMode(BLEND_MODES.get(look[2], QtGui.QPainter.CompositionMode_SourceOver))
            p.drawImage(0, 0, a)
            p.setOpacity(1.0)
        p.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        full = QtCore.QRectF(0, 0, W, H)
        for L in live:
            b = L.img
            sc = min(W / b.width(), H / b.height())  # слой вписан в кадр A, пропорции свои
            bw, bh = b.width() * sc, b.height() * sc
            br = QtCore.QRectF((W - bw) / 2, (H - bh) / 2, bw, bh)
            p.save()
            if L.split:
                p.setClipPath(self._half_plane(full))
            normal = L.mode == "Normal"
            if L.split and normal and L.opacity >= 1 and br != full:
                p.fillRect(full, QtGui.QColor(ui.BG))  # сравнение: вокруг слоя другой формы — пусто, не A
            p.setOpacity(L.opacity)
            p.setCompositionMode(BLEND_MODES.get(L.mode, QtGui.QPainter.CompositionMode_SourceOver))
            p.drawImage(br, b)
            p.restore()
        p.end()
        self._comp = (key, out)
        return out

    def _half_plane(self, t):
        P, u, n = self._wipe_geo(t)
        L = (t.width() + t.height()) * 2
        half = QtGui.QPainterPath()
        half.addPolygon(QtGui.QPolygonF([P - u * L, P + u * L, P + u * L + n * L, P - u * L + n * L]))
        half.closeSubpath()
        clip = QtGui.QPainterPath()
        clip.addRect(t)
        return clip.intersected(half)

    def reset_wipe(self):
        self.wipe, self.wipe_ang = [0.5, 0.5], 0.0
        self.update()

    def _wipe_geo(self, t):
        """(точка на линии, направление вдоль линии u, нормаль n в сторону B)."""
        a = math.radians(self.wipe_ang)
        P = QtCore.QPointF(t.x() + self.wipe[0] * t.width(), t.y() + self.wipe[1] * t.height())
        return P, QtCore.QPointF(math.sin(a), math.cos(a)), QtCore.QPointF(math.cos(a), -math.sin(a))

    def _wipe_knobs(self, t):
        """Центр (тянуть) и две ручки поворота по обе стороны на линии среза."""
        P, u, n = self._wipe_geo(t)
        R = 34.0
        return P, (P - u * R, P + u * R)

    def _paint_wipe(self, p, t):
        """Без линии и теней: срез виден по самой картинке. Жёлтая ручка с ◀ ▶ и две точки поворота по бокам."""
        P, u, n = self._wipe_geo(t)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        c, rots = self._wipe_knobs(t)
        hot = self._wipe_drag
        p.save()
        p.setOpacity(1.0 if hot or self._wipe_hover else 0.4)  # не мешает смотреть, пока не навели

        for q in rots:
            p.setPen(QtGui.QPen(QtGui.QColor(0, 0, 0, 90), 1))
            p.setBrush(QtGui.QColor(ui.ACCENT if hot == "rotate" else ui.TEXT))
            p.drawEllipse(q, 5, 5)
        R = 15
        p.setPen(QtGui.QPen(QtGui.QColor(0, 0, 0, 70), 1))
        p.setBrush(QtGui.QColor(ui.ACCENT))
        p.drawEllipse(c, R, R)
        # ◀ ▶ — залитые треугольники поперёк среза, читаются под любым углом
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(QtGui.QColor(ui.ON_ACCENT))
        p.save()
        p.translate(c)
        p.rotate(-self.wipe_ang)  # рисуем ◀ ▶ как для вертикали и поворачиваем вместе со срезом
        for sgn in (-1, 1):
            p.drawPolygon(QtGui.QPolygonF([QtCore.QPointF(9.5 * sgn, 0), QtCore.QPointF(3 * sgn, -4.5),
                                           QtCore.QPointF(3 * sgn, 4.5)]))
        p.restore()
        p.restore()
        # подписи A / B по верхним углам кадра
        f = QtGui.QFont(self.font())
        f.setPixelSize(11)
        f.setBold(True)
        p.setFont(f)
        fm = QtGui.QFontMetricsF(f)
        top = self.split_layer()
        names = (self.base_name, top.name if top else "")
        tags = ("A", top.tag if top else "B")
        for k, (tag, name) in enumerate(zip(tags, names)):
            txt = f"{tag}  {fm.elidedText(name, QtCore.Qt.ElideMiddle, min(260, t.width() * 0.35))}"
            w = fm.horizontalAdvance(txt) + 20
            x = max(t.left(), 0) + 10 if k == 0 else min(t.right(), self.width()) - 10 - w
            r = QtCore.QRectF(x, max(t.top(), 0) + 10, w, 22)
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(QtGui.QColor(18, 18, 18, 200))
            p.drawRoundedRect(r, 11, 11)
            p.setPen(QtGui.QColor(ui.TEXT))
            p.drawText(r, QtCore.Qt.AlignCenter, txt)

    def _wipe_hit(self, pos):
        if self.split_layer() is None or self.img is None or self.tool:
            return None
        t = self._target()
        c, rots = self._wipe_knobs(t)
        if QtCore.QLineF(c, pos).length() <= 17:
            return "move"
        if any(QtCore.QLineF(q, pos).length() <= 10 for q in rots):
            return "rotate"
        if QtCore.QLineF(c, pos).length() <= 17:
            return "move"
        P, u, n = self._wipe_geo(t)
        d = pos - P
        if abs(d.x() * n.x() + d.y() * n.y()) <= 6 and t.contains(pos):
            return "move"
        return None

    def _drag_wipe(self, pos, shift):
        t = self._target()
        if self._wipe_drag == "move":
            # ручка идёт за мышью в любую сторону: вбок двигает срез, вдоль среза — саму ручку
            q = QtCore.QPointF(min(max(pos.x(), t.left()), t.right()), min(max(pos.y(), t.top()), t.bottom()))
            self.wipe = [(q.x() - t.x()) / t.width(), (q.y() - t.y()) / t.height()]
        else:
            P, _, _ = self._wipe_geo(t)
            v = pos - P
            if v.manhattanLength() < 4:
                return
            if self._rot_sign > 0:  # тянут нижнюю ручку — она смотрит по +u
                v = -v
            a = math.degrees(math.atan2(-v.x(), -v.y()))
            if shift or min(abs(a - k) for k in (-180, -90, 0, 90, 180)) < 4:  # Shift — шаг 15°, у осей — прилипание
                a = round(a / (15 if shift else 90)) * (15 if shift else 90)
            self.wipe_ang = ((a + 180) % 360) - 180
        self.update()

    # ---- картинки-референсы: выбор, перенос, масштаб
    def images_here(self):
        """[(нота, штрих)] картинок на текущем кадре (не призраки), верхние — последними."""
        return [(n, s) for n, op, _ in self.overlay if op >= 0.999 for s in n.strokes if s.tool == "image"]

    ERASE_R = 10  # радиус ластика в пикселях экрана

    def _stroke_hit(self, s, t, pos):
        """Задевает ли кружок ластика в pos штрих s (на кадре в рамке t)."""
        if s.tool == "image":
            return ann.image_rect(s, t).adjusted(-self.ERASE_R, -self.ERASE_R, self.ERASE_R, self.ERASE_R).contains(pos)
        pts = [ann._map(t, q) for q in s.pts]
        if not pts:
            return False
        w = max(1.0, s.width * t.height())
        if len(pts) == 1 or (s.tool == "pen" and all(q == pts[0] for q in pts)):
            return QtCore.QLineF(pts[0], pos).length() <= self.ERASE_R + w / 2
        path = QtGui.QPainterPath()
        if s.tool in ("rect", "ellipse"):
            r = QtCore.QRectF(pts[0], pts[-1]).normalized()
            path.addRect(r) if s.tool == "rect" else path.addEllipse(r)
        elif s.tool == "arrow":
            path.moveTo(pts[0])
            path.lineTo(pts[-1])
        else:
            path.moveTo(pts[0])
            for q in pts[1:]:
                path.lineTo(q)
        st = QtGui.QPainterPathStroker()
        st.setWidth(w + 2 * self.ERASE_R)
        st.setCapStyle(QtCore.Qt.RoundCap)
        st.setJoinStyle(QtCore.Qt.RoundJoin)
        return st.createStroke(path).contains(pos)

    def _erase_at(self, pos):
        """Стереть штрихи текущего кадра (не призраки соседних) под кружком ластика."""
        if self.img is None:
            return
        t = self._target()
        for note, op, _ in list(self.overlay):
            if op < 0.999:
                continue
            for s in list(note.strokes):
                if self._stroke_hit(s, t, pos):
                    self.erased.emit(note, s)

    def _img_hit(self, pos):
        """('scale', нота, штрих, индекс угла) | ('move', нота, штрих, None) | None."""
        if self.img is None:
            return None
        t = self._target()
        here = self.images_here()
        for n, s in here:  # сначала ручки выбранной
            if s is self.sel_img:
                for k, c in enumerate(self._corners(ann.image_rect(s, t))):
                    if QtCore.QLineF(c, pos).length() <= self.HANDLE + 4:
                        return ("scale", n, s, k)
        for n, s in reversed(here):
            if ann.image_rect(s, t).contains(pos):
                return ("move", n, s, None)
        return None

    @staticmethod
    def _corners(r):
        return [r.topLeft(), r.topRight(), r.bottomRight(), r.bottomLeft()]

    def _drag_image(self, pos):
        mode, n, s, k, start, orig = self._img_drag
        t = self._target()
        (x0, y0), (x1, y1) = orig
        if mode == "move":
            dx = (pos.x() - start.x()) / t.width()
            dy = (pos.y() - start.y()) / t.height()
            s.pts = [[x0 + dx, y0 + dy], [x1 + dx, y1 + dy]]
            return
        # масштаб за угол k, противоположный угол стоит на месте, пропорции сохраняются
        xs, ys = (x0, x1, x1, x0), (y0, y0, y1, y1)
        ax, ay = xs[(k + 2) % 4], ys[(k + 2) % 4]
        ap = QtCore.QPointF(t.x() + ax * t.width(), t.y() + ay * t.height())
        w0, h0 = abs(x1 - x0) * t.width(), abs(y1 - y0) * t.height()
        sx, sy = (1 if xs[k] > ax else -1), (1 if ys[k] > ay else -1)
        f = max((pos.x() - ap.x()) * sx / w0, (pos.y() - ap.y()) * sy / h0, 12 / min(w0, h0))
        bx = ax + sx * w0 * f / t.width()
        by = ay + sy * h0 * f / t.height()
        s.pts = [[min(ax, bx), min(ay, by)], [max(ax, bx), max(ay, by)]]

    def _paint_selection(self, p, t):
        if self.tool not in ("image", "sticker") or self.sel_img is None:
            return
        if not any(s is self.sel_img for _, s in self.images_here()):
            return
        r = ann.image_rect(self.sel_img, t)
        acc = QtGui.QColor(ui.ACCENT)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        # тонкая рамка точно по пикселям экрана (полпикселя — линия не размывается на два)
        dpr = self.devicePixelRatioF()
        snap = lambda v: (round(v * dpr) + 0.5) / dpr
        rr = QtCore.QRectF(QtCore.QPointF(snap(r.left()), snap(r.top())), QtCore.QPointF(snap(r.right()), snap(r.bottom())))
        p.setPen(QtGui.QPen(acc, 1.0 / dpr * max(1, round(dpr))))
        p.setBrush(QtCore.Qt.NoBrush)
        p.drawRect(rr)
        # ручки — небольшие кружки: тёмная обводка, жёлтая заливка (хватать можно с запасом — HANDLE)
        p.setPen(QtGui.QPen(QtGui.QColor(0, 0, 0, 150), 1.0))
        p.setBrush(acc)
        for c in self._corners(rr):
            p.drawEllipse(c, self.HANDLE_DRAW, self.HANDLE_DRAW)

    def _norm(self, pos):
        t = self._target()
        return [(pos.x() - t.x()) / t.width(), (pos.y() - t.y()) / t.height()]

    def set_image(self, img):
        if isinstance(img, Exception):
            self.error, img = str(img), None
        else:
            self.error = None
        if img is not None:
            self.img = img
        self.update()

    def _scale(self):
        if self.img is None:
            return 1.0
        if self.fit:
            return min(self.width() / self.img.width(), self.height() / self.img.height())
        return self.zoom / self.devicePixelRatioF()

    def _target(self):
        s = self._scale()
        w, h = self.img.width() * s, self.img.height() * s
        cx = self.width() / 2 + (0 if self.fit else self.pan.x())
        cy = self.height() / 2 + (0 if self.fit else self.pan.y())
        return QtCore.QRectF(cx - w / 2, cy - h / 2, w, h)

    def paintGL(self):
        p = QtGui.QPainter(self)
        p.fillRect(self.rect(), QtGui.QColor(ui.BG))
        if self.img is not None:
            p.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, self.smooth and self._scale() < 4)
            t = self._target()
            p.drawImage(t, self.frame_image())
            if self.matte:
                self._paint_matte(p, t)
            p.setClipRect(t)
            self._badges = []
            for note, op, num in self.overlay:
                hot = note is self.hover_note or note is self.bubble.note
                c = ann.paint_note(p, note, t, op, num, badge_r=self.BADGE_R + (1 if hot else 0),
                                   badge_op=1.0 if hot else 0.55)
                if c is not None:
                    self._badges.append((c, note, num))
            if self.live:
                ann.paint_stroke(p, self.live, t)
            self._paint_selection(p, t)
            if self.tool == "eraser" and self._eraser_pos is not None:  # кружок ластика
                p.setClipping(False)
                p.setRenderHint(QtGui.QPainter.Antialiasing, True)
                p.setBrush(QtGui.QColor(255, 255, 255, 40 if self._erasing else 18))
                p.setPen(QtGui.QPen(QtGui.QColor(0, 0, 0, 160), 2.5))
                p.drawEllipse(self._eraser_pos, self.ERASE_R, self.ERASE_R)
                p.setPen(QtGui.QPen(QtGui.QColor(ui.TEXT), 1.2))
                p.drawEllipse(self._eraser_pos, self.ERASE_R, self.ERASE_R)
        if self.error:
            p.setPen(QtGui.QColor(ui.ERROR))
            p.drawText(self.rect().adjusted(12, 12, -12, -12), QtCore.Qt.AlignLeft | QtCore.Qt.AlignTop, self.error)
        elif self.img is None:
            # пустой экран: тихая M из логотипа + подсказка
            c = QtCore.QPointF(self.width() / 2, self.height() / 2 - 24)
            p.setRenderHint(QtGui.QPainter.Antialiasing, True)
            # логотип: M + треугольник Play, вместе по центру
            mw, mh = 80, 40
            gap = mw * ui.MARK_LEG  # отступ треугольника = ширина ножки M
            tw = mh * 0.866
            x0 = c.x() - mw / 2  # M ровно по центру, Play справа от неё
            col = QtGui.QColor(ui.HOVER)
            p.fillPath(ui.mark_path(QtCore.QRectF(x0, c.y() - mh / 2, mw, mh)), col)
            tri = QtGui.QPainterPath()
            tx = x0 + mw + gap
            tri.addPolygon(QtGui.QPolygonF([QtCore.QPointF(tx, c.y() - mh / 2), QtCore.QPointF(tx + tw, c.y()),
                                            QtCore.QPointF(tx, c.y() + mh / 2)]))
            tri.closeSubpath()
            p.fillPath(tri, col)
            p.setPen(QtGui.QColor(ui.DIM))
            p.drawText(QtCore.QRectF(0, c.y() + 36, self.width(), 40), QtCore.Qt.AlignHCenter | QtCore.Qt.AlignTop,
                       f"Drop a video, a sequence frame or a folder here\n{MOD}O to open")
            self._paint_credits(p)
        p.setClipping(False)
        if self.img is not None and self.split_layer() is not None:
            self._paint_wipe(p, self._target())
        if self.info and self.img is not None:
            self._paint_info(p)
        if self.hover_note is not None and self.img is not None and self.hover_note is not self.bubble.note:
            self._paint_note_tip(p)
        p.end()
        self._place_bubble()

    def _place_bubble(self):
        """Держать редактор у номера комментария (при зуме, пане, ресайзе)."""
        b = self.bubble
        c = next((c for c, n, _ in self._badges if n is b.note), None) if b.note is not None else None
        if c is None:
            return
        w, h = b.width(), b.sizeHint().height()
        x = c.x() + self.BADGE_R + 6
        if x + w > self.width() - 8:
            x = c.x() - self.BADGE_R - 6 - w
        x = max(8.0, x)
        y = max(8.0, min(c.y() - self.BADGE_R, self.height() - h - 8))
        pos = QtCore.QPoint(int(x), int(y))
        if b.pos() != pos:
            b.move(pos)

    def _paint_credits(self, p):
        """Внизу пустого экрана: версия, автор и ссылка на сайт компании."""
        f = QtGui.QFont(self.font())
        f.setPixelSize(12)
        p.setFont(f)
        fm = QtGui.QFontMetricsF(f)
        line1 = f"MOOOV_player {APP_VERSION}  ·  Developed by {AUTHOR} for "
        w1, w2 = fm.horizontalAdvance(line1), fm.horizontalAdvance(SITE)
        x = (self.width() - w1 - w2) / 2
        y = self.height() - 28 - fm.height()
        p.setPen(QtGui.QColor(ui.MUTED))
        p.drawText(QtCore.QPointF(x, y + fm.ascent()), line1)
        self._site_rect = QtCore.QRectF(x + w1, y - 4, w2, fm.height() + 8)
        p.setPen(QtGui.QColor(ui.ACCENT))
        p.drawText(QtCore.QPointF(x + w1, y + fm.ascent()), SITE)

    def _on_site(self, pos):
        return self.img is None and self._site_rect is not None and self._site_rect.contains(pos)

    def _badge_at(self, pos):
        for c, note, num in reversed(self._badges):
            d = c - pos
            if d.x() * d.x() + d.y() * d.y() <= (self.BADGE_R + 4) ** 2:
                return note
        return None

    def _paint_note_tip(self, p):
        """Всплывающий комментарий у номера — в стиле плашки Info."""
        hit = next(((c, n, num) for c, n, num in self._badges if n is self.hover_note), None)
        if hit is None:
            return
        c, note, num = hit
        f = QtGui.QFont(self.font())
        f.setPixelSize(11)
        fb = QtGui.QFont(self.font())
        fb.setPixelSize(13)
        head = "  ·  ".join(x for x in (f"{num}", note.author, f"frame {note.number}", ann.created_str(note)) if x)
        body = note.text.strip() or SKETCH_TEXT
        pad, maxw = 10, 320
        fmh, fmb = QtGui.QFontMetricsF(f), QtGui.QFontMetricsF(fb)
        br = fmb.boundingRect(QtCore.QRectF(0, 0, maxw, 2000), QtCore.Qt.TextWordWrap, body)
        w = min(maxw, max(fmh.horizontalAdvance(head), br.width())) + 2 * pad
        h = pad + fmh.height() + 4 + br.height() + pad
        x = c.x() + self.BADGE_R + 6
        y = c.y() - self.BADGE_R
        if x + w > self.width() - 8:  # не вылезать за край — тогда слева от номера
            x = c.x() - self.BADGE_R - 6 - w
        x = max(8.0, x)
        y = max(8.0, min(y, self.height() - h - 8))
        box = QtCore.QRectF(x, y, w, h)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        p.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 18), 1))
        bgc = QtGui.QColor(ui.BG)
        bgc.setAlpha(170)
        p.setBrush(bgc)
        p.drawRoundedRect(box, ui.RADIUS_CARD, ui.RADIUS_CARD)
        p.setFont(f)
        p.setPen(QtGui.QColor(ui.NOTE))
        p.drawText(QtCore.QRectF(x + pad, y + pad, w - 2 * pad, fmh.height()), QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter,
                   fmh.elidedText(head, QtCore.Qt.ElideRight, w - 2 * pad))
        p.setFont(fb)
        p.setPen(QtGui.QColor(ui.TEXT))
        p.drawText(QtCore.QRectF(x + pad, y + pad + fmh.height() + 4, w - 2 * pad, br.height() + 2),
                   QtCore.Qt.TextWordWrap, body)

    def _paint_info(self, p):
        """Полупрозрачная плашка в левом верхнем углу: подписи слева, значения справа."""
        _, self._path_rect = paint_info_box(p, self.info, QtCore.QPointF(12, 12), self.font(), self.width() - 24)

    def _on_path(self, pos):
        return bool(self.info and self._path_rect and self._path_rect.contains(pos))

    def _paint_matte(self, p, t):
        ann.paint_matte(p, t, self.matte, self.matte_alpha)

    def set_zoom(self, z, anchor=None):
        if self.img is None:
            return
        if self.fit:
            self.zoom = self._scale() * self.devicePixelRatioF()
            self.pan = QtCore.QPointF(0, 0)
            self.fit = False
        z = max(0.02, min(64.0, z))
        if anchor is not None:
            c = QtCore.QPointF(self.width() / 2, self.height() / 2) + self.pan
            self.pan += (anchor - c) * (1 - z / self.zoom)
        self.zoom = z
        self.update()

    def fit_view(self):
        self.fit = True
        self.update()

    def wheelEvent(self, e):
        d = e.angleDelta().y() or e.pixelDelta().y()
        if not d:
            return
        f = 1.0015 ** d
        base = self._scale() * self.devicePixelRatioF() if self.fit else self.zoom
        self.set_zoom(base * f, e.position())

    def tabletEvent(self, e):
        """Перо планшета: запоминаем нажим, а само рисование идёт через мышиные события,
        которые Qt делает из пера (событие не принимаем)."""
        t = e.type()
        if t == QtCore.QEvent.TabletPress:
            self._pen_p = e.pressure()
        elif t == QtCore.QEvent.TabletMove and self._pen_p is not None:
            self._pen_p = 0.5 * self._pen_p + 0.5 * e.pressure()  # без дрожи толщины
        elif t == QtCore.QEvent.TabletRelease:
            self._pen_p = None
        e.ignore()

    def _pen_pt(self, pos, start=False):
        q = self._norm(pos)
        if self.tool != "pen":
            return q
        p = self._pen_p
        if p is None:  # Qt нажима не дал (Windows Ink выключен) — спросить Wintab
            wt = wintab(self)
            if start:
                self._wt_p = wt.pressure() if wt else None
            elif wt:
                v = wt.pressure()
                if v is not None:
                    self._wt_p = v if self._wt_p is None else 0.5 * self._wt_p + 0.5 * v
            p = self._wt_p
        if p is not None:
            q.append(round(max(0.0, min(1.0, p)), 3))
        return q

    def mousePressEvent(self, e):
        clear_focus()
        self.bubble.close_edit()  # клик мимо редактора — сохранить и закрыть
        # навигация пером (и мышью) при любом инструменте: Shift+тянуть — двигать, Ctrl+тянуть — масштаб
        mods = e.modifiers()
        if (e.button() == QtCore.Qt.LeftButton and self.img is not None
                and mods & (QtCore.Qt.ShiftModifier | QtCore.Qt.ControlModifier)
                and not self._wipe_hit(e.position())):
            if mods & QtCore.Qt.ControlModifier:
                z0 = self._scale() * self.devicePixelRatioF() if self.fit else self.zoom
                self._nav = ("zoom", e.position(), z0)
                self.setCursor(QtCore.Qt.SizeHorCursor)
            else:
                self._nav = ("pan", e.position(), None)
                self.setCursor(QtCore.Qt.ClosedHandCursor)
            return
        if e.button() == QtCore.Qt.LeftButton and self._on_path(e.position()) and self.info_path:
            ui.open_url(QtCore.QUrl.fromLocalFile(self.info_path))
            return
        if e.button() == QtCore.Qt.LeftButton and self._on_site(e.position()):
            ui.open_url(QtCore.QUrl(SITE_URL))
            return
        if e.button() == QtCore.Qt.LeftButton and (w := self._wipe_hit(e.position())):
            self._wipe_drag = w
            _, rots = self._wipe_knobs(self._target())
            self._rot_sign = -1 if (QtCore.QLineF(rots[0], e.position()).length()
                                    <= QtCore.QLineF(rots[1], e.position()).length()) else 1
            self.update()
            return
        # номер комментария кликается всегда — и с включённым инструментом рисования
        if e.button() == QtCore.Qt.LeftButton and self._badge_at(e.position()) is not None:
            self.badge_clicked.emit(self._badge_at(e.position()))
            return
        if (self.tool in ("image", "sticker") and self.img is not None and e.button() == QtCore.Qt.LeftButton
                and not e.modifiers() & QtCore.Qt.AltModifier):
            hit = self._img_hit(e.position())
            if hit:
                mode, n, s, k = hit
                self.sel_img = s
                self._img_drag = (mode, n, s, k, e.position(), [list(q) for q in s.pts])
            else:
                self.sel_img = None
                self.image_requested.emit(self._norm(e.position()))
            self.update()
            return
        if (self.tool == "eraser" and self.img is not None and e.button() == QtCore.Qt.LeftButton
                and not e.modifiers() & QtCore.Qt.AltModifier):
            self._erasing = True
            self._eraser_pos = e.position()
            self._erase_at(e.position())
            self.update()
            return
        if (self.tool and self.img is not None and e.button() == QtCore.Qt.LeftButton
                and not e.modifiers() & QtCore.Qt.AltModifier):
            q = self._pen_pt(e.position(), start=True)
            pts = [q] if self.tool == "pen" else [q[:2], q[:2]]
            self.live = ann.Stroke(self.tool, self.color, self.stroke_w, pts, alpha=self.alpha)
            self._last_wp = e.position()
            self.update()
            return
        self._drag = e.position()

    def mouseMoveEvent(self, e):
        if self._nav is not None:
            mode, p0, z0 = self._nav
            if mode == "zoom":  # вправо/вверх — крупнее, точка нажатия стоит на месте
                d = (e.position().x() - p0.x()) - (e.position().y() - p0.y())
                self.set_zoom(z0 * 1.006 ** d, p0)
            else:
                if self.fit:
                    self.set_zoom(self._scale() * self.devicePixelRatioF())
                self.pan += e.position() - p0
                self._nav = (mode, e.position(), None)
                self.update()
            return
        if self._wipe_drag:
            self._drag_wipe(e.position(), bool(e.modifiers() & QtCore.Qt.ShiftModifier))
            return
        if self.tool == "eraser" and self._drag is None:
            if self._erasing:  # между событиями мыши — промежуточные точки, чтобы быстрый взмах не проскакивал
                a, b = self._eraser_pos or e.position(), e.position()
                steps = max(1, int(QtCore.QLineF(a, b).length() / (self.ERASE_R * 0.7)))
                for i in range(1, steps + 1):
                    self._erase_at(a + (b - a) * (i / steps))
            self._eraser_pos = e.position()
            self.setCursor(QtCore.Qt.BlankCursor if self.img is not None else QtCore.Qt.ArrowCursor)
            self.update()
            if self._erasing or self._badge_at(e.position()) is None:
                return
        if self._img_drag is not None:
            self._drag_image(e.position())
            self.update()
            return
        if self.tool in ("image", "sticker") and self._drag is None and self.img is not None:
            note = self._badge_at(e.position())
            if note is not self.hover_note:
                self.hover_note = note
                self.update()
            if note is not None:
                self.setCursor(QtCore.Qt.PointingHandCursor)
                self.setToolTip("")
                return
            hit = self._img_hit(e.position())
            if hit is None:
                self.setCursor(QtCore.Qt.CrossCursor)
                self.setToolTip("Click to put the sticker" if self.tool == "sticker" else "Click to add a reference image")
            elif hit[0] == "move":
                self.setCursor(QtCore.Qt.SizeAllCursor)
                self.setToolTip("")
            else:
                self.setCursor(QtCore.Qt.SizeFDiagCursor if hit[3] in (0, 2) else QtCore.Qt.SizeBDiagCursor)
                self.setToolTip("")
            return
        if not self.live and self._drag is None:
            if self.tool == "pen" and (wt := wintab(self)):
                wt.pressure()  # перо над кадром: старые пакеты из очереди вон, к нажатию она свежая
            wh = self._wipe_hit(e.position()) is not None
            if wh != self._wipe_hover:
                self._wipe_hover = wh
                self.update()
            note = self._badge_at(e.position())
            if note is not self.hover_note:
                self.hover_note = note
                self.update()
            if self._on_site(e.position()):
                self.setCursor(QtCore.Qt.PointingHandCursor)
                self.setToolTip(SITE_URL)
            elif self._on_path(e.position()):
                self.setCursor(QtCore.Qt.PointingHandCursor)
                self.setToolTip("Reveal in Finder" if sys.platform == "darwin" else "Show in Explorer")
            elif note is not None:
                self.setCursor(QtCore.Qt.PointingHandCursor)
                self.setToolTip("")
            elif (w := self._wipe_hit(e.position())):
                self.setCursor(QtCore.Qt.SplitHCursor if w == "move" and abs(self.wipe_ang) < 45
                               else QtCore.Qt.SizeAllCursor if w == "move" else QtCore.Qt.OpenHandCursor)
                self.setToolTip("Drag to move the split · double-click to reset" if w == "move"
                                else "Drag to rotate the split · Shift — 15° steps")
            else:
                self.setCursor(QtCore.Qt.CrossCursor if self.tool else QtCore.Qt.ArrowCursor)
                self.setToolTip("")
            return
        if self.live:
            if self.live.tool == "pen":
                d = e.position() - self._last_wp
                if abs(d.x()) + abs(d.y()) >= 2:
                    q = self._pen_pt(e.position())
                    if len(q) > 2 and len(self.live.pts[0]) == 2:  # нажим пришёл не с первой точки
                        for a in self.live.pts:
                            a.append(q[2])
                    self.live.pts.append(q)
                    self._last_wp = e.position()
            else:
                self.live.pts[-1] = self._norm(e.position())
            self.update()
            return
        if self._drag is not None and self.img is not None:
            if self.fit:
                self.set_zoom(self._scale() * self.devicePixelRatioF())
            self.pan += e.position() - self._drag
            self._drag = e.position()
            self.update()

    def mouseReleaseEvent(self, e):
        if self._erasing:
            self._erasing = False
            self.erase_done.emit()
            self.update()
            return
        if self._nav is not None:
            self._nav = None
            self.setCursor(QtCore.Qt.CrossCursor if self.tool else QtCore.Qt.ArrowCursor)
            return
        if self._wipe_drag:
            self._wipe_drag = None
            self.update()
            return
        if self._img_drag is not None:
            n = self._img_drag[1]
            self._img_drag = None
            self.image_edited.emit(n)
            self.update()
            return
        if self.live:
            s, self.live = self.live, None
            a, b = s.pts[0], s.pts[-1]
            if s.tool == "pen" or abs(a[0] - b[0]) + abs(a[1] - b[1]) > 0.003:
                self.stroke_done.emit(s)
            self.update()
            return
        self._drag = None

    def mouseDoubleClickEvent(self, e):
        if self._badge_at(e.position()) is not None:  # второй клик по номеру — редактор уже открыт
            return
        if self._wipe_hit(e.position()):
            self.reset_wipe()
            return
        if not self.tool:
            self.fit_view()

    def leaveEvent(self, e):
        if self._eraser_pos is not None and not self._erasing:
            self._eraser_pos = None
            self.update()
        if self._wipe_hover:
            self._wipe_hover = False
            self.update()
        if self.hover_note is not None:
            self.hover_note = None
            self.update()


class Timeline(QtWidgets.QWidget):
    """Слайдер-линейка: номера кадров, кэш, плейхед. Клик/драг — перемотка, колесо — по кадру."""
    scrubbed = QtCore.Signal(int)
    clip_moved = QtCore.Signal(int, int)  # (клип, новая позиция-«щель»)
    clip_removed = QtCore.Signal(int)
    layer_action = QtCore.Signal(int, str)  # (слой сверху вниз, действие) — крестик на полоске: remove
    layer_menu = QtCore.Signal(int, QtCore.QPoint)  # меню слоя (≡ или правый клик) в глобальной точке
    layer_moved = QtCore.Signal(int, int)  # перетащили полоску: (откуда, куда), сверху вниз; нижняя полоска — A
    layer_mode = QtCore.Signal(int, QtCore.QPoint)  # клик по режиму на полоске — список режимов
    layer_opacity = QtCore.Signal(int, float)  # тянут прозрачность на полоске — вживую
    layer_offset = QtCore.Signal(int, int)  # тянут полоску влево/вправо — новый сдвиг слоя в кадрах, вживую
    layer_trim = QtCore.Signal(int, int, int, int)  # тянут край полоски: (полоска, срез с начала, срез с конца, сдвиг)
    layer_to_edit = QtCore.Signal(int, bool)  # полоску бросили в начало (True) или конец (False) монтажа
    layer_popup = QtCore.Signal(int, str, QtCore.QPoint)  # иконка на полоске: 'info' | 'adjust'
    layer_chan = QtCore.Signal(int, QtCore.QPoint)  # клик по каналу на полоске — каналы и слои EXR
    io_changed = QtCore.Signal(object, object)  # точки входа/выхода (индексы кадров или None)
    PAD = 14
    BASE = 46  # высота без дорожки клипов
    LANE = 20  # дорожка клипов (только в монтаже)

    def __init__(self):
        super().__init__()
        self.n = 1
        self.frame = 0
        self.cached = []
        self.numbers = [1]
        self.marks = {}  # кадр -> сколько комментариев
        self.hover = None
        self.active = False  # пока ничего не открыто — только пустая дорожка
        self.clips = None  # [(начало, длина, имя)] в монтаже
        self.layers = []  # слои поверх: [(длина, имя, буква, видим, режим, прозрачность, шторка, сдвиг, EV)] сверху вниз
        self.drag_op = None  # полоска, у которой тянем прозрачность
        self.drag_layer = None  # [полоска, y нажатия, y сейчас, нажали на ≡, x нажатия, x сейчас, ось 'v'|'h'|None, сдвиг до]
        self.drag_trim = None  # (полоска, 'trim_l'|'trim_r', x нажатия, срез с начала, срез с конца, сдвиг, кадров в клипе)
        self.snap_at = None  # граница (в кадрах), к которой сейчас прилипло — рисуем жёлтую черту
        self.drop_hint = None  # перетаскивают файл над таймлайном: ('layer', кадр) | ('start', 0) | ('end', 0)
        self.hover_layer = None  # (полоска, 'grip' | 'close' | 'eye' | 'mode' | 'op') под мышью
        self.base = ""  # имя A
        self.base_look = (True, "Normal", 1.0, 0.0, "RGB")  # A: видим, режим, прозрачность, EV, канал
        self.info_open = None  # полоска, у которой сейчас открыта плашка ⓘ (иконка горит)
        self.drag_clip = None
        self.drag_x0 = self.drag_x = 0.0
        self.io = None  # (вход, выход) — кусок, который смотрим; None — весь клип
        self.drag_io = None  # 0 — тащим вход, 1 — выход
        self.setFixedHeight(self.BASE)
        self.setMouseTracking(True)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setFocusPolicy(QtCore.Qt.NoFocus)

    def set_clips(self, clips):
        self.clips = clips if clips and len(clips) > 1 else None
        self.drag_clip = None
        self._fit_height()

    def set_layers(self, layers, base="", base_look=(True, "Normal", 1.0, 0.0, "RGB")):
        self.layers = list(layers)
        self.base = base
        self.base_look = base_look
        self._fit_height()

    def _fit_height(self):
        rows = (1 if self.clips or self.layers else 0) + len(self.layers)
        self.setFixedHeight(self.BASE + self.LANE * rows)
        self.update()

    def _lane_y(self):
        """Дорожка клипов A (в монтаже); слои стопкой над ней."""
        return self.BASE - 2.0 + self.LANE * len(self.layers)

    def _rows(self):
        """Полоски, которые тащатся: слои сверху вниз и внизу A (кроме монтажа — там A дорожка клипов)."""
        return len(self.layers) + (0 if self.clips else 1)

    def _row(self, k):
        """(длина, имя, буква, видим, режим, прозрачность, шторка, сдвиг, EV, канал, срез с начала, срез с конца,
        кадров в клипе) полоски k; последняя — A."""
        if k < len(self.layers):
            return self.layers[k]
        vis, mode, op, ev, chan = self.base_look
        return self.n, self.base, "A", vis, mode, op, False, 0, ev, chan, 0, 0, self.n

    def _span(self, k):
        """Кадры A, где лежит полоска k: (первый, последний) с учётом сдвига, внутри таймлайна."""
        row = self._row(k)
        n, off = row[0], row[7]
        a = max(0, min(off, self.n - 1))
        return a, max(a, min(off + n, self.n) - 1)

    def _layer_rect(self, k):
        a, b = self._span(k)
        cw = self._cw()
        x0, x1 = self._x(a) - cw / 2, self._x(b) + cw / 2
        return QtCore.QRectF(x0 + 1.5, self.BASE - 2.0 + self.LANE * k, max(4.0, x1 - x0 - 3), 16)

    def _in_layer(self, pos):
        """Номер полоски под курсором (сверху вниз; len(layers) — A) или None."""
        rows = self._rows()
        if not self.layers or not (self.BASE - 3 <= pos.y() < self.BASE - 3 + self.LANE * rows):
            return None
        return max(0, min(rows - 1, int((pos.y() - self.BASE + 3) // self.LANE)))

    def _bar(self, p, r, text, cur, fm, tag=None, off=False, text_right=None):
        C = QtGui.QColor
        p.setPen(QtGui.QPen(C(ui.ACCENT_LINE if cur else ui.BORDER), 1))
        p.setBrush(C(ui.ACCENT_TINT if cur else ui.CONTROL))
        p.drawRoundedRect(r, 8, 8)
        x = r.left() + 8
        if tag:  # буква слоя в жёлтом кружке
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(C(ui.ACCENT))
            p.drawEllipse(QtCore.QPointF(r.left() + 9, r.center().y()), 6, 6)
            p.setPen(C(ui.ON_ACCENT))
            p.drawText(QtCore.QRectF(r.left() + 3, r.top(), 12, r.height()), QtCore.Qt.AlignCenter, tag)
            x = r.left() + 20
        p.setPen(C(ui.MUTED if off else ui.TEXT if cur else ui.DIM))
        right = r.right() - 8 if text_right is None else text_right
        tr = QtCore.QRectF(x, r.top(), max(0.0, right - x), r.height())
        p.drawText(tr, QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft,
                   fm.elidedText(text, QtCore.Qt.ElideMiddle, tr.width()))

    def _paint_layers(self, p):
        """Слои стопкой над A (верхний — сверху), A — нижней полоской; короткий слой дальше — пунктир."""
        f = QtGui.QFont(self.font())
        f.setPixelSize(10)
        p.setFont(f)
        fm = QtGui.QFontMetricsF(f)
        cw = self._cw()
        dragging = self.drag_layer is not None and self.drag_layer[6] == "v"
        for k in range(self._rows()):
            r = self._layer_rect(k)
            if dragging and k == self.drag_layer[0]:
                p.setPen(QtGui.QPen(QtGui.QColor(ui.BORDER), 1, QtCore.Qt.DashLine))
                p.setBrush(QtCore.Qt.NoBrush)
                p.drawRoundedRect(r, 8, 8)  # пустое место, откуда тащим
                continue
            self._layer_bar(p, k, r, fm)
            row = self._row(k)
            if row[7] + row[0] < self.n and not row[11] and r.right() + 8 < self._x(self.n - 1):
                rest = QtCore.QRectF(r.right() + 3, r.top(), self._x(self.n - 1) + cw / 2 - r.right() - 4.5, r.height())
                p.setPen(QtGui.QPen(QtGui.QColor(ui.BORDER), 1, QtCore.Qt.DashLine))
                p.setBrush(QtCore.Qt.NoBrush)
                p.drawRoundedRect(rest, 8, 8)
                p.setPen(QtGui.QColor(ui.MUTED))
                p.drawText(rest.adjusted(8, 0, -8, 0), QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft,
                           fm.elidedText("holds last frame", QtCore.Qt.ElideRight, max(0.0, rest.width() - 16)))
        if dragging:  # полоска едет за мышью, жёлтая черта — куда встанет
            k = self.drag_layer[0]
            r = self._layer_rect(k).translated(0, self.drag_layer[2] - self.drag_layer[1])
            p.setPen(QtGui.QPen(QtGui.QColor(ui.ACCENT), 1.5))
            hl = QtGui.QColor(ui.ACCENT_TINT)
            hl.setAlpha(235)
            p.setBrush(hl)
            p.drawRoundedRect(r, 8, 8)
            self._layer_bar(p, k, r, fm, ghost=True)
            t = self._layer_slot(self.drag_layer[2])
            y = self.BASE - 2.0 + self.LANE * (t if t <= k else t + 1) - 2.5
            p.fillRect(QtCore.QRectF(self._layer_rect(k).left(), y, self._layer_rect(k).width(), 2),
                       QtGui.QColor(ui.ACCENT))

    def _grip_rect(self, r):
        """≡ — по центру полоски: клик — меню слоя."""
        return QtCore.QRectF(r.center().x() - 11, r.top(), 22, r.height())

    def _close_rect(self, r):
        """× — справа на полоске: убрать слой."""
        return QtCore.QRectF(r.right() - 18, r.top(), 18, r.height())

    OP_W, MODE_W, CHAN_W = 84, 72, 66  # прозрачность (ползунок + %), режим (имя + ▾), канал / слой EXR (имя + ▾)

    def _parts(self, k, r):
        """Где что на полоске слоя: ≡ по центру; справа налево × · глаз · прозрачность · режим · split.
        Что не влезает (короткая полоска) — только в меню по правой кнопке."""
        P = {"close": self._close_rect(r)}
        x = r.right() - 18
        P["eye"] = QtCore.QRectF(x - 18, r.top(), 18, r.height())
        x -= 22
        if r.width() >= 90:
            P["grip"] = self._grip_rect(r)
        room = P["grip"].right() + 10 if "grip" in P else r.left() + 60
        if x - self.OP_W >= room:
            P["op"] = QtCore.QRectF(x - self.OP_W, r.top(), self.OP_W, r.height())
            x -= self.OP_W + 10
            if x - self.MODE_W >= room:
                P["mode"] = QtCore.QRectF(x - self.MODE_W, r.top(), self.MODE_W, r.height())
                x -= self.MODE_W + 6
                if x - self.CHAN_W >= room:  # канал / слой EXR этого клипа
                    P["chan"] = QtCore.QRectF(x - self.CHAN_W, r.top(), self.CHAN_W, r.height())
                    x -= self.CHAN_W + 6
                for key in ("adjust", "split", "info"):  # иконки: настройки вида, шторка-сравнение, метаданные
                    if key == "split" and k >= len(self.layers):
                        continue  # у A шторки нет — она у слоёв
                    if x - 18 < room:
                        break
                    P[key] = QtCore.QRectF(x - 18, r.top(), 18, r.height())
                    x -= 20
        return P

    @staticmethod
    def _op_track(op_rect):
        """Дорожка ползунка прозрачности внутри своей зоны (справа — «60%»)."""
        return QtCore.QRectF(op_rect.left() + 4, op_rect.center().y() - 1.5, op_rect.width() - 38, 3)

    def _layer_bar(self, p, k, r, fm, ghost=False):
        n, name, tag, vis, mode, op, split, off, ev, chan = self._row(k)[:10]
        P = self._parts(k, r)
        text = name + (f"  ·  {off:+d} fr" if off else "") + (f"  ·  {ev:+.1f} EV" if ev else "")
        lefts = [v.left() for key, v in P.items() if key != "grip" and v.left() > r.center().x() - 11]
        right = P["grip"].left() - 4 if "grip" in P else min(lefts) - 4
        self._bar(p, r, text, vis and k < len(self.layers), fm, tag, off=not vis, text_right=right)  # A — серая
        hot = None if ghost else self.hover_layer
        C = QtGui.QColor
        col = C(ui.MUTED if not vis else ui.TEXT)
        dim = C(ui.DIM if vis else ui.MUTED)
        for key, name_, lit in (("info", "info", False), ("split", "split", split), ("adjust", "adjust", bool(ev))):
            if key in P:  # иконка: жёлтая — включено (шторка) или под мышью; светлая — изменено (яркость)
                ir = P[key]
                c_ = ui.ACCENT if hot == (k, key) or (lit and key == "split" and vis) or (
                    key == "info" and self.info_open == k) else (
                    ui.TEXT if lit and vis else ui.MUTED if not vis else ui.DIM)
                ui.icon(name_, c_).paint(p, QtCore.QRect(int(ir.center().x() - 6.5), int(ir.center().y() - 6.5), 13, 13))
        if "mode" in P:  # режим наложения: «Add ▾»
            mr = P["mode"]
            on = hot == (k, "mode")
            p.setPen(C(ui.ACCENT) if on else (col if mode != "Normal" else dim))
            tr = mr.adjusted(0, 0, -11, 0)
            p.drawText(tr, QtCore.Qt.AlignVCenter | QtCore.Qt.AlignRight, fm.elidedText(mode, QtCore.Qt.ElideRight, tr.width()))
        if "chan" in P:  # канал или слой EXR: «diffuse ▾»
            cr = P["chan"]
            on = hot == (k, "chan")
            p.setPen(C(ui.ACCENT) if on else (col if chan != "RGB" else dim))
            tr = cr.adjusted(0, 0, -11, 0)
            p.drawText(tr, QtCore.Qt.AlignVCenter | QtCore.Qt.AlignRight, fm.elidedText(chan, QtCore.Qt.ElideMiddle, tr.width()))
        if "op" in P:  # прозрачность: тонкий ползунок + «60%»
            orr = P["op"]
            tr = self._op_track(orr)
            p.save()
            p.setRenderHint(QtGui.QPainter.Antialiasing, True)
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(C(ui.BORDER_HI))
            p.drawRoundedRect(tr, 1.5, 1.5)
            p.setBrush(C(ui.ACCENT) if vis else C(ui.MUTED))
            p.drawRoundedRect(QtCore.QRectF(tr.left(), tr.top(), tr.width() * op, tr.height()), 1.5, 1.5)
            on = hot == (k, "op") or self.drag_op == k
            p.setBrush(C(ui.ACCENT) if on else col)
            p.drawEllipse(QtCore.QPointF(tr.left() + tr.width() * op, tr.center().y()), 4.5 if on else 4, 4.5 if on else 4)
            p.restore()
            p.setPen(C(ui.ACCENT) if on else (col if op < 1 else dim))
            p.drawText(QtCore.QRectF(tr.right() + 4, orr.top(), orr.right() - tr.right() - 4, orr.height()),
                       QtCore.Qt.AlignVCenter | QtCore.Qt.AlignRight, f"{round(op * 100)}%")
        p.save()
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        for key in ("mode", "chan"):  # ▾ у режима и канала
            if key not in P:
                continue
            c = QtCore.QPointF(P[key].right() - 4, P[key].center().y())
            p.setPen(QtGui.QPen(C(ui.ACCENT) if hot == (k, key) else dim, 1.3, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap))
            p.drawLine(QtCore.QPointF(c.x() - 3, c.y() - 1.5), QtCore.QPointF(c.x(), c.y() + 1.5))
            p.drawLine(QtCore.QPointF(c.x(), c.y() + 1.5), QtCore.QPointF(c.x() + 3, c.y() - 1.5))
        if "grip" in P:
            c = P["grip"].center()
            on = hot == (k, "grip")
            p.setPen(QtGui.QPen(C(ui.ACCENT) if on else col, 1.4, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap))
            for dy in (-3, 0, 3):
                p.drawLine(QtCore.QPointF(c.x() - 4.5, c.y() + dy), QtCore.QPointF(c.x() + 4.5, c.y() + dy))
        p.restore()
        er = P["eye"]  # глаз: виден / скрыт
        on = hot == (k, "eye")
        ui.icon("eye" if vis else "eye_off", ui.ACCENT if on else (ui.TEXT if vis else ui.MUTED)).paint(
            p, QtCore.QRect(int(er.center().x() - 7), int(er.center().y() - 7), 14, 14))
        p.save()
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        c = P["close"].center()
        on = hot == (k, "close")
        p.setPen(QtGui.QPen(C(ui.ERROR) if on else col, 1.4, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap))
        p.drawLine(QtCore.QPointF(c.x() - 3, c.y() - 3), QtCore.QPointF(c.x() + 3, c.y() + 3))
        p.drawLine(QtCore.QPointF(c.x() + 3, c.y() - 3), QtCore.QPointF(c.x() - 3, c.y() + 3))
        p.restore()

    def _layer_part(self, pos):
        """(полоска, 'grip' | 'close' | 'eye' | 'mode' | 'op' | 'bar') под курсором или None."""
        k = self._in_layer(pos)
        if k is None:
            return None
        r = self._layer_rect(k)
        if not (r.left() - 3 <= pos.x() <= r.right() + 3):
            return None
        if k < len(self.layers) and r.width() > 30:  # за край полоски — обрезать слой с начала / с конца
            if abs(pos.x() - r.left()) <= 4:
                return k, "trim_l"
            if abs(pos.x() - r.right()) <= 4:
                return k, "trim_r"
        if not (r.left() <= pos.x() <= r.right()):
            return None
        P = self._parts(k, r)
        for key in ("close", "eye", "op", "mode", "chan", "info", "split", "adjust"):
            if key in P and P[key].adjusted(-1, 0, 1, 0).contains(pos):
                return k, key
        if "grip" in P and P["grip"].adjusted(-3, 0, 3, 0).contains(pos):
            return k, "grip"
        return k, "bar"

    def _op_at(self, k, x):
        tr = self._op_track(self._parts(k, self._layer_rect(k))["op"])
        return max(0, min(100, round((x - tr.left()) / max(1.0, tr.width()) * 20) * 5)) / 100

    def _bx(self, f):
        """x границы перед кадром f (между f-1 и f)."""
        return self.PAD + self._cw() * f

    def _snap_targets(self, skip):
        """Границы, к которым липнут края полосок: начало и конец A, In / Out, плейхед, стыки клипов, края других слоёв."""
        t = {0, self.n, self.frame}
        if self.io:
            t |= {self.io[0], self.io[1] + 1}
        for o, n, _ in self.clips or []:
            t |= {o, o + n}
        for j in range(len(self.layers)):
            if j != skip:
                row = self._row(j)
                t |= {row[7], row[7] + row[0]}
        return t

    def _snap(self, edges, skip):
        """edges — края полоски (в кадрах). Ближайшая граница в пределах 7 px: (на сколько кадров сдвинуть, граница)."""
        best = None
        for e in edges:
            for t in self._snap_targets(skip):
                d = abs(self._bx(e) - self._bx(t))
                if d <= 7 and (best is None or d < best[0]):
                    best = (d, t - e, t)
        return (best[1], best[2]) if best else (0, None)

    def _edit_zone(self, pos):
        """Полоску несут вниз, на A / дорожку клипов, к левому или правому краю — в начало или конец монтажа."""
        bottom = (self._lane_y() if self.clips else self.BASE - 2.0 + self.LANE * len(self.layers)) - 2
        if pos.y() < bottom:
            return None
        return "start" if pos.x() < self.width() * 0.2 else "end" if pos.x() > self.width() * 0.8 else None

    EDGE_DROP = 90  # ширина зон «в начало / в конец монтажа» при броске файла на таймлайн

    def drop_target(self, pos):
        """Файл бросают на таймлайн: края — в начало / конец монтажа, остальное — слой с этого кадра."""
        if pos.x() < self.EDGE_DROP:
            return "start", 0
        if pos.x() > self.width() - self.EDGE_DROP:
            return "end", 0
        f = max(0, min(self.n - 1, int(round((pos.x() - self.PAD) / max(1e-6, self._cw())))))
        if abs(self._bx(f) - self._bx(self.frame)) <= 7:  # к плейхеду липнет,
            f = self.frame
        elif pos.x() < self.EDGE_DROP + 16:  # у самой зоны «в начало» — с первого кадра
            f = 0
        return "layer", f

    def set_drop_hint(self, pos):
        hint = None if pos is None else self.drop_target(pos)
        if hint != self.drop_hint:
            self.drop_hint = hint
            self.update()

    def _paint_hints(self, p, top):
        """Жёлтая черта привязки, зоны «в начало / конец монтажа», подсказка броска файла."""
        C = QtGui.QColor
        h = self.height()
        f = QtGui.QFont(self.font())
        f.setPixelSize(10)
        f.setBold(True)
        if self.snap_at is not None:
            p.fillRect(QtCore.QRectF(self._bx(self.snap_at) - 1, top - 4, 2, h - top + 4), C(ui.ACCENT))
        zone, label = None, None
        if self.drag_layer is not None and self.drag_layer[6] == "v" and self.drag_layer[0] < len(self.layers):
            zone = self._edit_zone(QtCore.QPointF(self.drag_layer[5], self.drag_layer[2]))
            label = {"start": "Start of the edit", "end": "End of the edit"}.get(zone)
        elif self.drop_hint is not None:
            kind, fr = self.drop_hint
            if kind in ("start", "end"):
                zone = kind
                label = {"start": "+  Start of the edit", "end": "+  End of the edit"}[kind]
            else:
                x = self._bx(fr)
                p.fillRect(QtCore.QRectF(x - 1, top - 4, 2, h - top + 4), C(ui.ACCENT))
                txt = f"+  Layer from frame {self.numbers[min(fr, len(self.numbers) - 1)]}"
                fm = QtGui.QFontMetricsF(f)
                w = fm.horizontalAdvance(txt) + 16
                r = QtCore.QRectF(min(max(4.0, x + 6), self.width() - w - 4), 1, w, 16)
                p.setPen(QtCore.Qt.NoPen)
                p.setBrush(C(ui.ACCENT))
                p.drawRoundedRect(r, 8, 8)
                p.setFont(f)
                p.setPen(C(ui.ON_ACCENT))
                p.drawText(r, QtCore.Qt.AlignCenter, txt)
        if zone:
            w = self.EDGE_DROP if self.drop_hint else self.width() * 0.2
            r = QtCore.QRectF(0 if zone == "start" else self.width() - w, 0, w, h)
            tint = C(ui.ACCENT)
            tint.setAlpha(46)
            p.fillRect(r, tint)
            edge = r.left() + 1 if zone == "start" else r.right() - 3
            p.fillRect(QtCore.QRectF(edge, 0, 2, h), C(ui.ACCENT))
            if label:  # плашкой поверх номеров кадров, чтобы читалось
                p.setFont(f)
                lw = QtGui.QFontMetricsF(f).horizontalAdvance(label) + 16
                pr = QtCore.QRectF(r.left() + 6 if zone == "start" else r.right() - 6 - lw, 1, lw, 16)
                p.setPen(QtCore.Qt.NoPen)
                p.setBrush(C(ui.ACCENT))
                p.drawRoundedRect(pr, 8, 8)
                p.setPen(C(ui.ON_ACCENT))
                p.drawText(pr, QtCore.Qt.AlignCenter, label)

    def _layer_slot(self, y):
        return max(0, min(self._rows() - 1, int((y - self.BASE + 3) // self.LANE)))

    def _io_x(self):
        a, b = self.io
        cw = self._cw()
        return self._x(a) - cw / 2, self._x(b) + cw / 2

    def _io_edge(self, pos):
        """Какая скобка In/Out под курсором (на основной дорожке): 0, 1 или None."""
        if not self.io or not (16 <= pos.y() <= self.BASE - 3):
            return None
        xa, xb = self._io_x()
        da, db = abs(pos.x() - xa), abs(pos.x() - xb)
        if min(da, db) > 6:
            return None
        return 0 if da < db or (da == db and pos.x() < xa) else 1

    def _paint_io(self, p, top, bot, track):
        """Кусок In–Out: всё вне его притушено, края — жёлтые скобки [ ]."""
        C = QtGui.QColor
        xa, xb = self._io_x()
        cap = QtGui.QPainterPath()
        cap.addRoundedRect(track, track.height() / 2, track.height() / 2)
        p.setClipPath(cap)
        dim = C(ui.BG)
        dim.setAlpha(170)
        p.fillRect(QtCore.QRectF(track.left(), top, xa - track.left(), bot - top), dim)
        p.fillRect(QtCore.QRectF(xb, top, track.right() - xb, bot - top), dim)
        tint = C(ui.ACCENT)
        tint.setAlpha(26)
        p.fillRect(QtCore.QRectF(xa, top, xb - xa, bot - top), tint)  # сам кусок — лёгкая жёлтая подсветка
        p.setClipping(False)
        acc = C(ui.ACCENT)
        for x, d in ((xa, 1), (xb, -1)):
            p.fillRect(QtCore.QRectF(x - (1 if d > 0 else 1), top - 1, 2, bot - top + 2), acc)
            p.fillRect(QtCore.QRectF(x if d > 0 else x - 5, top - 1, 5, 2), acc)
            p.fillRect(QtCore.QRectF(x if d > 0 else x - 5, bot - 1, 5, 2), acc)

    def _in_lane(self, pos):
        return self.clips is not None and pos.y() >= self._lane_y() - 1

    def _clip_at(self, x):
        i = self._idx(x)
        for k, (o, n, _) in enumerate(self.clips or []):
            if o <= i < o + n:
                return k
        return None

    def _clip_rect(self, k):
        o, n, _ = self.clips[k]
        cw = self._cw()
        x0, x1 = self._x(o) - cw / 2, self._x(o + n - 1) + cw / 2
        return QtCore.QRectF(x0 + 1.5, self._lane_y(), max(4.0, x1 - x0 - 3), 16)

    def _slot_at(self, x):
        """Куда встанет перетаскиваемый клип: число клипов, чья середина левее курсора."""
        return sum(1 for k in range(len(self.clips)) if self._clip_rect(k).center().x() < x)

    def _paint_lane(self, p):
        C = QtGui.QColor
        f = QtGui.QFont(self.font())
        f.setPixelSize(10)
        p.setFont(f)
        fm = QtGui.QFontMetricsF(f)
        dragging = self.drag_clip is not None and abs(self.drag_x - self.drag_x0) >= 4
        for k, (o, n, name) in enumerate(self.clips):
            r = self._clip_rect(k)
            cur = o <= self.frame < o + n
            if dragging and k == self.drag_clip:
                p.setPen(QtGui.QPen(C(ui.BORDER), 1, QtCore.Qt.DashLine))
                p.setBrush(QtCore.Qt.NoBrush)
                p.drawRoundedRect(r, 8, 8)  # пустое место, откуда тащим
                continue
            self._bar(p, r, f"{k + 1}  {name}", cur, fm, "A" if self.layers and k == 0 else None)
        if dragging:
            k = self.drag_clip
            r = self._clip_rect(k).translated(self.drag_x - self.drag_x0, -1)
            lo, hi = self.PAD - 4.0, self.width() - self.PAD + 4.0  # не уезжать за края таймлайна
            r.moveLeft(max(lo, min(r.left(), hi - r.width())) if r.width() < hi - lo else lo)
            p.setPen(QtGui.QPen(C(ui.ACCENT), 1.5))
            hl = C(ui.ACCENT_TINT)
            hl.setAlpha(230)
            p.setBrush(hl)
            p.drawRoundedRect(r, 8, 8)
            p.setPen(C(ui.TEXT))
            p.drawText(r.adjusted(8, 0, -8, 0), QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft,
                       fm.elidedText(f"{k + 1}  {self.clips[k][2]}", QtCore.Qt.ElideMiddle, max(0.0, r.width() - 16)))
            # куда встанет — жёлтая вертикальная черта между клипами
            slot = self._slot_at(self.drag_x)
            if slot < len(self.clips):
                xs = self._clip_rect(slot).left() - 1.5
            else:
                xs = self._clip_rect(len(self.clips) - 1).right() + 1.5
            p.fillRect(QtCore.QRectF(xs - 1, self._lane_y() - 3, 2, 22), C(ui.ACCENT))
        C = QtGui.QColor

    def contextMenuEvent(self, e):
        if self.active and e.pos().y() < self.BASE - 3:  # основная дорожка: In / Out
            i = self._idx(e.pos().x())
            a, b = self.io or (None, None)
            m = QtWidgets.QMenu(self)
            m.addAction(f"Set In at {self.numbers[i]}", lambda: self.io_changed.emit(i, b if b is not None and b >= i else None))
            m.addAction(f"Set Out at {self.numbers[i]}", lambda: self.io_changed.emit(a if a is not None and a <= i else None, i))
            if self.io:
                m.addSeparator()
                m.addAction("Clear In / Out", lambda: self.io_changed.emit(None, None))
            m.exec(e.globalPos())
            return
        k = self._in_layer(QtCore.QPointF(e.pos()))
        if k is not None:
            self.layer_menu.emit(k, e.globalPos())
            return
        if not self._in_lane(e.pos()):
            return
        k = self._clip_at(e.pos().x())
        if k is None:
            return
        m = QtWidgets.QMenu(self)
        m.addAction("Go to clip start", lambda: self.scrubbed.emit(self.clips[k][0]))
        m.addAction("Remove from timeline", lambda: self.clip_removed.emit(k))
        m.exec(e.globalPos())

    def _cw(self):
        return (self.width() - 2 * self.PAD) / self.n

    def _x(self, i):
        return self.PAD + self._cw() * (i + 0.5)

    def _idx(self, x):
        return max(0, min(self.n - 1, int((x - self.PAD) / max(1e-6, self._cw()))))

    def paintEvent(self, e):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        w, h = self.width(), self.height()
        C = QtGui.QColor
        p.fillRect(self.rect(), C(ui.BG))
        cw = self._cw()
        top, bot = 20.0, self.BASE - 7.0  # дорожка
        track = QtCore.QRectF(self.PAD - 4, top, w - 2 * self.PAD + 8, bot - top)
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(C(ui.SURFACE))
        p.drawRoundedRect(track, track.height() / 2, track.height() / 2)  # капсула
        if not self.active:
            p.end()
            return

        f = p.font()
        f.setPixelSize(10)
        p.setFont(f)
        fm = QtGui.QFontMetrics(f)
        orange = C(ui.ACCENT)

        # плашка текущего кадра (считаем заранее, чтобы не перекрывать подписи)
        x0 = self._x(self.frame)
        fb = QtGui.QFont(f)
        fb.setPixelSize(11)
        fb.setBold(True)
        fmb = QtGui.QFontMetrics(fb)
        txt = str(self.numbers[self.frame])
        pw, ph = fmb.horizontalAdvance(txt) + 14, 16
        px = max(0.0, min(w - pw, x0 - pw / 2))
        pill = QtCore.QRectF(px, 1, pw, ph)

        # деления и подписи
        label_w = fm.horizontalAdvance(str(max(self.numbers, key=lambda v: len(str(v))))) + 16
        step = 1
        for st in (1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 5000, 10000):
            step = st
            if st * cw >= label_w:
                break
        if cw >= 5:
            for i in range(self.n):
                p.fillRect(QtCore.QRectF(self._x(i) - 0.5, top, 1, 4), C(255, 255, 255, 28))
        for i in range(self.n):
            if self.numbers[i] % step:
                if i != 0 or (step - self.numbers[0] % step) * cw < label_w:
                    continue
            x = self._x(i)
            p.fillRect(QtCore.QRectF(x - 0.5, top, 1, 7), C(255, 255, 255, 60))
            r = QtCore.QRectF(x - 30, 2, 60, 14)
            if r.adjusted(14, 0, -14, 0).intersects(pill.adjusted(-4, 0, 4, 0)):
                continue
            p.setPen(C(ui.DIM))
            p.drawText(r, QtCore.Qt.AlignCenter, str(self.numbers[i]))
        p.setPen(QtCore.Qt.NoPen)

        # кэш — тонкая линия по низу дорожки (обрезана по капсуле)
        cap = QtGui.QPainterPath()
        cap.addRoundedRect(track, track.height() / 2, track.height() / 2)
        p.setClipPath(cap)
        cache_col = C(ui.CACHE)
        cache_col.setAlpha(190)
        for i in self.cached:
            p.fillRect(QtCore.QRectF(self.PAD + cw * i, bot - 3, max(1.0, cw + 0.3), 2), cache_col)

        # текущий кадр: мягкая подсветка ячейки
        if cw >= 3:
            hl = C(ui.ACCENT)
            hl.setAlpha(40)
            p.fillRect(QtCore.QRectF(x0 - cw / 2, top, cw, bot - top), hl)
        p.setClipping(False)

        # hover
        if self.hover is not None and self.hover != self.frame:
            xh = self._x(self.hover)
            p.fillRect(QtCore.QRectF(xh - 0.5, top, 1, bot - top), C(255, 255, 255, 70))

        if self.io:
            self._paint_io(p, top, bot, track)

        # комментарии — жёлтые точки
        # комментарии — по точке на каждый (до 4), рядом друг с другом
        p.setBrush(C(ui.NOTE))
        yc = (top + bot) / 2 + 2
        for i, cnt in self.marks.items():
            if 0 <= i < self.n:
                k = min(cnt, 4)
                gap = 9.0
                for j in range(k):
                    p.drawEllipse(QtCore.QPointF(self._x(i) + (j - (k - 1) / 2) * gap, yc), 3.0, 3.0)

        # монтаж: границы клипов на дорожке + дорожка клипов снизу; слой сравнения — дорожкой над ней
        if self.clips:
            for o, _, _ in self.clips[1:]:
                p.fillRect(QtCore.QRectF(self._x(o) - cw / 2 - 0.75, top, 1.5, bot - top), C(ui.BORDER_HI))
            self._paint_lane(p)
        if self.layers:
            self._paint_layers(p)
        if self.clips or self.layers:
            p.fillRect(QtCore.QRectF(x0 - 0.5, bot, 1, h - bot), C(ui.ACCENT))  # плейхед сквозь дорожки
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(orange)

        # плейхед: тонкая линия + плашка с номером и «носиком»
        p.fillRect(QtCore.QRectF(x0 - 1, top - 2, 2, bot - top + 4), orange)
        p.setBrush(orange)
        p.drawRoundedRect(pill, ph / 2, ph / 2)
        nose = QtGui.QPolygonF([QtCore.QPointF(x0 - 4, pill.bottom() - 0.5), QtCore.QPointF(x0 + 4, pill.bottom() - 0.5),
                                QtCore.QPointF(x0, pill.bottom() + 4)])
        p.drawPolygon(nose)
        p.setFont(fb)
        p.setPen(C(ui.ON_ACCENT))
        p.drawText(pill, QtCore.Qt.AlignCenter, txt)
        self._paint_hints(p, top)
        p.end()

    def mousePressEvent(self, e):
        if e.button() == QtCore.Qt.LeftButton and self._io_edge(e.position()) is not None:
            self.drag_io = self._io_edge(e.position())
            return
        if self._in_layer(e.position()) is not None:
            part = self._layer_part(e.position())
            if part and e.button() == QtCore.Qt.LeftButton:
                k, what = part
                if what in ("close", "eye", "split"):
                    self.layer_action.emit(k, {"close": "remove", "eye": "toggle", "split": "split"}[what])
                    return
                if what in ("info", "adjust"):
                    r = self._parts(k, self._layer_rect(k))[what]
                    self.layer_popup.emit(k, what, self.mapToGlobal(QtCore.QPoint(int(r.left()), int(r.top()) - 4)))
                    return
                if what in ("mode", "chan"):
                    r = self._parts(k, self._layer_rect(k))[what]
                    (self.layer_mode if what == "mode" else self.layer_chan).emit(
                        k, self.mapToGlobal(QtCore.QPoint(int(r.left()), int(r.top()) - 4)))
                    return
                if what == "op":
                    self.drag_op = k
                    self.layer_opacity.emit(k, self._op_at(k, e.position().x()))
                    return
                if what in ("trim_l", "trim_r"):
                    row = self._row(k)
                    self.drag_trim = (k, what, e.position().x(), row[10], row[11], row[7], row[12])
                    self.setCursor(QtCore.Qt.SizeHorCursor)
                    return
                x, y = e.position().x(), e.position().y()
                self.drag_layer = [k, y, y, what == "grip", x, x, None, self._row(k)[7]]
                self.setCursor(QtCore.Qt.ClosedHandCursor)
            return
        if self._in_lane(e.position()) and e.button() == QtCore.Qt.LeftButton:
            k = self._clip_at(e.position().x())
            if k is not None:  # тащим клип
                self.drag_clip = k
                self.drag_x0 = self.drag_x = e.position().x()
                self.setCursor(QtCore.Qt.ClosedHandCursor)
            return
        if e.button() == QtCore.Qt.LeftButton:
            self.scrubbed.emit(self._idx(e.position().x()))

    def mouseMoveEvent(self, e):
        if self.drag_io is not None:  # тащим скобку: граница — между кадрами
            k = int(round((e.position().x() - self.PAD) / max(1e-6, self._cw())))
            a, b = self.io
            if self.drag_io == 0:
                a = max(0, min(b, k))
                self.scrubbed.emit(a)
            else:
                b = max(a, min(self.n - 1, k - 1))
                self.scrubbed.emit(b)
            if (a, b) != self.io:
                self.io_changed.emit(a, b)
            return
        if self.drag_clip is not None:
            self.drag_x = e.position().x()
            self.update()
            return
        if self.drag_op is not None:
            self.layer_opacity.emit(self.drag_op, self._op_at(self.drag_op, e.position().x()))
            return
        if self.drag_trim is not None:
            k, side, x0, tin0, tout0, off0, full = self.drag_trim
            d = int(round((e.position().x() - x0) / max(1e-6, self._cw())))
            if side == "trim_l":  # левый край: срез с начала, правый край слоя стоит на месте
                tin = max(0, min(full - tout0 - 1, tin0 + d))
                ds, self.snap_at = self._snap([off0 + tin - tin0], k)
                tin = max(0, min(full - tout0 - 1, tin + ds))
                tout, off = tout0, off0 + tin - tin0
                tip = f"trim start: {tin} fr  ·  starts at frame {self.numbers[max(0, min(self.n - 1, off))]}"
            else:  # правый край: срез с конца
                tout = max(0, min(full - tin0 - 1, tout0 - d))
                ds, self.snap_at = self._snap([off0 + full - tin0 - tout], k)
                tout = max(0, min(full - tin0 - 1, tout - ds))
                tin, off = tin0, off0
                tip = f"trim end: {tout} fr  ·  {full - tin - tout} of {full} frames"
            row = self._row(k)
            if (tin, tout, off) != (row[10], row[11], row[7]):
                self.layer_trim.emit(k, tin, tout, off)
            QtWidgets.QToolTip.showText(e.globalPosition().toPoint(), tip, self)
            self.update()
            return
        if self.drag_layer is not None:
            D = self.drag_layer
            D[2], D[5] = e.position().y(), e.position().x()
            dx, dy = D[5] - D[4], D[2] - D[1]
            if D[6] is None and max(abs(dx), abs(dy)) >= 4:  # ось — по первому движению: вбок — сдвиг во времени
                D[6] = "h" if abs(dx) > abs(dy) and D[0] < len(self.layers) else "v"
                self.setCursor(QtCore.Qt.SizeHorCursor if D[6] == "h" else QtCore.Qt.ClosedHandCursor)
            if D[6] == "h":
                n = self._row(D[0])[0]
                off = D[7] + int(round(dx / max(1e-6, self._cw())))
                ds, self.snap_at = self._snap([off, off + n], D[0])  # начало или конец липнет к краям
                off = max(-(n - 1), min(self.n - 1, off + ds))
                if off != self._row(D[0])[7]:
                    self.layer_offset.emit(D[0], off)
                QtWidgets.QToolTip.showText(e.globalPosition().toPoint(),
                                            f"starts at frame {self.numbers[max(0, min(len(self.numbers) - 1, off))]}"
                                            + (f"  ·  {off:+d} fr" if off else "  ·  in sync with A"), self)
            self.update()
            return
        i = self._idx(e.position().x())
        part = self._layer_part(e.position())
        hot = part if part and part[1] != "bar" else None
        if hot != self.hover_layer:
            self.hover_layer = hot
            self.update()
        if (k := self._in_layer(e.position())) is not None:
            what = part[1] if part else None
            self.setCursor(QtCore.Qt.PointingHandCursor if what in ("grip", "close", "eye", "mode", "chan", "info", "split",
                                                                    "adjust")
                           else QtCore.Qt.SizeHorCursor if what in ("op", "trim_l", "trim_r")
                           else QtCore.Qt.OpenHandCursor if what else QtCore.Qt.ArrowCursor)
            n, name, tag, vis, mode, op, split, off, ev, chan = self._row(k)[:10]
            info = f"{tag}  {name}  ·  {n} frames{'' if vis else '  ·  hidden'}" + (f"  ·  shifted {off:+d} fr" if off else "")
            if k >= len(self.layers):
                info += "  ·  base clip — drag it up into the stack to make another clip the base"
            self.setToolTip({"grip": f"{info}\nLayer settings — or drag to reorder",
                             "close": "Remove layer" if k < len(self.layers) else "Remove A — the layer below takes its place",
                             "eye": "Hide layer" if vis else "Show layer",
                             "mode": f"Blend mode: {mode}", "op": f"Opacity {round(op * 100)}% — drag to change",
                             "chan": f"Channel: {chan} — channels and EXR layers (AOV) of this clip",
                             "info": "Metadata", "split": "Split compare — " + ("on" if split else "off"),
                             "adjust": f"Brightness {ev:+.1f} EV · opacity · blend mode",
                             "trim_l": "Drag to trim the start of the layer", "trim_r": "Drag to trim the end of the layer"
                             }.get(what, f"{info}\n" + ("Drag up / down to reorder · right-click for settings"
                                                          if k >= len(self.layers) else
                                                          "Drag left / right to shift in time · up / down to reorder"
                                                          " · down to the left / right end — into the edit"
                                                          " · edges — trim · right-click for settings")))
            return
        if self._in_lane(e.position()):
            k = self._clip_at(e.position().x())
            self.setCursor(QtCore.Qt.OpenHandCursor if k is not None else QtCore.Qt.ArrowCursor)
            if k is not None:
                o, n, name = self.clips[k]
                self.setToolTip(f"{k + 1}. {name}  ·  {n} frames\nDrag to reorder · right-click for more")
            return
        edge = None if e.buttons() else self._io_edge(e.position())
        self.setCursor(QtCore.Qt.SizeHorCursor if edge is not None else QtCore.Qt.PointingHandCursor)
        if edge is not None:
            a, b = self.io
            self.setToolTip(f"{'In' if edge == 0 else 'Out'} {self.numbers[(a, b)[edge]]}  ·  {b - a + 1} frames\n"
                            "Drag to move · right-click to clear")
            return
        if e.buttons() & QtCore.Qt.LeftButton:
            self.scrubbed.emit(i)
        if i != self.hover:
            self.hover = i
            self.setToolTip(f"frame {self.numbers[i]}")
            self.update()

    def mouseReleaseEvent(self, e):
        if self.drag_io is not None:
            self.drag_io = None
            return
        if self.drag_trim is not None:
            self.drag_trim = None
            self.snap_at = None
            self.update()
            return
        if self.drag_op is not None:
            self.drag_op = None
            self.update()
            return
        if self.drag_layer is not None:
            k, y0, y, grip, x0, x, axis, off0 = self.drag_layer
            self.drag_layer = None
            self.snap_at = None
            self.setCursor(QtCore.Qt.OpenHandCursor)
            zone = self._edit_zone(QtCore.QPointF(x, y)) if axis == "v" and k < len(self.layers) else None
            if zone:
                self.layer_to_edit.emit(k, zone == "start")
            elif axis == "v":
                t = self._layer_slot(y)
                if t != k:
                    self.layer_moved.emit(k, t)
            elif axis is None and grip:  # клик по ≡ — меню под ним
                r = self._grip_rect(self._layer_rect(k))
                self.layer_menu.emit(k, self.mapToGlobal(QtCore.QPoint(int(r.left()), int(r.top()) - 4)))
            self.update()
            return
        if self.drag_clip is None:
            return
        k, self.drag_clip = self.drag_clip, None
        self.setCursor(QtCore.Qt.OpenHandCursor)
        if abs(self.drag_x - self.drag_x0) < 4:  # просто клик — к началу клипа
            self.scrubbed.emit(self.clips[k][0])
        else:
            self.clip_moved.emit(k, self._slot_at(self.drag_x))
        self.update()

    def leaveEvent(self, e):
        self.hover = None
        self.hover_layer = None
        self.update()

    def wheelEvent(self, e):
        d = e.angleDelta().y() or e.angleDelta().x()
        if d:
            self.scrubbed.emit(max(0, min(self.n - 1, self.frame + (-1 if d > 0 else 1))))


class FlowLayout(QtWidgets.QLayout):
    """Раскладка в строку с переносом: при узком окне элементы уходят на следующую строку."""

    def __init__(self, parent=None, margins=(8, 4, 8, 6), hspace=10, vspace=6, center=False):
        super().__init__(parent)
        self.center = center
        self.setContentsMargins(*margins)
        self._items = []
        self.hs, self.vs = hspace, vspace

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):
        return QtCore.Qt.Orientations(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, w):
        return self._do(QtCore.QRect(0, 0, w, 0), True)

    def setGeometry(self, r):
        super().setGeometry(r)
        self._do(r, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        s = QtCore.QSize()
        for it in self._items:
            s = s.expandedTo(it.minimumSize())
        m = self.contentsMargins()
        return s + QtCore.QSize(m.left() + m.right(), m.top() + m.bottom())

    def _do(self, rect, test):
        m = self.contentsMargins()
        r = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x, y, line_h = r.x(), r.y(), 0
        line = []

        def flush():  # выравнивание строки по центру (по высоте — тоже по центру)
            if not line:
                return
            shift = (r.right() + 1 - (x - self.hs)) // 2 if self.center else 0
            for it, px, sz in line:
                it.setGeometry(QtCore.QRect(QtCore.QPoint(px + shift, y + (line_h - sz.height()) // 2), sz))
            line.clear()

        for it in self._items:
            if it.widget() is not None and not it.widget().isVisible() and not test:
                continue
            sz = it.sizeHint()
            if x + sz.width() > r.right() + 1 and line_h > 0:
                if not test:
                    flush()
                x, y, line_h = r.x(), y + line_h + self.vs, 0
            line.append((it, x, sz))
            x += sz.width() + self.hs
            line_h = max(line_h, sz.height())
        if not test:
            flush()
        return y + line_h - rect.y() + m.bottom()


class BarLayout(QtWidgets.QLayout):
    """Нижняя панель в одну строку: вторичные группы слева и справа, центральный блок —
    так, чтобы кнопка Play была ровно по центру (насколько позволяет место).
    Если всё не помещается — центральный блок сверху, вторичные группы ниже (с переносом, по центру)."""

    # сверху меньше: под дорожкой таймлайна уже есть 7px воздуха → визуально 10px сверху и снизу
    def __init__(self, parent, center, anchor, left, right, optional=None, margins=(10, 2, 10, 9), gap=12,
                 vspace=6):
        super().__init__(parent)
        self.optional = optional  # прячется первым, если в одну строку не влезает
        self.setContentsMargins(*margins)
        self.gap, self.vs, self.anchor = gap, vspace, anchor
        self._items = []
        self.addWidget(center)
        self.center = self._items[0]
        for w in left + right:
            self.addWidget(w)
        self.left = self._items[1:1 + len(left)]
        self.right = self._items[1 + len(left):]

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):
        return QtCore.Qt.Orientations(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, w):
        return self._do(QtCore.QRect(0, 0, w, 0), True)

    def setGeometry(self, r):
        super().setGeometry(r)
        self._do(r, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        s = QtCore.QSize()
        for it in self._items:
            s = s.expandedTo(it.sizeHint())
        m = self.contentsMargins()
        return s + QtCore.QSize(m.left() + m.right(), m.top() + m.bottom())

    def _w(self, items):
        return sum(it.sizeHint().width() for it in items) + self.gap * max(0, len(items) - 1)

    def _do(self, rect, test):
        m = self.contentsMargins()
        r = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        W = r.width()
        cs = self.center.sizeHint()
        lw, rw = self._w(self.left), self._w(self.right)
        place = (lambda it, x, y, h: None) if test else \
            (lambda it, x, y, h: it.setGeometry(QtCore.QRect(int(x), int(y + (h - it.sizeHint().height()) // 2),
                                                             it.sizeHint().width(), it.sizeHint().height())))
        side = self.left + self.right
        need = lw + rw + cs.width() + 2 * self.gap
        opt = next((it for it in self.right if it.widget() is self.optional), None)
        drop = opt is not None and need > W and need - opt.sizeHint().width() - self.gap <= W
        if drop:  # без необязательного элемента влезает в одну строку
            if not test:
                opt.widget().move(-10000, 0)  # за пределами виджета = скрыт
            side = [it for it in side if it is not opt]
            self.right = [it for it in self.right if it is not opt]
            rw = self._w(self.right)
            self.right.append(opt)
            need = lw + rw + cs.width() + 2 * self.gap
        if need <= W:  # всё в одну строку
            right = [it for it in self.right if not (drop and it is opt)]
            h = max(it.sizeHint().height() for it in self._items)
            x = r.x()
            for it in self.left:
                place(it, x, r.y(), h)
                x += it.sizeHint().width() + self.gap
            x = r.right() + 1 - rw
            for it in right:
                place(it, x, r.y(), h)
                x += it.sizeHint().width() + self.gap
            cx = r.x() + W / 2 - self.anchor()
            cx = max(r.x() + lw + self.gap, min(cx, r.right() + 1 - rw - self.gap - cs.width()))
            place(self.center, cx, r.y(), h)
            return h + m.top() + m.bottom()
        # две и более строк
        cx = r.x() + W / 2 - self.anchor()
        cx = max(r.x(), min(cx, r.right() + 1 - cs.width()))
        place(self.center, cx, r.y(), cs.height())
        y = r.y() + cs.height() + self.vs
        line, lx, lh = [], 0, 0

        def flush(y):
            off = r.x() + (W - (lx - self.gap)) / 2
            for it, px in line:
                place(it, off + px, y, lh)

        for it in side:
            sw = it.sizeHint().width()
            if line and lx + sw > W:
                flush(y)
                y += lh + self.vs
                line, lx, lh = [], 0, 0
            line.append((it, lx))
            lx += sw + self.gap
            lh = max(lh, it.sizeHint().height())
        flush(y)
        return y + lh - rect.y() + m.bottom()


def hgroup(*widgets, spacing=6, h=None):
    """Несколько виджетов, которые переносятся вместе одним блоком."""
    g = QtWidgets.QWidget()
    lay = QtWidgets.QHBoxLayout(g)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(spacing)
    for w in widgets:
        lay.addWidget(w, 0, QtCore.Qt.AlignVCenter)
    g.setFixedHeight(h or ui.H)
    return g


class NoteCard(QtWidgets.QFrame):
    """Карточка комментария в правой панели. Клик — выбрать и перейти на кадр,
    двойной клик — редактировать текст прямо в карточке (обводится рамкой)."""
    clicked = QtCore.Signal(object)
    edited = QtCore.Signal(object)
    entered = QtCore.Signal(object)  # закончили Enter-ом

    def __init__(self, note, num, header):
        super().__init__()
        self.note = note
        self.editing = False
        self.selected = False
        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(10, 8, 10, 8)
        v.setSpacing(4)
        self.head = QtWidgets.QLabel(f"<b>{num}.</b>&nbsp; {header}")
        self.head.setWordWrap(True)
        self.head.setStyleSheet("color:#bbb; background:transparent; border:none;")
        self.body = QtWidgets.QLabel()
        self.body.setWordWrap(True)
        # не даём длинному тексту распирать карточку шире панели — только перенос строк
        for lbl in (self.head, self.body):
            lbl.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
            lbl.setMinimumWidth(1)
        self.body.setStyleSheet("color:#eee; background:transparent; border:none;")
        self.edit = QtWidgets.QTextEdit()
        self.edit.setAcceptRichText(False)
        self.edit.setPlaceholderText("Type a comment…  Enter to finish, Shift+Enter for new line")
        self.edit.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.edit.setStyleSheet(f"background:{ui.SURFACE}; border:none; padding:2px; color:{ui.TEXT};")
        self.edit.setFocusPolicy(QtCore.Qt.ClickFocus)
        self.edit.hide()
        self.edit.installEventFilter(self)
        self.edit.textChanged.connect(self._changed)
        v.addWidget(self.head)
        v.addWidget(self.body)
        v.addWidget(self.edit)
        self._update_body()
        self._style()

    def _update_body(self):
        t = self.note.text.strip()
        if not t:
            self.body.setText("<span style='color:#777'>" + (f"{SKETCH_TEXT} — double-click to add a note" if self.note.strokes else "double-click to write") + "</span>")
        else:
            short = t if len(t) <= 140 else t[:140].rstrip() + "…"
            self.body.setText(short.replace("&", "&amp;").replace("<", "&lt;").replace("\n", "<br>"))

    def _style(self):
        if self.editing:
            css = f"background:{ui.CONTROL}; border:2px solid {ui.ACCENT}; border-radius:{ui.RADIUS_CARD}px;"
        elif self.selected:
            css = f"background:{ui.ACCENT_TINT}; border:1px solid {ui.ACCENT_LINE}; border-radius:{ui.RADIUS_CARD}px;"
        else:
            css = f"background:{ui.CONTROL}; border:1px solid {ui.BORDER}; border-radius:{ui.RADIUS_CARD}px;"
        self.setStyleSheet(f"NoteCard {{ {css} }}")

    def set_selected(self, on):
        self.selected = on
        self._style()

    def _fit(self):
        doc = self.edit.document()
        doc.setTextWidth(self.edit.viewport().width())
        self.edit.setFixedHeight(max(54, int(doc.size().height()) + 8))

    def start_edit(self):
        self.editing = True
        self.body.hide()
        self.edit.blockSignals(True)
        self.edit.setPlainText(self.note.text)
        self.edit.blockSignals(False)
        self.edit.show()
        self.edit.setFocus()
        self.edit.moveCursor(QtGui.QTextCursor.End)
        QtCore.QTimer.singleShot(0, self._fit)
        self._style()

    def finish_edit(self):
        if not self.editing:
            return
        self.editing = False
        self.edit.hide()
        self._update_body()
        self.body.show()
        self._style()

    def _changed(self):
        self.note.text = self.edit.toPlainText()
        self._fit()
        self.edited.emit(self.note)

    def eventFilter(self, obj, e):
        if obj is self.edit:
            if e.type() == QtCore.QEvent.KeyPress:
                enter = e.key() in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter)
                # Enter или Esc — закончить; Shift+Enter — новая строка
                if e.key() == QtCore.Qt.Key_Escape or (enter and not e.modifiers() & QtCore.Qt.ShiftModifier):
                    self.edit.clearFocus()
                    if enter:
                        self.entered.emit(self.note)
                    return True
            if e.type() == QtCore.QEvent.FocusOut:
                self.finish_edit()
        return False

    def mousePressEvent(self, e):
        if not self.editing:
            clear_focus()  # закончить редактирование другой карточки
        self.clicked.emit(self.note)

    def mouseDoubleClickEvent(self, e):
        self.clicked.emit(self.note)
        self.start_edit()


class Fold(QtWidgets.QWidget):
    """Узкая полоска со стрелкой, которая раскрывает/скрывает панель.
    vertical=False — полоска сверху (стрелка ▸/▾), True — сбоку справа (◂/▸, текст повёрнут)."""
    toggled = QtCore.Signal(bool)

    def __init__(self, title, vertical=False, left=False):
        super().__init__()
        self.title = title
        self.left = left  # полоска у левого края: стрелки зеркально
        self.count = 0  # число комментариев — жёлтый бейдж
        self.vertical = vertical
        self.open = False
        self.hover = False
        if vertical:
            self.setFixedWidth(22)
        else:
            self.setFixedHeight(22)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setToolTip(f"Show / hide: {title}")

    def set_open(self, on):
        if on != self.open:
            self.open = on
            self.update()
            self.toggled.emit(on)

    def mousePressEvent(self, e):
        self.set_open(not self.open)

    def enterEvent(self, e):
        self.hover = True
        self.update()

    def leaveEvent(self, e):
        self.hover = False
        self.update()

    def paintEvent(self, e):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        p.fillRect(self.rect(), QtGui.QColor(ui.HOVER if self.hover else ui.SURFACE))
        col = QtGui.QColor(ui.TEXT if self.hover else ui.DIM)
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(col)
        # треугольник-стрелка
        if self.vertical:
            c = QtCore.QPointF(self.width() / 2, 16)
            d = 1 if self.open else -1  # открыто: ▸ (спрятать вправо), закрыто: ◂
            if self.left:
                d = -d
            tri = [c + QtCore.QPointF(4 * d, 0), c + QtCore.QPointF(-3 * d, -5), c + QtCore.QPointF(-3 * d, 5)]
        else:
            c = QtCore.QPointF(14, self.height() / 2)
            if self.open:  # ▾
                tri = [c + QtCore.QPointF(-5, -3), c + QtCore.QPointF(5, -3), c + QtCore.QPointF(0, 4)]
            else:  # ▸
                tri = [c + QtCore.QPointF(-3, -5), c + QtCore.QPointF(-3, 5), c + QtCore.QPointF(4, 0)]
        p.drawPolygon(QtGui.QPolygonF(tri))
        f = QtGui.QFont(self.font())
        f.setPixelSize(12)
        p.setFont(f)
        p.setRenderHint(QtGui.QPainter.TextAntialiasing)
        fm = QtGui.QFontMetricsF(f)
        tw = fm.horizontalAdvance(self.title)
        p.setPen(col)
        if self.vertical:
            # текст повёрнут на 90° и отцентрован по ширине полоски
            p.save()
            p.translate(self.width() / 2, 30)
            p.rotate(90)
            p.drawText(QtCore.QRectF(0, -self.width() / 2, tw + 2, self.width()), QtCore.Qt.AlignVCenter, self.title)
            p.restore()
            badge = QtCore.QPointF(self.width() / 2, 30 + tw + 16)
        else:
            p.drawText(QtCore.QRectF(26, 0, tw + 2, self.height()), QtCore.Qt.AlignVCenter, self.title)
            badge = QtCore.QPointF(26 + tw + 14, self.height() / 2)
        if self.count:
            txt = str(self.count)
            f.setPixelSize(10)
            f.setBold(True)
            p.setFont(f)
            r = max(7.5, QtGui.QFontMetricsF(f).horizontalAdvance(txt) / 2 + 4)
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(QtGui.QColor(ui.NOTE))
            if self.vertical:  # вытянутый по вертикали бейдж для двузначных чисел
                rect = QtCore.QRectF(badge.x() - 7.5, badge.y() - r, 15, 2 * r)
            else:
                rect = QtCore.QRectF(badge.x() - r, badge.y() - 7.5, 2 * r, 15)
            p.drawRoundedRect(rect, 7.5, 7.5)
            p.setPen(QtGui.QColor(ui.ON_NOTE))
            p.drawText(rect, QtCore.Qt.AlignCenter, txt)
        p.end()


# --------------------------------------------------------------------------- main window


class DropOverlay(QtWidgets.QWidget):
    """При перетаскивании файлов окно делится на зоны: Open | Add to edit / Add as layer | Add to playlist.
    Средняя колонка, когда что-то уже открыто, делится пополам: монтаж (сверху) или слой для сравнения (снизу)."""
    OPEN, EDIT, PLAYLIST, LAYER = range(4)

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents)
        self.side = 0  # OPEN | EDIT | PLAYLIST | LAYER
        self.count = 1
        self.has_media = False
        self.hide()

    def zones(self):
        """{зона: прямоугольник}."""
        m, gap = 28.0, 16.0
        w, h = self.width() - 2 * m, self.height() - 2 * m
        z = (w - 2 * gap) / 3
        col = [QtCore.QRectF(m + k * (z + gap), m, z, h) for k in range(3)]
        out = {self.OPEN: col[0], self.EDIT: col[1], self.PLAYLIST: col[2]}
        if self.has_media:
            hh = (h - gap) / 2
            out[self.EDIT] = QtCore.QRectF(col[1].left(), m, z, hh)
            out[self.LAYER] = QtCore.QRectF(col[1].left(), m + hh + gap, z, hh)
        return out

    def side_at(self, pos):
        x = (pos.x()) / max(1, self.width())
        k = max(0, min(2, int(x * 3)))
        if k == 1 and self.has_media and pos.y() > self.height() / 2:
            return self.LAYER
        return (self.OPEN, self.EDIT, self.PLAYLIST)[k]

    def paintEvent(self, e):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        p.fillRect(self.rect(), QtGui.QColor(0, 0, 0, 170))
        many = f"{self.count} files" if self.count > 1 else "the file"
        texts = {self.OPEN: ("Open", f"Replace what's playing with {many}"),
                 self.EDIT: ("Add to edit", f"Put {many} after the last clip" if self.has_media
                             else f"Start a timeline with {many}"),
                 self.PLAYLIST: ("Add to playlist", f"Keep {many} in the playlist for the review"),
                 self.LAYER: ("Add as layer", "Stack it over what's playing: blend, opacity, split")}
        for k, r in self.zones().items():
            title, sub = texts[k]
            hot = k == self.side
            fill = QtGui.QColor(ui.ACCENT_TINT if hot else ui.SURFACE)
            fill.setAlpha(200 if hot else 140)
            p.setPen(QtGui.QPen(QtGui.QColor(ui.ACCENT if hot else ui.BORDER), 2 if hot else 1))
            p.setBrush(fill)
            p.drawRoundedRect(r, 28, 28)
            # значок: круг с «стрелкой внутрь» (Open) или «+» (Add)
            c = r.center() - QtCore.QPointF(0, 34 if r.height() > 330 else 44)
            col = QtGui.QColor(ui.ACCENT if hot else ui.DIM)
            p.setPen(QtGui.QPen(col, 2.5, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap, QtCore.Qt.RoundJoin))
            p.setBrush(QtCore.Qt.NoBrush)
            p.drawEllipse(c, 26, 26)
            if k == 0:
                p.drawLine(c + QtCore.QPointF(0, -11), c + QtCore.QPointF(0, 9))
                p.drawLine(c + QtCore.QPointF(-8, 2), c + QtCore.QPointF(0, 10))
                p.drawLine(c + QtCore.QPointF(8, 2), c + QtCore.QPointF(0, 10))
            elif k == self.EDIT:
                p.drawLine(c + QtCore.QPointF(-11, 0), c + QtCore.QPointF(11, 0))
                p.drawLine(c + QtCore.QPointF(0, -11), c + QtCore.QPointF(0, 11))
            elif k == self.LAYER:  # сравнение: половина круга залита, по центру — шторка
                half = QtGui.QPainterPath()
                half.moveTo(c + QtCore.QPointF(0, -26))
                half.arcTo(QtCore.QRectF(c.x() - 26, c.y() - 26, 52, 52), 90, -180)
                half.closeSubpath()
                f2 = QtGui.QColor(col)
                f2.setAlpha(90)
                p.fillPath(half, f2)
                p.drawLine(c + QtCore.QPointF(0, -26), c + QtCore.QPointF(0, 26))
            else:  # строки списка
                for dy in (-8, 0, 8):
                    p.drawLine(c + QtCore.QPointF(-5, dy), c + QtCore.QPointF(11, dy))
                    p.drawPoint(c + QtCore.QPointF(-11, dy))
            f = QtGui.QFont(self.font())
            f.setPixelSize(20)
            f.setBold(True)
            p.setFont(f)
            p.setPen(QtGui.QColor(ui.TEXT if hot else ui.DIM))
            p.drawText(QtCore.QRectF(r.left(), c.y() + 40, r.width(), 28), QtCore.Qt.AlignCenter, title)
            f.setPixelSize(13)
            f.setBold(False)
            p.setFont(f)
            p.setPen(QtGui.QColor(ui.DIM))
            p.drawText(QtCore.QRectF(r.left() + 16, c.y() + 70, r.width() - 32, 40),
                       QtCore.Qt.AlignHCenter | QtCore.Qt.AlignTop | QtCore.Qt.TextWordWrap, sub)
        p.end()



class Layer:
    """Клип поверх кадра A: свой загрузчик кадров + как он ложится (прозрачность, режим, видимость, шторка)."""

    def __init__(self, src, loader, opacity=1.0, mode="Normal", visible=True, split=False, offset=0, ev=0.0, aov=None, channel="RGB",
                 trim_in=0, trim_out=0):
        self.src, self.loader = src, loader
        self.trim_in, self.trim_out = trim_in, trim_out  # сколько кадров слоя срезано с начала и с конца
        self.opacity, self.mode, self.visible, self.split = opacity, mode, visible, split
        self.offset = offset  # с какого кадра A слой начинается (сдвиг по таймлайну, может быть < 0)
        self.ev = ev  # яркость слоя в стопах — поверх общей экспозиции
        self.aov = aov  # свой слой EXR (AOV) этого клипа; None — основной. У A — из списка каналов
        self.channel = channel  # свой канал (RGB, R, G, B, A, Luma) — когда AOV не выбран
        self.img = None
        self.tag = "B"

    @property
    def name(self):
        return self.src.file_label

    @property
    def length(self):
        """Сколько кадров слоя видно после обрезки."""
        return max(1, self.src.n - self.trim_in - self.trim_out)

    def index(self, i):
        """Кадр слоя для кадра i с учётом сдвига и обрезки: до начала — None (слоя нет); после конца
        обрезанный с конца слой исчезает, необрезанный держит последний кадр."""
        j = i - self.offset
        if j < 0:
            return None
        if j >= self.length:
            if self.trim_out:
                return None
            j = self.length - 1
        return j + self.trim_in


class Player(QtWidgets.QMainWindow):
    def __init__(self, cache_gb):
        super().__init__()
        self.setWindowTitle("MOOOV_player")
        self.resize(1560, 900)
        self.setAcceptDrops(True)
        self.drop = DropOverlay(self)
        self.budget = int(cache_gb * 1024**3)
        self.src = None
        self.loader = None
        self.layers = []  # слои поверх A, снизу вверх (Layer)
        self.base_look = Layer(None, None)  # как лежит сам A: видимость, режим, прозрачность
        self.base_look.tag = "A"
        self.frame = 0
        self.playing = False
        self.next_due = 0.0
        self.shown_times = deque(maxlen=48)
        self.store = None
        self.active = None  # выбранная заметка
        self.settings = QtCore.QSettings("mooov", "mooov")
        st = self.settings.value("sticker", "") or ""
        self.sticker = st if st and os.path.exists(st) else None  # последний выбранный стикер
        ui.apply(QtWidgets.QApplication.instance())

        self.viewer = Viewer()
        self.viewer.stroke_done.connect(self.on_stroke)
        self.viewer.erased.connect(self.erase_stroke)
        self.viewer.erase_done.connect(self.erase_finished)
        self.edit_log = []  # для отмены: ("draw",) | ("erase", [(нота, место, штрих, нота удалена)])
        self._erase_batch = []
        self.viewer.image_requested.connect(self.add_ref_image)
        self.viewer.image_edited.connect(lambda n: (self.note_edited(n), self.viewer.update()))
        self.viewer.bubble.changed.connect(self.bubble_changed)
        self.viewer.bubble.done.connect(lambda n: self.notes_changed())
        self.viewer.bubble.entered.connect(self.note_committed)
        self.draw_note = None  # комментарий, в который идут штрихи («+» или эскиз); Enter его закрывает
        self.viewer.badge_clicked.connect(self.badge_clicked)
        self.timeline = Timeline()
        self.timeline.scrubbed.connect(self.scrub)
        self.timeline.clip_moved.connect(self.move_clip)
        self.timeline.clip_removed.connect(self.remove_clip)
        self.timeline.layer_action.connect(lambda k, a: self.layer_action(self._layer_at(k), a))
        self.timeline.layer_menu.connect(lambda k, pos: self.layer_menu(self._layer_at(k), pos))
        self.timeline.layer_moved.connect(self.move_layer)
        self.timeline.layer_mode.connect(lambda k, pos: self.mode_menu(self._layer_at(k), pos))
        self.timeline.layer_opacity.connect(lambda k, v: self.set_layer(self._layer_at(k), opacity=v)
                                            if self._layer_at(k).opacity != v else None)
        self.timeline.layer_offset.connect(lambda k, v: self.set_layer(self._layer_at(k), offset=v)
                                           if self._layer_at(k) is not self.base_look else None)
        self.timeline.layer_chan.connect(lambda k, pos: self.chan_menu(self._layer_at(k), pos))
        self.timeline.layer_trim.connect(lambda k, a, b, off: self.set_layer(self._layer_at(k), trim_in=a, trim_out=b, offset=off)
                                         if self._layer_at(k) is not self.base_look else None)
        self.timeline.layer_to_edit.connect(lambda k, start: self.layer_to_edit(self._layer_at(k), start))
        self.timeline.layer_popup.connect(lambda k, what, pos: (self.info_popup if what == "info" else self.look_menu)(
            self._layer_at(k), pos))
        self.timeline.io_changed.connect(self.set_io)
        self.io_in = self.io_out = None  # точки входа/выхода: смотрим только этот кусок

        def btn(name, tip, fn, repeat=False):
            b = ui.icon_button(name, tip, repeat=repeat)
            b.clicked.connect(fn)
            return b

        self.btn_first = btn("first", "First frame — or the In point (Home)", self.go_start)
        self.btn_prev = btn("prev", "Previous frame (←) — hold to scrub", lambda: self.step(-1), True)
        self.btn_play = btn("play", "Play / Pause (Space)", self.toggle_play)
        self.btn_next = btn("next", "Next frame (→) — hold to scrub", lambda: self.step(1), True)
        self.btn_last = btn("last", "Last frame — or the Out point (End)", self.go_end)
        self.btn_in = btn("mark_in", "Set In point here (I) — click again to clear", self.mark_in)
        self.btn_out = btn("mark_out", "Set Out point here (O) — click again to clear\nAlt+X clears both", self.mark_out)
        for b_ in (self.btn_in, self.btn_out):
            b_.setCheckable(True)
            b_.clicked.disconnect()
        self.btn_in.clicked.connect(lambda: self.mark_in(toggle=True))
        self.btn_out.clicked.connect(lambda: self.mark_out(toggle=True))

        self.spin_frame = QtWidgets.QSpinBox()
        self.spin_frame.setKeyboardTracking(False)
        self.spin_frame.setFocusPolicy(QtCore.Qt.ClickFocus)
        self.spin_frame.setObjectName("frameSpin")
        self.spin_frame.valueChanged.connect(self.frame_number_entered)
        self.lbl_frame = QtWidgets.QLabel("—")
        self.lbl_frame.setObjectName("dimLabel")
        self.fps = QtWidgets.QDoubleSpinBox(decimals=2, minimum=1, maximum=240, value=24)
        self.fps.setSuffix(" fps")
        self.speed = QtWidgets.QComboBox()  # скорость воспроизведения
        for sp in SPEEDS:
            self.speed.addItem(f"{sp:g}×", sp)
        self.speed.setToolTip("Playback speed  (Shift+← / Shift+→)")
        self.speed.setFocusPolicy(QtCore.Qt.ClickFocus)
        self.speed.setCurrentIndex(self.speed.findData(1.0))  # при запуске всегда 1×
        self.speed.currentIndexChanged.connect(self.speed_changed)
        self.play_step = 1
        self.chk_loop = QtWidgets.QCheckBox("Loop")
        self.chk_loop.setChecked(True)
        self.exposure = QtWidgets.QDoubleSpinBox(decimals=1, minimum=-10, maximum=10, singleStep=0.5)
        self.exposure.setPrefix("EV ")
        self.channel = QtWidgets.QComboBox()
        self.channel.addItems(list(CHANNELS))
        self.aov = QtWidgets.QComboBox()  # слой EXR (AOV): виден, только если в файле их несколько
        self.aov.addItem(BASE_LAYER, None)
        self.aov.setToolTip("EXR layer (AOV) — beauty, diffuse, specular, Z …")
        self.aov.setVisible(False)  # больше не показывается: слои EXR — в списке каналов, под чертой
        self.view = QtWidgets.QComboBox()
        self.view.addItems(["sRGB", "Linear"])
        if ocio_views():  # под чертой — OCIO: ACES 1 / ACES 2 / без тонмаппинга (+ виды конфига $OCIO)
            self.view.insertSeparator(self.view.count())
            self.view.addItems(list(ocio_views()))
        self.view.setToolTip("Display transform for EXR/HDR · below the line — OCIO ACES 1.0: "
                             "colour space of the EXR → monitor (the file's own space is in Info)")
        self.view.setCurrentText(self.settings.value("view", "sRGB"))
        self.chk_half = QtWidgets.QCheckBox("½ res")
        self.lbl_info = QtWidgets.QLabel()
        self.lbl_info.setObjectName("dimLabel")
        self.lbl_info.setFixedHeight(ui.HS)
        ui.small(self.btn_in, self.btn_out)
        ui.small(self.fps, self.speed, self.chk_loop, self.exposure, self.aov, self.channel, self.view, self.chk_half,
                 self.lbl_info)
        self.btn_play.setProperty("primary", True)
        self.btn_play.setIcon(ui.icon("play", ui.ON_ACCENT))
        self.btn_play.setIconSize(QtCore.QSize(22, 22))
        for lbl in (self.lbl_frame, self.lbl_info, self.spin_frame):
            ui.tabular(lbl)
        ui.fit_label(self.lbl_info, "888.8 fps  ·  88.8 GB")
        for w in (self.fps, self.exposure, self.aov, self.channel, self.view, self.chk_loop, self.chk_half):
            w.setFocusPolicy(QtCore.Qt.ClickFocus)
        self.spin_frame.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons)  # рядом есть кнопки шага
        for sp in (self.spin_frame, self.fps, self.exposure):
            sp.setAlignment(QtCore.Qt.AlignCenter)
        ui.steppers(self.fps)
        ui.steppers(self.exposure)
        self.spin_frame.setToolTip("Frame number — type and press Enter, or scroll")
        self.fps.setToolTip("Frame rate — type or scroll")
        self.exposure.setToolTip("Exposure in stops — scroll or [ ]")
        ui.fit_spin(self.fps, "240.00 fps")
        ui.fit_spin(self.exposure, "EV -10.0")
        ui.fit_spin(self.spin_frame, "1001")
        ui.fit_combo(self.channel)
        ui.fit_combo(self.speed)
        ui.fit_combo(self.view)
        self.matte =QtWidgets.QComboBox()
        self.matte.addItems(list(MATTES))
        ui.small(self.matte)
        self.matte.setToolTip("Matte — aspect ratio mask (M to cycle)")
        self.matte.setFocusPolicy(QtCore.Qt.ClickFocus)
        ui.fit_combo(self.matte)
        self.matte_op = QtWidgets.QComboBox()  # прозрачность каше
        ui.small(self.matte_op)
        for tr in (0, 25, 50, 75):
            self.matte_op.addItem("Solid" if tr == 0 else f"{tr}%", tr)
        self.matte_op.setToolTip("Matte transparency: solid black or 25 / 50 / 75 %")
        self.matte_op.setFocusPolicy(QtCore.Qt.ClickFocus)
        ui.fit_combo(self.matte_op)
        self.matte.setCurrentText(self.settings.value("matte", "No matte"))
        i = self.matte_op.findData(int(self.settings.value("matte_tr", 0)))
        self.matte_op.setCurrentIndex(max(0, i))
        self.matte.currentTextChanged.connect(self.matte_changed)
        self.matte_op.currentIndexChanged.connect(self.matte_changed)
        self.matte_changed()
        self.exposure.valueChanged.connect(self.params_changed)
        self.channel.currentIndexChanged.connect(self.params_changed)
        self.aov.currentIndexChanged.connect(self.params_changed)
        self.view.currentIndexChanged.connect(self.view_changed)
        self.chk_half.toggled.connect(self.params_changed)

        self.btn_info = ui.icon_button("info", "File info (Shift+I)", checkable=True)
        self.btn_fit = ui.icon_button("zoom", "Fit to window — reset zoom and position (F)")
        self.btn_fit.clicked.connect(self.viewer.fit_view)
        ui.small(self.btn_info, self.btn_fit)
        self.btn_info.setChecked(self.settings.value("show_info", "false") == "true")
        self.btn_info.toggled.connect(self.toggle_info)
        self.fps.valueChanged.connect(lambda *_: (self.update_info(), self.viewer.update()))
        self.info_cache = {}

        # одна строка: [вторичные слева] [кадр ⏮ ◀ ▶ ▶ ⏭ таймкод — по центру] [вторичные справа];
        # если не влезает — транспорт сверху, вторичные строкой ниже
        hs = ui.HS
        ui.fit_label(self.lbl_frame, "8888 / 8888  88:88:88:88")
        self.transport = hgroup(self.btn_first, self.btn_prev, self.btn_play, self.btn_next, self.btn_last, h=ui.HP)
        center = hgroup(self.spin_frame, self.transport, self.lbl_frame, spacing=10, h=ui.HP)
        bottom = QtWidgets.QWidget()
        self.bar = BarLayout(bottom, center, self.play_anchor,
                             left=[hgroup(self.fps, self.speed, self.chk_loop, spacing=8, h=hs),
                                   hgroup(self.btn_in, self.btn_out, spacing=4, h=hs),
                                   hgroup(self.exposure, self.aov, self.channel, self.view, self.chk_half, spacing=6, h=hs)],
                             right=[hgroup(self.matte, self.matte_op, spacing=4, h=hs),
                                    hgroup(self.btn_fit, self.btn_info, spacing=4, h=hs),
                                    self.lbl_info],
                             optional=self.lbl_info)

        self.draw_bar = self.build_draw_bar()
        self.panel = self.build_comments()
        self.draw_fold = Fold("Draw")
        self.notes_fold = Fold("Comments", vertical=True)
        self.draw_fold.toggled.connect(lambda on: self._fold(self.draw_bar, "fold_draw", on))
        self.notes_fold.toggled.connect(lambda on: self._fold(self.panel, "fold_notes", on))

        self.playlist = pl.PlaylistPanel(probe_media)
        self.playlist.key_fn = media_key
        self.pl_fold = Fold("Playlist", vertical=True, left=True)
        self.pl_fold.toggled.connect(self._toggle_playlist)
        self.playlist.open_requested.connect(self.open_from_playlist)
        self.playlist.timeline_requested.connect(self.open_paths)
        self.playlist.add_to_timeline.connect(lambda p: self.add_paths([p]))
        self.playlist.add_files_requested.connect(self.playlist_add_dialog)
        self.playlist.add_current_requested.connect(self.playlist_add_current)

        left = QtWidgets.QWidget()
        self.left_col = left
        lay = QtWidgets.QVBoxLayout(left)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.draw_fold)
        lay.addWidget(self.draw_bar)
        lay.addWidget(self.viewer, 1)
        lay.addWidget(self.timeline)
        lay.addWidget(bottom)
        central = QtWidgets.QWidget()
        row = QtWidgets.QHBoxLayout(central)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        row.addWidget(self.playlist)
        row.addWidget(self.pl_fold)
        row.addWidget(left, 1)
        row.addWidget(self.notes_fold)
        row.addWidget(self.panel)
        self.setCentralWidget(central)
        # при запуске панели рисования и комментариев всегда свёрнуты
        self.draw_bar.hide()
        self.panel.hide()
        self.draw_fold.set_open(False)
        self.notes_fold.set_open(False)
        # плейлист помним: открыт ли он был в прошлый раз
        self.playlist.hide()
        self.pl_fold.set_open(self.settings.value("fold_playlist", "false") == "true")

        m = self.menuBar().addMenu("File")
        m.addAction("Open File…", QtGui.QKeySequence.Open, self.open_file_dialog)
        m.addAction("Open Folder…", QtGui.QKeySequence("Ctrl+Shift+O"), self.open_dir_dialog)
        m.addAction("Add to Timeline…", QtGui.QKeySequence("Ctrl+Shift+A"), self.add_file_dialog)
        m.addAction("Add to Playlist…", QtGui.QKeySequence("Ctrl+Shift+L"), self.playlist_add_dialog)
        m.addAction("Add Layers…", QtGui.QKeySequence("Ctrl+Shift+C"), self.add_layer_dialog)
        m.addAction("Add What's Playing to Playlist", QtGui.QKeySequence("Ctrl+Shift+P"), self.playlist_add_current)
        m.addSeparator()
        m.addAction("Export Comments to PDF…", QtGui.QKeySequence("Ctrl+E"), self.export_pdf)
        m = self.menuBar().addMenu("Edit")
        m.addAction("Undo Stroke", QtGui.QKeySequence.Undo, self.undo_stroke)
        m.addAction("New Comment", QtGui.QKeySequence("Ctrl+N"), self.new_note)
        m.addAction("Add Reference Image…", QtGui.QKeySequence("Ctrl+Shift+I"), lambda: self.add_ref_image(None))
        m.addAction("Paste Image as Reference", QtGui.QKeySequence.Paste, self.paste_ref_image)
        m.addAction("Drawing Panel", QtGui.QKeySequence("Ctrl+D"), lambda: self.draw_fold.set_open(not self.draw_fold.open))
        m.addAction("Comments Panel", QtGui.QKeySequence("Ctrl+P"), lambda: self.notes_fold.set_open(not self.notes_fold.open))
        m.addAction("Playlist Panel", QtGui.QKeySequence("Ctrl+L"), lambda: self.pl_fold.set_open(not self.pl_fold.open))
        m.addSeparator()
        self.act_cmp_swap = m.addAction("Swap A and Top Layer", QtGui.QKeySequence("Ctrl+Shift+S"),
                                        lambda: self.layers and self.layer_action(self.layers[-1], "swap"))
        m.addAction("Reset Split", QtGui.QKeySequence("Ctrl+Shift+R"), self.viewer.reset_wipe)
        self.act_cmp_clear = m.addAction("Remove All Layers", QtGui.QKeySequence("Ctrl+Shift+X"), self.clear_layers)
        self.act_cmp_swap.setEnabled(False)
        self.act_cmp_clear.setEnabled(False)
        m.addSeparator()
        m.addAction("Set In Point  (I)", self.mark_in)
        m.addAction("Set Out Point  (O)", self.mark_out)
        self.act_io_clear = m.addAction("Clear In / Out  (Alt+X)", lambda: self.set_io(None, None))
        self.act_io_clear.setEnabled(False)
        m.addSeparator()
        m.addAction("Previous in Playlist", QtGui.QKeySequence("PgUp"), lambda: self.playlist.step(-1))
        m.addAction("Next in Playlist", QtGui.QKeySequence("PgDown"), lambda: self.playlist.step(1))
        m = self.menuBar().addMenu("Help")
        m.addAction(f"What's New in {'.'.join(APP_VERSION.split('.')[:2])}", self.show_release_notes)
        a = m.addAction("About MOOOV_player", self.show_about)
        a.setMenuRole(QtGui.QAction.AboutRole)  # на Mac уходит в меню приложения
        m.addAction(f"Visit {SITE}", lambda: ui.open_url(QtCore.QUrl(SITE_URL)))

        QtWidgets.QApplication.instance().installEventFilter(self)  # пробел = Play/Stop везде

        self.save_timer = QtCore.QTimer(self, singleShot=True, interval=400)
        self.save_timer.timeout.connect(self.save_notes)

        self.timer = QtCore.QTimer(self, singleShot=True, timerType=QtCore.Qt.PreciseTimer)
        self.timer.timeout.connect(self.tick)
        self.ui_timer = QtCore.QTimer(self, interval=250)
        self.ui_timer.timeout.connect(self.refresh_ui)
        self.ui_timer.start()

        self.shortcuts = {
            QtCore.Qt.Key_Space: self.toggle_play,
            QtCore.Qt.Key_Right: lambda: self.step(1),
            QtCore.Qt.Key_Left: lambda: self.step(-1),
            QtCore.Qt.Key_Up: lambda: self.step(10),
            QtCore.Qt.Key_Down: lambda: self.step(-10),
            QtCore.Qt.Key_Comma: lambda: self.step(-1),
            QtCore.Qt.Key_Period: lambda: self.step(1),
            QtCore.Qt.Key_Home: self.go_start,
            QtCore.Qt.Key_End: self.go_end,
            QtCore.Qt.Key_O: self.mark_out,
            QtCore.Qt.Key_F: self.viewer.fit_view,
            QtCore.Qt.Key_1: lambda: self.viewer.set_zoom(1.0),
            QtCore.Qt.Key_2: lambda: self.viewer.set_zoom(2.0),
            QtCore.Qt.Key_Minus: lambda: self.viewer.set_zoom((self.viewer.zoom if not self.viewer.fit else self.viewer._scale() * self.viewer.devicePixelRatioF()) / 1.5),
            QtCore.Qt.Key_Equal: lambda: self.viewer.set_zoom((self.viewer.zoom if not self.viewer.fit else self.viewer._scale() * self.viewer.devicePixelRatioF()) * 1.5),
            QtCore.Qt.Key_BracketLeft: lambda: self.exposure.setValue(self.exposure.value() - 0.5),
            QtCore.Qt.Key_BracketRight: lambda: self.exposure.setValue(self.exposure.value() + 0.5),
            QtCore.Qt.Key_Backspace: lambda: self.delete_ref_image() or self.exposure.setValue(0),
            QtCore.Qt.Key_Delete: self.delete_ref_image,
            QtCore.Qt.Key_C: lambda: self.channel.setCurrentText("RGB"),
            QtCore.Qt.Key_R: lambda: self.toggle_channel("R"),
            QtCore.Qt.Key_G: lambda: self.toggle_channel("G"),
            QtCore.Qt.Key_B: lambda: self.toggle_channel("B"),
            QtCore.Qt.Key_A: lambda: self.toggle_channel("A"),
            QtCore.Qt.Key_L: lambda: self.chk_loop.toggle(),
            QtCore.Qt.Key_H: lambda: self.chk_half.toggle(),
            QtCore.Qt.Key_M: self.cycle_matte,
            QtCore.Qt.Key_I: self.mark_in,
            QtCore.Qt.Key_D: lambda: self.set_tool(None if self.viewer.tool else (self.last_tool or "pen")),
            QtCore.Qt.Key_N: self.new_note,
            QtCore.Qt.Key_E: lambda: self.set_tool(None if self.viewer.tool == "eraser" else "eraser"),
            QtCore.Qt.Key_Escape: lambda: self.set_tool(None),
            QtCore.Qt.Key_Tab: lambda: self.notes_fold.set_open(not self.notes_fold.open),
            QtCore.Qt.Key_P: lambda: self.pl_fold.set_open(not self.pl_fold.open),
        }

    # ---- open
    def open_file_dialog(self):
        exts = " ".join("*" + e for e in sorted(IMG_EXT | VID_EXT))
        p, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Open", "", f"Media ({exts});;All files (*)")
        if p:
            self.open(p)

    def add_file_dialog(self):
        exts = " ".join("*" + e for e in sorted(IMG_EXT | VID_EXT))
        ps, _ = QtWidgets.QFileDialog.getOpenFileNames(self, "Add to timeline", "", f"Media ({exts});;All files (*)")
        if ps:
            self.add_paths(ps)

    def open_dir_dialog(self):
        p = QtWidgets.QFileDialog.getExistingDirectory(self, "Sequence folder")
        if p:
            self.open(p)

    def open(self, path):
        self.open_paths([path])

    # ---- playlist
    def _toggle_playlist(self, on):
        self.playlist.setVisible(on)
        self.settings.setValue("fold_playlist", "true" if on else "false")

    def open_from_playlist(self, path):
        if not os.path.exists(path):
            self.statusBar().showMessage(f"File not found: {path}", 4000)
            return
        if self.src and not isinstance(self.src, CompositeSource) and self.playlist.is_current(path):
            return  # уже играет
        self.open_paths([path])

    def playlist_add_dialog(self):
        exts = " ".join("*" + e for e in sorted(IMG_EXT | VID_EXT))
        ps, _ = QtWidgets.QFileDialog.getOpenFileNames(self, "Add to playlist", "", f"Media ({exts});;All files (*)")
        if ps:
            self.add_to_playlist(ps)

    def playlist_add_current(self):
        if self.src:
            self.add_to_playlist([src_path(c) for c in self.clips()])

    def add_to_playlist(self, paths):
        n = self.playlist.insert_paths(paths)
        self.pl_fold.set_open(True)
        self.statusBar().showMessage(f"Added {n} to “{self.playlist.store.name}”" if n else "Already in the playlist", 2500)

    def _sync_playlist(self):
        """Подсветить в плейлисте то, что играет, и обновить счётчики комментариев."""
        if isinstance(self.src, CompositeSource):
            self.playlist.set_current(None, None)
            counts = {c.sidecar: 0 for c in self.src.clips}
            for n in self.store.notes:
                if not n.is_empty():
                    counts[self.src.clips[self.src.locate(n.frame)[0]].sidecar] += 1
        elif self.src:
            self.playlist.set_current(self.src.sidecar, src_path(self.src))
            counts = {self.src.sidecar: sum(1 for n in self.store.notes if not n.is_empty())}
        else:
            return
        for sc, k in counts.items():
            self.playlist.set_comment_count(sc, k)

    def _load(self, paths):
        """Пути -> источники; один и тот же клип (секвенция/видео) дважды не берём."""
        out, seen, errs = [], set(), []
        for p in paths:
            try:
                src = open_source(p)
            except Exception as e:
                errs.append(f"{os.path.basename(p)}: {e}")
                continue
            if src.sidecar not in seen:
                seen.add(src.sidecar)
                out.append(src)
        if errs:
            QtWidgets.QMessageBox.warning(self, "MOOOV_player", "\n".join(errs))
        return out

    def clips(self):
        if isinstance(self.src, CompositeSource):
            return list(self.src.clips)
        return [self.src] if self.src else []

    def open_paths(self, paths):
        """Открыть заново: один файл — как обычно, несколько — сразу монтаж."""
        srcs = self._load(paths)
        if srcs:
            self.set_source(srcs[0] if len(srcs) == 1 else CompositeSource(srcs))

    def add_paths(self, paths):
        """Добавить клипы в конец таймлайна."""
        if not self.src:
            return self.open_paths(paths)
        have = {c.sidecar for c in self.clips()}
        new = [s for s in self._load(paths) if s.sidecar not in have]
        if not new:
            self.statusBar().showMessage("Already on the timeline", 2500)
            return
        self._rebuild(self.clips() + new)

    def move_clip(self, k, to):
        """Переставить клип k на место to (to — позиция-«щель» 0..len)."""
        clips = self.clips()
        if not (0 <= k < len(clips)) or to in (k, k + 1):
            return
        c = clips.pop(k)
        clips.insert(to - 1 if to > k else to, c)
        self._rebuild(clips)

    def remove_clip(self, k):
        clips = self.clips()
        if len(clips) < 2 or not (0 <= k < len(clips)):
            return
        clips.pop(k)
        self._rebuild(clips)

    def _rebuild(self, clips):
        """Пересобрать монтаж, оставив плейхед на том же кадре того же клипа (если он остался)."""
        anchor = None
        if isinstance(self.src, CompositeSource):
            k, j = self.src.locate(self.frame)
            anchor = (self.src.clips[k], j)
        elif self.src:
            anchor = (self.src, self.frame)
        new = clips[0] if len(clips) == 1 else CompositeSource(clips)
        frame = 0
        if anchor and anchor[0] in clips:
            off = new.offsets[clips.index(anchor[0])] if isinstance(new, CompositeSource) else 0
            frame = off + anchor[1]
        self.set_source(new, frame=frame, fresh=False)

    def set_source(self, src, frame=0, fresh=True):
        self.stop()
        self.viewer.bubble.close_edit()
        self.save_notes()
        self.edit_log, self._erase_batch = [], []  # отмена — в пределах одного клипа
        if self.loader:
            self.loader.close()
        self.src = src
        if fresh:
            self.clear_layers()
        comp = isinstance(src, CompositeSource)
        self.store = ann.CompositeStore(src) if comp else ann.NoteStore(src.sidecar)
        self.active = None
        self.update_aovs(src)
        self.loader = Loader(src, self.budget, self._params(self.base_look))
        self.loader.bridge.ready.connect(self.on_ready)
        if fresh:
            self.fps.setValue(src.fps)
        self.timeline.n = src.n
        self.timeline.active = True
        self.timeline.numbers = src.numbers
        self.timeline.set_clips([(o, c.n, c.file_label) for o, c in zip(src.offsets, src.clips)] if comp else None)
        if fresh:
            self.set_io(None, None)
        else:
            self.set_io(self.io_in, self.io_out)  # клип стал другой длины — подрезаем
        self.spin_frame.blockSignals(True)
        self.spin_frame.setRange(src.numbers[0], src.numbers[-1])
        ui.fit_spin(self.spin_frame, "8" * max(4, len(str(src.numbers[-1]))))
        nd = len(str(src.n))
        ui.fit_label(self.lbl_frame, f"{'8' * nd} / {'8' * nd}  88:88:88:88")
        self.bar.invalidate()
        self.spin_frame.blockSignals(False)
        self.setWindowTitle(f"MOOOV_player — {src.name}")
        if self.layers:
            self._layers_changed()
        self.info_cache = {}
        if fresh:
            self.viewer.img = None
            self.viewer.fit_view()
        self.refresh_notes()
        self.scrub(min(frame, src.n - 1))
        self._sync_playlist()

    @staticmethod
    def _drop_paths(e):
        return [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]

    def _drop_side(self, e):
        r = self.drop.geometry()
        return self.drop.side_at(e.position() - QtCore.QPointF(r.topLeft()))

    def _ref_drop(self, e):
        """С инструментом Image картинку, брошенную на кадр, кладём референсом, без меню Open/Timeline."""
        paths = self._drop_paths(e)
        return (self.viewer.tool == "image" and self.viewer.img is not None and len(paths) == 1
                and os.path.splitext(paths[0])[1].lower() in IMG_EXT | {".gif"})

    def _accept_drop(self, e):
        """Строку из своего плейлиста — копией (иначе список её удалит), файлы из Проводника — как предложено."""
        if e.source() is not None:
            e.setDropAction(QtCore.Qt.CopyAction)
            e.accept()
        else:
            e.acceptProposedAction()

    def _over_timeline(self, e):
        """Точка броска на таймлайне (в его координатах) или None; пока ничего не открыто — таймлайна нет."""
        if not self.src:
            return None
        pt = self.timeline.mapFrom(self, e.position().toPoint())
        return QtCore.QPointF(pt) if self.timeline.rect().contains(pt) else None

    def dragEnterEvent(self, e):
        paths = self._drop_paths(e)
        if not paths:
            return
        self._accept_drop(e)
        if self._ref_drop(e):
            return
        if (pt := self._over_timeline(e)) is not None:
            self.drop.hide()
            self.timeline.set_drop_hint(pt)
            return
        self.drop.setGeometry(QtCore.QRect(self.left_col.mapTo(self, QtCore.QPoint(0, 0)), self.left_col.size()))
        self.drop.count = len(paths)
        self.drop.has_media = self.src is not None
        self.drop.update()
        self.drop.side = self._drop_side(e)
        self.drop.show()
        self.drop.raise_()

    def dragMoveEvent(self, e):
        self._accept_drop(e)
        if self._ref_drop(e):
            return
        if (pt := self._over_timeline(e)) is not None:
            self.drop.hide()
            self.timeline.set_drop_hint(pt)
            return
        self.timeline.set_drop_hint(None)
        if not self.drop.isVisible():
            self.drop.setGeometry(QtCore.QRect(self.left_col.mapTo(self, QtCore.QPoint(0, 0)), self.left_col.size()))
            self.drop.count = len(self._drop_paths(e))
            self.drop.has_media = self.src is not None
            self.drop.show()
            self.drop.raise_()
        side = self._drop_side(e)
        if side != self.drop.side:
            self.drop.side = side
            self.drop.update()

    def dragLeaveEvent(self, e):
        self.drop.hide()
        self.timeline.set_drop_hint(None)

    def dropEvent(self, e):
        self.drop.hide()
        self.timeline.set_drop_hint(None)
        paths = self._drop_paths(e)
        if not paths:
            return
        self._accept_drop(e)
        if not self._ref_drop(e) and (pt := self._over_timeline(e)) is not None:
            kind, frame = self.timeline.drop_target(pt)
            if kind == "layer":
                QtCore.QTimer.singleShot(0, lambda: self.layer_paths_at(paths, frame))
            else:
                QtCore.QTimer.singleShot(0, lambda: self.edit_paths(paths, kind == "start"))
            return
        if self._ref_drop(e):
            img = self.load_ref(paths[0])
            pos = self.viewer._norm(self.viewer.mapFrom(self, e.position().toPoint()).toPointF())
            if img is not None:
                QtCore.QTimer.singleShot(0, lambda: self.add_ref_image(pos, img))
            return
        side = self._drop_side(e)
        # дать окну перерисоваться без оверлея до загрузки
        act = {DropOverlay.OPEN: self.open_paths, DropOverlay.EDIT: self.add_paths,
               DropOverlay.PLAYLIST: self.add_to_playlist, DropOverlay.LAYER: self.layer_paths}[side]
        QtCore.QTimer.singleShot(0, lambda: act(paths))

    # ---- params
    def _chan_aov(self):
        """Один список «Канал»: основные каналы (RGB, R, G, B, A, Luma), под чертой — слои EXR (AOV).
        Выбран слой → показываем его RGB; выбран канал → основной слой."""
        layer = self.channel.currentData()
        return ("RGB", layer) if layer else (self.channel.currentText(), None)

    def current_params(self):
        ch, aov = self._chan_aov()
        return (self.exposure.value(), ch, self.view.currentText(), self.chk_half.isChecked(), aov)

    def _params(self, L=None):
        """Параметры декодирования клипа: общие + своя яркость слоя (или A)."""
        e, ch, view, half, aov = self.current_params()
        if L is not None and L is not self.base_look:  # у слоя стопки — свой канал / AOV, у A — из списка внизу
            aov = L.aov
            ch = "RGB" if aov else L.channel
        return (e + (L.ev if L is not None else 0.0), ch, view, half, aov)

    def _set_base_chan(self, channel="RGB", layer=None):
        """Канал или слой EXR основного клипа A — в списке внизу (после смены A на другой клип или с полоски)."""
        i = self.channel.findData(layer) if layer else self.channel.findText(channel)
        self.channel.setCurrentIndex(max(0, i))

    @staticmethod
    def _clip_aovs(src):
        """Слои EXR клипа (без основного)."""
        if isinstance(src, SequenceSource):
            return [nm for nm in exr_layers(src.items[0][0]) if nm != BASE_LAYER]
        return []

    def params_changed(self, *_):
        if self.loader:
            self.loader.set_params(self._params(self.base_look))
            self.loader.set_playhead(self.frame)
        for L in self.layers:
            L.loader.set_params(self._params(L))
            L.loader.set_playhead(L.index(self.frame) or 0)
        if self.layers:  # канал A виден и на его полоске
            self._layers_timeline()

    def view_changed(self, *_):
        """Показ (sRGB / Linear / OCIO) — запоминаем."""
        self.settings.setValue("view", self.view.currentText())
        if hasattr(self, "bar"):
            self.bar.invalidate()
        self.params_changed()

    def matte_changed(self, *_):
        self.viewer.matte = MATTES.get(self.matte.currentText())
        tr = self.matte_op.currentData() or 0
        self.viewer.matte_alpha = 1 - tr / 100
        self.matte_op.setEnabled(self.viewer.matte is not None)
        self.settings.setValue("matte", self.matte.currentText())
        self.settings.setValue("matte_tr", tr)
        self.viewer.update()

    def play_anchor(self):
        """Смещение центра Play от левого края центрального блока — всегда одним способом
        (по фиксированным размерам), чтобы раскладка не «прыгала» после первой перерисовки."""
        def ww(w):
            return w.minimumWidth() if w.minimumWidth() == w.maximumWidth() else w.sizeHint().width()
        x = ww(self.spin_frame) + self.bar.center.widget().layout().spacing()
        for b in (self.btn_first, self.btn_prev):
            x += ww(b) + self.transport.layout().spacing()
        return x + ww(self.btn_play) / 2

    def update_aovs(self, src):
        """Слои EXR текущего клипа — в список; один слой — список прячется."""
        clips = src.clips if isinstance(src, CompositeSource) else [src]
        names = []
        for c in clips:
            if isinstance(c, SequenceSource):
                for nm in exr_layers(c.items[0][0]):
                    if nm not in names:
                        names.append(nm)
        extra = [nm for nm in names if nm != BASE_LAYER]
        old = [self.channel.itemData(i) for i in range(self.channel.count()) if self.channel.itemData(i)]
        if extra == old:
            return
        cur_ch, cur_layer = self._chan_aov()
        cb = self.channel
        cb.blockSignals(True)
        cb.clear()
        cb.addItems(list(CHANNELS))
        if extra:  # слои EXR — в том же списке, под чертой
            cb.insertSeparator(cb.count())
            for nm in extra:
                cb.addItem(nm, nm)
        i = cb.findData(cur_layer) if cur_layer else cb.findText(cur_ch)
        cb.setCurrentIndex(max(0, i))
        cb.blockSignals(False)
        cb.setToolTip("Channel · EXR layers (AOV) below the line" if extra else "")
        ui.fit_combo(cb)
        self.bar.invalidate()
        if cur_layer and i < 0:  # выбранного слоя в новом клипе нет — обратно к основному
            self.params_changed()

    def step_aov(self, d):
        """Следующий / предыдущий слой EXR (с основным — RGB)."""
        idx = [self.channel.findText("RGB")] + [i for i in range(self.channel.count()) if self.channel.itemData(i)]
        if len(idx) > 1:
            k = idx.index(self.channel.currentIndex()) if self.channel.currentIndex() in idx else 0
            self.channel.setCurrentIndex(idx[(k + d) % len(idx)])

    def toggle_info(self, on):
        self.settings.setValue("show_info", "true" if on else "false")
        self.update_info()
        self.viewer.update()

    @staticmethod
    def _info_of(clip):
        try:
            return clip.info()
        except Exception as e:
            return {"name": clip.name, "path": "", "format": f"? ({e})", "w": 0, "h": 0,
                    "bits": "?", "range": "?", "size": "?"}

    def _clip_info(self, clip):
        """Метаданные клипа. У секвенции (по сети это секунды: первый кадр целиком + размеры всех файлов)
        считаем в фоне, а пока показываем то, что известно сразу."""
        d = self.info_cache.get(id(clip))
        if d is not None:
            return d
        if not isinstance(clip, SequenceSource):
            d = self.info_cache[id(clip)] = self._info_of(clip)
            return d
        nums = clip.numbers
        d = {"name": clip.file_label, "path": os.path.dirname(clip.items[0][0]), "format": "…", "w": 0, "h": 0,
             "bits": "…", "range": f"{nums[0]}–{nums[-1]}", "size": "…"}
        cache = self.info_cache[id(clip)] = d
        if not hasattr(self, "_info_bridge"):
            self._info_bridge = Bridge()
            self._info_bridge.ready.connect(self._info_done)

        def job(store=self.info_cache, key=id(clip)):
            store[key] = self._info_of(clip)
            self._info_bridge.ready.emit(0)
        threading.Thread(target=job, daemon=True).start()
        return cache

    def _info_done(self, _):
        self.update_info()
        self.viewer.update()

    def update_info(self):
        """Строки плашки Info: имя, путь, формат, разрешение, битность, кадры, длительность, размер.
        В монтаже — про клип под плейхедом + строка Edit."""
        if not self.src or not self.btn_info.isChecked():
            self.viewer.info = None
            return
        comp = isinstance(self.src, CompositeSource)
        k, j = self.src.locate(self.frame) if comp else (0, self.frame)
        clip = self.src.clips[k] if comp else self.src
        d = self._clip_info(clip)
        n, fps = clip.n, self.fps.value()
        rows = []
        if comp:
            o = self.src.offsets[k]
            rows.append(("Edit", f"clip {k + 1} of {len(self.src.clips)}   ·   {o + 1}–{o + n} of {self.src.n}"
                                 f"   ·   total {self.src.n / fps:.2f} s"))
        rows += [("Name", d["name"]), ("Path", d["path"])]
        if isinstance(clip, SequenceSource):
            rows.append(("File", clip.frame_file(j)))
        rows.append(("Format", d["format"]))
        cs = clip.color_space() if isinstance(clip, SequenceSource) else None
        if cs:  # что записано в заголовке EXR — подсказка, какой пункт ACES выбрать
            rows.append(("Colour", f"{cs}  (from the file header)"))
        n_aov = sum(1 for i in range(self.channel.count()) if self.channel.itemData(i))
        if n_aov:
            rows.append(("Layer", f"{self._chan_aov()[1] or BASE_LAYER}   ·   {n_aov + 1} layers in the file"))
        if d["h"]:
            rows.append(("Resolution", f"{d['w']} × {d['h']}   ({d['w'] / d['h']:.2f}:1)"))
        rows += [("Bit depth", d["bits"]),
                 ("Frames", f"{d['range']}   ·   {n} frames"),
                 ("Duration", f"{n / fps:.2f} s   ·   {self.timecode(n)} @ {fps:g} fps"),
                 ("Size", d["size"])]
        self.viewer.info = rows
        self.viewer.info_path = d["path"]

    def cycle_matte(self):
        self.matte.setCurrentIndex((self.matte.currentIndex() + 1) % self.matte.count())

    def toggle_channel(self, ch):
        self.channel.setCurrentText("RGB" if self.channel.currentText() == ch else ch)

    # ---- playback
    def show_frame(self, i):
        b = self.viewer.bubble
        if b.note is not None and b.note.frame != i:
            b.close_edit()  # ушли с кадра комментария — редактор закрывается
        self.frame = i
        self.timeline.frame = i
        self.loader.set_playhead(i)
        img = self.loader.get(i)
        if img is not None:
            self.viewer.set_image(img)
        for L in self.layers:
            j = L.index(i)
            if j is None:  # слой ещё не начался (сдвинут вправо)
                L.img = None
                continue
            L.loader.set_playhead(j)
            b = L.loader.get(j)
            if isinstance(b, QtGui.QImage):
                L.img = b
        if self.layers:
            self.viewer.update()
        self.spin_frame.blockSignals(True)
        self.spin_frame.setValue(self.src.numbers[i])
        self.spin_frame.blockSignals(False)
        self.lbl_frame.setText(f"{i + 1} / {self.src.n}  {self.timecode(i)}")
        self.update_info()
        self.update_overlay()
        self.timeline.update()

    def scrub(self, i):
        if self.src:
            self.show_frame(max(0, min(self.src.n - 1, i)))

    def timecode(self, i):
        fps = self.fps.value()
        r = max(1, round(fps))
        t = i
        return f"{t // (r * 3600):02d}:{t // (r * 60) % 60:02d}:{t // r % 60:02d}:{t % r:02d}"

    def goto(self, i):
        if self.src:
            self.stop()
            self.show_frame(i % self.src.n)

    # ---- In / Out: кусок, который смотрим и крутим в петле
    def play_range(self):
        if not self.src:
            return 0, 0
        lo = self.io_in if self.io_in is not None else 0
        hi = self.io_out if self.io_out is not None else self.src.n - 1
        return lo, hi

    def set_io(self, a, b):
        n = self.src.n if self.src else 1
        a = None if a is None else max(0, min(n - 1, a))
        b = None if b is None else max(0, min(n - 1, b))
        if a is not None and b is not None and a > b:
            a, b = b, a
        self.io_in, self.io_out = a, b
        on = a is not None or b is not None
        self.timeline.io = self.play_range() if on else None
        self.btn_in.setChecked(a is not None)
        self.btn_out.setChecked(b is not None)
        if hasattr(self, "act_io_clear"):
            self.act_io_clear.setEnabled(on)
        self.timeline.update()
        if on and self.src:
            lo, hi = self.play_range()
            nums = self.src.numbers
            self.statusBar().showMessage(f"In {nums[lo]}  ·  Out {nums[hi]}  ·  {hi - lo + 1} frames", 3000)

    def mark_in(self, toggle=False):
        if not self.src:
            return
        if toggle and self.io_in == self.frame:
            return self.set_io(None, self.io_out)
        b = self.io_out if self.io_out is not None and self.io_out >= self.frame else None
        self.set_io(self.frame, b)

    def mark_out(self, toggle=False):
        if not self.src:
            return
        if toggle and self.io_out == self.frame:
            return self.set_io(self.io_in, None)
        a = self.io_in if self.io_in is not None and self.io_in <= self.frame else None
        self.set_io(a, self.frame)

    def go_start(self):
        lo, _ = self.play_range()
        self.goto(lo if lo != self.frame else 0)

    def go_end(self):
        _, hi = self.play_range()
        self.goto(hi if hi != self.frame else -1)

    def frame_number_entered(self, num):
        if not self.src:
            return
        import bisect
        nums = self.src.numbers
        k = bisect.bisect_left(nums, num)
        if k >= len(nums) or (k > 0 and num - nums[k - 1] < nums[k] - num):
            k -= 1
        self.spin_frame.clearFocus()
        self.goto(max(0, k))

    def step(self, d):
        if not self.src:
            return
        self.stop()
        n = self.src.n
        i = self.frame + d
        i = i % n if self.chk_loop.isChecked() else max(0, min(n - 1, i))
        self.show_frame(i)

    def on_ready(self, i):
        if i == self.frame:
            self.viewer.set_image(self.loader.get(i))

    # ---- слои: клипы стопкой поверх A (прозрачность, режим наложения, видимость, шторка)
    def layer_paths(self, paths):
        if not self.src:
            return self.open_paths(paths)
        self.add_layers(self._load(paths))

    def add_layer_dialog(self):
        exts = " ".join("*" + e for e in sorted(IMG_EXT | VID_EXT))
        ps, _ = QtWidgets.QFileDialog.getOpenFileNames(self, "Add layers", "", f"Media ({exts});;All files (*)")
        if ps:
            self.layer_paths(ps)

    def add_layers(self, srcs, offset=0):
        """Каждый клип — новый слой сверху: Normal 100%, без шторки (сравнение включается иконкой на полоске)."""
        srcs = [s_ for s_ in srcs if s_ is not None]
        if not srcs or not self.src:
            return
        self.stop()
        for src in srcs:
            L = Layer(src, None, offset=offset)
            L.loader = loader = Loader(src, self.budget // 4, self._params(L), alpha=True)
            loader.bridge.ready.connect(lambda j, L=L: self.on_layer_ready(L, j))
            self.layers.append(L)
        self._layers_changed()
        odd = [s_.file_label for s_ in srcs if s_.n != self.src.n]
        if odd:
            self.statusBar().showMessage(f"Different length from {self.src.file_label} ({self.src.n} fr): "
                                         + ", ".join(odd), 5000)
        self.show_frame(self.frame)

    def _layers_changed(self):
        """Состав стопки поменялся: буквы, память, панель, дорожки, меню."""
        for k, L in enumerate(self.layers):
            L.tag = chr(ord("B") + k) if k < 25 else "+"
        n = len(self.layers)
        if self.loader:
            self.loader.set_budget(self.budget // 2 if n else self.budget)
        for L in self.layers:  # слоям — вторая половина памяти поровну
            L.loader.set_budget(max(self.budget // (2 * n), 64 * 1024**2))
        base = self.src.file_label if self.src else ""
        if not n:  # без слоёв полоски A нет — и вид A обычный, чтобы кадр не пропал без управления
            B = self.base_look
            B.visible, B.opacity, B.mode = True, 1.0, "Normal"
            if B.ev:
                B.ev = 0.0
                self.params_changed()
        self.viewer.set_layers(self.layers, base, self.base_look)
        self._layers_timeline()
        self.act_cmp_clear.setEnabled(bool(n))
        self.act_cmp_swap.setEnabled(bool(n))

    def _layers_timeline(self):
        B = self.base_look
        self.timeline.set_layers([(L.length, L.name, L.tag, L.visible, L.mode, L.opacity, L.split, L.offset, L.ev,
                                   self._chan_label(L), L.trim_in, L.trim_out, L.src.n) for L in reversed(self.layers)],
                                 self.src.file_label if self.src else "",
                                 (B.visible, B.mode, B.opacity, B.ev, self._chan_label(B)))

    def _layer_at(self, k):
        """Полоска k на таймлайне (сверху вниз) → слой; нижняя полоска — вид A."""
        return self.base_look if k >= len(self.layers) else self.layers[len(self.layers) - 1 - k]

    def move_layer(self, k, t):
        """Перетащили полоску k → t (сверху вниз). Нижняя полоска — A: если вниз встал слой,
        он становится основным клипом, а A — слоем на его месте (с его режимом и прозрачностью)."""
        order = list(reversed(self.layers)) + [None]  # None — A
        order.insert(t, order.pop(k))
        if order[-1] is None:
            self.layers = list(reversed(order[:-1]))
            return self._layers_changed()
        if isinstance(self.src, CompositeSource):
            self.statusBar().showMessage("The base can be swapped when a single clip is playing, not an edit", 3500)
            return
        L, a, frame, B = order[-1], self.src, self.frame, self.base_look
        new = Layer(a, None, B.opacity, B.mode, B.visible, ev=B.ev, aov=self._chan_aov()[1], channel=self._chan_aov()[0])  # каждый клип уносит свой вид
        new.loader = loader = Loader(a, self.budget // 4, self._params(new), alpha=True)
        B.opacity, B.mode, B.visible, B.ev = L.opacity, L.mode, L.visible, L.ev
        loader.bridge.ready.connect(lambda j, L=new: self.on_layer_ready(L, j))
        order[order.index(None)] = new
        self.layers = list(reversed(order[:-1]))
        L.loader.close()
        self.set_source(L.src, frame=min(frame, L.src.n - 1), fresh=False)
        self._set_base_chan(L.channel, L.aov)
        self._layers_changed()
        self.show_frame(self.frame)

    def mode_menu(self, L, pos):
        """Режимы наложения — по клику на режим прямо на полоске."""
        m = QtWidgets.QMenu(self)
        grp = QtGui.QActionGroup(m)
        for name in BLEND_MODES:
            b = m.addAction(name)
            b.setCheckable(True)
            b.setChecked(name == L.mode)
            grp.addAction(b)
            b.triggered.connect(lambda _=False, name=name: self.set_layer(L, mode=name))
        popup_above(m, pos)

    def layer_menu(self, L, pos):
        """Все настройки слоя: видимость, режим наложения, прозрачность, шторка, порядок, A ⇄ слой, убрать."""
        base = L is self.base_look
        if not base and L not in self.layers:
            return
        k = -1 if base else self.layers.index(L)
        m = QtWidgets.QMenu(self)
        title = m.addAction(f"{L.tag}   {self.src.file_label if base else L.name}")
        title.setEnabled(False)
        m.addSeparator()
        a = m.addAction("Visible")
        a.setCheckable(True)
        a.setChecked(L.visible)
        a.toggled.connect(lambda on: self.set_layer(L, visible=on))
        modes = m.addMenu(f"Blend mode:  {L.mode}")
        grp = QtGui.QActionGroup(modes)
        for name in BLEND_MODES:
            b = modes.addAction(name)
            b.setCheckable(True)
            b.setChecked(name == L.mode)
            grp.addAction(b)
            b.triggered.connect(lambda _=False, name=name: self.set_layer(L, mode=name))
        aovs = self._clip_aovs(self.src if base else L.src)
        if aovs:  # слой EXR (AOV) этого клипа — у каждого свой
            cur = self._chan_aov()[1] if base else L.aov
            sub = m.addMenu(f"EXR layer:  {cur or BASE_LAYER}")
            g2 = QtGui.QActionGroup(sub)
            for nm in [None] + aovs:
                b = sub.addAction(nm or BASE_LAYER)
                b.setCheckable(True)
                b.setChecked(nm == cur)
                g2.addAction(b)
                if nm is None:
                    sub.addSeparator()
                b.triggered.connect(lambda _=False, nm=nm: self._set_base_chan("RGB", nm) if base else self.set_layer(L, aov=nm, channel="RGB"))
        self._look_rows(m, L)
        if not base:
            self._offset_row(m, L)
        if base:
            m.addSeparator()
            m.addAction("Move up — the layer above becomes A", lambda: self.layer_action(L, "up"))
            m.addSeparator()
            m.addAction("Remove A — the layer above takes its place", lambda: self.layer_action(L, "remove"))
            popup_above(m, pos)
            return
        a = m.addAction("Split — show it on one side of a wipe")
        a.setCheckable(True)
        a.setChecked(L.split)
        a.toggled.connect(lambda on: self.set_layer(L, split=on))
        m.addSeparator()
        m.addAction("Move up", lambda: self.layer_action(L, "up")).setEnabled(k < len(self.layers) - 1)
        m.addAction("Move down — below A makes it the base" if k == 0 else "Move down", lambda: self.layer_action(L, "down"))
        m.addAction(f"Swap {L.tag} and A", lambda: self.layer_action(L, "swap"))
        m.addAction("Reset split", self.viewer.reset_wipe).setEnabled(L.split)
        m.addAction(f"Reset trim  ({L.trim_in} / {L.trim_out} fr)" if L.trim_in or L.trim_out else "Reset trim",
                    lambda: self.set_layer(L, trim_in=0, trim_out=0, offset=L.offset - L.trim_in)
                    ).setEnabled(bool(L.trim_in or L.trim_out))
        m.addSeparator()
        m.addAction("Put at the start of the edit — plays before A", lambda: self.layer_to_edit(L, True))
        m.addAction("Put at the end of the edit — plays after A", lambda: self.layer_to_edit(L, False))
        m.addSeparator()
        m.addAction("Remove layer", lambda: self.layer_action(L, "remove"))
        popup_above(m, pos)

    def _slider_row(self, m, label, lo, hi, value, fmt, on_change, step=1, page=10):
        """Строка «подпись — ползунок — значение» прямо в меню; меняет вживую."""
        row = QtWidgets.QWidget()
        h = QtWidgets.QHBoxLayout(row)
        h.setContentsMargins(18, 4, 14, 4)
        h.setSpacing(10)
        lab = QtWidgets.QLabel(label)
        ui.fit_label(lab, "Brightness")
        sl = QtWidgets.QSlider(QtCore.Qt.Horizontal, minimum=lo, maximum=hi, singleStep=step, pageStep=page)
        sl.setValue(value)
        sl.setFixedWidth(130)
        val = QtWidgets.QLabel(fmt(value))
        ui.tabular(val)
        ui.fit_label(val, max((fmt(lo), fmt(hi)), key=len) + "0")
        val.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)

        def changed(v):
            val.setText(fmt(v))
            on_change(v)
        sl.valueChanged.connect(changed)
        for x in (lab, sl, val):
            h.addWidget(x)
        wa = QtWidgets.QWidgetAction(m)
        wa.setDefaultWidget(row)
        m.addAction(wa)
        return sl

    def _look_rows(self, m, L):
        """Яркость (EV) и прозрачность слоя — ползунками в меню."""
        timer = QtCore.QTimer(m, singleShot=True, interval=90)  # яркость пересчитывает кадры — не на каждый пиксель
        want = [L.ev]
        timer.timeout.connect(lambda: self.set_layer(L, ev=want[0]) if L.ev != want[0] else None)

        def ev(v):
            want[0] = v / 10
            timer.start()
        self._slider_row(m, "Brightness", -40, 40, round(L.ev * 10), lambda v: f"{v / 10:+.1f} EV", ev, 1, 5)
        self._slider_row(m, "Opacity", 0, 100, round(L.opacity * 100), lambda v: f"{int(round(v / 5) * 5)}%",
                         lambda v: self.set_layer(L, opacity=int(round(v / 5) * 5) / 100), 5, 25)

    def _offset_row(self, m, L):
        """Сдвиг слоя по времени: с какого кадра A он начинается."""
        row = QtWidgets.QWidget()
        h = QtWidgets.QHBoxLayout(row)
        h.setContentsMargins(18, 4, 14, 4)
        h.setSpacing(10)
        lab = QtWidgets.QLabel("Offset")
        ui.fit_label(lab, "Brightness")
        sp = QtWidgets.QSpinBox(minimum=-(L.src.n - 1), maximum=max(0, self.src.n - 1), suffix=" fr")
        sp.setValue(L.offset)
        ui.small(sp)
        ui.fit_spin(sp, f"-{L.src.n} fr")
        sp.valueChanged.connect(lambda v: self.set_layer(L, offset=v))
        here = QtWidgets.QPushButton("Start here")
        ui.small(here)
        here.setToolTip("Layer starts at the playhead")
        here.clicked.connect(lambda: sp.setValue(self.frame))
        zero = QtWidgets.QPushButton("Sync")
        ui.small(zero)
        zero.setToolTip("Back in sync with A (offset 0)")
        zero.clicked.connect(lambda: sp.setValue(0))
        for x in (lab, sp, here, zero):
            h.addWidget(x)
        h.addStretch(1)
        wa = QtWidgets.QWidgetAction(m)
        wa.setDefaultWidget(row)
        m.addAction(wa)

    def look_menu(self, L, pos):
        """☀ на полоске: яркость, прозрачность, режим наложения, сдвиг, сброс."""
        base = L is self.base_look
        if not base and L not in self.layers:
            return
        m = QtWidgets.QMenu(self)
        m.addAction(f"{L.tag}   {self.src.file_label if base else L.name}").setEnabled(False)
        m.addSeparator()
        self._look_rows(m, L)
        modes = m.addMenu(f"Blend mode:  {L.mode}")
        grp = QtGui.QActionGroup(modes)
        for name in BLEND_MODES:
            b = modes.addAction(name)
            b.setCheckable(True)
            b.setChecked(name == L.mode)
            grp.addAction(b)
            b.triggered.connect(lambda _=False, name=name: self.set_layer(L, mode=name))
        if not base:
            self._offset_row(m, L)
        m.addSeparator()
        kw = dict(ev=0.0, opacity=1.0, mode="Normal") if base else dict(ev=0.0, opacity=1.0, mode="Normal", offset=0)
        m.addAction("Reset", lambda: self.set_layer(L, **kw))
        popup_above(m, pos)

    def info_popup(self, L, pos):
        """i на полоске: метаданные клипа слоя (или A)."""
        base = L is self.base_look
        if not base and L not in self.layers:
            return
        clip = self.src if base else L.src
        if isinstance(clip, CompositeSource):
            clip = clip.clips[clip.locate(self.frame)[0]]
        d = self._clip_info(clip)
        fps = self.fps.value()
        rows = [("Name", d["name"]), ("Path", d["path"]), ("Format", d["format"])]
        if d["h"]:
            rows.append(("Resolution", f"{d['w']} × {d['h']}   ({d['w'] / d['h']:.2f}:1)"))
        rows += [("Bit depth", d["bits"]), ("Frames", f"{d['range']}   ·   {clip.n} frames"),
                 ("Duration", f"{clip.n / fps:.2f} s   ·   {self.timecode(clip.n)} @ {fps:g} fps"), ("Size", d["size"])]
        look = f"{L.mode}   ·   {round(L.opacity * 100)}%   ·   {L.ev:+.1f} EV" + ("" if L.visible else "   ·   hidden")
        rows.append(("Look", look))
        rows.append(("Channel", self._chan_label(L)))
        n_aov = len(self._clip_aovs(clip))
        if n_aov:
            rows.append(("EXR layers", f"{n_aov + 1} in the file"))
        if not base:
            a = max(0, L.offset)
            rows.append(("Offset", f"{L.offset:+d} fr   ·   starts at frame {self.src.numbers[min(a, self.src.n - 1)]} of A"
                         if L.offset else "in sync with A"))
            if L.trim_in or L.trim_out:
                rows.append(("Trim", f"{L.trim_in} fr from the start   ·   {L.trim_out} fr from the end   ·   "
                                     f"{L.length} of {clip.n} frames"))
        rows.insert(0, ("Layer", L.tag))
        k = len(self.layers) if base else len(self.layers) - 1 - self.layers.index(L)  # полоска на таймлайне
        if not hasattr(self, "_info_pop"):
            self._info_pop = InfoPopup(self)
            self._info_pop.closed.connect(lambda: (setattr(self.timeline, "info_open", None), self.timeline.update()))
        self.timeline.info_open = k
        self.timeline.update()
        self._info_pop.show_rows(rows, d["path"], pos)

    def _chan_label(self, L):
        """Что показывает клип на полоске: канал (RGB, R, …, Luma) или слой EXR."""
        if L is self.base_look:
            return self.channel.currentText()
        return L.aov or L.channel

    def chan_menu(self, L, pos):
        """Канал или слой EXR (AOV) клипа — по клику на «RGB ▾» прямо на полоске. У A — то же, что список внизу."""
        base = L is self.base_look
        if not base and L not in self.layers:
            return
        cur = self._chan_label(L)
        m = QtWidgets.QMenu(self)
        grp = QtGui.QActionGroup(m)

        def add(text, ch, aov):
            b = m.addAction(text)
            b.setCheckable(True)
            b.setChecked(text == cur)
            grp.addAction(b)
            b.triggered.connect(lambda _=False: self._set_base_chan(ch, aov) if base
                                else self.set_layer(L, channel=ch, aov=aov))
        for ch in CHANNELS:
            add(ch, ch, None)
        aovs = self._clip_aovs(self.src if base else L.src)
        if aovs:
            m.addSeparator()
            for nm in aovs:
                add(nm, "RGB", nm)
        popup_above(m, pos)

    def set_layer(self, L, **kw):
        for key, v in kw.items():
            setattr(L, key, v)
        redecode = "ev" in kw or "aov" in kw or "channel" in kw
        if redecode:  # яркость / канал / слой EXR — заново декодируем кадры этого клипа
            loader = self.loader if L is self.base_look else L.loader
            if loader:
                loader.set_params(self._params(L))
                if L is not self.base_look:  # старая картинка висит, пока не готова новая
                    loader.set_playhead(L.index(self.frame) or 0)
        if redecode or "offset" in kw or "trim_in" in kw or "trim_out" in kw:
            if "trim_in" in kw and L is not self.base_look and L.loader:
                L.loader.set_playhead(L.index(self.frame) or L.trim_in)
            self.show_frame(self.frame)
        self.layer_changed(L)

    def layer_changed(self, L):
        self._layers_timeline()
        self.viewer.update()

    def layer_action(self, L, act):
        base = L is self.base_look
        if not base and L not in self.layers:
            return
        if act == "toggle":
            L.visible = not L.visible
            return self.layer_changed(L)
        if act == "split" and not base:  # шторка-сравнение: включается только руками
            L.split = not L.split
            if L.split and not any(x.split for x in self.layers if x is not L):
                self.viewer.reset_wipe()
            return self.layer_changed(L)
        n = len(self.layers)
        s = n if base else n - 1 - self.layers.index(L)  # полоска сверху вниз; n — A
        if act in ("up", "down"):
            t = s + (-1 if act == "up" else 1)
            if 0 <= t <= n:
                self.move_layer(s, t)
            return
        if base and act == "remove":
            return self.remove_base()
        if act == "remove":
            L.loader.close()
            self.layers.remove(L)
            self._layers_changed()
            return
        if act == "swap":
            self.swap_layer(L)

    def remove_base(self):
        """× у A: A уходит, нижний слой становится основным клипом (со своим видом)."""
        if not self.layers:
            return
        if isinstance(self.src, CompositeSource):
            self.statusBar().showMessage("A can be removed when a single clip is playing, not an edit", 3500)
            return
        L, frame, B = self.layers.pop(0), self.frame, self.base_look
        B.opacity, B.mode, B.visible, B.ev = L.opacity, L.mode, L.visible, L.ev
        L.loader.close()
        self.set_source(L.src, frame=min(frame, L.src.n - 1), fresh=False)
        self._set_base_chan(L.channel, L.aov)
        self._layers_changed()
        self.show_frame(self.frame)

    def swap_layer(self, L):
        """Слой ⇄ A: слой становится основным клипом (с его комментариями), A встаёт на его место в стопке."""
        if isinstance(self.src, CompositeSource):
            self.statusBar().showMessage("Swap works when a single clip is playing, not an edit", 3500)
            return
        a, k, frame, (a_ch, a_aov) = self.src, self.layers.index(L), self.frame, self._chan_aov()
        L.loader.close()
        self.set_source(L.src, frame=min(frame, L.src.n - 1), fresh=False)
        self._set_base_chan(L.channel, L.aov)
        B = self.base_look  # A уходит в стопку со своим видом, новый A — с видом слоя
        new = Layer(a, None, B.opacity, B.mode, B.visible, L.split, L.offset, B.ev, aov=a_aov, channel=a_ch)
        new.loader = loader = Loader(a, self.budget // 4, self._params(new), alpha=True)
        B.opacity, B.mode, B.visible, B.ev = L.opacity, L.mode, L.visible, L.ev
        self.loader.set_params(self._params(B))
        loader.bridge.ready.connect(lambda j, L=new: self.on_layer_ready(L, j))
        self.layers[k] = new
        self._layers_changed()
        self.show_frame(self.frame)

    def layer_to_edit(self, L, at_start):
        """Слой уходит из стопки в сам монтаж: в начало (играет до A) или в конец (после)."""
        if L is self.base_look or L not in self.layers:
            return
        clips = self.clips()
        if any(c.sidecar == L.src.sidecar for c in clips):
            self.statusBar().showMessage(f"{L.name} is already in the edit", 3500)
            return
        trimmed = bool(L.trim_in or L.trim_out)
        L.loader.close()
        self.layers.remove(L)
        self._insert_clips([L.src], at_start)
        self.statusBar().showMessage(f"{L.name} — {'start' if at_start else 'end'} of the edit"
                                     + ("  ·  the whole clip: trim applies to layers only" if trimmed else ""), 4500)

    def _insert_clips(self, srcs, at_start):
        """Клипы — в начало или конец монтажа; слои остаются на своих кадрах A."""
        clips = self.clips()
        if at_start:
            shift = sum(c.n for c in srcs)
            for X in self.layers:  # A уехал вправо — слои вместе с ним
                X.offset += shift
            # In / Out остаются на тех же кадрах A (монтаж применит их при пересборке)
            self.io_in, self.io_out = (None if v is None else v + shift for v in (self.io_in, self.io_out))
            self._rebuild(srcs + clips)
        else:
            self._rebuild(clips + srcs)
        self._layers_changed()
        self.show_frame(self.frame)

    def edit_paths(self, paths, at_start):
        """Файлы, брошенные на край таймлайна: в начало или конец монтажа."""
        if not self.src:
            return self.open_paths(paths)
        have = {c.sidecar for c in self.clips()}
        new = [x for x in self._load(paths) if x.sidecar not in have]
        if not new:
            self.statusBar().showMessage("Already on the timeline", 2500)
            return
        self._insert_clips(new, at_start)

    def layer_paths_at(self, paths, frame):
        """Файлы, брошенные на таймлайн: слоями, которые начинаются с этого кадра."""
        if not self.src:
            return self.open_paths(paths)
        self.add_layers(self._load(paths), offset=frame)

    def clear_layers(self):
        for L in self.layers:
            L.loader.close()
        self.layers = []
        if hasattr(self, "act_cmp_clear"):
            self._layers_changed()

    def on_layer_ready(self, L, j):
        if L in self.layers and self.src and j == L.index(self.frame):
            b = L.loader.get(j)
            if isinstance(b, QtGui.QImage):
                L.img = b
                self.viewer.update()

    def eventFilter(self, obj, e):
        t = e.type()
        if (t in (QtCore.QEvent.KeyPress, QtCore.QEvent.ShortcutOverride) and e.key() == QtCore.Qt.Key_Space
                and not e.modifiers() & ~QtCore.Qt.KeypadModifier
                and QtWidgets.QApplication.activeWindow() is self
                and QtWidgets.QApplication.activePopupWidget() is None):
            fw = QtWidgets.QApplication.focusWidget()
            typing = isinstance(fw, (QtWidgets.QTextEdit, QtWidgets.QPlainTextEdit)) or (
                isinstance(fw, QtWidgets.QLineEdit) and not isinstance(fw.parent(), QtWidgets.QAbstractSpinBox))
            if not typing:
                if t == QtCore.QEvent.KeyPress and not e.isAutoRepeat():
                    self.toggle_play()
                return True  # кнопки/списки/поля чисел не перехватывают пробел
        return super().eventFilter(obj, e)

    def toggle_play(self):
        if self.playing:
            self.stop()
        elif self.src:
            lo, hi = self.play_range()
            if not lo <= self.frame < hi and hi > lo:
                self.show_frame(lo)  # стоим вне куска или на его конце — играем с In
            self.playing = Loader.playing = True
            self.viewer.smooth = False
            self.btn_play.setIcon(ui.icon("pause", ui.ON_ACCENT))
            self.next_due = time.perf_counter()
            self.shown_times.clear()
            self.tick()

    def speed_changed(self, *_):
        if self.playing:
            self.next_due = time.perf_counter()
            self.shown_times.clear()

    def step_speed(self, d):
        self.speed.setCurrentIndex(max(0, min(self.speed.count() - 1, self.speed.currentIndex() + d)))

    def stop(self):
        self.playing = Loader.playing = False
        for ld in [getattr(self, "loader", None)] + [getattr(L, "loader", None) for L in getattr(self, "layers", [])]:
            if ld:
                ld.set_playhead(ld.playhead)  # разбудить: на паузе кадр под плейхедом снова первый
        self.timer.stop()
        self.btn_play.setIcon(ui.icon("play", ui.ON_ACCENT))
        self.viewer.smooth = True
        self.viewer.update()

    def tick(self):
        if not self.playing:
            return
        now = time.perf_counter()
        if now < self.next_due:
            self.timer.start(max(0, int((self.next_due - now) * 1000)))
            return
        lo, hi = self.play_range()
        n = hi + 1
        rate = self.fps.value() * (self.speed.currentData() or 1.0)  # кадров клипа в секунду
        step = max(1, math.ceil(rate / MAX_DISPLAY_FPS - 1e-6))
        self.play_step = step
        nxt = self.frame + step
        if nxt >= n:
            if not self.chk_loop.isChecked():
                if self.frame < n - 1:
                    nxt = n - 1
                else:
                    self.stop()
                    return
            else:
                nxt = lo
        if self.loader.has(nxt) and all(L.index(nxt) is None or L.loader.has(L.index(nxt)) for L in self.layers if L.visible):  # играем все кадры (до 60 fps): если не готов — ждём
            self.show_frame(nxt)
            self.shown_times.append(now)
            period = step / rate
            self.next_due += period
            if self.next_due < now - 2 * period:  # отстали — не догоняем рывками
                self.next_due = now + period
            self.timer.start(max(0, int((self.next_due - time.perf_counter()) * 1000)))
        else:
            self.loader.set_playhead(nxt)
            for L in self.layers:
                L.loader.set_playhead(L.index(nxt) or 0)
            self.timer.start(2)

    def refresh_ui(self):
        if not self.loader:
            return
        self.timeline.cached = self.loader.cached()
        self.timeline.update()
        parts = []
        if self.playing and len(self.shown_times) > 2:
            dt = self.shown_times[-1] - self.shown_times[0]
            if dt > 0:
                parts.append(f"{(len(self.shown_times) - 1) * self.play_step / dt:.1f} fps")
        parts.append(f"{self.loader.used / 1024**3:.1f} GB")
        self.lbl_info.setText("  ·  ".join(parts))
        self.lbl_info.setToolTip(f"Playback speed · cache memory\nCached frames: {len(self.timeline.cached)} / {self.src.n}")

    # ---- панель рисования (сверху) / комментариев (справа)

    def _fold(self, widget, key, on):
        widget.setVisible(on)

    def build_draw_bar(self):
        w = ui.surface(QtWidgets.QWidget(), "drawBar")
        row = FlowLayout(w, margins=(10, 6, 10, 6), hspace=22)
        tools = []
        self.tool_btns = {}
        self.last_tool = "pen"
        for key, tip in (("pen", "Brush (D)"), ("arrow", "Arrow"),
                         ("rect", "Rectangle"), ("ellipse", "Ellipse"),
                         ("eraser", f"Eraser (E) — drag over a drawing to erase it, {MOD}Z brings it back"),
                         ("sticker", "Stickers — pick one, then click the frame to put it; drag to move, corners to scale"),
                         ("image", f"Reference image ({MOD}{'⇧' if sys.platform == 'darwin' else 'Shift+'}I, paste {MOD}V) — click the frame to add, drag to move, "
                                   "corners to scale, Delete to remove")):
            b = ui.icon_button(key, tip, checkable=True)
            if key == "sticker":  # звезда — сначала выбрать стикер
                b.clicked.connect(lambda _=False, b=b: self.pick_sticker(b))
            else:
                b.clicked.connect(lambda on, k=key: self.set_tool(k if on else None))
            self.tool_btns[key] = b
            tools.append(b)
        undo = ui.icon_button("undo", f"Undo stroke or erase ({MOD}Z)")
        undo.clicked.connect(self.undo_stroke)
        row.addWidget(hgroup(*tools, undo))

        self.color_btns = []
        for c in ann.COLORS:
            b = QtWidgets.QPushButton()
            b.setCheckable(True)
            b.setToolTip(c)
            b.setFocusPolicy(QtCore.Qt.NoFocus)
            b.setFixedSize(22, 22)
            b.setStyleSheet(f"QPushButton {{ background:{c}; border:2px solid {ui.BORDER_HI}; border-radius:11px;"
                            f" min-height:18px; max-height:18px; min-width:18px; max-width:18px; padding:0; }}"
                            f"QPushButton:hover {{ border-color:{ui.DIM}; }}"
                            f"QPushButton:checked {{ border:2px solid {ui.TEXT}; }}")
            b.clicked.connect(lambda _, c=c: self.set_color(c))
            self.color_btns.append(b)
        row.addWidget(hgroup(*self.color_btns, spacing=8))

        self.brush = QtWidgets.QSpinBox(minimum=1, maximum=60, value=5)  # при каждом запуске — дефолты
        self.brush.setPrefix("size ")
        self.brush.setFocusPolicy(QtCore.Qt.ClickFocus)
        ui.steppers(self.brush)
        self.brush.setToolTip("Stroke size — scroll or type")
        self.brush.valueChanged.connect(self.set_brush)
        self.ghost = QtWidgets.QSpinBox(minimum=0, maximum=24, value=3)
        self.ghost.setPrefix("ghost ±")
        self.ghost.setSuffix(" fr")
        self.ghost.setToolTip("How many frames before/after a drawing stays visible, fading out")
        self.ghost.setFocusPolicy(QtCore.Qt.ClickFocus)
        ui.steppers(self.ghost)
        self.opacity = QtWidgets.QSpinBox(minimum=25, maximum=100, singleStep=25,
                                          value=100)
        self.opacity.setPrefix("opacity ")
        self.opacity.setSuffix("%")
        self.opacity.setToolTip("Opacity of new strokes and images — 25% steps")
        self.opacity.setFocusPolicy(QtCore.Qt.ClickFocus)
        self.opacity.setCorrectionMode(QtWidgets.QAbstractSpinBox.CorrectToNearestValue)
        ui.steppers(self.opacity)
        self.opacity.valueChanged.connect(self.set_opacity)
        ui.small(self.brush, self.opacity, self.ghost)  # вторичные — низкие, как EV / fps внизу
        for sp, sample in ((self.brush, "size 60"), (self.opacity, "opacity 100%"), (self.ghost, "ghost ±24 fr")):
            ui.fit_spin_live(sp, sample)
        self.ghost.valueChanged.connect(lambda v: self.update_overlay())
        row.addWidget(hgroup(self.brush, self.opacity, self.ghost, h=ui.HS))
        self.set_opacity(self.opacity.value())

        self.set_color(self.settings.value("color", ann.COLORS[0]))
        self.set_brush(self.brush.value())
        return w

    def build_comments(self):
        w = ui.surface(QtWidgets.QWidget(), "notesPanel")
        w.setFixedWidth(320)
        v = QtWidgets.QVBoxLayout(w)
        v.setContentsMargins(10, 8, 10, 10)
        v.setSpacing(8)

        def title(t):
            l = QtWidgets.QLabel(t)
            l.setObjectName("sectionTitle")
            return l

        v.addWidget(title("AUTHOR"))
        self.author = QtWidgets.QLineEdit(self.settings.value("author", "") or os.environ.get("USER", ""))
        self.author.setFocusPolicy(QtCore.Qt.ClickFocus)
        # только имя: длинный текст сюда попадает по ошибке вместо комментария и ставится автором всем
        self.author.setPlaceholderText("Your name")
        self.author.setMaxLength(40)
        self.author.setToolTip("Your name — shown on every comment and in the PDF")
        self.author.returnPressed.connect(self.author.clearFocus)
        self.author.textChanged.connect(lambda t: self.settings.setValue("author", t))
        v.addWidget(self.author)

        v.addWidget(title("COMMENTS"))
        self.cards = {}
        self.cards_box = QtWidgets.QWidget()
        self.cards_lay = QtWidgets.QVBoxLayout(self.cards_box)
        self.cards_lay.setContentsMargins(0, 0, 4, 0)
        self.cards_lay.setSpacing(6)
        self.cards_lay.addStretch(1)
        self.cards_scroll = QtWidgets.QScrollArea()
        self.cards_scroll.setWidgetResizable(True)
        self.cards_scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.cards_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.cards_scroll.setFocusPolicy(QtCore.Qt.NoFocus)
        self.cards_scroll.setWidget(self.cards_box)
        v.addWidget(self.cards_scroll, 1)

        row = QtWidgets.QHBoxLayout()
        b_new = ui.text_button("Comment", "New comment on current frame (N)", "plus")
        b_new.clicked.connect(self.new_note)
        b_del = ui.icon_button("trash", "Delete selected comment")
        b_del.clicked.connect(self.delete_note)
        row.addWidget(b_new, 1)
        row.addWidget(b_del)
        v.addLayout(row)
        b_pdf = ui.text_button("Export PDF…", f"Save all comments to PDF ({MOD}E)", "pdf")
        b_pdf.clicked.connect(self.export_pdf)
        v.addWidget(b_pdf)
        return w

    def set_tool(self, tool):
        if tool:
            if tool not in ("image", "eraser", "sticker"):  # D возвращает к рисованию, а не к ластику
                self.last_tool = tool
            self.stop()
            self.draw_fold.set_open(True)
        self.viewer.set_tool(tool)
        for k, b in self.tool_btns.items():
            b.setChecked(k == tool)
        if tool == "image" and self.src and not self.viewer.images_here():
            QtCore.QTimer.singleShot(0, lambda: self.add_ref_image(None))  # на кадре пусто — сразу выбрать файл

    # ---- картинки-референсы в комментарии
    REF_FILTER = "Images (*.png *.jpg *.jpeg *.webp *.tif *.tiff *.bmp *.gif *.exr *.hdr);;All files (*)"

    @staticmethod
    def load_ref(path):
        img = QtGui.QImage(path)
        if img.isNull():  # EXR / HDR и прочее, что Qt не читает сам
            try:
                img = to_qimage(read_image(path), (0.0, "RGB", "sRGB", False),
                                path.lower().endswith((".exr", ".hdr"))).copy()
            except Exception:
                return None
        return img if not img.isNull() else None

    def pick_sticker(self, btn):
        """Звезда: сетка стикеров; выбранный ставится кликом по кадру, пока инструмент включён."""
        self.tool_btns["sticker"].setChecked(self.viewer.tool == "sticker")
        pop = StickerPicker(self, getattr(self, "sticker", None))
        pop.picked.connect(self.use_sticker)
        pop.adjustSize()
        g = btn.mapToGlobal(QtCore.QPoint(0, btn.height() + 6))
        scr = (btn.screen() or QtGui.QGuiApplication.primaryScreen()).availableGeometry()
        pop.move(max(scr.left() + 8, min(g.x(), scr.right() - pop.width() - 8)), g.y())
        pop.show()

    def use_sticker(self, path):
        self.sticker = path
        self.settings.setValue("sticker", path)
        self.set_tool("sticker")
        self.statusBar().showMessage(f"Sticker «{os.path.splitext(os.path.basename(path))[0].replace('_', ' ')}» — "
                                     "click the frame to put it", 3500)

    def add_ref_image(self, pos=None, img=None):
        """Добавить картинку в комментарий текущего кадра (pos — место на кадре в долях)."""
        if not self.src or self.viewer.img is None:
            return
        if img is None and self.viewer.tool == "sticker":  # стикер — меньше референса, сразу из набора
            img = sticker_image(getattr(self, "sticker", "") or "", 512) if getattr(self, "sticker", None) else None
            if img is None:
                return
            fr = self.viewer.img
            s = ann.image_stroke(img, pos or [0.5, 0.5], (fr.width(), fr.height()), height=0.2)
            s.color = ann.STICKER
            s.alpha = self.viewer.alpha
            self.on_stroke(s)
            self.viewer.sel_img = s
            self.viewer.update()
            return
        if img is None:
            path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Add Reference Image",
                                                            self.settings.value("ref_dir", ""), self.REF_FILTER)
            if not path:
                return
            self.settings.setValue("ref_dir", os.path.dirname(path))
            img = self.load_ref(path)
            if img is None:
                self.statusBar().showMessage(f"Cannot open image: {os.path.basename(path)}", 4000)
                return
        if self.viewer.tool not in ("image", "sticker"):
            self._select_image_tool()
        fr = self.viewer.img
        s = ann.image_stroke(img, pos or [0.5, 0.5], (fr.width(), fr.height()))
        s.alpha = self.viewer.alpha
        self.on_stroke(s)
        self.viewer.sel_img = s
        self.viewer.update()

    def _select_image_tool(self):
        """Включить инструмент Image без диалога (картинка уже есть в руках)."""
        self.stop()
        self.draw_fold.set_open(True)
        self.viewer.set_tool("image")
        for k, b in self.tool_btns.items():
            b.setChecked(k == "image")

    def paste_ref_image(self):
        md = QtWidgets.QApplication.clipboard().mimeData()
        img = None
        if md.hasImage():
            img = QtGui.QImage(md.imageData())
        elif md.hasUrls():
            for u in md.urls():
                if u.isLocalFile():
                    img = self.load_ref(u.toLocalFile())
                    if img is not None:
                        break
        if img is None or img.isNull():
            self.statusBar().showMessage("No image in the clipboard", 3000)
            return
        self.add_ref_image(None, img)

    def delete_ref_image(self):
        """Delete / Backspace с инструментом Image: убрать выбранную картинку. True — если убрали."""
        s = self.viewer.sel_img
        if self.viewer.tool not in ("image", "sticker") or s is None or not self.store:
            return False
        for n in self.store.notes:
            if any(x is s for x in n.strokes):
                n.strokes = [x for x in n.strokes if x is not s]
                if n.is_empty() and n is not self.active:
                    self.store.notes.remove(n)
                break
        self.viewer.sel_img = None
        self.notes_changed()
        return True

    def set_color(self, c):
        self.viewer.color = c
        self.settings.setValue("color", c)
        for b, cc in zip(self.color_btns, ann.COLORS):
            b.setChecked(cc == c)

    def set_opacity(self, v):
        q = max(25, min(100, int(round(v / 25.0)) * 25))  # набранное руками — к ближайшему шагу 25%
        if q != v:
            self.opacity.setValue(q)
            return
        self.viewer.alpha = q / 100

    def set_brush(self, v):
        self.viewer.stroke_w = v / 1080

    def update_overlay(self):
        self.viewer.overlay = self.store.near(self.frame, self.ghost.value()) if self.store else []
        self.viewer.update()

    def make_note(self):
        n = ann.Note(frame=self.frame, number=self.src.numbers[self.frame], author=self.author.text().strip())
        self.store.notes.append(n)
        return n

    def on_stroke(self, stroke):
        if not self.src:
            return
        self.stop()
        # рисуем в выбранный комментарий этого кадра (нажали «+» или кликнули по нему);
        # иначе — в эскиз кадра: комментарий-рисунок с подписью, без окна ввода текста
        # после Enter комментарий закрыт — новые штрихи начинают отдельный эскиз
        note = self.draw_note
        if note is None or note.frame != self.frame or note not in self.store.notes:
            note = self.make_note()
        note.strokes.append(stroke)
        self.active = self.draw_note = note
        self.edit_log.append(("draw",))
        self.notes_changed()

    def new_note(self):
        if not self.src:
            return
        self.stop()
        self.active = self.draw_note = self.make_note()
        self.notes_changed()
        self.write_note(self.active)

    def write_note(self, note):
        """Печатать комментарий: в карточке, если панель открыта, иначе — в пузыре на кадре.
        Панель комментариев сама не раскрывается — только руками."""
        if self.notes_fold.open:
            return self.edit_note(note)
        num = next((k for k, n in self.store.numbered() if n is note), "") if self.store else ""
        QtCore.QTimer.singleShot(0, lambda: (self.viewer.bubble.open(note, num), self.viewer.update()))

    def erase_stroke(self, note, s):
        """Ластик задел штрих: убрать. Рисунок без текста, у которого не осталось штрихов, уходит целиком."""
        if not self.store or s not in note.strokes:
            return
        i = note.strokes.index(s)
        note.strokes.pop(i)
        gone = False
        if note.is_empty() and note in self.store.notes:
            self.store.notes.remove(note)
            gone = True
            if self.active is note:
                self.active = None
            if self.draw_note is note:
                self.draw_note = None
            if self.viewer.sel_img is s:
                self.viewer.sel_img = None
        self._erase_batch.append((note, i, s, gone))
        self.notes_changed()

    def erase_finished(self):
        if self._erase_batch:
            self.edit_log.append(("erase", self._erase_batch))
            self._erase_batch = []

    def undo_stroke(self):
        if self.edit_log and self.edit_log[-1][0] == "erase":  # вернуть стёртое последним движением ластика
            for note, i, s, gone in reversed(self.edit_log.pop()[1]):
                if gone and note not in self.store.notes:
                    self.store.notes.append(note)
                note.strokes.insert(min(i, len(note.strokes)), s)
            self.notes_changed()
            return
        if self.edit_log:
            self.edit_log.pop()
        n = self.active
        if n is None or n.frame != self.frame:
            cands = [x for x in self.store.notes if x.frame == self.frame and x.strokes] if self.store else []
            n = max(cands, key=lambda x: x.created) if cands else None
        if n and n.strokes:
            n.strokes.pop()
            if n.is_empty():
                self.store.notes.remove(n)
                self.active = None
            self.notes_changed()

    def delete_note(self):
        if self.active and self.store:
            self.store.notes.remove(self.active)
            self.active = None
            self.notes_changed()

    def badge_clicked(self, note):
        """Клик по номеру на кадре: писать/править комментарий прямо на кадре."""
        self.stop()
        self.note_clicked(note)
        card = self.cards.get(note.id)
        if card and self.notes_fold.open:
            QtCore.QTimer.singleShot(0, lambda: self.cards_scroll.ensureWidgetVisible(card))
        num = next((k for k, n in self.store.numbered() if n is note), "") if self.store else ""
        self.viewer.bubble.open(note, num)
        self.viewer.update()

    def bubble_changed(self, note):
        card = self.cards.get(note.id)
        if card:
            card._update_body()
        self.note_edited(note)

    def note_clicked(self, note):
        self.active = note
        for c in self.cards.values():
            c.set_selected(c.note is note)
        if note.frame != self.frame:
            self.goto(note.frame)

    def edit_note(self, note):
        card = self.cards.get(note.id)
        if card:
            card.set_selected(True)
            QtCore.QTimer.singleShot(0, lambda: (card.start_edit(), self.cards_scroll.ensureWidgetVisible(card)))

    def note_committed(self, note):
        """Enter в комментарии: он готов, следующий рисунок на кадре — уже новый комментарий."""
        if self.draw_note is note:
            self.draw_note = None

    def note_edited(self, note):
        self.timeline.update()
        self.save_timer.start()

    def note_header(self, n):
        who = "  ·  ".join(x for x in (n.author, ann.created_str(n)) if x)
        a = f"<br><span style='color:#888'>{who}</span>"
        return f"frame {n.number}  ·  {self.timecode(n.frame)}{a}"

    def notes_changed(self):
        self.refresh_notes()
        self.update_overlay()
        self.save_timer.start()

    def refresh_notes(self):
        for c in self.cards.values():
            c.edit.removeEventFilter(c)
            c.deleteLater()
        self.cards = {}
        if self.store:
            for num, n in self.store.numbered():
                c = NoteCard(n, num, self.note_header(n))
                c.set_selected(n is self.active)
                c.clicked.connect(self.note_clicked)
                c.edited.connect(self.note_edited)
                c.entered.connect(self.note_committed)
                self.cards_lay.insertWidget(self.cards_lay.count() - 1, c)
                self.cards[n.id] = c
            marks = {}
            for n in self.store.notes:
                if not n.is_empty() or n is self.active:
                    marks[n.frame] = marks.get(n.frame, 0) + 1
            self.timeline.marks = marks
            cnt = len([n for n in self.store.notes if not n.is_empty()])
            self.notes_fold.count = cnt
            self.notes_fold.update()
        else:
            self.timeline.marks = {}
        self.timeline.update()

    def save_notes(self):
        if not self.store:
            return
        self.store.notes = [n for n in self.store.notes if not n.is_empty() or n is self.active]
        try:
            self.store.save()
        except OSError as e:
            self.statusBar().showMessage(f"Could not save comments: {e}", 5000)
        self._sync_playlist()

    def full_image(self, i):
        exp, _, view, _, aov = self.current_params()
        a = self.src.read(i, aov) if aov else self.src.read(i)
        return to_qimage(a, (exp, "RGB", view, False), self.src.linear)

    def show_release_notes(self):
        base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
        try:
            with open(os.path.join(base, "RELEASE_NOTES.md"), encoding="utf-8") as f:
                text = f.read()
        except OSError:
            text = f"# MOOOV_player {APP_VERSION}\n\n{CREDITS}"
        d = QtWidgets.QDialog(self)
        d.setWindowTitle(f"What's New — MOOOV_player {APP_VERSION}")
        d.resize(640, 720)
        v = QtWidgets.QVBoxLayout(d)
        v.setContentsMargins(0, 0, 0, 16)
        tb = QtWidgets.QTextBrowser()
        tb.setOpenExternalLinks(True)
        tb.setFrameShape(QtWidgets.QFrame.NoFrame)
        tb.document().setDocumentMargin(28)
        tb.setStyleSheet(f"QTextBrowser {{ background: {ui.BG}; color: {ui.TEXT}; font-size: 13px; }}")
        tb.setHtml(notes_html(text))
        ok = QtWidgets.QPushButton("OK")
        ok.clicked.connect(d.accept)
        v.addWidget(tb, 1)
        v.addWidget(ok, 0, QtCore.Qt.AlignHCenter)
        d.exec()

    def show_about(self):
        d = QtWidgets.QDialog(self)
        d.setWindowTitle("About MOOOV_player")
        v = QtWidgets.QVBoxLayout(d)
        v.setContentsMargins(36, 28, 36, 24)
        v.setSpacing(6)
        logo = QtWidgets.QLabel()
        logo.setPixmap(ui.app_icon().pixmap(72, 72))
        logo.setAlignment(QtCore.Qt.AlignHCenter)
        name = QtWidgets.QLabel("MOOOV_player")
        name.setStyleSheet("font-size: 18px; font-weight: 600;")
        ver = QtWidgets.QLabel(f"Version {APP_VERSION}")
        ver.setStyleSheet(f"color: {ui.DIM};")
        cred = QtWidgets.QLabel(f"Developed by <b>{AUTHOR}</b><br>for "
                                f"<a href='{SITE_URL}' style='color:{ui.ACCENT}; text-decoration:none'>{SITE}</a>")
        cred.setOpenExternalLinks(True)
        cred.setTextFormat(QtCore.Qt.RichText)
        for w in (name, ver, cred):
            w.setAlignment(QtCore.Qt.AlignHCenter)
        ok = QtWidgets.QPushButton("OK")
        ok.setProperty("primary", False)
        ok.clicked.connect(d.accept)
        v.addWidget(logo)
        v.addSpacing(6)
        v.addWidget(name)
        v.addWidget(ver)
        v.addSpacing(10)
        v.addWidget(cred)
        v.addSpacing(14)
        v.addWidget(ok, 0, QtCore.Qt.AlignHCenter)
        d.exec()

    def export_pdf(self):
        if not self.store:
            return
        self.save_notes()
        items = [(k, n) for k, n in self.store.numbered() if not n.is_empty()]
        if not items:
            QtWidgets.QMessageBox.information(self, "MOOOV_player", "No comments to export")
            return
        self.stop()
        if isinstance(self.src, CompositeSource):
            default = os.path.join(os.path.dirname(self.src.clips[0].sidecar), "edit.comments.pdf")
        else:
            default = os.path.splitext(self.src.sidecar)[0].replace(".mooov", "") + ".comments.pdf"
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Export PDF", default, "PDF (*.pdf)")
        if not path:
            return
        prog = QtWidgets.QProgressDialog("Exporting PDF…", "Cancel", 0, len(items), self)
        prog.setWindowModality(QtCore.Qt.WindowModal)
        prog.setMinimumDuration(300)

        def progress(k):
            prog.setValue(k)
            QtWidgets.QApplication.processEvents()
            return not prog.wasCanceled()

        try:
            ann.export_pdf(path, items, self.full_image, self.src.file_label, self.timecode, progress,
                           matte=self.viewer.matte, matte_alpha=self.viewer.matte_alpha,
                           file_for=self.src.frame_file, app_version=APP_VERSION)
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "MOOOV_player", f"Export failed: {e}")
            return
        finally:
            prog.close()
        ui.open_url(QtCore.QUrl.fromLocalFile(path))

    # ---- keys
    def keyPressEvent(self, e):
        if e.key() == QtCore.Qt.Key_Escape and QtWidgets.QApplication.focusWidget():
            clear_focus()
            return
        if e.key() == QtCore.Qt.Key_I and e.modifiers() & QtCore.Qt.ShiftModifier:
            self.btn_info.toggle()
            return
        if e.key() == QtCore.Qt.Key_X and e.modifiers() & QtCore.Qt.AltModifier:
            self.set_io(None, None)
            return
        if e.modifiers() & QtCore.Qt.ShiftModifier and e.key() in (QtCore.Qt.Key_Left, QtCore.Qt.Key_Right):
            self.step_speed(1 if e.key() == QtCore.Qt.Key_Right else -1)
            return
        fn = self.shortcuts.get(e.key())
        if fn and not (e.modifiers() & (QtCore.Qt.ControlModifier | QtCore.Qt.MetaModifier)):
            fn()
        else:
            super().keyPressEvent(e)

    def closeEvent(self, e):
        self.save_notes()
        if self.loader:
            self.loader.close()
        self.clear_layers()
        super().closeEvent(e)


class App(QtWidgets.QApplication):
    """Приложение: на Mac файлы из Finder («Открыть с помощью», двойной клик, перетаскивание
    на иконку в Dock) приходят событием FileOpen, а не в аргументах запуска."""

    def __init__(self, argv):
        super().__init__(argv)
        self.window = None
        self.pending = None

    def event(self, e):
        if e.type() == QtCore.QEvent.FileOpen:
            path = e.file() or e.url().toLocalFile()
            if path:
                if self.window is not None:
                    self.window.open(path)
                    self.window.raise_()
                    self.window.activateWindow()
                else:
                    self.pending = path
            return True
        return super().event(e)


def main():
    ap = argparse.ArgumentParser(description="MOOOV_player — video and image sequence player")
    ap.add_argument("path", nargs="?")
    ap.add_argument("--cache-gb", type=float, default=6.0, help="RAM frame cache limit in GB (default 6)")
    args, _ = ap.parse_known_args()  # macOS может добавить свои служебные аргументы
    if args.path:
        args.path = os.path.abspath(args.path)
    try:  # внешние программы (Acrobat, Проводник), открытые из плеера, наследуют рабочую папку —
        os.chdir(os.path.expanduser("~"))  # не держать им папку плеера (обновление, переустановка)
    except OSError:
        pass

    fmt = QtGui.QSurfaceFormat()
    fmt.setSwapInterval(0)
    fmt.setSamples(4)  # сглаживание в окне кадра: ручки, ластик, линии рисунка без «лесенки»
    QtGui.QSurfaceFormat.setDefaultFormat(fmt)
    app = App(sys.argv)
    app.setApplicationName("MOOOV_player")
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName("MOOOV")
    app.setWindowIcon(ui.app_icon())
    w = Player(args.cache_gb)
    app.window = w
    w.show()
    QtCore.QTimer.singleShot(300, lambda: ui.warm_popups(w))  # списки открываются без задержки
    path = args.path or app.pending
    if path and os.path.exists(path):
        w.open(path)
    snap = os.environ.get("MOOOV_SNAPSHOT")  # для проверки сборки: снимок кадра и выход
    if snap:
        def _snap():
            w.viewer.grabFramebuffer().save(snap)
            print("snapshot", snap, "frames", w.src.n if w.src else 0, flush=True)
            app.quit()
        QtCore.QTimer.singleShot(4000, _snap)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
