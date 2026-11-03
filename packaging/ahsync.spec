# PyInstaller recipe for AHSync, one for both OSes (SPEC §3.7.12). Run by tools/build.py from the repo root:
#   python -m PyInstaller --noconfirm packaging/ahsync.spec
# Onedir and windowed. Windows: dist/AHSync/AHSync.exe (+ _internal/), packaged by Inno Setup.
# macOS: dist/AHSync.app (LSUIElement: no Dock icon), packaged as a dmg and a zip.
# Signing (macOS): tools/build.py sets AHSYNC_CODESIGN_IDENTITY and AHSYNC_ENTITLEMENTS in mode "sign";
# unset, PyInstaller signs ad hoc. This file is outside DR16's scan (it is a recipe, not code).
import os
import sys
from pathlib import Path

ROOT = Path(SPECPATH).parent          # noqa: F821  (SPECPATH is set by PyInstaller)
MAC = sys.platform == "darwin"

version_ns = {}
exec((ROOT / "ahsync" / "version.py").read_text(encoding="utf-8"), version_ns)
VERSION = version_ns["VERSION"]

identity = os.environ.get("AHSYNC_CODESIGN_IDENTITY") or None
entitlements = os.environ.get("AHSYNC_ENTITLEMENTS") or None

# Data files the code reads next to its modules (Path(__file__)...), plus the addon the app installs (SPEC §3.7.7).
datas = [
    (str(ROOT / "addon"), "addon"),
    (str(ROOT / "ahsync" / "data"), "ahsync/data"),
    (str(ROOT / "ahsync" / "db" / "schema.sql"), "ahsync/db"),
    (str(ROOT / "ahsync" / "web" / "static"), "ahsync/web/static"),
    (str(ROOT / "ahsync_app" / "icon.png"), "ahsync_app"),
]

# The backends pystray and pywebview pick at run time; pywebview's and pythonnet's DLLs come with their hooks.
hiddenimports = (["pystray._darwin", "webview.platforms.cocoa"] if MAC else
                 ["pystray._win32", "webview.platforms.edgechromium", "webview.platforms.winforms", "clr"])

a = Analysis(                         # noqa: F821
    [str(ROOT / "ahsync_app" / "__main__.py")],
    pathex=[str(ROOT)],
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "pytest"],
)
pyz = PYZ(a.pure)                     # noqa: F821

exe = EXE(                            # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="AHSync",
    console=False,
    upx=False,
    icon=str(ROOT / "ahsync_app" / "icon.png"),
    codesign_identity=identity if MAC else None,
    entitlements_file=entitlements if MAC else None,
)
coll = COLLECT(exe, a.binaries, a.datas, name="AHSync", upx=False)   # noqa: F821

if MAC:
    app = BUNDLE(                     # noqa: F821
        coll,
        name="AHSync.app",
        icon=str(ROOT / "ahsync_app" / "icon.png"),
        bundle_identifier="com.wowauction.ahsync",
        version=VERSION,
        info_plist={"LSUIElement": True, "CFBundleShortVersionString": VERSION, "CFBundleVersion": VERSION,
                    "NSHighResolutionCapable": True},
    )
