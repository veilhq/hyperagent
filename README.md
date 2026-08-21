<p align="center">
  <img src="assets/hyperagent.svg" alt="Hyperagent" width="200">
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

> **Requires Hyperkit.** `python build.py` reads shared CSS/JS from `.hyperspace/.hyperkit/` and fails with a `FileNotFoundError` if it's missing. Hyperkit must exist as a sibling of `.hyperagent/` (i.e. `.hyperspace/.hyperkit/`) before building. See [Dependencies](#dependencies) below.

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
- `.hyperspace/.hyperkit/` present as a sibling directory (see [Dependencies](#dependencies))

## Dependencies

### Hyperkit (required, not a package)

Hyperagent is not self-contained — it depends on **Hyperkit**, the shared design system package at `.hyperspace/.hyperkit/`. Hyperkit supplies:

- `css/tokens.css` + `css/primitives.css` — the universal `:root` custom properties and shared component classes (`hv-chip`, `hv-row`, `hv-button`, etc.), prepended ahead of every file in `assets/css/`
- Five JS modules (`cursor-box.js`, `noise-field.js`, `greeting.js`, `cursor-trail.js`, `toast.js`) — inlined as `<script>` blocks before any file in `assets/js/`
- `python/hyper_logging.py` — the structured logging setup imported by `hyperagent.py` and `acp_bridge.py`

This isn't a pip package — it's a sibling directory that must physically exist at `.hyperspace/.hyperkit/` relative to this repo. If you're cloning Hyperagent standalone into a workspace that doesn't already have `.hyperkit/`, copy or clone it in before running `build.py`. `build.py` raises `FileNotFoundError` immediately (not a silent fallback) if any required Hyperkit file is missing.

See [`.hyperspace/.hyperkit/README.md`](../.hyperkit/README.md) for what lives there and the override pattern for anything Hyperagent needs to render differently than Hypervisor (e.g. the clip-path `.hv-tab` shape).

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
.hyperspace/.hyperkit/     ← Shared tokens.css, primitives.css, 5 JS modules
                           ↓ read first, before any local file
assets/shell.html          ← HTML skeleton with {{CSS}} and {{JS_BLOCKS}} placeholders
assets/css/*.css           ← concatenated in sorted order → replaces {{CSS}}
assets/js/*.js             ← emitted as per-module <script> blocks → replaces {{JS_BLOCKS}}
                           ↓
build.py                   → generated_html.py (HTML as Python string literal)
                           ↓
hyperagent.py imports HTML → passes to webview.create_window(html=HTML)
```

No runtime file serving — the entire UI is a single inline HTML string passed to PyWebView. Each JS module (Hyperkit's five plus every app-local file) is emitted as its own `<script>` block so a parse error in one module doesn't take down the app.

## Project Structure

```
.hyperagent/
├── hyperagent.py          ← Main app: ACPClient, HyperagentAPI, PyWebView setup
├── acp_client.py          ← ACPClient class: JSON-RPC state machine over TCP
├── acp_pool.py            ← ACP connection pool management
├── acp_bridge.py          ← TCP ↔ stdio relay subprocess
├── bridge_api.py          ← Bridge API: theme, palette, accent sync, gradient maps
├── helpers.py             ← Shared utilities: OKLCh palette generation, color math
├── build.py               ← Concatenates CSS/JS into generated_html.py (prepends Hyperkit — see below)
├── assets/
│   ├── shell.html         ← HTML template
│   ├── css/
│   │   ├── 00-primitives.css  ← Local override only (post-WI-142) — the clip-path .hv-tab shape
│   │   ├── 00-variables.css   ← App-local globals only (post-WI-142) — reset, cursors, scrollbar, animations
│   │   ├── 01-layout.css      ← Topbar, app layout, error bar, status cluster
│   │   ├── 02-messages.css    ← Message bubbles, code blocks, markdown, welcome screen
│   │   ├── 03-tools.css       ← Tool call cards and states
│   │   ├── 04-input.css       ← Input area, send/cancel buttons
│   │   ├── 05-sidebar.css     ← Session sidebar
│   │   ├── 06-splash.css      ← Loading splash screen
│   │   ├── 07-skills.css      ← Skill activation strip
│   │   ├── 07-tabs.css        ← Per-tab message containers
│   │   ├── 08-tasks.css       ← Task sidebar panel
│   │   └── zz-accessibility.css ← A11y overrides (loads last)
│   ├── js/
│   │   ├── 00-core.js         ← DOM refs, state, accent sync, PyWebView bridge detection
│   │   ├── 01-markdown.js     ← Lightweight markdown→HTML renderer
│   │   ├── 02-handlers.js     ← ACP event handlers, tool cards, stream buffer
│   │   ├── 03-ui.js           ← Send, cancel, shortcuts, welcome screen mount
│   │   ├── 04-sidebar.js      ← Session list management
│   │   ├── 05-thinking.js     ← WebGL2 thinking bar indicator
│   │   ├── 06-welcome.js      ← startWelcomeNoise / destroyWelcomeNoise shim over HvNoiseField
│   │   ├── 07-tasks.js        ← Task sidebar panel
│   │   └── 08-tabs.js         ← Session tab bar
│   └── (icons: .ico, .png, .svg)
└── .gitignore
```

### Hyperkit (WI-142)

Five ecosystem JS modules (`HvCursorBox`, `HvNoiseField`, `HvGreeting`, `HvCursorTrail`, `HvToast`) and the shared CSS (`tokens.css`, `primitives.css`) no longer live inside `.hyperagent/`. They live one directory up at `.hyperspace/.hyperkit/`, shared verbatim with Hypervisor. `build.py` reads them from there and emits them first — before any file in `assets/`. See `.hyperspace/.hyperkit/README.md` for the full consumption pattern and override rules. Edit those five JS files and the two CSS files in `.hyperkit/`, never as a local copy inside `.hyperagent/assets/`.

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
