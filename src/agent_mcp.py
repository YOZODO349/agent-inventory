# -*- coding: utf-8 -*-
"""Agent 资产总览 · MCP 服务端（stdio，纯标准库，零第三方依赖）

为何自己实现协议：本应用刻意不引第三方库（界面只用标准库 tkinter）。
MCP 的 stdio 传输就是「一行一个 JSON-RPC 报文」，自己实现不过百行，
比为一个服务端把整套依赖拖进来划算。

用法（两种身份，同一个二进制）：
    python agent_mcp.py                 # 以 MCP 服务端运行（stdio）
    Agent资产总览.exe --mcp             # 打包后同理
    python agent_mcp.py --demo search 关键词    # 不开 MCP，直接看某工具的输出（调试用）

暴露给 Agent 的工具：
    inventory_overview   本机总览（各栏计数 + 主要项目 + 最近动态）
    inventory_search     四栏 + 各 Agent 工作记录全文检索（给文件·行号·上下文）
    inventory_project    某个主要项目的全貌（已实现功能 / 开发日志 / 产物地址）
    inventory_agents     本机 Agent 名册（含工作记录目录与启动方式）
    inventory_recent     最近 N 天各家 Agent 干了什么
    inventory_skills     技能库检索
    inventory_reindex    重扫本机（可选顺手抄录日志）—— 让 Agent 能主动刷新数据
"""
import json
import os
import re
import sys
import time

VERSION = "0.5.3"

# MCP 的数据通道是 stdin/stdout。Windows 上打包后这两条流有三重坑：
#   ① 无控制台（console=False）时 PyInstaller 会把它们置为 None；
#   ② 有控制台时默认按系统编码（简体中文机上是 GBK）读写 —— 中文一进去就乱码、
#      参数（如项目名）传进来直接解坏；
#   ③ 窗口模式下 reconfigure() 会静默失败，改了等于没改。
# 故此处**一律从文件描述符重建**（由 MCP 客户端拉起时 fd 0/1/2 必是有效的管道），
# 重建不成再退回原地 reconfigure。
for _fd, _nm, _md in ((0, "stdin", "r"), (1, "stdout", "w"), (2, "stderr", "w")):
    try:
        setattr(sys, _nm, os.fdopen(_fd, _md, encoding="utf-8",
                                    errors="replace", buffering=(1 if _md == "w" else -1)))
    except Exception:
        try:
            _cur = getattr(sys, _nm, None)
            if _cur is not None:
                _cur.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


SERVER_NAME = "agent-inventory"
PROTOCOL = "2024-11-05"
MAX_READ = 1024 * 1024          # 单文件读取上限（1 MB）
TEXT_EXT = (".md", ".txt", ".json", ".log")


def app_dir():
    """应用所在目录：打包后是 exe 旁，源码运行时是本文件旁。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


HERE = app_dir()


def log(msg):
    """日志一律走 stderr —— stdout 是 MCP 的数据通道，混一句话就废了。"""
    try:
        sys.stderr.write("[agent-inventory-mcp] %s\n" % msg)
        sys.stderr.flush()
    except Exception:
        pass


def load_json(name, default):
    for base in (HERE, os.path.join(HERE, "src"),
                 os.environ.get("LOCALAPPDATA", HERE)):
        p = os.path.join(base, name)
        if os.path.isfile(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                log("读 %s 失败：%s" % (p, e))
    return default


def load_agents_data():
    d = load_json("agents.json", {})
    return (d.get("agents") or []), (d.get("tasks") or [])


def load_inventory():
    return load_json("agent_inventory.json", {})


def load_projects():
    d = load_json("主要项目.json", {})
    out = []
    for p in (d.get("projects") or []):
        if not isinstance(p, dict) or not p.get("name"):
            continue
        q = dict(p)
        keys = []
        for k in (q.get("keys") or []):
            for part in re.split(u"[、,\uFF0C;\uFF1B/|\\s]+", str(k)):
                part = part.strip()
                if part and part not in keys:
                    keys.append(part)
        q["keys"] = keys
        out.append(q)
    return out


SETTINGS_NAME = "MCP设置.json"


def load_settings():
    """本应用的 MCP 相关设置。

    mask_paths: 脱敏 —— 把本机用户名目录折成 <用户目录>。
    Agent 调用工具读到的内容会随对话进模型上下文，介意路径外泄时打开它。
    """
    d = load_json(SETTINGS_NAME, {})
    if not isinstance(d, dict):
        d = {}
    return {"mask_paths": bool(d.get("mask_paths", False)),
            "auto_attach": bool(d.get("auto_attach", True))}


def mask_text(text):
    """按设置脱敏：家目录、应用目录、以及路径里的用户名。"""
    if not load_settings().get("mask_paths"):
        return text
    home = os.path.expanduser("~")
    user = os.path.basename(home)
    out = text.replace(home, u"<用户目录>").replace(HERE, u"<应用目录>")
    if user:
        out = out.replace("\\" + user + "\\", u"\\<用户名>\\")
        out = out.replace("/" + user + "/", u"/<用户名>/")
        out = out.replace(user + "@", u"<用户名>@")
    return out


def load_intros():
    d = load_json("agent_intros.json", {})
    return d.get("intros") if isinstance(d, dict) and "intros" in d else d


# ---------------- 文本缓存：一个进程内每文件只读一次 ----------------
_TEXT = {}


def read_text(path):
    try:
        st = os.stat(path)
    except Exception:
        return ""
    key = (path, int(st.st_mtime), st.st_size)
    got = _TEXT.get(path)
    if got and got[0] == key:
        return got[1]
    if st.st_size > MAX_READ:
        return ""
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            t = f.read()
    except Exception:
        t = ""
    _TEXT[path] = (key, t)
    return t


def name_date(path):
    """从文件名里取真实日期（YYYY-MM-DD）。

    为何要它：抄录进来的记录，文件 mtime 是**抄的那一时刻**（今天），
    若拿它当时间用，几十份旧记录会一起冒充「最近刚干的活」。
    文件名里的日期才是真的（`…_2026-09-12-11-26-03`、`rollout-2026-09-12T…`）。
    """
    m = re.search(r"(20\d\d)[-_]?(\d\d)[-_]?(\d\d)", os.path.basename(path))
    if not m:
        return ""
    y, mo, d = m.group(1), m.group(2), m.group(3)
    try:
        if not (1 <= int(mo) <= 12 and 1 <= int(d) <= 31):
            return ""
    except Exception:
        return ""
    return "%s-%s-%s" % (y, mo, d)


def title_date(title):
    """任务标题里常自带日期（`2026-09-12｜樱境物语自动化…`），优先取它。"""
    m = re.search(r"(20\d\d-\d\d-\d\d)", str(title or ""))
    return m.group(1) if m else ""


def stamp(path):
    """该记录的「日期」：优先文件名里的真日期，退而用 mtime。"""
    return name_date(path) or time.strftime("%Y-%m-%d", time.localtime(os.path.getmtime(path)))


def _scanner():
    """动态载入采集器（它持有「记录根登记表」——就地索引的唯一真源）。"""
    import importlib.util
    for d in (HERE, os.path.join(HERE, "src"), getattr(sys, "_MEIPASS", "") or HERE):
        fp = os.path.join(d, "scan_agents_apps.py")
        if os.path.isfile(fp):
            try:
                spec = importlib.util.spec_from_file_location("scanner_mcp", fp)
                mod = importlib.util.module_from_spec(spec)
                sys.modules["scanner_mcp"] = mod
                spec.loader.exec_module(mod)
                return mod
            except Exception as e:
                log("载入采集器失败：%s" % e)
    return None


def record_files():
    """产出 (agent_name, 文件路径) —— **就地读各家原生记录根**，不看抄录副本。

    第三十七轮：抄录副本已废，搜索直接读 `RECORD_ROOTS` 里登记的目录
    （含桌面），新写的内容立刻可查。
    """
    agents, _tasks = load_agents_data()
    mod = _scanner()
    if mod is not None and hasattr(mod, "iter_record_files"):
        try:
            for name, fp, _src in mod.iter_record_files(
                    [a for a in agents if (a.get("kind") or "agent") == "agent"]):
                yield {"name": name}, fp
            return
        except Exception as e:
            log("就地索引失败，退回扫记录夹：%s" % e)
    # 退路：老办法（扫各 Agent 的记录夹）
    for a in agents:
        if (a.get("kind") or "agent") != "agent":
            continue
        root = a.get("works_dir") or ""
        if not root or not os.path.isdir(root):
            continue
        for dp, dns, fns in os.walk(root):
            if dp[len(root):].count(os.sep) > 2:
                dns[:] = []
                continue
            dns[:] = [d for d in dns if d not in (".git", "__pycache__", "node_modules")]
            for f in fns:
                if os.path.splitext(f)[1].lower() in TEXT_EXT:
                    yield a, os.path.join(dp, f)


def _hit_block(path, text, idx, width=300):
    """只给**命中所在那一行**（去掉首尾空白、截断）。

    从前按字符窗口切，长行会把半篇 JSON 糊进来 —— Agent 读到一堆噪音不如不给。
    """
    line_no = text[:idx].count("\n") + 1
    start = text.rfind("\n", 0, idx) + 1
    end = text.find("\n", idx)
    if end < 0:
        end = len(text)
    ln = " ".join(text[start:end].split())
    return "  - `%s` 第 %d 行\n    %s" % (path, line_no, ln[:width])


def tool_search(query, scope="all", limit=20):
    if not query:
        return u"请给个关键词。"
    q = query.lower()
    lim = max(1, min(int(limit or 20), 100))
    lines = [u"# 检索：%s（范围 %s）" % (query, scope), u""]
    total = 0
    # ① 工作记录全文
    if scope in ("all", "records"):
        lines.append(u"## 工作记录")
        n = 0
        for a, fp in record_files():
            if n >= lim:
                break
            t = read_text(fp)
            if not t:
                continue
            low = t.lower()
            i = low.find(q)
            # 文件名里带关键词也算命中 —— 记录往往靠文件名就能认出
            # （如 `2026-09-19_AI生图_深海星空水母.md`），只搜正文会漏。
            if i < 0 and q in os.path.basename(fp).lower():
                lines.append(u"- **[%s]** %s（文件名命中）"
                             % (a.get("name"), os.path.basename(fp)))
                lines.append(u"  - `%s`" % fp)
                n += 1
                continue
            # 会话实录是 jsonl 转出来的，命中行常是原始 JSON —— 那种片段给 Agent 没用，
            # 往后找下一个「像人话」的命中行
            while i >= 0:
                ls = t.rfind("\n", 0, i) + 1
                le = t.find("\n", i)
                seg = t[ls:le if le > 0 else len(t)].strip()
                if seg[:1] in '{"[' or '":' in seg:
                    i = low.find(q, i + 1)
                    continue
                break
            if i < 0:
                continue
            lines.append(u"- **[%s]** %s" % (a.get("name"), os.path.basename(fp)))
            lines.append(_hit_block(fp, t, i))
            n += 1
        total += n
        if not n:
            lines.append(u"  （无命中）")
    # ② 名册三栏
    inv = load_inventory()
    if scope in ("all", "skills"):
        hit = [s for s in (inv.get("skills") or [])
               if q in (" ".join([str(s.get("name", "")), str(s.get("desc", "")),
                                  " ".join(s.get("tags") or [])])).lower()]
        if hit or scope == "skills":
            lines += [u"", u"## 技能（%d 条命中）" % len(hit)]
            for s in hit[:lim]:
                lines.append(u"- **%s** ｜ %s" % (s.get("name"), (s.get("desc") or "")[:90]))
                lines.append(u"  路径：`%s`" % s.get("path"))
    if scope in ("all", "mcp"):
        hit = [m for m in (inv.get("mcp") or [])
               if q in json.dumps(m, ensure_ascii=False).lower()]
        if hit or scope == "mcp":
            lines += [u"", u"## MCP 服务（%d 条命中）" % len(hit)]
            for m in hit[:lim]:
                lines.append(u"- **%s**（%s）｜ `%s`" % (m.get("name"), m.get("client"),
                                                        m.get("command")))
    agents, tasks = load_agents_data()
    if scope in ("all", "agents"):
        hit = [a for a in agents
               if q in json.dumps(a, ensure_ascii=False).lower()]
        if hit or scope == "agents":
            lines += [u"", u"## Agent（%d 条命中）" % len(hit)]
            for a in hit[:lim]:
                lines.append(u"- **%s**（%s）｜ 工作记录：`%s`"
                             % (a.get("name"), a.get("category"), a.get("works_dir")))
    if scope in ("all", "projects"):
        hit = []
        for pr in load_projects():
            keys = [str(k).lower() for k in (pr.get("keys") or [])] or [str(pr["name"]).lower()]
            if any(k in q or q in k for k in keys):
                hit.append(pr)
        if hit:
            lines += [u"", u"## 主要项目"]
            for pr in hit:
                lines.append(u"- **%s** ｜ 关键词：%s" % (pr.get("name"),
                                                        u"、".join(pr.get("keys") or [])))
                lines.append(u"  查看全貌：inventory_project(name=\"%s\")" % pr.get("name"))
    lines += [u"", u"（命中示例仅列前 %d 条；要更精确可加 scope 或换关键词。）" % lim]
    return "\n".join(lines)


def _project_hit_tasks(keys, tasks):
    out = []
    for t in tasks:
        blob = " ".join([str(t.get("title") or ""), str(t.get("did") or ""),
                         " ".join(t.get("artifacts") or []), str(t.get("log") or "")]).lower()
        if any(k in blob for k in keys):
            out.append(t)
    return out


def _project_records(keys, limit=30):
    out, seen = [], set()
    for a, fp in record_files():
        if len(out) >= limit:
            break
        t = read_text(fp)
        if not t:
            continue
        low = t.lower()
        pos = [low.find(k) for k in keys]
        pos = [x for x in pos if x >= 0]
        if not pos:
            continue
        if fp in seen:
            continue
        seen.add(fp)
        out.append((a, fp, t, min(pos)))
    return out


def tool_project(name=""):
    projects = load_projects()
    if not name:
        if not projects:
            return (u"本机还没设置「主要项目」。请在人机界面「主要项目」栏里"
                    u"点「设置主要项目」添加，或在 主要项目.json 里写。")
        out = [u"# 主要项目（共 %d 个）" % len(projects), u""]
        for pr in projects:
            out.append(u"- **%s** ｜ 关键词：%s" % (pr.get("name"),
                                                 u"、".join(pr.get("keys") or []) or u"（按名匹配）"))
        out.append(u"\n查看某个全貌：inventory_project(name=\"…\")")
        return "\n".join(out)
    pr = next((p for p in projects if name in (p.get("name") or "")), None)
    if pr is None:
        pr = {"name": name, "keys": [name]}
    keys = [str(k).lower() for k in (pr.get("keys") or [])] or [name.lower()]
    _, tasks = load_agents_data()
    hit_tasks = _project_hit_tasks(keys, tasks)
    recs = _project_records(keys)
    out = [u"# %s" % pr.get("name")]
    if pr.get("note"):
        out.append(u"> %s" % pr["note"])
    out.append(u"> 关联记录 %d 条（工作记录全文命中 %d 份）｜ 数据生成于 %s"
               % (len(hit_tasks), len(recs), time.strftime("%Y-%m-%d %H:%M")))
    # 已实现的功能
    feats = pr.get("features") if isinstance(pr.get("features"), list) else None
    if not feats:
        feats, seen = [], set()
        for a, fp, t, _i in recs:
            base = os.path.basename(fp)
            # 只认**人写的记录**：机器抄来的会话实录/记忆/索引全是原始输出碎片，
            # 从里面摘「功能介绍」只会摘到 echo、JSON、树形图
            # 第三十九轮（爱卿问：整理记录还需要吗）——**实测自动写的会话记忆质量
            #   与整理稿相当**（同一天同一事：记忆摘出 5 条功能，整理稿 3 条，
            #   内容重合，连"含完整路径"这点也一样）。故不再跳过"会话记忆"与"笔记"，
            #   只跳过真正是噪音的那几类：转写实录（jsonl 摘录）、会话工作区
            #   （一堆干活碎屑）、总索引（目录）。
            if any(k in base for k in (u"会话实录", u"会话工作区", u"总索引",
                                       u"raw_memories")):
                continue
            for ln in t.split("\n"):
                raw = ln.strip()
                if not raw or raw[0] in "#>|\u2502\u251c\u2514\u250c\u2500{}[]":
                    continue
                if "```" in raw or "|" in raw:
                    continue
                item = raw.lstrip(u"-*\u00b7").strip()
                item = item.lstrip("0123456789.\u3001) ").strip()
                if not (8 <= len(item) <= 100) or item in seen:
                    continue
                if "\\" in item or "://" in item or item.count("/") > 2:
                    continue
                if item.startswith(("echo ", "cd ", "git ", "python ", "pip ", "$ ")):
                    continue
                # 真像条目的：本来就是列表项；或像一句话（不含代码符号）
                bullet = raw[0] in u"-*\u00b7" or (raw[0].isdigit() and
                                                  len(raw) > 1 and raw[1] in u".\u3001)")
                sentence = (not bullet) and not any(c in item for c in "`(){}<>=")
                if len(feats) and not (bullet or sentence):
                    continue
                seen.add(item)
                feats.append(item)
                if len(feats) >= 20:
                    break
            if len(feats) >= 20:
                break
        feats = [f for f in feats if not re.match(r"^[-\s]*$", f)][:20]
    out += [u"", u"## 已实现的功能（%d 条）" % len(feats)]
    out += ([u"- " + f for f in feats] or [u"（日志里没摘出功能条目）"])
    # 开发日志
    out += [u"", u"## 开发日志（按时间倒序，前 15 条）"]
    for a, fp, t, i in sorted(recs, key=lambda x: stamp(x[1]), reverse=True)[:15]:
        out.append(u"- **%s** ｜ [%s] `%s`" % (stamp(fp), a.get("name"), fp))
        out.append(_hit_block(fp, t, i))
    if hit_tasks:
        out += [u"", u"## 相关任务条目（名册任务表，前 10 条）"]
        for t in sorted(hit_tasks,
                        key=lambda x: (title_date(x.get("title")) or "", x.get("mtime", 0)),
                        reverse=True)[:10]:
            out.append(u"- %s ｜ %s" % (title_date(t.get("title")) or t.get("when"),
                                      (t.get("title") or "")[:70]))
            for art in (t.get("artifacts") or [])[:3]:
                out.append(u"  产物：`%s`" % art)
    return "\n".join(out)


def tool_index():
    """一页目录（**最省 token 的第一步**）。

    先看目录（几百字），再决定要不要深挖 —— 而不是让模型自己去逐个读文件。
    目录里给：各记录来源的文件数与最近日期、主要项目清单（各带记录条数）。
    """
    agents, tasks = load_agents_data()
    out = [u"# 本机资产索引（目录页）",
           u"> 生成于 %s ｜ 数据目录 `%s`" % (time.strftime("%Y-%m-%d %H:%M"), HERE), u""]
    rows = []
    mod = _scanner()
    if mod:
        try:
            by = {}
            keep = [a for a in agents if (a.get("kind") or "agent") == "agent"]
            for name, fp, src in mod.iter_record_files(keep):
                k = (name, src)
                r = by.setdefault(k, {"n": 0, "new": ""})
                r["n"] += 1
                d = ""
                m = re.search(r"(20\d\d-\d\d-\d\d)", os.path.basename(fp))
                if m:
                    d = m.group(1)
                else:
                    try:
                        d = time.strftime("%Y-%m-%d", time.localtime(os.path.getmtime(fp)))
                    except Exception:
                        d = ""
                if d > r["new"]:
                    r["new"] = d
            rows = sorted(by.items(), key=lambda x: -x[1]["n"])
        except Exception as e:
            out.append(u"（记录统计失败：%s）" % str(e)[:60])
    out += [u"## 记录（按来源，就地读，不复制）", u"",
            u"| Agent | 来源 | 文件 | 最近 |", u"|---|---|---|---|"]
    for (name, src), r in rows:
        out.append(u"| %s | %s | %d | %s |" % (name, src, r["n"], r["new"] or u"—"))
    projects = load_projects()
    out += [u"", u"## 主要项目（%d 个）" % len(projects)]
    for pr in projects:
        keys = [str(k).lower() for k in (pr.get("keys") or [])] or [str(pr["name"]).lower()]
        n, new = 0, ""
        for t in tasks:
            blob = " ".join([str(t.get("title") or ""), str(t.get("did") or ""),
                             " ".join(t.get("artifacts") or [])]).lower()
            if any(k in blob for k in keys):
                n += 1
                d = title_date(t.get("title")) or (t.get("when") or "")[:10]
                if d > new:
                    new = d
        out.append(u"- **%s** ｜ 记录 %d 条 ｜ 最近 %s" % (pr.get("name"), n, new or u"—"))
    out += [u"", u"## 接着怎么查（省 token 的顺序）",
            u"1. 找某件事在哪：`inventory_search(query=\"关键词\", scope=\"records\")` —— 只回命中行",
            u"2. 某个项目的全貌：`inventory_project(name=\"…\")`",
            u"3. 最近谁干了什么：`inventory_recent(days=7)`",
            u"",
            u"这些工具都在**本地**扫，只有片段进你的上下文 —— 别让模型自己逐个去读文件。"]
    return "\n".join(out)


def tool_agents():
    agents, tasks = load_agents_data()
    intros = load_intros() or {}
    out = [u"# 本机 Agent 名册（%d 个）" % len(agents), u""]
    for a in agents:
        if (a.get("kind") or "agent") != "agent":
            continue
        intro = ""
        if isinstance(intros, dict):
            intro = intros.get(a.get("name")) or (a.get("desc") or "")
        out.append(u"## %s ｜ %s" % (a.get("name"), a.get("category") or ""))
        if intro:
            out.append(u"%s" % str(intro)[:300])
        out.append(u"- 可执行：`%s`" % a.get("exe"))
        out.append(u"- 工作记录：`%s`" % a.get("works_dir"))
        n = len([t for t in tasks if t.get("agent") == a.get("name")])
        out.append(u"- 名册里的任务条目：%d 条" % n)
    return "\n".join(out)


def tool_recent(days=7, agent=None):
    agents, tasks = load_agents_data()
    today = time.strftime("%Y-%m-%d")
    cut_off = time.strftime("%Y-%m-%d", time.localtime(time.time() - float(days) * 86400))
    rows = []
    for t in tasks:
        d = name_date(t.get("log") or "") or (t.get("when") or "")[:10]
        if d and d >= cut_off and d <= today:
            t = dict(t)
            t["_date"] = d
            rows.append(t)
        elif not d and t.get("mtime", 0) >= time.time() - float(days) * 86400:
            rows.append(t)
    if agent:
        rows = [t for t in rows if agent in (t.get("agent") or "")]
    rows.sort(key=lambda x: (x.get("_date") or "0", x.get("mtime", 0)), reverse=True)
    out = [u"# 最近 %s 天的工作动态（%d 条）%s"
           % (days, len(rows), (u"｜限定 Agent：%s" % agent) if agent else u""), u""]
    by = {}
    for t in rows:
        by.setdefault(t.get("agent") or u"未标注", []).append(t)
    for who, ts in sorted(by.items(), key=lambda x: -len(x[1])):
        out.append(u"## %s（%d 条）" % (who, len(ts)))
        for t in ts[:8]:
            out.append(u"- %s ｜ %s" % (t.get("_date") or t.get("when"),
                                      (t.get("title") or "")[:70]))
            d = " ".join(str(t.get("did") or "").split())
            if d:
                out.append(u"  %s" % d[:160])
            for art in (t.get("artifacts") or [])[:2]:
                out.append(u"  产物：`%s`" % art)
    return "\n".join(out)


def tool_skills(query=None):
    inv = load_inventory()
    sk = inv.get("skills") or []
    if query:
        q = query.lower()
        sk = [s for s in sk
              if q in json.dumps(s, ensure_ascii=False).lower()]
    out = [u"# 技能库（%d 个%s）" % (len(sk), (u"，筛选：%s" % query) if query else u""), u""]
    for s in sk:
        out.append(u"- **%s** ｜ %s" % (s.get("name"), (s.get("desc") or "")[:100]))
        out.append(u"  `%s`（%s，%s 个文件）" % (s.get("path"), s.get("source"), s.get("files")))
    return "\n".join(out)


def tool_overview():
    agents, tasks = load_agents_data()
    inv = load_inventory()
    projects = load_projects()
    week = [t for t in tasks if t.get("mtime", 0) >= time.time() - 7 * 86400]
    out = [u"# Agent 资产总览 · 本机总览",
           u"> 生成于 %s ｜ 数据目录 `%s`" % (time.strftime("%Y-%m-%d %H:%M"), HERE), u"",
           u"| 栏目 | 数量 |", u"|---|---|",
           u"| Agent 客户端 | %d |" % len([a for a in agents if (a.get("kind") or "agent") == "agent"]),
           u"| 技能 | %d |" % len(inv.get("skills") or []),
           u"| MCP 服务 | %d |" % len(inv.get("mcp") or []),
           u"| 主要项目 | %d |" % len(projects),
           u"| 工作记录条目 | %d（近 7 天 %d）|" % (len(tasks), len(week)), u"",
           u"## 主要项目"]
    for pr in projects:
        out.append(u"- **%s** ｜ %s" % (pr.get("name"), u"、".join(pr.get("keys") or [])))
    out += [u"", u"## 近 7 天谁在干活"]
    by = {}
    for t in week:
        by[t.get("agent") or u"未标注"] = by.get(t.get("agent") or u"未标注", 0) + 1
    for who, n in sorted(by.items(), key=lambda x: -x[1]):
        out.append(u"- %s：%d 条" % (who, n))
    out += [u"", u"用法：`inventory_search(query=…)` 全文检索 ｜ "
                u"`inventory_project(name=…)` 项目全貌 ｜ `inventory_recent(days=7)` 近期动态"]
    return "\n".join(out)


def tool_reindex(transcribe=False):
    """重扫本机名册；transcribe=True 时顺手把各 Agent 的日志抄录进来。"""
    import importlib.util
    got = {}
    dirs = [HERE, os.path.join(HERE, "src"), getattr(sys, "_MEIPASS", "") or HERE]
    for mod in ("scan_agents", "scan_agents_apps"):
        fp = ""
        for d in dirs:
            cand = os.path.join(d, mod + ".py")
            if os.path.isfile(cand):
                fp = cand
                break
        if not fp:
            return u"找不到采集器 %s.py（找过：%s）" % (mod, u" ｜ ".join(dirs))
        spec = importlib.util.spec_from_file_location(mod + "_mcp", fp)
        m = importlib.util.module_from_spec(spec)
        sys.modules[mod + "_mcp"] = m
        spec.loader.exec_module(m)
        got[mod] = m
    msg = []
    try:
        inv = got["scan_agents"].collect()
        msg.append(u"名册已重扫：%d 技能 / %d MCP"
                   % (len(inv.get("skills") or []), len(inv.get("mcp") or [])))
    except Exception as e:
        msg.append(u"技能/MCP 重扫失败：%s" % e)
    try:
        d = got["scan_agents_apps"].collect()
        with open(os.path.join(HERE, "agents.json"), "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
        msg.append(u"Agent 与任务表已重扫：%d 个 Agent / %d 条任务"
                   % (len(d.get("agents") or []), len(d.get("tasks") or [])))
        if transcribe:
            a, b = got["scan_agents_apps"].sync_agent_logs(d.get("agents") or [])[:2]
            msg.append(u"抄录完成：新抄 %d 个文件、%d 段摘录" % (a, b))
    except Exception as e:
        msg.append(u"Agent 重扫失败：%s" % e)
    # 顺手把新出现的 Agent 接上 MCP + 放指针（与图形界面同一套逻辑）
    try:
        import importlib.util as _iu
        _fp = os.path.join(HERE, "agent_onboard.py")
        if os.path.isfile(_fp):
            _sp = _iu.spec_from_file_location("onboard_mcp", _fp)
            _m = _iu.module_from_spec(_sp)
            _sp.loader.exec_module(_m)
            _r = _m.attach(d.get("agents") or [], sys.executable
                           if getattr(sys, "frozen", False) else os.path.join(HERE, "Agent资产总览.exe"),
                           only_new=False)
            for _l in (_r.get("lines") or []):
                msg.append(u"接入：" + _l)
    except Exception as e:
        msg.append(u"自动接入跳过：%s" % str(e)[:60])
    _TEXT.clear()
    return u"# 重扫完成\n" + "\n".join(u"- " + m for m in msg)


TOOLS = [
    {"name": "inventory_overview",
     "description": u"本机 AI Agent 资产总览：各栏数量、主要项目清单、近 7 天各 Agent 的工作量。"
                    u"想了解这台机器上装了什么、在做什么，先用它。",
     "inputSchema": {"type": "object", "properties": {}, "required": []},
     "fn": lambda **kw: tool_overview()},
    {"name": "inventory_index",
     "description": u"**最省 token 的第一步**：一页目录 —— 各记录来源的文件数与最近日期、"
                    u"主要项目清单。先看它（几百字），再决定要不要 inventory_search / "
                    u"inventory_project 深挖；别让模型自己去逐个读文件。",
     "inputSchema": {"type": "object", "properties": {}, "required": []},
     "fn": lambda **kw: tool_index()},
    {"name": "inventory_search",
     "description": u"跨栏全文检索：Agent 名册、技能、MCP 服务、主要项目，以及**各 Agent 的工作记录**"
                    u"（逐字翻，给文件名·行号·上下文）。查「某功能/某项目由谁在哪做过」用它。",
     "inputSchema": {"type": "object", "properties": {
         "query": {"type": "string", "description": u"关键词（中文/英文均可）"},
         "scope": {"type": "string", "enum": ["all", "records", "agents", "skills", "mcp", "projects"],
                   "description": u"检索范围，默认 all"},
         "limit": {"type": "integer", "description": u"各类最多返回几条，默认 20"}},
         "required": ["query"]},
     "fn": lambda query="", scope="all", limit=20: tool_search(query, scope, limit)},
    {"name": "inventory_project",
     "description": u"某个「主要项目」的全貌：已实现的功能、开发日志（含产物完整路径）、参与 Agent。"
                    u"不传 name 则列出所有主要项目。多个 Agent 接力做的项目，用它能一次看全。",
     "inputSchema": {"type": "object", "properties": {
         "name": {"type": "string", "description": u"项目名（可部分匹配）；留空则列出全部"}},
         "required": []},
     "fn": lambda name="": tool_project(name)},
    {"name": "inventory_agents",
     "description": u"本机 Agent 客户端名册：名字、分类、可执行文件、工作记录目录、简介。"
                    u"想知道「这台机器上有哪些 Agent 可以干活、它们的记录在哪」用它。",
     "inputSchema": {"type": "object", "properties": {}, "required": []},
     "fn": lambda **kw: tool_agents()},
    {"name": "inventory_recent",
     "description": u"最近 N 天各家 Agent 干了什么（按 Agent 分组，含任务与产物路径）。"
                    u"接手别人的活、续接上次进度时用。",
     "inputSchema": {"type": "object", "properties": {
         "days": {"type": "integer", "description": u"天数，默认 7"},
         "agent": {"type": "string", "description": u"只看某个 Agent（可部分匹配），可省"}},
         "required": []},
     "fn": lambda days=7, agent=None: tool_recent(days, agent)},
    {"name": "inventory_skills",
     "description": u"技能库检索：本机所有技能的说明与路径（可按关键词筛）。"
                    u"想知道「有没有现成技能能做这件事」用它。",
     "inputSchema": {"type": "object", "properties": {
         "query": {"type": "string", "description": u"关键词，可省"}}, "required": []},
     "fn": lambda query=None: tool_skills(query)},
    {"name": "inventory_reindex",
     "description": u"重扫本机名册（技能/MCP/Agent/任务表），可选顺手把各 Agent 的日志抄录进来。"
                    u"当你怀疑数据过期、或刚做完一批活想让记录进库时调用。",
     "inputSchema": {"type": "object", "properties": {
         "transcribe": {"type": "boolean", "description": u"true 则同时抄录各 Agent 日志，默认 false"}},
         "required": []},
     "fn": lambda transcribe=False: tool_reindex(transcribe)},
]
_TOOL_MAP = {t["name"]: t for t in TOOLS}


def _public_tools():
    return [{"name": t["name"], "description": t["description"],
             "inputSchema": t["inputSchema"]} for t in TOOLS]


def call_tool(name, args):
    t = _TOOL_MAP.get(name)
    if not t:
        return {"content": [{"type": "text", "text": u"没有这个工具：%s" % name}], "isError": True}
    try:
        text = t["fn"](**(args or {}))
        return {"content": [{"type": "text", "text": mask_text(text)}], "isError": False}
    except Exception as e:
        import traceback
        log("工具 %s 出错：%s\n%s" % (name, e, traceback.format_exc()[:800]))
        return {"content": [{"type": "text", "text": u"工具执行出错：%s" % e}], "isError": True}


def handle(req):
    """处理一条 JSON-RPC 请求；通知类返回 None。"""
    method = req.get("method")
    rid = req.get("id")
    params = req.get("params") or {}
    if method == "initialize":
        want = params.get("protocolVersion")
        return {"jsonrpc": "2.0", "id": rid, "result": {
            "protocolVersion": want or PROTOCOL,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": VERSION},
            "instructions": u"本机 AI 资产的统一索引：先 inventory_index（目录页，最省 token）或 inventory_overview 看全貌，"
                            u"再 inventory_search / inventory_project 深挖。"
                            u"数据全部来自本机，不联网。"}}
    if method in ("notifications/initialized", "notifications/cancelled",
                  "notifications/roots/list_changed"):
        return None
    if method == "ping":
        return {"jsonrpc": "2.0", "id": rid, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": _public_tools()}}
    if method == "tools/call":
        return {"jsonrpc": "2.0", "id": rid,
                "result": call_tool(params.get("name"), params.get("arguments"))}
    if method in ("resources/list", "prompts/list"):
        key = "resources" if method.startswith("resources") else "prompts"
        return {"jsonrpc": "2.0", "id": rid, "result": {key: []}}
    if method == "shutdown":
        return {"jsonrpc": "2.0", "id": rid, "result": {}}
    return {"jsonrpc": "2.0", "id": rid,
            "error": {"code": -32601, "message": "Method not found: %s" % method}}


def serve():
    log(u"启动：数据目录 %s ｜ 工具 %d 个" % (HERE, len(TOOLS)))
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception as e:
            log(u"报文不是 JSON：%s" % e)
            continue
        try:
            resp = handle(req)
        except Exception as e:
            log(u"处理出错：%s" % e)
            resp = {"jsonrpc": "2.0", "id": req.get("id"),
                    "error": {"code": -32603, "message": str(e)}}
        if resp is None:
            continue
        sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
        sys.stdout.flush()
        if req.get("method") == "shutdown":
            break
    log(u"退出。")


def main(argv):
    if "--demo" in argv:
        i = argv.index("--demo")
        rest = argv[i + 1:]
        if not rest:
            sys.stdout.write(u"用法：--demo <工具名> [参数]\n")
            return 0
        tool = rest[0]
        args = {}
        if len(rest) > 1:
            if tool == "inventory_search":
                args = {"query": rest[1]}
            elif tool == "inventory_project":
                args = {"name": rest[1]}
            elif tool == "inventory_recent":
                args = {"days": int(rest[1])} if rest[1].isdigit() else {"agent": rest[1]}
            elif tool == "inventory_skills":
                args = {"query": rest[1]}
            elif tool == "inventory_reindex":
                args = {"transcribe": "true" in [x.lower() for x in rest[1:]]}
        r = call_tool(tool, args)
        sys.stdout.write(r["content"][0]["text"] + "\n")
        return 0
    if "--tools" in argv:
        sys.stdout.write(json.dumps(_public_tools(), ensure_ascii=False, indent=1) + "\n")
        return 0
    serve()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
