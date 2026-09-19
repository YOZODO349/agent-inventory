> *Screenshots use placeholder data — no real paths or project names.*

[中文](README.md) ｜ **English**

> `AI agent inventory` · `skill manager` · `MCP server inventory` · `session log aggregator` · `cross-agent project tracker` · local-first, no upload

> Scans a Windows machine for the AI agents, skills, MCP servers and projects
> scattered across it, and lays them out as one clickable inventory. Click a card to open
> its workbench — or launch the agent itself.

![Main window](docs/screenshot.en.png)

*(Both screenshots use placeholder data.)*

## Download (Windows, no install)

Grab `AgentAssetOverview.exe` from the **[latest release](https://github.com/cduxiu349/agent-inventory/releases/latest)** and double-click it:

- **No Python required**, nothing to configure;
- On first launch it scans **this** machine — the inventory carries an origin stamp, so a file
  carried over from someone else's computer is detected and re-scanned;
- Put it in a normal folder before running: it writes its data files next to itself
  (falling back to `%LOCALAPPDATA%` if that location is not writable, e.g. inside a zip);
- On high-DPI displays (125% / 150% / 200% scaling) the UI is **rendered natively** — no bitmap
  blur — and window sizes are clamped to the screen so nothing runs off the usable area.

## Why

Anyone who works with AI tools ends up with a pile of them: coding assistants, chat bots,
image-generation pipelines, dozens of skills and MCP servers. They hide in `AppData`, in
home-directory dotfiles, in each vendor's own config. Move to a new machine and you have to
hunt them down again — and after a while you forget what you even installed.

This tool does one thing: **finds them all and puts them on one table** — and gathers up the
work records they leave behind while it is at it.

## Four columns

| Column | What it shows |
|---|---|
| **Agents** | Installed agent clients — built-in registry, plus desktop / Start-menu shortcuts and a last-resort disk search. **Click a card to open its workbench.** |
| **Skills** | User-level and project-level skills (reads `SKILL.md` frontmatter). Skills that belong to one family are collapsed into a single **bundle card**. |
| **MCP Servers** | MCP registrations found in WorkBuddy / Cursor / Claude Desktop / Codex configs. **Only portable ones are listed** — entries pointing into a client's private runtime directory are filtered out. |
| **Main projects** | The projects you actually care about: dev log, implemented features, and where the artifacts live — on one page. |

### Every agent gets a workbench

- **Workbench**: the agent's blurb on top, its **entire work record** below;
- **Update data**: opens the agent and hands you a ready-to-paste prompt that tells it where to
  put its work records inside this app;
- **Auto-transcription**: every 10 minutes the app copies each agent's logs into that same
  folder (there is also a manual "手动抄录" button in the toolbar). A watermark means unchanged
  files are never copied twice, and oversized session transcripts are **summarised into readable text**;
- **Editable blurb**: each agent can have its own blurb, stored in `agent_intros.json` and kept
  across re-scans.

### Search

- **Global search**: type once and the app searches **all four columns at the same time**
  (agents / skills / MCP / work records), grouping hits by column and labelling their source;
- **Full-text search over work records**: reads every agent's records character by character and
  reports **which agent · which file · which line · surrounding context**; click a hit to open it.

### Main projects: track what you are actually building

- Define **as many as you like**, add or remove them at any time;
- When you create one, the app **automatically searches every agent's logs** and gathers the
  scattered records in one place — because a real project is usually built by several agents
  handing off to each other;
- Open a project card to see **implemented features**, the **dev log in reverse chronological
  order with full artifact paths**, and which agents took part.

### UI and interaction

- **Hover descriptions** on buttons (attached per widget class, so **buttons added later get one
  automatically**);
- **Keyboard shortcuts**: `Ctrl+F` focus search ｜ `F5` rescan ｜ `Ctrl+1..4` switch column ｜
  `Ctrl+L` manual transcription ｜ `Ctrl+P` project settings ｜ `Esc` clear search;
- Empty columns hide themselves, tab included; anything not found is simply not shown;
- The visual design follows a restrained "warm monochrome, hairline borders, near-black text,
  one solid accent" rule — colour is reserved for meaning.

## Quick start

Windows, Python 3.9 or newer. The UI uses only the standard library (`tkinter`) — **no third-party dependencies**.

Using [uv](https://docs.astral.sh/uv/) is recommended:

```bash
winget install --id=astral-sh.uv -e     # or: pip install uv

git clone https://github.com/cduxiu349/agent-inventory.git
cd Agent-asset-overview
uv run python src/agent_inventory_app.py
```

Or just use any Python:

```bash
python src/agent_inventory_app.py
```

On first launch it scans the machine in the background and writes two inventory files
(`agents.json`, `agent_inventory.json`) into `src/`. They are **not** tracked by git.

To re-collect without opening the UI:

```bash
uv run python scan_agents.py            # from src/, or double-click scripts/rescan.bat
```

## Build a single-file exe

```bash
uv run pyinstaller --noconfirm scripts/build_exe.spec
```

The result is `dist/AgentAssetOverview.exe`.

## Project layout

```
src/
  agent_inventory_app.py   # main program: tkinter UI + all interaction logic
  glass_widget.py          # the card widget (pure tkinter, hand-drawn)
  scan_agents.py           # collector: skills / MCP / inventory
  scan_agents_apps.py      # collector: agent clients / auto-transcription / task list
scripts/
  build_exe.spec           # PyInstaller spec
  rescan.bat               # double-click rescan
pyproject.toml             # project metadata and uv config
```

Once running, the app grows these folders **next to itself** (local data, never committed):

```
通用资源\skills\                   the skill library itself (other agents' shelves hold links)
通用资源\工作记录\<Agent>\          that agent's work records
通用资源\安卓模拟器MCP\              self-contained Android toolchain, if you deployed one
主要项目.json                       the projects you track
agent_intros.json                  the blurbs you wrote
```

> Folder names stay in Chinese on disk — only the app's own labels are translated here.

> The four `.py` files must stay in the same directory: the program resolves collectors and
> data files relative to its own location.

## Extending it

Everything lives in `src/scan_agents_apps.py` — copy the existing shapes:

| Constant | Purpose |
|---|---|
| `KNOWN` | Registry of known agent clients. Use `@HOME@` as a placeholder for the home directory; `hidden: True` keeps an entry off the list. |
| `SIGNATURES` | "Real body" file names per client — used to find an install whose registered path no longer matches. |
| `LOG_SOURCES` | **Log sources for auto-transcription**: where each agent keeps its logs (whole-file copy or summarised). |
| `DOC_DIRS` | Directories of Markdown docs to include in the inventory. |

## Where the data comes from

- Skills: `~/agent-skills` (a link) plus the usual shelves `~/.claude` `~/.cursor` `~/.trae` `~/.agent` `~/.agents`
- MCP: `~/.workbuddy/mcp.json`, `~/.cursor/mcp.json`, Claude Desktop config, `~/.codex/config.toml`
- **Work logs**: WorkBuddy session memory, Codex session transcripts and notes, AstrBot session workspaces (see `LOG_SOURCES`)
- Agents: built-in registry + desktop / Start-menu shortcuts + an on-demand full-disk search (installers and uninstallers are skipped)

## About the inventory files

`src/agents.json` and `src/agent_inventory.json` are **runtime artifacts** collected from
your own machine: they contain your user-name paths and the skills you have installed. They are
listed in `.gitignore` — **please do not commit them**; that would publish your machine's inventory.

Every launch checks the origin stamp on those files: if they came from another computer,
they are discarded and re-scanned.

## License

[MIT](LICENSE)
