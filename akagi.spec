# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules


project_root = Path(SPECPATH)

datas = []
binaries = []
hiddenimports = []

hiddenimports += collect_submodules("akagi")
hiddenimports += collect_submodules("autoplay")
hiddenimports += collect_submodules("mitm")
hiddenimports += collect_submodules("settings")
hiddenimports += collect_submodules("textual")
hiddenimports += collect_submodules("mjai")
hiddenimports += collect_submodules("mjai_bot")
hiddenimports += collect_submodules("mjai_bot.mortal")
hiddenimports += collect_submodules("mjai_bot.mortal3p")
# NumPy 2.x keeps C-extension under `numpy._core`, but some native modules
# still import the legacy path `numpy.core._multiarray_umath`.
hiddenimports += [
    "numpy.core",
    "numpy.core.multiarray",
    "numpy.core._multiarray_umath",
]

datas += collect_data_files("akagi", include_py_files=False)
datas += collect_data_files("autoplay", include_py_files=False)
datas += collect_data_files("settings", include_py_files=False)
datas += collect_data_files("mjai", include_py_files=False)
datas += collect_data_files("mjai_bot", include_py_files=False)
datas += collect_data_files("mitm", include_py_files=False)

binaries += collect_dynamic_libs("mjai")
binaries += collect_dynamic_libs("mjai_bot")
binaries += collect_dynamic_libs("mjai_bot.mortal")
binaries += collect_dynamic_libs("mjai_bot.mortal3p")

for relative in ["img", "docs"]:
    source = project_root / relative
    if source.exists():
        datas.append((str(source), relative))


a = Analysis(
    ["run_akagi.py"],
    pathex=[str(project_root)],
    binaries=binaries,
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
    name="Akagi",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
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
    upx=False,
    upx_exclude=[],
    name="Akagi",
)
