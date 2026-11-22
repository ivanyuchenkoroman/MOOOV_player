# PyInstaller spec — общий для Mac и Windows.
#   Mac:     uv run pyinstaller packaging/MOOOV_player.spec   → dist/MOOOV_player.app
#   Windows: uv run pyinstaller packaging\MOOOV_player.spec   → dist\MOOOV_player\MOOOV_player.exe
import os
import sys

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
NAME = "MOOOV_player"
VERSION = "1.1.0"
IS_MAC = sys.platform == "darwin"

VIDEO = ["mov", "mp4", "m4v", "avi", "mkv", "webm", "mxf"]
IMAGES = ["exr", "png", "jpg", "jpeg", "tif", "tiff", "hdr", "bmp", "webp"]

a = Analysis(
    [os.path.join(ROOT, "MOOOV_player.py")],
    pathex=[ROOT],
    datas=[(os.path.join(ROOT, "RELEASE_NOTES.md"), ".")],  # окно Help → What's New
    excludes=[  # не нужны плееру — меньше размер
        "tkinter", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtQuick",
        "PySide6.QtQml", "PySide6.Qt3DCore", "PySide6.QtMultimedia", "PySide6.QtCharts",
        "PySide6.QtDataVisualization", "PySide6.QtBluetooth", "PySide6.QtSql", "PySide6.QtTest",
        "matplotlib", "scipy", "pandas", "IPython",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name=NAME,
    console=False,  # без окна терминала
    icon=os.path.join(SPECPATH, NAME + (".icns" if IS_MAC else ".ico")),
    argv_emulation=False,  # файлы из Finder ловим сами (QFileOpenEvent)
)
coll = COLLECT(exe, a.binaries, a.datas, name=NAME)

if IS_MAC:
    doc_types = [
        {"CFBundleTypeName": "Video", "CFBundleTypeRole": "Viewer", "LSHandlerRank": "Alternate",
         "CFBundleTypeExtensions": VIDEO, "LSItemContentTypes": ["public.movie"]},
        {"CFBundleTypeName": "Image / frame of a sequence", "CFBundleTypeRole": "Viewer",
         "LSHandlerRank": "Alternate", "CFBundleTypeExtensions": IMAGES,
         "LSItemContentTypes": ["public.image", "com.ilm.openexr-image"]},
        {"CFBundleTypeName": "Folder with a sequence", "CFBundleTypeRole": "Viewer",
         "LSHandlerRank": "Alternate", "LSItemContentTypes": ["public.folder"]},
    ]
    app = BUNDLE(
        coll,
        name=NAME + ".app",
        icon=os.path.join(SPECPATH, NAME + ".icns"),
        bundle_identifier="team.mooov.player",
        version=VERSION,
        info_plist={
            "CFBundleDisplayName": NAME,
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "NSHumanReadableCopyright": "Developed by Ivaniuchenko Roman for mooov.team",
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "11.0",
            "CFBundleDocumentTypes": doc_types,
        },
    )
