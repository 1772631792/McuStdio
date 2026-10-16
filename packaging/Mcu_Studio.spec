# -*- mode: python ; coding: utf-8 -*-

import os

project_root = os.path.abspath(os.path.join(SPECPATH, '..'))
entry_script = os.path.join(project_root, 'live_set.py')
icon_file = os.path.join(project_root, 'assets', 'mcu_studio.ico')
app_name = os.environ.get('MCU_STUDIO_APP_NAME', 'Mcu_Studio')

# pyqtgraph can discover several installed Qt bindings. Pin both common
# selectors before Analysis so PyInstaller's hooks consistently choose PyQt6.
os.environ['PYQTGRAPH_QT_LIB'] = 'PyQt6'
os.environ['QT_API'] = 'pyqt6'

a = Analysis(
    [entry_script],
    pathex=[project_root],
    binaries=[],
    datas=[],
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
    ],
    noarchive=False,
    optimize=0,
)

# Qt 6 on Windows links against the OS-provided ICU ABI.  The build host also
# has Poppler's unrelated ICU 78 on PATH; PyInstaller otherwise collects that
# as generic `icuuc.dll`, and Qt6Core fails with WinError 127 at startup.
a.binaries = [
    entry for entry in a.binaries
    if os.path.basename(entry[0]).lower() != 'icuuc.dll'
    and not os.path.basename(entry[0]).lower().startswith('icudt')
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
