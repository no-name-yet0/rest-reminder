# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：单文件绿色版 exe。

用法（在 rest-reminder 目录下）：
    python -m PyInstaller --clean --noconfirm rest_reminder.spec
"""

import os

ROOT = os.path.abspath(SPECPATH)

EXCLUDES = [
    # PySide6 里用不到的重模块，剔掉能省下大量体积
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebEngineQuick",
    "PySide6.QtWebChannel",
    "PySide6.QtWebSockets",
    "PySide6.Qt3DCore",
    "PySide6.Qt3DRender",
    "PySide6.Qt3DInput",
    "PySide6.Qt3DLogic",
    "PySide6.Qt3DAnimation",
    "PySide6.Qt3DExtras",
    "PySide6.Qt3DQuick",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtGraphs",
    "PySide6.QtQuick",
    "PySide6.QtQuick3D",
    "PySide6.QtQuickWidgets",
    "PySide6.QtQuickControls2",
    "PySide6.QtQml",
    "PySide6.QtQmlModels",
    "PySide6.QtPdf",
    "PySide6.QtPdfWidgets",
    "PySide6.QtSql",
    "PySide6.QtTest",
    "PySide6.QtDesigner",
    "PySide6.QtHelp",
    "PySide6.QtUiTools",
    "PySide6.QtBluetooth",
    "PySide6.QtNfc",
    "PySide6.QtSerialPort",
    "PySide6.QtSensors",
    "PySide6.QtPositioning",
    "PySide6.QtLocation",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.QtSpatialAudio",
    "PySide6.QtNetworkAuth",
    "PySide6.QtRemoteObjects",
    "PySide6.QtScxml",
    "PySide6.QtStateMachine",
    "PySide6.QtTextToSpeech",
    "PySide6.QtOpenGL",
    "PySide6.QtOpenGLWidgets",
    # 标准库里用不到的
    "tkinter",
    "unittest",
    "doctest",
    "pydoc",
    "lib2to3",
    # 只在打包阶段用到的
    "PIL",
    "PyInstaller",
    "setuptools",
    "pip",
]

a = Analysis(
    [os.path.join(ROOT, "src", "main.py")],
    pathex=[os.path.join(ROOT, "src")],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="RestReminder",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(ROOT, "assets", "icon.ico"),
)
