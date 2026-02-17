# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec file for UAV-AI Desktop Application.
Build with:  pyinstaller uav-ai.spec
"""

import os

block_cipher = None
ROOT = os.path.abspath(os.path.dirname(SPEC))

a = Analysis(
    [os.path.join(ROOT, 'launcher.py')],
    pathex=[ROOT],
    binaries=[],
    datas=[
        (os.path.join(ROOT, 'static'), 'static'),
        (os.path.join(ROOT, 'configs'), 'configs'),
    ],
    hiddenimports=[
        'web_server',
        'drone_validator',
        'JARVIS',
        'copilot',
        'log_parser',
        'logging_config',
        'stt_module',
        'Mavlink_rx_handler',
        'flask',
        'flask_socketio',
        'engineio',
        'engineio.async_drivers.threading',
        'pymavlink',
        'pymavlink.dialects.v20.all',
        'pymavlink.dialects.v20.ardupilotmega',
        'serial',
        'dotenv',
        'google.generativeai',
        'openai',
        'anthropic',
        'numpy',
        'eventlet',
        'platformdirs',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter',
        'matplotlib',
        'scipy',
        'PIL',
        'IPython',
        'jupyter',
        'notebook',
        # pymavlink pulls these in but UAV-AI doesn't use them
        'wx',
        'wxPython',
        'cv2',
        'opencv-python',
        'lxml',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='UAV-AI',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,  # No terminal window on Windows
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='UAV-AI',
)
