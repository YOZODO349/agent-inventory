# -*- coding: utf-8 -*-
"""Agent 资产总览 —— 原生桌面应用（tkinter）。

单文件、无外部依赖、Python 3.10+。数据来自同目录 agent_inventory.json，
若不存在则自动实时扫描。
"""
import os
import re
import sys
import json
import shutil
import subprocess
import threading
import time
import queue

import tkinter as tk
from tkinter import ttk, messagebox


def _init_dpi():
    """声明 DPI 感知，并算出界面该放大多少。

    缩放屏上「字糊」的根因：程序没说自己是 DPI 感知的，Windows 就把整个窗口
    当成一张位图**拉伸**去凑尺寸 —— 125%/150%/200% 的屏上全是虚边。
    此处在建立任何窗口**之前**声明感知，之后字体走原生渲染，就清晰了。
    返回建议缩放比（96 DPI 即 1.0）。
    """
    scale = 1.0
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)      # 系统级 DPI 感知
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()          # 老系统退路
        try:
            dpi = ctypes.windll.user32.GetDpiForSystem()
        except Exception:
            dpi = 0
        if not dpi:
            hdc = ctypes.windll.user32.GetDC(0)
            dpi = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88)   # LOGPIXELSX
            ctypes.windll.user32.ReleaseDC(0, hdc)
        if dpi and dpi > 0:
            scale = dpi / 96.0
    except Exception:
        pass
    return max(1.0, min(scale, 3.0))          # 上限 3 倍，免得异常值把界面撑爆


UI_SCALE = _init_dpi()


def _px(v):
    """按屏幕缩放换算像素（96 DPI 时原样返回）。"""
    try:
        return int(round(float(v) * UI_SCALE))
    except Exception:
        return int(v)


def _load_glass():
    """取玻璃杯控件：先按常规 import，不行就顺着 _MEIPASS / 程序旁去找。"""
    try:
        from glass_widget import GlassCard as _GC
        return _GC
    except Exception:
        pass
    try:
        import importlib.util
        roots = []
        mp = getattr(sys, "_MEIPASS", None)
        if mp:
            roots.append(mp)
        roots.append(os.path.dirname(os.path.abspath(__file__)))
        exe = getattr(sys, "executable", None)
        if exe:
            roots.append(os.path.dirname(os.path.abspath(exe)))
        for root in roots:
            p = os.path.join(root, "glass_widget.py")
            if os.path.isfile(p):
                spec = importlib.util.spec_from_file_location("_gw", p)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                return getattr(mod, "GlassCard", None)
    except Exception:
        pass
    return None


GlassCard = _load_glass()

APP_TITLE = "Agent 资产总览"

# 本工具自己的技能库：从别处导入的 Skill 都落在这里。
# 与 scan_agents.py 里那处 `agent-skills` **须保持一致**（那支扫描器要单独跑，
# 不能回头 import 本文件）。
def _skill_lib_default():
    """应用内的技能库：`通用资源\skills`（打包后即 exe 旁那一份）。"""
    if getattr(sys, "frozen", False):
        base = os.path.dirname(os.path.abspath(sys.executable))
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, "通用资源", "skills")


# 第二十一轮（本版要求）：技能库收进应用内部；家目录那处留 Junction 兼容旧引用。
_SKILL_LIB_CANDS = (_skill_lib_default(),
                    os.path.join(os.path.expanduser("~"), "agent-skills"))
SKILL_LIB = next((c for c in _SKILL_LIB_CANDS if os.path.isdir(c)), _SKILL_LIB_CANDS[0])

# 「已为哪些 Agent 自动收过 Skill」的记录。放 %LOCALAPPDATA%，不动程序旁。
SKILL_IMPORT_LOG = os.path.join(
    os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"),
    APP_TITLE, "skill_auto_import.json")


def _base_dir():
    """程序所在目录（用于放扫描器、写数据）。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def _bundle_dir():
    """只读资源目录：打包后为 _MEIPASS，未打包时即源码目录。"""
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(sys.executable)))
    return os.path.dirname(os.path.abspath(__file__))


HERE = _base_dir()
BUNDLE = _bundle_dir()

PORTABLE = "便携数据"


def _portable_paths():
    """便携底册的候选位置：程序旁 → 内嵌资源 → %LOCALAPPDATA%\\名字\\便携数据。"""
    out = [os.path.join(HERE, PORTABLE), os.path.join(BUNDLE, PORTABLE)]
    la = os.environ.get("LOCALAPPDATA")
    if la:
        out.append(os.path.join(la, APP_TITLE, PORTABLE))
    return out


def _seed_portable():
    """首次运行：把内嵌的便携底册解到程序旁（或 %LOCALAPPDATA%）。

    单文件 exe 里的 `便携数据\\` 是**随包携带**的快照。它要到这儿才落到磁盘上，
    如此新电脑上：
      · 程序旁有了可读的底册 → 即便本机扫描器来不及，也能先摆出界面；
      · 用户也看得见这些 json，知道程序在哪儿存取数据。
    已有文件**不覆盖**（免得冲掉本机刚扫出的新数据）。
    """
    src = os.path.join(BUNDLE, PORTABLE)
    if not os.path.isdir(src):
        return
    tgt = _write_dir()
    if not tgt:
        return
    try:
        os.makedirs(tgt, exist_ok=True)
        for f in os.listdir(src):
            sp = os.path.join(src, f)
            dp = os.path.join(tgt, f)
            if os.path.isfile(sp) and not os.path.exists(dp):
                shutil.copy2(sp, dp)
    except Exception:
        pass


def _write_dir():
    """可写目录：优先程序旁，不成退 %LOCALAPPDATA%\\名字。"""
    for d in (HERE, os.path.join(os.environ.get("LOCALAPPDATA", ""), APP_TITLE)
              if os.environ.get("LOCALAPPDATA") else None):
        if not d:
            continue
        try:
            os.makedirs(d, exist_ok=True)
            probe = os.path.join(d, ".w")
            with open(probe, "w") as fh:
                fh.write("1")
            os.remove(probe)
            return d
        except Exception:
            continue
    return None


def _find_inventory():
    """优先用程序旁的 json（可被重扫刷新），其次内嵌资源，再次便携底册。"""
    cands = [os.path.join(HERE, "agent_inventory.json"),
             os.path.join(BUNDLE, "agent_inventory.json")]
    for d in _portable_paths():
        cands.append(os.path.join(d, "agent_inventory.json"))
    for p in cands:
        if os.path.isfile(p):
            return p
    return None


def _guard_inventory():
    """开窗之前先请看守出手：清单缺了便就地重产，免得技能栏整栏失声。

    第八轮之教训 —— 那份 json 曾只作内嵌静态数据，外界一份被当成垃圾清掉，
    程序照开，技能/MCP 诸栏却空空如也，且不报错，最难查。
    今由 scan_agents_apps.py 里的 ensure_inventory() 兜底。

    注意：`_import_scanner()` 定义在本文件更靠后处，此处**运行期**才调用它，
    故须把这一步挪到模块末尾（见文件底部 `INVENTORY_NOTE = _guard_inventory()`），
    不可在模块顶部直接调用，否则 `NameError: _import_scanner is not defined`。
    """
    try:
        fn = globals().get("_import_scanner")
        if fn is not None:
            mod = fn()
            if mod is not None and hasattr(mod, "ensure_inventory"):
                note, _ = mod.ensure_inventory()
                return note
    except Exception as e:
        return "看守未成：" + str(e)
    return ""


# 首次运行：把内嵌的便携底册解到磁盘（必须在算 INV / AGENTS_JSON **之前**做）
_seed_portable()


def _find_agents():
    cands = [os.path.join(HERE, "agents.json"),
             os.path.join(BUNDLE, "agents.json")]
    for d in _portable_paths():
        cands.append(os.path.join(d, "agents.json"))
    for p in cands:
        if os.path.isfile(p):
            return p
    return None


def _icon_dir():
    for p in (os.path.join(HERE, "agent_icons"),
              os.path.join(BUNDLE, "agent_icons")):
        if os.path.isdir(p):
            return p
    return None


INV = _find_inventory()
AGENTS_JSON = _find_agents()
ICON_DIR = _icon_dir()

# 各 Agent 的官方配色（备用：图标取色失败时兜底）
AGENT_COLORS = {
    "workbuddy": "#19b17a",
    "cursor": "#3a3f47",
    "codexplus": "#4a6fd8",
    "codexplus_manager": "#4a6fd8",
    "astrbot": "#1f9bd8",
    "astrbot_launcher": "#1f9bd8",
    "grokbot": "#5a6069",
    "comfyui": "#18a866",
    "chunxiao": "#1f9bd8",
}

# ---------- 配色（明亮白底 · 冰柜） ----------
# 配色：照「Premium Utilitarian Minimalism」那套来 ——
# 暖白底、近黑字、超淡边；**颜色是稀缺资源**，只用于语义与细微点缀。
#
# 2026-09-20（UI 美化）：把配色收成**三层**，免得「暖灰、冷灰混着用」
# （照 redesign-skill 审：灰必须同族，暖就一路暖到底）
#   第一层 · 中性面：底色、卡片、浅底、描边、四级文字 —— 全部暖调，数值唯一
#   第二层 · 语义色：成功 / 提醒 / 危险 —— 只在真正表意时出现（如逾期、灯号）
#   第三层 · 品牌色：各 Agent 自己的主色，只用于卡片左脊与图标
BG = "#fbfbfa"          # 暖白画布
CARD = "#ffffff"        # 卡片白
SOFT = "#f7f6f3"        # 暖浅底（标签、键帽）
STRIP = "#f3f2ef"       # 更浅一档的暖底（分段控件槽、悬停底）
LINE = "#eaeaea"        # 分隔线一律这个超淡灰
EDGE = "#e6e5e1"        # 卡片/控件描边（比 LINE 略实，勾得出轮廓）
FG = "#111111"          # 正文用近黑（不用纯黑）
DIM = "#5a5f5c"         # 次文字
FAINT = "#84817c"       # 弱文字（暖灰）—— 统一为暖调，不再用冷灰 #787774
INFO = "#1f6feb"        # 强调蓝（语义：可点、可启）
INFO_BG = "#e1f3fe"     # 淡蓝底（pastel）
INK = "#111111"         # 主操作实心色（黑底白字，全应用只此一种「最重」）
INK_ON = "#2b2b2b"      # 主操作的悬停态
FOCUS = "#111111"       # 焦点环：键盘走到哪儿，一眼看得见

# 间距令牌：全应用只用这几档（4 的倍数），别再随手写 7/13/26
SP_1 = _px(4)
SP_2 = _px(8)
SP_3 = _px(12)
SP_4 = _px(16)
SP_5 = _px(20)
SP_6 = _px(24)
SP_8 = _px(32)
# 页面两侧的统一留白（页头、指标条、卡片区一律对齐这条线）
PAGE_X = _px(28)

# 首页右上角那张配图（本版要求）：等比例缩到与「页头 + 统计条」齐平，
# 统计条相应向左收窄给它腾位。文件不在就当没有这张图，不影响启动。
HOME_ART_DIR = os.path.join(os.path.expanduser("~"), "Desktop", u"配图")
HOME_ART_STEM = "777"
HOME_ART_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp")
HOME_ART_H = 196          # 逻辑像素：刚好等于「页头 + 统计条」那一块的高
HOME_ART_BLEED = True     # True=右边缘贴窗口右缘；False=与内容列右缘对齐
HOME_RULE = True          # 要不要那条「从左边拉过去、与配图底部黑条接上」的粗黑线
# 粉彩点缀（标签底色 / 深色文字），来自该协议指定的四色
PASTEL = {
    "red":    ("#fdebec", "#9f2f2d"),
    "blue":   ("#e1f3fe", "#1f6c9f"),
    "green":  ("#edf3ec", "#346538"),
    "yellow": ("#fbf3db", "#956400"),
    "gray":   ("#f7f6f3", "#4a4f57"),
}

# 字体（加字号、标题加粗）
F_TITLE = ("Microsoft YaHei UI", 20, "bold")
F_SUB = ("Microsoft YaHei UI", 11)
F_TAB = ("Microsoft YaHei UI", 11, "bold")
F_METRIC_L = ("Microsoft YaHei UI", 11, "bold")
F_METRIC_N = ("Microsoft YaHei UI", 21, "bold")
F_CARD_T = ("Microsoft YaHei UI", 13, "bold")
F_CARD_D = ("Microsoft YaHei UI", 10)
F_MONO = ("Consolas", 9)
F_TAG = ("Microsoft YaHei UI", 9, "bold")
F_HINT = ("Microsoft YaHei UI", 9)
F_DLG_T = ("Microsoft YaHei UI", 15, "bold")
F_DLG_B = ("Microsoft YaHei UI", 12, "bold")
F_DLG = ("Microsoft YaHei UI", 10)
F_DLG_M = ("Consolas", 9)
F_SEC = ("Microsoft YaHei UI", 11, "bold")

# 每栏的饮料色（同一栏内的杯子再按序号微调明度）
CAT_LIQUID = {
    "agents": "#2f7fe0",
    "skills": "#0f9d6e",
    "mcp": "#c9761a",
    "tasks": "#c9a227",
}

# 五味饮料轮盘：同一栏内轮流上色，像一柜子不同口味
DRINK_RAMP = [
    "#2f7fe0", "#0f9d6e", "#c9761a", "#8a5cd8", "#c94f6b",
    "#1f8f9e", "#5b8c1f", "#b8593f", "#3f6fd8", "#a8447f",
]


def cat_liquid(cat, idx):
    """取该栏目里第 idx 张卡片的主色（非 Agents 栏按栏基调微调明度）。"""
    if cat == "agents":
        return DRINK_RAMP[idx % len(DRINK_RAMP)]
    base = CAT_LIQUID.get(cat, INFO)
    # 同栏内做极轻的明暗轮换，免得一柜同色显得死
    shifts = [0.0, -0.10, 0.10, -0.05, 0.05]
    t = shifts[idx % len(shifts)]
    if t > 0:
        return lighten_hex(base, t)
    return darken_hex(base, -t)


def lighten_hex(c, t):
    a = [int(c[i:i + 2], 16) for i in (1, 3, 5)]
    m = [int(round(v + (255 - v) * t)) for v in a]
    return "#%02x%02x%02x" % tuple(m)


def darken_hex(c, t):
    a = [int(c[i:i + 2], 16) for i in (1, 3, 5)]
    m = [int(round(v * (1 - t))) for v in a]
    return "#%02x%02x%02x" % tuple(m)


def clip_units(text, budget):
    """按**显示宽度**截断文字（中日韩全角算 2 个单位，其余算 1）。

    为什么不用 `text[:n]`：卡片的宽是固定的（约 400px），一行装得下
    26 个汉字，却装得下 50 个西文字母。按**字数**截，纯英文的说明会
    短得可惜、纯中文的说明会溢出行外 —— 卡片高度是死的，溢出就压到
    底下那行路径上去（改版前的原病）。按显示宽度截，中英各得其所。
    """
    text = " ".join(str(text or "").split())
    out = []
    used = 0
    for ch in text:
        u = 2 if ord(ch) > 0x2E80 else 1        # 0x2E80 起为 CJK 部首及全角
        if used + u > budget:
            return "".join(out).rstrip() + u"\u2026"
        out.append(ch)
        used += u
    return text


# 卡片描述的行数预算：一行 26 个汉字 ≈ 52 个单位，两行 ≈ 104，留点余量取 100
DESC_UNITS = 100


def _icon_dominant_color(path, fallback="#2f7fe0"):
    """取图标主色调。

    做法：把图标缩到 24×24，丢弃透明/近白/近灰的像素，
    把余下像素按 RGB 量化投票（每通道压到 4 bit，即 4096 桶），
    取票数最多那桶的**桶内均值**；若整体过于灰暗，退到 fallback。
    不依赖 Pillow 亦可用（走 tkinter PhotoImage 慢速回退）。
    """
    if not path or not os.path.isfile(path):
        return fallback
    try:
        from PIL import Image
    except Exception:
        return fallback
    try:
        im = Image.open(path).convert("RGBA")
        im = im.resize((24, 24), Image.LANCZOS)
        buckets = {}
        for (r, g, b, a) in im.getdata():
            if a < 110:                      # 太透明，弃
                continue
            mx, mn = max(r, g, b), min(r, g, b)
            if mx > 238 and (mx - mn) < 26:  # 近白（图标底），弃
                continue
            if (mx - mn) < 22:               # 近灰（黑白图标），弃
                continue
            key = (r >> 4, g >> 4, b >> 4)
            acc = buckets.setdefault(key, [0, 0, 0, 0])
            acc[0] += r
            acc[1] += g
            acc[2] += b
            acc[3] += 1
        if not buckets:
            return fallback
        key = max(buckets, key=lambda k: buckets[k][3])
        n = buckets[key][3]
        r = int(buckets[key][0] / n)
        g = int(buckets[key][1] / n)
        b = int(buckets[key][2] / n)
        # 取到的色若太暗（近黑），提亮一档，免得卡片竖条看着像墨块
        while max(r, g, b) < 130:
            r = min(255, int(r * 1.35) + 10)
            g = min(255, int(g * 1.35) + 10)
            b = min(255, int(b * 1.35) + 10)
        return "#%02x%02x%02x" % (r, g, b)
    except Exception:
        return fallback

# 工作记录全库检索：哪些文件算「工作/对话历史」，以及扫多大。
# （本版要求：搜索框要能翻遍各 Agent 的工作记录，好知道某个项目出自谁手）
HIST_EXTS = (".md", ".txt", ".json", ".jsonl", ".py", ".js", ".ts", ".html", ".htm",
             ".csv", ".log", ".bat", ".cmd", ".ps1", ".sh", ".yaml", ".yml",
             ".toml", ".ini", ".cfg", ".sql", ".java", ".kt", ".xml")
HIST_MAX_BYTES = 3 * 1024 * 1024          # 单文件上限 3 MB，免得撞上巨型日志
HIST_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".idea"}


# 鼠标悬停说明：一枚按钮一句话，讲清它干什么。
# 索引键是**按钮上的字**（比对时去空格，故「保 存」与「保存」等同）；
# 挂载方式是「按控件类接管」（见 _tips_on_class），**因此以后新加的按钮
# 只要文字落在这张表里，就自动有说明**，不必再去每个窗里手挂一遍。
TIP_TEXT = {
    # 主页工具条
    "Agents": "查看可供差遣的智能体",
    "技能": "查看本机可用的技能（SKILL.md）",
    "MCP服务": "查看已接入的 MCP 服务与启动命令",
    "主要项目": "只看你设为「主要项目」的那些：开发日志、已实现的功能、产物在哪",
    "设置主要项目": "增删你要盯的项目（每行一条：项目名 = 关键词1, 关键词2）",
    "重新扫描": "重新检索本机，刷新这份名册",
    "接入 Agent": "给各 Agent 接上 MCP 检索、并在它每次必读处放一行指针；内含测试提示词",
    "检索地址": "登记这个 Agent 自己的记忆/记录目录（让它自报家门，把路径粘进来）",
    # 第四十一轮（本版要求）：把「接入 Agent」窗里那几枚按钮的作用写清楚
    "立即接入/重新复检": "现在就把各 Agent 接一遍（接 MCP、放读取指引），"
                        "并把结果重新检查——哪些接了、哪些没接、为什么",
    "复制测试提示词": "复制一段自检提示词：粘给任意 Agent，看它到底通没通"
                     "（会问它能不能查到你机器上的真实项目，而不是问它『有没有工具』）",
    "复制读取指引（免MCP）": "复制那份含数据地址的读取指引——不走 MCP 也能用："
                            "粘进任何 Agent 的人格/设置里即可（对 WorkBuddy、"
                            "Claude Desktop 这类有信任门槛或没有 MCP 的客户端，"
                            "这是唯一的办法）",
    "复制指针原文": "复制那行『先查后答』的短指针——给人读的短版，"
                   "适合粘进人格文件开头",
    # 顺带把几个对话框按钮也补上（以前一直没说明）
    "＋添加": "加一个要盯的项目（填项目名 + 关键词，逗号或顿号分隔）",
    "编辑": "改选中项目：项目名与关键词；留空关键词就按项目名匹配",
    "删除": "从主要项目里去掉选中项（只删设置，不动任何记录文件）",
    "打开": "在资源管理器里打开这个记录根所在的位置",
    "＋添加Agent": "手动登记一个可启动的程序（exe / lnk / bat）",
    "＋导入Skill": "把别处的技能收进本机技能库",
    # 工作台 / 详情窗
    "打开本体目录": "在资源管理器里打开这个 Agent 本体所在的目录",
    "手动添加检索地址": "添加记忆信息供其他 agent 浏览",
        "查看/编辑简介": "看简介全文，并可直接改（改完存进自订档，重扫不丢）",
    "打开所在目录": "在资源管理器里打开它所在的目录",
    "打开工作台": "打开它的工作台：它记在哪、本体在哪、可启动",
    "启动": "启动这个 Agent 本体",
    # 更新数据浮窗
    "复制提示词": "把上面这段提示词整段拷进剪贴板",
    # 编辑简介窗
    "保存": "把这段简介存进自订档（重扫不会丢）",
    "取消": "放弃改动，关掉本窗",
    "清空": "清掉简介，恢复成扫描到的说明",
    # 通用
    "关闭": "关掉本窗",
}

CATS = [
    ("agents", "Agents"),
    ("skills", "技能"),
    ("mcp", "MCP 服务"),
    ("tasks", "主要项目"),
]




def load_inventory():
    if INV and os.path.isfile(INV):
        try:
            with open(INV, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"skills": [], "mcp": [], "skill_shelves": []}


# 不上架的名单。与 scan_agents_apps.py 的 BLOCK 同源，双保险：
# 即便磁盘上残留着旧版 agents.json，被点名者也休想复活。
_BLOCK_NAMES = [
    "codex++管理工具",
    "codex++manager",
    "codexplusmanager",
    "astrbotlauncher",
    "astrbot启动器",
]


def _norm_name(name):
    """归一化名字（去空格、连字符、下划线、加号，转小写）。"""
    return re.sub(r"[\s\-_+＋]+", "", str(name)).lower()


_BLOCK = set(_norm_name(n) for n in _BLOCK_NAMES)


def load_agents():
    if AGENTS_JSON and os.path.isfile(AGENTS_JSON):
        try:
            with open(AGENTS_JSON, "r", encoding="utf-8") as f:
                d = json.load(f)
            rows = d.get("agents") or []
            out = []
            for a in rows:
                # 加载侧再筛一道：被除名的两位不许借旧快照回魂
                if _norm_name(a.get("name", "")) in _BLOCK:
                    continue
                p = a.get("exe", "")
                # 工具目录也算「在」，故目录亦认
                a["exists"] = os.path.isfile(p) or os.path.isdir(p)
                a.setdefault("kind", "agent")
                out.append(a)
            return out
        except Exception:
            pass
    return []


def _visible_windows():
    """列出此刻所有「可见且有标题」的顶层窗口：[(pid, 标题), …]。

    用来判断某个 Agent 的界面到底出来了没有 —— 拉进程成功只说明「叫醒了」，
    界面出现才算「真起来了」。
    """
    import ctypes
    from ctypes import wintypes
    u = ctypes.windll.user32
    res = []
    try:
        u.IsWindowVisible.argtypes = [wintypes.HWND]
        u.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        u.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        u.GetWindowThreadProcessId.argtypes = [wintypes.HWND,
                                               ctypes.POINTER(wintypes.DWORD)]
    except Exception:
        pass
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    def cb(hwnd, _l):
        try:
            if not u.IsWindowVisible(hwnd):
                return True
            n = u.GetWindowTextLengthW(hwnd)
            if n <= 0:
                return True
            buf = ctypes.create_unicode_buffer(n + 1)
            u.GetWindowTextW(hwnd, buf, n + 1)
            pid = wintypes.DWORD(0)
            u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            res.append((int(pid.value), buf.value))
        except Exception:
            pass
        return True

    try:
        u.EnumWindows(WNDENUMPROC(cb), 0)
    except Exception:
        pass
    return res


def _proc_paths(pids):
    """给一批 pid 采出各自的可执行文件路径（采不到的就跳过）。"""
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.windll.kernel32
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    out = {}
    for pid in pids:
        try:
            h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
            if not h:
                continue
            buf = ctypes.create_unicode_buffer(2048)
            size = wintypes.DWORD(2048)
            if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                out[int(pid)] = buf.value
            k32.CloseHandle(h)
        except Exception:
            pass
    return out


def _proc_alive(pid):
    """这个进程还在不在？（拿不到句柄即视为已退出）"""
    import ctypes
    if not pid:
        return False
    try:
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000, False, int(pid))      # QUERY_LIMITED_INFORMATION
        if not h:
            return False
        k32.CloseHandle(h)
        return True
    except Exception:
        return False


def launch_agent(agent):
    """启动某个 Agent。返回 (是否成功, 说明)。"""
    exe = agent.get("exe", "")
    if not exe:
        return False, "未配置可执行文件"
    if not os.path.isfile(exe):
        return False, "文件不存在：%s" % exe
    try:
        low = exe.lower()
        cwd = os.path.dirname(exe)
        pid = 0
        if low.endswith((".bat", ".cmd")):
            # 交给 cmd 起，拿到的是那个 cmd 的 pid —— 它的控制台窗口也算「界面出来了」
            pid = subprocess.Popen('start "" "%s"' % exe, shell=True, cwd=cwd).pid
        elif low.endswith(".lnk"):
            os.startfile(exe)            # 壳里是谁拿不到 pid，只能靠窗口名认
        else:
            pid = subprocess.Popen([exe], cwd=cwd).pid
        return True, "已启动 %s" % agent.get("name", ""), pid
    except Exception as e:
        return False, "启动失败：%s" % e


# 整合卡的专属主色（金）—— 与技能栏的绿拉开距离，一眼看得出「这不是普通技能卡」
# 「整合卡」（多篇同源技能合成的一张卡）用它标色。
# 2026-09-20：由 #b8860b 压深一档 —— 那枚金底小徽标上写的是白字，
# 原色对比度只有 3.2:1，小字达不到可读标准；压深后约 4.6:1，够看。
SUITE_COLOR = "#a3740a"

SUITE_MARK = ".suite.json"        # 整合卡的成员名单（放在入口技能目录里）


def _suite_of(skill_dir):
    """若该技能目录是「整合卡」，返回其成员表（dict）；否则 None。

    数据驱动：入口目录里放一份 `*.suite.json`，写明 members（成员目录名）。
    以后要增减成员、或再收别家的套件，**改数据即可，不必动代码**。
    """
    try:
        if not skill_dir or not os.path.isdir(skill_dir):
            return None
        for fn in sorted(os.listdir(skill_dir)):
            if fn.endswith(SUITE_MARK):
                with open(os.path.join(skill_dir, fn), "r", encoding="utf-8") as f:
                    d = json.load(f)
                if isinstance(d, dict) and d.get("members"):
                    return d
    except Exception:
        pass
    return None


def _skill_brief(skill_dir):
    """从某技能目录的 SKILL.md 里读 name / description（给整合卡列成员用）。"""
    name, desc = os.path.basename(skill_dir), ""
    try:
        t = open(os.path.join(skill_dir, "SKILL.md"), "r", encoding="utf-8",
                 errors="ignore").read()
        m = re.search(r"^name:\s*(.+)$", t, re.M)
        if m:
            name = m.group(1).strip()
        m = re.search(r"^description:\s*(.+)$", t, re.M)
        if m:
            desc = m.group(1).strip()
    except Exception:
        pass
    return name, desc


PROJECTS_NAME = "主要项目.json"


def _projects_file():
    for base in (HERE, BUNDLE):
        p2 = os.path.join(base, PROJECTS_NAME)
        if os.path.isfile(p2):
            return p2
    return os.path.join(HERE, PROJECTS_NAME)


def load_projects():
    """主要项目清单：{projects: [{name, keys, note, features}]}。

    数据驱动 —— 名字、关键词、人工功能清单都在这份 json 里，改数据即生效。
    """
    try:
        with open(_projects_file(), "r", encoding="utf-8") as f:
            d = json.load(f)
        ps = d.get("projects") if isinstance(d, dict) else None
        out = []
        for p2 in (ps or []):
            if not (isinstance(p2, dict) and p2.get("name")):
                continue
            # 关键词自愈：手写 json 时用「、」「，」「；」甚至空格分隔，一并拆开
            keys = []
            for k in (p2.get("keys") or []):
                for part in re.split(r"[、,，;；/|\s]+", str(k)):
                    part = part.strip()
                    if part and part not in keys:
                        keys.append(part)
            p2 = dict(p2)
            p2["keys"] = keys
            out.append(p2)
        return out
    except Exception:
        return []


def save_projects(projs):
    try:
        with open(_projects_file(), "w", encoding="utf-8") as f:
            json.dump({"projects": projs}, f, ensure_ascii=False, indent=1)
        return True
    except Exception:
        return False


def load_tasks():
    """工作任务及产物一览：采集器写在 agents.json 的 tasks 字段里。"""
    cands = []
    try:
        if AGENTS_JSON:
            cands.append(AGENTS_JSON)
    except Exception:
        pass
    cands += [os.path.join(HERE, "agents.json"), os.path.join(BUNDLE, "agents.json")]
    for p in cands:
        try:
            with open(p, "r", encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict) and isinstance(d.get("tasks"), list):
                return d["tasks"]
        except Exception:
            continue
    return []


def _import_scanner():
    """载入采集器模块：优先程序旁，其次内嵌资源。"""
    import importlib.util
    for p in (os.path.join(HERE, "scan_agents.py"),
              os.path.join(BUNDLE, "scan_agents.py")):
        if os.path.isfile(p):
            spec = importlib.util.spec_from_file_location("_scan_agents", p)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
    return None


def _import_app_scanner():
    """载入 Agent 客户端采集器。"""
    import importlib.util
    for p in (os.path.join(HERE, "scan_agents_apps.py"),
              os.path.join(BUNDLE, "scan_agents_apps.py")):
        if os.path.isfile(p):
            spec = importlib.util.spec_from_file_location("_scan_apps", p)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
    return None


def _count_rows(obj, keys):
    if not isinstance(obj, dict):
        return 0
    return sum(len(obj.get(k) or []) for k in keys)


def _write_data(fname, obj):
    """把采集结果写到**程序所在目录**（而非只读的 _MEIPASS）。

    单文件 exe 解压后 `HERE` 即 exe 所在目录，可能落在「下载」夹甚至 U 盘 ——
    须先试写，写不动便退回 `%LOCALAPPDATA%`，两条路都不成则放弃（内存里仍可用）。
    """
    cands = [HERE]
    la = os.environ.get("LOCALAPPDATA")
    if la:
        cands.append(os.path.join(la, APP_TITLE))
    for d in cands:
        try:
            os.makedirs(d, exist_ok=True)
            p = os.path.join(d, fname)
            with open(p, "w", encoding="utf-8") as fh:
                json.dump(obj, fh, ensure_ascii=False, indent=1)
            return p
        except Exception:
            continue
    return None


def _machine_tag():
    """本机标识：家目录路径（小写、反斜杠）。与两采集器里的同名函数**同法同源**。"""
    return os.path.expanduser("~").replace("/", "\\").rstrip("\\").lower()


def _is_local(obj):
    """这份名册是不是**本机**所产？（看产地戳 `_home`）

    · 戳与本机合 → 是
    · 戳不合      → 否（随包带来的老底册）
    · 无戳        → 视作否（旧版名册一律重扫，宁可多扫一次）

    为何要这一问 —— 本宫曾验：单文件 exe 落地即把**内嵌便携底册**解出来，
    那份底册采自打包那台机器，于是「文件在」却「不是本机的」。
    旧判据只看文件在不在，遂**永远判为「名册已齐」，一辈子不扫新机器**。
    """
    if not isinstance(obj, dict):
        return False
    return str(obj.get("_home", "")).strip().lower() == _machine_tag()


def cold_scan(force=False):
    """开箱自检：名册若缺、若为**他机所产**、或 `force`，就地全量扫描一次并入盘。

    作者之命 ——「在新电脑打开时，自动完成检索并生成对应的按钮」。
    新机上这两份 json 本不存在，或只有随包带来的**他机底册**，故须开窗之前先扫：
      · `agent_inventory.json` —— 技能 / MCP / 工具（由 scan_agents.py 采）
      · `agents.json`          —— Agent 客户端与兵站工具（由 scan_agents_apps.py 采）
    采集器若是从内嵌资源解出来的（打包形态），其 `HERE` 指向临时目录，
    故产出须显式另存到 exe 旁，程序下次才读得着。

    返回 (说明, 技能等条数, Agent 条数)。
    """
    global INV, AGENTS_JSON

    def _raw(fname):
        """取名册原文（只为读戳，不走过 `load_*` 的清洗）。"""
        for p in (os.path.join(HERE, fname), os.path.join(BUNDLE, fname)):
            if os.path.isfile(p):
                try:
                    with open(p, "r", encoding="utf-8") as fh:
                        return json.load(fh)
                except Exception:
                    pass
        for d in _portable_paths():
            p = os.path.join(d, fname)
            if os.path.isfile(p):
                try:
                    with open(p, "r", encoding="utf-8") as fh:
                        return json.load(fh)
                except Exception:
                    pass
        return None

    inv_raw = _raw("agent_inventory.json")
    ag_raw = _raw("agents.json")
    need_inv = (force
                or _count_rows(load_inventory(), INVENTORY_KEYS) == 0
                or not _is_local(inv_raw))
    need_ag = (force
               or not load_agents()
               or not _is_local(ag_raw))
    if not (need_inv or need_ag):
        return "", 0, 0

    touched = []
    faults = []
    if need_inv:
        mod = _import_scanner()
        if mod is not None:
            try:
                obj = mod.collect()
                p = _write_data("agent_inventory.json", obj)
                if p:
                    globals()["INV"] = p
                    touched.append("清单")
            except Exception as e:
                faults.append("清单未成：%s" % e)
        else:
            faults.append("清单未成：未找到采集器 scan_agents.py")
    if need_ag:
        amod = _import_app_scanner()
        if amod is not None:
            try:
                obj = amod.collect()
                p = _write_data("agents.json", obj)
                if p:
                    globals()["AGENTS_JSON"] = p
                    touched.append("Agent")
            except Exception as e:
                faults.append("Agent 未成：%s" % e)
        else:
            faults.append("Agent 未成：未找到采集器 scan_agents_apps.py")

    n_inv = _count_rows(load_inventory(), INVENTORY_KEYS)
    n_ag = len(load_agents())
    if not touched:
        note = "自动扫描未成（%s），请点右上「重新扫描」再试。" % ("；".join(faults) or "无采集器")
    else:
        note = "已按**本机**重采 %s。" % "与".join(touched)
        if faults:
            note += "（%s）" % "；".join(faults)
    # 原本是「他机底册」时，把缘由说清楚，免得用户以为丢了东西
    if inv_raw and not _is_local(inv_raw):
        note += "原有名册是别的电脑所产，已弃用重扫。"
    return note, n_inv, n_ag


# ---------- 自订简介（可编辑） ----------
# 第二十二轮（本版要求）：给 Agent 加可编辑的简介栏。
# 要紧之处：扫描器每次重扫都会重写 agents.json，编辑若存那儿必被冲掉，
# 故自订简介另存一份 `agent_intros.json`，按 agent 的 key 索引。
INTROS_NAME = "agent_intros.json"


def _intros_file(create=False):
    """自订简介的位置：程序旁优先 → 内嵌资源 → %LOCALAPPDATA%（同 _write_data 的思路）。"""
    owned = os.path.join(HERE, INTROS_NAME)
    if os.path.isfile(owned):
        return owned
    if os.path.isfile(os.path.join(BUNDLE, INTROS_NAME)):
        return os.path.join(BUNDLE, INTROS_NAME)
    la = os.environ.get("LOCALAPPDATA")
    if la:
        p2 = os.path.join(la, APP_TITLE, INTROS_NAME)
        if os.path.isfile(p2):
            return p2
    return owned


def load_intros():
    """读自订简介；文件不在或读坏了都当空，绝不惊动主流程。"""
    try:
        with open(_intros_file(), "r", encoding="utf-8") as f:
            d = json.load(f)
        if not isinstance(d, dict):
            return {}
        return {str(k): str(v) for k, v in d.items() if str(v).strip()}
    except Exception:
        return {}


def save_intros(d):
    """写自订简介；写不动程序旁会自动退 %LOCALAPPDATA%。"""
    try:
        return bool(_write_data(INTROS_NAME, d))
    except Exception:
        return False


INVENTORY_KEYS = ("skills", "agents", "mcp", "skill_shelves")


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self._beat("tk ok")
        self.title(APP_TITLE)
        # 字体按 DPI 缩放（Tk 的点→像素换算），窗口尺寸同比放大 ——
        # 这样在缩放屏上「视觉大小不变，但笔画是原生渲染」。
        try:
            self.tk.call("tk", "scaling", (96.0 / 72.0) * UI_SCALE)
        except Exception:
            pass
        # 夹进屏幕：缩放屏上按比放大后，窗口可能比屏幕还大
        #（极端例：1080p 屏若设 200% 缩放，本窗需 2360×1520 物理像素）
        _sw, _sh = self.winfo_screenwidth(), self.winfo_screenheight()
        _w = min(_px(1180), max(_px(700), _sw - _px(40)))
        _h = min(_px(760), max(_px(460), _sh - _px(80)))
        # 第五十轮（本版要求：让这个窗口聪明点）：**记住上次的尺寸与位置** ——
        #   否则每次开都是默认大小、还要自己拖。记住的几何也要夹进当前屏幕
        #   （换了显示器/缩放了也不至于开到屏幕外）。
        _geo = self._load_window_geo()
        if _geo.get("w") and _geo.get("h"):
            _w2 = max(_px(700), min(int(_geo["w"]), _sw - _px(20)))
            _h2 = max(_px(460), min(int(_geo["h"]), _sh - _px(60)))
            _x2 = _geo.get("x")
            _y2 = _geo.get("y")
            if _x2 is None or _y2 is None or not (0 <= int(_x2) < _sw - _px(200)) \
                    or not (0 <= int(_y2) < _sh - _px(120)):
                self.geometry("%dx%d" % (_w2, _h2))
            else:
                self.geometry("%dx%d+%d+%d" % (_w2, _h2, int(_x2), int(_y2)))
            if _geo.get("zoomed"):
                try:
                    self.state("zoomed")
                except Exception:
                    pass
        else:
            self.geometry("%dx%d" % (_w, _h))
        self.minsize(min(_px(900), _w), min(_px(560), _h))
        self.bind("<Configure>", self._on_configure)
        self.configure(bg=BG)
        self._set_window_icon()

        # 第六十二轮（本版要求）：页头右上角摆一张【配图】——**右边缘与窗口右边缘重合**。
        #   原图 1216×1632（3:4 竖图），按页头那条带子的高度**等比例缩小**到
        #   103×138 物理像素（=69×92 逻辑像素），用 place(relx=1.0, anchor="ne") 钉在右上角：
        #   窗口怎么拉、怎么缩，它始终贴着右边、垂直位置不变。
        try:
            _bp = os.path.join(HERE, "agent_icons", "brand_666.png")
            if os.path.isfile(_bp):
                self._brand_img = tk.PhotoImage(file=_bp)
                _bl = tk.Label(self, bg=BG, bd=0, image=self._brand_img)
                _bl.place(relx=1.0, y=_px(22), anchor="ne")
                self._brand_label = _bl
        except Exception:
            self._brand_img = None

        # 开箱自检：新电脑上名册缺失，先就地全量扫一遍，再来摆卡片。
        # 第五十二轮（照 redesign-skill 审）：扫盘要摸一遍盘、可能好几秒 ——
        #   先摆一句「正在扫描本机…」并**立刻刷一次窗口**，别让人对着空窗干等。
        _splash = tk.Label(self,
                           text=u"正在扫描本机…\n（首次启动要在盘上摸一遍，稍候）",
                           bg=BG, fg=FAINT, font=("Microsoft YaHei UI", 11),
                           justify="center")
        _splash.pack(expand=True)
        try:
            self.update_idletasks()
            self.update()
        except Exception:
            pass
        self.scan_note, _ni, _na = cold_scan()
        try:
            _splash.destroy()
        except Exception:
            pass
        self.data = load_inventory()
        self._beat("data ok")
        self.agents = load_agents()
        # 见到「没见过的 Agent」→ 自动把它存储目录里的 Skill 收一份进技能库
        try:
            self.import_note = self._auto_import_new_agents(self.agents)
        except Exception:
            self.import_note = ""
        self.cat = "agents"
        self.rows = []
        self.intros = load_intros()      # 自订简介：key → 文字（可编辑，重扫不丢）
        self.tasks = load_tasks()         # 任务一览（采集器写在 agents.json 里）
        self.projects = load_projects()   # 主要项目清单（用户自己设的）
        self._hist_queue = queue.Queue()  # 工作记录全库检索：子线程 → 主线程的信箱
        self._hist_hits = {}              # {关键字: [命中,…]}
        self._img_cache = {}

        self._style()
        self._beat("style ok")
        self._match_titlebar()
        self._build_header()
        self._home_art_w = self._build_home_art()   # 右上角配图（没有就是 0）
        self._build_bar()
        self._build_list()
        self.refresh()
        try:
            if getattr(self, "_home_art_lb", None) is not None:
                self._home_art_lb.lift()        # 压在统计条白面之上
            if getattr(self, "_home_rule", None) is not None:
                self._home_rule.lift()
        except Exception:
            pass
        self._tips_on_class()        # 悬停说明：按控件类接管，此后新造的按钮也自动受管
        # 第六十二轮（本版要求）：首页那张配图也挂一句悬停说明
        try:
            if getattr(self, "_home_art_lb", None) is not None:
                self._tip(self._home_art_lb, u"", side="left")
        except Exception:
            pass
        self._poll_hist()            # 工作记录检索结果的取件循环
        self._bind_shortcuts()       # 键盘快捷键
        # 自动抄录：开机 8 秒后先抄一轮，此后每 10 分钟一轮
        self._autolog_box = queue.Queue()
        self._autolog_busy = False
        self.after(_px(8000) // 1, self._auto_log_tick)
        self.after(1200, self._poll_autolog)
        self._beat("render ok")
        # 观感起见，让窗口先亮出来，再禀报本次自动检索的结果
        if self.scan_note or getattr(self, "import_note", ""):
            self.after(420, self._announce_scan)

    def _announce_scan(self):
        try:
            extra = getattr(self, "import_note", "")
            head = (self.scan_note + " " if self.scan_note else "") + \
                   (extra + " " if extra else "")
            self.status.configure(
                text="%s技能 %d · MCP %d · Agent %d。"
                     % (head, self._skill_count(),
                        len(self.data.get("mcp", [])), len(self.agents)))
        except Exception:
            pass

    def _beat(self, tag):
        """排障心跳：把启动进度落到文件，只在前 12 行内写。"""
        try:
            p = os.path.join(HERE, "_boot.log")
            n = 0
            if os.path.isfile(p):
                with open(p, "r", encoding="utf-8") as f:
                    n = len(f.readlines())
            if n < 12:
                with open(p, "a", encoding="utf-8") as f:
                    f.write("%s\t%s\n" % (tag, "GlassCard=%s" % (GlassCard is not None)
                                          if tag == "tk ok" else "ok"))
        except Exception:
            pass

    # ---------- 图标 ----------
    def _icon(self, name, size=44):
        """按尺寸取（并缓存）图标 PhotoImage；失败返回 None。"""
        if not name or not ICON_DIR:
            return None
        key = (name, size)
        if key in self._img_cache:
            return self._img_cache[key]
        path = os.path.join(ICON_DIR, name)
        img = None
        if os.path.isfile(path):
            try:
                from PIL import Image, ImageTk
                im = Image.open(path).convert("RGBA")
                im = im.resize((size, size), Image.LANCZOS)
                img = ImageTk.PhotoImage(im)
            except Exception:
                try:
                    img = tk.PhotoImage(file=path)
                    if size and img.width() != size:
                        fac = max(1, round(img.width() / size))
                        if fac > 1:
                            img = img.subsample(fac, fac)
                except Exception:
                    img = None
        self._img_cache[key] = img
        return img

    def _fit(self, w, h):
        """把窗口尺寸夹进屏幕再返回 (w, h)。

        缩放屏上按比放大后，对话框可能超出屏幕（200% 缩放 + 1080p 屏尤甚），
        超出就会被切掉一截、按不到底部的按钮。故一律先夹后设。
        """
        try:
            sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
            return (min(int(w), max(_px(360), sw - _px(30))),
                    min(int(h), max(_px(280), sh - _px(60))))
        except Exception:
            return (int(w), int(h))

    def _set_window_icon(self):
        """把窗口/任务栏图标也换成程序旁那枚 app_icon.ico。

        从前后只在打包时嵌了 exe 图标，**运行时没设过窗口图标** ——
        所以直接跑源码时任务栏是 tk 羽毛。今令一处换、两处都变。
        """
        for p in (os.path.join(HERE, "app_icon.ico"),
                  os.path.join(BUNDLE, "app_icon.ico")):
            if os.path.isfile(p):
                try:
                    self.iconbitmap(p)
                    return
                except Exception:
                    continue

    def _match_titlebar(self):
        """窗口标题栏**跟随内容走浅色**（Win10 1809+）。

        2026-09-20（UI 美化）：原先这里写死深色，理由是「与内容浑然一体」——
        可内容明明是暖白底。结果是一条近黑标题栏压在暖白页面上，像从别的程序
        上撕下来贴上去的（照 redesign-skill 审：「浅色页面里插一块深色」是最显眼
        的 AI 痕迹）。今改为显式跟随浅色，也与「暖白极简」这一套自洽。
        """
        try:
            import ctypes
            self.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(self.winfo_id())
            if not hwnd:
                hwnd = self.winfo_id()
            value = ctypes.c_int(0)          # 0 = 浅色标题栏
            for attr in (20, 19):            # DWMWA_USE_IMMERSIVE_DARK_MODE
                ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, attr, ctypes.byref(value), ctypes.sizeof(value))
            ctypes.windll.user32.SetWindowTextW(hwnd, APP_TITLE)
        except Exception:
            pass

    # ---------- 样式 ----------
    def _style(self):
        st = ttk.Style(self)
        try:
            st.theme_use("clam")
        except Exception:
            pass
        st.configure(".", background=BG, foreground=FG, fieldbackground=SOFT,
                     bordercolor=LINE, lightcolor=LINE, darkcolor=LINE)
        st.configure("TFrame", background=BG)
        st.configure("Card.TFrame", background=CARD)
        st.configure("TLabel", background=BG, foreground=FG)
        st.configure("Dim.TLabel", background=BG, foreground=DIM)
        st.configure("Faint.TLabel", background=BG, foreground=FAINT)
        st.configure("Metric.TLabel", background=CARD, foreground=FAINT)
        st.configure("MetricNum.TLabel", background=CARD, foreground=FG)

        # ---- 控件四级制（2026-09-20 美化重排）----
        # 从前是「页签描边方块 + 重新扫描描边方块 + 栏内动作实心黑」，
        # 一排里三种方块挤着，谁都不是主角。今按用途分成四级：
        #   ① Nav / NavOn —— 栏目导航（主导航只此一处，选中者是全场唯一的黑）
        #   ② Act         —— 本栏主操作（一栏只许一个）
        #   ③ Tab         —— 次要操作（描边白底，各处对话框按钮都是它）
        #   ④ Nav（未选） —— 未选中项：无底无边，纯文字，安静
        st.configure("Tab.TButton", background=CARD, foreground=FG,
                     borderwidth=1, relief="solid", padding=(15, 8), focusthickness=0,
                     font=F_TAB)
        st.map("Tab.TButton",
               background=[("active", SOFT)],
               foreground=[("active", FG)],
               bordercolor=[("active", "#d8d6d1")])
        # 未选中的栏：无底无边，只在悬停时浮出一层暖底
        st.configure("Nav.TButton", background=BG, foreground=DIM,
                     borderwidth=0, relief="flat", padding=(14, 8), focusthickness=0,
                     font=F_TAB)
        st.map("Nav.TButton",
               background=[("active", STRIP)],
               foreground=[("active", FG)])
        # 选中的栏：实心近黑 + 白字，全应用只有它这么重
        st.configure("NavOn.TButton", background=INK, foreground="#ffffff",
                     borderwidth=0, relief="flat", padding=(14, 8), focusthickness=0,
                     font=F_TAB)
        st.map("NavOn.TButton",
               background=[("active", INK_ON)],
               foreground=[("active", "#ffffff")])
        # （旧名保留：别处若有引用不至于炸）
        st.configure("TabOn.TButton", background=INK, foreground="#ffffff",
                     borderwidth=0, relief="flat", padding=(16, 8), focusthickness=0,
                     font=F_TAB)

        # 滚动条：细、暖、无箭头槽 —— 从前那根又宽又蓝，每次滚都被它牵走视线
        st.configure("TScrollbar", background="#dcdad5", troughcolor=BG,
                     bordercolor=BG, arrowcolor=BG, relief="flat")
        st.map("TScrollbar",
               background=[("active", "#c4c2bc"), ("pressed", "#b4b2ac")])
        st.configure("TSeparator", background=LINE)

        # 主操作按钮：实心近黑 + 白字（协议里的 Primary CTA）。
        # 与描边的次要按钮形成「实心 vs 描边」的层级差 ——
        # 这也是把「栏内主操作」与「栏内副操作」区分开的主要手段。
        st.configure("Act.TButton", background=INK, foreground="#ffffff",
                     borderwidth=0, relief="flat", padding=(15, 8),
                     focusthickness=0, font=F_TAB)
        st.map("Act.TButton",
               background=[("active", INK_ON), ("disabled", "#c9c7c2")],
               foreground=[("disabled", "#ffffff")])
        # 主操作（第六十三轮）：一屏**只此一处实心**。比 Act 大一号、字加粗；
        #   悬停提亮、按下再压深 —— 照 redesign-skill：交互必须有悬停与按下反馈。
        st.configure("Primary.TButton", background=INK, foreground="#ffffff",
                     borderwidth=0, relief="flat", padding=(22, 11),
                     focusthickness=0, font=("Microsoft YaHei UI", 11, "bold"))
        st.map("Primary.TButton",
               background=[("pressed", "#000000"), ("active", INK_ON),
                           ("disabled", "#c9c7c2")],
               foreground=[("disabled", "#ffffff")])

        # 搜索框：白底 + 淡描边，键盘进去时描边转为近黑（这是焦点环，不是装饰）
        st.configure("Search.TEntry", fieldbackground=CARD, background=CARD,
                     foreground=FG, bordercolor=EDGE, lightcolor=EDGE,
                     darkcolor=EDGE, insertcolor=FG, relief="flat",
                     padding=(10, 7), font=("Microsoft YaHei UI", 11))
        st.map("Search.TEntry",
               bordercolor=[("focus", FOCUS)],
               lightcolor=[("focus", FOCUS)],
               darkcolor=[("focus", FOCUS)],
               fieldbackground=[("disabled", SOFT)])
        # 键帽（快捷键提示）：1px 淡边 + 浅底 + 等宽字，同协议的 Keystroke Micro-UI
        st.configure("Kbd.TLabel", background=SOFT, foreground=DIM, borderwidth=1,
                     relief="solid", padding=(5, 1), font=F_MONO)
        st.configure("Eyebrow.TLabel", background=BG, foreground=FAINT,
                     font=("Microsoft YaHei UI", 8, "bold"))

    # ---------- 头部 ----------
    def _build_header(self):
        """页头三行：小字抬头 → 大标题 → 一句话说明。

        照 redesign-skill：标题要有「分量」—— 改版前标题 15 号常规字重，
        与说明文字几乎一样重，整页没有一个落脚点。今把标题加重到 17 号粗体，
        上面再压一枚 8 号弱色抬头，三级层次一眼分明。
        """
        head = ttk.Frame(self, padding=(PAGE_X, SP_5, PAGE_X, SP_2))
        head.pack(fill="x")
        tk.Label(head, text=u"\u672c \u673a \u540d \u518c", bg=BG, fg=FAINT,
                 font=("Microsoft YaHei UI", 8, "bold")).pack(anchor="w")
        tk.Label(head, text=APP_TITLE, bg=BG, fg=FG,
                 font=("Microsoft YaHei UI", 17, "bold")).pack(
                     anchor="w", pady=(SP_1, 0))
        ttk.Label(
            head,
            text="本机所有 Agent、技能、MCP 服务与工具的统一名册。点击 Agent 卡片即可启动。",
            style="Faint.TLabel", font=("Microsoft YaHei UI", 10),
        ).pack(anchor="w", pady=(SP_1, 0))

    def _build_home_art(self):
        """首页右上角配图：按比例缩到与「页头 + 统计条」同高，返回它的宽度。

        本版要求：图放右上角、右边缘贴窗口右缘；统计条向左缩小腾位。
        图缺失 / 没装 PIL 都**静默跳过**（返回 0）—— 装饰不该成为启动的前提。
        """
        path = ""
        for _e in HOME_ART_EXTS:
            _p = os.path.join(HOME_ART_DIR, HOME_ART_STEM + _e)
            if os.path.isfile(_p):
                path = _p
                break
        if not path:
            return 0
        h = _px(HOME_ART_H)
        try:
            from PIL import Image, ImageTk
            src_img = Image.open(path).convert("RGBA")
            # 先在原图上看：**最底下那条全宽黑条有多厚** —— 那条就是「桌沿」，
            # 新画的粗黑线要跟它接上，故位置与厚度都从图上量，而不是写死。
            _pxl = src_img.load()
            _w0, _h0 = src_img.size
            _band = 0
            for _yy in range(_h0 - 1, max(0, _h0 - _h0 // 4), -1):
                _sample = [_pxl[_xx, _yy] for _xx in range(0, _w0, max(1, _w0 // 40))]
                if all((c[3] > 180 and c[0] < 80 and c[1] < 80 and c[2] < 80)
                       for c in _sample):
                    _band += 1
                else:
                    break
            im = src_img
            w = max(1, int(round(im.width * h / float(max(1, im.height)))))
            im = im.resize((w, h), Image.LANCZOS)
            img = ImageTk.PhotoImage(im)
        except Exception:
            return 0
        lb = tk.Label(self, bg=BG, image=img, bd=0)
        lb.image = img                      # 留住引用，别被回收
        self._home_art_img = img
        self._home_art_lb = lb
        lb.place(relx=1.0, x=(0 if HOME_ART_BLEED else -PAGE_X), y=0, anchor="ne")
        self._home_art_w = w
        # 那条粗黑线（本版要求）：从窗口左边缘拉过去，右端压进图里 2px 与黑条接上，
        # 于是「线 + 图上的桌沿」连成一条从左到右贯通的粗黑线。
        if HOME_RULE and _band:
            _rh = max(3, int(round(_band * h / float(max(1, _h0)))))
            rule = tk.Frame(self, bg="#000000", bd=0, highlightthickness=0)
            rule.place(x=0, y=(h - _rh), relwidth=1.0,
                       width=-(w - 2), height=_rh)
            self._home_rule = rule
        return w

    # ---------- 指标条 ----------
    def _build_metrics(self, parent):
        wrap = ttk.Frame(parent)
        # 第五十九轮（本版要求）：统计条向右少占一块，给右上角那张配图腾位
        _pw = getattr(self, "_home_art_w", 0)
        wrap.pack(fill="x", pady=(0, SP_4),
                  padx=(0, (_pw + SP_3) if _pw else 0))
        self.metric_wrap = wrap
        self.metric_labels = {}
        self._render_metrics()

    def _metric_values(self):
        return [
            # 只数真 Agent（客户端）；兵站工具与说明文档不计入（本版要求）
            ("Agents", len([a for a in self.agents
                            if (a.get("kind") or "agent") == "agent"
                            and a.get("exists")])),
            ("技能", self._skill_count()),
            ("MCP 服务", len([m for m in self.data.get("mcp", []) if not m["name"].endswith(".env")])),
            ("工作任务", len(getattr(self, "tasks", []) or [])),
        ]

    def _render_metrics(self):
        """一条**细线分隔的统计条**，不再是四张并排的小白卡。

        改版前是四个各自带边的白框，四个框挤在一条线上，读起来像四块
        碎片；此处收成一整条，格与格之间只用一根发丝线分开 —— 白面连成
        一片，数字才立得起来（照 redesign-skill：卡片只在真正表达层级时
        才有存在的理由）。数字走等宽字，位数不同也对得齐。
        """
        wrap = getattr(self, "metric_wrap", None)
        if wrap is None:
            return
        for ch in wrap.winfo_children():
            ch.destroy()
        strip = tk.Frame(wrap, bg=CARD, highlightbackground=EDGE,
                         highlightthickness=1, bd=0)
        strip.pack(fill="x")
        for i, (label, num) in enumerate(self._metric_values()):
            if i:
                # 发丝分隔线：上下各留一段气口，不顶到边
                tk.Frame(strip, bg=LINE, width=1).pack(
                    side="left", fill="y", pady=SP_3)
            cell = tk.Frame(strip, bg=CARD)
            cell.pack(side="left", fill="both", expand=True,
                      padx=(SP_5, SP_4), pady=SP_3)
            tk.Label(cell, text=label, bg=CARD, fg=FAINT, anchor="w",
                     font=("Microsoft YaHei UI", 10)).pack(anchor="w")
            tk.Label(cell, text=str(num), bg=CARD, fg=FG, anchor="w",
                     font=("Consolas", 20, "bold")).pack(
                         anchor="w", pady=(SP_1, 0))

    # ---------- 接入 Agent（MCP + 每次必读指针） ----------
    def _self_exe(self):
        """本应用的可执行文件：打包后是自身，源码运行时取同目录那枚 exe。"""
        if getattr(sys, "frozen", False):
            return sys.executable
        return os.path.join(HERE, "Agent资产总览.exe")

    def _settings_file(self):
        return os.path.join(HERE, "MCP设置.json")

    def _load_settings(self):
        try:
            d = json.load(open(self._settings_file(), "r", encoding="utf-8"))
            return d if isinstance(d, dict) else {}
        except Exception:
            return {}

    def _save_settings(self, d):
        try:
            json.dump(d, open(self._settings_file(), "w", encoding="utf-8"),
                      ensure_ascii=False, indent=1)
            return True
        except Exception:
            return False

    def _auto_onboard(self):
        """扫描结束后：**发现新 Agent 就自动给它接 MCP、放指针**（本版要求，写进应用里）。"""
        try:
            import agent_onboard
            if not self._load_settings().get("auto_attach", True):
                return
            agents = [a for a in (self.data or [])
                      if (a.get("kind") or "agent") == "agent"]
            news = agent_onboard.new_agents(agents)
            if not news:
                return
            r = agent_onboard.attach(agents, self._self_exe(), only_new=True)
            if r.get("lines"):
                self.status.configure(
                    text="新 Agent 已自动接入：%s" % " ｜ ".join(r["lines"])[:140])
        except Exception:
            pass

    def onboard_dialog(self, agent=None):
        """接入 Agent：接 MCP、放指针、开关、以及**可复制的测试提示词**。

        第五十九轮（本版要求）：从首页搬进各 Agent 的工作台 —— 传了 agent 就
        **只办这一个**（标题、文案、接入范围都跟着它走）；不传仍是全体模式
        （MCP 状态窗里那枚「去接入 Agent」、卡上「人格」灯仍走这条老路）。
        """
        import agent_onboard
        _nm = (agent or {}).get("name") or ""
        _scope = (u"接入 Agent · %s" % _nm) if _nm else u"接入 Agent"
        dlg = tk.Toplevel(self)
        dlg.title(_scope)
        dlg.configure(bg=BG)
        dlg.transient(self)
        w, h = self._fit(_px(780), _px(640))
        sw, sh = dlg.winfo_screenwidth(), dlg.winfo_screenheight()
        dlg.geometry("%dx%d+%d+%d" % (w, h, (sw - w) // 2, (sh - h) // 2))
        pad = ttk.Frame(dlg, padding=(20, 16))
        pad.pack(fill="both", expand=True)
        ttk.Label(pad, text=_scope, style="TLabel",
                  font=("Microsoft YaHei UI", 13, "bold")).pack(anchor="w")
        ttk.Label(pad, text=(u"给 %s 接上 MCP 检索，并在它「每次必读」处放一行指针 —— "
                             u"否则工具在它手边，它也不知道去用。\n"
                             u"改动前一律备份；发现新 Agent 会自动接入。" % _nm)
                  if _nm else
                  (u"给各 Agent 接上 MCP 检索，并在它「每次必读」处放一行指针 —— "
                   u"否则工具在它手边，它也不知道去用。\n"
                   u"改动前一律备份；发现新 Agent 会自动接入。"),
                  style="Hint.TLabel", font=("Microsoft YaHei UI", 9),
                  justify="left").pack(anchor="w", pady=(4, 10))
        st = self._load_settings()
        v_auto = tk.BooleanVar(value=bool(st.get("auto_attach", True)))
        v_mask = tk.BooleanVar(value=bool(st.get("mask_paths", False)))
        row = tk.Frame(pad, bg=BG)
        row.pack(fill="x", pady=(0, 8))
        tk.Checkbutton(row, text="发现新 Agent 自动接入", variable=v_auto, bg=BG,
                       fg=FG, activebackground=BG, selectcolor=BG,
                       font=("Microsoft YaHei UI", 9)).pack(side="left")
        tk.Checkbutton(row, text="脱敏（把 <用户目录> 折起来，再给模型看）",
                       variable=v_mask, bg=BG, fg=FG, activebackground=BG,
                       selectcolor=BG,
                       font=("Microsoft YaHei UI", 9)).pack(side="left", padx=(16, 0))
        foot = ttk.Frame(pad)
        foot.pack(side="bottom", fill="x", pady=(12, 0))
        body = tk.Frame(pad, bg=BG)
        body.pack(fill="both", expand=True)
        txt = tk.Text(body, wrap="word", bg="#ffffff", fg=FG, relief="flat", bd=0,
                      highlightthickness=1, highlightbackground=LINE,
                      font=("Microsoft YaHei UI", 10), padx=12, pady=10, height=14)
        sb = ttk.Scrollbar(body, orient="vertical", command=txt.yview)
        txt.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        txt.pack(side="left", fill="both", expand=True)

        def show(text):
            txt.configure(state="normal")
            txt.delete("1.0", "end")
            txt.insert("1.0", text)
            txt.configure(state="disabled")

        def save_switches():
            d = self._load_settings()
            d["auto_attach"] = bool(v_auto.get())
            d["mask_paths"] = bool(v_mask.get())
            self._save_settings(d)
            self.status.configure(text="已保存：自动接入 %s ｜ 脱敏 %s"
                                  % ("开" if v_auto.get() else "关",
                                     "开" if v_mask.get() else "关"))

        def do_attach():
            save_switches()
            agents = ([agent] if agent else
                      [a for a in (self.data or [])
                       if (a.get("kind") or "agent") == "agent"])
            r = agent_onboard.attach(agents, self._self_exe(), only_new=False)
            out = ["【接入报告】", ""]
            out += r.get("lines") or [r.get("reason") or "（无）"]
            out += ["", "【测试用提示词 —— 复制粘贴给任意 Agent，看它自检结果】", "",
                    r.get("test_prompt") or ""]
            show("\n".join(out))
            self.status.configure(text="接入完成：%d 项已处理" % len(r.get("lines") or []))

        def copy_prompt():
            self.clipboard_clear()
            self.clipboard_append(agent_onboard.TEST_PROMPT)
            self.status.configure(text="测试提示词已复制 —— 粘给任意 Agent 试试。")

        def copy_read_guide():
            """复制**免 MCP 的读取指引**：含应用数据地址与查法，
            可直接粘进任何 Agent 的人格（如 WorkBuddy 的 SOUL.md）。"""
            self.clipboard_clear()
            self.clipboard_append(
                agent_onboard.READ_GUIDE_SKILL.replace("{{APP}}", HERE)
                .replace("{{VER}}", agent_onboard.GUIDE_VERSION_MARK))
            self.status.configure(
                text="读取指引已复制（含数据地址，可直接粘进任何 Agent 的人格）。")

        def copy_pointer():
            self.clipboard_clear()
            self.clipboard_append(agent_onboard.POINTER_MD % {"mark": agent_onboard.MARK})
            self.status.configure(text="指针原文已复制 —— 给不读文件的客户端（如 Claude Desktop）人工粘。")

        for t, fn in (("立即接入 / 重新复检", do_attach), ("复制测试提示词", copy_prompt),
                      ("复制读取指引（免 MCP）", copy_read_guide),
                      ("复制指针原文", copy_pointer)):
            ttk.Button(foot, text=t, style="Act.TButton" if t.startswith("立即")
                       else "Tab.TButton", command=fn).pack(side="left", padx=(0, 6))
        ttk.Button(foot, text="关 闭", style="Tab.TButton",
                   command=lambda: (save_switches(), dlg.destroy())).pack(side="right")
        # 开场先跑一遍，把现状摆出来
        do_attach()
        dlg.bind("<Escape>", lambda _e: (save_switches(), dlg.destroy()))

    def mcp_status_dialog(self, agent=None):
        """「MCP 状态」：这一家的 MCP 配置在哪、注册了没、要不要受信。

        点卡片上的 MCP 灯进来的 —— 暗灯/半灯要能变成**可操作项**，
        否则用户只知道"没亮"，不知道下一步做什么。
        """
        import json as _json
        name = (agent or {}).get("name") or ""
        key = ((agent or {}).get("key") or name).strip().lower()
        exe = self._self_exe()
        cfg = {
            "workbuddy": (os.path.join(os.path.expanduser("~"), ".workbuddy", "mcp.json"), "json"),
            "cursor": (os.path.join(os.path.expanduser("~"), ".cursor", "mcp.json"), "json"),
            "claude": (os.path.join(os.environ.get("APPDATA", ""), "Claude",
                                    "claude_desktop_config.json"), "json"),
            "astrbot": (os.path.join(os.path.expanduser("~"), ".astrbot", "data",
                                     "mcp_server.json"), "json"),
            "codexplus": (os.path.join(os.path.expanduser("~"), ".codex",
                                       "config.toml"), "toml"),
            "codex": (os.path.join(os.path.expanduser("~"), ".codex", "config.toml"), "toml"),
        }.get(key)
        dlg = tk.Toplevel(self)
        dlg.title("MCP 状态 · %s" % name)
        dlg.configure(bg=BG)
        dlg.transient(self)
        w, h = self._fit(_px(820), _px(560))
        sw, sh = dlg.winfo_screenwidth(), dlg.winfo_screenheight()
        dlg.geometry("%dx%d+%d+%d" % (w, h, (sw - w) // 2, (sh - h) // 2))
        pad = ttk.Frame(dlg, padding=(20, 16))
        pad.pack(fill="both", expand=True)
        ttk.Label(pad, text="MCP 状态 · %s" % name, style="TLabel",
                  font=("Microsoft YaHei UI", 13, "bold")).pack(anchor="w")
        it = (agent or {}).get("integration") or {}
        st_txt = ("已注册" + ("（且受信）" if it.get("mcp_trusted", True) else "（**未受信**）"))
        if not it.get("mcp"):
            st_txt = "未注册"
        ttk.Label(pad, text="现状：%s" % st_txt, style="Dim.TLabel",
                  font=("Microsoft YaHei UI", 11)).pack(anchor="w", pady=(6, 0))
        foot = ttk.Frame(pad)
        foot.pack(side="bottom", fill="x", pady=(12, 0))
        body = tk.Frame(pad, bg=BG)
        body.pack(fill="both", expand=True, pady=(8, 0))
        txt = tk.Text(body, wrap="word", bg="#ffffff", fg=FG, relief="flat", bd=0,
                      highlightthickness=1, highlightbackground=LINE,
                      font=("Microsoft YaHei UI", 9), padx=12, pady=10)
        sb = ttk.Scrollbar(body, orient="vertical", command=txt.yview)
        txt.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        txt.pack(side="left", fill="both", expand=True)

        if not cfg:
            snippet = ("这家没有已知的 MCP 配置位置（可能是自定义客户端）。\n"
                       "可在它的设置里找「MCP / 连接器」，按下面的片段登记：")
            snippet_body = _json.dumps(
                {"mcpServers": {"agent-inventory": {"command": exe, "args": ["--mcp"]}}},
                ensure_ascii=False, indent=1)
            fp = ""
        else:
            fp, kind = cfg
            if key == "astrbot":
                py = os.path.join(os.environ.get("LOCALAPPDATA", ""), "AstrBot",
                                  "backend", "python", "python.exe")
                snippet = ("⚠️ **AstrBot 有启动命令白名单**（只认 python/node 之类，见它的 "
                           "`core/agent/mcp_client.py`）—— 不能直接指 exe，要这样写：")
                snippet_body = _json.dumps(
                    {"mcpServers": {"agent-inventory": {
                        "command": py,
                        "args": [os.path.join(HERE, "agent_mcp.py")]}}},
                    ensure_ascii=False, indent=1)
            elif kind == "toml":
                snippet = "Codex 用 TOML，追加这一段："
                snippet_body = ("[mcp_servers.agent-inventory]\ncommand = '%s'\n"
                                "args = [\"--mcp\"]" % exe)
            else:
                snippet = "把这个键加进它的 `mcpServers`："
                snippet_body = _json.dumps(
                    {"agent-inventory": {"command": exe, "args": ["--mcp"]}},
                    ensure_ascii=False, indent=1)
        lines = [u"配置文件：", u"  %s" % (fp or "（未知）"),
                 u"  在不在：%s" % (u"在 ✓" if fp and os.path.isfile(fp) else u"不在 ✗"),
                 u"", u"%s" % snippet, u"", snippet_body, u""]
        for nt in (it.get("notes") or []):
            lines.append(u"· " + str(nt))
        if key == "workbuddy":
            lines += [u"", u"WorkBuddy 的 MCP 有**哈希信任清单**（见它日志里的 "
                          u"[MCP Security] skipping untrusted server）——",
                      u"注册了也会被跳过。要它真正用起来，得在它的**连接器 / MCP 设置**里"
                      u"把这个服务受信/启用。",
                      u"（另一条路：不依赖 MCP —— 用「接入 Agent」把读取指引写进它人格，"
                      u"见卡片上的「人格」灯。）"]
        txt.insert("1.0", u"\n".join(lines))
        txt.configure(state="disabled")

        def open_dir():
            if fp:
                self.open_path(os.path.dirname(fp))
            else:
                self.status.configure(text=u"这家没有已知的配置位置。")
        ttk.Button(foot, text=u"打开配置所在目录", style="Tab.TButton",
                   command=open_dir).pack(side="left")
        ttk.Button(foot, text=u"复制登记片段", style="Act.TButton",
                   command=lambda: (self.clipboard_clear(),
                                    self.clipboard_append(snippet_body),
                                    self.status.configure(
                                        text=u"登记片段已复制 —— 粘进它的 MCP 配置即可。"))
                   ).pack(side="left", padx=(6, 0))
        ttk.Button(foot, text=u"去接入 Agent", style="Tab.TButton",
                   command=lambda: (dlg.destroy(), self.onboard_dialog())
                   ).pack(side="left", padx=(6, 0))
        ttk.Button(foot, text=u"关闭", style="Tab.TButton",
                   command=dlg.destroy).pack(side="right")

    def _elide_path(self, text, limit):
        """路径缩写：保留盘符 + 末两级，中间用 … 省掉。

        比单纯按长度截尾好看得多，也更容易认（`C:\\…\\Programs\\WorkBuddy`）。
        """
        t = " ".join(str(text or "").split())
        if len(t) <= limit:
            return t
        # 把「 ｜ 参与：…」这类后缀先摘掉
        for sep in (" \uFF5C ", " | "):
            if sep in t:
                t = t.split(sep)[0]
                break
        if "\\" in t or "/" in t:
            parts = [x for x in re.split(r"[\\/]+", t) if x]
            if len(parts) >= 3:
                head = parts[0] + ("\\" if "\\" in t else "/")
                tail = ("\\" if "\\" in t else "/").join(parts[-2:])
                cand = head + u"\u2026" + ("\\" if "\\" in t else "/") + tail
                if len(cand) <= limit + 6:
                    return cand
        return t[:limit - 1] + u"\u2026"

    # ---------- 窗口几何的记住与还原（第五十轮） ----------
    def _window_geo_file(self):
        d = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
                         "Agent资产总览")
        try:
            os.makedirs(d, exist_ok=True)
        except Exception:
            pass
        return os.path.join(d, "窗口.json")

    def _load_window_geo(self):
        try:
            d = json.load(open(self._window_geo_file(), "r", encoding="utf-8"))
            return d if isinstance(d, dict) else {}
        except Exception:
            return {}

    def _on_configure(self, event=None):
        """窗口尺寸/位置变了 → 防抖 1 秒后存一次（别在拖动时狂写盘）。"""
        try:
            if event is not None and event.widget is not self:
                return
            if getattr(self, "_geo_job", None):
                self.after_cancel(self._geo_job)
            self._geo_job = self.after(1000, self._save_window_geo)
        except Exception:
            pass

    def _save_window_geo(self):
        try:
            self._geo_job = None
            d = {"w": self.winfo_width(), "h": self.winfo_height(),
                 "x": self.winfo_x(), "y": self.winfo_y(),
                 "zoomed": (self.state() == "zoomed")}
            if self.state() == "zoomed":
                # 全屏态的 w/h 是**屏幕**尺寸，别当窗口尺寸记 —— 保留上次的值，
                # 只记「上次是最大化」这一事实，还原时再最大化
                old = self._load_window_geo()
                d["w"] = old.get("w") or d["w"]
                d["h"] = old.get("h") or d["h"]
            json.dump(d, open(self._window_geo_file(), "w", encoding="utf-8"),
                      ensure_ascii=False, indent=1)
        except Exception:
            pass

    def _skill_count(self):
        """技能数（**不含**整合卡里标 hidden 的成员 —— 它们只在检索里出现）。"""
        try:
            return len([x for x in [x for x in self.data.get("skills", []) if not x.get("hidden")] if not x.get("hidden")])
        except Exception:
            return 0

    def _integration_line(self, agent):
        """工作台右上角那句：这家 Agent 与应用的**接入程度**（四盏灯 + 备注）。

        第四十六轮（本版要求）：卡片上打灯，点进来这里给细账。
        """
        it = (agent or {}).get("integration") or {}
        if not it:
            return u"记录就地读取：本格的原始记录，应用直接就地读，不必搬运。"
        mcp_on = bool(it.get("mcp"))
        mcp_full = mcp_on and bool(it.get("mcp_trusted", True))

        def lamp(on, half=False):
            return u"●" if on else (u"◐" if half else u"○")
        # 只报"结果性"的三件（记录的产出 / 人格指针到位 / MCP 可用）；
        # 「登记过检索地址」是手段，不单列 —— 它体现在记录灯里（第四十八轮）
        parts = [u"接入程度（与应用数据）",
                 u"记录 %s ｜ 人格 %s ｜ MCP %s"
                 % (lamp(bool(it.get("records"))),
                    lamp(bool(it.get("persona"))),
                    lamp(mcp_full, mcp_on and not mcp_full))]
        if it.get("records"):
            parts[-1] += u"（%d 份）" % it.get("records")
        notes = it.get("notes") or []
        if notes:
            parts.append(u"· " + u"\n· ".join(notes[:3]))
        return u"\n".join(parts)

    # ---------- 检索地址（用户手工登记，让 Agent 自报家门） ----------
    def record_paths_dialog(self, agent=None):
        """某个 Agent 的【检索地址】：把 Agent 自报的目录粘进来，即纳入主动检索。"""
        try:
            mod = self._scanner_mod()
        except Exception:
            mod = None
        name = (agent or {}).get("name") or ""
        key = ((agent or {}).get("key") or name).strip().lower()
        dlg = tk.Toplevel(self)
        dlg.title("检索地址 · %s" % (name or "全部"))
        dlg.configure(bg=BG)
        dlg.transient(self)
        w, h = self._fit(_px(780), _px(580))
        sw, sh = dlg.winfo_screenwidth(), dlg.winfo_screenheight()
        dlg.geometry("%dx%d+%d+%d" % (w, h, (sw - w) // 2, (sh - h) // 2))
        pad = ttk.Frame(dlg, padding=(20, 16))
        pad.pack(fill="both", expand=True)
        ttk.Label(pad, text="检索地址 · %s" % (name or ""), style="TLabel",
                  font=("Microsoft YaHei UI", 13, "bold")).pack(anchor="w")
        ttk.Label(pad,
                  text=u"让这个 Agent 「自报家门」（问它：你的记忆都存在哪），把报出来的路径"
                       u"粘到这里 —— 那一处立刻纳入主动检索。应用会自动探测体量，"
                       u"并建议「直读」还是「摘录」。",
                  style="Hint.TLabel", font=("Microsoft YaHei UI", 9), justify="left",
                  wraplength=_px(710)).pack(anchor="w", pady=(4, 10))
        foot = ttk.Frame(pad)
        foot.pack(side="bottom", fill="x", pady=(12, 0))
        body = tk.Frame(pad, bg=BG)
        body.pack(fill="both", expand=True)
        txt = tk.Text(body, wrap=u"word", bg="#ffffff", fg=FG, relief="flat", bd=0,
                      highlightthickness=1, highlightbackground=LINE,
                      font=("Microsoft YaHei UI", 9), padx=12, pady=10)
        sb = ttk.Scrollbar(body, orient="vertical", command=txt.yview)
        txt.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        txt.pack(side="left", fill="both", expand=True)

        def refresh():
            lines = [u"【已登记的检索地址】"]
            extra = (mod.load_extra_roots() if mod else {}).get(key) or []
            if not extra:
                lines.append(u"  （还没有）—— 先把下面的问话发给它，再回来粘路径。")
            for it in extra:
                pr = mod.probe_path(it.get("path")) if mod else {}
                lines.append(u"  · " + str(it.get("path")))
                lines.append(u"      %s ｜ %s ｜ %s 个文件 ｜ %s"
                             % (it.get("name") or u"（未命名）",
                                u"直读" if (it.get("mode") or "direct") == "direct"
                                else u"摘录",
                                pr.get("files", u"?"),
                                (u"%.1f MB" % (pr.get("bytes", 0) / 1048576))
                                if pr.get("exists") else u"路径不存在 ✗"))
            lines += [u"", u"【内置记录根（本来就检索）】"]
            for r in (mod.record_roots_of(agent) if mod and agent else []):
                lines.append(u"  · %-20s %-6s %s 个文件 ｜ %s"
                             % (r["name"],
                                {"direct": u"直读", "digest": u"摘录",
                                 "skip": u"只登记", "digest_sqlite": u"摘录"}.get(
                                    r["mode"], r["mode"]),
                                r["count"], r["root"]))
            txt.configure(state=u"normal")
            txt.delete("1.0", "end")
            txt.insert("1.0", u"\n".join(lines))
            txt.configure(state=u"disabled")

        def add():
            import tkinter.filedialog as _fd
            p2 = _fd.askdirectory(title=u"选这个 Agent 记忆/记录所在目录", parent=dlg)
            if not p2:
                return
            pr = mod.probe_path(p2) if mod else {}
            mode = pr.get("suggest") or "direct"
            ok = messagebox.askyesno(
                u"加入检索",
                u"路径：%s\n\n探测：%s 个文件 ｜ %.2f MB\n主要格式：%s\n\n"
                u"建议处理：%s\n\n就按这个加入？"
                % (p2, pr.get("files", u"?"), pr.get("bytes", 0) / 1048576,
                   u"、".join(u"%s×%d" % (e, n) for e, n in (pr.get("exts") or [])[:4]),
                   u"摘录（体量大或不全是纯文本）" if mode == "digest" else u"直读"),
                parent=dlg)
            if not ok:
                return
            d2 = mod.load_extra_roots() if mod else {}
            d2.setdefault(key, []).append({
                "path": p2, "mode": mode,
                "name": os.path.basename(p2.rstrip(u"\\/")) or p2})
            mod.save_extra_roots(d2)
            mod.invalidate_roots()
            refresh()
            self.status.configure(text=u"已登记检索地址：%s（%s）" % (p2, mode))

        def remove():
            extra = (mod.load_extra_roots() if mod else {}).get(key) or []
            if not extra:
                return
            if messagebox.askyesno(u"移除", u"移除最后一条：\n%s ？"
                                   % extra[-1].get("path"), parent=dlg):
                d2 = mod.load_extra_roots()
                d2[key] = extra[:-1]
                mod.save_extra_roots(d2)
                mod.invalidate_roots()
                refresh()

        def copy_ask():
            fp2 = os.path.join(HERE, u"通用资源", u"skills", u"agent-self-report",
                               u"SKILL.md")
            body_txt = u""
            try:
                t2 = open(fp2, encoding="utf-8").read()
                i2 = t2.find(u"请只做一件事")
                body_txt = t2[i2:] if i2 > 0 else t2
            except Exception:
                body_txt = (u"请只做一件事，不要做任何别的操作：如实告诉我，你自己的"
                            u"记忆/会话记录/工作记录/产出文件都存放在哪些目录"
                            u"（绝对路径、文件数、体量、是文本还是二进制）。"
                            u"不确定就说不确定，不要编。")
            self.clipboard_clear()
            self.clipboard_append(body_txt)
            self.status.configure(text=u"「自报家门」问话已复制 —— 粘给这个 Agent 试试。")

        ttk.Button(foot, text=u"＋ 添加地址", style="Act.TButton",
                   command=add).pack(side="left")
        ttk.Button(foot, text=u"移除最后一条", style="Tab.TButton",
                   command=remove).pack(side="left", padx=(6, 0))
        ttk.Button(foot, text=u"复制自报家门问话", style="Act.TButton",
                   command=lambda: copy_ask()).pack(side="left", padx=(6, 0))
        ttk.Button(foot, text=u"关闭", style="Tab.TButton",
                   command=dlg.destroy).pack(side="right")
        refresh()

    def _scanner_mod(self):
        import importlib.util
        fp = os.path.join(HERE, "scan_agents_apps.py")
        if not os.path.isfile(fp):
            return None
        spec = importlib.util.spec_from_file_location("scanner_app", fp)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["scanner_app"] = mod
        spec.loader.exec_module(mod)
        return mod

    # 第六十轮（本版要求）：record_sources_dialog 整段删除 ——
    #   那些「记录长在哪」已经在各 Agent 的工作台里常态摊开了，主页的没用了。
    # ---------- 快捷键 ----------
    def _bind_shortcuts(self):
        """键盘也走得通：搜、扫、切栏、抄录。

        Ctrl+F 聚焦搜索　F5 重新扫描　Ctrl+1..4 切栏
        Ctrl+L 重新扫描（并顺手重建摘录缓存）　Ctrl+P 设置主要项目　Esc 清空搜索
        """
        def on(seq, fn):
            try:
                self.bind_all(seq, fn)
            except Exception:
                pass

        def focus_search(_e=None):
            try:
                self.entry.focus_set()
                self.entry.select_range(0, "end")
            except Exception:
                pass
            return "break"

        def clear_search(_e=None):
            try:
                self.var_q.set("")
            except Exception:
                pass
            return "break"

        on("<Control-f>", focus_search)
        on("<Control-F>", focus_search)
        on("<Escape>", clear_search)
        on("<F5>", lambda _e: (self.rescan(), "break")[1])
        # 第三十八轮：「手动抄录」已撤（就地索引后它只剩重建摘录缓存，
        # 那是后台十分钟一轮的事）。Ctrl+L 改为「重新扫描」，顺手也催一次摘录。
        on("<Control-l>", lambda _e: (self._autolog_now(), self.rescan(), "break")[2])
        on("<Control-p>", lambda _e: (self.projects_dialog(), "break")[1])
        for i, (key, _label) in enumerate(CATS, 1):
            on("<Control-Key-%d>" % i,
               lambda _e, k=key: (self.switch(k), "break")[1])

    # ---------- 主要项目 ----------
    def _match_tasks(self, proj):
        """把与某个项目相关的任务挑出来（名字或关键词出现在标题/正文/路径里）。"""
        keys = [str(k).strip().lower() for k in (proj.get("keys") or []) if str(k).strip()]
        if not keys and proj.get("name"):
            keys = [str(proj["name"]).strip().lower()]
        hit = []
        for t in (getattr(self, "tasks", None) or []):
            blob = " ".join([str(t.get("title") or ""), str(t.get("did") or ""),
                             " ".join(t.get("artifacts") or []),
                             str(t.get("log") or ""), str(t.get("source") or "")]).lower()
            if any(k in blob for k in keys):
                hit.append(t)
        hit.sort(key=lambda x: -x.get("mtime", 0))
        return hit

    def _project_records(self, proj):
        """某项目的**全部**相关记录。

        本版要求：「既然是主要项目，必然是经历了一个或多个 agent 接力对话打造的」——
        故不能只看任务池里那点摘要（摘要只截了 400 字，藏在长文里的就漏了），
        还要**逐字翻遍各 Agent 工作记录夹里的每一个文件**，把提到该项目的一网打尽。
        结果缓存 60 秒，免得每次重绘都翻一遍盘。
        """
        keys = [str(k).strip().lower() for k in (proj.get("keys") or []) if str(k).strip()]
        if not keys and proj.get("name"):
            keys = [str(proj["name"]).strip().lower()]
        if not keys:
            return []
        ck = (proj.get("name") or "", tuple(sorted(keys)))
        cache = getattr(self, "_proj_cache", None)
        if cache is None:
            cache = self._proj_cache = {}
        old = cache.get(ck)
        if old and (time.time() - old[0]) < 60:
            return old[1]

        out = {}
        # ① 任务池里已整理好的
        for t in (getattr(self, "tasks", None) or []):
            blob = " ".join([str(t.get("title") or ""), str(t.get("did") or ""),
                             " ".join(t.get("artifacts") or []),
                             str(t.get("log") or "")]).lower()
            if any(k in blob for k in keys):
                out[str(t.get("log") or t.get("key"))] = t
        # ② 逐字翻各家的**原生记录根**（第五十五轮：应用内那格已废）
        for a in (self.agents or []):
            if (a.get("kind") or "agent") != "agent":
                continue
            for root in self._record_roots_of(a):
                if not os.path.isdir(root):
                    continue
                for dp, dns, fns in os.walk(root):
                    if dp[len(root):].count(os.sep) > 2:
                        dns[:] = []
                        continue
                    dns[:] = [d for d in dns if d not in (".git", "__pycache__",
                                                          "node_modules", ".venv")]
                    for f in fns:
                        if os.path.splitext(f)[1].lower() not in (".md", ".txt", ".json", ".log"):
                            continue
                        fp = os.path.join(dp, f)
                        if fp in out:
                            continue
                        try:
                            if os.path.getsize(fp) > 1024 * 1024:
                                continue
                            txt = open(fp, "r", encoding="utf-8", errors="ignore").read()
                        except Exception:
                            continue
                        low = txt.lower()
                        pos = [low.find(k) for k in keys]
                        pos = [x for x in pos if x >= 0]
                        if not pos:
                            continue
                        j = min(pos)
                        try:
                            mt = os.path.getmtime(fp)
                        except Exception:
                            mt = 0
                        out[fp] = {
                            "agent": a.get("name") or "",
                            "source": "工作记录",
                            "title": f,
                            "did": "…" + " ".join(txt[max(0, j - 60): j + 260].split()) + "…",
                            "artifacts": [],
                            "log": fp,
                            "when": time.strftime("%Y-%m-%d %H:%M", time.localtime(mt)) if mt else "",
                            "mtime": int(mt),
                        }
        res = sorted(out.values(), key=lambda x: -x.get("mtime", 0))
        cache[ck] = (time.time(), res)
        return res

    def _project_features(self, proj, tasks):
        """已实现的功能：**人工清单优先**（projects.json 里的 features），
        没写就从日志里摘含「已实现/支持/新增/完成/上线」的条目。"""
        man = proj.get("features")
        if isinstance(man, list) and man:
            return [str(x) for x in man if str(x).strip()]
        keys = ("已实现", "支持", "新增", "完成", "上线", "修好", "搞定", "做好了")
        out, seen = [], set()
        for t in tasks:
            # 第四十轮：与 MCP 那份对齐 —— 跳过纯粹是噪音的来源
            #   （转写实录、会话工作区、总索引）；「会话记忆」与「笔记」是好料，放行。
            _base = os.path.basename(str(t.get("log") or ""))
            if any(k in _base for k in (u"会话实录", u"会话工作区", u"总索引",
                                        u"raw_memories")):
                continue
            txt = str(t.get("did") or "")
            try:
                txt = open(t.get("log") or "", "r", encoding="utf-8",
                           errors="ignore").read()[:8000]
            except Exception:
                pass
            for ln in txt.split("\n"):
                s2 = ln.strip()
                # 表格行、代码块、索引行都不是「功能条目」，滤掉
                if "|" in s2 or s2.startswith(("```", "    ", "\t")):
                    continue
                s2 = s2.lstrip("-·*").strip().lstrip("0123456789. ").strip()
                if 6 <= len(s2) <= 90 and any(k in s2 for k in keys) and s2 not in seen:
                    seen.add(s2)
                    out.append(s2)
                if len(out) >= 30:
                    return out
        return out

    def _edit_project_features(self, proj, after=None):
        """编「我划的重点」：一个项目一段人工清单，存进 主要项目.json 的 features。

        第六十一轮（本版要求）：主要项目＝**用户手选的整理层**，人工清单**优先于**
        自动摘录 —— 一份写好的清单，比让每个 Agent 什么都整理一遍更省、更准。
        """
        dlg = tk.Toplevel(self)
        dlg.title("我划的重点 · %s" % (proj.get("name") or ""))
        dlg.configure(bg=BG)
        dlg.transient(self)
        w, h = self._fit(_px(660), _px(470))
        sw, sh = dlg.winfo_screenwidth(), dlg.winfo_screenheight()
        dlg.geometry("%dx%d+%d+%d" % (w, h, (sw - w) // 2, (sh - h) // 2))
        pad = ttk.Frame(dlg, padding=(18, 14))
        pad.pack(fill="both", expand=True)
        ttk.Label(pad, text="我划的重点 · %s" % (proj.get("name") or ""), style="TLabel",
                  font=("Microsoft YaHei UI", 13, "bold")).pack(anchor="w")
        ttk.Label(pad, text="一行一条：这个项目做到了什么 / 关键结论 / 为什么这么定。"
                            "写在这里的会**优先于**自动摘录显示给人看，Agent 查项目时先看到的就是它。",
                  style="Hint.TLabel", font=("Microsoft YaHei UI", 9),
                  justify="left", wraplength=_px(600)).pack(anchor="w", pady=(4, 8))
        foot = ttk.Frame(pad)
        foot.pack(side="bottom", fill="x", pady=(10, 0))
        body = tk.Frame(pad, bg=BG)
        body.pack(fill="both", expand=True)
        t = tk.Text(body, wrap="word", bg="#ffffff", fg=FG, bd=0,
                    highlightthickness=1, highlightbackground=LINE, height=12,
                    font=("Microsoft YaHei UI", 10), padx=10, pady=8)
        sb = ttk.Scrollbar(body, orient="vertical", command=t.yview)
        t.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        t.pack(side="left", fill="both", expand=True)
        _cur = proj.get("features")
        if isinstance(_cur, list) and _cur:
            t.insert("1.0", "\n".join(str(x) for x in _cur))
        t.focus_set()

        def do_save():
            lines = [x.strip() for x in t.get("1.0", "end").split("\n")]
            lines = [x for x in lines if x]
            try:
                projs = load_projects()
            except Exception:
                projs = [dict(x) for x in (getattr(self, "projects", None) or [])]
            hit = None
            for x in projs:
                if (x.get("name") or "") == (proj.get("name") or ""):
                    hit = x
                    break
            if hit is None:
                hit = dict(proj)
                projs.append(hit)
            hit["features"] = lines
            try:
                save_projects(projs)
                self.projects = load_projects()
                self.status.configure(text=u"已存：%s 的重点 %d 条（人工清单优先）。"
                                           % (hit.get("name"), len(lines)))
                dlg.destroy()
                if callable(after):
                    after()
            except Exception as e:
                self.status.configure(text=u"存盘不成：%s" % e)

        ttk.Button(foot, text="保存", style="Act.TButton",
                   command=do_save).pack(side="left")
        ttk.Button(foot, text="取消", style="Tab.TButton",
                   command=dlg.destroy).pack(side="left", padx=(6, 0))
        dlg.bind("<Escape>", lambda _e: dlg.destroy())
        return dlg

    def projects_dialog(self):
        """主要项目设置：**列表式，想加几条加几条**，随时增删改。

        第三十一轮（本版要求）：从前是个多行文本框，看不出「加一条 / 删一条」该怎么办，
        且保存时会把项目备注等字段弄丢。今改列表式：每行一条，配 添加 / 编辑 / 删除。
        """
        dlg = tk.Toplevel(self)
        dlg.title("设置主要项目")
        dlg.configure(bg=BG)
        dlg.transient(self)
        w, h = self._fit(_px(720), _px(520))
        sw, sh = dlg.winfo_screenwidth(), dlg.winfo_screenheight()
        dlg.geometry("%dx%d+%d+%d" % (w, h, (sw - w) // 2, (sh - h) // 2))
        pad = ttk.Frame(dlg, padding=(20, 16))
        pad.pack(fill="both", expand=True)
        ttk.Label(pad, text="主要项目", style="TLabel",
                  font=("Microsoft YaHei UI", 13, "bold")).pack(anchor="w")
        ttk.Label(pad,
                  text="可以设多个，互不影响。只有命中该项目关键词的记录才会出现在"
                       "「主要项目」栏里；关键词留空就用项目名匹配。",
                  style="Hint.TLabel", font=("Microsoft YaHei UI", 9),
                  justify="left", wraplength=_px(660)).pack(anchor="w", pady=(4, 10))

        foot = ttk.Frame(pad)
        foot.pack(side="bottom", fill="x", pady=(12, 0))
        mid = tk.Frame(pad, bg=BG)
        mid.pack(fill="both", expand=True)

        lb = tk.Listbox(mid, selectmode="browse", activestyle="none",
                        exportselection=False, font=("Microsoft YaHei UI", 10),
                        bd=0, highlightthickness=1, highlightbackground=LINE)
        sb = ttk.Scrollbar(mid, orient="vertical", command=lb.yview)
        lb.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        lb.pack(side="left", fill="both", expand=True)

        edit = [dict(x) for x in (getattr(self, "projects", None) or [])]

        def show(keep=None):
            lb.delete(0, "end")
            for pr in edit:
                ks = "、".join(pr.get("keys") or []) or "（按项目名匹配）"
                lb.insert("end", "  %s    关键词：%s" % (pr.get("name", ""), ks))
            if keep is not None and 0 <= keep < len(edit):
                lb.selection_clear(0, "end")
                lb.selection_set(keep)
                lb.see(keep)

        def ask(initial=None):
            """弹个小窗问「项目名 + 关键词」；返回 (name, keys) 或 None。"""
            sub = tk.Toplevel(dlg)
            sub.title("项目")
            sub.configure(bg=BG)
            sub.transient(dlg)
            sw2, sh2 = sub.winfo_screenwidth(), sub.winfo_screenheight()
            sw2, sh2 = self._fit(_px(520), _px(240))
            sub.geometry("%dx%d+%d+%d" % (sw2, sh2, (sw2 - sw2) // 2, 0))
            sub.geometry("+%d+%d" % (max(0, (sub.winfo_screenwidth() - sw2) // 2),
                                     max(0, (sub.winfo_screenheight() - sh2) // 3)))
            p2 = ttk.Frame(sub, padding=(18, 14))
            p2.pack(fill="both", expand=True)
            ttk.Label(p2, text="项目名", style="TLabel",
                      font=("Microsoft YaHei UI", 10)).pack(anchor="w")
            v_nm = tk.StringVar(value=(initial or {}).get("name", ""))
            ttk.Entry(p2, textvariable=v_nm,
                      font=("Microsoft YaHei UI", 10)).pack(fill="x", ipady=4,
                                                            pady=(2, 10))
            ttk.Label(p2, text="关键词（逗号或顿号分隔，可留空）", style="Hint.TLabel",
                      font=("Microsoft YaHei UI", 9)).pack(anchor="w")
            v_ks = tk.StringVar(value="、".join((initial or {}).get("keys") or []))
            ttk.Entry(p2, textvariable=v_ks,
                      font=("Microsoft YaHei UI", 10)).pack(fill="x", ipady=4,
                                                            pady=(2, 10))
            got = {}

            def ok():
                nm = v_nm.get().strip()
                if not nm:
                    return
                got["v"] = (nm, v_ks.get())
                sub.destroy()

            b2 = ttk.Frame(p2)
            b2.pack(fill="x")
            ttk.Button(b2, text="确 定", style="Tab.TButton",
                       command=ok).pack(side="right")
            ttk.Button(b2, text="取 消", style="TButton",
                       command=sub.destroy).pack(side="right", padx=(0, 8))
            sub.grab_set()
            dlg.wait_window(sub)
            return got.get("v")

        def add():
            r = ask()
            if r:
                edit.append({"name": r[0], "keys": [x for x in
                                                    re.split(r"[、,，;；/|\s]+", r[1])
                                                    if x.strip()]})
                show(len(edit) - 1)

        def modify():
            sel = lb.curselection()
            if not sel:
                return
            k = sel[0]
            r = ask(edit[k])
            if r:
                edit[k] = dict(edit[k])
                edit[k]["name"] = r[0]
                edit[k]["keys"] = [x for x in re.split(r"[、,，;；/|\s]+", r[1])
                                   if x.strip()]
                show(k)

        def remove():
            sel = lb.curselection()
            if not sel:
                return
            k = sel[0]
            if self._ask_yes_no("删除主要项目",
                                "确定不再关注「%s」？\n（只删设置，不动任何记录文件）"
                                % edit[k].get("name")):
                del edit[k]
                show(min(k, max(0, len(edit) - 1)))

        def do_save():
            self.projects = [dict(x) for x in edit]
            self._proj_cache = {}
            msgs = []
            for pr in self.projects:
                hit = self._project_records(pr)
                who = []
                for t in hit:
                    if t.get("agent") and t["agent"] not in who:
                        who.append(t["agent"])
                msgs.append("「%s」%d 条（%s）" % (pr.get("name"), len(hit),
                                                "、".join(who) or "暂无"))
            if save_projects(self.projects):
                self.status.configure(text="主要项目已保存：%s" % "；".join(msgs)
                                      if msgs else "主要项目已清空。")
            else:
                self.status.configure(text="主要项目已改，但存盘不成（重启会丢）。")
            self.refresh()
            dlg.destroy()

        ttk.Button(foot, text="＋ 添加", style="Tab.TButton",
                   command=add).pack(side="left")
        ttk.Button(foot, text="编 辑", style="Tab.TButton",
                   command=modify).pack(side="left", padx=(6, 0))
        ttk.Button(foot, text="删 除", style="Tab.TButton",
                   command=remove).pack(side="left", padx=(6, 0))
        ttk.Button(foot, text="保 存", style="Tab.TButton",
                   command=do_save).pack(side="right")
        ttk.Button(foot, text="关 闭", style="Tab.TButton",
                   command=dlg.destroy).pack(side="right", padx=(0, 8))
        lb.bind("<Double-Button-1>", lambda _e: modify())
        show(0)
        dlg.bind("<Escape>", lambda _e: dlg.destroy())

    def _ask_yes_no(self, title, text):
        """一个简单的是/否确认（免得 messagebox 的样式与全局不搭）。"""
        try:
            return messagebox.askyesno(title, text, parent=self)
        except Exception:
            return False

    # ---------- 自动抄录各 Agent 的日志 ----------
    def _autolog_now(self):
        """「抄录日志」：立刻抄一轮（与十分钟一次的自动抄录同一条路）。"""
        if getattr(self, "_autolog_busy", False):
            self.status.configure(text="正在抄录，稍候…")
            return
        self._autolog_busy = True
        self._autolog_asked = True
        self.status.configure(text="正在抄录各 Agent 的日志…")
        threading.Thread(target=self._auto_log_work, daemon=True).start()

    def _auto_log_tick(self):
        """每 10 分钟抄一轮（后台线程干活，主线程只管刷新）。"""
        try:
            if not getattr(self, "_autolog_busy", False):
                self._autolog_busy = True
                threading.Thread(target=self._auto_log_work, daemon=True).start()
        except Exception:
            pass
        try:
            self.after(600000, self._auto_log_tick)      # 10 分钟后再来
        except Exception:
            pass

    def _auto_log_work(self):
        """后台：调采集器里的 sync_agent_logs；结果只往信箱里放。"""
        n = d = 0
        who = []
        try:
            amod = _import_app_scanner()
            if amod is not None:
                n, d, who = amod.sync_agent_logs(self.agents or [])
        except Exception:
            pass
        try:
            self._autolog_box.put((n, d, who))
        except Exception:
            pass

    def _poll_autolog(self):
        """主线程取件：抄着了就重扫刷新，名册与工作台立刻跟上。"""
        try:
            while True:
                n, d, who = self._autolog_box.get_nowait()
                self._autolog_busy = False
                if n or d:
                    self.status.configure(
                        text="自动抄录：抄入 %d 个文件、%d 篇摘录（%s）。正在刷新名册…"
                             % (n, d, "、".join(who) or "各 Agent"))
                    self.rescan()
                elif getattr(self, "_autolog_asked", False):
                    self._autolog_asked = False
                    self.status.configure(text="自动抄录：暂无新日志。")
        except queue.Empty:
            pass
        except Exception:
            pass
        try:
            self.after(1200, self._poll_autolog)
        except Exception:
            pass

    # ---------- 全局搜索（不分栏） ----------
    CAT_LABEL = {"agents": "Agents", "skills": "技能", "mcp": "MCP 服务"}

    def collect_global(self):
        """各栏都收一遍，返回 {栏: [行,…]}（只留真有命中的栏）。

        实现取巧但可靠：借用现成的 collect() —— 它按 self.cat 组装行、
        并已带搜索词的过滤，故临时换一下 cat 即可，不必重写组装逻辑。
        """
        keep = self.cat
        out = {}
        try:
            for c in ("agents", "skills", "mcp"):
                self.cat = c
                rows = self.collect()
                if rows:
                    out[c] = rows
        finally:
            self.cat = keep
        return out

    def _lay_cards(self, rows, is_agents, start_row=0, span_all=False):
        """把一批行铺成卡片网格，返回下一个可用的 row 号（网格用，勿混 pack）。"""
        LIMIT_DESC, LIMIT_META = 150, 120
        cw = self.canvas.winfo_width()
        cols = self._cols_for(cw)
        if GlassCard is None:
            cols = max(1, cw // _px(340)) if cw > 1 else 3
        self._setup_columns(cols)
        card_size = self._card_size(cw, cols)
        for i, r in enumerate(rows):
            grow = start_row + (i // cols) * 2
            if GlassCard is not None:
                if is_agents:
                    liquid = self._agent_color(r)
                elif r.get("_members"):
                    liquid = SUITE_COLOR            # 整合卡：金
                else:
                    liquid = cat_liquid(r.get("_cat") or "skills", i)
                cell = GlassCard(self.inner, liquid=liquid,
                                 size=card_size, fill_ratio=0.58)
                self._fill_glass(cell, r, is_agents, LIMIT_DESC, LIMIT_META)
            else:
                cell = self._plain_card(r, is_agents, LIMIT_DESC, LIMIT_META)
            cell.grid(row=grow, column=i % cols, sticky="nsew",
                      padx=SP_2, pady=(0, 0))
        nrow = (len(rows) + cols - 1) // cols
        return start_row + max(1, nrow) * 2

    def _grid_title(self, text, sub, row):
        """搜索视图里的分栏小标题（grid，跨全部列）。"""
        box = tk.Frame(self.inner, bg=BG)
        box.grid(row=row, column=0, columnspan=6, sticky="ew", padx=2, pady=(18, 0))
        tk.Label(box, text=text, bg=BG, fg=FG,
                 font=("Microsoft YaHei UI", 12, "bold")).pack(side="left")
        if sub:
            tk.Label(box, text=" " + sub, bg=BG, fg=FAINT,
                     font=F_HINT).pack(side="left", padx=(6, 0))
        return row + 1

    def render_search(self, q):
        """全局搜索视图：不分栏，凡是命中的都摊出来，只按栏分组标明出处。"""
        for w in self.inner.winfo_children():
            w.destroy()
        groups = self.collect_global()
        hits = (getattr(self, "_hist_hits", None) or {}).get(q)
        self.rows = [r for rs in groups.values() for r in rs]

        if not groups and not hits and hits is not None:
            tk.Label(self.inner, text="没有相符的条目", bg=BG, fg=FG,
                     font=("Microsoft YaHei UI", 13, "bold")).grid(
                row=0, column=0, columnspan=6, pady=(60, 6))
            tk.Label(self.inner, text="没有与「%s」相符的条目，换个词试试。" % q,
                     bg=BG, fg=FAINT, justify="center",
                     font=("Microsoft YaHei UI", 10)).grid(row=1, column=0,
                                                           columnspan=6, pady=(0, 26))
            self.status.configure(text="共 0 项。")
            self.canvas.yview_moveto(0)
            return

        row = 0
        for cat in ("agents", "skills", "mcp"):
            rows = groups.get(cat)
            if not rows:
                continue
            for r in rows:
                r["_cat"] = cat
            row = self._grid_title("【%s】" % self.CAT_LABEL[cat],
                                   "%d 条" % len(rows), row)
            row = self._lay_cards(rows, cat == "agents", row)
        if hits:
            row = self._grid_title("【工作记录】", "%d 条命中" % len(hits), row)
            row = self._grid_hits(hits, row)
        elif hits is None and q:
            tk.Label(self.inner, text="正在翻各 Agent 的工作记录…", bg=BG, fg=FAINT,
                     font=F_HINT).grid(row=row, column=0, columnspan=6,
                                       sticky="w", pady=(18, 0))
            row += 1
        tk.Label(self.inner, text="（清空搜索框即回到分栏浏览）", bg=BG, fg=FAINT,
                 font=F_HINT).grid(row=row, column=0, columnspan=6,
                                   sticky="w", pady=(16, 20))
        parts = ["%s %d" % (self.CAT_LABEL[c], len(groups[c])) for c in groups]
        if hits:
            parts.append("工作记录 %d" % len(hits))
        self.status.configure(text="全局搜索「%s」：" % q + " · ".join(parts) if parts
                              else "全局搜索「%s」：无" % q)
        self.canvas.yview_moveto(0)

    def _grid_hits(self, hits, start_row):
        """按网格铺「工作记录命中」（与卡片同一容器，不能混 pack）。"""
        row = start_row
        for h in hits[:12]:
            box = tk.Frame(self.inner, bg=CARD, highlightthickness=1,
                           highlightbackground=LINE)
            box.grid(row=row, column=0, columnspan=6, sticky="ew", padx=2, pady=(8, 0))
            pad = tk.Frame(box, bg=CARD)
            pad.pack(fill="x", padx=12, pady=8)
            _where = ("（第 %d 行）" % h["line"]) if h.get("line") else "（文件名命中）"
            tk.Label(pad, text="%s · %s%s" % (h["agent"], h["file"], _where), bg=CARD,
                     fg=FG, anchor="w",
                     font=("Microsoft YaHei UI", 10, "bold")).pack(fill="x")
            tk.Label(pad, text=h["snippet"] or "（该行无可摘要内容）", bg=CARD, fg=DIM,
                     anchor="w", justify="left", wraplength=_px(820),
                     font=("Microsoft YaHei UI", 9)).pack(fill="x", pady=(3, 0))
            tk.Label(pad, text=h["path"], bg=CARD, fg=FAINT, anchor="w",
                     font=F_MONO).pack(fill="x", pady=(3, 0))
            for w in (box, pad, *pad.winfo_children()):
                try:
                    w.configure(cursor="hand2")
                    w.bind("<Button-1>", lambda _e, p=h["path"]: self.open_file(p))
                except Exception:
                    pass
            row += 1
        if len(hits) > 12:
            tk.Label(self.inner, text="（只显示前 12 条，共 %d 条）" % len(hits),
                     bg=BG, fg=FAINT, font=F_HINT).grid(row=row, column=0,
                                                        columnspan=6, sticky="w")
            row += 1
        return row

    # ---------- 工作记录全库检索 ----------
    def _on_query_change(self):
        """输入框一变：先重绘，再安排一次（防抖的）全库检索。"""
        try:
            self.render()
            self._kick_hist_search(self.var_q.get().strip().lower())
        except Exception:
            pass

    def _kick_hist_search(self, q):
        """防抖：停手 250ms 才真去翻，免得每敲一个键就全盘扫一遍。"""
        self._hist_q = q
        try:
            if getattr(self, "_hist_timer", None):
                self.after_cancel(self._hist_timer)
        except Exception:
            pass
        try:
            self._hist_timer = self.after(250, lambda: self._hist_work(q))
        except Exception:
            self._hist_timer = None

    def _hist_work(self, q):
        """开一条后台线程去扫；扫完塞进队列，由主线程取。"""
        if len(q) < 2:
            self._hist_hits = {}
            self.render()
            return
        if q in (getattr(self, "_hist_hits", None) or {}):
            self.render()                     # 已经搜过，直接用缓存
            return

        def job():
            try:
                self._hist_queue.put((q, self._scan_history(q)))
            except Exception:
                self._hist_queue.put((q, []))

        threading.Thread(target=job, daemon=True).start()

    def _scan_history(self, q):
        """翻遍**每个 Agent 的原生记录根**，找含该关键字词的文件与行号。

        第五十五轮（本版要求）：应用内那格工作记录夹已废 —— 改读各家原生位置
        （就地索引的来源），一条都不复制。只认文本类扩展名、单文件 ≤3 MB。
        """
        hits = []
        for a in (self.agents or []):
            if (a.get("kind") or "agent") != "agent":
                continue
            for d in self._record_roots_of(a):
                if not os.path.isdir(d):
                    continue
                for dp, dns, fns in os.walk(d):
                    dns[:] = [x for x in dns if x not in HIST_SKIP_DIRS]
                    for f in fns:
                        if os.path.splitext(f)[1].lower() not in HIST_EXTS:
                            continue
                        fp = os.path.join(dp, f)
                        try:
                            if os.path.getsize(fp) > HIST_MAX_BYTES:
                                continue
                            txt = open(fp, "r", encoding="utf-8", errors="ignore").read()
                        except Exception:
                            continue
                        low = txt.lower()
                        i = low.find(q)
                        if i < 0:
                            if q in f.lower():
                                hits.append({"agent": a.get("name", ""), "file": f,
                                             "path": fp, "line": 0,
                                             "snippet": "（文件名命中）" + f})
                            continue
                        line = txt.count("\n", 0, i) + 1
                        snip = " ".join(txt[max(0, i - 70): i + 100].split())
                        hits.append({"agent": a.get("name", ""), "file": f, "path": fp,
                                     "line": line, "snippet": snip})
                        if len(hits) >= 60:
                            return hits
        return hits

    def _poll_hist(self):
        """主线程取后台结果（子线程一律不碰 tk）。"""
        try:
            while True:
                q, hits = self._hist_queue.get_nowait()
                self._hist_hits = {q: hits}
                if q == self.var_q.get().strip().lower():
                    self.render()
        except queue.Empty:
            pass
        except Exception:
            pass
        try:
            self.after(120, self._poll_hist)
        except Exception:
            pass

    def _render_hist_hits(self, q):
        """把「工作记录命中」铺在卡片下面 —— 一看便知这个项目出自谁手。"""
        if not q or len(q) < 2:
            return
        hits = (getattr(self, "_hist_hits", None) or {}).get(q)
        if hits is None:
            tk.Label(self.inner, text="正在翻各 Agent 的工作记录…", bg=BG, fg=FAINT,
                     font=F_HINT).pack(anchor="w", pady=(18, 0))
            return
        if not hits:
            return
        tk.Label(self.inner, text="工作记录命中 · %d 条" % len(hits), bg=BG, fg=INFO,
                 font=("Microsoft YaHei UI", 11, "bold")).pack(anchor="w", pady=(22, 6))
        for h in hits[:12]:
            box = tk.Frame(self.inner, bg=CARD, highlightthickness=1,
                           highlightbackground=LINE)
            box.pack(fill="x", pady=(0, 6))
            pad = tk.Frame(box, bg=CARD)
            pad.pack(fill="x", padx=12, pady=8)
            _where = ("（第 %d 行）" % h["line"]) if h.get("line") else "（文件名命中）"
            tk.Label(pad, text="%s · %s%s" % (h["agent"], h["file"], _where),
                     bg=CARD, fg=FG, anchor="w",
                     font=("Microsoft YaHei UI", 10, "bold")).pack(fill="x")
            tk.Label(pad, text=h["snippet"] or "（该行无可摘要内容）", bg=CARD, fg=DIM,
                     anchor="w", justify="left", wraplength=_px(820),
                     font=("Microsoft YaHei UI", 9)).pack(fill="x", pady=(3, 0))
            tk.Label(pad, text=h["path"], bg=CARD, fg=FAINT, anchor="w",
                     font=F_MONO).pack(fill="x", pady=(3, 0))
            for w in (box, pad, *pad.winfo_children()):
                try:
                    w.configure(cursor="hand2")
                    w.bind("<Button-1>", lambda _e, p=h["path"]: self.open_file(p))
                except Exception:
                    pass
        if len(hits) > 12:
            tk.Label(self.inner, text="（只显示前 12 条，共 %d 条；换个更具体的词更准）"
                     % len(hits), bg=BG, fg=FAINT, font=F_HINT).pack(anchor="w", pady=(2, 8))

    # ---------- 鼠标悬停说明框 ----------
    def _tip_for(self, widget):
        """按按钮上的字查说明；查不到返回空串（宁可不弹，也不乱弹）。"""
        try:
            raw = str(widget.cget("text"))
        except Exception:
            return ""
        key = raw.replace(" ", "").replace("\u3000", "").strip()
        if not key:
            return ""
        if key in TIP_TEXT:
            return TIP_TEXT[key]
        for k, v in TIP_TEXT.items():        # 前缀放宽：「启动 WorkBuddy」这类
            if len(k) >= 2 and key.startswith(k):
                return v
        return ""

    def _tips_on_class(self):
        """按控件类**给所有 ttk.Button 挂悬停说明。

        这是「以后新加的也要有」的正解：类级绑定是整解释器一份，
        此后任何窗里新造的 ttk.Button 都自动受管，不必再逐个手挂。
        用 add="+" 追加，**不覆盖** ttk 自带的悬停类绑定（覆盖会把按钮的
        鼠标反馈弄没）。
        """
        try:
            self.bind_class("TButton", "<Enter>", self._tip_enter_any, "+")
            self.bind_class("TButton", "<Leave>", lambda _e: self._tip_hide(), "+")
            self.bind_class("TButton", "<Button-1>", lambda _e: self._tip_hide(), "+")
        except Exception:
            pass

    def _tip_enter_any(self, event):
        """类级 Enter：按这枚按钮自己的文字决定弹什么。"""
        try:
            txt = self._tip_for(event.widget)
            if txt:
                self._tip_show(event.widget, txt)
        except Exception:
            pass

    def _tip(self, widget, text, side="top"):
        """给一枚控件挂上「悬停即弹一句话」的说明。

        第六十二轮（本版要求）加 `side`：默认仍在**正上方**（谁也不动），
        传 "left" 则贴在控件**左侧**纵中处 —— 首页那张配图要的就是左边。
        """
        if not text:
            return
        try:
            widget.bind("<Enter>",
                        lambda _e, w=widget, t=text, s=side: self._tip_show(w, t, s))
            widget.bind("<Leave>", lambda _e: self._tip_hide())
            widget.bind("<Button-1>", lambda _e: self._tip_hide())
        except Exception:
            pass

    def _tip_show(self, widget, text, side="top"):
        """在该控件**正上方**（或按 `side` 指定的方向）弹一枚黑底白字的小牌子。

        位置取自控件自己的屏幕坐标（不是鼠标位置）；顶上放不下就翻到控件下方。
        与 `_busy_dialog` 同法：`overrideredirect(True)` 单独用没事，
        但**不可再叠 `-topmost`** —— 两者并用时窗建了却不显影（踩过）。
        故此处只 overrideredirect + transient + deiconify + lift。
        """
        TIP_BG = "#1c1c1e"          # 近黑
        TIP_FG = "#ffffff"          # 白字
        try:
            win = getattr(self, "_tip_win", None)
            if win is None or not win.winfo_exists():
                win = tk.Toplevel(self)
                win.overrideredirect(True)
                win.configure(bg="#000000")          # 1px 黑边，勾出轮廓
                try:
                    win.transient(self)
                except Exception:
                    pass
                box = tk.Frame(win, bg=TIP_BG)
                box.pack(fill="both", expand=True, padx=1, pady=1)
                # wraplength：文案长了就折行，别横着拉出一条长条
                lbl = tk.Label(box, text="", bg=TIP_BG, fg=TIP_FG, font=F_HINT,
                               justify="left", wraplength=_px(430))
                lbl.pack(padx=12, pady=(7, 8))
                win._box, win._lbl = box, lbl
                self._tip_win = win
            win._lbl.configure(text=text)
            win.update_idletasks()
            w, h = win.winfo_reqwidth(), win.winfo_reqheight()
            sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
            # 贴着该控件的正上方居中；顶到屏幕边就翻到控件下方
            wx, wy = widget.winfo_rootx(), widget.winfo_rooty()
            ww, wh = widget.winfo_width(), widget.winfo_height()
            if side == "left":
                # 贴在控件**左侧**、纵向居中；左边实在放不下才翻到右侧
                x = wx - w - _px(8)
                y = wy + (wh - h) // 2
                if x < 4:
                    x = wx + ww + _px(8)
            else:
                x = wx + (ww - w) // 2
                y = wy - h - _px(8)
                if y < 4:
                    y = wy + wh + _px(8)
            x = max(4, min(x, sw - w - 4))
            y = max(4, min(y, sh - h - 4))
            win.geometry("+%d+%d" % (x, y))
            win.deiconify()
            win.lift()
        except Exception:
            pass

    def _tip_hide(self):
        """收起说明框（withdraw 即可，留着下次复用，免得反复建窗闪）。"""
        try:
            win = getattr(self, "_tip_win", None)
            if win is not None and win.winfo_exists():
                win.withdraw()
        except Exception:
            pass

    # ---------- 工具条（窄窗可横向滚） ----------
    def _build_bar(self):
        outer = ttk.Frame(self, padding=(PAGE_X, 0, PAGE_X, 0))
        outer.pack(fill="x")
        self._build_metrics(outer)

        # 第十五轮（本版要求）：一排页签加搜索框再挂四枚按钮，窗口一窄，
        # 右边那几枚就被裁在窗外 —— 「重新扫描」看得见名字点不着。
        # 此处把整条塞进横向 Canvas：装不下就露一条横向滚动条，滑轮也能滚，
        # 非全屏照样点得到；装得下时滚动条自动收起，观感与从前无异。
        holder = ttk.Frame(outer)
        holder.pack(fill="x", pady=(0, SP_2))
        self.bar_holder = holder

        self.bar_canvas = tk.Canvas(holder, bg=BG, highlightthickness=0, bd=0,
                                    takefocus=0, xscrollincrement=18)
        self.bar_hsb = ttk.Scrollbar(holder, orient="horizontal",
                                     command=self.bar_canvas.xview)
        self.bar_canvas.configure(xscrollcommand=self.bar_hsb.set)
        self.bar_canvas.pack(fill="x", expand=True)

        bar = ttk.Frame(self.bar_canvas)
        self.bar = bar
        self.bar_win = self.bar_canvas.create_window((0, 0), window=bar,
                                                     anchor="nw")
        bar.bind("<Configure>", self._bar_sync_region)
        self.bar_canvas.bind("<Configure>", self._bar_on_resize)

        self.tab_btns = {}
        for key, label in CATS:
            b = ttk.Button(bar, text=label, style="Nav.TButton",
                           command=lambda k=key: self.switch(k))
            b.pack(side="left", padx=(0, SP_1))
            self.tab_btns[key] = b

        self.var_q = tk.StringVar()
        # 敲字即重绘 + 顺手踢一次「工作记录全库检索」（带防抖，见 _kick_hist_search）
        self.var_q.trace_add("write", lambda *_: self._on_query_change())
        # width 只影响「自然宽度」—— 它本就 fill="x" 铺开，用不着靠自然宽度撑；
        # 定成 14 字是**留给窄窗的余量**：自然宽度若按默认 20 字算，一排页签加
        # 搜索框加「重新扫描」会多出十几个像素，工具条就判定「装不下」而挂出
        # 一根横向滚动条（其实只差一点点）。
        ent = ttk.Entry(bar, textvariable=self.var_q, style="Search.TEntry",
                        font=("Microsoft YaHei UI", 11), width=14)
        ent.pack(side="left", fill="x", expand=True, padx=(SP_3, SP_2))
        self.entry = ent
        # 搜索框不是按钮，类级接管管不着，仍单独挂（它没有「文字」可查表）
        self._tip(ent, "输入关键字：筛当前栏，并翻遍各 Agent 的工作记录 —— 查得出某个项目出自谁手")

        # 主条上只留「搜」与「扫」—— 这两件跟哪一栏都有关。
        self.btn_rescan = ttk.Button(bar, text="重新扫描", style="Tab.TButton",
                                     command=self.rescan)
        self.btn_rescan.pack(side="left", padx=(6, 0))

        self._bar_sync_region()

        # ---------- 第二行：当前栏自己的动作（跟着页签走） ----------
        # 第三十轮（本版要求）：主界面按钮太多，按栏目归类 ——
        #   Agents      → ＋ 添加 Agent（接入 Agent、记录来源 都已移进工作台）
        #   技能        → ＋ 导入 Skill
        #   主要项目    → 设置主要项目
        # 于是这一行会随页签整体变化，主条也终于不再挤成一团。
        self.act_bar = ttk.Frame(outer)
        self.act_bar.pack(fill="x", pady=(0, SP_4))

        # 栏内动作：一律用**实心近黑**的主操作样式（Act），
        # 与上一行的描边页签/「重新扫描」拉开「实心 vs 描边」的层级差。
        # 第四十九轮（美化）：一行里只留**一个**主操作（实心）——
        #   先前「＋ 添加 Agent」与「接入 Agent」都是实心，谁都不是主
        self.btn_add = ttk.Button(self.act_bar, text="＋ 添加 Agent",
                                  style="Tab.TButton",
                                  command=self.add_agent_dialog)
        # 第六十轮（本版要求）：「记录来源」撤除 —— 内容已在各 Agent 的工作台
        # 第五十九轮（本版要求）：「接入 Agent」搬进各 Agent 的工作台，本栏不再放
        self.btn_skill = ttk.Button(self.act_bar, text="＋ 导入 Skill",
                                    style="Act.TButton",
                                    command=self.import_skill_dialog)
        self.btn_proj = ttk.Button(self.act_bar, text="设置主要项目",
                                   style="Act.TButton",
                                   command=self.projects_dialog)
        self._refresh_actions()

    ACTIONS = {
        "agents": ("btn_add",),                 # 接入 Agent / 记录来源 都已移入工作台
        "skills": ("btn_skill",),
        "tasks": ("btn_proj",),
    }

    def _refresh_actions(self):
        """按当前页签换第二行的动作按钮（谁家的活谁家管）。

        第三十二轮：这一行与上面那行要**看得出是一家的两件事** ——
        故加一条超淡分隔线、一枚 eyebrow 小字抬头（「<栏名> · 本栏动作」），
        按钮本身走实心主操作样式（Act），与描边的页签/「重新扫描」层级分明。
        """
        try:
            for w in self.act_bar.winfo_children():
                w.pack_forget()
                if getattr(w, "_sep", False) or getattr(w, "_eyebrow", False):
                    w.destroy()
            names = self.ACTIONS.get(self.cat, ())
            # 抬头：小号、宽字距的栏名（Eyebrow 规范）
            lbl = ttk.Label(self.act_bar,
                            text=(self.CAT_LABEL.get(self.cat, self.cat).upper()
                                  + " · 本栏动作"),
                            style="Eyebrow.TLabel")
            lbl._eyebrow = True
            lbl.pack(side="left", padx=(0, 10))
            for nm in names:
                b = getattr(self, nm, None)
                if b is not None:
                    b.pack(side="left", padx=(0, 6))
            if not names:
                empty = tk.Label(self.act_bar, text="本栏没有额外动作", bg=BG,
                                 fg=FAINT, font=F_HINT)
                empty._eyebrow = True
                empty.pack(side="left")
            elif "btn_logs" in names:
                # 快捷键提示只挂在需要的栏上
                cap = ttk.Label(self.act_bar, text="抄录快捷键", style="Eyebrow.TLabel")
                cap._eyebrow = True
                cap.pack(side="right", padx=(0, 6))
                kb = ttk.Label(self.act_bar, text="Ctrl+L", style="Kbd.TLabel")
                kb._eyebrow = True
                kb.pack(side="right")
        except Exception:
            pass

    # ---------- 工具条：横向滚动 ----------
    def _bar_sync_region(self, _e=None):
        """内条尺寸一变就重算可视区，并把 Canvas 高度贴到内容高度。"""
        c = getattr(self, "bar_canvas", None)
        bar = getattr(self, "bar", None)
        if c is None or bar is None or not c.winfo_exists():
            return
        try:
            h = bar.winfo_reqheight()
            if int(c.cget("height")) != h:
                c.configure(height=h)
        except Exception:
            pass
        try:
            c.configure(scrollregion=c.bbox("all"))
        except Exception:
            pass
        self._bar_update_scroll()

    def _bar_on_resize(self, e):
        """窗宽一变就跟着调：装得下就铺满（搜索框照旧横向铺开），装不下就留原宽给滚。"""
        c = getattr(self, "bar_canvas", None)
        bar = getattr(self, "bar", None)
        if c is None or bar is None or not c.winfo_exists():
            return
        try:
            need = bar.winfo_reqwidth()
            c.itemconfigure(self.bar_win, width=max(e.width, need))
            c.configure(scrollregion=c.bbox("all"))
        except Exception:
            pass
        self._bar_update_scroll()

    def _bar_overflow(self):
        """内容比可视区宽？宽了才需要滚。"""
        c = getattr(self, "bar_canvas", None)
        bar = getattr(self, "bar", None)
        if c is None or bar is None:
            return False
        try:
            if not c.winfo_exists():
                return False
            w = c.winfo_width()
            if w <= 1:                      # 还没轮到布局，用外框的宽度顶上
                w = getattr(self, "bar_holder", c).winfo_width()
            return bar.winfo_reqwidth() > w + 1
        except Exception:
            return False

    def _bar_update_scroll(self, _retry=True):
        """装得下就把滚动条收起，装不下才露出来 —— 免得白占一行。

        2026-09-20（UI 美化）：补一次**延迟复判**。启动的那一瞬，工具条与画布
        都还没量出真实宽度（winfo_width 报 1），此时算出来的「装不下」是假的，
        滚动条就这么被挂上去了 —— 而 <Configure> 只来一次，于是那根多余的灰条
        一直挂在那儿，静静占掉一行。今隔一拍再判一次，判完即止（只补一次，
        不会自己叫自己）。
        """
        c = getattr(self, "bar_canvas", None)
        hsb = getattr(self, "bar_hsb", None)
        if c is None or hsb is None or not c.winfo_exists():
            return
        if getattr(self, "_bar_sb_busy", False):
            return
        self._bar_sb_busy = True
        try:
            if self._bar_overflow():
                if not hsb.winfo_ismapped():
                    hsb.pack(fill="x")
            else:
                if hsb.winfo_ismapped():
                    hsb.pack_forget()
            c.xview_moveto(0)
        except Exception:
            pass
        finally:
            self._bar_sb_busy = False
        if _retry:
            try:
                self.after(160, lambda: self._bar_update_scroll(False))
            except Exception:
                pass

    def _pointer_in_bar(self):
        """指针此刻是否压在工具条上。"""
        holder = getattr(self, "bar_holder", None)
        if holder is None:
            return False
        try:
            if not holder.winfo_exists():
                return False
            x, y = self.winfo_pointerxy()
            w = self.winfo_containing(x, y)
            while w is not None:
                if w is holder:
                    return True
                w = getattr(w, "master", None)
        except Exception:
            return False
        return False

    def _bar_scroll(self, units):
        """横向滚工具条；units 为正即左移（与滑轮 delta 同向）。"""
        c = getattr(self, "bar_canvas", None)
        if c is None:
            return
        try:
            if not c.winfo_exists():
                return
            n = int(units)
            if n == 0:
                n = 1 if units > 0 else -1
            c.xview_scroll(n, "units")
        except Exception:
            pass

    # ---------- 栏目显隐：空栏目连页签一并隐去 ----------
    def _cat_has_content(self, key):
        """这一栏有没有真东西？

        本版要求（第十四轮）：「空栏目连着页签一起隐去」。
        · Agents —— **常驻**，一台没有也留着（本版要求：空栏里写提示
          「未检测到 agent，请重新扫描或手动导入」）；本工具自身自第十八轮起
          不再上榜，所以空栏时确实可能是真的空；
        · 其余四栏 —— 清单里对应的条数。
        """
        try:
            if key == "agents":
                # 常驻 —— 空也留栏，栏内自有提示
                return True
            if key == "tasks":
                # 「主要项目」栏：**没设过项目也要留着** —— 栏内写明怎么设
                return True
            return len((self.data or {}).get(key) or []) > 0
        except Exception:
            return True

    def _refresh_tabs(self):
        """按内容显隐页签；并保证当前页签仍有效。"""
        try:
            for key, btn in getattr(self, "tab_btns", {}).items():
                if self._cat_has_content(key):
                    if not btn.winfo_ismapped():
                        btn.pack(side="left", padx=(0, SP_1))
                else:
                    btn.pack_forget()
            # 当前页签若已被隐去，退到第一个尚存的
            if not self._cat_has_content(self.cat):
                for key, _b in CATS:
                    if self._cat_has_content(key):
                        self.cat = key
                        break
            # 更新页签样式（当前项高亮）
            for key, btn in getattr(self, "tab_btns", {}).items():
                btn.configure(style="NavOn.TButton" if key == self.cat
                              else "Nav.TButton")
            # 页签增减会改内条宽度，滚动区跟着重算
            self._bar_sync_region()
            # 当前页签若被隐去，cat 会退到别处 —— 动作行也得跟着换
            self._refresh_actions()
        except Exception:
            pass

    # ---------- 简介：可编辑 ----------
    def _edit_intro(self, key, name, on_saved=None):
        """编辑某个 Agent 的简介。

        存的是自订档 `agent_intros.json`（按 key 索引），**不写 agents.json** ——
        后者每次重扫都会被扫描器整体重写，写在那儿必丢。
        """
        if not key:
            self.status.configure(text="这一项没有 key，改不了简介。")
            return
        dlg = tk.Toplevel(self)
        dlg.title("编辑简介")
        dlg.configure(bg=BG)
        dlg.transient(self)
        w, h = self._fit(_px(660), _px(430))
        sw, sh = dlg.winfo_screenwidth(), dlg.winfo_screenheight()
        dlg.geometry("%dx%d+%d+%d" % (w, h, (sw - w) // 2, (sh - h) // 2))

        pad = ttk.Frame(dlg, padding=(20, 16))
        pad.pack(fill="both", expand=True)
        ttk.Label(pad, text="简介 · %s" % name, style="TLabel",
                  font=("Microsoft YaHei UI", 13, "bold")).pack(anchor="w")
        ttk.Label(pad, text="这段字会印在该 Agent 的卡片上。留空即恢复成扫描到的说明。",
                  style="Hint.TLabel", font=("Microsoft YaHei UI", 9)).pack(anchor="w", pady=(4, 10))

        # 底栏与提示先「占住底部」，正文再去撑剩余空间 ——
        # 反过来写，正文会把它们挤到窗外，保存按钮就看不见了（本轮修的正是这个）。
        foot = ttk.Frame(pad)
        foot.pack(side="bottom", fill="x", pady=(8, 0))
        msg = ttk.Label(pad, text="", style="Hint.TLabel",
                        font=("Microsoft YaHei UI", 9))
        msg.pack(side="bottom", anchor="w", pady=(8, 0))

        body = tk.Frame(pad, bg=BG)
        body.pack(fill="both", expand=True)
        # Text 的自然高度必须显式给：默认请求 24 行，够把整扇窗吃光。
        txt = tk.Text(body, wrap="word", bg="#ffffff", fg=FG, relief="flat", bd=0,
                      highlightthickness=1, highlightbackground=LINE, height=8,
                      font=("Microsoft YaHei UI", 10), padx=12, pady=10)
        sb = ttk.Scrollbar(body, orient="vertical", command=txt.yview)
        txt.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        txt.pack(side="left", fill="both", expand=True)
        txt.insert("1.0", self.intros.get(key, ""))
        txt.focus_set()

        def do_save():
            t = txt.get("1.0", "end").strip()
            if t:
                self.intros[key] = t
            else:
                self.intros.pop(key, None)
            if save_intros(self.intros):
                self.status.configure(
                    text=("简介已保存：%s" % name) if t else ("简介已清空：%s" % name))
            else:
                self.status.configure(text="简介已改，但存盘不成（内存里生效，重启即丢）。")
            self.refresh()
            if on_saved is not None:
                try:
                    on_saved(t)
                except Exception:
                    pass
            dlg.destroy()

        ttk.Button(foot, text="保 存", style="Tab.TButton",
                   command=do_save).pack(side="right")
        txt.bind("<Control-Return>", lambda _e: do_save())    # 顺手：Ctrl+Enter 即存
        ttk.Button(foot, text="取 消", style="Tab.TButton",
                   command=dlg.destroy).pack(side="right", padx=(0, 8))
        ttk.Button(foot, text="清 空", style="Tab.TButton",
                   command=lambda: txt.delete("1.0", "end")).pack(side="right", padx=(0, 8))

    # ---------- 手动添加 Agent ----------
    def add_agent_dialog(self):
        """「添加 Agent」窗：选一枚 exe，命名、备注，存进手动档。"""
        dlg = tk.Toplevel(self)
        dlg.title("添加 Agent")
        dlg.configure(bg=BG)
        dlg.resizable(False, False)
        dlg.transient(self)
        w, h = self._fit(_px(560), _px(300))
        sw, sh = dlg.winfo_screenwidth(), dlg.winfo_screenheight()
        dlg.geometry("%dx%d+%d+%d" % (w, h, (sw - w) // 2, (sh - h) // 2))

        pad = ttk.Frame(dlg, padding=(20, 16))
        pad.pack(fill="both", expand=True)
        ttk.Label(pad, text="添加一个 Agent", style="TLabel",
                  font=("Microsoft YaHei UI", 13, "bold")).pack(anchor="w")
        ttk.Label(pad, text="选一枚可执行文件（.exe / .lnk / .bat / .cmd），"
                            "本工具会把它登记进名册，卡片可点击启动。",
                  style="TLabel", font=("Microsoft YaHei UI", 9),
                  wraplength=_px(510), justify="left").pack(anchor="w", pady=(4, 12))

        v_path, v_name, v_desc = tk.StringVar(), tk.StringVar(), tk.StringVar()
        row1 = ttk.Frame(pad)
        row1.pack(fill="x", pady=(0, 8))
        ttk.Label(row1, text="程序路径", width=9,
                  font=("Microsoft YaHei UI", 10)).pack(side="left")
        e1 = ttk.Entry(row1, textvariable=v_path, font=("Microsoft YaHei UI", 10))
        e1.pack(side="left", fill="x", expand=True, ipady=4)
        ttk.Button(row1, text="浏览…", style="Tab.TButton",
                   command=lambda: self._pick_exe(v_path, v_name)).pack(side="left", padx=(6, 0))

        row2 = ttk.Frame(pad)
        row2.pack(fill="x", pady=(0, 8))
        ttk.Label(row2, text="名称", width=9,
                  font=("Microsoft YaHei UI", 10)).pack(side="left")
        ttk.Entry(row2, textvariable=v_name, font=("Microsoft YaHei UI", 10)).pack(
            side="left", fill="x", expand=True, ipady=4)

        row3 = ttk.Frame(pad)
        row3.pack(fill="x", pady=(0, 8))
        ttk.Label(row3, text="备注", width=9,
                  font=("Microsoft YaHei UI", 10)).pack(side="left")
        ttk.Entry(row3, textvariable=v_desc, font=("Microsoft YaHei UI", 10)).pack(
            side="left", fill="x", expand=True, ipady=4)

        msg = ttk.Label(pad, text="", style="Hint.TLabel",
                        font=("Microsoft YaHei UI", 9), wraplength=_px(510),
                        justify="left")
        msg.pack(anchor="w", pady=(6, 0))

        btns = ttk.Frame(pad)
        btns.pack(side="bottom", fill="x", pady=(14, 0))

        def do_add():
            path = v_path.get().strip().strip('"')
            if not path:
                msg.configure(text="请先选一枚程序。")
                return
            if not os.path.isfile(path):
                msg.configure(text="该路径不是文件：%s" % path)
                return
            nm = v_name.get().strip() or os.path.splitext(os.path.basename(path))[0]
            ok, why = False, "未找到采集器"
            try:
                mod = _import_app_scanner()
                if mod is not None:
                    ok, why = mod.add_manual(nm, path, v_desc.get().strip())
            except Exception as e:
                why = "失败：%s" % e
            if ok:
                msg.configure(text="已添加「%s」。" % nm)
                self.rescan()
                dlg.after(600, dlg.destroy)
            else:
                msg.configure(text=why)

        ttk.Button(btns, text="关 闭", style="Tab.TButton",
                   command=dlg.destroy).pack(side="right")
        ttk.Button(btns, text="添 加", style="Tab.TButton",
                   command=do_add).pack(side="right", padx=(0, 8))
        e1.focus_set()

    def _pick_exe(self, v_path, v_name):
        """开文件对话框选一枚程序，顺带把名填上。"""
        try:
            import tkinter.filedialog as fd
            p = fd.askopenfilename(
                title="选一枚程序",
                filetypes=[("可执行文件", "*.exe *.lnk *.bat *.cmd"),
                           ("所有文件", "*.*")])
            if p:
                v_path.set(p)
                if not v_name.get().strip():
                    v_name.set(os.path.splitext(os.path.basename(p))[0])
        except Exception:
            pass

    # ---------- 自动收编：见到「新 Agent」就把它目录里的 Skill 收一份 ----------
    def _skill_dirs_for_agent(self, a):
        """猜某个 Agent 存放 Skill 的目录：家目录约定 + 安装目录旁的 skills\\。"""
        home = os.path.expanduser("~")
        key = (a.get("key") or "").strip().lower()
        nm = (a.get("name") or "").strip().lower()
        names = {key}
        if nm:
            names.add(nm.replace(" ", ""))
            names.add(nm.replace(" ", "-"))
        cands = []
        for k in [x for x in names if x]:
            cands.append(os.path.join(home, k, "skills"))
            cands.append(os.path.join(home, "." + k, "skills"))
        exe = a.get("exe") or ""
        if exe:
            base = os.path.dirname(exe)
            cands.append(os.path.join(base, "skills"))
            cands.append(os.path.join(os.path.dirname(base), "skills"))
        out = []
        seen = set()
        for p in cands:
            n = os.path.normcase(p)
            if os.path.isdir(p) and n not in seen:
                seen.add(n)
                out.append(p)
        return out

    def _load_import_log(self):
        try:
            with open(SKILL_IMPORT_LOG, "r", encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict):
                d.setdefault("enabled", True)
                d.setdefault("agents", {})
                return d
        except Exception:
            pass
        return {"enabled": True, "agents": {}}

    def _save_import_log(self, d):
        try:
            os.makedirs(os.path.dirname(SKILL_IMPORT_LOG), exist_ok=True)
            with open(SKILL_IMPORT_LOG, "w", encoding="utf-8") as f:
                json.dump(d, f, ensure_ascii=False, indent=1)
            return True
        except Exception:
            return False

    def _auto_import_new_agents(self, agents):
        """扫描过后：发现**没见过的 Agent**，就把它存储目录里的 Skill 收一份进技能库。

        · 「新」以 `%LOCALAPPDATA%\<名字>\skill_auto_import.json` 为准；
          **初次运行只登记不搬运**（免得一上来就往人家库里搬东西），往后遇到新 key 才动手；
        · **只复制，绝不动来源**；库里已有同名的跳过；
        · 全程 try/except，出岔子不惊动主流程。
        返回一句人话（无事则空串）。把 json 里的 enabled 改成 false 即可停用。
        """
        log = self._load_import_log()
        if not log.get("enabled", True):
            return ""
        known = log.get("agents") or {}
        first_run = not known
        try:
            os.makedirs(SKILL_LIB, exist_ok=True)
        except Exception:
            pass

        fresh, total, failed = [], 0, 0
        for a in (agents or []):
            if (a.get("kind") or "agent") != "agent":
                continue        # 兵站工具与说明文档不是 Agent，不参这一局
            key = (a.get("key") or "").strip()
            if not key or key in known:
                continue
            if first_run:
                known[key] = "初次扫描，仅登记"
                continue
            got = 0
            for d in self._skill_dirs_for_agent(a):
                try:
                    entries = sorted(os.listdir(d))
                except Exception:
                    continue
                for nm in entries:
                    s = os.path.join(d, nm)
                    t = os.path.join(SKILL_LIB, nm)
                    if not (os.path.isdir(s) and os.path.isfile(os.path.join(s, "SKILL.md"))):
                        continue
                    if os.path.exists(t):
                        continue
                    try:
                        shutil.copytree(s, t)
                        got += 1
                    except Exception:
                        failed += 1
            known[key] = "%s 自动收 %d 个" % (time.strftime("%Y-%m-%d %H:%M"), got)
            total += got
            fresh.append("%s（%d 个）" % (a.get("name") or key, got))

        log["agents"] = known
        self._save_import_log(log)
        if first_run or not fresh:
            return ""
        return "识别到新 Agent：%s，已收 %d 个 Skill 入技能库%s。" % (
            "、".join(fresh), total,
            ("，另 %d 个失败" % failed) if failed else "")

    # ---------- 技能库：从别处导入 Skill ----------
    # 复制 skill 时一律跳过的杂物目录：版本库、字节码缓存、编辑器配置。
    # jianying-editor 那种大包，光 .git 与 __pycache__ 就够拖慢一大截。
    _SKILL_COPY_SKIP = {"__pycache__", ".git", ".idea", ".vscode", ".pytest_cache"}

    def _copy_skill_tree(self, src_dir, dst_dir, on_progress=None):
        """把 skill 目录拷进技能库，逐文件来。

        与 `shutil.copytree` 只差两点，都是为了「别卡死」：
          · 每 40 个文件回一次进度 —— 大包复制时界面照旧刷新（真正的解药在调用处
            的线程里，此处只是让进度有得可报）；
          · 目标已存在的同名文件不重复拷 —— 上次中途卡死留下的半截目录，再点一次
            「导入」就接着补，不必整个重来。

        返回 (本次实拷文件数, 源文件总数, 跳过的杂物目录数)。
        """
        n = total = junk = 0
        for dp, dns, fns in os.walk(src_dir):
            keep = []
            for d in dns:
                if d in self._SKILL_COPY_SKIP:
                    junk += 1
                else:
                    keep.append(d)
            dns[:] = keep
            rel = os.path.relpath(dp, src_dir)
            tgt = dst_dir if rel == "." else os.path.join(dst_dir, rel)
            try:
                os.makedirs(tgt, exist_ok=True)
            except Exception:
                continue
            for f in fns:
                sf = os.path.join(dp, f)
                tf = os.path.join(tgt, f)
                total += 1
                if os.path.isfile(tf):
                    continue
                try:
                    shutil.copy2(sf, tf)
                    n += 1
                except Exception:
                    pass
                if on_progress is not None and n and n % 40 == 0:
                    try:
                        on_progress(n)
                    except Exception:
                        pass
        return n, total, junk

    def _skill_sources(self):
        """本机可能装着 skill 的目录（供导入窗下拉选择）。"""
        home = os.path.expanduser("~")
        out = []
        for rel in (".workbuddy", ".claude", ".cursor", ".trae", ".agent", ".agents"):
            p = os.path.join(home, rel, "skills")
            if os.path.isdir(p):
                out.append(p)
        ws = os.path.join(home, "WorkBuddy")            # 各项目级技能
        if os.path.isdir(ws):
            try:
                for proj in sorted(os.listdir(ws)):
                    p = os.path.join(ws, proj, ".workbuddy", "skills")
                    if os.path.isdir(p):
                        out.append(p)
            except Exception:
                pass
        return out

    def _pick_dir(self, v_src, after=None):
        """选一个目录（导入窗的「浏览…」）。"""
        try:
            import tkinter.filedialog as fd
            p = fd.askdirectory(title="选一个装着 Skill 的目录")
            if p:
                v_src.set(p)
                if after:
                    after()
        except Exception:
            pass

    def import_skill_dialog(self):
        """「导入 Skill」窗：把别的 agent 目录下的 skill 收进本工具技能库。

        两条路：**复制**（库中独立一份，推荐）或 **目录链接**（mklink /J，
        不占空间、源改了这边跟着变，但源一删链接就断）。
        """
        try:
            os.makedirs(SKILL_LIB, exist_ok=True)
        except Exception:
            pass

        dlg = tk.Toplevel(self)
        dlg.title("导入 Skill")
        dlg.configure(bg=BG)
        dlg.transient(self)
        w, h = self._fit(_px(780), _px(585))
        sw, sh = dlg.winfo_screenwidth(), dlg.winfo_screenheight()
        dlg.geometry("%dx%d+%d+%d" % (w, h, (sw - w) // 2, (sh - h) // 2))

        pad = ttk.Frame(dlg, padding=(20, 16))
        pad.pack(fill="both", expand=True)
        ttk.Label(pad, text="导入 Skill 到本工具技能库", style="TLabel",
                  font=("Microsoft YaHei UI", 13, "bold")).pack(anchor="w")

        line = ttk.Frame(pad)
        line.pack(fill="x", pady=(4, 0))
        ttk.Label(line, text="技能库：%s" % SKILL_LIB, style="Hint.TLabel",
                  font=("Microsoft YaHei UI", 9)).pack(side="left")
        ttk.Button(line, text="打开", style="Tab.TButton",
                   command=lambda: self.open_path(SKILL_LIB)).pack(side="left", padx=(8, 0))

        row = ttk.Frame(pad)
        row.pack(fill="x", pady=(12, 6))
        ttk.Label(row, text="来源目录　", font=("Microsoft YaHei UI", 10)).pack(side="left")
        v_src = tk.StringVar()
        cb = ttk.Combobox(row, textvariable=v_src, font=("Microsoft YaHei UI", 9))
        cb.pack(side="left", fill="x", expand=True, padx=(0, 6))
        ttk.Button(row, text="浏览…", style="Tab.TButton",
                   command=lambda: self._pick_dir(v_src, refresh)).pack(side="left")

        mid = ttk.Frame(pad)
        mid.pack(fill="both", expand=True)
        lb = tk.Listbox(mid, selectmode="extended", activestyle="none",
                        exportselection=False, font=("Microsoft YaHei UI", 10),
                        bd=0, highlightthickness=1, highlightbackground=LINE)
        sb = ttk.Scrollbar(mid, orient="vertical", command=lb.yview)
        lb.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        lb.pack(side="left", fill="both", expand=True)

        info = ttk.Label(pad, text="", style="Hint.TLabel",
                         font=("Microsoft YaHei UI", 9), wraplength=_px(720), justify="left")
        info.pack(anchor="w", pady=(8, 0))

        v_mode = tk.StringVar(value="copy")
        mr = ttk.Frame(pad)
        mr.pack(fill="x", pady=(6, 0))
        ttk.Label(mr, text="导入方式", font=("Microsoft YaHei UI", 10)).pack(side="left")
        ttk.Radiobutton(mr, text="复制（推荐：独立一份，各改各的）", value="copy",
                        variable=v_mode).pack(side="left", padx=(8, 14))
        ttk.Radiobutton(mr, text="目录链接（不占空间，源改了这边跟着变）", value="link",
                        variable=v_mode).pack(side="left")

        found = {"names": [], "size": {}}
        busy = {"on": False}
        qbox = {"q": None}
        btn = {"go": None, "close": None}

        def _fmt_size(b):
            if b >= 1048576:
                return "%.0f MB" % (b / 1048576.0)
            if b >= 1024:
                return "%.1f KB" % (b / 1024.0)
            return "%d B" % b

        def _row(nm):
            """列表里那一行的文字：名字 + 库里有没有 + 体积（量好了才带）。"""
            tag = "（库中已有）" if os.path.isdir(os.path.join(SKILL_LIB, nm)) else ""
            sz = found["size"].get(nm)
            tail = "   %s / %d 文件" % (_fmt_size(sz[1]), sz[0]) if sz else ""
            return "  %s%s%s" % (nm, ("   " + tag) if tag else "", tail)

        def _default_info():
            if not os.path.isdir(v_src.get().strip()):
                return "这个目录不存在，换一个，或点「浏览…」挑一个。"
            return ("找到 %d 个 Skill（含 SKILL.md 的文件夹才算）。"
                    "可多选：Ctrl 逐个、Shift 连选。" % len(found["names"]))

        def _show_info(extra=""):
            """状态行：没别的话要说时，就把「已选几个、共多大」摆出来。"""
            if extra:
                info.configure(text=extra)
                return
            names = found["names"]
            picks = [names[k] for k in lb.curselection() if k < len(names)]
            if not picks:
                info.configure(text=_default_info())
                return
            known = [found["size"][x] for x in picks if x in found["size"]]
            tot = ("合计 %s / %d 个文件" % (_fmt_size(sum(v[1] for v in known)),
                                           sum(v[0] for v in known))
                   if known else "体积统计中…")
            info.configure(text="已选 %d 个：%s　%s。可多选：Ctrl 逐个、Shift 连选。"
                                % (len(picks), "、".join(picks), tot))

        sizeq = queue.Queue()

        def _measure_async(names):
            """体积另开线程去量 —— 免得开窗或选中的那一刻先卡一下。

            量完只往队列里丢，由主线程的 `_poll_sizes` 取走 —— 后台线程一律不碰
            控件（从别的线程喊 `after` 会撞上 "main thread is not in main loop"）。
            """
            root = v_src.get().strip()

            def work():
                res = {}
                for nm in names:
                    n = b = 0
                    for dp, _dn, fns in os.walk(os.path.join(root, nm)):
                        for f in fns:
                            try:
                                b += os.path.getsize(os.path.join(dp, f))
                                n += 1
                            except Exception:
                                pass
                    res[nm] = (n, b)
                sizeq.put(res)

            threading.Thread(target=work, daemon=True).start()

        def _poll_sizes():
            if not dlg.winfo_exists():
                return
            try:
                while True:
                    _apply_sizes(sizeq.get_nowait())
            except queue.Empty:
                pass
            except Exception:
                pass
            dlg.after(200, _poll_sizes)

        def _apply_sizes(res):
            if not dlg.winfo_exists():
                return
            found["size"].update(res)
            cur = list(lb.curselection())
            lb.delete(0, "end")
            for nm in found["names"]:
                lb.insert("end", _row(nm))
            for k in cur:
                if k < len(found["names"]):
                    lb.selection_set(k)
            if not busy["on"]:
                _show_info()

        def refresh(*_a):
            lb.delete(0, "end")
            root = v_src.get().strip()
            names = []
            if os.path.isdir(root):
                try:
                    for nm in sorted(os.listdir(root)):
                        d = os.path.join(root, nm)
                        if os.path.isdir(d) and os.path.isfile(os.path.join(d, "SKILL.md")):
                            names.append(nm)
                except Exception:
                    pass
            found["names"] = names
            found["size"] = {k: v for k, v in found["size"].items() if k in names}
            for nm in names:
                lb.insert("end", _row(nm))
            _show_info()
            todo = [nm for nm in names if nm not in found["size"]]
            if todo:
                _measure_async(todo)

        def do_import():
            """导入的全部重活都丢给后台线程 —— 界面线程只负责收消息、刷文字。

            从前这里是主线程里直接 `shutil.copytree`，碰上 jianying-editor 那种
            4565 个文件 / 319MB 的大包，Windows 五秒收不到消息就判「未响应」。
            """
            if busy["on"]:
                return
            sel = list(lb.curselection())
            names = found["names"]
            root = v_src.get().strip()
            if not sel:
                _show_info("请先在上面的列表里选中要导入的 Skill。")
                return
            if not os.path.isdir(root):
                _show_info("来源目录不见了，重新挑一个再导。")
                return
            picks = [names[k] for k in sel if k < len(names)]
            if not picks:
                return
            mode = v_mode.get()
            busy["on"] = True
            try:
                btn["go"].configure(text="导入中…", state="disabled")
            except Exception:
                pass
            info.configure(text="开始处理 %d 个 Skill，窗口照旧能操作……" % len(picks))
            q = queue.Queue()
            qbox["q"] = q

            def work():
                done, skip, bad, notes = [], [], [], []
                for k, nm in enumerate(picks, 1):
                    s = os.path.join(root, nm)
                    d = os.path.join(SKILL_LIB, nm)
                    head = "第 %d/%d 个：%s" % (k, len(picks), nm)
                    try:
                        if mode == "link":
                            if os.path.isdir(d):
                                if os.path.isfile(os.path.join(d, "SKILL.md")):
                                    skip.append(nm)
                                    q.put(("line", head + " —— 库里已有，跳过"))
                                    continue
                                # 半截/断链：只摘链接本身，绝不动源目录
                                try:
                                    os.rmdir(d)
                                except Exception:
                                    raise RuntimeError("库里那份不完整且非链接，请先手动清掉")
                            r = subprocess.run(["cmd", "/c", "mklink", "/J", d, s],
                                               capture_output=True, text=True)
                            if r.returncode != 0 or not os.path.isdir(d):
                                raise RuntimeError(
                                    (r.stdout or r.stderr or "mklink 失败").strip()[:60])
                            done.append(nm)
                            q.put(("line", head + " —— 链接已建"))
                        else:
                            if os.path.isdir(d) and os.path.isfile(os.path.join(d, "SKILL.md")):
                                skip.append(nm)
                                q.put(("line", head + " —— 库里已有，跳过"))
                                continue
                            resume = os.path.isdir(d)
                            q.put(("line", head + " —— 开始%s…" % ("续拷" if resume else "复制")))
                            copied, total, junk = self._copy_skill_tree(
                                s, d, lambda n: q.put(
                                    ("line", head + " —— 已拷 %d 个文件…" % n)))
                            if not os.path.isfile(os.path.join(d, "SKILL.md")):
                                raise RuntimeError("源目录里没有 SKILL.md")
                            done.append(nm)
                            notes.append("%s（%s %d/%d）" % (
                                nm, "补拷" if resume else "新拷", copied, total))
                            q.put(("line", head + " —— 完成，实拷 %d/%d 个文件%s"
                                   % (copied, total,
                                      "，跳过 %d 个杂物目录" % junk if junk else "")))
                    except Exception as e:
                        bad.append("%s（%s）" % (nm, e))
                        q.put(("line", head + " —— 失败：%s" % e))
                q.put(("end", done, skip, bad, notes))

            threading.Thread(target=work, daemon=True).start()
            dlg.after(80, _pump)

        def _pump():
            if not dlg.winfo_exists():
                return          # 窗关了：后台让它自己跑完，别再碰控件
            q = qbox["q"]
            try:
                while q is not None:
                    item = q.get_nowait()
                    if item[0] == "line":
                        info.configure(text=item[1])
                    elif item[0] == "end":
                        _finish(*item[1:])
                        return
                    else:
                        break
            except queue.Empty:
                pass
            except Exception:
                pass
            dlg.after(80, _pump)

        def _finish(done, skip, bad, notes):
            busy["on"] = False
            try:
                btn["go"].configure(text="导 入", state="normal")
            except Exception:
                pass
            msg = []
            if done:
                msg.append("已导入 %d 个：%s" % (len(done), "、".join(done)))
                if notes:
                    msg.append("（%s）" % "；".join(notes))
            else:
                msg.append("没有导入任何 Skill")
            if skip:
                msg.append("库里已有、跳过 %d 个：%s" % (len(skip), "、".join(skip)))
            if bad:
                msg.append("失败 %d 个：%s" % (len(bad), "；".join(bad)))
            if done:
                msg.append("正在重扫名册…")
            refresh()
            info.configure(text="　".join(msg))
            if done:
                self.rescan()

        lb.bind("<<ListboxSelect>>", lambda *_: None if busy["on"] else _show_info())

        btn["go"] = ttk.Button(pad, text="导 入", style="Tab.TButton", command=do_import)
        btn["go"].pack(side="right", pady=(12, 0))
        btn["close"] = ttk.Button(pad, text="关 闭", style="Tab.TButton",
                                  command=dlg.destroy)
        btn["close"].pack(side="right", padx=(0, 8), pady=(12, 0))

        srcs = self._skill_sources()
        cb.configure(values=srcs)
        if srcs:
            v_src.set(srcs[0])
        refresh()
        dlg.after(200, _poll_sizes)

    # ---------- 列表 ----------
    def _build_list(self):
        outer = ttk.Frame(self, padding=(PAGE_X, 0, PAGE_X, 0))
        outer.pack(fill="both", expand=True)

        self.canvas = tk.Canvas(outer, bg=BG, highlightthickness=0, bd=0)
        vsb = ttk.Scrollbar(outer, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)

        self.inner = tk.Frame(self.canvas, bg=BG)
        self.win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")

        self.inner.bind("<Configure>",
                        lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", self._on_canvas_resize)
        self.canvas.bind_all("<MouseWheel>", self._on_wheel)
        self.canvas.bind_all("<Shift-MouseWheel>", self._on_shift_wheel)

        self.status = ttk.Label(self, text="", style="Faint.TLabel",
                                font=("Microsoft YaHei UI", 9),
                                padding=(PAGE_X, SP_2, PAGE_X, SP_3))
        self.status.pack(fill="x")

    def _on_canvas_resize(self, e):
        self.canvas.itemconfigure(self.win, width=e.width)

    def _on_wheel(self, e):
        # 滑轮压在工具条上、且工具条装不下时，改去横向滚工具条（纵向留给列表）
        try:
            if self._bar_overflow() and self._pointer_in_bar():
                # 一格滑轮约 54px（xscrollincrement 18 × 3），十来下就滚到底
                self._bar_scroll(-e.delta / 120.0 * 3)
                return
        except Exception:
            pass
        if self.canvas.winfo_exists():
            self.canvas.yview_scroll(int(-e.delta / 120), "units")

    def _on_shift_wheel(self, e):
        """Shift + 滑轮：不管指针在哪，都横向滚工具条。"""
        try:
            self._bar_scroll(-e.delta / 120.0 * 3)
        except Exception:
            pass
        return "break"

    # ---------- 数据整理 ----------
    def collect(self):
        cat = self.cat
        out = []
        if cat == "agents":
            for a in self.agents:
                exe = a.get("exe", "")
                ok = a.get("exists")
                kind = a.get("kind", "agent")
                # 兵站工具（kind=tool）与说明文档（kind=doc）本非 Agent，
                # 原先误归此栏，本版要求删 —— 此处直接拦下，不进 Agents 栏。
                if kind in ("tool", "doc"):
                    continue
                desc = a.get("desc", "")
                if not ok:
                    desc = "（未找到程序文件）" + desc
                out.append({
                    "title": a.get("name", ""),
                    "desc": desc,
                    "meta": exe,
                    "extra": a.get("category", ""),
                    "tags": [t for t in (a.get("category", ""),
                                         a.get("source", "")) if t],
                    "icon": a.get("icon", ""),
                    "key": a.get("key", ""),
                    # 自订简介（作者可编辑）；没有则空串，卡片退回扫描到的说明
                    "intro": self.intros.get(a.get("key") or "", ""),
                    "agent": a,
                    "kind": kind,
                    "clickable": bool(ok),
                    # 名下挂有工作区散件者（WorkBuddy）：点卡片开工作台，而非直接启动
                    "has_works": bool(a.get("works")),
                })
        elif cat == "skills":
            shelves = {}
            for s in self.data.get("skill_shelves", []):
                shelves.setdefault(s.get("entry"), []).append(s.get("shelf"))
            for s in [x for x in self.data.get("skills", []) if not x.get("hidden")]:
                tags = [s.get("source") or ""]
                if s.get("version"):
                    tags.append("v" + str(s["version"]))
                if s.get("license"):
                    tags.append(s["license"])
                extra = ""
                sh = shelves.get(s.get("dir"))
                if sh:
                    extra = "已挂技能架：" + " · ".join(sh)
                out.append({
                    "title": s.get("name", ""),
                    "desc": s.get("desc", ""),
                    "meta": s.get("path", ""),
                    "extra": extra,
                    "tags": [t for t in tags if t],
                    # 详情窗要用的原始字段（卡片上只用截断版）
                    "_source": s.get("source", ""),
                    "_version": s.get("version", ""),
                    "_author": s.get("author", ""),
                    "_license": s.get("license", ""),
                    "_tags": s.get("tags") or [],
                    "_files": s.get("files", ""),
                })
        elif cat == "tasks":
            # 只列用户设过的「主要项目」——全摊出来等于没重点
            for pr in (getattr(self, "projects", None) or []):
                hit = self._project_records(pr)
                arts = [a for t in hit for a in (t.get("artifacts") or [])]
                last = hit[0].get("when") if hit else ""
                keys = "、".join(pr.get("keys") or []) or pr.get("name", "")
                out.append({
                    "title": pr.get("name") or "未命名项目",
                    "desc": (pr.get("note") or
                             ("关联记录 %d 条，最近 %s；产物 %d 件。"
                              % (len(hit), last or "—", len(arts)))),
                    "meta": "关键词：%s ｜ 记录 %d 条 ｜ 产物 %d 件 ｜ 最近 %s"
                            % (keys, len(hit), len(arts), last or "—"),
                    "extra": "主要项目",
                    "tags": ["主要项目"],
                    "key": "proj_" + str(pr.get("name")),
                    "proj": pr,
                    "_tasks": hit,
                })
        elif cat == "mcp":
            for m in self.data.get("mcp", []):
                if m.get("name", "").endswith(".env"):
                    continue
                _cmd = m.get("command") or ""
                _desc = "注册于 " + str(m.get("client", ""))
                if "安卓模拟器MCP" in _cmd:
                    # 原「工具与运行时」栏里那点真正有用的东西，挪到本位：
                    # 这是一条**自带运行时的兵站**，不是普通的一条 MCP 登记。
                    _desc += " · 安卓兵站（自备 Node/JDK/Android SDK，14 个工具）"
                out.append({
                    "title": m.get("name", ""),
                    "desc": _desc,
                    "meta": m.get("command") or m.get("config", ""),
                    "extra": str(m.get("client", "")),
                    "tags": [str(m.get("client", ""))],
                })
        # —— 整合卡：入口目录里有 *.suite.json 的，把 members 收进它怀里 ——
        # 第二十六轮（本版要求）：应用里只露入口那一张卡，13 篇同源技能都收进去。
        if cat == "skills":
            entries, hidden = {}, set()
            for r in out:
                m = _suite_of(r.get("meta") or "")
                if m:
                    entries[(r.get("meta") or "").lower()] = m
            for m in entries.values():
                for sub in (m.get("members") or []):
                    hidden.add(str(sub).strip().lower())
            _keep = []
            _entry_seen = set()
            for r in out:
                _dir = (r.get("meta") or "")
                _base = os.path.basename(_dir.rstrip("\\/")).lower()
                _m = entries.get(_dir.lower())
                if _m:
                    # 整合卡只留一张 —— 否则「库」与「WorkBuddy 技能架」各出一张，
                    # 两张一模一样的金卡并排，比不分组还乱。
                    if _base in _entry_seen:
                        continue
                    _entry_seen.add(_base)
                    memb = list(_m.get("members") or [])
                    r["_members"] = memb
                    r["_suite_labels"] = _m.get("labels") or {}
                    r["_suite"] = _m.get("label") or "整合卡"
                    r["extra"] = "%s · 整合卡" % r["_suite"]
                    r["desc"] = ("整合卡 · 内含 %d 篇同源技能（%s）。%s"
                                 % (len(memb), r["_suite"],
                                    (_m.get("note") or "").strip()))
                    _keep.append(r)
                    continue
                # 成员篇默认不单独露面；**一搜就露** —— 搜得着才找得回
                if _base in hidden and not self.var_q.get().strip():
                    continue
                _keep.append(r)
            # 整合卡置顶 —— 免得「藏起来之后自己都找不着」
            _keep.sort(key=lambda rr: 0 if rr.get("_members") else 1)
            out = _keep

        q = self.var_q.get().strip().lower()
        if q:
            out = [r for r in out if q in (
                r["title"] + " " + r["desc"] + " " + r["meta"] + " " +
                r["extra"] + " " + " ".join(r["tags"])).lower()]
        return out

    # ---------- 渲染 ----------
    def switch(self, key):
        self.cat = key
        self._refresh_actions()      # 动作行跟着页签换
        self.refresh()

    def refresh(self):
        # 先定栏目显隐，再高亮当前项（空栏目连页签隐去）
        self._refresh_tabs()
        self.render()

    def _shorten(self, text, limit):
        text = " ".join(str(text).split())
        return text if len(text) <= limit else text[:limit].rstrip() + "…"

    def render(self):
        _q0 = self.var_q.get().strip().lower()
        if len(_q0) >= 1:
            # 有搜索词 → 全局搜索视图（不分栏，命中什么都摊出来）
            return self.render_search(_q0)
        for w in self.inner.winfo_children():
            w.destroy()
        self.rows = self.collect()

        if not self.rows:
            # 第十八轮（本版要求）：一台 Agent 都没有时，Agents 栏不许整栏消失，
            # 就地写明缘由，并把两条出路做成按钮摆在这儿。
            # 注意：只因搜索词没命中时不走这条 —— 那是「筛没了」，不是「本机没有」。
            if self.cat == "agents" and not self.var_q.get().strip():
                tk.Label(self.inner, text="未检测到 Agent", bg=BG, fg=FG,
                         font=("Microsoft YaHei UI", 13, "bold")).pack(pady=(64, 8))
                tk.Label(self.inner,
                         text="本机一台 Agent 也没检出。\n"
                              "请点「重新扫描」重试，或用「＋ 添加 Agent」手动导入。",
                         bg=BG, fg=FAINT, justify="center",
                         font=("Microsoft YaHei UI", 10)).pack(pady=(0, 8))
                # 第五十二轮（照 redesign-skill 审）：空状态别只说"没有"，
                #   还要说清**它是怎么找的** —— 新电脑上用户才知道该往哪儿看。
                tk.Label(self.inner,
                         text=u"它会找这几处：\n"
                              u"· 内置登记表 + 桌面/开始菜单快捷方式 + 按特征名全盘寻真身\n"
                              u"· 按特征自动发现（有 MCP 配置 / skills 目录 / AGENTS.md 的目录）\n"
                              u"· 你手工加过的（「＋ 添加 Agent」存在 %LOCALAPPDATA%）",
                         bg=BG, fg=FAINT, justify="left",
                         font=("Microsoft YaHei UI", 9)).pack(pady=(0, 18))
                _row = ttk.Frame(self.inner)
                _row.pack()
                ttk.Button(_row, text="重新扫描", style="Tab.TButton",
                           command=self.rescan).pack(side="left", padx=(0, 8))
                ttk.Button(_row, text="＋ 添加 Agent", style="Tab.TButton",
                           command=self.add_agent_dialog).pack(side="left")
                self.status.configure(text="共 0 项。本机未检出 Agent。")
                self.canvas.yview_moveto(0)
                return
            _q = self.var_q.get().strip()
            _hits = (getattr(self, "_hist_hits", None) or {}).get(_q.lower()) if _q else None
            _has = bool(_hits)
            # 有工作记录命中时，这话就不能说得那么绝 —— 否则界面上会自相矛盾：
            # 上面写「没有相符的条目」，下面却列着「工作记录命中 · N 条」。
            tk.Label(self.inner,
                     text=("本栏没有相符的条目" if (_q and _has) else "没有相符的条目"),
                     bg=BG, fg=FG,
                     font=("Microsoft YaHei UI", 13, "bold")).pack(pady=(60, 6))
            if _q and _has:
                _sub = ("「%s」在这一栏没有条目；但工作记录里有 %d 条命中，见下。"
                        % (_q, len(_hits)))
            elif _q:
                _sub = "没有与「%s」相符的条目，换个词试试。" % _q
            else:
                _sub = ("本机未检出此类。可点「重新扫描」重试，\n"
                        "或用「＋ 添加 Agent」手动登记。")
            tk.Label(self.inner, text=_sub, bg=BG, fg=FAINT, justify="center",
                     font=("Microsoft YaHei UI", 10)).pack(pady=(0, 26))
            self.status.configure(text="共 0 项。" +
                                  ("工作记录命中 %d 条。" % len(_hits) if _has else ""))
            self._render_hist_hits(_q.lower())     # 本栏没命中，工作记录里也许有
            self.canvas.yview_moveto(0)
            return

        LIMIT_DESC = 150
        LIMIT_META = 120
        is_agents = (self.cat == "agents")

        cw = self.canvas.winfo_width()
        cols = self._cols_for(cw)
        if GlassCard is None:
            cols = max(1, cw // _px(340)) if cw > 1 else 3
        self._setup_columns(cols)
        card_size = self._card_size(cw, cols)

        n = len(self.rows)
        nrow = (n + cols - 1) // cols
        for i, r in enumerate(self.rows):
            grow = (i // cols) * 2
            if GlassCard is not None:
                # Agents 栏：卡片主色取自该 Agent 图标的实际主色调
                if is_agents:
                    liquid = self._agent_color(r)
                elif r.get("_members"):
                    liquid = SUITE_COLOR            # 整合卡：金
                else:
                    liquid = cat_liquid(self.cat, i)
                cell = GlassCard(self.inner, liquid=liquid,
                                 size=card_size, fill_ratio=0.58)
                self._fill_glass(cell, r, is_agents, LIMIT_DESC, LIMIT_META)
            else:
                cell = self._plain_card(r, is_agents, LIMIT_DESC, LIMIT_META)
            cell.grid(row=grow, column=i % cols, sticky="nsew",
                      padx=SP_2, pady=(0, 0))

        # 行间只留空白间隔，**不铺搁板横线**（作者明令去掉那些灰横线）
        for k in range(nrow - 1):
            gap = tk.Frame(self.inner, bg=BG, height=SP_4)
            gap.grid(row=k * 2 + 1, column=0, columnspan=cols, sticky="ew")

        if is_agents:
            n_live = len([x for x in self.rows if x.get("clickable")])
            self.status.configure(
                text="共 %d 项，其中 %d 项可点开。可启者点卡片即启。"
                     % (len(self.rows), n_live))
        else:
            self.status.configure(text="共 %d 项。" % len(self.rows))
        self._render_hist_hits(self.var_q.get().strip().lower())
        self.canvas.yview_moveto(0)

    def _cols_for(self, cw):
        """按画布宽定列数。

        第五十三轮（本版要求：回到一行两张）—— 原式 `cw // _px(430)` 有个缝：
        本应用允许的最小窗宽是 900（逻辑像素），而「两列」需要
        2×430 + 左右留白 ≈ 965 —— 于是窗口一旦被记成最小尺寸，画布宽就
        差着 3% 掉到 1 列，卡片瞬间涨成一张巨卡（开窗第一帧还看不出，
        因为那时画布还没量出宽、走的是兜底两列）。故留 5% 容差：
        差不到 5% 仍按两列排，别把两列挤成一列。
        """
        per = _px(430)
        if cw <= 1:
            return 2                      # 画布还没量出来，先按两列兜底
        cols = max(1, cw // per)
        if cols == 1 and cw >= per * 2 * 0.95:
            cols = 2
        return cols

    def _setup_columns(self, cols, maxc=8):
        """排网格前先把列属性**全部复位**，再按本次列数重设。

        上一次若排过两列，第二列身上还留着 `weight=1, uniform='card'`；
        本次只排一列时，那一列虽然空着，`uniform` 仍会把它算进来，把仅存的
        这一列也拽成半宽 —— 于是卡片怎么设都不对：实测声明 1228 宽的卡，
        被网格塞进了 602 的格子里（改版时踩到的真事，量了半天才揪出来）。
        复位一行，从此不会再被上一轮的排版牵着走。
        """
        for c in range(maxc):
            try:
                self.inner.columnconfigure(c, weight=0, uniform="")
            except Exception:
                pass
        for c in range(cols):
            self.inner.columnconfigure(c, weight=1, uniform="card")

    def _card_size(self, cw, cols):
        """卡片该多大：**铺满自己那一格**。

        从前是写死 400（逻辑像素）：双列时每格比它宽，卡片右边空出一条；单列时
        更明显，右边空掉一大半、看着像没排完版。今按「画布宽 ÷ 列数 − 格内两侧
        留白」算 —— 两列也好、单列也好，都把格子填满；窄窗上它就是一行一条的
        名册列表。留 330 的下限，免得算成负数或细条。
        """
        w = _px(400)
        if cw > _px(460):
            w = max(_px(330), cw // max(1, cols) - SP_2 * 2)
        return (w, _px(172))

    # ---------- 取色 ----------
    def _agent_color(self, row):
        """取该 Agent 的主色：先按图标实色，取不到再退到内置登记表。"""
        key = row.get("key", "")
        icon = row.get("icon", "")
        cache = getattr(self, "_color_cache", None)
        if cache is None:
            cache = self._color_cache = {}
        if key in cache:
            return cache[key]
        fallback = AGENT_COLORS.get(key, INFO)
        col = fallback
        if icon and ICON_DIR:
            p = os.path.join(ICON_DIR, icon)
            col = _icon_dominant_color(p, fallback)
        cache[key] = col
        return col

    # ---------- 卡片里的内容 ----------
    def _desc_budget(self, wraplen):
        """按真实像素宽度算：**两行**装得下多少个「显示宽度单位」。

        为什么非量不可：卡片宽度是活的（宽窗双列各半、窄窗单列铺满），字号又受
        DPI 缩放影响 —— 无论写死「88 个字」还是「100 个单位」，都只是猜，猜大了
        就有一行字从卡里爬出来压到底下的路径行上（改版前正是如此）。
        这里用 tkinter 自己的字体度量量一次「永」字宽（结果缓存），
        再拿折行宽度去除 —— 量出来的才作数。
        """
        try:
            if getattr(self, "_unit_px", None) is None:
                import tkinter.font as _tkfont
                _f = _tkfont.Font(font=F_CARD_D)
                # 一个全角字 = 2 个单位，故 1 个单位 = 半个字宽
                self._unit_px = max(5.0, float(_f.measure(u"\u6c38")) / 2.0)
            return max(24, int(wraplen / self._unit_px) * 2 - 4)
        except Exception:
            return DESC_UNITS

    def _fill_glass(self, glass, r, is_agents, LIMIT_DESC, LIMIT_META):
        """往白卡里铺文字与图标。左边留出彩色竖条的位置。"""
        glass.clear_content()
        # ---- 卡内垂直节奏（改版前是 10 / 38 / 39 / 78 / 133 一组手写数字，
        #      各行间距 顺 眼 但 不 齐；今按 4 的倍数重排，两列卡片由此严格同高同线）----
        pad = SP_5                                  # 竖脊之后再起文字（20）
        inner = glass._cw - pad - SP_4
        col = glass.liquid
        accent = darken_hex(col, 0.34)              # 文字用色：主色压深，保证可读
        Y_CHIP = SP_3                               # 顶部小标签
        Y_ICON = _px(42)                            # 图标与标题同一顶线
        Y_DESC = _px(80)                            # 描述两行
        Y_FOOT = _px(132)                           # 路径 / 备注一行

        # 顶部小标签（种类 / 来源）
        kind = r.get("extra", "") if is_agents else (
            str(r["tags"][0]) if r.get("tags") else "")
        if kind:
            chip = tk.Label(glass, text=" " + self._shorten(kind, 14) + " ",
                            bg=SOFT, fg=accent, font=F_TAG)
            glass.add_content(chip, pad, Y_CHIP)

        # 图标（仅 Agents 栏）
        has_icon = False
        if is_agents:
            ic = self._icon(r.get("icon", ""), _px(34))
            if ic:
                lab = tk.Label(glass, bg=CARD, image=ic, bd=0)
                glass._icon_ref = ic
                glass.add_content(lab, pad, Y_ICON + _px(2))
                has_icon = True

        # 标题（加粗、放大）
        tx = pad + _px(46) if has_icon else pad
        title = tk.Label(glass, text=self._shorten(r["title"], 20), bg=CARD, fg=FG,
                         anchor="w", justify="left", font=F_CARD_T)
        glass.add_content(title, tx, Y_ICON, width=glass._cw - tx - SP_4,
                          height=_px(30))

        # 描述：**自订简介优先**（作者可编辑），没写才退回扫描到的说明。
        # 第二十三轮：卡上不再摆编辑入口 —— 编辑挪进「点进来的详情页」，
        # 卡片恢复原高度与原宽度，简介在这儿只作展示。
        # 注意：Tk 的 Label 不给 wraplength 就**不会折行**，只会单行截断。
        #
        # 2026-09-20（美化）：**溢出治本**。卡片高是死的（172），描述从前按
        #   「88 个字」截 —— 那是按字数算的，88 个汉字排下来是四行，比留给它的
        #   两行整整多出一倍，多出来的字就压到底下那行路径上去（改版前的原病）。
        #   今改两处：① 按**显示宽度**截（clip_units，全角算 2 个单位），
        #             ② Label 直接钉死 height=2 行，多一个字也不许爬出来。
        intro = (r.get("intro") or "") if is_agents else ""
        wraplen = max(_px(120), inner - _px(4))
        desc = tk.Label(glass,
                        text=clip_units(intro or r["desc"],
                                        self._desc_budget(wraplen)),
                        bg=CARD, fg=FG if intro else DIM, anchor="nw",
                        justify="left", height=2, font=F_CARD_D,
                        wraplength=wraplen)
        # 只给宽、**不给高**：让 Label 自己按「两行」定高。
        # 从前这里钉了 height=_px(46) —— 本意是「留两行的地方」，可 _px(46) 在
        # 150% 缩放屏上是 69px，而两行文字只要 60px，多出来的 9px 正好够第三行
        # 探出头来。宽度交给外面、高度交给字体，两边就不会打架。
        glass.add_content(desc, pad, Y_DESC, width=inner)

        # 名下挂着工作区散件者，在标题右上角缀一枚小徽标，提示「点开有东西看」
        n_works = len((r.get("agent") or {}).get("works") or []) \
            if is_agents else 0
        # 第四十六轮（本版要求）：按「接进应用数据的程度」在卡片上打四盏灯 ——
        #   记录 / 人格（每轮先查的指针）/ MCP（注册且受信）/ 地址（登记的检索地址）
        #   ● 亮 ｜ ◐ 半（注册了但客户端受信门槛拦着）｜ ○ 暗
        if is_agents:
            it = (r.get("agent") or {}).get("integration") or {}
            if it:
                row = tk.Frame(glass, bg=CARD)
                _mcp_on = bool(it.get("mcp"))
                _mcp_full = _mcp_on and bool(it.get("mcp_trusted", True))
                # 灯可点（第四十七轮）：每盏灯连到该管的那个窗 ——
                #   记录 → 工作台 ｜ 人格 → 接入 Agent ｜ MCP → MCP 状态
                #   （第四十八轮去掉了「地址」灯：那是手段，不是结果）
                _ag = r.get("agent") or {}
                _acts = {
                    # 第六十轮：记录灯改开**这个 Agent 的工作台**（记录都长在哪、点即开）
                    u"记录": lambda rr=r: self.open_workspace(rr),
                    u"人格": lambda a=_ag: self.onboard_dialog(a),
                    "MCP": lambda a=_ag: self.mcp_status_dialog(a),
                }
                _tips = {
                    u"记录": u"点开这个 Agent 的工作台：它的记录都长在哪些位置，点路径即开",
                    u"人格": u"点开「接入 Agent」：接 MCP、放「每轮先查再答」指针、取自检提示词",
                    "MCP": u"点开「MCP 状态」：它的 MCP 配置在哪、注册了没、要不要在客户端里受信",
                }
                # 第四十八轮（本版要求）：去掉「地址」那盏灯 —— 自报家门只是让
                #   「记录」灯亮起来的**手段之一**，本身不是结果；登记的地址本就
                #   算在记录根里，记录灯亮就说明含它在内。手段不该占一格。
                for nm, on, half in (
                        (u"记录", bool(it.get("records")), False),
                        (u"人格", bool(it.get("persona")), False),
                        ("MCP", _mcp_full, _mcp_on and not _mcp_full)):
                    glyph = u"●" if on else (u"◐" if half else u"○")
                    # 壳底去掉（先前套浅灰底框，比设计吵）；亮灯用主色，暗灯用弱色
                    lb = tk.Label(row, text=u"%s %s" % (glyph, nm),
                                  bg=CARD,
                                  fg=accent if on else FAINT,
                                  font=F_TAG, padx=_px(2), pady=_px(1),
                                  cursor="hand2")
                    lb.pack(side="left", padx=(0, _px(3)))
                    lb.bind("<Button-1>",
                            lambda _e, f=_acts[nm]: (f(), "break")[1])
                    self._tip(lb, _tips[nm])
                if n_works:
                    # 「工作 N 件」并进同一行（先前挂在灯的下一行，孤零零的）
                    tk.Label(row, text=u"· 工作 %d 件" % n_works, bg=CARD,
                             fg=DIM, font=F_TAG, padx=_px(2)).pack(
                                 side="left", padx=(_px(6), 0))
                glass.add_content(row, glass._cw - SP_4, Y_CHIP - _px(2),
                                  anchor="ne")
        elif n_works:
            badge = tk.Label(glass, text=" 工作 %d 件 " % n_works,
                             bg=SOFT, fg=accent, font=F_TAG)
            glass.add_content(badge, glass._cw - SP_5, Y_CHIP - _px(2),
                              anchor="ne")
        elif r.get("_members"):
            # 整合卡：右上角挂一枚同色徽标，与普通技能卡拉开距离
            badge = tk.Label(glass, text=" ★ 整合卡 · %d 篇 " % len(r["_members"]),
                             bg=SUITE_COLOR, fg="#ffffff", font=F_TAG)
            glass.add_content(badge, glass._cw - SP_5, Y_CHIP - _px(2),
                              anchor="ne")

        # 底部一行：路径（Agent 栏）或备注（其余栏）
        # 第四十九轮：路径按「中间省略」缩写（保留盘符与末两级）——
        #   先前整条铺满，右下角的「工作台 ›」提示会压在它上面，字尾巴从提示底下穿出去
        if is_agents:
            foot = self._elide_path(r["meta"], 66)
            ffont = F_MONO
            fc = FAINT
        else:
            # 掐掉尾上孤零零的分隔符 —— 有些 meta 本就是「… ｜ 记录 25 条 ｜ 」，
            # 那个尾巴上的「｜」落在脚注里像没写完的半句话
            foot = self._shorten(r["meta"], 62).rstrip(u" \uFF5C|\u00b7, ")
            ffont = F_MONO
            fc = FAINT
        # 脚注之上拉一根发丝线：把「说明」与「出处」分开，卡内立见结构。
        # （用 1px 的 Frame 而不是画线 —— 画在画布上的东西，窗口一缩放就会被
        #    redraw 抹掉；摆成内容控件才会跟着卡片一起重排。）
        sep = tk.Frame(glass, bg=LINE, height=1, bd=0)
        glass.add_content(sep, pad, Y_FOOT - _px(10), width=inner, height=1)

        fl = tk.Label(glass, text=foot, bg=CARD, fg=fc, anchor="w",
                      justify="left", font=ffont)
        # 让出右下角那枚提示的位置（76 而非 66 —— 先前「点击启动 ›」与路径尾巴
        # 只差十几个像素，两列时看着要贴上了）
        glass.add_content(fl, pad, Y_FOOT, width=inner - _px(76),
                          height=_px(20))

        # 右下角那枚淡字：有工作台者写「工作台」，可启动者写「点击启动」，其余写「详情 ›」
        # （**不是按钮**，作者已令撤去按钮；此处只是文字暗示）
        if is_agents and r.get("has_works"):
            hint_text = "工作台 ›"
        elif is_agents and r.get("clickable"):
            hint_text = "点击启动 ›"
        elif r.get("_members"):
            hint_text = "打开整合卡 ›"
        else:
            hint_text = "详情 ›"
        hint = tk.Label(glass, text=hint_text,
                        bg=CARD, fg=FAINT, anchor="e", font=F_HINT)
        glass.add_content(hint, glass._cw - SP_5, Y_FOOT, anchor="ne",
                          height=_px(20))

        glass.redraw_all(0.0)
        glass._chip = None
        if is_agents:
            # 第二十四轮：**人人都有工作台** —— 点卡片一律进工作台
            # （从前只有 WorkBuddy 有；其余 Agent 一点就把本体启动了，进不去页面）
            glass.bind_click(lambda rr=r: self.open_workspace(rr))
            glass.rebind_contents()
        elif r.get("_members"):
            # 整合卡：点开进「成员卡片」窗，而不是普通详情
            glass.bind_click(lambda rr=r: self.open_suite(rr))
            glass.rebind_contents()
        elif is_agents and r.get("clickable"):
            agent = r["agent"]
            # 整块可点：直接启动 Agent（无按钮）
            glass.bind_click(lambda a=agent: self.start_agent(a))
            glass.rebind_contents()
        else:
            # 详情路径：整卡可点，点了直接弹详情窗。
            # （此前靠「点文字→Label 取焦点」触发，点空白/图标皆无反应，故改此。）
            # **不绑右键** —— 留待未来加「更多操作」菜单。
            glass.bind_click(lambda rr=r: self._open_detail(rr))
            glass.rebind_contents()

    # ---------- 应急卡片（控件缺席时的白卡） ----------
    def _plain_card(self, r, is_agents, LIMIT_DESC, LIMIT_META):
        """没有卡片控件时，退化为白底卡片，绝不空窗。"""
        base_col = self._agent_color(r) if is_agents else cat_liquid(self.cat, 0)
        accent = darken_hex(base_col, 0.34)
        cell = tk.Frame(self.inner, bg=CARD, highlightbackground=EDGE,
                        highlightthickness=1, bd=0)
        # 左缘一道主色竖脊（与主卡片同为 4px，两种卡不打架）
        tk.Frame(cell, bg=base_col, width=max(3, int(4 * UI_SCALE))).pack(
            side="left", fill="y")
        pad = tk.Frame(cell, bg=CARD)
        pad.pack(side="left", fill="both", expand=True, padx=14, pady=12)

        top = tk.Frame(pad, bg=CARD)
        top.pack(fill="x")
        if is_agents:
            ic = self._icon(r.get("icon", ""), _px(34))
            if ic:
                lb = tk.Label(top, bg=CARD, image=ic, bd=0)
                lb.image = ic
                lb.pack(side="left")
        tk.Label(top, text=self._shorten(r["title"], 20), bg=CARD, fg=FG,
                 anchor="w", font=F_CARD_T).pack(side="left", padx=(8, 0))

        tk.Label(pad, text=clip_units(r["desc"], self._desc_budget(_px(360))),
                 bg=CARD, fg=DIM, anchor="w", justify="left", height=2,
                 wraplength=_px(360), font=F_CARD_D).pack(fill="x",
                                                          pady=(SP_2, 0))

        foot = self._shorten(r["meta"], 62) if not is_agents else \
            self._shorten(r["meta"], 70)
        tk.Label(pad, text=foot, bg=CARD, fg=FAINT, anchor="w",
                 justify="left", font=F_MONO).pack(fill="x", pady=(8, 0))
        tk.Label(pad, text="详情 ›", bg=CARD, fg=FAINT, anchor="e",
                 font=F_HINT).pack(fill="x")

        if is_agents:
            agent = r["agent"]
            if r.get("has_works"):
                self._bind_click_all(cell, lambda rr=r: self.open_workspace(rr))
            elif r.get("clickable"):
                # 整个白卡（含所有子孙）都可点，且点击即启（无按钮）
                self._bind_click_all(cell, lambda a=agent: self.start_agent(a))
            else:
                self._bind_click_all(cell, lambda rr=r: self._open_detail(rr))
        else:
            self._bind_click_all(cell, lambda rr=r: self._open_detail(rr))
        return cell

    def _bind_click_all(self, card, fn):
        """递归给整块卡及其子孙绑点击（用于应急白卡）。"""
        stack = [card]
        while stack:
            w = stack.pop()
            try:
                w.bind("<Button-1>", lambda _e, f=fn: f())
                w.configure(cursor="hand2")
            except Exception:
                pass
            try:
                stack.extend(w.winfo_children())
            except Exception:
                pass

    def _bind_detail_all(self, card, row):
        """应急白卡的详情绑定：走自然点击 → Label 取焦点 → 弹详情。"""
        cb = lambda _e, r=row: self._detail_focus(r)
        stack = [card]
        while stack:
            w = stack.pop()
            try:
                w.bind("<FocusIn>", cb)
                w.configure(takefocus=1, cursor="hand2")
            except Exception:
                pass
            try:
                stack.extend(w.winfo_children())
            except Exception:
                pass

    def _bind_card(self, card, row):
        """（保留）旧式整卡点击绑定。"""
        if not row.get("clickable"):
            return
        agent = row["agent"]

        def on_enter(_e):
            card.configure(highlightbackground=INFO)

        def on_leave(_e):
            card.configure(highlightbackground=LINE)

        def on_click(_e):
            self.launch(agent)

        stack = [card]
        while stack:
            w = stack.pop()
            try:
                w.bind("<Enter>", on_enter)
                w.bind("<Leave>", on_leave)
                w.bind("<Button-1>", on_click)
                w.configure(cursor="hand2")
            except Exception:
                pass
            stack.extend(w.winfo_children())

    def launch(self, agent):
        ok, msg, _pid = launch_agent(agent)     # 第三值是进程号，此处不用
        self.status.configure(text=msg)
        if not ok:
            try:
                messagebox.showwarning("无法启动", msg, parent=self)
            except Exception:
                pass

    # ---------- 打开器物 ----------
    def open_path(self, path):
        """在资源管理器里打开某个文件或目录（并尽量选中该文件）。"""
        if not path:
            self.status.configure(text="此项没有可打开的路径。")
            return
        try:
            if os.path.isdir(path):
                os.startfile(path)
            elif os.path.isfile(path):
                subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
            else:
                self.status.configure(text="路径不存在：%s" % path)
                return
            self.status.configure(text="已打开：%s" % path)
        except Exception as e:
            self.status.configure(text="打开失败：%s" % e)

    def open_file(self, path):
        """用系统默认程序打开文件（文档走这条路）。"""
        if not path or not os.path.isfile(path):
            self.status.configure(text="文件不存在：%s" % path)
            return
        try:
            os.startfile(path)
            self.status.configure(text="已打开：%s" % path)
        except Exception as e:
            self.status.configure(text="打开失败：%s" % e)

    # ---------- Agent：整块可点 → 先亮提示窗 → 再启动 ----------
    def start_agent(self, agent):
        """点 Agent 卡：立刻弹「正在启动，请稍后…」小窗，再于后台启动。

        小窗一出，点击便有了回响，作者不至于疑心没点到而反复点。
        """
        if getattr(self, "_launching", False):
            return                       # 已有一次启动在途，不理重复点击
        self._launching = True
        name = agent.get("name", "")
        dlg = self._busy_dialog(name)
        # 开跑之前先给屏幕拍个「窗口快照」—— 之后只认**新冒出来的**窗口，
        # 免得某个早就开着的同名窗口（比如它自己的聊天窗）被当成「启动完成」。
        try:
            before = set(_visible_windows())
        except Exception:
            before = set()

        mailbox = []                     # 子线程只往这里放结果，**不碰 tk**

        def work():
            try:
                mailbox.append(launch_agent(agent))
            except Exception as e:       # 万一炸了，也要把窗收回去
                mailbox.append((False, "启动出错：%s" % e))

        threading.Thread(target=work, daemon=True).start()

        # 由**主线程**轮询信箱：tkinter 的 after/窗口操作都不可跨线程
        def close_dialog():
            try:
                if dlg.winfo_exists():
                    dlg.destroy()
            except Exception:
                pass

        def wait_ui(pid, spins=0):
            """第二段：等它的**界面**出来再收窗。

            从前是一拉起进程就关窗，所以「正在启动」一闪而过 —— 进程起来了
            不等于界面出来了，尤其是 Electron 那类（WorkBuddy / Cursor）要好几秒。
            此处盯三样：自己起的那个 pid 有没有开窗、有没有哪个窗口的进程
            exe 就是它、有没有新窗口的标题带着它的名字（.bat / .lnk 起的最常见）。
            """
            if not self.winfo_exists():
                return
            try:
                started = self._agent_started(agent, pid, before)
            except Exception:
                started = False
            if started:
                close_dialog()
                self._launching = False
                self.status.configure(text="已启动 %s（界面已出来）。" % name)
                return
            # 单实例程序：它已经开着的话，新起的进程会立刻退出、把已有窗口唤到前台 ——
            # 此时永远等不到「新窗口」，再等就是白挂，故给 3 秒宽限后收窗。
            if pid and not _proc_alive(pid) and spins >= 10:
                close_dialog()
                self._launching = False
                self.status.configure(
                    text="已启动 %s（它已在运行，窗口已唤到前台）。" % name)
                return
            # 拿不到进程号的（.lnk 那类）别久等；有进程号的最多等 45 秒
            cap = 150 if pid else 45
            if spins >= cap:
                close_dialog()
                self._launching = False
                self.status.configure(
                    text="已启动 %s（没等到界面，可能它开得慢或本无界面）。" % name)
                return
            try:
                self.after(300, lambda: wait_ui(pid, spins + 1))
            except Exception:
                pass

        def poll(tries=120):
            if not self.winfo_exists():
                return
            if mailbox:
                got = mailbox[0]
                ok, msg = got[0], got[1]
                pid = got[2] if len(got) > 2 else 0
                if ok:
                    # 进程已起 —— 但窗先别关，交给第二段等界面
                    self.status.configure(text=msg + "，正在等它开界面…")
                    wait_ui(pid)
                    return
                close_dialog()
                self._launching = False
                self.status.configure(text=msg)
                try:
                    messagebox.showwarning("无法启动", msg, parent=self)
                except Exception:
                    pass
                return
            if tries <= 0:               # 超时兜底，免得窗永远挂着
                close_dialog()
                self._launching = False
                self.status.configure(text="启动超时，请稍后再试。")
                return
            try:
                self.after(50, lambda: poll(tries - 1))
            except Exception:
                pass

        self.after(50, poll)

    def _agent_started(self, agent, pid, before):
        """那个 Agent 的界面出来了没有？

        只认「快照之后新出现的窗口」，三种认法依次放宽：
          1. 自己起的那个进程开了窗（最准）；
          2. 某个新窗口的所属进程，其可执行文件正是这个 Agent 的 exe；
          3. 新窗口的标题里带着这个 Agent 的名字（.bat / .lnk 起的最常见）。
        """
        cur = _visible_windows()
        fresh = [w for w in cur if w not in before]
        if not fresh:
            return False
        if pid and any(p == pid for p, _t in fresh):
            return True
        exe = agent.get("exe") or ""
        if exe:
            paths = _proc_paths({p for p, _t in fresh})
            want = os.path.normcase(os.path.abspath(exe))
            for _p, path in paths.items():
                if path and os.path.normcase(os.path.abspath(path)) == want:
                    return True
        nm = (agent.get("name") or "").strip().lower()
        if len(nm) >= 3:
            for _p, title in fresh:
                if nm in title.lower():
                    return True
        return False

    def _busy_dialog(self, name):
        """一枚无边框小窗：「正在启动，请稍后…」。

        踩过的坑：`overrideredirect(True)` 与 `attributes("-topmost", True)`
        并用时，窗建了出来却不显影。故此处**不设 topmost**，
        改为 `transient` + 显式 `deiconify()` + `lift()`，稳稳显形。
        """
        dlg = tk.Toplevel(self)
        dlg.overrideredirect(True)
        dlg.configure(bg=LINE)
        try:
            dlg.transient(self)
        except Exception:
            pass

        box = tk.Frame(dlg, bg=CARD)
        box.pack(fill="both", expand=True, padx=1, pady=1)
        tk.Label(box, text="正在启动，请稍后…", bg=CARD, fg=FG,
                 font=F_DLG_B).pack(padx=34, pady=(22, 6))
        tk.Label(box, text=self._shorten(name, 22), bg=CARD, fg=FAINT,
                 font=F_DLG).pack(padx=34, pady=(0, 20))

        dlg.update_idletasks()
        w, h = dlg.winfo_reqwidth(), dlg.winfo_reqheight()
        x = self.winfo_rootx() + (self.winfo_width() - w) // 2
        y = self.winfo_rooty() + (self.winfo_height() - h) // 2
        dlg.geometry("%dx%d+%d+%d" % (w, h, x, y))
        dlg.deiconify()
        dlg.lift()
        # 埋一枚标志，探针与排障脚本据此认出它
        try:
            dlg._busy = True
        except Exception:
            pass
        return dlg

    # ---------- 详情窗 ----------
    # 第五十五轮（本版要求）：_works_dir_of / _update_data / _prompt_float
    #   随「应用内一格工作记录夹」整体删除 —— 本体的东西就地读，不收进应用。

    # ---------- 详情页顶部的「简介栏」 ----------
    def _intro_bar(self, parent, row):
        """把该 Agent 的简介整段摊在页面顶部，右侧配「查看/编辑简介」。

        本版要求（第二十三轮）：卡上不摆编辑入口，编辑挪进点进来的页面；
        同一段简介在此再显示一份全文（卡片上那处只放得下三行）。
        保存后就地刷新，不必关窗重开。
        """
        key = row.get("key") or ""
        if not key or not (row.get("agent") or row.get("kind") == "agent"):
            return                      # 非 Agent 的条目没有简介栏
        text = (self.intros.get(key) or "").strip() or row.get("desc") or "（尚未填写简介）"

        box = tk.Frame(parent, bg=SOFT)
        box.pack(fill="x", padx=22, pady=(0, 10))
        top = tk.Frame(box, bg=SOFT)
        top.pack(fill="x", padx=12, pady=(9, 3))
        tk.Label(top, text="简介", bg=SOFT, fg=INFO, font=F_TAG).pack(side="left")

        lbl = tk.Label(box, text=text, bg=SOFT, fg=FG, font=F_DLG, justify="left",
                       anchor="w", wraplength=_px(780))
        lbl.pack(fill="x", padx=12, pady=(0, 11))

        def saved(t):
            lbl.configure(text=(t or "").strip() or row.get("desc") or "（尚未填写简介）")

        ttk.Button(top, text="查看/编辑简介", style="Tab.TButton",
                   command=lambda k=key, n=row.get("title", ""): self._edit_intro(k, n, saved)
                   ).pack(side="right")

    def _open_detail(self, row):
        """整卡可点 → 打开详情窗。

        防重入：Tk 的 bindtags 链有时会让同一次点击落到不止一处绑定上，
        故设一个极短的窗口把重复的一并吃掉（与 `_wire_detail` 旧法同理）。
        """
        if getattr(self, "_dlg_open", False):
            return
        self._dlg_open = True
        try:
            self._show_detail(row)
        finally:
            def reset():
                self._dlg_open = False
            try:
                self.after(150, reset)
            except Exception:
                self._dlg_open = False

    def _wire_detail(self, glass, row):
        """把整块卡接上「打开详情」。

        不用 `bind_click()` —— 它吞掉点击且不返回 "break"，会挡住 Tk 的默认绑定，
        使焦点永远落不到内容 Label 上。改走自然路径：悬停自己给反馈，
        **点击留给 Tk 默认行为**（Label 取焦点）→ 触发 `_detail_focus`。
        """
        glass.bind_hover_only()
        cb = lambda _e, r=row: self._detail_focus(r)
        for (widget, *_rest) in list(getattr(glass, "_content", [])):
            stack = [widget]
            while stack:
                w = stack.pop()
                if str(w) not in glass._bound:
                    glass._bound.add(str(w))
                    try:
                        w.bind("<Enter>", glass._hover_on)
                        w.bind("<Leave>", glass._hover_off)
                    except Exception:
                        pass
                try:
                    w.bind("<FocusIn>", cb)
                    w.configure(takefocus=1)
                except Exception:
                    pass
                try:
                    stack.extend(w.winfo_children())
                except Exception:
                    pass

    # ---------- 工作台：WorkBuddy 名下的工作区散件 ----------
    def _record_roots_of(self, agent):
        """该 Agent 的**原生记录根**（通配已展开）—— 就地读的来源。

        第五十五轮（本版要求）：应用内那格工作记录夹已废，记录一律直接从这儿读。
        """
        out = []
        try:
            mod = self._scanner_mod()
            roots = mod.record_roots_of(agent) if mod else []
        except Exception:
            roots = []
        for r in (roots or []):
            if r.get("mode") not in ("direct", "digest"):
                continue
            p = r.get("root") or ""
            if not p:
                continue
            if any(c in p for c in "*?["):
                hits = []
                try:
                    hits = mod._expand_roots(p)
                except Exception:
                    try:
                        hits = glob.glob(p)
                    except Exception:
                        hits = []
                out.extend([h for h in (hits or []) if h])
            else:
                out.append(p)
        return out

    def _agent_home(self, agent):
        """这个 Agent **本体所在的目录**（取代原先那格工作记录夹）。

        先取可执行文件所在目录；没有可执行文件时，退回它第一个原生记录根。
        """
        exe = (agent or {}).get("exe") or ""
        if exe:
            d = os.path.dirname(exe)
            if d and os.path.isdir(d):
                return d
        fd = (agent or {}).get("found_dir") or ""      # 按名字找到的那格（可能没有 exe）
        if fd and os.path.isdir(fd):
            return fd
        for r in self._record_roots_of(agent):
            if r and os.path.isdir(r):
                return r
        return ""

    def open_workspace(self, row):
        """打开某个 Agent 的「工作台」：本体在哪、记录都长在哪些位置。

        第五十六轮（本版要求）——按钮收成两枚，地址清单常态摊开：
          · 「打开本体目录」：一键开这个 Agent 所在的**总目录**；
          · 「手动添加」：把原〔＋添加检索地址〕与〔复制自报家门问话〕并成一处
            （本就是同一件事：不认识它的目录结构时，让 Agent 自己把位置报出来），
            点开即摊开一层提示 —— 可复制的提示词 + 「选择目录加入…」。
        清单**不再区分内置 / 手工**：都是它记录所在之处，一律就地读、不复制。
        """
        agent = row.get("agent") or {}

        win = tk.Toplevel(self)
        win.title("工作台 · %s" % agent.get("name", ""))
        win.configure(bg=BG)
        win.geometry("%dx%d" % self._fit(_px(920), _px(700)))
        win.minsize(_px(520), _px(400))
        try:
            win.transient(self)
        except Exception:
            pass

        # 顶栏
        head = tk.Frame(win, bg=BG)
        head.pack(fill="x", padx=22, pady=(18, 4))
        ic = self._icon(agent.get("icon", ""), 30)
        if ic:
            lb = tk.Label(head, bg=BG, image=ic, bd=0)
            lb.image = ic
            lb.pack(side="left", padx=(0, 8))
        tk.Label(head, text="工作台 · %s" % agent.get("name", ""), bg=BG, fg=FG,
                 font=F_DLG_T, anchor="w").pack(side="left")
        tk.Label(head, text="本 Agent 在本机的工作区文件", bg=SOFT, fg=INFO,
                 font=F_TAG).pack(side="left", padx=(10, 0))
        # 第五十四轮（本版要求）：启动键在页头，紧挨标题那一行。
        if agent.get("exe"):
            # 第六十三轮（本版要求）：启动是这一页的**主操作** —— 实心近黑 + 白字、
            #   比常规按钮大一号；因此下方工具条一律改成描边，一屏只有这一处实心。
            _go = ttk.Button(head, text=u"\u25b6  启动 %s" % agent.get("name", ""),
                             style="Primary.TButton",
                             command=lambda a=agent: self.start_agent(a))
            _go.pack(side="left", padx=(18, 0))
            self._tip(_go, u"启动它本体；打不开会告诉你为什么")
        # 右上角：说明这一页的记录是怎么来的
        tk.Label(head,
                 text=self._integration_line(agent),
                 bg=BG, fg=FAINT, font=F_HINT, justify="right"
                 ).pack(side="right")

        # 顶部：同一段简介再显示一份
        self._intro_bar(win, row)

        tk.Label(win, text="本体目录：%s"
                 % (self._agent_home(agent) or "（未登记可执行文件）"),
                 bg=BG, fg=FAINT, font=F_MONO, anchor="w"
                 ).pack(fill="x", padx=22, pady=(2, 10))

        # 工具条：只留「打开本体目录」「手动添加」（第五十六轮）
        bar = tk.Frame(win, bg=BG)
        bar.pack(fill="x", padx=22, pady=(0, 8))
        ttk.Button(bar, text="打开本体目录", style="Tab.TButton",
                   command=lambda: self.open_path(self._agent_home(agent) or HERE)
                   ).pack(side="left")
        _add_btn = ttk.Button(bar, text="手动添加检索地址", style="Tab.TButton",
                              command=lambda: toggle_add())
        _add_btn.pack(side="left", padx=(6, 0))
        self._tip(_add_btn, u"添加记忆信息供其他 agent 浏览")
        # 第五十九轮（本版要求）：从首页搬过来的「接入 Agent」，在这儿只办这一个
        _onb_btn = ttk.Button(bar, text="接入 Agent", style="Tab.TButton",
                              command=lambda: self.onboard_dialog(agent))
        _onb_btn.pack(side="left", padx=(6, 0))
        self._tip(_onb_btn, u"只给这个 Agent：接 MCP 检索、在它每次必读处放一行指针，"
                            u"并给你可复制的测试提示词")
        # 第六十四轮（本版要求）：一键把它的文件地址重新检索一遍
        _scan_btn = ttk.Button(bar, text="自动检索", style="Tab.TButton",
                               command=lambda: auto_scan())
        _scan_btn.pack(side="left", padx=(6, 0))
        self._tip(_scan_btn, u"重新枚举这个 Agent 的记录位置，并在它本体目录下"
                             u"找一层候选目录（找到的可以一键登记）")
        ttk.Button(bar, text="关闭", style="Tab.TButton",
                   command=win.destroy).pack(side="right")

        # 第五十六轮：「手动添加」那一层提示留个空框在这儿，展开时才长东西
        hint_box = tk.Frame(win, bg=BG)   # 先不占位：摊开提示时才 pack 到工具条下面

        # 正文（可滚动清单）—— 常态摊开，不再收 / 放
        body = tk.Frame(win, bg=BG)
        body.pack(fill="both", expand=True, padx=22, pady=(0, 16))
        txt = tk.Text(body, wrap="word", bg=CARD, fg=FG, bd=0,
                      highlightthickness=1, highlightbackground=LINE,
                      font=F_DLG, padx=16, pady=14, spacing1=2, spacing3=5,
                      cursor="arrow")
        bar2 = ttk.Scrollbar(body, orient="vertical", command=txt.yview)
        txt.configure(yscrollcommand=bar2.set)
        bar2.pack(side="right", fill="y")
        txt.pack(side="left", fill="both", expand=True)

        txt.tag_configure("h", font=F_SEC, foreground=FG, spacing1=12, spacing3=6)
        txt.tag_configure("name", font=F_DLG_M, foreground=FG)
        txt.tag_configure("meta", font=F_HINT, foreground=FAINT)
        txt.tag_configure("role", font=F_TAG, foreground=INFO)
        txt.tag_configure("link", font=F_DLG_M, foreground=INFO, underline=True,
                          lmargin1=_px(20), lmargin2=_px(20))
        txt.tag_configure("desc", font=F_DLG, foreground=DIM, lmargin1=20, lmargin2=20)
        # 第五十八轮（本版要求：美化这一栏）—— 原先 h2 / sub / sessT / sessLab / hair
        #   一个都没配样式，标题、路径、备注、分隔线全落回默认字体与纯黑，
        #   层级看不出来、线也太重。今按「标题—路径—备注」三级重排，
        #   发丝线改用描边色，间距走 8 的倍数。
        txt.tag_configure("h2", font=F_SEC, foreground=FG, spacing1=2, spacing3=2)
        txt.tag_configure("h2n", font=F_HINT, foreground=FAINT, spacing1=2, spacing3=6)
        txt.tag_configure("sub", font=F_DLG, foreground=DIM, spacing3=16)
        txt.tag_configure("sessT", font=F_DLG_M, foreground=FG, spacing1=12, spacing3=4)
        txt.tag_configure("sessLab", font=F_HINT, foreground=FAINT,
                          lmargin1=_px(20), lmargin2=_px(20), spacing3=14)
        txt.tag_configure("hair", font=F_HINT, foreground=LINE, spacing3=14)
        # 「复制」小标：一枚浅浅的小胶囊（不占新行，背景只覆盖这三个字）
        txt.tag_configure("copy", font=F_HINT, foreground=DIM, background=SOFT)
        txt.tag_configure("reg", font=F_HINT, foreground=INFO, background=SOFT)
        txt.tag_configure("undo", font=F_HINT, foreground=INFO, underline=True)

        line_map = {}       # 行号 → ("open", 路径)
        manual_map = {}     # 行号 → 手工登记的路径（右键可移除）
        copy_map = {}       # 行号 → 这一行要复制的地址
        reg_map = {}        # 行号 → (候选路径, 建议处理方式)：点「登记」加进来

        def _resolve(p2):
            """带通配的地址（如 ~/WorkBuddy/*/.workbuddy/memory）→ 取一个真实位置。"""
            if not p2:
                return ""
            if any(c in p2 for c in "*?["):
                try:
                    hits = sorted(glob.glob(p2))
                    if hits:
                        return hits[0]
                except Exception:
                    pass
                return os.path.dirname(p2.split("*")[0].rstrip("\\/"))
            return p2

        def on_click(_e):
            """点路径 → 打开那个位置；点「复制」小标 → 把这条地址拷进剪贴板。"""
            try:
                # 按**鼠标坐标**取位置：Tk 是在本绑定之后才挪 insert 的，
                # 用 insert 会拿到上一次点击的旧位置（点哪都跳回上一次）。
                try:
                    _ix = txt.index("@%d,%d" % (_e.x, _e.y))
                except Exception:
                    _ix = txt.index("insert")
                _tags = txt.tag_names(_ix)
                _ln0 = int(str(_ix).split(".")[0])
                if "undo" in _tags:                     # 点「恢复」
                    _unhide_all()
                    return "break"
                if "reg" in _tags:                      # 点「登记」把候选加进来
                    _p3, _m3 = reg_map.get(_ln0, ("", "direct"))
                    if _p3:
                        try:
                            _mod3 = self._scanner_mod()
                            _d3 = _mod3.load_extra_roots()
                            _d3.setdefault(_key, []).append({
                                "path": _p3, "mode": _m3,
                                "name": os.path.basename(_p3.rstrip("\\/")) or _p3})
                            _mod3.save_extra_roots(_d3)
                            _mod3.invalidate_roots()
                            self.status.configure(text=u"已登记：%s（%s）" % (_p3, _m3))
                        except Exception as e:
                            self.status.configure(text=u"登记失败：%s" % e)
                        _reopen()
                    return "break"
                if "copy" in _tags:                     # 点在「复制」上
                    _p2 = copy_map.get(int(str(_ix).split(".")[0]))
                    if _p2:
                        self.clipboard_clear()
                        self.clipboard_append(_p2)
                        self.status.configure(text=u"已复制地址：%s" % _p2)
                    return "break"
                ln = int(str(_ix).split(".")[0])
                v = line_map.get(ln)
                if isinstance(v, tuple) and v and v[0] == "open":
                    t2 = _resolve(v[1])
                    if t2 and os.path.isdir(t2):
                        self.open_path(t2)
                    elif t2 and os.path.isfile(t2):
                        self.open_file(t2)
            except Exception:
                pass
            return "break"

        def on_rclick(_e):
            """右键点在**任何一条**地址上 → 删掉它。

            第六十四轮（本版要求）：手工登记的从登记表里删；**内置的**记进「隐藏」表
            —— 从此不再登记、也不再读它，随时可恢复。
            """
            try:
                ln = int(str(txt.index("@%d,%d" % (_e.x, _e.y))).split(".")[0])
            except Exception:
                return "break"
            if ln in reg_map:                 # 候选还没登记，用不着删
                self.status.configure(text=u"这是候选目录，还没登记，不必删除。")
                return "break"
            p2 = manual_map.get(ln)
            if not p2:
                v2 = line_map.get(ln)
                if isinstance(v2, tuple) and v2 and v2[0] == "open":
                    p2 = v2[1]
            if p2:
                remove_one(p2)
            return "break"

        def _row(label, path, note=u"", manual=u""):
            """一行地址：标签 → 路径（点开即到）→ 备注 → 细线。"""
            ln = int(str(txt.index("insert")).split(".")[0])
            line_map[ln] = ("open", path)
            if manual:
                manual_map[ln] = manual
            txt.insert("end", label + "\n", "sessT")
            ln = int(str(txt.index("insert")).split(".")[0])
            line_map[ln] = ("open", path)
            if manual:
                manual_map[ln] = manual
            txt.insert("end", path, "link")
            txt.insert("end", u"  ", "")
            txt.insert("end", u" 复制 ", "copy")     # 点它就拷这一条地址
            txt.insert("end", "\n", "")
            copy_map[ln] = path
            if note:
                txt.insert("end", "  " + note + "\n", "sessLab")
            txt.insert("end", "─" * 68 + "\n", "hair")

        def _cand_row(name, path, pr):
            """自动检索找到的候选：一行 + 「登记 / 复制」两枚小标。"""
            ln = int(str(txt.index("insert")).split(".")[0])
            line_map[ln] = ("open", path)
            txt.insert("end", name + "\n", "sessT")
            ln = int(str(txt.index("insert")).split(".")[0])
            line_map[ln] = ("open", path)
            reg_map[ln] = (path, (pr or {}).get("suggest") or "direct")
            txt.insert("end", path, "link")
            txt.insert("end", u"  ", "")
            txt.insert("end", u" 登记 ", "reg")
            txt.insert("end", u" 复制 ", "copy")
            txt.insert("end", "\n", "")
            copy_map[ln] = path
            txt.insert("end", u"  候选 ｜ %s 个文件 ｜ %.1f MB\n"
                       % (pr.get("files", u"?"), (pr.get("bytes") or 0) / 1048576),
                       "sessLab")

        _key = ((agent or {}).get("key") or agent.get("name") or "").strip().lower()

        def _reopen():
            try:
                win.destroy()
            except Exception:
                pass
            self.open_workspace(row)

        def _ask_text():
            """给 Agent 的「自报家门」问话（有现成的 SKILL 就取那一段）。"""
            try:
                fp2 = os.path.join(HERE, u"通用资源", u"skills", u"agent-self-report",
                                   u"SKILL.md")
                t2 = open(fp2, encoding="utf-8").read()
                i2 = t2.find(u"请只做一件事")
                return t2[i2:] if i2 > 0 else t2
            except Exception:
                return (u"请只做一件事，不要做任何别的操作：如实告诉我，你自己的记忆/"
                        u"会话记录/工作记录都存放在哪些目录（绝对路径、文件数、体量、"
                        u"文本还是二进制）。不确定就说不确定，不要编。")

        def copy_ask():
            t2 = _ask_text()
            self.clipboard_clear()
            self.clipboard_append(t2)
            self.status.configure(text=u"「自报家门」问话已复制 —— 粘给这个 Agent 试试。")

        def add_addr():
            import tkinter.filedialog as _fd
            try:
                _mod = self._scanner_mod()
            except Exception:
                return
            p2 = _fd.askdirectory(parent=win, title=u"选这个 Agent 记忆/记录所在目录")
            if not p2:
                return
            pr = _mod.probe_path(p2) or {}
            mode = pr.get("suggest") or "direct"
            if not messagebox.askyesno(
                    u"加入检索",
                    u"路径：%s\n\n探测：%s 个文件 ｜ %.2f MB\n主要格式：%s\n\n"
                    u"建议处理：%s\n\n就按这个加入？"
                    % (p2, pr.get("files", u"?"), (pr.get("bytes") or 0) / 1048576,
                       u"、".join(u"%s×%d" % (e, n) for e, n in (pr.get("exts") or [])[:4]),
                       u"摘录（体量大或不全是纯文本）" if mode == "digest" else u"直读"),
                    parent=win):
                return
            d2 = _mod.load_extra_roots()
            d2.setdefault(_key, []).append({
                "path": p2, "mode": mode,
                "name": os.path.basename(p2.rstrip("\\/")) or p2})
            _mod.save_extra_roots(d2)
            _mod.invalidate_roots()
            self.status.configure(text=u"已登记检索地址：%s（%s）" % (p2, mode))
            _reopen()

        def _unhide_all():
            """恢复这个 Agent 被删掉的内置记录根。"""
            try:
                _mod = self._scanner_mod()
                _h = _mod.load_hidden_roots()
                _h[_key] = []
                _mod.save_hidden_roots(_h)
                _mod.invalidate_roots()
                self.status.configure(text=u"已恢复这个 Agent 被删掉的记录根。")
            except Exception as e:
                self.status.configure(text=u"恢复失败：%s" % e)
            _reopen()

        def auto_scan():
            """自动检索：清缓存 → 重新枚举它的记录根 → 探一遍路径，
            再在它**本体目录**下扫一层，挑出像记录目录的候选给你一键登记。"""
            try:
                _mod = self._scanner_mod()
            except Exception:
                _mod = None
            if _mod is None:
                self.status.configure(text=u"扫描器不可用，检索不了。")
                return
            try:
                _mod.invalidate_roots()
            except Exception:
                pass
            self.status.configure(text=u"正在检索 %s 的文件地址…" % (agent.get("name") or ""))
            try:
                win.update_idletasks()
            except Exception:
                pass
            rows, files = [], 0
            try:
                for r in _mod.record_roots_of(agent):
                    pr = _mod.probe_path(r.get("root")) or {}
                    rows.append(r)
                    files += pr.get("files") or 0
            except Exception:
                pass
            cands = []
            try:
                known = set()
                for r in (self._record_roots_of(agent) or []):
                    known.add(os.path.normcase(str(r).lower()))
                for it in ((_mod.load_extra_roots() or {}).get(_key) or []):
                    known.add(os.path.normcase(str(it.get("path") or "").lower()))
                KREC = ("memory", "memories", "record", "records", "log", "logs",
                        "history", "session", "sessions", "data", "workspace",
                        "workspaces", "project", "projects", "output", "outputs",
                        u"记忆", u"记录", u"日志", u"会话", u"笔记", u"历史", u"输出")
                home = self._agent_home(agent)
                if home and os.path.isdir(home):
                    for sub2 in sorted(os.listdir(home)):
                        fp = os.path.join(home, sub2)
                        if not os.path.isdir(fp):
                            continue
                        if os.path.normcase(fp.lower()) in known:
                            continue
                        low = sub2.lower()
                        if not any(k in low for k in KREC):
                            continue
                        pr = _mod.probe_path(fp) or {}
                        if not (pr.get("files") or 0):
                            continue
                        cands.append((sub2, fp, pr))
            except Exception:
                cands = []
            self._ws_report = {"key": _key, "roots": len(rows), "files": files,
                               "cands": cands}
            self.status.configure(
                text=u"自动检索完成：%d 个记录根 ｜ 共 %d 个文件 ｜ 发现 %d 个候选目录"
                     % (len(rows), files, len(cands)))
            _reopen()

        def remove_one(path):
            """删掉一条地址：手工登记的从登记表里删；**内置的**记进「隐藏」表
            —— 从此不再登记、也不再读它，随时可恢复。"""
            try:
                _mod = self._scanner_mod()
                d2 = _mod.load_extra_roots()
            except Exception:
                return
            lst = d2.get(_key) or []
            hit = None
            for i2, it in enumerate(lst):
                if os.path.normcase(it.get("path") or "") == os.path.normcase(path or ""):
                    hit = i2
                    break
            if hit is None:
                # 内置的记录根：记进「隐藏」表（真的不再读它）
                if not messagebox.askyesno(
                        u"删除地址",
                        u"这是**内置登记**的记录根：\n%s\n\n"
                        u"删掉后本应用不再登记、也不再读它（随时可点下面的「恢复」找回）。\n\n"
                        u"确定删？" % path, parent=win):
                    return
                try:
                    _h = _mod.load_hidden_roots()
                    _lst = _h.setdefault(_key, [])
                    if path not in _lst:
                        _lst.append(path)
                    _mod.save_hidden_roots(_h)
                    _mod.invalidate_roots()
                    self.status.configure(text=u"已删除（不再读取）：%s" % path)
                    _reopen()
                except Exception as e:
                    self.status.configure(text=u"删除失败：%s" % e)
                return
            if not messagebox.askyesno(u"移除", u"移除这条登记的地址？\n%s" % path,
                                       parent=win):
                return
            lst.pop(hit)
            d2[_key] = lst
            _mod.save_extra_roots(d2)
            _mod.invalidate_roots()
            self.status.configure(text=u"已移除：%s" % path)
            _reopen()

        def toggle_add():
            """「手动添加检索地址」：摊开 / 收起那一层提示（提示词 + 选目录入口）。

            第五十七轮（本版要求）：收起时**连位子一起撤**（pack_forget）——
            先前只销毁里面的东西，空 Frame 还占着原本那块高度，中间就空一块。
            """
            if hint_box.winfo_manager():
                hint_box.pack_forget()
                try:
                    win.update_idletasks()
                except Exception:
                    pass
                return
            for c in hint_box.winfo_children():
                c.destroy()
            card = tk.Frame(hint_box, bg=CARD, highlightthickness=1,
                            highlightbackground=LINE)
            card.pack(fill="x")
            tk.Label(card,
                     text=u"不确定它的工作目录在哪？把下面这段提示词粘给这个 Agent，"
                          u"它会把自己记录所在的目录全报给你；\n"
                          u"拿到之后，逐个用「选择目录加入…」登记进来就行。",
                     bg=CARD, fg=DIM, font=F_HINT, justify="left", anchor="w"
                     ).pack(fill="x", padx=12, pady=(10, 6))
            t = tk.Text(card, wrap="word", bg="#ffffff", fg=FG, bd=0, height=5,
                        highlightthickness=1, highlightbackground=LINE,
                        font=F_DLG, padx=10, pady=8, cursor="arrow")
            t.insert("1.0", _ask_text())
            t.configure(state="disabled")
            t.pack(fill="x", padx=12)
            row2 = tk.Frame(card, bg=CARD)
            row2.pack(fill="x", padx=12, pady=(8, 10))
            ttk.Button(row2, text="复制提示词", style="Tab.TButton",
                       command=lambda: copy_ask()).pack(side="left")
            ttk.Button(row2, text="选择目录加入…", style="Act.TButton",
                       command=lambda: add_addr()).pack(side="left", padx=(6, 0))
            ttk.Button(row2, text="收起", style="Tab.TButton",
                       command=lambda: toggle_add()).pack(side="right")
            # 摊在工具条与清单之间（before=body），不是塞在最底下
            try:
                hint_box.pack(fill="x", padx=22, pady=(0, 6), before=body)
            except Exception:
                hint_box.pack(fill="x", padx=22, pady=(0, 6))
            try:
                win.update_idletasks()
            except Exception:
                pass

        # ---- 清单：内置与手工**一视同仁**，同一条流水排下去 ----
        _rows = []
        try:
            _mod = self._scanner_mod()
            for r in (_mod.record_roots_of(agent) if _mod else []):
                _mode = {"direct": u"直接读", "digest": u"摘录",
                         "digest_sqlite": u"摘录", "skip": u"只登记（格式读不了）"}.get(
                             r.get("mode"), r.get("mode") or "")
                _ext = u"、".join(r.get("ext") or [])
                _rows.append((r.get("name") or u"（未命名）", r.get("root") or "",
                              u"%s%s ｜ %s 个文件"
                              % (_mode, (u"（%s）" % _ext) if _ext else u"",
                                 r.get("count") or 0), u""))
        except Exception:
            _rows = []
        _extra = []
        try:
            _extra = (self._scanner_mod().load_extra_roots() or {}).get(_key) or []
        except Exception:
            _extra = []
        for _it in _extra:
            _pr = {}
            try:
                _pr = self._scanner_mod().probe_path(_it.get("path")) or {}
            except Exception:
                _pr = {}
            _p = _it.get("path") or ""
            _nm2 = _it.get("name") or os.path.basename(_p.rstrip("\\/")) or _p
            _rows.append((_nm2, _p,
                          u"%s ｜ %s 个文件%s"
                          % (u"直读" if (_it.get("mode") or "direct") == "direct" else u"摘录",
                             _pr.get("files", u"?"),
                             u"" if _pr.get("exists", True) else u"　⚠ 路径不存在"),
                          _p))
        txt.insert("end", u"数据文件地址", "h2")
        txt.insert("end", u"　　共 %d 处\n" % len(_rows), "h2n")
        txt.insert("end", u"本 Agent 的记录都长在这些位置，一律就地读、不复制；"
                          u"点任一行路径即打开该位置。\n", "sub")
        # 同一个目录可能被登记了**两种读法**（如 WorkBuddy 的 projects：
        #   .jsonl 存档走摘录、.md/.txt 走直读）—— 合并成一行显示，
        #   备注里把两种处理都写清楚，免得看着像重复（本版要求）。
        _groups = []
        for _r in _rows:
            _norm = os.path.normcase((_r[1] or "").rstrip("\\/").lower())
            for _g in _groups:
                if _g["norm"] == _norm:
                    _g["rows"].append(_r)
                    break
            else:
                _groups.append({"norm": _norm, "rows": [_r]})
        for _g in _groups:
            if len(_g["rows"]) == 1:
                _row(*_g["rows"][0])
                continue
            _nms = [x[0] for x in _g["rows"]]
            _pre = os.path.commonprefix(_nms)
            if len(_pre) >= 4:
                _name2 = _pre + u" ＋ ".join(n[len(_pre):] for n in _nms)
            else:
                _name2 = u" ＋ ".join(_nms)
            _note2 = (u" ｜ ".join(x[2] for x in _g["rows"]) +
                      u"（同一目录的两种读法）")
            _row(_name2, _g["rows"][0][1], _note2, _g["rows"][0][3])
        _rep = getattr(self, "_ws_report", None)
        if _rep and _rep.get("key") == _key and _rep.get("cands"):
            _known2 = set()
            try:
                for _it2 in ((self._scanner_mod().load_extra_roots() or {}).get(_key) or []):
                    _known2.add(os.path.normcase(str(_it2.get("path") or "").lower()))
            except Exception:
                pass
            _todo = [_c for _c in _rep["cands"]
                     if os.path.normcase(_c[1].lower()) not in _known2]
            if _todo:
                txt.insert("end", u"\n自动检索找到的候选目录（还没登记）\n", "h2")
                txt.insert("end", u"登记前请确认它确实是这个 Agent 记东西的地方。\n", "sub")
                for _nm3, _p3, _pr3 in _todo:
                    _cand_row(_nm3, _p3, _pr3)
        try:
            _hid = (self._scanner_mod().load_hidden_roots() or {}).get(_key) or []
        except Exception:
            _hid = []
        if _hid:
            txt.insert("end", u"\n已删除 %d 条内置地址 —— " % len(_hid), "sessLab")
            txt.insert("end", u"恢复", "undo")
            txt.insert("end", u"\n", "")
        txt.insert("end", u"点路径即打开该位置 ｜ 点「复制」拷地址 ｜ "
                          u"任何一条都可右键删除。\n", "sessLab")

        txt.configure(state="disabled")
        txt.bind("<Button-1>", on_click)
        txt.bind("<Button-3>", on_rclick)
        win.bind("<Escape>", lambda _e: win.destroy())
        return win

    def _detail_focus(self, row):
        """焦点落上即开详情；用短延迟挡掉重复开窗。"""
        if getattr(self, "_dlg_open", False):
            return
        self._dlg_open = True

        def go():
            try:
                self._show_detail(row)
            finally:
                def reset():
                    self._dlg_open = False
                try:
                    self.after(150, reset)
                except Exception:
                    self._dlg_open = False
        try:
            self.after(1, go)
        except Exception:
            go()

    def _show_detail(self, row):
        """详情窗：标题、种类、来源、路径，以及尽可能的长描述 + SKILL.md 原文。"""
        win = tk.Toplevel(self)
        win.title("详情 · %s" % row.get("title", ""))
        win.configure(bg=BG)
        win.geometry("%dx%d" % self._fit(_px(860), _px(680)))
        win.minsize(_px(480), _px(380))
        try:
            win.transient(self)
        except Exception:
            pass

        # 顶栏
        head = tk.Frame(win, bg=BG)
        head.pack(fill="x", padx=22, pady=(18, 6))
        tk.Label(head, text=row.get("title", ""), bg=BG, fg=FG,
                 font=F_DLG_T, anchor="w").pack(side="left")

        kinds = [t for t in (row.get("tags") or []) if t]
        if row.get("extra"):
            kinds.insert(0, row["extra"])
        if kinds:
            tk.Label(head, text="  " + " · ".join(kinds) + "  ", bg=SOFT,
                     fg=darken_hex(INFO, 0.18), font=F_TAG).pack(side="left", padx=(10, 0))
        # 第五十五轮：「更新数据」按钮已撤（该机制废除）
        if row.get("meta"):
            tk.Label(head, text="路径", bg=BG, fg=FAINT, font=F_HINT).pack(side="right")

        # 顶部：同一段简介再显示一份（Agent 才有）
        self._intro_bar(win, row)

        # 正文（可滚动）
        body = tk.Frame(win, bg=BG)
        body.pack(fill="both", expand=True, padx=22, pady=(4, 14))
        txt = tk.Text(body, wrap="word", bg=CARD, fg=FG, bd=0, height=10,
                      highlightthickness=1, highlightbackground=LINE,
                      font=F_DLG, padx=16, pady=14, spacing1=2, spacing3=5)
        bar = ttk.Scrollbar(body, orient="vertical", command=txt.yview)
        txt.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        txt.pack(side="left", fill="both", expand=True)

        # 文本样式
        txt.tag_configure("h", font=F_SEC, foreground=FG, spacing1=12, spacing3=6)
        txt.tag_configure("lbl", font=F_TAG, foreground=INFO)
        txt.tag_configure("desc", font=F_DLG, foreground=FG, spacing3=6)
        txt.tag_configure("mono", font=F_DLG_M, foreground=DIM, lmargin1=2, lmargin2=12)
        txt.tag_configure("dim", font=F_HINT, foreground=FAINT)
        txt.tag_configure("rule", font=("Consolas", 4), foreground=LINE)

        def sec(title):
            txt.insert("end", "\n" + title + "\n", "h")

        def field(label, value):
            if value in (None, "", []):
                return
            txt.insert("end", label + "：", "lbl")
            txt.insert("end", str(value) + "\n", "desc")

        txt.insert("end", "\n")
        agent = row.get("agent") if isinstance(row.get("agent"), dict) else None

        # 主要项目：开发日志（按时间）+ 已实现的功能 + 产物地址
        if row.get("proj"):
            pr = row["proj"]
            hit = row.get("_tasks") or []
            # 第六十一轮（本版要求）：**人工清单优先** —— 主要项目就是「用户手选的整理层」，
            #   自动摘录只当线索；哪一段是人工的，页面上直接标出来。
            _man = pr.get("features") if isinstance(pr.get("features"), list) else []
            _man = [str(x).strip() for x in _man if str(x).strip()]
            feats = self._project_features(pr, hit)
            sec("已实现的功能" + ("（我划的重点）" if _man else "（自动摘录，供定位）"))
            if feats:
                for _f in feats[:12]:
                    txt.insert("end", "  · " + _f + "\n", "desc")
                if not _man:
                    txt.insert("end", "（上面是应用从记录里自动摘出的片段，只当线索用；"
                                      "要准的结论，点右边「编辑我划的重点」自己写一份。）\n", "dim")
            else:
                txt.insert("end", "（还没摘出功能条目 —— 点右边「编辑我划的重点」写一份，"
                                  "那份会优先显示）\n", "dim")
            sec("开发日志（%d 条，自动摘录，按时间倒序）" % len(hit))
            for _i, _t in enumerate(hit, 1):
                txt.insert("end", "  %d. %s　" % (_i, _t.get("when") or "—"), "lbl")
                txt.insert("end", (_t.get("title") or "")[:70] + "\n", "desc")
                _d = str(_t.get("did") or "")[:180]
                if _d:
                    txt.insert("end", "      " + _d + "\n", "dim")
                for _a in (_t.get("artifacts") or [])[:6]:
                    txt.insert("end", "      产物： " + _a + "\n", "mono")
                txt.insert("end", "      日志： " + str(_t.get("log") or "") + "\n", "mono")
            if not hit:
                txt.insert("end", "（还没匹配到记录。关键词写宽一点试试，"
                                  "或先点「抄录日志」。）\n", "dim")
            txt.configure(state="disabled")
            _foot = tk.Frame(win, bg=BG)
            _foot.pack(fill="x", padx=22, pady=(0, 16))
            ttk.Button(_foot, text="关闭", style="Tab.TButton",
                       command=win.destroy).pack(side="right")
            _clip = [a for t in hit for a in (t.get("artifacts") or [])]
            if _clip:
                ttk.Button(_foot, text="打开最新产物所在目录", style="Tab.TButton",
                           command=lambda _p=os.path.dirname(_clip[0]): self.open_path(_p)
                           ).pack(side="right", padx=(0, 6))
            ttk.Button(_foot, text="编辑我划的重点…", style="Act.TButton",
                       command=lambda _r=row: self._edit_project_features(
                           _r["proj"],
                           after=lambda: (win.destroy(), self._show_detail(_r)))
                       ).pack(side="right", padx=(0, 6))
            ttk.Button(_foot, text="设置主要项目", style="Tab.TButton",
                       command=self.projects_dialog).pack(side="right", padx=(0, 6))
            try:
                win.bind("<Escape>", lambda _e: win.destroy())
            except Exception:
                pass
            return

        # 工作任务：单开一种画法 —— 做了什么 / 谁参与 / 产物在哪（给全地址）
        if row.get("task"):
            tk_ = row["task"]
            arts = tk_.get("artifacts") or []
            sec("这次做了什么")
            txt.insert("end", (tk_.get("did") or "（无记录）") + "\n", "desc")
            sec("参与的 Agent")
            txt.insert("end", "、".join(tk_.get("participants") or []) + "\n", "desc")
            sec("产物（%d 件）" % len(arts))
            if arts:
                for _i, _p in enumerate(arts, 1):
                    txt.insert("end", "  %d. " % _i, "lbl")
                    txt.insert("end", _p + "\n", "mono")
                    try:
                        _sz = os.path.getsize(_p)
                        txt.insert("end", "     %.1f KB ｜ %s\n"
                                   % (_sz / 1024.0,
                                      time.strftime("%Y-%m-%d %H:%M",
                                                    time.localtime(os.path.getmtime(_p)))),
                                   "dim")
                    except Exception:
                        pass
            else:
                txt.insert("end", "本次会话没有留下产物文件。\n", "dim")
            sec("原始日志")
            txt.insert("end", (tk_.get("log") or "") + "\n", "mono")
            txt.insert("end", "时间：%s　来源：%s\n"
                       % (tk_.get("when") or "—", tk_.get("source") or "—"), "dim")
            txt.configure(state="disabled")
            foot = tk.Frame(win, bg=BG)
            foot.pack(fill="x", padx=22, pady=(0, 16))
            ttk.Button(foot, text="关闭", style="Tab.TButton",
                       command=win.destroy).pack(side="right")
            if arts:
                _d0 = os.path.dirname(arts[0])
                ttk.Button(foot, text="打开产物所在目录", style="Tab.TButton",
                           command=lambda _p=_d0: self.open_path(_p)
                           ).pack(side="right", padx=(0, 6))
            _lg = tk_.get("log") or ""
            if _lg:
                ttk.Button(foot, text="打开日志", style="Tab.TButton",
                           command=lambda _p=_lg: (self.open_file(_p)
                                                   if os.path.isfile(_p)
                                                   else self.open_path(_p))
                           ).pack(side="right", padx=(0, 6))
            try:
                win.bind("<Escape>", lambda _e: win.destroy())
            except Exception:
                pass
            return

        # 整合卡：先把它怀里那几篇列清楚 —— 这是「一张卡装一套」的门面
        if row.get("_members"):
            _memb = row.get("_members") or []
            sec("整合卡 · 内含 %d 篇同源技能（%s）" % (len(_memb), row.get("_suite") or ""))
            txt.insert("end",
                       "这是一张**整合卡**：下列 %d 篇同源技能都收在它里面，"
                       "不在技能栏里单独露面。要看某一篇，直接在搜索框敲它的名字即可"
                       "（一搜就露出来）。\n" % len(_memb), "dim")
            _root = os.path.dirname(row.get("meta") or "")
            for _i, _sub in enumerate(_memb, 1):
                _nm, _ds = _skill_brief(os.path.join(_root, _sub))
                txt.insert("end", "  %2d. %s　" % (_i, _nm), "lbl")
                txt.insert("end", (self._shorten(_ds, 88) if _ds else "（无说明）") + "\n",
                           "dim")

        # 说明文档：正文就是那篇长文，直接整篇铺上
        if agent and agent.get("kind") == "doc":
            text = agent.get("text") or ""
            if text:
                sec("文档正文")
                txt.insert("end", text, "mono")
            else:
                field("说明", row.get("desc"))
            field("原文件", agent.get("path") or row.get("meta"))
            txt.configure(state="disabled")
            return self._detail_buttons(win, row, agent)

        full = self._full_desc(row)
        if full:
            field("简介", full)
        field("来源", row.get("_source") or (agent or {}).get("source"))
        field("版本", row.get("_version"))
        field("作者", row.get("_author"))
        field("许可", row.get("_license"))
        if row.get("extra"):
            field("备注", row["extra"])
        if row.get("meta"):
            field("路径", row.get("meta"))
        # 安卓兵站的家底：原「工具与运行时」栏里唯一对干活有帮助的信息，
        # 现并到它对应的 MCP 条目底下（本版要求：删栏，有用的并过来）。
        _cmd = row.get("meta") or ""
        if row.get("title") == "replicant" and "安卓模拟器MCP" in _cmd:
            _root = os.path.dirname(os.path.dirname(_cmd))
            sec("安卓兵站")
            field("工具链根目录", _root)
            field("自带运行时", "Node 22.23.2 · JDK 17 · Android SDK"
                              "（adb 1.0.41 / emulator 37.1.11.0）· scrcpy · 不依赖系统 PATH")
            field("可干之事", "14 个工具：装/卸/启停 App、按无障碍树点控件、筛 logcat、"
                            "管模拟器、编译跑测试")
            field("旁支入口", "安卓操作台：%s\n dockerify-android（备用路线，需 Docker/WSL2）：%s"
                 % (os.path.join(_root, "console", "run.cmd"),
                    os.path.join(_root, "dockerify-android")))
        if agent and agent.get("kind") == "tool" and agent.get("source"):
            field("出处", agent["source"])

        md = self._read_skill_md(row)
        if md:
            sec("SKILL.md")
            txt.insert("end", md, "mono")
        elif row.get("_tags"):
            field("标签", "、".join(row["_tags"]))

        txt.configure(state="disabled")
        return self._detail_buttons(win, row, agent)

    def _show_member(self, d, name, zh):
        """整合卡里的一篇成员：点开即完整详情窗（含 SKILL.md 全文）。"""
        self._show_detail({"title": name, "desc": zh, "meta": d, "tags": [],
                           "extra": "审美套件 · 成员", "key": "", "kind": "skill"})

    def open_suite(self, row):
        """整合卡窗：把它怀里那几篇**以卡片列出**，各带中文说明，点开看全文。"""
        memb = list(row.get("_members") or [])
        labels = row.get("_suite_labels") or {}
        root = os.path.dirname(row.get("meta") or "")
        label = row.get("_suite") or "整合卡"

        win = tk.Toplevel(self)
        win.title("%s · 整合卡 · %d 篇" % (label, len(memb)))
        win.configure(bg=BG)
        w, h = self._fit(_px(1120), _px(760))
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        win.geometry("%dx%d+%d+%d" % (w, h, (sw - w) // 2, (sh - h) // 2))
        win.minsize(_px(640), _px(460))
        try:
            win.transient(self)
        except Exception:
            pass

        head = tk.Frame(win, bg=BG)
        head.pack(fill="x", padx=22, pady=(18, 4))
        tk.Label(head, text="%s · 整合卡" % label, bg=BG, fg=SUITE_COLOR,
                 font=F_DLG_T, anchor="w").pack(side="left")
        tk.Label(head, text=" 内含 %d 篇同源技能 " % len(memb), bg=SUITE_COLOR,
                 fg="#ffffff", font=F_TAG).pack(side="left", padx=(10, 0))
        tk.Label(win, text=(row.get("desc") or ""), bg=BG, fg=FAINT, font=F_HINT,
                 anchor="w", justify="left", wraplength=w - _px(70)
                 ).pack(fill="x", padx=22, pady=(4, 10))

        body = tk.Frame(win, bg=BG)
        body.pack(fill="both", expand=True, padx=22)
        cv = tk.Canvas(body, bg=BG, highlightthickness=0, bd=0)
        sb = ttk.Scrollbar(body, orient="vertical", command=cv.yview)
        cv.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        cv.pack(side="left", fill="both", expand=True)
        inner = tk.Frame(cv, bg=BG)
        cv.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>",
                   lambda _e: cv.configure(scrollregion=cv.bbox("all")))
        cv.bind("<Configure>", lambda e: cv.itemconfigure(1, width=e.width))

        cols = max(1, (w - _px(70)) // _px(350))
        for c in range(cols):
            inner.columnconfigure(c, weight=1, uniform="m")
        for i, sub in enumerate(memb):
            dd = os.path.join(root, sub)
            nm, en = _skill_brief(dd)
            zh = (labels.get(sub) or "").strip() or en or "（尚未写说明）"
            box = tk.Frame(inner, bg=CARD, highlightthickness=1,
                           highlightbackground=LINE)
            box.grid(row=(i // cols) * 2, column=i % cols, sticky="nsew",
                     padx=_px(6), pady=(_px(10), 0))
            tk.Frame(box, bg=SUITE_COLOR, width=_px(5)).pack(side="left", fill="y")
            pad = tk.Frame(box, bg=CARD)
            pad.pack(side="left", fill="both", expand=True, padx=_px(12),
                     pady=_px(10))
            tk.Label(pad, text=nm, bg=CARD, fg=FG, anchor="w",
                     font=F_CARD_T).pack(fill="x")
            tk.Label(pad, text=self._shorten(zh, 120), bg=CARD, fg=DIM, anchor="w",
                     justify="left", wraplength=_px(290),
                     font=F_CARD_D).pack(fill="x", pady=(_px(4), 0))
            tk.Label(pad, text="点开看全文 ›", bg=CARD, fg=FAINT, anchor="e",
                     font=F_HINT).pack(fill="x", pady=(_px(8), 0))
            for wd in (box, pad, *pad.winfo_children()):
                try:
                    wd.configure(cursor="hand2")
                    wd.bind("<Button-1>",
                            lambda _e, d0=dd, n0=nm, z0=zh: self._show_member(d0, n0, z0))
                except Exception:
                    pass

        foot = tk.Frame(win, bg=BG)
        foot.pack(fill="x", padx=22, pady=(12, 16))
        ttk.Button(foot, text="打开技能库目录", style="Tab.TButton",
                   command=lambda: self.open_path(root)).pack(side="left")
        ttk.Button(foot, text="关 闭", style="Tab.TButton",
                   command=win.destroy).pack(side="right")
        win.bind("<Escape>", lambda _e: win.destroy())
        return win

    def _detail_buttons(self, win, row, agent):
        """详情窗底栏：按条目性质给出动作按钮。"""
        foot = tk.Frame(win, bg=BG)
        foot.pack(fill="x", padx=22, pady=(0, 16))
        ttk.Button(foot, text="关闭", style="Tab.TButton",
                   command=win.destroy).pack(side="right")

        # 整合卡：一键打开技能库目录，里面那 13 篇的文件夹一眼看全
        if row.get("_members"):
            ttk.Button(foot, text="打开技能库目录（%d 篇在其中）" % len(row["_members"]),
                       style="Tab.TButton",
                       command=lambda rr=row: self.open_path(
                           os.path.dirname(rr.get("meta") or ""))
                       ).pack(side="right", padx=(0, 6))

        path = (agent or {}).get("exe") or row.get("meta") or ""

        # 详情窗里也能进工作台（第五十五轮：不再按「工作 N 件」论有无）
        if agent and agent.get("kind") != "doc":
            ttk.Button(foot, text="打开工作台", style="Tab.TButton",
                       command=lambda rr=row: self.open_workspace(rr)
                       ).pack(side="right", padx=(0, 6))

        # 说明文档：打开这篇文档
        if agent and agent.get("kind") == "doc" and path:
            ttk.Button(foot, text="打开文档", style="Tab.TButton",
                       command=lambda: self.open_file(path)).pack(side="right", padx=(0, 6))
        # 可启动者：从详情窗也能直接启动
        elif row.get("clickable") and agent:
            ttk.Button(foot, text="启动", style="Tab.TButton",
                       command=lambda a=agent: self.start_agent(a)).pack(side="right",
                                                                        padx=(0, 6))

        if path:
            ttk.Button(foot, text="打开所在目录", style="Tab.TButton",
                       command=lambda p=path: self.open_path(p)).pack(side="right",
                                                                     padx=(0, 6))

        try:
            win.bind("<Escape>", lambda _e: win.destroy())
        except Exception:
            pass
        win.focus_set()
        return win

    def _full_desc(self, row):
        """取未截断的描述：技能/MCP/工具自原始数据里再取一遍全文。"""
        title = row.get("title", "")
        for group in ("skills", "mcp"):
            for item in self.data.get(group, []):
                if item.get("name", "") == title:
                    return item.get("desc") or row.get("desc") or ""
        return row.get("desc", "")

    def _read_skill_md(self, row):
        """若该条目是技能且有 SKILL.md，读其正文（去掉 YAML 抬头）。"""
        p = row.get("meta") or ""
        if not p or not os.path.isdir(p):
            return ""
        md = os.path.join(p, "SKILL.md")
        if not os.path.isfile(md):
            return ""
        try:
            with open(md, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
        except Exception:
            return ""
        # 去掉开头的 YAML front matter
        text = text.lstrip("\ufeff")
        if text.startswith("---"):
            parts = text.split("---", 2)
            if len(parts) >= 3:
                text = parts[2]
        return text.strip()[:12000]

    # ---------- 重扫 ----------
    def rescan(self):
        self.btn_rescan.configure(text="扫描中…", state="disabled")
        threading.Thread(target=self._do_rescan, daemon=True).start()

    def _do_rescan(self):
        msg = ""
        try:
            mod = _import_scanner()
            if mod is None:
                msg = "未找到采集器，仅重载现有数据"
            else:
                data = mod.collect()
                # 写程序旁；写不动（如 U 盘只读、Program Files）则退 %LOCALAPPDATA%
                out = _write_data("agent_inventory.json", data)
                if out:
                    globals()["INV"] = out
                n_s = len(data.get("skills", []))
                n_m = len(data.get("mcp", []))
                msg = "已刷新：%d 技能 / %d MCP" % (n_s, n_m)
            # 同时重扫 Agent 客户端
            amod = _import_app_scanner()
            if amod is not None:
                adata = amod.collect()
                aout = _write_data("agents.json", adata)
                if aout:
                    globals()["AGENTS_JSON"] = aout
                msg += "；Agent %d 个" % len(adata.get("agents", []))
        except Exception as e:
            msg = "扫描失败：%s" % e
        self.after(0, lambda: self._after_rescan(msg))

    def _after_rescan(self, msg):
        self.data = load_inventory()
        self.agents = load_agents()
        self.tasks = load_tasks()
        # 重扫之后再过一遍：新装了 Agent，就把它目录里的 Skill 收进来
        try:
            extra = self._auto_import_new_agents(self.agents)
            if extra:
                self.data = load_inventory()      # 技能库有变，重载一次
                msg = msg + " · " + extra
        except Exception:
            pass
        self._render_metrics()
        self.btn_rescan.configure(text="重新扫描", state="normal")
        self.refresh()
        self.status.configure(text=msg + " · 共 %d 项。" % len(self.rows))
        self._auto_onboard()      # 新 Agent 一出现就自动接 MCP + 放指针


# ---------- 单实例：已有窗口就把它提到前台，别再开一个（第五十一轮） ----------
# 本版要求：连点应用程序会弹出一堆一模一样的主界面 —— 应该把已有窗口前置。
# 做法：命名互斥体判「是否已有实例」+ 抢前台（Windows 的前台锁要用
#   AttachThreadInput 绕，这是老办法里最稳的一条；不成再闪任务栏提示）。
_SINGLE_MUTEX = None          # 句柄必须留着，不然互斥体被回收，闸门就废了
SINGLE_NAME = "Global\\AgentAssetOverview.SingleInstance.v1"


def _bring_to_front(hwnd):
    """把某个窗口提到前台（尽力而为）。"""
    import ctypes
    u32 = ctypes.windll.user32
    k32 = ctypes.windll.kernel32
    try:
        u32.ShowWindow(hwnd, 9)                     # SW_RESTORE：最小化的也拉回来
    except Exception:
        pass
    try:
        fg = u32.GetForegroundWindow()
        t_fg = u32.GetWindowThreadProcessId(fg, None) if fg else 0
        t_me = k32.GetCurrentThreadId()
        if t_fg and t_fg != t_me:
            u32.AttachThreadInput(t_me, t_fg, True)
            u32.BringWindowToTop(hwnd)
            ok = u32.SetForegroundWindow(hwnd)
            u32.AttachThreadInput(t_me, t_fg, False)
        else:
            u32.BringWindowToTop(hwnd)
            ok = u32.SetForegroundWindow(hwnd)
    except Exception:
        ok = False
    # 前台没抢到也至少闪一下任务栏，让人看见它在哪儿
    try:
        class FLASHWINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("hwnd", ctypes.c_void_p),
                        ("dwFlags", ctypes.c_uint), ("uCount", ctypes.c_uint),
                        ("dwTimeout", ctypes.c_uint)]
        fi = FLASHWINFO(ctypes.sizeof(FLASHWINFO), hwnd, 3, 3, 0)   # FLASHW_ALL
        u32.FlashWindowEx(ctypes.byref(fi))
    except Exception:
        pass
    return bool(ok)


def _find_main_window():
    """找本应用**真正的主窗**。

    2026-09-20（单实例闸门修复）：从前是先 `FindWindowW(None, APP_TITLE)` ——
    只按标题认人。问题在于本应用里所有 `tk.Toplevel`（提示小牌、更新数据浮窗、
    忙碌浮窗）**都顶着与主窗一模一样的标题**（Tk 的 Toplevel 默认继承根的标题）。
    于是只要进程里还剩一枚提示牌没销毁，闸门就认定「程序开着呢」，把新启动的
    挡在门外 —— 双击图标毫无反应，最坑的是它连个错都不报。

    今按三条硬标准认主窗：
      ① 可见；② 标题恰为 APP_TITLE；③ **没有属主**（owner==0）——
      提示牌与浮窗都是 `transient(self)`，必有属主，一条就筛掉了；
      再加一条尺寸下限（≥300×200）兜底，免得将来又冒出别的小窗。
    """
    import ctypes
    from ctypes import wintypes
    u32 = ctypes.windll.user32
    found = []

    CB = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    def _cb(hw, _l):
        try:
            if not u32.IsWindowVisible(hw):
                return True
            if u32.GetWindow(hw, 4):                # GW_OWNER != 0 → 是别人的子窗
                return True
            n = u32.GetWindowTextLengthW(hw)
            if n <= 0:
                return True
            b = ctypes.create_unicode_buffer(n + 1)
            u32.GetWindowTextW(hw, b, n + 1)
            if b.value != APP_TITLE:
                return True
            r = wintypes.RECT()
            u32.GetWindowRect(hw, ctypes.byref(r))
            if (r.right - r.left) < 300 or (r.bottom - r.top) < 200:
                return True
            found.append(hw)
        except Exception:
            pass
        return True

    try:
        u32.EnumWindows(CB(_cb), 0)
    except Exception:
        return 0
    return found[0] if found else 0


def _single_instance_gate():
    """已经有实例吗？有就把它的窗口提到前台，并让本次启动退出。

    返回 True 表示「已有实例，请退出」。
    """
    import ctypes
    global _SINGLE_MUTEX
    try:
        k32 = ctypes.windll.kernel32
        k32.CreateMutexW.restype = ctypes.c_void_p
        _SINGLE_MUTEX = k32.CreateMutexW(None, False, SINGLE_NAME)
        if k32.GetLastError() != 183:            # ERROR_ALREADY_EXISTS
            return False                          # 我是第一个，继续开界面
    except Exception:
        return False                              # 判不了就别拦，宁可多开也别开不了
    # 已有实例：等它的窗口出现（可能正在启动），提到前台，然后退出
    import time as _t
    for _ in range(12):                           # 约 3 秒
        h = _find_main_window()
        if h:
            _bring_to_front(h)
            return True
        _t.sleep(0.25)
    # 2026-09-20：等了 3 秒也没有**能看见的主窗** —— 说明守着这道闸门的那个实例
    # 早就没了踪影（被强杀、或者只剩个没窗的残骸），而互斥体是 Global 的，
    # 只要还有任何一个进程攥着那个句柄，它就一直「存在」。
    # 此时**必须放行**：宁可多开一扇窗，也不能让人双击了图标却什么都不发生。
    # （从前这里写的是 return True —— 没窗也拦，于是谁也开不了。）
    return False


def _hide_console():
    """把控制台窗口藏掉。

    主 exe 现在以**控制台模式**打包 —— 因为 MCP 的数据通道就是 stdin/stdout，
    而窗口模式下 PyInstaller 会把这两条流指向空设备，服务端发不出也收不到报文。
    代价是双击时会闪出一枚黑窗口，故图形界面一启动就把它隐藏，观感与从前一致。
    """
    try:
        import ctypes
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 0)      # SW_HIDE
    except Exception:
        pass


def main():
    # 第三十三轮（本版要求：直接上 MCP）：同一枚 exe 兼两个身份 ——
    # 不带参数是图形界面；带 --mcp 则作 MCP 服务端（stdio），
    # 任何支持 MCP 的 Agent 都能直接检索本应用整合的信息。
    if "--mcp" in sys.argv:
        import agent_mcp
        return agent_mcp.serve()
    # 单实例闸门：**只拦不带参数的正常启动** —— `--mcp`（可能被多个客户端各拉一个）
    # 与 `--selftest` 这类开关不拦（第五十一轮）
    if not any(a.startswith("--") for a in sys.argv[1:]):
        try:
            if _single_instance_gate():
                return 0          # 已有主界面，已把它提到前台，本次悄悄退出
        except Exception:
            pass
    _hide_console()                  # 图形界面：藏掉控制台那枚黑窗口
    app = App()
    app.mainloop()


def _selftest():
    """隐藏开关 `--selftest`：程序自走一遍交互，把结论落成「自检结果.txt」。

    为何要有它：打包后的 exe 只能在**本进程内**验交互最可靠 —— 跨进程发鼠标
    要先把窗口摁到前台，而 Windows 的前台锁常把 `SetForegroundWindow` 拦掉，
    鼠标消息便落到别处，验出来全是假阴性。
    """
    lines = []
    app = App()
    app.geometry("1240x840")
    app.update()

    def step(t):
        lines.append(t)

    # --- 甲：技能栏，点一张卡，看详情窗 ---
    try:
        app.switch("skills")
        app.update()
        app.after(120, app.quit)
        app.mainloop()
        cards = []

        def walk(w):
            for c in w.winfo_children():
                if c.__class__.__name__ == "GlassCard":
                    cards.append(c)
                walk(c)

        walk(app.inner)
        step("技能卡数 = %d" % len(cards))
        if not cards:
            # 新机上本无技能，此段无从验起 —— 明说，不作假绿，亦不炸
            step("（本机无技能卡，甲段跳过）")
        else:
            c0 = cards[0]
            c0.update()
            lbl = c0._content[2][0] if len(c0._content) > 2 else c0._content[0][0]
            before = len([w for w in app.winfo_children() if isinstance(w, tk.Toplevel)])
            # 整卡可点：直接给内容控件发一击（模拟真实点击；旧法为 focus_set 取焦点）
            lbl.event_generate("<Button-1>", x=4, y=4)
            t0 = time.time()
            while time.time() - t0 < 0.8:
                app.update()
                time.sleep(0.02)
            tops = [w for w in app.winfo_children() if isinstance(w, tk.Toplevel)]
            step("点技能卡文字 → 新增窗 %d 个" % (len(tops) - before))
            for t in tops:
                step("    标题=%r 尺寸=%s" % (t.title(), t.geometry()))
                t.destroy()
            app.update()
    except Exception as e:
        import traceback
        step("甲段出错：" + traceback.format_exc())

    # --- 乙：Agents 栏，点一张卡，看启动小窗 ---
    try:
        app.switch("agents")
        app.update()
        app.after(120, app.quit)
        app.mainloop()
        cards = []

        def walk2(w):
            for c in w.winfo_children():
                if c.__class__.__name__ == "GlassCard":
                    cards.append(c)
                walk2(c)

        walk2(app.inner)
        step("Agent 卡数 = %d" % len(cards))
        if cards:
            c0 = cards[0]
            c0.update()
            step("卡上有可点回调 = %s" % (getattr(c0, "_click_cb", None) is not None))

            calls = []
            real = launch_agent

            def fake(agent):
                calls.append(agent.get("name"))
                time.sleep(0.9)
                return True, "已启动 " + agent.get("name", "")

            globals()["launch_agent"] = fake
            lbl = c0._content[-1][0]

            found = None
            lbl.event_generate("<Button-1>", x=4, y=4)
            t0 = time.time()
            while time.time() - t0 < 1.0:
                app.update()
                for t in app.winfo_children():
                    if isinstance(t, tk.Toplevel) and getattr(t, "_busy", False):
                        found = t
                if found:
                    break
                time.sleep(0.02)
            if found:
                step("点 Agent 卡 → 弹出启动小窗 尺寸=%s 可见=%s"
                     % (found.geometry(), bool(found.winfo_ismapped())))
                texts = []

                def scan(w):
                    for c in w.winfo_children():
                        try:
                            texts.append(c.cget("text"))
                        except Exception:
                            pass
                        scan(c)

                scan(found)
                step("    窗内文字 = %s" % texts)
                # 趁窗还在，连点两次
                n0 = len(calls)
                lbl.event_generate("<Button-1>", x=4, y=4)
                lbl.event_generate("<Button-1>", x=4, y=4)
                t1 = time.time()
                while time.time() - t1 < 0.35:
                    app.update()
                    time.sleep(0.02)
                step("窗在时连点两次 → 启动被调增量 = %d（应为 0）" % (len(calls) - n0))
                step("此时小窗仍在 = %s（应 True，只此一窗）"
                     % bool([t for t in app.winfo_children()
                             if isinstance(t, tk.Toplevel) and getattr(t, "_busy", False)]))
            else:
                step("点 Agent 卡 → 未弹出启动小窗（本机 Agent 皆不在，属常情）")

            t2 = time.time()
            while time.time() - t2 < 2.6:
                app.update()
                time.sleep(0.03)
            step("等 2.6 秒后小窗仍在 = %s（应 False，已自收）"
                 % bool([t for t in app.winfo_children()
                         if isinstance(t, tk.Toplevel) and getattr(t, "_busy", False)]))
            step("启动被调总次数 = %d %s" % (len(calls), calls))
            globals()["launch_agent"] = real
        else:
            step("（本机无 Agent 卡，乙段跳过）")
    except Exception:
        import traceback
        step("乙段出错：" + traceback.format_exc())

    # --- 丁：栏目显隐（空栏目连页签隐去）与「未探到不显示」 ---
    try:
        app._refresh_tabs()
        shown = [k for k, b in app.tab_btns.items() if b.winfo_ismapped()]
        step("可见页签 = %s" % shown)
        # 每栏报「有内容否」，与可见性对照
        for k, label in CATS:
            step("    %s(%s)：有内容=%s 可见=%s"
                 % (label, k, app._cat_has_content(k),
                    app.tab_btns[k].winfo_ismapped()))
        # 名册里一个「未探到」的都不该有
        dead = [a.get("name") for a in app.agents if not a.get("exists")]
        step("名册里 exists=False 的残留 = %s（应为空）" % dead)
        step("手动添加按钮已挂 = %s" % hasattr(app, "btn_add"))
        if hasattr(app, "btn_add"):
            step("按钮文字 = %r" % app.btn_add.cget("text"))
    except Exception:
        import traceback
        step("丁段出错：" + traceback.format_exc())

    # --- 戊：工作台（第五十五轮：应用内那格已废，改验原生记录与本体目录） ---
    try:
        wb = [a for a in app.agents if a.get("key") == "workbuddy"]
        if not wb:
            step("戊段：名册里无 WorkBuddy 条目")
        else:
            works = wb[0].get("works") or []
            step("戊1 WorkBuddy 原生记录数 = %d（应 > 0）" % len(works))
            step("戊2 works_dir 是否还产出 = %s（应 False）"
                 % bool(wb[0].get("works_dir")))
            bad = [w.get("name") for w in works
                   if not os.path.exists(w.get("path", ""))]
            step("戊3 路径不存在的条目 = %s（应为空）" % bad[:5])
            step("戊4 本体目录 = %s" % (app._agent_home(wb[0]) or "（无）"))
            step("戊5 原生记录根数 = %d" % len(app._record_roots_of(wb[0])))
    except Exception:
        import traceback
        step("戊段出错：" + traceback.format_exc())

    # --- 己：本工具技能库与「导入 Skill」 ---
    try:
        step("技能库 = %s（存在=%s）" % (SKILL_LIB, os.path.isdir(SKILL_LIB)))
        step("「导入 Skill」按钮已挂 = %s" % hasattr(app, "btn_skill"))
        step("探测到的 Skill 来源 = %d 处" % len(app._skill_sources()))
        _log = app._load_import_log()
        step("自动收编记录 = %s" % SKILL_IMPORT_LOG)
        step("    已登记 Agent %d 个：%s" % (len(_log.get("agents") or {}),
                                            "、".join((_log.get("agents") or {}).keys()) or "（无）"))
        _hit = app._skill_dirs_for_agent({"key": "workbuddy", "name": "WorkBuddy",
                                          "exe": os.path.join(os.path.expanduser("~"), "x", "WorkBuddy.exe")})
        step("    按 Agent 猜技能目录（workbuddy）= %d 处 %s" % (len(_hit), _hit))
    except Exception:
        import traceback
        step("己段出错：" + traceback.format_exc())

    out = os.path.join(HERE, "自检结果.txt")
    try:
        with open(out, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
    except Exception:
        pass
    try:
        app.destroy()
    except Exception:
        pass
    print("\n".join(lines))


if __name__ == "__main__":
    try:
        if "--syncdata" in sys.argv:
            # 隐藏开关：只补名册（缺则扫），不开窗。桌面那枚首次运行时自呼此法，
            # 省得用户等界面里再点一次「重新扫描」。
            _note, _a, _b = cold_scan()
            try:
                sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
            print(_note or "名册已齐，不必再扫。")
        elif "--selftest" in sys.argv:
            _selftest()
        else:
            main()
    except Exception:
        import traceback
        log = ""
        try:
            log = os.path.join(HERE, "启动失败.log")
            with open(log, "w", encoding="utf-8") as f:
                f.write(traceback.format_exc())
        except Exception:
            pass
        try:
            r = tk.Tk()
            r.withdraw()
            messagebox.showerror("Agent 资产总览 启动失败",
                                 traceback.format_exc() + "\n\n日志：" + log)
            r.destroy()
        except Exception:
            pass


# ---------- 运行期初始化（须在所有函数定义之后） ----------
# 这几步都要调用本文件后面才定义的函数，故不可写在模块顶部。
INVENTORY_NOTE = _guard_inventory()
