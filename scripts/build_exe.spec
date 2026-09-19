# -*- coding: utf-8 -*-
"""PyInstaller 规格文件：单文件、无控制台、图标可选。

用法（在仓库根目录执行）：
    uv run pyinstaller --noconfirm scripts/build_exe.spec

要点：
  · 源码在 ../src；`datas` 逐件判断存在才带上 —— 采集器（scan_agents*.py）是
    运行期用 importlib 动态载入的，必须随包带走。
  · 路径不写死：由 spec 自身位置推导，换台电脑重打包也不必改。
  · `hiddenimports` 里那几件标准库是给动态载入的采集器用的 —— PyInstaller
    静态分析扫不到，不带上的话打包后一跑就 ModuleNotFoundError: No module
    named 'glob'。
"""
import os

HERE = os.path.dirname(os.path.abspath(SPEC))     # scripts/
ROOT = os.path.dirname(HERE)                      # 仓库根
SRC = os.path.join(ROOT, "src")

datas = []
for _f in ["agent_inventory.json", "agents.json", "scan_agents.py",
           "scan_agents_apps.py", "glass_widget.py"]:
    _p = os.path.join(SRC, _f)
    if os.path.isfile(_p):
        datas.append((_p, "."))
for _d in ["agent_icons"]:
    _p = os.path.join(SRC, _d)
    if os.path.isdir(_p):
        datas.append((_p, _d))

_icon = os.path.join(SRC, "app_icon.ico")

a = Analysis(
    [os.path.join(SRC, "agent_inventory_app.py")],
    pathex=[SRC],
    binaries=[],
    datas=datas,
    hiddenimports=[
        "tkinter", "tkinter.ttk", "tkinter.font",
        "glass_widget",
        # 动态载入的采集器用到的标准库，静态分析扫不到，须显式列出
        "glob", "time", "hashlib", "datetime",
        "importlib", "importlib.util", "json", "re", "shutil", "subprocess",
        "os", "sys", "math", "ctypes", "ctypes.wintypes",
        "tkinter.filedialog", "os.path",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=["numpy", "scipy", "pandas", "matplotlib", "PyQt5", "PySide2"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="AgentAssetOverview",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    icon=_icon if os.path.isfile(_icon) else None,
)
