# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for NORP Agent (Linux)
Copyright (c) 2026 xingluosama
"""

import os
import sys
from pathlib import Path

# ── 项目根目录 ──
ROOT = Path(os.path.dirname(os.path.abspath(__file__))).parent

# ── hidden imports: pywebview on Linux requires GTK/WebKit2GTK ──
hiddenimports = [
    # pywebview + GTK backend
    'gi',
    'gi.repository.Gtk',
    'gi.repository.Gdk',
    'gi.repository.GLib',
    'gi.repository.GObject',
    'gi.repository.WebKit2',
    'gi.repository.Soup',
    'webview',
    'webview.platforms.gtk',
    'webview.platforms',
    'webview.guilib',
    # cryptography (Fernet for Linux)
    'cryptography',
    'cryptography.fernet',
    'cryptography.hazmat.backends',
    'cryptography.hazmat.backends.openssl',
    'cryptography.hazmat.primitives',
    # keyring
    'keyring',
    'keyring.backends',
    'keyring.backends.SecretService',
    'keyring.backends.fail',
    # asyncio + threading
    'asyncio',
    'concurrent.futures',
    # others
    'openai',
    'anthropic',
    'requests',
    'urllib3',
    'charset_normalizer',
    'idna',
    'certifi',
    'json',
    'hashlib',
    'base64',
    'uuid',
    'tempfile',
    'shutil',
    'subprocess',
    'threading',
    'queue',
    'pathlib',
    'html',
]

# ── Data files ──
datas = [
    # Frontend
    (str(ROOT / 'front.html'), '.'),
    # Plugin system
    (str(ROOT / 'plugin_system'), 'plugin_system'),
    # Official plugins
    (str(ROOT / 'official_plugins'), 'official_plugins'),
]

# ── Exclude unnecessary modules to reduce size ──
excluded_modules = [
    'tkinter',
    'PyQt5',
    'PyQt6',
    'PySide2',
    'PySide6',
    'wx',
    'matplotlib',
    'numpy',
    'pandas',
    'scipy',
    'PIL',
    'cv2',
    'tensorflow',
    'torch',
    'jedi',
    'IPython',
    'jupyter',
    'notebook',
    'pytest',
    'setuptools',
    'pip',
    'wheel',
    'distutils',
]

a = Analysis(
    [str(ROOT / 'main.py')],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[str(ROOT / 'linux_build')],
    hooksconfig={},
    runtime_hooks=[str(ROOT / 'linux_build' / 'runtime_hook.py')],
    excludes=excluded_modules,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=None,
    noarchive=False,
)

pyz = PYZ(
    a.pure,
    a.zipped_data,
    cipher=None,
)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='norp-agent',
    debug=False,
    bootloader_ignore_signals=False,
    strip=True,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
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
    a.zipfiles,
    a.datas,
    strip=True,
    upx=True,
    upx_exclude=[],
    name='norp-agent',
)
