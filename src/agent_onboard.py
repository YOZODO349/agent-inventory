# -*- coding: utf-8 -*-
"""Agent 资产总览 · Agent 自动接入

两件事，一个入口：
  ① 把本应用注册成一个 MCP 服务（写进各 Agent 的 MCP 配置）
  ② 在各 Agent 的「每次必读」位置放一行指针（否则它不知道有这个索引）

设计要点：
  · **只改已存在的配置文件**，绝不凭空造配置文件（免得把人家客户端弄坏）；
  · 指针文件在**该 Agent 的配置目录已存在**时才创建；
  · 动任何文件前一律备份成 `<原名>.bak_before_agentinventory`；
  · 每次做了什么都记在报告里，应用会显示给人看。

状态文件：%LOCALAPPDATA%\\<程序名>\\mcp_auto_attach.json
    {"enabled": true, "known": ["WorkBuddy", ...], "log": [...]}
「已知清单」用来判断谁是新来的 —— 新 Agent 一出现就自动接。
"""
import io
import json
import os
import re
import time

APP = "Agent资产总览"
SERVER_KEY = "agent-inventory"
MARK = "<!-- agent-inventory:pointer -->"

POINTER_MD = """%(mark)s
## 本机 Agent 资产索引（MCP 服务：`agent-inventory`）

要回答「以前做过什么 / 某个项目现在到哪一步了 / 本机有哪些技能与 MCP 服务」这类问题，
**先调用 MCP 工具，不要凭印象回答**：

- `inventory_index()` —— 一页目录（最省 token，先看它）
- `inventory_overview()` —— 本机总览（各栏数量、主要项目、最近谁在干活）
- `inventory_project(name="项目名")` —— 某项目的全貌：已实现功能 / 开发日志 / 产物路径
- `inventory_search(query="关键词", scope="records")` —— 逐字翻各 Agent 的工作记录
- `inventory_recent(days=7)` —— 最近几天各家干了什么

（本段由「Agent 资产总览」自动写入，删掉即可停用；`inventory_reindex()` 可刷新数据。）
"""

POINTER_MDC = """---
description: 本机 agent 资产索引（MCP: agent-inventory）
alwaysApply: true
---
%(body)s"""

TEST_PROMPT = """请做一次接入自检。逐条回答，不要省略、不要合并、不要猜。

① 你的工具列表里，有没有名字以 `inventory_` 开头的工具（如 `inventory_overview`）？
   有就说「① 有」并列出工具名；没有就回「① 没有」。

② 直接回答（答不出就明说答不出，**不许编**）：
   本机「主要项目」有哪几个？
   挑其中一个（优先「某个主要项目」），说出它**最近一次记录是哪天**，
   以及对应的**记录文件或产物的完整路径**。

③ 你刚才是怎么得到②的？调用了 MCP 工具，还是直接读了
   `C:\\Users\\<用户名>\\Agent资产总览\\` 目录下的文件？
   把用到的工具名或文件路径**原样贴出来**。

④ 你每次开工必读的说明里，有没有指向 `Agent资产总览`
   （写法可能是 `SKILL.md` 路径、或 `inventory_*` 工具）？
   有就**原样引用那一行**；没有就回「④ 没有」。

—— 判读 ——
· ② 报出**真实项目名 + 真实日期 + 真实路径** → ✅ 主动检索生效（走①的 MCP 或走③的文件，都算）
· ③ 能说清是哪条路 → 便于你知道该维护哪一处
· ④ 有 → 指针已进它的必读；没有 → 指针没到位（去「接入 Agent」复制读取指引人工粘）
· ② 报不出 / 含糊 / 说"我无法访问" → 没生效；② 能报项目名但给不出日期与路径 → 半生效（只读了索引）"""


def state_path():
    base = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), APP)
    return os.path.join(base, "mcp_auto_attach.json")


def load_state():
    try:
        d = json.load(open(state_path(), "r", encoding="utf-8"))
        if isinstance(d, dict):
            return {"enabled": bool(d.get("enabled", True)),
                    "known": list(d.get("known") or []),
                    "log": list(d.get("log") or [])}
    except Exception:
        pass
    return {"enabled": True, "known": [], "log": []}


def save_state(st):
    p = state_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    json.dump(st, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def _backup(path):
    try:
        if os.path.isfile(path) and not os.path.isfile(path + ".bak_before_agentinventory"):
            open(path + ".bak_before_agentinventory", "wb").write(open(path, "rb").read())
    except Exception:
        pass


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    _backup(path)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


# ---- 已知客户端的接入点。rules 用 None 表示「该客户端不读文件，只能人工粘」 ----
def targets(home, app_dir, exe):
    return {
        # WorkBuddy：它的「每次必读」是 MEMORY.md（长期约定），AGENTS.md 见它不读
        # WorkBuddy：它的 MCP 有信任门槛（哈希清单不在明文里，程序无权代授），
        # 故走**不依赖 MCP 的路子** —— 把「读取指引」的地址写进它的人格 SOUL.md，
        # 它每次开工自己照着读文件即可。
        "workbuddy": {"mcp": os.path.join(home, ".workbuddy", "mcp.json"),
                      "rules": None,
                      "persona_files": [os.path.join(home, ".workbuddy", "SOUL.md"),
                                        os.path.join(home, ".workbuddy", "IDENTITY.md")],
                      "note": u"MCP 有信任门槛；已改为把读取指引写进其人格（每次必读）"},
        "cursor": {"mcp": os.path.join(home, ".cursor", "mcp.json"),
                   "rules": [os.path.join(home, ".cursor", "rules",
                                          "agent-inventory.mdc")]},
        "claude": {"mcp": os.path.join(os.environ.get("APPDATA", ""), "Claude",
                                       "claude_desktop_config.json"),
                   "rules": None},
        # AstrBot：它对 stdio MCP 的 command 有**白名单**
        #   （`_DEFAULT_STDIO_COMMAND_ALLOWLIST` 只认 python/node/npx 之类），
        #   直接指 exe 会被静默拒绝 —— 故改用白名单内的 python 拉起本应用的 MCP 脚本。
        "astrbot": {"mcp": os.path.join(home, ".astrbot", "data", "mcp_server.json"),
                    "rules": None,
                    # AstrBot 没有文件式「必读」机制（代码里搜不到 AGENTS.md/CLAUDE.md），
                    # 它的人格存在 data_v4.db 的 personas 表里 —— 指针得写进那儿
                    "persona": True,
                    "cmd": os.path.join(os.environ.get("LOCALAPPDATA", ""), "AstrBot",
                                        "backend", "python", "python.exe"),
                    "args": [os.path.join(app_dir, "agent_mcp.py")],
                    "note": u"经 python 白名单拉起；指针写入其人格（重启后端才生效）"},
        "codex": {"mcp": os.path.join(home, ".codex", "config.toml"),
                  "rules": [os.path.join(home, ".codex", "AGENTS.md")]},
    }


def _slug(name):
    return re.sub(r"[^a-z0-9]+", "", str(name or "").lower())


READ_GUIDE_MARK = u"<!-- agent-inventory:read-guide -->"
GUIDE_VERSION_MARK = u"<!-- guide-version: 7 -->"
SKILL_REL = os.path.join(u"通用资源", "skills", "agent-inventory", "SKILL.md")

READ_GUIDE_POINT = u'''## 先查后答（**每一轮都做，不要等对方提醒**）

**每次收到消息，在回答之前先做这一步：**
- **有 MCP**：先调 `inventory_index()`（一页目录，约 266 token）。**目录页里有「主要项目」一节 ——
  命中项目就直接 `inventory_project(name="…")` 拿全貌（已实现的功能 / 开发日志 / 产物），比全库捞更省**；
  没命中再按关键词调 `inventory_search(query="关键词", scope="records")`，命中才细读。
- **没有 MCP**：用你自己的搜索工具，拿对方话里的关键词在各家记录目录里搜一遍，
  **命中才打开文件**，别整篇读。
- 完整目录清单与省 token 的查法见：

```
{{APP}}\\通用资源\\skills\\agent-inventory\\SKILL.md
```
'''

READ_GUIDE_SKILL = u'''{{VER}}
---
name: agent-inventory
description: 查本机 AI Agent 资产与工作历史（免 MCP）。涉及「以前做过什么 / 某个项目现在到哪一步 / 本机有哪些技能或 MCP 服务 / 谁在什么时候干了什么」时，先读本篇，按里面的路径直接读文件即可。
---

# 本机 Agent 资产总览 · 读取指引

数据全在本机，**直接读文件，不依赖任何 MCP 服务**。应用目录：

```
{{APP}}\\
```

## 一、有什么数据

| 文件 / 目录 | 内容 |
|---|---|
| `agents.json`（约 170 KB） | `agents[]` Agent（name/desc/exe/works_dir）；`tasks[]` 任务与产物（title/did/artifacts/log/when） |
| `agent_inventory.json`（约 44 KB） | `skills[]` 技能（name/desc/path）；`mcp[]` MCP 注册；`skill_shelves[]` 技能架 |
| `主要项目.json` | 用户划重点的项目：`name` / `keys` / `note` |
| `agent_intros.json` | 各 Agent 的简介 |
| 各 Agent 的**原生记录目录** | 就地索引，直接读（见下方清单） |

## 二、怎么查（按需取用，**别整份读**）

1. **总览**：`agent_inventory.json` 的条数 + `agents.json` 的 `agents[]`。
2. **某项目做到哪一步**：**先看主要项目** —— `主要项目.json` 里 `name` / `keys` / `note`，
   项目若带人工 `features` 清单，那就是最准的结论。拿它的关键词去
   `inventory_search(query="关键词", scope="records")`（无 MCP 就用你自己的搜索工具，在第三节的
   记录根里搜）→ **命中才打开文件**，产物路径就在记录正文里。
3. **谁在什么时候干过什么**：`agents.json` 的 `tasks[]`。
4. **有没有现成技能**：`agent_inventory.json` 的 `skills[]`，需要正文再读其 `path`。

## 三、原生记录目录（就地索引，直接读这些地方）

| Agent | 记录根 | 处理 |
|---|---|---|
| WorkBuddy | `~/WorkBuddy/*/.workbuddy/memory/`、`~/.workbuddy/memory/` | 直接读 |
| Codex++ | `~/.codex/memories/`、`~/.codex/sessions/`（jsonl，单文件可达十几 MB） | 笔记直读；实录摘录 |
| AstrBot | `~/.astrbot/data/workspaces/` | 直接读 |
| 桌面 | `~/Desktop/*.md`、`*.txt` | 直接读 |
| Cursor / Grok Bot | 私有二进制格式 | 只登记，读不了 |

**摘录缓存在** `%LOCALAPPDATA%\\Agent资产总览\\digest缓存\\`（可随时重建，不是唯一副本）。

## 四、触发时机（**每一轮都搜，不是等对方提醒**）

> **最要紧的一条**：**动手写或改任何东西之前**，先 `inventory_skills(query="那件事的关键词")`
> 查本机有没有现成技能 —— 例如做界面/视觉，先读 `taste-suite` 总纲再选一篇；
> 别凭手感开工。（第五十三轮补：有人问"为什么没主动搜 skill"，这就是那条规矩。）

**每次收到消息，回答之前先搜一遍** —— 不要等对方问「还记不记得……」：

1. **有 MCP**：`inventory_index()` 看目录（约 266 token），再拿对方话里的关键词
   `inventory_search(query="关键词", scope="records")`；**命中才细读**。
2. **没有 MCP**：用你的搜索工具，拿关键词在「六、该搜哪些目录」里列的**每个目录**搜一遍；
   **命中才打开文件**。
3. 什么算关键词：项目名、功能名、文件名、日期、专有名词。纯闲聊/纯指令挑不出关键词就跳过。

> 代价很小（目录页约 266 token，一次检索回几百字），换来的是**永不"失忆"**。

## 五、省 token 的查法（**先读这节**）

1. **能用 MCP 就走 MCP**：先 `inventory_index()` 看目录（几百字），再
   `inventory_search(query="关键词", scope="records")` 定点查 ——
   **全盘搜索在本地工具进程里跑，只有命中片段进你的上下文**（实测一次只回几百字）。
2. **没有 MCP 就用你自己的搜索工具**（Grep 之类）**递归搜关键词**，
   **别一个文件一个文件地读** —— 逐篇读等于把整个库塞进上下文，那是真正的烧 token。
3. **实录摘录别整篇打开**：`Codex 会话实录_*`、`*会话记忆*` 动辄几十上百 KB，
   先搜关键词，只读命中的那几行。
4. 要细看时只打开**命中的那一两个文件**，优先看**文件名里日期最新**的。

## 六、该搜哪些目录（**没走 MCP 时，照着这几个地方搜**）

搜关键词时**这几个根都要搜**，别只搜应用自己的记录夹 ——
"某件事在哪"往往只记在其中一处（教训：WorkBuddy 曾只搜了别人的工作区，
而答案就在**它自己的记忆**里）：

| # | 目录 | 里面是什么 |
|---|---|---|
| 1 | `~/WorkBuddy/*/.workbuddy/memory/` | WorkBuddy 每次会话的记忆（**最全**）|
| 2 | `~/.workbuddy/memory/` | WorkBuddy 的长期记忆（正在进行的会话写这儿）|
| 3 | `~/.codex/memories/` | Codex 的笔记 |
| 4 | `~/.workbuddy/projects/*/*.jsonl` | **WorkBuddy 完整会话存档**（已摘录进缓存）|
| 5 | `~/.astrbot/data/workspaces/` | AstrBot 会话里产出的文件 |
| 6 | `~/Desktop/*.md`、`*.txt` | 放在桌面上的文本 |
| 7 | `%LOCALAPPDATA%\Agent资产总览\digest缓存\` | 大文件摘出来的可读文本（含 **AstrBot 的对话记忆**）|

**照着搜**：`Grep -r "关键词" <上表每个目录>` —— 一次搜全，别一个一个目录试。

## 七、三个坑

- `agents.json` 约 170 KB，别整份灌进上下文，用搜索工具。
- `Codex 会话实录_*`、`*会话记忆*` 是机器抄录的原始转写，噪音大；**优先读文件名带中文标题的**（人写的）。
- 判断时间**以文件名里的日期为准**，别信文件修改时间（抄录件的修改时间是"抄的那一刻"）。
'''


def write_skill(app_dir):
    """把「读取指引」落成应用技能库里的一个技能（各家 agent 都能当技能读）。"""
    fp = os.path.join(app_dir, SKILL_REL)
    os.makedirs(os.path.dirname(fp), exist_ok=True)
    try:
        want = (READ_GUIDE_SKILL.replace("{{APP}}", app_dir)
                .replace("{{VER}}", GUIDE_VERSION_MARK))
        if os.path.isfile(fp):
            old = io.open(fp, encoding="utf-8", errors="ignore").read()
            if GUIDE_VERSION_MARK in old:
                return True, fp          # 已是本版，不动（也免得盖掉人家手改的内容）
            _backup(fp)                  # 旧版（或没有版本标记）→ 备份后刷新
        io.open(fp, "w", encoding="utf-8").write(want)
        return True, fp
    except Exception as e:
        try:
            sys.stderr.write("[agent-inventory] 写技能文件失败：%s\n" % e)
        except Exception:
            pass
    return os.path.isfile(fp), fp


def write_persona_file(path, app_dir=None):
    """把「先查后答」指针写进某个 agent 的人格文件（如 WorkBuddy 的 SOUL.md）。"""
    try:
        if not os.path.isfile(path):
            return False, u"没有这个文件"
        old = io.open(path, encoding="utf-8", errors="ignore").read()
        new_block = (READ_GUIDE_MARK + "\n"
                     + READ_GUIDE_POINT.replace("{{APP}}", app_dir or ""))
        if READ_GUIDE_MARK in old:
            # 指针永远挂在文末：从标记处截断再重写 —— 这样**规则变了也能更新**
            head = old[:old.index(READ_GUIDE_MARK)].rstrip() + "\n\n"
            if head + new_block.strip() == old.strip():
                return True, u"已是最新指针"
            _backup(path)
            io.open(path, "w", encoding="utf-8").write(head + new_block + "\n")
            return True, u"指针已更新（触发条件：每轮先搜）"
        _backup(path)
        io.open(path, "w", encoding="utf-8").write(
            old.rstrip() + "\n\n" + new_block + "\n")
        return True, u"指针已写入人格"
    except Exception as e:
        return False, u"写人格文件失败：%s" % str(e)[:50]


PERSONA_MARK = u"【本机 Agent 资产索引】"
PERSONA_POINT = (
    PERSONA_MARK + u"【本机 Agent 资产索引】**每一轮对话，在回答之前都先查一遍** —— "
    u"不要等对方说『还记不记得』。有 MCP 就先调 `inventory_index()`（一页目录，"
    u"约 266 token），再按对方话里的关键词调 `inventory_search(query=\"关键词\", "
    u"scope=\"records\")`，命中才细读；没有 MCP 就用你自己的搜索工具，"
    u"在各家记录目录里搜关键词，命中才打开文件。服务名 `agent-inventory`；"
    u"另外 `inventory_project`(项目全貌) / `inventory_recent`(近期动态) 按需用。"
    u"【更要紧的一条】**动手写或改任何东西之前**，先调 "
    u"`inventory_skills(query=\"那件事的关键词\")` 看本机有没有现成技能 —— "
    u"例如要做界面/视觉，先查设计类技能（taste-suite 那一家）；"
    u"别凭手感开工。")
def write_astrbot_persona(home):
    """把指针写进 AstrBot 当前默认人格的 system_prompt（在 data_v4.db 里）。

    为何动数据库：AstrBot 没有文件式的「每次必读」入口，人格就是它的必读文本。
    改写前把原文另存一份 json，可一键还原。
    """
    import sqlite3
    data = os.path.join(home, ".astrbot", "data")
    cfg_p = os.path.join(data, "cmd_config.json")
    db = os.path.join(data, "data_v4.db")
    if not (os.path.isfile(cfg_p) and os.path.isfile(db)):
        return False, u"没找到 AstrBot 的配置或数据库"
    try:
        cfg = json.load(open(cfg_p, encoding="utf-8-sig"))
        pid = (((cfg.get("agent_runner") or {}).get("config") or {})
               .get("persona") or {}).get("persona_id") or "default"
    except Exception as e:
        return False, u"读配置失败：%s" % str(e)[:40]
    try:
        con = sqlite3.connect(db, timeout=30)
        con.execute("PRAGMA busy_timeout=20000")
        cur = con.cursor()
        row = cur.execute("SELECT rowid, system_prompt FROM personas WHERE persona_id=?",
                          (pid,)).fetchone()
        if not row:
            con.close()
            return False, u"数据库里没有人格 %s" % pid
        rid, old = row[0], row[1] or ""
        if PERSONA_MARK in old:
            # 已有指针 —— 但规则可能变了（如"每轮先搜"），故**替换**而非直接返回：
            # 指针永远挂在文末，从标记处截断再重写即可。
            head = old[:old.index(PERSONA_MARK)].rstrip() + "\n\n"
            head = re.sub(re.escape(PERSONA_MARK) + r"(\s*" + re.escape(PERSONA_MARK)
                          + r")+", PERSONA_MARK, head)
            bak = os.path.join(data, "persona_%s_原文备份_%s.json"
                               % (pid, time.strftime("%Y%m%d_%H%M%S")))
            io.open(bak, "w", encoding="utf-8").write(json.dumps(
                {"rowid": rid, "persona_id": pid, "system_prompt": old},
                ensure_ascii=False, indent=1))
            new_sp = re.sub(re.escape(PERSONA_MARK) + r"(\s*" + re.escape(PERSONA_MARK)
                            + r")+", PERSONA_MARK, head + PERSONA_POINT + "\n")
            cur.execute("UPDATE personas SET system_prompt=?, updated_at=? WHERE rowid=?",
                        (new_sp,
                         time.strftime("%Y-%m-%d %H:%M:%S"), rid))
            con.commit()
            con.close()
            return True, u"人格 %s 指针已更新（触发条件：每轮先搜）" % pid
        bak = os.path.join(data, "persona_%s_原文备份_%s.json"
                           % (pid, time.strftime("%Y%m%d_%H%M%S")))
        open(bak, "w", encoding="utf-8").write(json.dumps(
            {"rowid": rid, "persona_id": pid, "system_prompt": old},
            ensure_ascii=False, indent=1))
        cur.execute("UPDATE personas SET system_prompt=?, updated_at=? WHERE rowid=?",
                    (old.rstrip() + "\n\n" + PERSONA_POINT + "\n",
                     time.strftime("%Y-%m-%d %H:%M:%S"), rid))
        con.commit()
        con.close()
        return True, u"指针已写入人格 %s（原文备份 %s）" % (pid, os.path.basename(bak))
    except Exception as e:
        return False, u"写人格失败：%s" % str(e)[:60]


def attach_one(name, info, exe, reports):
    """给单个 Agent 接 MCP + 放指针；返回 (接了MCP?, 放了指针?, 备注)。"""
    mcp_done = ptr_done = False
    notes = []
    cfg = (info or {}).get("mcp")
    if cfg and os.path.isfile(cfg):
        low = cfg.lower()
        try:
            if low.endswith(".json"):
                d = json.load(open(cfg, "r", encoding="utf-8"))
                key = "mcpServers" if "mcpServers" in d else (
                    "servers" if "servers" in d else "mcpServers")
                d.setdefault(key, {})
                _cmd = (info or {}).get("cmd") or exe
                _args = (info or {}).get("args") or ["--mcp"]
                if not os.path.isfile(_cmd):
                    notes.append(u"指定的启动器不存在：%s" % _cmd)
                elif (d[key].get(SERVER_KEY, {}).get("command") != _cmd
                      or d[key].get(SERVER_KEY, {}).get("args") != _args):
                    d[key][SERVER_KEY] = {"command": _cmd, "args": _args}
                    _write(cfg, json.dumps(d, ensure_ascii=False, indent=1))
                    mcp_done = True
                else:
                    notes.append("MCP 已存在")
            else:                                  # Codex 的 TOML
                t = open(cfg, "r", encoding="utf-8").read()
                if "[mcp_servers.%s]" % SERVER_KEY not in t:
                    t = t.rstrip() + ("\n\n[mcp_servers.%s]\ncommand = '%s'\n"
                                      'args = ["--mcp"]\n' % (SERVER_KEY, exe))
                    _write(cfg, t)
                    mcp_done = True
                else:
                    notes.append("MCP 已存在")
        except Exception as e:
            notes.append("MCP 写入失败：%s" % str(e)[:60])
    elif cfg:
        notes.append("未找到其 MCP 配置文件，跳过")
    # 指针
    rules = (info or {}).get("rules")
    if rules:
        for rp in rules:
            try:
                ext = os.path.splitext(rp)[1].lower()
                if not os.path.isdir(os.path.dirname(rp)):
                    notes.append("无该客户端的配置目录，指针未放")
                    continue
                body = POINTER_MD % {"mark": MARK}
                if ext == ".mdc":
                    body = POINTER_MDC % {"body": body}
                old = ""
                if os.path.isfile(rp):
                    old = open(rp, "r", encoding="utf-8", errors="ignore").read()
                    if MARK in old or SERVER_KEY in old:
                        # 已有指针：**不置 ptr_done**，让它落到「已就位」那一态，
                        # 否则会报成「本次写入（指针已存在）」，自相矛盾
                        notes.append("指针已存在")
                        break
                _write(rp, (old.rstrip() + "\n\n" if old.strip() else "") + body)
                ptr_done = True
                if (info or {}).get("_guess"):
                    notes.append("指针写到 %s（该客户端是否读取未经验证）"
                                 % os.path.relpath(rp, os.path.expanduser("~")))
                break
            except Exception as e:
                notes.append("指针写入失败：%s" % str(e)[:60])
    elif (info or {}).get("persona_files"):
        for pf in info["persona_files"]:
            ok, why = write_persona_file(pf, os.path.dirname(os.path.abspath(exe)))
            if ok:
                ptr_done = True
                notes.append(u"%s：%s" % (os.path.basename(pf), why))
                break
            notes.append(u"%s 未写（%s）" % (os.path.basename(pf), why))
    elif (info or {}).get("persona"):
        ok, why = write_astrbot_persona(os.path.expanduser("~"))
        ptr_done = ok
        notes.append(why)
    else:
        notes.append("它不读这类文件 —— 请在它的设置里人工粘贴（窗口里有「复制指针原文」）")
    return mcp_done, ptr_done, "；".join([n for n in notes if n])


def attach(agents, exe, enabled=None, only_new=False):
    """给 agents 列表里的客户端接 MCP + 放指针。

    only_new=True 时只处理「上次没见过的」Agent（新装机、新装的客户端）。
    """
    try:
        write_skill(os.path.dirname(os.path.abspath(exe)))
    except Exception:
        pass
    st = load_state()
    if enabled is not None:
        st["enabled"] = bool(enabled)
    if not st["enabled"]:
        return {"ok": False, "reason": "自动接入已关闭", "log": st.get("log") or []}
    home = os.path.expanduser("~")
    app_dir = os.path.dirname(os.path.abspath(exe))
    tgt = targets(home, app_dir, exe)
    names, lines = [], []
    for a in agents or []:
        if (a.get("kind") or "agent") != "agent":
            continue
        nm = a.get("name") or ""
        if not nm:
            continue
        if only_new and nm in st["known"]:
            continue
        key = _slug(nm)
        info = tgt.get(key) or tgt.get(key.split("-")[0]) or {}
        if not info:
            # 不认识的客户端：按惯例猜它的配置目录（仅当目录已存在才动）
            for cand in (os.path.join(home, "." + key), os.path.join(home, key)):
                if os.path.isdir(cand):
                    # 不认识的客户端：按惯例猜它的配置目录（该目录已存在才动），
                    # 写进去之后**是否被读取无法验证** —— 报告里会如实写明
                    info = {"mcp": os.path.join(cand, "mcp.json"),
                            "rules": [os.path.join(cand, "AGENTS.md")],
                            "_guess": cand}
                    break
        m, p, note = attach_one(nm, info, exe, lines)
        if (info or {}).get("note"):
            note = (note + u"；" if note else u"") + info["note"]
        if m or p or note:
            lines.append("%s：MCP %s ｜ 指针 %s%s" % (
                nm,
                "本次写入" if m else ("已就位" if note and u"MCP 已存在" in note else "未接入"),
                "本次写入" if p else ("已就位" if note and u"指针已存在" in note else "未放"),
                ("（%s）" % note) if note else ""))
        names.append(nm)
    for nm in names:
        if nm not in st["known"]:
            st["known"].append(nm)
    st["log"] = (st["log"] + ["%s %s" % (time.strftime("%Y-%m-%d %H:%M"), l)
                              for l in lines])[-40:]
    save_state(st)
    return {"ok": True, "lines": lines, "known": st["known"], "log": st["log"],
            "test_prompt": TEST_PROMPT, "pointer_md": POINTER_MD % {"mark": MARK}}


def new_agents(agents):
    """上次登记之后新出现的 Agent 名字。"""
    st = load_state()
    known = set(st["known"])
    return [a.get("name") for a in (agents or [])
            if (a.get("kind") or "agent") == "agent" and a.get("name")
            and a["name"] not in known]


if __name__ == "__main__":
    import importlib.util
    here = os.path.dirname(os.path.abspath(__file__))
    spec = importlib.util.spec_from_file_location("apps_", os.path.join(here, "scan_agents_apps.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    d = m.collect()
    r = attach(d.get("agents") or [], os.path.join(here, "Agent资产总览.exe"))
    print("\n".join(r.get("lines") or [r.get("reason")]))
