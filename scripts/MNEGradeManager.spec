# -*- mode: python ; coding: utf-8 -*-
# PyInstaller — MNE Grade Manager (Windows / macOS)
# Lancer depuis la racine du projet :
#   pyinstaller --noconfirm scripts/MNEGradeManager.spec

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

project_root = Path(SPECPATH).resolve().parent
src_root = project_root / "src"
entry = project_root / "scripts" / "pyinstaller_entry.py"
pkg = src_root / "mne_grade_manager"

datas = [
    (str(pkg / "core" / "schema.sql"), "mne_grade_manager/core"),
    (str(pkg / "assets"), "mne_grade_manager/assets"),
]

hiddenimports = [
    "mne_grade_manager",
    "mne_grade_manager.app",
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "openpyxl",
    "openpyxl.cell",
    "openpyxl.workbook",
    "reportlab",
    "reportlab.pdfgen",
    "reportlab.pdfgen.canvas",
    "reportlab.lib",
    "reportlab.lib.pagesizes",
    "reportlab.lib.colors",
    "reportlab.platypus",
    "pypdf",
    "cv2",
    "PIL",
    "PIL.Image",
    "numpy",
]
hiddenimports += collect_submodules("mne_grade_manager")

a = Analysis(
    [str(entry)],
    pathex=[str(src_root)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="MNEGradeManager",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="MNEGradeManager",
)
