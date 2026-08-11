# Infinity Code Design System

## 1. Atmosphere & Identity

Infinity Code is a focused creative command center: calm enough for long conversations, precise enough for real code and filesystem work, and explicit about what the assistant can do. The signature is a warm amber signal moving through layered charcoal surfaces. Amber always means active, selected, or ready; it is never decorative noise. The continuous infinity mark and compact tool-status language make the product recognizable without turning the workspace into a branded billboard.

## 2. Color

### Palette

| Role | Token | Dark | Light | Usage |
|---|---|---:|---:|---|
| Background | `--c-bg` / `--bg` | `#18181b` | `#fafaf9` | App canvas |
| Surface | `--c-surface` / `--surface` | `#202024` | `#ffffff` | Composer, sidebar controls, cards |
| Surface raised | `--c-surface2` / `--surface-2` | `#27272c` | `#f4f4f5` | Popovers, selected controls, overlays |
| Text primary | `--c-tx` / `--text` | `#ededf0` | `#18181b` | Headings and body |
| Text secondary | `--c-tx-dim` / `--text-dim` | `#a6a6ae` | `#52525b` | Supporting copy and labels |
| Text muted | `--c-tx-mut` / `--text-muted` | `#78788a` | `#84848d` | Metadata, placeholders, disabled states |
| Interaction light | `--interaction-light-rgb` | `255 255 255` | `255 255 255` | Brief hover/focus light sweep on interactive labels only |
| Border default | `--border` | white at 8% | ink at 9% | Hairlines and contained controls |
| Border strong | `--border-strong` | white at 14% | ink at 16% | Focused or raised surfaces |
| Accent primary | `--accent` | `#f0b347` | `#d97a06` | Active tools, primary CTA, focus |
| Accent hover | `--accent-hover` | `#f7bd55` | `#c26d05` | Hovered accent controls |
| Status success | `--status-success` | `#58b98b` | `#167a52` | Completed, connected |
| Status warning | `--status-warning` | `#e2a84b` | `#a35d08` | Approval and caution |
| Status error | `--status-error` | `#df7373` | `#b42323` | Failed and destructive |
| Status info | `--status-info` | `#70a9e8` | `#2767a8` | Running and informational |
| Window background | `--window-bg` | `#101010` | `#fafafa` | Undecorated shell and Models page |
| Window chrome | `--window-chrome` | `#111111` | `#ffffff` | Desktop title bar |
| Registry heading | `--registry-heading` | `#8190a3` | `#4f6175` | Models page title |

### Rules

- Amber is reserved for interaction, selection, focus, and live capability state.
- Status colors communicate outcomes or risk only.
- Background tone presets may change the neutral ramp, but the hierarchy must remain background -> surface -> raised.
- New colors must be added here before use.

## 3. Typography

### Scale

| Level | Size | Weight | Line height | Tracking | Usage |
|---|---:|---:|---:|---:|---|
| Display | 38px | 600 | 1.12 | -0.03em | Empty-state greeting |
| H1 | 28px | 600 | 1.2 | -0.025em | Screen title |
| H2 | 22px | 600 | 1.25 | -0.02em | Panel heading |
| H3 | 17px | 600 | 1.35 | -0.01em | Section or card heading |
| Body large | 16px | 400 | 1.65 | 0 | Lead and chat content |
| Body | 15px | 400 | 1.6 | 0 | Default application copy |
| Body small | 14px | 400/500 | 1.5 | 0 | Controls and secondary UI |
| Caption | 12px | 500 | 1.4 | 0.01em | Metadata and compact labels |
| Overline | 11px | 600 | 1.3 | 0.08em | Group labels |

### Font stack

- The default family is `Geist Sans`, with the platform UI stack as fallback.
- Users can choose Geist Sans, Inter, Space Grotesk, or JetBrains Mono in Appearance. Persist the chosen family and apply it globally so density remains intentional.
- Keep the same selected family across application chrome, forms, costs, counts, durations, identifiers, and metadata. Hierarchy comes from size, weight, color, spacing, and tabular numerals—not arbitrary font mixing.
- Reserve JetBrains Mono for users who explicitly want a code-first workspace; do not use a second display face just for decoration.
- Bundle only the Latin subset of optional families by default. Non-Latin glyphs fall back to the platform UI font rather than silently forcing every workspace to download every script subset.

### Rules

- Body copy never renders below 14px; 11-12px is metadata only.
- Data and counts use tabular figures.
- Headings use balanced wrapping and no more than three lines.

## 4. Spacing & Layout

### Base unit

All layout spacing derives from 4px.

| Token | Value | Usage |
|---|---:|---|
| `--space-1` | 4px | Icon detail and tight inline gaps |
| `--space-2` | 8px | Compact control groups |
| `--space-3` | 12px | Input and list padding |
| `--space-4` | 16px | Standard panel padding |
| `--space-5` | 20px | Comfortable card padding |
| `--space-6` | 24px | Screen gutters |
| `--space-8` | 32px | Major group separation |
| `--space-10` | 40px | Large empty-state rhythm |
| `--space-12` | 48px | Major section separation |

### Grid

- Conversation content: 768px maximum, centered.
- Tools popover: 400px target width, clamped to the available viewport.
- Sidebar: user-resizable from 200px to 420px; 264px default.
- Compact breakpoint: below 720px, popovers and modals pin to safe screen gutters and dense labels may collapse while retaining accessible names.

### Rules

- Full-height application shells use `100dvh`.
- Composer controls may wrap but never clip or overlap the send action.
- Popovers remain anchored to their trigger and fully visible within the viewport.

## 5. Components

### Desktop title bar

- **Height:** 54px, fixed at the top of the undecorated Tauri window.
- **Surface:** `#111111` with a 1px `#252525` lower hairline; title is left-padded
  35px and controls are quiet 54px hit areas on the right.
- **States:** controls stay neutral at rest, brighten on hover, and use a muted
  blue focus ring; close uses a red hover surface only because it is destructive.
- **Accessibility:** every window action is a real button with an accessible name.

### Models registry view

- **Layout:** a single centered content column, max-width 996px, with 48px top
  padding. The empty state intentionally stays quiet: `Models` heading at 26px
  and a compact `+ Add` action aligned to the right.
- **Surface:** near-black `#101010`; heading uses the cool slate `#8190a3` token
  and the Add control uses `#2a2a2a` with a 4px radius.
- **Interaction:** Add opens a bounded form dialog; Escape returns to the main
  workspace. No decorative cards are shown before the first model exists.

### Product mark

- **Placement:** one small geometric mark sits in the app header beside the wordmark; mobile uses the same mark. Do not scatter large Infinity logos through empty states or mission content.
- **Treatment:** 24px rounded-square container, quiet surface border, accent-colored line mark. The wordmark carries the product identity; the symbol is supporting chrome.
- **Sidebar rhythm:** the primary new-item action is followed by a compact search action, then the workspace and content list. Keep this order stable.

### Agent access picker

- **Placement:** a compact access chip belongs in the chat composer toolbar, near tool and action controls. It opens upward so the composer remains readable.
- **Modes:** Ask for approval (all external/file/screen/action risks), Approve for me (only risky classes), Full access (no per-step prompts for enabled tools), and Custom (owner-configured rules). The selection persists locally and is included in each chat request.
- **Visual treatment:** 32px quiet pill trigger. The popover is a single dense list with icon, label, one-line consequence, and a clear selected state. Full access uses the product accent as an explicit warning—not a permanently loud danger banner.
- **Accessibility:** every option is a real button, exposes its selected state in text, and remains fully keyboard reachable. The mode must change backend gates; a purely decorative safety selector is prohibited.

### Action button

- **Structure:** native `button`, optional SVG icon, label, optional count/status.
- **Variants:** primary amber, secondary tonal, quiet icon, destructive.
- **Spacing:** `--space-2` to `--space-4`.
- **States:** default, hover, pressed, focus-visible, disabled, busy.
- **Accessibility:** native keyboard behavior; visible amber focus ring; icon-only buttons require an accessible label.
- **Motion:** 140ms transform/color; pressed state translates by 1px or scales to 0.98.

### Composer

- **Structure:** one quiet input surface with a single-line footer. The default footer exposes only `+`, access mode, model, and send/stop. Attachments, web, MCP, tools, persona, tone, motion, and export live behind the `+` menu.
- **Variants:** Chat and Assistant.
- **Spacing:** `--space-2`, `--space-3`, `--space-4`.
- **States:** empty, focused, dragging files, sending, steering, queued, error, disabled.
- **Accessibility:** labelled textarea; all controls remain keyboard reachable; state is conveyed by text and `aria-pressed`, not color alone.
- **Motion:** standard surface focus transition; no layout animation while typing.
- **Steer:** while a response is running, typing an instruction reveals a labelled `Steer` action. It stops the active response and immediately promotes that instruction ahead of queued follow-ups.
- **Visual restraint:** no emoji labels, decorative mode chips, persistent voice dock, or second toolbar above the input. Typography uses normal weight by default; emphasis comes from spacing and tone.

### Reference-frame dropzone

- **Structure:** dashed image slot with thumbnail, filename, and explicit remove action; video mode shows paired start/end slots.
- **States:** empty, drag-over, uploading, uploaded, error, and removed.
- **Accessibility:** each slot is a labelled file input with keyboard activation; image previews use decorative alt text and the filename remains visible.
- **Motion:** border tint and thumbnail opacity only; no animated layout shifts while a file uploads.

### Video evidence

- **Structure:** mission attempt row with native controls, bounded aspect-ratio media surface, and provider/error metadata.
- **States:** queued, generating, playable, failed, and missing-frame validation.
- **Accessibility:** video controls are native; failure text explains the next action and never relies on color alone.
- **Motion:** live generation uses the existing status-breathe/progress-sweep tokens; playback itself is user-controlled.

### Build crew picker

- **Structure:** a compact `Crew` action in the composer opens the existing Agent Library in bounded multi-select mode; selected names are surfaced as briefing chips before launch.
- **States:** empty, one-to-three selected, core swarm enabled, core swarm disabled, loading, search-empty.
- **Accessibility:** the trigger has a descriptive accessible name; agent cards expose `aria-pressed` during multi-select; the modal keeps its Escape/outside-close behavior and the footer exposes the core-swarm decision as a labelled checkbox.
- **Motion:** reuse existing 140ms control transitions and the modal's 220ms opacity/translate entry; no layout animation while selections change.

### Tools trigger

- **Structure:** wrench icon, visible `Tools` label, available count, on/off indicator.
- **Variants:** off, on, unavailable/loading.
- **States:** default, hover, pressed, focus-visible, expanded.
- **Accessibility:** `aria-expanded`, `aria-controls`, and a label describing the enabled state.
- **Motion:** 140ms color/transform; popover uses 220ms opacity/translate.

### Tool catalog popover

- **Structure:** header and master switch, short explanation, grouped capability cards, live MCP section, footer link to detailed settings.
- **Variants:** Chat catalog and Assistant catalog.
- **States:** loading, populated, no MCP connections, fetch error, enabled/disabled.
- **Accessibility:** dialog semantics, descriptive heading, Escape closes, outside click closes, focus returns to trigger.
- **Motion:** opacity and translate only; no animated height.

### Tool capability row

- **Structure:** category icon tile, title, plain-language description, availability/count badge.
- **Variants:** compute, web, vision, reasoning, files, creation, MCP.
- **States:** available, action-gated, disconnected, disabled by master switch.
- **Accessibility:** never relies on icon or color alone; risk/action labels are explicit.
- **Motion:** hover tint only when the row is actionable.

### Surface panel

- **Structure:** semantic container with optional header and footer.
- **Variants:** flat, raised, floating.
- **States:** default, hover where interactive, focus-within, error.
- **Accessibility:** landmarks or headings where appropriate; no click handlers on non-interactive containers.
- **Motion:** opacity/transform only for entry and exit.

### Sidebar navigation

- **Structure:** search, compact section label, dense chat rows, then Settings and Models only.
- **Sizing:** search and utility rows use a 30–31px minimum height; chat rows use 32px; labels use 13px type with a 20px line height.
- **Spacing:** adjacent rows use a 1px rhythm inside 12px side gutters. Utility navigation is divided from scrollable chat history by a single hairline.
- **Advanced surfaces:** Long Tasks, Knowledge, Health, Council, Vision Verify, Training, Beast Arena, and Scheduled Tasks stay out of primary navigation and remain available through command search for developer access.
- **Motion:** hover tint and press feedback only; row density never changes during interaction.

### Tab strip

- **Structure:** compact rounded tabs on a quiet rail, with a separate 30px new-tab control.
- **Active state:** neutral raised surface, hairline boundary, and a restrained accent edge instead of a heavy filled block.
- **Close behavior:** visible but muted on the active tab; revealed on hover for inactive tabs.
- **Motion:** the active surface glides between tabs using transform; controls use short scale feedback without animated layout.

### Coding activity rail

- **Structure:** a persistent 44px right activity rail with a collapsible 280px contextual panel.
- **Files:** workspace-backed filterable tree, expandable folders, selected-file state, and copy-path action.
- **Developer access:** the rail stays Files-only. Internal capability settings remain in Settings and command search instead of occupying primary navigation.
- **Responsive:** the drawer collapses below 980px and the rail disappears below 760px so chat never becomes cramped.

### Provider connection cards

- **Structure:** one card per provider with purpose, named API-key field, textual connection state, reveal control, and a per-provider action.
- **Primary flow:** paste a draft key, choose **Save & test**, then receive Connected or Error inline. Testing never checks a stale key when a new draft exists.
- **Security copy:** keys are described truthfully as local, outside the project, and masked after save. The UI never claims plaintext JSON storage is encrypted.
- **Runtime providers:** Qwen / DashScope, DeepSeek, FAL, ElevenLabs, and Novita share the same visual pattern.

### Coding harness

- **Discovery:** bounded workspace glob, literal text search, and multi-file reads complement single-file read/edit tools.
- **Desktop launch:** installed apps launch through a dedicated action tool with approval, direct argument arrays, no shell interpolation, and conventional-path resolution.
- **Capability truth:** every turn receives a live contract generated from its actual registered tools, workspace, and action state; matching tools are attempted before manual instructions.

### Brand light and audio feedback

- **Infinity mark:** an SVG path-length beam traces the mark slowly while idle and accelerates only during active work.
- **Thinking text:** a restrained light sweep crosses Thinking/Working and the sidebar wordmark while the model is active.
- **Sound:** local Web Audio cues cover send, tool, approval, completion, and error states. Filtered envelopes and quiet gain keep them tactile rather than game-like; paid TTS is never used for UI clicks.
- **Interactive label light:** primary navigation, tab, file-tree, suggestion, settings-nav, and composer labels use the shared `light-sweep-control` / `light-sweep-text` primitive. Hover or keyboard focus raises the label to white, adds a restrained bloom, and carries one travelling highlight across the glyphs. The pass never loops while idle and never changes layout.

### Toggle

- **Structure:** text label and hint, native button or checkbox switch.
- **Variants:** standard, compact.
- **States:** off, on, hover, pressed, focus-visible, disabled.
- **Accessibility:** state exposed through `checked` or `aria-pressed`; minimum 36px target in dense desktop UI.
- **Motion:** 140ms transform and color.

## 6. Motion & Interaction

| Type | Duration | Easing | Usage |
|---|---:|---|---|
| Micro | 140ms | `ease-out` | Press, hover, toggle |
| Standard | 220ms | `cubic-bezier(0.22, 1, 0.36, 1)` | Popover and tab transition |
| Emphasis | 420ms | `cubic-bezier(0.16, 1, 0.3, 1)` | Screen or major state entry |
| Light pass | 680ms | `cubic-bezier(0.22, 1, 0.36, 1)` | One-shot white sweep across an interactive text label |

- Animate only `transform`, `opacity`, and short color/filter transitions.
- Motion explains state: a tool popover opens from its trigger; a running tool pulses only while running.
- The empty chat uses one GSAP-scoped entrance sequence: mark, copy, suggestions, then composer. It completes in under 500ms and never loops.
- Composer focus settles upward by 1px; transient menus scale from their trigger edge; the plus rotates into a close affordance while expanded.
- New messages, queued work, and the Steer action use short directional entrances so state changes are legible without adding persistent chrome.
- Continuous motion is limited to active work states. Idle composer and message surfaces remain still.
- Interactive text may bloom on hover/focus and run one 680ms light pass. It must settle to a static high-contrast label instead of looping.
- Respect `prefers-reduced-motion` across every animation.
- Escape closes the topmost transient surface; outside click closes non-blocking popovers.

## 7. Depth & Surface

### Strategy

Mixed tonal shift plus restrained elevation.

| Level | Treatment | Usage |
|---|---|---|
| Flat | tonal separation and 1px hairline | Sidebar, message regions |
| Raised | surface-2, inner highlight, soft tinted shadow | Composer and cards |
| Floating | raised surface, strong hairline, deep charcoal shadow | Tool catalog, command palette, settings |

- Lighting comes from the upper left: top/inner highlights are lighter, shadows fall down and right.
- Glass blur is reserved for modal scrims and transient overlays, never used as the only source of depth.
- Rounded radii: 8px controls, 12px panels, 16px floating surfaces. Pills are limited to compact status and primary round actions.

## 8. Accessibility Constraints & Accepted Debt

### Constraints

- WCAG 2.2 AA target: 4.5:1 for body text, 3:1 for large text and UI boundaries.
- Every interactive control has a visible `:focus-visible` state.
- Core chat, tools, settings, and approval flows are fully keyboard reachable.
- Enabled state, risk, cost, and connectivity are communicated with words as well as color.
- Reduced motion is respected; hover-only information is duplicated through focus or visible text.
- Pointer targets are at least 36px in the dense desktop shell and 44px at compact breakpoints.

### Accepted debt

| Item | Location | Why accepted | Owner / Exit |
|---|---|---|---|
| Existing raw syntax-highlight colors | `src/index.css` Prism token palette | Language tokens require a broader contrast pass and are outside the tools-flow scope | Consolidate during editor/code-view redesign |
| Existing one-off Tailwind sizes | Older screens and settings | The current UI predates this design system; this pass prioritizes the composer and tools journey | Remove incrementally as each screen is redesigned |
| Desktop-first responsive model | Tauri window shell | The shipped product is a desktop application; compact widths are still protected from clipping | Revisit if a web/mobile distribution is added |
