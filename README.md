<p align="center">
  <img src="assets/hyperagent-box.png" alt="Hyperagent" width="200">
</p>

<h1 align="center">Hyperagent</h1>

<p align="center">
  A standalone desktop AI chat app powered by Kiro CLI's Agent Communication Protocol.
</p>

---

## What It Does

Hyperagent wraps `kiro-cli acp` in a native desktop window, replacing the terminal-based chat experience with a graphical UI:

- **Streaming responses** — real-time markdown rendering as the agent types
- **Tool call visualization** — compact cards showing each tool invocation with status and expandable details
- **Session management** — sidebar with session history, load/switch/delete
- **Cancel support** — stop a running prompt mid-stream with immediate feedback
- **Keyboard-driven** — shortcuts for send, cancel, new session, search, fullscreen
- **In-session search** — Ctrl+F to find text across the conversation
- **Theme sync** — accent color automatically synced from Hypervisor's palette
- **Welcome prompts** — quick-start chips for common actions

## Design Philosophy

- **Zero frameworks** — Python + vanilla CSS + vanilla JS. No React, no Node, no bundler.
- **Brutalist terminal aesthetic** — pure black background, Departure Mono everywhere, hard edges, no border-radius.
- **Native desktop** — PyWebView window, not a browser tab. Launchable from Start menu or taskbar.
- **Thin client** — Hyperagent is just a UI shell. All intelligence lives in kiro-cli.

## Quick Start

```bash
pip install pywebview
cd .hyperagent
python build.py
pythonw hyperagent.py
```

**Prerequisites:**
- Python 3.10+
- `pywebview` (pip install)
- `kiro-cli` installed and authenticated (`kiro-cli login`)

## How It Works

### Architecture

```
┌─────────────────────────────────────────────────────┐
│  PyWebView Window (hyperagent.py)                   │
│  ┌────────────────────┐  ┌───────────────────────┐  │
│  │  HyperagentAPI     │  │  ACPClient            │  │
│  │  (JS bridge)       │  │  (JSON-RPC state mgr) │  │
│  └────────────────────┘  └──────────┬────────────┘  │
│                                     │ TCP socket     │
├─────────────────────────────────────┼───────────────┤
│  acp_bridge.py (subprocess)         │               │
│  ┌──────────────────────────────────┴─────────────┐ │
│  │  TCP ↔ stdio relay                             │ │
│  └──────────────────────────────────┬─────────────┘ │
│                                     │ stdin/stdout   │
│  kiro-cli acp --trust-all-tools     │               │
│  ┌──────────────────────────────────┴─────────────┐ │
│  │  AI agent (ACP protocol, JSON-RPC over stdio)  │ │
│  └────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────┘
```

**Why the TCP bridge?** PyWebView interferes with subprocess stdio pipes on Windows. The bridge (`acp_bridge.py`) runs as a separate process that spawns kiro-cli with direct pipe access and relays JSON-RPC messages over a localhost TCP socket.

### State Machine

```
stopped → starting → ready ↔ prompting
                       ↓
                    crashed (on connection loss)
```

The frontend reacts to state transitions pushed from Python via `window.evaluate_js()`.

### Build Pipeline

```
assets/shell.html          ← HTML skeleton with {{CSS}} and {{JS}} placeholders
assets/css/00-*.css        ← concatenated in sorted order → replaces {{CSS}}
assets/js/00-*.js          ← concatenated in sorted order → replaces {{JS}}
                           ↓
build.py                   → generated_html.py (HTML as Python string literal)
                           ↓
hyperagent.py imports HTML → passes to webview.create_window(html=HTML)
```

No runtime file serving — the entire UI is a single inline HTML string passed to PyWebView.

## Project Structure

```
.hyperagent/
├── hyperagent.py          ← Main app: ACPClient, HyperagentAPI, PyWebView setup
├── acp_bridge.py          ← TCP ↔ stdio relay subprocess
├── build.py               ← Concatenates CSS/JS into generated_html.py
├── assets/
│   ├── shell.html         ← HTML template
│   ├── css/
│   │   ├── 00-variables.css   ← Custom properties, reset, scrollbar, animations
│   │   ├── 01-layout.css      ← Topbar, app layout, error bar
│   │   ├── 02-messages.css    ← Message bubbles, code blocks, markdown
│   │   ├── 03-tools.css       ← Tool call cards and states
│   │   ├── 04-input.css       ← Input area, send/cancel buttons
│   │   ├── 05-sidebar.css     ← Session sidebar
│   │   └── 06-splash.css      ← Loading splash screen
│   ├── js/
│   │   ├── 00-core.js         ← IIFE open, DOM refs, state, accent sync
│   │   ├── 01-markdown.js     ← Lightweight markdown→HTML renderer
│   │   ├── 02-handlers.js     ← ACP event handlers, tool cards, thinking indicator
│   │   ├── 03-ui.js           ← Send, cancel, shortcuts, search, IIFE close
│   │   └── 04-sidebar.js      ← Session list management (outside IIFE)
│   └── (icons: .ico, .png, .svg)
└── .gitignore
```

## Features

### Chat Interface

- User and agent message bubbles with timestamps
- Streaming markdown rendering with cursor indicator
- Code blocks with syntax highlighting and copy button
- Message-level copy button

### Tool Calls

- Compact card per tool invocation (icon + name)
- Color-coded by MCP group (core, AWS, DevOps, Hypervisor, web, knowledge)
- Running/completed/failed states with long-running detection
- Click to expand input/output details

### Session Management

- Sidebar lists sessions filtered to current project
- Session age, message count, and lock status
- AI-generated session titles from first prompt
- Load, switch, and delete sessions
- Auto-restore last session on launch

### Keyboard Shortcuts

| Key | Action |
|-----|--------|
| Enter | Send message |
| Shift+Enter | New line |
| Escape | Cancel prompt / close overlay |
| / | Focus input |
| ? | Toggle shortcuts overlay |
| Ctrl+B | Toggle sidebar |
| Ctrl+N | New session |
| Ctrl+F | Search messages |
| F | Toggle fullscreen |

## Development

Edit source files in `assets/`, never `generated_html.py`:

1. Edit CSS modules in `assets/css/` or JS modules in `assets/js/`
2. Run `python build.py`
3. Restart the app (`pythonw hyperagent.py`)

For Python changes to `hyperagent.py` or `acp_bridge.py`, just restart — no build step needed.

## License

Personal project. Not currently licensed for distribution.
