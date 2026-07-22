# Hyperagent Visual Regression Checklist

Surface-by-surface pre/post comparison for the [Hyperagent Aesthetic Refresh (WI-113)](../../work/to-do/hyperagent-aesthetic-refresh.md). Each surface enumerated below is the Hyperagent-owned slice of the [WI-111](../../work/to-do/hyper-ecosystem-aesthetic-refresh.md) expanded verification list, mirroring the structure of [Hypervisor's checklist](../../.hypervisor/docs/visual-regression-checklist.md).

- Created: 2026-07-21T11:01
- Updated: 2026-07-21T11:01

---

## How to Use

1. **Baseline pass** — before Phase 1 begins, capture a screenshot of every surface in every listed state and save to `.hyperagent/docs/screenshots/baseline-pre-refresh/` using the filename convention `{surface-slug}--{state}.png`. Per WI-112's lesson, screenshots are optional if a clean git baseline commit exists — but high-risk surfaces (welcome noise field, thinking bar, tool cards) benefit from screenshot capture.
2. **Refresh pass** — after Phase 9 completes, capture a matching screenshot for every row and save to `.hyperagent/docs/screenshots/refresh-post/`.
3. **Compare** — for each row, tick the checkbox and note any intentional visual delta in the Notes column. Unintentional deltas open a bugfix before this WI closes.

Screenshot resolution: capture at the app's default window size (do not resize between pre and post). Include the topbar/status cluster and input area so surrounding context is comparable.

---

## Filename Convention

`{surface-slug}--{state}.png`

- `surface-slug` — kebab-case, matches the row's slug column
- `state` — one of `rest`, `hover`, `focus`, `active`, `open`, `running`, `completed`, `failed` (as applicable)

Example: `msg-agent--hover.png`, `tool-card--completed.png`, `sidebar--open.png`.

---

## Surface Checklist

### Chrome (Topbar, Status, Error Bar)

| Slug | Surface | States | Baseline | Refresh | Notes |
|------|---------|--------|----------|---------|-------|
| `status-cluster` | Floating status cluster (top-right) | rest | [ ] | [ ] | |
| `topbar-status-ready` | Status pill — ready | rest | [ ] | [ ] | |
| `topbar-status-prompting` | Status pill — prompting | rest | [ ] | [ ] | |
| `topbar-status-crashed` | Status pill — crashed | rest | [ ] | [ ] | |
| `status-title` | Session title pill | rest | [ ] | [ ] | |
| `ctx-meter` | Context usage meter | rest, warn, critical | [ ] | [ ] | |
| `plan-credits` | Plan credits widget | rest, warn, critical | [ ] | [ ] | |
| `plan-credits-refresh` | Plan credits refresh button | rest, hover, spinning | [ ] | [ ] | |
| `error-bar` | Error banner | visible | [ ] | [ ] | |

### Welcome Screen

| Slug | Surface | States | Baseline | Refresh | Notes |
|------|---------|--------|----------|---------|-------|
| `welcome-icon` | Welcome icon | rest | [ ] | [ ] | |
| `welcome-text-plain` | Welcome text (plain) | rest | [ ] | [ ] | |
| `welcome-text-emote` | Welcome text (kaomoji + glow) | rest | [ ] | [ ] | Bayer-dither backdrop |
| `welcome-canvas` | Bayer-dither noise field backdrop | rest, fade-out | [ ] | [ ] | Migrating to HvNoiseField |
| `welcome-chip` | Prompt suggestion chip | rest, hover | [ ] | [ ] | |
| `welcome-prompts` | Prompt chip row | rest | [ ] | [ ] | |

### Messages

| Slug | Surface | States | Baseline | Refresh | Notes |
|------|---------|--------|----------|---------|-------|
| `msg-user` | User message bubble | rest, hover | [ ] | [ ] | Red corner indicator |
| `msg-agent` | Agent message bubble | rest, hover | [ ] | [ ] | Accent corner indicator |
| `msg-meta` | Role label + timestamp | rest, hover (shows time) | [ ] | [ ] | |
| `msg-copy` | Copy button on agent message | hover, copied | [ ] | [ ] | |
| `steering-card` | Steering files included card | rest | [ ] | [ ] | |
| `typing-indicator` | Typing indicator (blinking box) | rest | [ ] | [ ] | |
| `streaming-cursor` | Streaming cursor block | rest | [ ] | [ ] | |
| `stream-word` | Stream word fade-in | rest | [ ] | [ ] | |
| `turn-end` | Turn-end divider | rest | [ ] | [ ] | |

### Markdown Content (in Agent Message)

| Slug | Surface | States | Baseline | Refresh | Notes |
|------|---------|--------|----------|---------|-------|
| `md-h1` | H1 (accent, uppercase) | rest | [ ] | [ ] | |
| `md-h2` | H2 (accent, uppercase) | rest | [ ] | [ ] | |
| `md-h3` | H3 (warm) | rest | [ ] | [ ] | |
| `md-h4` | H4 (cool) | rest | [ ] | [ ] | |
| `md-p` | Paragraph | rest | [ ] | [ ] | |
| `md-list` | UL / OL lists | rest | [ ] | [ ] | |
| `md-inline-code` | Inline code | rest | [ ] | [ ] | |
| `md-code-block` | Fenced code block | rest, hover (lang + copy) | [ ] | [ ] | |
| `md-code-copy` | Code block copy button | rest, hover, copied | [ ] | [ ] | |
| `md-table` | Markdown table | rest, row-hover | [ ] | [ ] | |
| `md-blockquote` | Blockquote | rest | [ ] | [ ] | |
| `md-link` | Inline link | rest, hover | [ ] | [ ] | |
| `md-hr` | Horizontal rule | rest | [ ] | [ ] | |

### Tool Cards

| Slug | Surface | States | Baseline | Refresh | Notes |
|------|---------|--------|----------|---------|-------|
| `tool-row` | Tool row container | rest | [ ] | [ ] | |
| `tool-card-core` | Tool card — core group | running, completed, failed, hover | [ ] | [ ] | Warm |
| `tool-card-web` | Tool card — web group | running, completed | [ ] | [ ] | Cool |
| `tool-card-aws` | Tool card — AWS group | running, completed | [ ] | [ ] | Amber |
| `tool-card-devops` | Tool card — DevOps group | running, completed | [ ] | [ ] | Blue |
| `tool-card-hyper` | Tool card — hyper group | running, completed | [ ] | [ ] | Accent |
| `tool-card-knowledge` | Tool card — knowledge group | running, completed | [ ] | [ ] | Comp |
| `tool-card-subagent` | Tool card — subagent | rest | [ ] | [ ] | |
| `tool-card-tasks` | Tool card — tasks | rest | [ ] | [ ] | |
| `tool-card-long-running` | Tool card — long-running pulse | rest | [ ] | [ ] | |
| `tool-card-labeled` | Tool card with expanded label | show-label | [ ] | [ ] | |
| `tool-detail` | Tool detail panel (expandable) | visible | [ ] | [ ] | |
| `tool-error-inline` | Failed tool inline error snippet | visible | [ ] | [ ] | |

### Input Area

| Slug | Surface | States | Baseline | Refresh | Notes |
|------|---------|--------|----------|---------|-------|
| `input-area` | Input area container | rest | [ ] | [ ] | |
| `input-textarea` | Prompt textarea | rest, focus, disabled | [ ] | [ ] | |
| `input-icon-btn` | Input icon button | rest, hover | [ ] | [ ] | |
| `send-btn` | Send button | rest, hover, disabled | [ ] | [ ] | |
| `cancel-btn` | Cancel button | rest, hover | [ ] | [ ] | |

### Sidebar (Sessions)

| Slug | Surface | States | Baseline | Refresh | Notes |
|------|---------|--------|----------|---------|-------|
| `sidebar` | Sessions sidebar | closed, open | [ ] | [ ] | |
| `session-item` | Session list item | rest, hover, active | [ ] | [ ] | |
| `session-item-title` | Session title within item | rest | [ ] | [ ] | |
| `session-delete-btn` | Session delete button | rest, hover | [ ] | [ ] | |
| `session-search` | Session search input | rest, focus | [ ] | [ ] | |

### Splash

| Slug | Surface | States | Baseline | Refresh | Notes |
|------|---------|--------|----------|---------|-------|
| `splash-overlay` | Splash overlay | rest, fade-out | [ ] | [ ] | |
| `splash-logo` | Splash logo | rest | [ ] | [ ] | |
| `splash-text` | Splash tagline | rest | [ ] | [ ] | |
| `splash-blink` | Splash blinking cursor | rest | [ ] | [ ] | |

### Tabs

| Slug | Surface | States | Baseline | Refresh | Notes |
|------|---------|--------|----------|---------|-------|
| `tab-bar` | Tab bar | rest | [ ] | [ ] | |
| `tab-item` | Individual tab | rest, hover, active | [ ] | [ ] | |
| `tab-close` | Tab close button | rest, hover | [ ] | [ ] | |
| `tab-new` | New tab button | rest, hover | [ ] | [ ] | |

### Tasks Panel

| Slug | Surface | States | Baseline | Refresh | Notes |
|------|---------|--------|----------|---------|-------|
| `tasks-panel` | Tasks sidebar panel | rest, open | [ ] | [ ] | |
| `task-item` | Task item | rest, hover, completed | [ ] | [ ] | |
| `task-progress` | Task progress bar | rest | [ ] | [ ] | Migrating to `--progress-gradient` |

### Overlays

| Slug | Surface | States | Baseline | Refresh | Notes |
|------|---------|--------|----------|---------|-------|
| `shortcuts-overlay` | Keyboard shortcuts overlay | visible | [ ] | [ ] | |
| `search-bar` | In-session search bar | visible, hit | [ ] | [ ] | |

### Thinking Bar

| Slug | Surface | States | Baseline | Refresh | Notes |
|------|---------|--------|----------|---------|-------|
| `thinking-bar` | CRT-scan thinking bar | active (tool-category color) | [ ] | [ ] | Migrating with WebGL2 shared modules |

---

## Global Verification

After per-surface capture, verify these cross-surface concerns hold:

- [ ] **Accent cascade**: change the accent color via bridge/Hypervisor sync; every surface updates without a page reload
- [ ] **Reduced motion**: enable OS-level reduced motion; every animation shortens or disappears (welcome noise field, thinking bar, message appear, tool card flash/shake/pulse)
- [ ] **High contrast**: chip/badge/tool-card variants remain distinguishable (colors alone are not the only signal)
- [ ] **Focus outlines**: keyboard-focused primitives (textarea, buttons, session items, tabs) show a visible outline meeting WCAG contrast
- [ ] **No off-scale values**: grep `.hyperagent/assets/css/` for raw `rem`/`px`/`z-index`/`box-shadow` literals (Phase 1 audit)
- [ ] **Byte-identical tokens**: `diff .hypervisor/assets/css/00-variables.css .hyperagent/assets/css/00-variables.css` — the `:root` token block is identical
