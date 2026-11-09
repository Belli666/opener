# -*- mode: python ; coding: utf-8 -*-
import sys
from PyInstaller.utils.hooks import collect_all

datas = []
binaries = []
hiddenimports = [
    'selenium',
    'selenium.webdriver',
    'selenium.webdriver.chrome.webdriver',
    'selenium.webdriver.chrome.service',
    'selenium.webdriver.chrome.options',
    'selenium.webdriver.common.service',
    'selenium.webdriver.common.selenium_manager',
    'selenium.webdriver.common.by',
    'selenium.webdriver.common.action_chains',
    'selenium.webdriver.common.actions.wheel_input',
    'selenium.webdriver.support.ui',
    'selenium.webdriver.support.expected_conditions',
    'urllib.request',
    'urllib.error',
    'urllib.parse',
    'json',
    'zipfile',
    'shutil',
    'subprocess',
    'ctypes',
    'hashlib',
    'tempfile',
    'platform',
    'atexit',
    'logging',
    'time',
    'datetime',
    'traceback',
    're',
]

if sys.platform == "win32":
    hiddenimports.extend(['winreg'])

tmp_ret = collect_all('selenium')
datas += tmp_ret[0]
binaries += tmp_ret[1]
hiddenimports += tmp_ret[2]

try:
    wm_ret = collect_all('webdriver_manager')
    datas += wm_ret[0]
    binaries += wm_ret[1]
    hiddenimports += wm_ret[2]
except Exception:
    pass

a = Analysis(
    ['open.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'numpy', 'scipy', 'pandas'],
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
    name='opener',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
