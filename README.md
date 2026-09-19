# Agent 资产总览 · Agent Asset Overview

**中文** ｜ [English](README.en.md)

> `AI agent inventory` · `skill manager` · `MCP server inventory` · `session log aggregator` · `cross-agent project tracker` · local-first, no upload

> 把一台 Windows 电脑上散落的 **AI Agent 客户端 / 技能 / MCP 服务 / 主要项目**，
> 扫成一张看得见、点得动的名册。点卡片进工作台，或直接启动。

![主界面](docs/screenshot.png)

> *截图为示例数据，不含任何真实路径与项目名。*

## 下载（Windows 免安装版）

到 **[Releases](https://github.com/cduxiu349/agent-inventory/releases/latest)** 下载 `AgentAssetOverview.exe`，双击即用：

- **不需要装 Python**，也不需要配任何环境；
- 首次运行会自动扫描**你这台机器**——名册带「产地戳」，别人带来的底册会被识破并弃用重扫；
- 建议先放进一个正常文件夹再双击（它会在自己旁边写名册与工作记录；写不动时退到 `%LOCALAPPDATA%`）；
- 高 DPI 屏（125% / 150% / 200% 缩放）下界面**原生渲染**，不糊；窗口尺寸会自动夹进屏幕，不会超出可用区。

## 它想解决什么

用 AI 的人，机器上常常不知不觉装了一堆东西：编程助手、聊天机器人、绘图工作流、
各种技能与 MCP 服务……它们散落在 `AppData`、家目录、各家自己的配置里。
换一台电脑就要重新找一遍；时间久了，自己也记不清装过什么。

这个工具只做一件事：**把它们全找出来，摆成一张桌子。**
并且——**顺手把干活留下的记录也收拢起来**。

## 四栏名册

| 栏目 | 内容 |
|---|---|
| **Agents** | 本机的 Agent 客户端（内置登记表 + 桌面/开始菜单快捷方式 + 全盘寻真身）。**点卡片进它的工作台** |
| **技能** | 用户级与项目级的技能（读 `SKILL.md` 的 frontmatter）；同源成套的技能会收成一张**整合卡** |
| **MCP 服务** | 各家客户端的 MCP 注册（WorkBuddy / Cursor / Claude Desktop / Codex …）。**只收通用项**——绑死在某个客户端私有运行时目录里的会被剔除 |
| **主要项目** | 你划重点的项目：开发日志、已实现的功能、产物在哪，一页看尽 |

### 每个 Agent 都有自己的工作台

- **工作台**：顶部是简介，下面是这个 Agent 的**全部工作记录**；
- **更新数据**：一键打开该 Agent，并给出一段可直接粘贴的提示词，让它把工作记录整理进应用给它留的那格文件夹；
- **自动抄录**：应用**每 10 分钟**自动把各 Agent 的日志抄进同一格（工具条另有「手动抄录」可随时催一轮）。抄录带水位记账，没变的不重复搬；过大的会话实录会被**摘录成可读文本**；
- **可编辑简介**：每个 Agent 都能写一段自己的简介，存 `agent_intros.json`，重扫不会丢。

### 搜索与检索

- **全局搜索**：搜索框一敲字，**四路同搜**（Agents / 技能 / MCP / 工作记录），结果按栏分组、标明出处；
- **工作记录全库检索**：逐字翻遍各 Agent 的工作记录，命中给出**哪个 Agent · 哪个文件 · 第几行 · 上下文**，点一下打开该文件。

### 主要项目：盯住你真正在做的

- 可设**多条**，随时**增删改**；
- 建项目时**自动逐字检索各 Agent 的工作日志**，把散落各家的记录汇到一处——因为一个主要项目多半是几个 Agent 接力做出来的；
- 点开项目卡：**已实现的功能**、**开发日志（按时间倒序，含产物完整地址）**、参与过的 Agent。

### 界面与交互

- **悬停说明**：鼠标停在按钮上即弹一句话说明（按控件类接管，**以后新加的按钮自动生效**）；
- **键盘快捷键**：`Ctrl+F` 聚焦搜索 ｜ `F5` 重新扫描 ｜ `Ctrl+1..4` 切换栏目 ｜ `Ctrl+L` 手动抄录 ｜ `Ctrl+P` 设置主要项目 ｜ `Esc` 清空搜索；
- **空栏目连页签一起隐去**；没检索到的东西不摆出来；
- 界面照「暖单色 + 超淡边 + 近黑字 + 一处实心主色」的规范收敛，颜色只用于语义。

## 快速开始

需要 **Windows + Python 3.9+**（界面用标准库 `tkinter`，**没有第三方依赖**）。

推荐用 [uv](https://docs.astral.sh/uv/) 管理环境：

```bash
# 安装 uv（任选其一）
winget install --id=astral-sh.uv -e
pip install uv

git clone https://github.com/cduxiu349/agent-inventory.git
cd Agent-asset-overview
uv run python src/agent_inventory_app.py     # 首次运行会自动装好环境
```

不想用 uv 也可以直接跑：

```bash
python src/agent_inventory_app.py
```

首次运行会在后台扫描本机，并在 `src/` 下生成两份名册（`agents.json`、`agent_inventory.json`）——它们**不进版本库**。

只重采名册、不开界面：

```bash
uv run python src/scan_agents.py     # 或双击 scripts/rescan.bat
```

## 打包成单文件 exe

```bash
uv run pyinstaller --noconfirm scripts/build_exe.spec
```

产物在 `dist/AgentAssetOverview.exe`。

## 目录结构

```
src/
  agent_inventory_app.py   # 主程序：tkinter 界面 + 全部交互逻辑
  glass_widget.py          # 卡片控件（纯 tkinter 手绘）
  scan_agents.py           # 采集器：技能 / MCP / 名册
  scan_agents_apps.py      # 采集器：Agent 客户端 / 自动抄录 / 任务与产物一览
scripts/
  build_exe.spec           # PyInstaller 规格
  rescan.bat               # 双击重扫
pyproject.toml             # 项目元数据与 uv 配置
```

程序跑起来之后，会在**自己旁边**长出这些（都是本机数据，不进版本库）：

```
通用资源\skills\           技能库真身（其余各家技能架里留的是指向这里的链接）
通用资源\工作记录\<Agent>\ 该 Agent 的工作记录（自动抄录的与手写/agent 交的同处）
通用资源\安卓模拟器MCP\     自带运行时的安卓工具链（若你部署过）
主要项目.json              你设的主要项目
agent_intros.json         你写的 Agent 简介
```

> 四个 `.py` 必须**待在同一个目录**：程序以「自身所在目录」为基准去找采集器、写数据。

## 想加自己的东西，改这几处

都在 `src/scan_agents_apps.py` 里，格式照抄即可：

| 常量 | 用途 |
|---|---|
| `KNOWN` | 已知 Agent 客户端登记表。路径用 `@HOME@` 占位；`hidden: True` 表示不上架 |
| `SIGNATURES` | 各客户端的「真身」特征名，登记路径落空时靠它全盘找 |
| `LOG_SOURCES` | **自动抄录的日志源**：哪个 Agent 的日志在哪（支持整篇抄与摘录两种） |
| `DOC_DIRS` | 想收编进来的说明文档目录 |

## 数据从哪来

- 技能：`~/agent-skills`（链接）与传统各家技能架 `~/.claude` `~/.cursor` `~/.trae` `~/.agent` `~/.agents`
- MCP：`~/.workbuddy/mcp.json`、`~/.cursor/mcp.json`、Claude Desktop 配置、`~/.codex/config.toml`
- **工作日志**：WorkBuddy 的会话记忆、Codex 的会话实录与笔记、AstrBot 的会话工作区等（见 `LOG_SOURCES`）
- Agent：内置登记表 + 桌面/开始菜单快捷方式 + 需要时的全盘搜索（跳过安装包与卸载器）

## 关于名册文件

`src/agents.json` 与 `src/agent_inventory.json` 是**运行期产物**，采自你自己的电脑：
里面有你的用户名路径、你装的技能与清单。已在 `.gitignore` 中忽略，**请勿提交** ——
那等于把你的机器清单公开了。每次启动都会检查它们的「产地戳」，不是本机的就整份弃用重扫。

## 许可

[MIT](LICENSE)
