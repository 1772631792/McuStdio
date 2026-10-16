# -*- mode: python ; coding: utf-8 -*-

import os

project_root = os.path.abspath(os.path.join(SPECPATH, '..'))
entry_script = os.path.join(project_root, 'live_set.py')
icon_file = os.path.join(project_root, 'assets', 'mcu_studio.ico')

a = Analysis(
    [entry_script],
    pathex=[project_root],
    binaries=[],
    datas=[],
    hiddenimports=['pylink', 'elftools'],
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
    name='MCU_Live_Watch',
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
    icon=[icon_file],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='MCU_Live_Watch',
)
