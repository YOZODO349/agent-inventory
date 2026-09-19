# -*- coding: utf-8 -*-
"""Agent 探测与登记：自动扫描本机 agent 客户端，维护 agents.json。

探测策略（由强到弱）：
  1. 已知客户端的固定路径（app_paths）
  2. 桌面 / 开始菜单快捷方式里名字含 agent 关键词者
  3. 已注册 MCP 配置的宿主（说明它是个 agent 容器）

每条记录：key, name, desc, exe(或 lnk), args, cwd, icon, category, source, verified

换机可用（2026-09-18 第九轮）：
  表里的用户路径一律写作 `@HOME@` 占位符，运行时经 `_expand()` 还原成**本机真实家目录**。
  如此同一份 exe 拿到别的电脑（用户名不同）也能正确定位。
  ⚠️ 新增条目时**务必照此写法**，不要再硬编码某个具体用户名下的绝对路径。
"""
import os
import re
import sys
import glob
import io
import json

HOME = os.path.expanduser("~")
HERE = os.path.dirname(os.path.abspath(__file__))
ICON_DIR = os.path.join(HERE, "agent_icons")


def machine_tag():
    """本机标识：家目录路径（小写、反斜杠）。

    名册里盖的「产地戳」—— 程序读名册时若见戳非本机，即知是随包的老底册，
    须当即重扫，免得在新电脑上摆出一堆本机根本没有的技能与插件。
    与 scan_agents.py 的同名函数**同法同源**，两处不可改得不一致。
    """
    return HOME.replace("/", "\\").rstrip("\\").lower()


def _expand(p):
    """把 `@HOME@` 还原成本机家目录；顺带兼容 `%VAR%` 环境变量写法。"""
    if not isinstance(p, str):
        return p
    if "@HOME@" in p:
        p = p.replace("@HOME@", HOME)
    if "%" in p:
        p = os.path.expandvars(p)
    return p


def _expand_rec(obj):
    """递归还原字典/列表里所有字符串中的占位符（入口处一律先过一道）。"""
    if isinstance(obj, dict):
        return {k: _expand_rec(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_expand_rec(v) for v in obj]
    return _expand(obj)


# ---------- 1. 已知 agent 定义表 ----------
KNOWN = [
    {
        "key": "workbuddy",
        "name": "WorkBuddy",
        "desc": "AI 编程助手客户端，支持 MCP 插件、技能与多专家协作。",
        "exe": r"@HOME@\AppData\Local\Programs\WorkBuddy\WorkBuddy.exe",
        "icon": "workbuddy.png",
        "category": "编程助手",
    },
    {
        "key": "cursor",
        "name": "Cursor",
        "desc": "AI 优先的代码编辑器，内置对话式编程与智能补全。",
        "exe": r"@HOME@\AppData\Local\Programs\cursor\Cursor.exe",
        "icon": "cursor.png",
        "category": "编程助手",
    },
    {
        "key": "codexplus",
        "name": "Codex++",
        "desc": "Codex 增强客户端，带模型管理与多会话编排。",
        "exe": r"@HOME@\AppData\Local\Programs\Codex++\codex-plus-plus.exe",
        "icon": "codexplus.png",
        "category": "编程助手",
    },
    {
        "key": "codexplus_manager",
        "name": "Codex++ 管理工具",
        "desc": "Codex++ 的配置与模型管理面板。",
        "exe": r"@HOME@\AppData\Local\Programs\Codex++\codex-plus-plus-manager.exe",
        "icon": "codexplus_manager.png",
        "category": "管理面板",
        # 爱卿明令：此乃管理工具，不算 Agent 本体，不上架
        "hidden": True,
    },
    {
        "key": "astrbot",
        "name": "AstrBot",
        "desc": "多平台聊天机器人框架，可接入 QQ、微信、Telegram 等。",
        "exe": r"@HOME@\AppData\Local\AstrBot\astrbot-desktop-tauri.exe",
        "icon": "astrbot.png",
        "category": "聊天机器人",
    },
    {
        "key": "astrbot_launcher",
        "name": "AstrBot Launcher",
        "desc": "AstrBot 的启动与运行环境管理入口。",
        "exe": r"@HOME@\AppData\Local\AstrBot Launcher\astrbot-launcher.exe",
        "icon": "astrbot_launcher.png",
        "category": "聊天机器人",
        # 爱卿明令：此乃启动器，不算 Agent 本体，不上架
        "hidden": True,
    },
    {
        "key": "grokbot",
        "name": "Grok Bot",
        "desc": "接入 Grok 模型的对话机器人桌面客户端。",
        "exe": r"@HOME@\AppData\Local\Programs\Grok Bot\Grok Bot.exe",
        "icon": "grokbot.png",
        "category": "对话客户端",
    },
    {
        "key": "comfyui",
        "name": "ComfyUI",
        "desc": "节点式 AI 绘图工作流引擎，可跑本地扩散模型。",
        "exe": r"C:\ComfyUI\ComfyUI_windows_portable\run_nvidia_gpu.bat",
        "icon": "comfyui.png",
        "category": "AI 绘图",
    },
    {
        "key": "chunxiao",
        "name": "春宵夜宴助手",
        "desc": "基于 AstrBot 运行时的自建机器人实例。",
        "exe": r"@HOME@\AppData\Local\AstrBot\backend\python\pythonw.exe",
        "icon": "astrbot.png",
        "category": "聊天机器人",
        "hidden": True,
    },
]


def _icon_exists(name):
    return bool(name) and os.path.isfile(os.path.join(ICON_DIR, name))


# ---------- 1.4 全盘寻真身：只要在，就一定找得着 ----------
# 爱卿之令（2026-09-18 第十四轮）：
#   「不应该出现这个问题，只要在就一定能找到」
# 缘由：登记表里那条**死路径**未必与真身相符 —— 真身可能改了名、换了目录
# （AstrBot 尤甚：`astrbot-desktop-tauri.exe` 只是它诸多版本之一的名）。
# 故：登记路径落空时，按**特征名**在常见安装根里掘；仍不成，则全盘搜。

# 跳过这些目录，免得全盘搜成了全盘等
_SKIP_DIRS = {
    "windows", "$recycle.bin", "system volume information", "program files",
    "program files (x86)", "programdata", "msys64", "temp", "tmp",
    "node_modules", ".git", ".svn", "__pycache__", "site-packages",
    "winsxs", "assembly", "installer", "servicing", "driverstore",
}

# 各 agent 的真身特征（可多枚）：命中其一即认
SIGNATURES = {
    "workbuddy": ("workbuddy.exe",),
    "cursor": ("cursor.exe",),
    "codexplus": ("codex-plus-plus.exe", "codex++.exe"),
    "astrbot": ("astrbot-desktop-tauri.exe", "astrbot.exe",
                "astrbot-desktop.exe", "astrbot launcher.exe"),
    "grokbot": ("grok bot.exe", "grokbot.exe"),
}


def _walk_find(roots, want_names, max_depth=4, budget=None):
    """在 `roots` 之下逐层找**文件名**命中 `want_names` 的可执行文件。

    · `want_names` 为小写文件名集合（模糊：只须包含其一）。
    · 深不过 `max_depth` 层，且绕开 `_SKIP_DIRS` 里的重目录。
    · `budget` 是可选的「最多看多少个目录」之限，防全盘搜卡死。
    返回命中的绝对路径（首个），无则 None。

    ⚠️ **须排除安装包/卸载器**（本宫曾在 `C:\\KDubaSoftDownloads\\` 里
    把 `CursorSetup0.45.11-x64.exe` 认成了 Cursor 本体 —— 点它只会又弹安装向导）。
    """
    seen = 0
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        base_depth = root.rstrip("\\/").count(os.sep)
        for dirpath, dirnames, filenames in os.walk(root, topdown=True):
            # 深度闸
            if dirpath.count(os.sep) - base_depth > max_depth:
                dirnames[:] = []
                continue
            # 绕开重目录
            dirnames[:] = [d for d in dirnames
                           if d.lower() not in _SKIP_DIRS
                           and not d.startswith("$")]
            seen += 1
            if budget is not None and seen > budget:
                return None
            low = {f.lower(): f for f in filenames}
            for want in want_names:
                if not want.endswith(".exe"):
                    continue
                stem = want[:-4]
                for lf, orig in sorted(low.items()):
                    if not lf.endswith(".exe"):
                        continue
                    if _is_installer(lf):
                        continue
                    if lf == want or stem in lf:
                        return os.path.join(dirpath, orig)
    return None


def _is_installer(fname):
    """看起来像安装包/卸载器/更新器吗？（这些不是程序本体）"""
    low = fname.lower()
    for bad in ("setup", "install", "unins", "uninst", "update", "updater",
                "installer", "portable", "patch", "-x64.exe", "-x86.exe",
                "web_installer", "bootstrapper"):
        if bad in low:
            return True
    return False


def deep_find(key, hint_dir=None):
    """给某个 agent key 寻真身：先近处（`hint_dir` 及其父），再全盘。

    返回找到的路径（str），寻不着则 None。
    """
    wants = SIGNATURES.get(key)
    if not wants:
        return None
    # 近处：该 agent 的常见落脚处（含其父目录的兄弟夹）
    near = []
    if hint_dir:
        near.append(hint_dir)
        up = os.path.dirname(hint_dir.rstrip("\\/"))
        if up:
            near.append(up)
    near += [
        os.path.join(HOME, "AppData", "Local"),
        os.path.join(HOME, "AppData", "Local", "Programs"),
        os.path.join(HOME, "AppData", "Roaming"),
    ]
    hit = _walk_find(near, wants, max_depth=3, budget=4000)
    if hit:
        return hit
    # 全盘：从各盘根起，深度从严（3 层），目录预算放宽
    roots = []
    for ch in "CDEFG":
        d = ch + ":\\"
        if os.path.isdir(d):
            roots.append(d)
    return _walk_find(roots, wants, max_depth=3, budget=60000)


# ---------- 1.45 手动添入的 Agent ----------
# 爱卿之令（第十四轮）：「或者添加一个可以手动添加Agent的功能」
# 自动探法纵有全盘搜兜底，仍可能有漏（或爱卿想收一枚本工具不认识的）。
# 存 %LOCALAPPDATA%\Agent 资产总览\manual_agents.json —— **不动程序旁**，
# 免得工作区与桌面又多出散件（符爱卿「桌面只留一枚 exe」之旨）。
def manual_store_path():
    base = os.environ.get("LOCALAPPDATA") or os.path.join(HOME, "AppData", "Local")
    d = os.path.join(base, "Agent 资产总览")
    return os.path.join(d, "manual_agents.json")


def load_manual():
    """读手动档。返回 list（每条为 dict）。文件不在或读不动 → 空表。"""
    p = manual_store_path()
    if not os.path.isfile(p):
        return []
    try:
        with open(p, "r", encoding="utf-8") as f:
            obj = json.load(f) or {}
        rows = obj.get("agents") if isinstance(obj, dict) else obj
        return [r for r in (rows or []) if isinstance(r, dict)]
    except Exception:
        return []


def save_manual(rows):
    """写手动档。返回 (成否, 说明)。"""
    p = manual_store_path()
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        obj = {"agents": list(rows or []), "_home": machine_tag(),
               "_made": __import__("time").strftime("%Y-%m-%d %H:%M:%S")}
        with open(p, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=1)
        return True, "已存 %s" % p
    except Exception as e:
        return False, "未能写入：%s" % e


def add_manual(name, exe, desc="", category="手动添加"):
    """添一枚手动 Agent。返回 (成否, 说明)。"""
    exe = os.path.normpath(exe)
    if not os.path.isfile(exe):
        return False, "该路径不是文件：%s" % exe
    name = (name or "").strip() or os.path.splitext(os.path.basename(exe))[0]
    rows = load_manual()
    rows = [r for r in rows if _norm(r.get("name", "")) != _norm(name)]
    rows.append({
        "key": "man_" + re.sub(r"\W+", "_", name).lower(),
        "name": name,
        "desc": desc or "手动添入的 Agent。",
        "exe": exe,
        "icon": "",
        "category": category,
        "source": "手动添加",
        "kind": "agent",
    })
    return save_manual(rows)


def remove_manual(name):
    """删一枚手动 Agent（按名去）。返回 (成否, 说明)。"""
    rows = load_manual()
    keep = [r for r in rows if _norm(r.get("name", "")) != _norm(name)]
    if len(keep) == len(rows):
        return False, "手动档里没有「%s」" % name
    return save_manual(keep)


# Android 兵站（replicant-mcp 全套）：第十九轮起搬进**本工具目录**下，
# 故先按程序旁找，再退到 exe 旁（打包后从别处运行）、最后退回家目录（旧布局）。
def _android_env():
    cands = [os.path.join(HERE, "通用资源", "安卓模拟器MCP"),
             os.path.join(HERE, "安卓模拟器MCP"),
             os.path.join(HERE, "android-agent-env")]
    try:
        if getattr(sys, "frozen", False):
            exed = os.path.dirname(os.path.abspath(sys.executable))
            cands.append(os.path.join(exed, "通用资源", "安卓模拟器MCP"))
            cands.append(os.path.join(exed, "安卓模拟器MCP"))
            cands.append(os.path.join(exed, "android-agent-env"))
    except Exception:
        pass
    cands.append(os.path.join(HOME, "安卓模拟器MCP"))
    cands.append(os.path.join(HOME, "android-agent-env"))
    for c in cands:
        if os.path.isdir(c):
            return c
    return cands[0]


ANDROID_ENV = _android_env()


# 技能真身：第二十一轮起统一收在应用内「通用资源\skills」，
# 各家 agent 的技能架（.workbuddy / .claude / .cursor ...）里只留 Junction 指过来。
def _skill_lib_dir():
    for c in (os.path.join(HERE, "通用资源", "skills"),
              os.path.join(HOME, "agent-skills")):
        if os.path.isdir(c):
            return c
    return os.path.join(HERE, "通用资源", "skills")


SKILL_LIB_DIR = _skill_lib_dir()


# ---------- 1.5 兵站工具：不是客户端，而是装在机器上的工具箱 ----------
# 这些既非 skill 也非 MCP，是**给人/给 AI 用的实体程序**，散在 agent-tools 与
# android-agent-env 两座兵站里，不扫客户端配置便发现不了。故单列一张表。
# 第二十五轮（爱卿令）：「工具与运行时」一栏撤除后，这张表也撤了 ——
# 它登记的四件「命名工具」在界面上本来就不显示（Agents 栏拦 kind=tool，
# 工具栏又读的是另一份数据），纯死数据。其中安卓那三件的信息
# （兵站根目录、自带运行时、安卓操作台、dockerify）已并入 MCP 条目的详情；
# 剪映那件与技能栏的 jianying-editor 是同一份东西，那边已有说明。


# ---------- 1.6 说明文档：那些「给人看」的长文 ----------
# 爱卿原先在桌面自建【Agent通用工具一览】收纳夹，装各工具的说明书。
# 第八轮：正文已收入名册，桌面收纳夹遂撤（移入回收站）。
# 为免「夹子一撤、正文陪葬」，正文另存工作区 说明文档\ 存档，此处为第一顺位。
# 桌面路径仍列第二，若爱卿日后又想往那里丢新说明，照收不误。
DOC_DIRS = [
    os.path.join(HERE, "说明文档"),
    os.path.join(HOME, "Desktop", "Agent通用工具一览"),
]


# 工作区里**该入册的散件**：可读文件，一律收进 WorkBuddy 的名下。
# 跳过的是「数据产物」与「体积大件」—— 说明见下面 _WS_SKIP_FILE / _WS_SKIP_DIR。
_WS_EXT = (".py", ".json", ".md", ".txt", ".bat", ".cmd", ".ps1", ".spec",
           ".html", ".htm", ".log", ".ico", ".png", ".jpg", ".zip", ".lnk")

# 这些是运行期数据产物，不算「工作成果」，不入册
_WS_SKIP_FILE = {"agent_inventory.json", "agents.json"}

# 这些是目录，其内容另有条目（说明文档）或体量过大（备份、构建产物），不入册
_WS_SKIP_DIR = {".workbuddy", "备份", "dist", "__pycache__", "build",
                "agent_icons", "便携数据",
                # 第十九轮：Android 兵站（6.8 GB）搬进本工具目录后，不该再被
                # 当成「工作区散件」逐个列进 WorkBuddy 名下 —— 它自成一条工具条目。
                "android-agent-env", "安卓模拟器MCP", "通用资源"}


def _file_note(p):
    """给一件工作文件写一句「这是什么」。

    优先从文件内容里取线索：Python 的模块注释、Markdown 的首个标题、
    bat 的注释。取不到则按扩展名给一句话。
    """
    ext = os.path.splitext(p)[1].lower()
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as fh:
            head = fh.read(4000)
    except Exception:
        head = ""
    head = head.lstrip("\ufeff")

    if ext == ".py":
        m = re.search(r'^\s*"""(.+?)(?:"""|\Z)', head, re.S)
        if m:
            line = m.group(1).strip().splitlines()[0].strip()
            if line:
                return line[:200]
    if ext == ".md":
        for line in head.splitlines():
            s = line.strip().lstrip("#").strip()
            if s:
                return s[:200]
    if ext in (".bat", ".cmd", ".ps1"):
        for line in head.splitlines():
            s = line.strip()
            if s.startswith(("::", "rem ", "REM ", "#")):
                s = s.lstrip(":#").strip()
                if s:
                    return s[:200]
            elif s and not s.startswith("@"):
                break
    fallback = {
        ".py": "工作区脚本（Python 源码）。",
        ".json": "工作区数据文件（JSON）。",
        ".md": "工作区文稿（Markdown）。",
        ".txt": "工作区文本。",
        ".bat": "Windows 批处理（双击即跑）。",
        ".cmd": "Windows 命令脚本（双击即跑）。",
        ".ps1": "PowerShell 脚本。",
        ".spec": "打包规格（PyInstaller spec）。",
        ".html": "静态网页（可直接双击打开）。",
        ".log": "运行日志。",
        ".ico": "应用图标。",
        ".png": "截图 / 图片。",
        ".jpg": "截图 / 图片。",
        ".zip": "压缩包（备份）。",
        ".lnk": "快捷方式。",
    }
    return fallback.get(ext, "工作区文件。")


def collect_workspace():
    """把工作区里的散件收进名册，挂在 **WorkBuddy** 的名下。

    爱卿之意（第十五轮）——「把工作区的所有项目文件地址统一放到与你的名字
    对应的 WorkBuddy 那个 Agent 的页面下，别的 Agent 想接力时一眼看全」。
    故此处收的是「工作成果」：源码、规格、快捷批处理、文稿、图片、图标、备份包。
    数据产物（两份名册）与目录（说明文档 / 备份 / 便携数据 / 图标夹）另有去处，
    单体超过 40 MB 者亦不收（免得卡片列表被一件大物压住）。
    """
    ws = HERE
    if not os.path.isdir(ws):
        return []

    docs_dir = os.path.join(ws, "说明文档")
    doc_paths = set()
    if os.path.isdir(docs_dir):
        for f in os.listdir(docs_dir):
            doc_paths.add(os.path.normcase(os.path.join(docs_dir, f)))

    out = []
    try:
        names = sorted(os.listdir(ws))
    except Exception:
        return out

    for f in names:
        p = os.path.join(ws, f)
        if not os.path.isfile(p):
            continue
        if f in _WS_SKIP_FILE:
            continue
        if os.path.splitext(f)[1].lower() not in _WS_EXT:
            continue
        try:
            if os.path.getsize(p) > 40 * 1024 * 1024:
                continue
        except Exception:
            continue
        out.append({
            "key": "wswork_" + re.sub(r"\W+", "_", f).lower(),
            "name": f,
            "role": "工作区文件",
            "path": p,
            "size": os.path.getsize(p),
            "desc": _file_note(p),
        })

    # 说明文档整夹入册（每篇一条，正文随该条自己的条目走）
    if os.path.isdir(docs_dir):
        for f in sorted(os.listdir(docs_dir)):
            p = os.path.join(docs_dir, f)
            if not os.path.isfile(p):
                continue
            out.append({
                "key": "wswork_" + re.sub(r"\W+", "_", f).lower(),
                "name": f,
                "role": "说明文档",
                "path": p,
                "size": os.path.getsize(p),
                "desc": _file_note(p),
            })

    # 子目录也各立一条（备份、便携数据等，便于接力者知道有哪些去处）
    for d in ("备份", "便携数据"):
        p = os.path.join(ws, d)
        if not os.path.isdir(p):
            continue
        try:
            n = len(os.listdir(p))
        except Exception:
            n = 0
        out.append({
            "key": "wswork_" + re.sub(r"\W+", "_", d).lower(),
            "name": d + "\\",
            "role": "工作区目录",
            "path": p,
            "size": 0,
            "desc": "工作区子目录，含 %d 件。" % n,
        })
    return out


def collect_docs():
    """收编说明文档（.md）的正文，供名册的【说明文档】栏陈列。"""
    docs = []
    for d in DOC_DIRS:
        if not os.path.isdir(d):
            continue
        for f in sorted(os.listdir(d)):
            if not f.lower().endswith((".md", ".txt")):
                continue
            p = os.path.join(d, f)
            if not os.path.isfile(p):
                continue
            try:
                with open(p, "r", encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
            except Exception:
                text = ""
            title = os.path.splitext(f)[0]
            # 取正文里第一个非空行作摘要
            brief = ""
            for line in text.splitlines():
                s = line.strip().lstrip("#").strip()
                if s:
                    brief = s
                    break
            docs.append({
                "key": "doc_" + re.sub(r"\W+", "_", title).lower(),
                "name": title,
                "desc": brief[:180] or "（说明文档）",
                "exe": p,
                "path": p,
                "text": text,
                "source": "桌面收纳夹",
                "category": "说明文档",
            })
    return docs


def _norm(name):
    """归一化名字，供去重与屏蔽比对（去空格、连字符，转小写）。"""
    return re.sub(r"[\s\-_+＋]+", "", str(name)).lower()


# 上架黑名单：这些名字一律不许出现在名册里。
# 爱卿明令：Codex++ 管理工具、AstrBot Launcher 皆非 Agent 本体，从页面除名。
# 第十八轮：本工具自身亦已除名（爱卿令：「本应用栏根本不是 agent」）。
BLOCK_NAMES = [
    "codex++管理工具",
    "codex++manager",
    "codexplusmanager",
    "astrbotlauncher",
    "astrbot启动器",
    "astrbotlauncher启动器",
]
BLOCK = set(_norm(n) for n in BLOCK_NAMES)


def _blocked(name):
    """名字是否在黑名单内。"""
    return _norm(name) in BLOCK


# 本工具自身：桌面那枚快捷方式指向的就是本程序，不必再给自己上架一次。
# （第十八轮：爱卿令「本应用栏根本不是 agent」，内置登记之外，这枚同名快捷方式
#   也会扫出一张一模一样的卡，故一并拦下。）
SELF_LNK = ("agent资产总览", "agent 资产总览")


def _is_self_lnk(path):
    """这枚快捷方式是不是本工具自己？名字对上，或壳里写着本程序的 exe。"""
    base = os.path.splitext(os.path.basename(path))[0]
    if _norm(base) in [_norm(x) for x in SELF_LNK]:
        return True
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except Exception:
        return False
    probes = ["Agent资产总览.exe", "Agent 资产总览.exe"]
    try:
        probes.append(os.path.basename(sys.executable or ""))
    except Exception:
        pass
    for probe in probes:
        if not probe:
            continue
        for enc in ("utf-16-le", "gbk", "latin-1"):
            try:
                if probe.encode(enc, "ignore") in raw:
                    return True
            except Exception:
                pass
    return False


def probe_shortcuts():
    """扫桌面与开始菜单，捞与 agent 相关的快捷方式。"""
    pats = [os.path.join(HOME, "Desktop", "*.lnk"),
            os.path.join(HOME, "AppData", "Roaming", "Microsoft", "Windows",
                         "Start Menu", "**", "*.lnk"),
            r"C:\ProgramData\Microsoft\Windows\Start Menu\**\*.lnk"]
    keys = ("agent", "claude", "codex", "cursor", "astrbot", "grok",
            "windsurf", "trae", "ollama", "gemini", "chatgpt", "copilot")
    # 排除：卸载器、说明文档、管理工具/启动器、与本主题无关者
    deny = ("uninstall", "卸载", "readme", "说明", "manual", "license",
            "custom cursor", "管理工具", "launcher", "启动器")
    found = []
    import glob as _glob          # 局部导入：此件是动态载入时用的，顶上导入易被打包器漏掉
    for p in pats:
        for f in _glob.glob(p, recursive=True):
            base = os.path.basename(f).lower()
            if any(d in base for d in deny):
                continue
            if _is_self_lnk(f):          # 本工具自己那枚，不上架
                continue
            if any(k in base for k in keys):
                found.append(f)
    return sorted(set(found))


# ---------- 自动抄录：各 Agent 的工作日志源（第二十七轮） ----------
# 爱卿令：扫描定位到 Agent 的同时记下它的工作数据文件夹，发现更新就自动抄进应用，
# 免得每次都要手动叫 agent 总结记录。
# 三件事：
#   ① 记下每个 Agent 的日志源（下列表，数据驱动，加源不必改代码）；
#   ② 只抄**新增/有改动**的文本小文件（水位记在 agent_logs_state.json）；
#   ③ 太大的会话实录（.jsonl）不整篇抄，摘出里面的对话文本另存成 .md。
LOG_TEXT_EXTS = (".md", ".txt", ".log", ".csv", ".json", ".py", ".ps1", ".bat",
                 ".cmd", ".html", ".htm", ".yml", ".yaml", ".toml", ".ini")
LOG_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "shots",
                 "attachments", "Cache", "logs"}
LOG_MAX_COPY = 512 * 1024          # 单文件超 512 KB 不抄（大件另走摘录）
LOG_MAX_FILES = 40                 # 一轮最多抄几个，免得一口气搬空
LOG_DIGEST_MAX = 300 * 1024        # 摘录单篇上限

LOG_SOURCES = {
    "workbuddy": [
        {"name": "WorkBuddy 会话记忆",
         "glob": os.path.join(HOME, "WorkBuddy", "*", ".workbuddy", "memory", "*.md"),
         "mode": "copy"},
        # 第三十六轮（爱卿问：最新的那条记录怎么没抄到）——
        # WorkBuddy 还有一份**用户级长期记忆**：~/.workbuddy/memory/<会话>_memory.md，
        # 而且**正在进行的会话就往这里写**。先前的 glob 只扫各会话工作区里的
        # memory 子目录，于是"当前这次会话"的记录永远抄不到 —— 症状就是
        # 点了「手动抄录」却报"暂无新日志"。
        {"name": "WorkBuddy 长期记忆",
         "glob": os.path.join(HOME, ".workbuddy", "memory", "*.md"),
         "mode": "copy"},
    ],
    "codexplus": [
        {"name": "Codex 笔记",
         "glob": os.path.join(HOME, ".codex", "memories", "*.md"),
         "mode": "copy"},
        {"name": "Codex 会话实录",
         "glob": os.path.join(HOME, ".codex", "sessions", "*", "*", "*", "*.jsonl"),
         "mode": "digest"},
    ],
    "astrbot": [
        {"name": "AstrBot 会话工作区",
         "glob": os.path.join(HOME, ".astrbot", "data", "workspaces", "*", "**", "*"),
         "mode": "copy"},
    ],
    # 只有缓存/二进制的，记下位置但不抄（免得搬一堆没用的）
    "cursor": [{"name": "Cursor 会话存储（仅登记，不抄）",
                "glob": os.path.join(os.environ.get("APPDATA", ""), "Cursor", "User",
                                     "workspaceStorage", "*"),
                "mode": "skip"}],
    "grokbot": [{"name": "Grok Bot 数据（仅登记，不抄）",
                 "glob": os.path.join(os.environ.get("APPDATA", ""), "Grok Bot", "*"),
                 "mode": "skip"}],
}

# ---------- 工作任务及产物一览（第二十八轮） ----------
# 爱卿令：把各 Agent 的任务/会话整理成单独一览；点进去看「做了什么、谁参与、
# 产出了什么」，有产物就给**明确地址**。
# 原料＝自动抄录下来的那堆日志（工作记录\<Agent>\自动抄录\<来源>\…）：
#   · 一份日志（.md 之类） = 一个任务
#   · 一个会话文件夹      = 一个任务（里面的文件即其产物）
PATH_RE = re.compile(
    r"[A-Za-z]:\\[^\s\"'<>|*?\n\r]+\.(?:md|txt|py|json|html?|exe|lnk|bat|cmd|ps1|"
    r"png|jpe?g|webp|mp4|mp3|wav|csv|xlsx?|docx?|pdf|zip|vue|js|ts|ya?ml|toml|ini|log)",
    re.IGNORECASE)

AGENT_ALIASES = {
    "workbuddy": "WorkBuddy", "cursor": "Cursor", "codex++": "Codex++",
    "codexplus": "Codex++", "codex": "Codex++", "astrbot": "AstrBot",
    "小软": "AstrBot", "grok bot": "Grok Bot", "grok": "Grok Bot",
    "desktop": "桌面记录", "桌面": "桌面记录",
    "comfyui": "ComfyUI",
}


def _strip_meta(text):
    """去掉自动摘录留的那行 HTML 注释与「摘录时间」——那是元信息，不是内容。"""
    out = []
    for ln in (text or "").split("\n"):
        t = ln.strip()
        if t.startswith("<!--") or t.startswith("摘录时间") or t.startswith("自动摘录自"):
            continue
        out.append(ln)
    return "\n".join(out)


def _task_title(text, fallback):
    """任务名：优先取正文第一个像人话的标题（已滤掉元信息与系统提示）。"""
    bad = ("you are", "you must", "instructions", "system", "以下是", "规则：",
           "<", "-", "|", "#", "*")
    for ln in _strip_meta(text).split("\n"):
        t = ln.strip().lstrip("#").strip()
        if len(t) >= 6 and not t.lower().startswith(bad):
            return t[:60]
    return fallback


def _task_participants(text, owner):
    """有哪些 Agent 参与：正文里点过名的都算，再加它自己。"""
    low = (text or "").lower()
    out = []
    for k, v in AGENT_ALIASES.items():
        if k in low and v not in out:
            out.append(v)
    if owner and owner not in out:
        out.insert(0, owner)
    return out


def _task_artifacts(text, extra_paths=None):
    """产物：正文里出现且**确实存在**的文件路径，加上同会话里的实物文件。"""
    out = []
    for m in PATH_RE.findall(text or ""):
        p = m.strip().rstrip(".,;，。；）)")
        try:
            if os.path.isfile(p) and p not in out:
                out.append(p)
        except Exception:
            continue
    for p in (extra_paths or []):
        if p not in out:
            out.append(p)
    return out


# ---------- 记录根登记表（第三十七轮：就地索引取代抄录） ----------
# 爱卿之见：既然各家 Agent 的记录本来就长在各自的目录里，何必再抄一份？
# 故改成「指名各家的记录根，搜索 / 工作台 / MCP **就地读**」：
#   · 新写的内容立刻可查（不必等一轮抄录）
#   · 再不会出现「源漏写一处 ⇒ 那类记录永远抄不到」（本宫栽过两次）
#   · 不再存第二份，去掉重复
# 三档处理：
#   direct —— 直接读（绝大多数）
#   digest —— 太大/不是给人读的（如 Codex 的 jsonl 实录，单文件十几 MB），
#             摘录成可读文本，存**缓存**目录（可随时重建，不是唯一副本）
#   skip   —— 私有/二进制格式（Cursor 的 sqlite 之类），只登记位置
RECORD_ROOTS = {
    "workbuddy": [
        {"name": "WorkBuddy 会话记忆",
         "root": os.path.join(HOME, "WorkBuddy", "*", ".workbuddy", "memory"),
         "mode": "direct"},
        {"name": "WorkBuddy 长期记忆",
         "root": os.path.join(HOME, ".workbuddy", "memory"),
         "mode": "direct"},
        # 第四十三轮（爱卿指路）：WorkBuddy 的**完整会话存档**在
        #   ~/.workbuddy/projects/<项目>/<会话>.jsonl —— 一个会话一个文件，
        #   单个可达 12 MB（不是给人读的），故走摘录一档；
        #   同目录下的 .txt 是可读的，走直读。
        {"name": "WorkBuddy 会话存档",
         "root": os.path.join(HOME, ".workbuddy", "projects"), "mode": "digest",
         "glob": os.path.join(HOME, ".workbuddy", "projects", "*", "*.jsonl")},
        {"name": "WorkBuddy 项目文本",
         "root": os.path.join(HOME, ".workbuddy", "projects"), "mode": "direct"},
    ],
    "codexplus": [
        {"name": "Codex 笔记", "root": os.path.join(HOME, ".codex", "memories"),
         "mode": "direct"},
        {"name": "Codex 会话实录",
         "root": os.path.join(HOME, ".codex", "sessions"), "mode": "digest",
         "glob": os.path.join(HOME, ".codex", "sessions", "*", "*", "*", "*.jsonl")},
    ],
    "astrbot": [
        {"name": "AstrBot 会话工作区",
         "root": os.path.join(HOME, ".astrbot", "data", "workspaces"),
         "mode": "direct"},
        # 第四十二轮（爱卿令：WorkBuddy 找不到"叫你写题"那件事）——
        #   AstrBot 的**对话内容**不在文件里，在 data_v4.db 的 conversations 表里
        #   （16 行、56 MB 的 JSON）。先前只登记了 workspaces（干活的产物），
        #   于是"聊过什么"这一层完全检索不到。此处补上，走**摘录**一档：
        #   剥出纯文本、按条截断，落进缓存目录供检索。
        {"name": "AstrBot 对话记忆", "mode": "digest_sqlite",
         "root": os.path.join(HOME, ".astrbot", "data", "data_v4.db"),
         "db": os.path.join(HOME, ".astrbot", "data", "data_v4.db"),
         "table": "conversations", "key_col": "conversation_id",
         "time_col": "updated_at", "content_col": "content"},
    ],
    "cursor": [
        {"name": "Cursor 会话存储",
         "root": os.path.join(os.environ.get("APPDATA", ""), "Cursor", "User",
                              "workspaceStorage"),
         "mode": "skip"},
    ],
    "grokbot": [
        {"name": "Grok Bot 数据",
         "root": os.path.join(os.environ.get("APPDATA", ""), "Grok Bot"),
         "mode": "skip"},
    ],
    # 桌面：爱卿习惯把记录直接放桌面 —— 直读（只收 .md/.txt，不复制、不搬走）
    "desktop": [
        {"name": "桌面文本记录", "root": os.path.join(HOME, "Desktop"),
         "mode": "direct", "ext": (".md", ".txt"), "depth": 2},
    ],
}

EXTRA_ROOTS_NAME = "检索地址.json"


def extra_roots_file():
    """用户手工登记的检索地址（不在程序旁，免得被当成数据到处同步）。"""
    return os.path.join(os.environ.get("LOCALAPPDATA", HOME), "Agent资产总览",
                        EXTRA_ROOTS_NAME)


def load_extra_roots():
    """读用户登记的检索地址。

    第四十四轮（爱卿之策）：让 Agent **自报家门**（问它"你的记忆存在哪"），
    把它报出来的路径粘进应用对应 Agent 的【检索地址】里，那一处就纳入了
    主动检索 —— 比本宫去猜各家目录结构靠谱得多。
    形如：{"workbuddy": [{"path": "C:\\…", "mode": "direct"|"digest", "name": "…"}]}
    """
    try:
        d = json.load(io.open(extra_roots_file(), encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def save_extra_roots(d):
    fp = extra_roots_file()
    try:
        os.makedirs(os.path.dirname(fp), exist_ok=True)
        io.open(fp, "w", encoding="utf-8").write(json.dumps(d, ensure_ascii=False,
                                                            indent=1))
        return True
    except Exception:
        return False


_ROOTS_CACHE = {"t": 0, "v": None}


def _roots_for(key):
    """取某 Agent 的（内置 + 手工登记）记录根；缓存 5 秒，免得每文件都读盘。"""
    import time as _t
    now = _t.time()
    if not _ROOTS_CACHE["v"] or now - _ROOTS_CACHE["t"] > 5:
        _ROOTS_CACHE["v"] = all_roots()
        _ROOTS_CACHE["t"] = now
    return _ROOTS_CACHE["v"].get(key) or []


def invalidate_roots():
    _ROOTS_CACHE["t"] = 0
    _ROOTS_CACHE["v"] = None


def all_roots():
    """内置登记表 + 用户手工登记的（**据此检索与摘录**）。"""
    out = {k: [dict(r) for r in v] for k, v in RECORD_ROOTS.items()}
    for key, items in (load_extra_roots() or {}).items():
        for it in (items or []):
            p2 = str((it or {}).get("path") or "").strip()
            if not p2:
                continue
            nm = (it or {}).get("name") or (u"手工登记 · " + os.path.basename(
                p2.rstrip("\\/")) or u"手工登记")
            out.setdefault(key, []).append({
                "name": nm, "root": p2,
                "mode": (it or {}).get("mode") or "direct", "user": True})
    return out


def probe_path(p2):
    """探一个路径：有多少文件、多大、什么格式 —— 好判断该直读还是摘录。"""
    if not p2 or not os.path.exists(p2):
        return {"exists": False}
    files, tot, exts, dirs = 0, 0, {}, 0
    if os.path.isfile(p2):
        files, tot = 1, os.path.getsize(p2)
        exts[os.path.splitext(p2)[1].lower() or u"（无）"] = 1
    else:
        for dp, dns, fns in os.walk(p2):
            dns[:] = [d for d in dns if d not in ROOT_SKIP_DIRS]
            dirs += 1
            for f in fns:
                fp = os.path.join(dp, f)
                try:
                    sz = os.path.getsize(fp)
                except Exception:
                    continue
                files += 1
                tot += sz
                e = os.path.splitext(f)[1].lower() or u"（无）"
                exts[e] = exts.get(e, 0) + 1
            if files > 4000:
                break
    top = sorted(exts.items(), key=lambda x: -x[1])[:5]
    big = [e for e, _ in top if e in (".jsonl", ".ndjson", ".db", ".sqlite", ".sqlite3",
                                      ".dat", ".bin", ".log", ".json")]
    text_like = [e for e, _ in top if e in (".md", ".txt", ".markdown")]
    mode = "digest" if (tot > 2 * 1024 * 1024 or (big and not text_like)) else "direct"
    return {"exists": True, "files": files, "bytes": tot, "exts": top,
            "suggest": mode, "dirs": dirs}


ROOT_SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv",
                  "site-packages", "binaries", "cache", "blobs", "artifact-index",
                  "logs", "temp", "attachments", "tamper", "appearance-resources",
                  ".workbuddy-sqlite-migrations", "t2i_templates", "dist", "build"}
ROOT_MAX_BYTES = 1024 * 1024          # 单文件上限 1 MB（再大就不是记录，是数据）
ROOT_EXTS = (".md", ".txt")           # 记录一般就这两种；.py/.log/.json 是干活的碎屑


def digest_cache_dir():
    """摘录缓存目录（可随时重建，故不算「唯一副本」）。"""
    d = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
                     "Agent资产总览", "digest缓存")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return d


def _expand_roots(root):
    """记录根可能带通配（如 ~/WorkBuddy/*/.workbuddy/memory），展开成真实目录。"""
    if any(c in root for c in "*?["):
        try:
            return [x for x in glob.glob(root) if os.path.isdir(x)]
        except Exception:
            return []
    return [root] if os.path.isdir(root) else []


def _walk_root(root, exts=ROOT_EXTS, depth=4, cap=ROOT_MAX_BYTES):
    """遍历一个记录根里的可读记录文件。"""
    out = []
    for base in _expand_roots(root):
        for dp, dns, fns in os.walk(base):
            if dp[len(base):].count(os.sep) >= depth:
                dns[:] = []
            dns[:] = [d for d in dns if d not in ROOT_SKIP_DIRS]
            for f in fns:
                if exts and os.path.splitext(f)[1].lower() not in exts:
                    continue
                fp = os.path.join(dp, f)
                try:
                    st2 = os.stat(fp)
                except Exception:
                    continue
                if st2.st_size == 0 or st2.st_size > cap:
                    continue
                out.append(fp)
    return out


def agent_key_of(agent):
    """取 Agent 的登记键（用来对上记录根）。"""
    return (agent.get("key") or "").strip().lower()


def iter_record_files(agents, include_digest_cache=True):
    """就地索引的**统一入口**：产出 (agent_name, 文件路径, 来源名)。

    搜索、工作台、MCP 全走它 —— 不再依赖任何抄录副本。
    """
    covered = set()
    for a in (agents or []):
        if (a.get("kind") or "agent") != "agent":
            continue
        key = agent_key_of(a)
        covered.add(key)
        for r in _roots_for(key):
            if r.get("mode") not in ("direct", "digest"):
                continue
            for fp in _walk_root(r["root"], r.get("ext") or ROOT_EXTS,
                                 r.get("depth", 4)):
                yield a.get("name") or key, fp, r["name"]
        # 各 Agent **主动整理的工作记录**（`工作记录\<Agent>\`）也是记录，同样就地读。
        #    这一格是人（或 Agent 按「更新数据」提示词）写的整理稿 —— 内容最有价值，
        #    第三十七轮改就地索引时曾漏掉它，此处补回。
        wd = a.get("works_dir") or ""
        if wd and os.path.isdir(wd):
            for fp in _walk_root(wd, ROOT_EXTS, 2):
                yield a.get("name") or key, fp, "工作记录"
        # 这个 Agent 的摘录缓存也算它的记录
        if include_digest_cache:
            for fp in glob.glob(os.path.join(digest_cache_dir(), "*.md")):
                bn = os.path.basename(fp)
                for r in _roots_for(key):
                    if r.get("mode") in ("digest", "digest_sqlite") and \
                            bn.startswith(r["name"] + "_"):
                        yield a.get("name") or key, fp, r["name"] + "（摘录）"
    # 没有对应客户端的记录根也要读（如桌面 —— 爱卿习惯把记录直接放桌面）
    for key, roots in all_roots().items():
        if key in covered:
            continue
        for r in roots:
            if r.get("mode") not in ("direct", "digest"):
                continue
            for fp in _walk_root(r["root"], r.get("ext") or ROOT_EXTS,
                                 r.get("depth", 4)):
                yield AGENT_ALIASES.get(key, key), fp, r["name"]


def record_roots_of(agent):
    """某个 Agent 的记录根清单（给界面显示用）：[(来源名, 路径, 处理方式, 文件数)]。"""
    out = []
    key = agent_key_of(agent)
    for r in _roots_for(key):
        roots = _expand_roots(r["root"])
        mode = r.get("mode")
        if mode in ("direct", "digest", "digest_sqlite"):
            n = (len(glob.glob(os.path.join(digest_cache_dir(), r["name"] + "_*.md")))
                 if mode == "digest_sqlite"
                 else len(_walk_root(r["root"], r.get("ext") or ROOT_EXTS,
                                     r.get("depth", 4))))
        else:
            n = len(roots)
        out.append({"name": r["name"], "root": r["root"], "mode": mode,
                    "found": bool(roots), "count": n})
    return out


def _pick_text(obj, cap=2000):
    """从 AstrBot 的消息结构里挖出纯文本（它把消息存成嵌套 JSON）。

    `[{"role":"user","content":[{"type":"text","text":"…"}]}, …]` 这种形状，
    图片/工具结果里也可能带超长 text，故逐段截断。
    """
    out = []

    def walk(x, depth=0):
        if depth > 8 or isinstance(x, str):
            return
        if isinstance(x, dict):
            t = x.get("text")
            if isinstance(t, str) and t.strip():
                out.append(t.strip()[:cap])
            for k, v in x.items():
                if k != "text":
                    walk(v, depth + 1)
        elif isinstance(x, list):
            for i in x[:400]:
                walk(i, depth + 1)

    walk(obj)
    return "\n".join(out)


def _digest_sqlite(src, st, key, lim_bytes=160 * 1024):
    """把 SQLite 里的会话摘成可读 .md，落进缓存目录。返回 (新摘几篇, 源名)。"""
    import sqlite3
    import time as _t
    db = src.get("db")
    if not db or not os.path.isfile(db):
        return 0, src["name"]
    dest = digest_cache_dir()
    seen_map = (st.setdefault(key, {})).setdefault(src["name"], {})
    made = 0
    try:
        con = sqlite3.connect("file:%s?mode=ro" % db.replace("\\", "/"), uri=True,
                              timeout=20)
        cur = con.cursor()
        rows = list(cur.execute(
            "SELECT rowid, %s, %s, LENGTH(%s) FROM %s ORDER BY %s DESC LIMIT 200"
            % (src["key_col"], src["content_col"], src["content_col"],
               src["table"], src.get("time_col") or "rowid")))
        tcol = list(cur.execute("PRAGMA table_info(%s)" % src["table"]))
        tnames = [c[1] for c in tcol]
        con.close()
    except Exception as e:
        return 0, src["name"] + "（读库失败：%s）" % str(e)[:40]
    for rid, cid, content, clen in rows:
        tag = "%s:%s" % (db, cid)
        if seen_map.get(tag) == [int(clen or 0)]:
            continue                     # 没变过，跳过
        try:
            import json as _json
            msgs = _json.loads(content) if isinstance(content, str) else content
        except Exception:
            msgs = [{"role": "?", "content": [{"type": "text", "text": str(content)}]}]
        if not isinstance(msgs, list):
            msgs = [msgs]
        body, total = [], 0
        for m in msgs:
            if not isinstance(m, dict):
                continue
            role = {"user": u"用户", "assistant": u"助手",
                    "system": u"系统"}.get(str(m.get("role")), str(m.get("role")))
            txt = _pick_text(m.get("content") if "content" in m else m)
            if not txt:
                continue
            body.append(u"### %s\n%s" % (role, txt))
            total += len(txt)
            if total > 60000:            # 单篇上限，别把一篇做成几 MB
                body.append(u"\n（后略）")
                break
        if not body:
            continue
        out = os.path.join(dest, "%s_%s.md" % (src["name"], str(cid)[:8]))
        try:
            io.open(out, "w", encoding="utf-8").write(
                u"# %s · %s\n> 源自 `%s` 的会话记录（自动摘录，纯文本）\n\n%s\n"
                % (src["name"], str(cid)[:8], db, "\n\n".join(body)))
            seen_map[tag] = [int(clen or 0)]
            made += 1
        except Exception:
            pass
    return made, src["name"]


def cleanup_transcribed(agents, archive=True):
    """清掉早期抄录留下的副本（内容与原生目录重复）。

    安全底线：**删之前先确认源还在** —— 源里找不到这条记录的踪影，就留着
    （那可能是唯一一份）。默认不硬删，而是移进 `备份/抄录副本_<日期>/`，
    真要删就把那个文件夹删掉（一步的事，且可反悔）。
    """
    import shutil
    import time as _t
    prefixes = []
    for roots in RECORD_ROOTS.values():
        for r in roots:
            prefixes.append(r["name"] + "_")
    prefixes += ["WorkBuddy 会话记忆_", "WorkBuddy 长期记忆_", "Codex 笔记_",
                 "Codex 会话实录_", "AstrBot 会话工作区_", "自动抄录"]
    # 先把所有原生记录根的**路径**收集起来，用来判断「源还在不在」
    live = []
    for a in (agents or []):
        if (a.get("kind") or "agent") != "agent":
            continue
        for _who, fp, _src in iter_record_files([a], include_digest_cache=False):
            live.append(fp)
    for key, roots in RECORD_ROOTS.items():
        for r in roots:
            for d in _expand_roots(r["root"]):
                live.append(d)
            if r.get("mode") == "digest" and r.get("glob"):
                # 摘录档的「源」是那些大文件本身（如 .jsonl），也得算进来
                try:
                    live.extend(glob.glob(r["glob"], recursive=True))
                except Exception:
                    pass
    live_blob = "\n".join(live).lower()
    box = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "备份", "抄录副本_" + _t.strftime("%Y%m%d-%H%M"))
    moved, kept = [], []
    for a in (agents or []):
        base = a.get("works_dir") or ""
        if not base or not os.path.isdir(base):
            continue
        for entry in sorted(os.listdir(base)):
            if not any(entry.startswith(x) for x in prefixes):
                continue
            if entry.lower().endswith(".bak_before_agentinventory"):
                continue
            src = os.path.join(base, entry)
            tail = entry.split("_", 1)[1] if "_" in entry else entry
            tail = os.path.splitext(tail)[0].lower()
            if tail and tail not in live_blob:
                kept.append((a.get("name"), entry))       # 源里找不到 → 留着
                continue
            try:
                if archive:
                    os.makedirs(box, exist_ok=True)
                    shutil.move(src, os.path.join(box, (a.get("name") or "?") + "_" + entry))
                elif os.path.isdir(src):
                    shutil.rmtree(src, ignore_errors=True)
                else:
                    os.remove(src)
                moved.append((a.get("name"), entry))
            except Exception as e:
                kept.append((a.get("name"), entry + "（失败：%s）" % str(e)[:30]))
    return {"moved": moved, "kept": kept, "box": box if archive else "",
            "live_count": len(live)}


def collect_tasks(agents):
    """把各 Agent 自动抄录下来的日志整理成「任务一览」。"""
    import time as _t
    tasks = []
    for a in (agents or []):
        if (a.get("kind") or "agent") != "agent":
            continue
        owner = a.get("name") or ""
        base = a.get("works_dir") or ""
        if not base or not os.path.isdir(base):
            continue
        for src_name in ["工作记录"]:            # 外层只跑一次：直接扫这格
            src_dir = base
            if not os.path.isdir(src_dir):
                continue
            for entry in sorted(os.listdir(src_dir)):
                if entry == "自动抄录":
                    continue                     # 旧版留下的分层目录，忽略
                ep = os.path.join(src_dir, entry)
                text, arts = "", []
                if os.path.isdir(ep):
                    for dp, _dn, fns in os.walk(ep):
                        for f in fns:
                            arts.append(os.path.join(dp, f))
                    for f in sorted(os.listdir(ep)):
                        fp = os.path.join(ep, f)
                        if os.path.isfile(fp) and os.path.splitext(f)[1].lower() in (".md", ".txt"):
                            try:
                                text = open(fp, "r", encoding="utf-8", errors="ignore").read()[:4000]
                            except Exception:
                                text = ""
                            if text:
                                break
                else:
                    try:
                        text = open(ep, "r", encoding="utf-8", errors="ignore").read()[:20000]
                    except Exception:
                        text = ""
                    # 日志本身不算「产物」—— 产物是它记录下来的东西
                try:
                    mt = os.path.getmtime(ep)
                    stamp = _t.strftime("%Y-%m-%d %H:%M", _t.localtime(mt))
                except Exception:
                    mt, stamp = 0, ""
                did = " ".join(_strip_meta(text).split())[:400]
                tasks.append({
                    "key": "task_" + re.sub(r"\W+", "_", (owner + "_" + src_name + "_" + entry)).lower()[:60],
                    "agent": owner,
                    "source": src_name,
                    "title": _task_title(text, entry),
                    "did": did or "（本会话无文字记录，仅存产物）",
                    "participants": _task_participants(text, owner),
                    "artifacts": _task_artifacts(text, arts),
                    "log": ep,
                    "when": stamp,
                    "mtime": int(mt),
                })
    # 第三十七轮（爱卿令）：就地索引 —— 直接读各 Agent 的原生记录根，不再依赖抄录副本
    for a in (agents or []):
        if (a.get("kind") or "agent") != "agent":
            continue
        pass
    for owner, fp, src_name in iter_record_files(agents):
            try:
                text = open(fp, "r", encoding="utf-8", errors="ignore").read()[:20000]
                mt = os.path.getmtime(fp)
                stamp = _t.strftime("%Y-%m-%d %H:%M", _t.localtime(mt))
            except Exception:
                continue
            tasks.append({
                "key": "task_root_" + re.sub(r"\W+", "_", (owner + "_" + fp)).lower()[:60],
                "agent": owner,
                "source": src_name,
                "title": _task_title(text, os.path.basename(fp)),
                "did": " ".join(_strip_meta(text).split())[:400] or "（无文字记录）",
                "participants": _task_participants(text, owner),
                "artifacts": _task_artifacts(text, []),
                "log": fp,
                "when": stamp,
                "mtime": int(mt),
            })
    # 同一份记录可能既被原生读到、又是旧抄录留下的副本 —— 按真实路径去重
    seen_rp, uniq = set(), []
    for t in tasks:
        try:
            rp = os.path.realpath(t.get("log") or "")
        except Exception:
            rp = t.get("log") or ""
        if rp and rp in seen_rp:
            continue
        if rp:
            seen_rp.add(rp)
        uniq.append(t)
    tasks = uniq
    tasks.sort(key=lambda t: -t.get("mtime", 0))
    return tasks


LOG_STATE_NAME = "agent_logs_state.json"


def log_state_path():
    """抄录水位：放 %LOCALAPPDATA%\\<名字>，不动程序旁的数据文件。"""
    base = os.environ.get("LOCALAPPDATA") or HERE
    d = os.path.join(base, "Agent_asset_overview_logs")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        d = HERE
    return os.path.join(d, LOG_STATE_NAME)


def load_log_state():
    try:
        with open(log_state_path(), "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def save_log_state(st):
    try:
        with open(log_state_path(), "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False, indent=1)
        return True
    except Exception:
        return False


def _digest_jsonl(src, dst):
    """把 Codex 那种会话实录（jsonl）摘成可读的 .md。

    不猜 schema：递归找出所有像"正文"的 text 字段，按序落成段落。
    """
    import json as _j
    import time as _t
    texts = []

    SYS_HINTS = ("you are codex", "you are an ai", "you are a helpful",
                 "you must ", "system prompt", "follow these instructions",
                 "you are judging", "instructions:", "tool description")

    def dig(o, out):
        if isinstance(o, dict):
            for k, v in o.items():
                if k == "text" and isinstance(v, str) and len(v.strip()) > 8:
                    low = v.strip().lower()
                    if v.strip().startswith("<"):
                        continue          # <app-context> / <permissions…> 这类元块
                    if any(h in low[:160] for h in SYS_HINTS):
                        continue          # 系统提示词不算「做了什么」
                    out.append(v.strip())
                else:
                    dig(v, out)
        elif isinstance(o, list):
            for v in o:
                dig(v, out)

    try:
        with open(src, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    dig(_j.loads(line), texts)
                except Exception:
                    continue
    except Exception:
        return 0
    seen, keep, size = set(), [], 0
    for t in texts:
        key = t[:120]
        if key in seen:
            continue
        seen.add(key)
        keep.append(t)
        size += len(t.encode("utf-8", "ignore"))
        if size >= LOG_DIGEST_MAX:
            break
    if not keep:
        return 0
    head = ("<!-- 自动摘录自：%s\n     摘录时间：%s，共 %d 段 -->\n\n"
            % (src, _t.strftime("%Y-%m-%d %H:%M"), len(keep)))
    try:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, "w", encoding="utf-8") as f:
            f.write(head)
            for t in keep:
                f.write(t.replace("\r", "") + "\n\n---\n\n")
    except Exception:
        return 0
    return len(keep)


def sync_agent_logs(agents, state=None):
    """抄一轮各 Agent 的日志。返回 (抄了几个文件, 摘了几篇, 涉及的 Agent 名)。

    只搬（或摘）**新增或有改动**的 —— 靠 state 里记的 (mtime, size) 判断，
    故反复调用不会重复搬运。
    """
    import time as _t
    import glob as _glob
    st = state if isinstance(state, dict) else load_log_state()
    n_copy = n_dig = 0
    touched = []
    for a in (agents or []):
        if (a.get("kind") or "agent") != "agent":
            continue
        key = (a.get("key") or "").strip()
        # 第三十七轮：**只做摘录**这一档（直读档已改成就地索引，不再搬运）。
        # 摘录产物落进缓存目录 —— 可随时重建，不是唯一副本。
        # ④ 数据库型（AstrBot 的对话记忆）：摘成 md 落缓存
        for r in _roots_for(key):
            if r.get("mode") == "digest_sqlite":
                n2, who2 = _digest_sqlite(r, st, key)
                if n2:
                    n_dig += n2
                    if who2 not in touched:
                        touched.append(who2)
        srcs = [r for r in _roots_for(key)
                if r.get("mode") == "digest" and r.get("glob")]
        if not srcs:
            continue
        dest_root = digest_cache_dir()          # 摘录产物：缓存目录（非记录夹）
        # 第二十九轮（爱卿令）：抄录产物**直接落进这格**，不再套「自动抄录\」子目录 ——
        # 自动抄录本就是要替掉那段要人手动粘贴的提示词，产物就该长在同一个地方。
        sub = dest_root
        for src in srcs:
            if src.get("mode") == "skip":
                continue
            try:
                files = _glob.glob(src["glob"], recursive=True)
            except Exception:
                continue
            seen_map = (st.setdefault(key, {})).setdefault(src["name"], {})
            for fp in sorted(files):
                if not os.path.isfile(fp):
                    continue
                base = os.path.basename(fp)
                if os.path.splitext(base)[1].lower() not in LOG_TEXT_EXTS and \
                        src.get("mode") != "digest":
                    continue
                try:
                    sz = os.path.getsize(fp)
                    mt = int(os.path.getmtime(fp))
                except Exception:
                    continue
                rec = seen_map.get(fp)
                if rec and rec[0] == mt and rec[1] == sz:
                    continue                     # 没变过，跳过
                if src.get("mode") == "digest":
                    out = os.path.join(sub, "%s_%s.md" % (
                        src["name"], os.path.splitext(os.path.basename(fp))[0]))
                    got = _digest_jsonl(fp, out)
                    if got:
                        n_dig += 1
                        seen_map[fp] = [mt, sz]
                        if a.get("name") not in touched:
                            touched.append(a.get("name"))
                    continue
                if sz > LOG_MAX_COPY or n_copy >= LOG_MAX_FILES:
                    seen_map[fp] = [mt, sz]      # 太大多记一笔，免得每轮都试
                    continue
                try:
                    root0 = src["glob"].split("*")[0].rstrip("\\/")
                    rel = os.path.relpath(fp, root0)
                except Exception:
                    rel = base
                parts = rel.split(os.sep)
                if len(parts) > 1:
                    # 会话树：根目录下一个「来源_会话」文件夹，内部结构保留
                    dst = os.path.join(sub, "%s_%s" % (src["name"], parts[0]),
                                       *parts[1:])
                else:
                    dst = os.path.join(sub, "%s_%s" % (src["name"], rel))
                try:
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    if not os.path.isfile(dst) or os.path.getsize(dst) != sz:
                        import shutil as _sh
                        _sh.copy2(fp, dst)
                        n_copy += 1
                        if a.get("name") not in touched:
                            touched.append(a.get("name"))
                except Exception:
                    continue
                seen_map[fp] = [mt, sz]
    st["_last"] = _t.strftime("%Y-%m-%d %H:%M:%S")
    save_log_state(st)
    return n_copy, n_dig, touched


# ---------- 各 Agent 的工作记录（第二十四轮） ----------
# 爱卿令：每个 Agent 都要像 WorkBuddy 那样有自己的工作台，导入的数据按此收编。
# 全部落在应用内「通用资源\工作记录\<Agent名>」—— 一格一个 Agent，互不越界。
WORKS_ROOT_NAME = os.path.join("通用资源", "工作记录")


def works_root():
    return os.path.join(HERE, WORKS_ROOT_NAME)


def agent_works_dir(agent):
    """某 Agent 的工作记录夹；没建过就顺手建（建不动也不炸，只返回路径）。"""
    name = (agent.get("name") or agent.get("key") or "未命名").strip()
    name = re.sub(r'[\\/:*?"<>|]', "_", name)
    d = os.path.join(works_root(), name)
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return d


def collect_agent_works(agent):
    """收编该 Agent 工作记录夹里的东西，格式与 collect_workspace 一致，

    如此工作台窗那套按 role 分组的画法照旧能用 —— 导进来的东西看着就跟
    WorkBuddy 名下那些一模一样。
    """
    d = agent_works_dir(agent)
    out = []
    try:
        names = sorted(os.listdir(d))
    except Exception:
        return out
    for f in names:
        p = os.path.join(d, f)
        try:
            if os.path.isdir(p):
                n = len(os.listdir(p))
                out.append({"key": "agwork_" + re.sub(r"\W+", "_", f).lower(),
                            "name": f + "\\", "role": "工作区目录", "path": p,
                            "size": 0,
                            "desc": "该 Agent 的工作记录子目录（%d 项）。" % n})
            else:
                out.append({"key": "agwork_" + re.sub(r"\W+", "_", f).lower(),
                            "name": f, "role": "工作区文件", "path": p,
                            "size": os.path.getsize(p), "desc": _file_note(p)})
        except Exception:
            continue
    return out


def collect():
    agents = []
    for k in KNOWN:
        a = dict(k)
        a["exe"] = _expand(a["exe"])          # 先还原占位符，再判在否
        exe = a["exe"]
        a["verified"] = os.path.isfile(exe)
        # 登记路径落空 —— 掘一层 / 全盘寻真身（爱卿令：只要在就一定找得着）
        if not a["verified"] and a.get("key") in SIGNATURES:
            hint = os.path.dirname(exe)
            found = deep_find(a["key"], hint_dir=hint)
            if found:
                a["exe"] = found
                a["verified"] = True
                a["source"] = "全盘寻得"
        a["exists"] = a["verified"]
        if not _icon_exists(a.get("icon")):
            a["icon"] = ""
        a.setdefault("source", "内置登记")
        if a.get("hidden") or _blocked(a["name"]):
            continue
        # ★ 爱卿之令：没检索到的不显示
        #   （第十八轮改：本工具自身也一并不上榜 —— 它本不是 Agent）
        if not a["exists"]:
            continue
        agents.append(a)

    # ★ WorkBuddy 名下挂「工作区散件」：别的 Agent 想接力，点开它即可看全
    #   （爱卿第十五轮之令）。只挂得到的、且确有文件的。
    ws_works = collect_workspace()
    for a in agents:
        if (a.get("kind") or "agent") != "agent":
            continue                      # 只有真 Agent 有工作台
        d = agent_works_dir(a)
        mine = collect_agent_works(a)
        # WorkBuddy 另有自己的历史工作区（HERE 那堆源码与文稿），一并算上
        a["works"] = (mine + ws_works) if a.get("key") == "workbuddy" else mine
        a["works_dir"] = d

    # 补二：桌面上那些说明文档（正文一并收进来）
    for doc in collect_docs():
        a = dict(doc)
        a["verified"] = os.path.isfile(a["exe"])
        a["exists"] = a["verified"]
        a["kind"] = "doc"
        if a.get("hidden") or _blocked(a["name"]):
            continue
        agents.append(a)

    # 补三：手动添入的 Agent（存 %LOCALAPPDATA%，不动程序旁）
    for a in load_manual():
        if _blocked(a.get("name", "")):
            continue
        a = dict(a)
        a["verified"] = os.path.isfile(a.get("exe", ""))
        a["exists"] = a["verified"]
        a["kind"] = "agent"
        a["source"] = "手动添加"
        if not _icon_exists(a.get("icon")):
            a["icon"] = ""
        agents.append(a)

    # 补四：快捷方式里的额外 agent（去重 + 屏蔽名单）
    have = {_norm(a["name"]) for a in agents}
    for lnk in probe_shortcuts():
        nm = os.path.splitext(os.path.basename(lnk))[0]
        if _blocked(nm):
            continue
        if _norm(nm) in have:
            continue
        have.add(_norm(nm))
        agents.append({
            "key": "lnk_" + re.sub(r"\W+", "_", nm).lower(),
            "name": nm,
            "desc": "来自快捷方式的 Agent 入口。",
            "exe": lnk,
            "icon": "",
            "category": "快捷入口",
            "source": "快捷方式扫描",
            "kind": "agent",
            "verified": os.path.isfile(lnk),
            "exists": os.path.isfile(lnk),
        })
    # 出口统一还原占位符：`@HOME@` → 本机家目录，并盖一枚产地戳
    import time as _t            # 局部导入：动态载入时用的，顶上导入易被打包器漏掉
    # 工作任务及产物一览：原料就是上面自动抄录下来的那堆日志
    tasks = collect_tasks(agents)

    return {"agents": _expand_rec(agents),
            "tasks": _expand_rec(tasks),
            "_home": machine_tag(),
            "_made": _t.strftime("%Y-%m-%d %H:%M:%S")}


# ---------- 3. agent_inventory.json：技能/MCP/工具诸栏的命脉 ----------
# 这份清单由另一支扫描器产出（记录 skills / mcp / tools / skill_shelves）。
# 第八轮之教训：它当时只作 exe 的内嵌静态数据，外界那份被当垃圾清了，
# 遂致技能栏整栏失声。今令本脚本兼任「看守」：
#   ① 文件在 → 原样保留，只报条数；
#   ② 文件缺 → 自动补跑 scan_agents.py 重产；
#   ③ 仍产不出 → 立一份最小骨架，至少不让程序开天窗。
INVENTORY = os.path.join(HERE, "agent_inventory.json")
INVENTORY_KEYS = ("skills", "agents", "mcp", "skill_shelves")
GENERATOR = os.path.join(HERE, "scan_agents.py")


def _brief_inventory(obj):
    if not isinstance(obj, dict):
        return "（非字典）"
    return " ".join("%s=%d" % (k, len(obj.get(k) or [])) for k in INVENTORY_KEYS)


def _empty_inventory():
    obj = {}
    for k in INVENTORY_KEYS:
        obj[k] = []
    return obj


def _is_local(obj):
    """这份名册是不是**本机**所产？（看产地戳）

    三种情形：
      · 戳与本机合 → 是（`True`）
      · 戳不合      → 否（随包带来的老底册，须重扫）
      · 无戳        → 视作否（旧版名册一律重扫，宁可多扫一次）
    """
    if not isinstance(obj, dict):
        return False
    return str(obj.get("_home", "")).strip().lower() == machine_tag()


def ensure_inventory():
    """确保 agent_inventory.json 存在、可读、**且为本机所产**；否则重产。

    返回 (状态语, 数据)。
    """
    if os.path.isfile(INVENTORY):
        try:
            with open(INVENTORY, "r", encoding="utf-8") as fh:
                obj = json.load(fh)
            if (isinstance(obj, dict) and any(obj.get(k) for k in INVENTORY_KEYS)
                    and _is_local(obj)):
                return "原有清单 -> " + _brief_inventory(obj), obj
            if isinstance(obj, dict) and any(obj.get(k) for k in INVENTORY_KEYS):
                note = "原有清单是**别的电脑**所产（戳为 %r）" % str(obj.get("_home", ""))[:40]
            else:
                note = "原有清单是空壳"
        except Exception as e:
            note = "原有清单读不动（%s）" % e
    else:
        note = "原有清单不存在"

    # ② 请原产者出手
    if os.path.isfile(GENERATOR):
        try:
            import subprocess as _sp
            r = _sp.run([sys.executable, GENERATOR], cwd=HERE,
                        capture_output=True, text=True, timeout=180)
            if os.path.isfile(INVENTORY):
                with open(INVENTORY, "r", encoding="utf-8") as fh:
                    obj = json.load(fh)
                if (isinstance(obj, dict) and any(obj.get(k) for k in INVENTORY_KEYS)
                        and _is_local(obj)):
                    return "%s，已补跑扫描器 -> %s" % (note, _brief_inventory(obj)), obj
            tail = (r.stderr or r.stdout or "").strip().splitlines()
            note += "，补跑扫描器未成（%s）" % (tail[-1][:60] if tail else "无输出")
        except Exception as e:
            note += "，补跑扫描器出错（%s）" % e
    else:
        note += "，且扫描器 scan_agents.py 亦不在"

    # ③ 兜底骨架（亦须带本机戳，免得下回又判为「他机所产」而反复重扫）
    import time as _t
    obj = _empty_inventory()
    obj["_home"] = machine_tag()
    obj["_made"] = _t.strftime("%Y-%m-%d %H:%M:%S")
    with open(INVENTORY, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=1)
    return note + "，已立空骨架（技能/MCP/插件栏将为 0）", obj


def main():
    data = collect()
    out = os.path.join(HERE, "agents.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    ok = sum(1 for a in data["agents"] if a.get("exists"))
    print("agents=%d, 可用=%d -> %s" % (len(data["agents"]), ok, out))
    for a in data["agents"]:
        print("  [%s] %-22s %s" % ("✓" if a["exists"] else "×", a["name"],
                                   a.get("exe", "")[:76]))
    inv_note, _inv = ensure_inventory()
    print("清单：" + inv_note)
    _stash_portable()


def _stash_portable():
    """把两份名册另存一份进 `便携数据\\`，供换机后自举。

    本机重扫时，扫描器常是从 exe 解出的**临时目录**里跑的（其 HERE 非 exe 旁），
    故产出未必落在用户看得见的地方。这里再存一份到脚本旁的 `便携数据\\`，
    打包时随 exe 一同带走 —— 到新电脑解压后，程序即便来不及重扫，
    也能先照这份快照把界面摆出来。
    """
    d = os.path.join(HERE, "便携数据")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        return
    for fn in ("agents.json", "agent_inventory.json"):
        src = os.path.join(HERE, fn)
        if not os.path.isfile(src):
            continue
        try:
            with open(src, "r", encoding="utf-8") as a:
                obj = json.load(a)
            with open(os.path.join(d, fn), "w", encoding="utf-8") as b:
                json.dump(obj, b, ensure_ascii=False, indent=1)
            print("    已存便携副本：便携数据\\" + fn)
        except Exception as e:
            print("    便携副本未成（%s）：%s" % (fn, e))


if __name__ == "__main__":
    main()
