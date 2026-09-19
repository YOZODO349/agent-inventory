# -*- coding: utf-8 -*-
"""PyInstaller 规格：单文件 exe（仓库版，供 CI 用）。

要点：
  · 源码在 ../src；`datas` 逐件判断存在才带上 —— 采集器与 MCP/接入模块是
    运行期用 importlib 动态载入的，必须随包带走。
  · **控制台模式**（console=True）：同一枚 exe 兼两个身份 ——
      不带参数双击 → 图形界面（启动时自行隐藏控制台窗口，见 _hide_console()）
      带 --mcp      → MCP 服务端（stdio 通道在窗口模式下不可靠，故此处必须 console）
  · exe 名用 ASCII（AgentAssetOverview），免得 CI 与各平台对中文名处理不一致；
    本机自用那份另有一枚中文名规格。
"""
import os

HERE = os.path.dirname(os.path.abspath(SPEC))     # scripts/
ROOT = os.path.dirname(HERE)                      # 仓库根
SRC = os.path.join(ROOT, "src")

datas = []
for _f in ["agent_inventory.json", "agents.json", "scan_agents.py",
           "scan_agents_apps.py", "glass_widget.py",
           "agent_mcp.py", "agent_onboard.py"]:
    _p = os.path.join(SRC, _f)
    if os.path.isfile(_p):
        datas.append((_p, "."))
for _d in ["agent_icons"]:
    _p = os.path.join(SRC, _d)
    if os.path.isdir(_p):
        datas.append((_p, _d))

a = Analysis(
    [os.path.join(SRC, "agent_inventory_app.py")],
    pathex=[SRC],
    binaries=[],
    datas=datas,
    hiddenimports=[
        "tkinter", "tkinter.ttk", "tkinter.font",
        "glass_widget", "agent_mcp", "agent_onboard",
        "glob", "time", "hashlib", "datetime",
        "importlib", "importlib.util", "json", "re", "shutil", "subprocess",
        "os", "sys", "math", "ctypes", "ctypes.wintypes", "sqlite3",
        "tkinter.filedialog", "os.path", "io",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=["numpy", "scipy", "pandas", "matplotlib", "PyQt5", "PySide2"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name="AgentAssetOverview",
    debug=False,
    strip=False,
    upx=False,
    console=True,          # MCP 服务端需要真正的 stdio
    icon=os.path.join(SRC, "app_icon.ico"),
)
