# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the WuwaTerm desktop client.

One-folder build: client/build.ps1 drives
``pyinstaller client/WuwaTerm.spec`` and produces
``client/dist/WuwaTerm/WuwaTerm.exe``. No code signing.

The names below (Analysis, PYZ, EXE, COLLECT, SPECPATH) are injected into
this file's execution namespace by PyInstaller itself when it runs the spec;
they are not imports.
"""

from __future__ import annotations

import sys
from pathlib import Path

block_cipher = None

# PyInstaller resolves an extension module's dependent DLLs through the build
# machine's PATH. `_ssl.pyd` is taken from this interpreter, but its
# `libssl-3-x64.dll` / `libcrypto-3-x64.dll` are whatever PATH found first, so
# a machine with any other OpenSSL installed silently ships a mismatched pair
# and the built program dies at start-up with
# "ImportError: DLL load failed while importing _ssl" the first time anything
# constructs an HTTP client. Bind this interpreter's own copies explicitly.
INTERPRETER_DLL_DIR = Path(sys.base_prefix) / "DLLs"
OPENSSL_BINARIES = [
    (str(path), ".") for path in sorted(INTERPRETER_DLL_DIR.glob("lib*-3-x64.dll"))
]
if not OPENSSL_BINARIES:
    raise SystemExit(
        f"no OpenSSL runtime found next to the interpreter in {INTERPRETER_DLL_DIR};"
        " the build would produce a program that cannot open a connection"
    )

CLIENT_ROOT = Path(SPECPATH)
SRC_ROOT = CLIENT_ROOT / "src"
# client/main.py, NOT wuwaterm_client/__main__.py: PyInstaller runs the entry
# script as a top-level module with no package context, so an entry that uses
# relative imports fails at start-up with "attempted relative import with no
# known parent package". See client/main.py.
ENTRY_POINT = CLIENT_ROOT / "main.py"

a = Analysis(
    [str(ENTRY_POINT)],
    pathex=[str(SRC_ROOT)],
    binaries=OPENSSL_BINARIES,
    datas=[],
    hiddenimports=[
        "keyring.backends.Windows",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="WuwaTerm",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
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
    strip=False,
    upx=False,
    upx_exclude=[],
    name="WuwaTerm",
)
