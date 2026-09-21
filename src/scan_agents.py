# -*- coding: utf-8 -*-
"""重新扫描本机，刷新 Agent 资产名册。

用法：
    python scan_agents.py            # 扫描并刷新 Agent资产总览.html
    python scan_agents.py --json     # 只输出 JSON，不生成 HTML
"""
import os, sys, json, re, subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

def read_frontmatter(path):
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            txt = f.read(6000)
    except Exception:
        return {}
    m = re.match(r"^---\s*\n(.*?)\n---", txt, re.S)
    if not m:
        return {}
    meta, cur = {}, None
    for line in m.group(1).splitlines():
        if re.match(r"^\s*-\s+", line) and cur:
            meta.setdefault(cur + "_list", []).append(line.strip()[2:].strip())
            continue
        mm = re.match(r"^([A-Za-z_][\w\-]*)\s*:\s*(.*)$", line)
        if mm:
            cur = mm.group(1)
            meta[cur] = mm.group(2).strip().strip('"').strip("'")
    return meta

def first_paragraph(path, limit=220):
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            txt = f.read(8000)
    except Exception:
        return ""
    txt = re.sub(r"^---\s*\n.*?\n---", "", txt, flags=re.S)
    for line in txt.splitlines():
        s = line.strip()
        if not s or s.startswith(("#", "|", "```", "-", "*")):
            continue
        return s[:limit]
    return ""

def scan_suite_members(skills):
    """把「整合卡」里的成员也收进索引 —— 带中文说明、标 `hidden`。

    第五十三轮（有人问：为什么中文查不到 minimalist-ui）——
    成员的**中文说明只长在界面上**，没进索引；检索只认 SKILL.md 的英文
    frontmatter，于是"极简""粗野"这类中文词一个都命不中。

    此处把成员也收一份：界面上仍只露总纲那张金卡（`hidden=True` 不单列），
    但**检索能按中文命中**它们。
    """
    import json as _j
    out = []
    for s in (skills or []):
        d = s.get("path") or ""
        if not os.path.isdir(d):
            continue
        suite_json = ""
        try:
            for f in sorted(os.listdir(d)):
                if f.endswith(".suite.json"):
                    suite_json = os.path.join(d, f)
                    break
        except Exception:
            continue
        if not suite_json:
            continue
        try:
            j = _j.load(open(suite_json, "r", encoding="utf-8"))
        except Exception:
            continue
        labels = j.get("labels") or {}
        # 成员是**平铺**在技能库根下的（与套件目录同级），不是套在套件里 ——
        #   先找同级，找不到再看套件目录内部（两种布局都兼容）
        _parent = os.path.dirname(d)
        for m in (j.get("members") or []):
            md = os.path.join(_parent, m)
            if not os.path.isdir(md):
                md = os.path.join(d, m)
            sk = os.path.join(md, "SKILL.md")
            if not (os.path.isdir(md) and os.path.isfile(sk)):
                continue
            fm = read_frontmatter(sk)
            nm = fm.get("name") or m
            cn = labels.get(nm) or labels.get(m) or ""
            files = 0
            try:
                files = sum(len(fs) for _, _, fs in os.walk(md))
            except Exception:
                pass
            out.append({
                "name": nm, "dir": m,
                "desc": cn or fm.get("description") or first_paragraph(sk),
                "cn": cn, "suite": s.get("name") or s.get("dir") or "",
                "suite_label": j.get("label") or "",
                "hidden": True, "member_of": s.get("dir") or "",
                "path": md, "source": s.get("source", ""), "files": files,
            })
    # 同一套件可能在多处技能架里各有一份（如 ~/.workbuddy/skills 与本工具技能库），
    # 成员会因此收重 —— 按 name 去重，优先留「本工具技能库」那份
    uniq = {}
    for x in out:
        k = x["name"]
        old = uniq.get(k)
        if old is None or (x.get("source") == "本工具技能库"
                           and old.get("source") != "本工具技能库"):
            uniq[k] = x
    return list(uniq.values())


def skill_lib_dir():
    r"""技能真身所在：应用内「通用资源\skills」，缺则退回家目录 agent-skills。"""
    lib = os.path.join(HERE, "通用资源", "skills")
    if not os.path.isdir(lib):
        lib = os.path.join(os.path.expanduser("~"), "agent-skills")
    return lib

def is_lib_mount(d):
    r"""技能架里的这一枚，是不是**指向技能库的联接**？

    第五十四轮（有人问：同一个技能怎么在名册里冒出两张卡）——
    第二十一轮起技能真身统一收在应用内「通用资源\skills」，
    各家技能架（~/.workbuddy/skills、~/.claude/skills …）里只留 Junction 指过来。
    扫描器原先照单全收：同一个技能被记两遍（「用户级」一份、「本工具技能库」一份），
    界面上就并排摆出两张一模一样的卡 —— 名册 65 条里 24 个名字是重名的。

    判据两条：**realpath 落在技能库之内**，且**自身路径与 realpath 不同**（即联接）。
    真身就在库里的目录两条都不成立，照收不误。
    """
    try:
        lib = skill_lib_dir()
        if not os.path.isdir(lib):
            return False
        rp = os.path.realpath(d).rstrip("\\/").lower()
        lp = os.path.realpath(lib).rstrip("\\/").lower()
        if rp != lp and not rp.startswith(lp + os.sep):
            return False
        return os.path.normcase(os.path.abspath(d)) != os.path.normcase(rp)
    except Exception:
        return False

def scan_skills(root, source):
    out = []
    if not os.path.isdir(root):
        return out
    for name in sorted(os.listdir(root)):
        d = os.path.join(root, name)
        sk = os.path.join(d, "SKILL.md")
        if not (os.path.isdir(d) and os.path.isfile(sk)):
            continue
        # 指向技能库的联接不再重复计 —— 真身那份已由「本工具技能库」收走
        if is_lib_mount(d):
            continue
        fm = read_frontmatter(sk)
        files = sum(len(fs) for _, _, fs in os.walk(d))
        out.append({
            "name": fm.get("name") or name, "dir": name,
            "desc": fm.get("description") or first_paragraph(sk),
            "version": fm.get("version", ""), "author": fm.get("author", ""),
            "license": fm.get("license", ""), "tags": fm.get("tags_list", []),
            "path": d, "source": source, "files": files,
        })
    return out

def machine_tag():
    """本机标识：家目录路径 + 用户名。

    用途 —— 名册里盖一枚「产地戳」。程序读名册时若见戳非本机，
    便知这是**随包带来的老底册**（采自别的电脑），须当即重扫，
    免得在新机上摆出一堆本机根本没有的技能与插件。
    """
    home = os.path.expanduser("~")
    return home.replace("/", "\\").rstrip("\\").lower()


def collect():
    HOME = os.path.expanduser("~")
    import time as _t            # 局部导入：动态载入时用的，顶上导入易被打包器漏掉
    # 第十八轮：`plugins` 一栏已从本工具永久撤除（WorkBuddy 私有包，只能看不能用），
    # 名册里不再收这个字段。
    R = {"skills": [], "mcp": [], "agents": [],
         "_home": machine_tag(), "_made": _t.strftime("%Y-%m-%d %H:%M:%S")}
    R["skills"] += scan_skills(os.path.join(HOME, ".workbuddy", "skills"), "用户级")
    # 本工具自己的技能库：从别处导入的 skill 落在这里（家目录下 agent-skills\）——
    # 路径须与 agent_inventory_app.py 里的 SKILL_LIB 保持一致。
    # 本工具技能库：第二十一轮起收进应用内部「通用资源\skills」，
    # 家目录那处留了 Junction，故旧路径照样认得。
    _lib = os.path.join(HERE, "通用资源", "skills")
    if not os.path.isdir(_lib):
        _lib = os.path.join(HOME, "agent-skills")
    R["skills"] += scan_skills(_lib, "本工具技能库")
    # 整合卡的成员：收进索引（hidden），让中文也能命中它们
    try:
        R["skills"] += scan_suite_members(R["skills"])
    except Exception:
        pass

    R["skill_shelves"] = []
    for lr in [".claude", ".cursor", ".trae", ".agent", ".agents"]:
        p = os.path.join(HOME, lr, "skills")
        if os.path.isdir(p):
            for n in sorted(os.listdir(p)):
                R["skill_shelves"].append({"shelf": lr, "entry": n, "path": os.path.join(p, n)})

    # 第二十五轮（本版要求）：「工具与运行时」一栏已从应用里撤除 ——
    # 原先此处把 agent-tools 与兵站下的一级目录当作「工具」来收，收出来的
    # 只是一串文件夹名（bin / jdk / node …），对 agent 干活没有帮助，且与
    # MCP 栏、技能栏重复。有用于干活的信息（安卓兵站的家底）已并入 MCP 条目。
    # Android 兵站：第二十轮起定名「安卓模拟器MCP」，且已搬进本工具自己的目录下。
    # 故先看程序旁，再退回家目录（兼容旧布局与旧名）。
    # 第二十二轮（本版要求）：「MCP 要全部通用，不通用的从应用里移除」——
    #   判据只有一条：入口是不是绑死在某个客户端自己的安装/运行时目录里。
    #   那种目录是那家安装或升级时整体替换的，别的 agent 不可能共用，
    #   故不算公共 MCP，不入册。（replicant 那种装在应用内、四家共用一行命令的，才算。）
    PRIVATE_MCP_MARKERS = (
        "\\appdata\\local\\openai\\codex\\runtimes\\",   # Codex 自带运行时
        "\\appdata\\local\\programs\\codex",              # Codex 程序目录
    )

    def _private_mcp(cmd):
        c = (cmd or "").lower()
        return any(mk in c for mk in PRIVATE_MCP_MARKERS)

    for label, p in {
        "WorkBuddy": os.path.join(HOME, ".workbuddy", "mcp.json"),
        "Cursor": os.path.join(HOME, ".cursor", "mcp.json"),
        "Claude Desktop": os.path.join(os.environ.get("APPDATA", ""), "Claude", "claude_desktop_config.json"),
        # 第三十四轮（有人问：「AstrBot 为什么没接入」）——
        #   AstrBot 的 MCP 注册表在 ~\.astrbot\data\mcp_server.json，结构与别家同为
        #   {"mcpServers": {...}}，先前漏扫了这一处，应用里便永远见不到 AstrBot 那一列。
        "AstrBot": os.path.join(HOME, ".astrbot", "data", "mcp_server.json"),
    }.items():
        if os.path.isfile(p):
            try:
                with open(p, "r", encoding="utf-8", errors="ignore") as f:
                    d = json.load(f)
                for sname, cfg in (d.get("mcpServers") or {}).items():
                    _cmd = cfg.get("command", "")
                    if _private_mcp(_cmd):      # 私有运行时，不算公共 MCP
                        continue
                    R["mcp"].append({"client": label, "name": sname,
                                     "command": _cmd, "config": p})
            except Exception as e:
                R["mcp"].append({"client": label, "name": "(解析失败)", "command": str(e), "config": p})

    codex = os.path.join(HOME, ".codex", "config.toml")
    if os.path.isfile(codex):
        txt = open(codex, "r", encoding="utf-8", errors="ignore").read()
        for mm in re.finditer(r"\[mcp_servers\.([\w\-\.]+)\]([\s\S]*?)(?=\n\[|\Z)", txt):
            name = mm.group(1)
            body = mm.group(2)
            cm = re.search(r"command\s*=\s*['\"]([^'\"]+)['\"]", body)
            # `[mcp_servers.X.env]` 是 X 的环境变量子表，不是另一个服务 —— 旧版
            # 把它当成名叫「X.env」的服务器收了进来，纯噪音，此处按「无 command 即非服务」跳过。
            if name.endswith(".env") or not cm:
                continue
            if _private_mcp(cm.group(1)):    # 私有运行时，不算公共 MCP
                continue
            _cmd = cm.group(1)
            # 配置里可能有 \uXXXX 转义（TOML 与 JSON 都允许），解成真字再入册，
            # 否则 MCP 栏里会显示成「u89c8.exe」这种半截路径
            try:
                _cmd = re.sub(r"\\u([0-9a-fA-F]{4})",
                              lambda m: chr(int(m.group(1), 16)), _cmd)
            except Exception:
                pass
            R["mcp"].append({"client": "Codex", "name": name,
                             "command": _cmd, "config": codex})

    # ★ 第十八轮（本版要求）：「把插件栏永久删除」——
    #   原先此处读 `~/.workbuddy/plugins/installed_plugins.json`，把 WorkBuddy 的
    #   48 个插件包收进名册。那些是 WorkBuddy 客户端自己加载的提示词/子代理包，
    #   本工具只能看不能用，故「插件」一栏与其数据源一并永久撤除。
    #   要恢复：备份\scan_agents.bak_before_plugincut.py。

    ws = os.path.join(HOME, "WorkBuddy")
    if os.path.isdir(ws):
        for proj in sorted(os.listdir(ws)):
            p = os.path.join(ws, proj, ".workbuddy", "skills")
            if os.path.isdir(p):
                R["skills"] += scan_skills(p, "项目级/%s" % proj)
    return R

def main():
    R = collect()
    with open(os.path.join(HERE, "agent_inventory.json"), "w", encoding="utf-8") as f:
        json.dump(R, f, ensure_ascii=False, indent=1)
    print("已刷新 agent_inventory.json：%d 技能 / %d MCP" % (
        len(R["skills"]), len(R["mcp"])))
    if "--json" not in sys.argv:
        gen = os.path.join(HERE, "_gen_html.py")
        if os.path.isfile(gen):
            subprocess.run([sys.executable, gen])

main()
