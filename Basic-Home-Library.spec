# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build recipe for Basic-Home-Library.

Conda environments lay Tcl/Tk out differently from a python.org install: the
runtime DLLs live in <env>\\Library\\bin and the script libraries in
<env>\\Library\\lib, neither of which PyInstaller scans by default.  Without the
two DLLs below the frozen app dies at start-up with
"ImportError: DLL load failed while importing _tkinter".

Everything here is conditional, so the same spec also builds correctly from a
plain python.org install or a venv, where PyInstaller's own tkinter hook
already does the right thing.
"""

import os
import sys

env_root = os.path.dirname(sys.executable)
conda_bin = os.path.join(env_root, "Library", "bin")
conda_lib = os.path.join(env_root, "Library", "lib")

# PyInstaller resolves the DLLs that .pyd extensions link against by searching
# PATH.  Putting Library\bin on it lets the dependency scan find them all --
# libexpat for pyexpat, liblzma for lzma, and so on -- instead of the build
# silently succeeding and the frozen app dying on the first import.
if os.path.isdir(conda_bin):
    os.environ["PATH"] = conda_bin + os.pathsep + os.environ.get("PATH", "")

# Tcl and Tk are loaded by name at runtime rather than linked, so the scan above
# cannot see them; they still have to be named explicitly.
binaries = []
for name in ("tcl86t.dll", "tk86t.dll", "zlib1.dll"):
    candidate = os.path.join(conda_bin, name)
    if os.path.isfile(candidate):
        binaries.append((candidate, "."))

# PyInstaller's tkinter runtime hook points TCL_LIBRARY/TK_LIBRARY at these two
# folder names inside the unpacked bundle.
datas = []
for source, destination in (("tcl8.6", "_tcl_data"), ("tk8.6", "_tk_data"), ("tcl8", "tcl8")):
    candidate = os.path.join(conda_lib, source)
    if os.path.isdir(candidate):
        datas.append((candidate, destination))


a = Analysis(
    ["Basic-Home-Library.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=["openpyxl", "PIL._tkinter_finder"],
    hookspath=[],
    runtime_hooks=[],
    # Trimming these keeps the single file small; none are used by the app.
    excludes=["numpy", "pandas", "matplotlib", "scipy", "pytest", "setuptools", "pip"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="Basic-Home-Library",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
