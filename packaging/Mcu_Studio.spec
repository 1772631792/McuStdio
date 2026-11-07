# -*- mode: python ; coding: utf-8 -*-

import os

project_root = os.path.abspath(os.path.join(SPECPATH, '..'))
entry_script = os.environ.get(
    'MCU_STUDIO_ENTRY_SCRIPT',
    os.path.join(project_root, 'main.py'),
)
icon_file = os.environ.get(
    'MCU_STUDIO_ICON_FILE',
    os.path.join(project_root, 'assets', 'mcu_studio_anime.ico'),
)
icon_png = os.environ.get(
    'MCU_STUDIO_ICON_PNG',
    os.path.join(project_root, 'assets', 'mcu_studio_anime.png'),
)
app_name = os.environ.get('MCU_STUDIO_APP_NAME', 'Mcu_Studio')

# pyqtgraph can discover several installed Qt bindings. Pin both common
# selectors before Analysis so PyInstaller's hooks consistently choose PyQt6.
os.environ['PYQTGRAPH_QT_LIB'] = 'PyQt6'
os.environ['QT_API'] = 'pyqt6'

a = Analysis(
    [entry_script],
    pathex=[project_root],
    binaries=[],
    datas=[(icon_png, 'assets')],
    hiddenimports=['pylink', 'elftools'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # pyqtgraph can optionally import every installed Qt/scientific binding.
    # This application uses PyQt6 + numpy only; collecting the alternatives
    # adds conflicting runtimes and makes the one-file bundle fragile.
    excludes=[
        'PySide6', 'shiboken6',
        'PyQt5', 'PySide2', 'shiboken2',
        'matplotlib', 'scipy',
        # Plotting is explicitly raster/CPU based. pyqtgraph probes these
        # optional modules but falls back cleanly when they are absent.
        'PyQt6.QtOpenGL', 'PyQt6.QtOpenGLWidgets',
        'PyQt6.QtPdf', 'PyQt6.QtTest',
        'pyqtgraph.examples', 'pyqtgraph.console', 'pyqtgraph.jupyter',
        'pyqtgraph.opengl',
    ],
    noarchive=False,
    optimize=1,
)

# Qt 6 on Windows links against the OS-provided ICU ABI.  The build host also
# has Poppler's unrelated ICU 78 on PATH; PyInstaller otherwise collects that
# as generic `icuuc.dll`, and Qt6Core fails with WinError 127 at startup.
a.binaries = [
    entry for entry in a.binaries
    if os.path.basename(entry[0]).lower() != 'icuuc.dll'
    and not os.path.basename(entry[0]).lower().startswith('icudt')
    and os.path.basename(entry[0]).lower() not in {
        'qt6pdf.dll', 'qt6opengl.dll', 'qt6openglwidgets.dll',
        'qpdf.dll', 'qgif.dll', 'qicns.dll', 'qtga.dll',
        'qtiff.dll', 'qwbmp.dll', 'qwebp.dll',
    }
]
# Application strings are Chinese and no QTranslator is installed. Native
# Windows dialogs use the OS language, so bundling every Qt locale is unused.
a.datas = [
    entry for entry in a.datas
    if '/translations/' not in entry[0].replace('\\', '/').lower()
]
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name=app_name,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=[icon_file],
)
