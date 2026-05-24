# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_submodules
from pathlib import Path

ROOT = Path(SPECPATH).parent.resolve()

datas = [
    (str(ROOT / "src" / "templates"), "src/templates"),
    (str(ROOT / "src" / "static"), "src/static"),
    (str(ROOT / ".env.example"), "."),
    (str(ROOT / "dialer_config.example.json"), "."),
    (str(ROOT / "README.md"), "."),
]

hiddenimports = []


def runtime_module(name):
    parts = name.split(".")
    if "tests" in parts or "testing" in parts:
        return False
    if any(part.endswith("_tests") or part == "__pyinstaller" for part in parts):
        return False
    return True


for package in (
    "fastapi",
    "uvicorn",
    "jinja2",
    "pydantic",
    "groq",
    "selenium",
    "webdriver_manager",
    "pandas",
    "openpyxl",
    "sounddevice",
    "soundfile",
    "soundcard",
    "edge_tts",
    "pyttsx3",
    "keyboard",
):
    hiddenimports += collect_submodules(package, filter=runtime_module)


a = Analysis(
    [str(ROOT / "src" / "desktop_app.py")],
    pathex=[str(ROOT)],
    binaries=[],
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
    a.binaries,
    a.datas,
    [],
    name="IndusDispatchConsole",
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
)
