# DESIGN.md — AppUAT Design System

> Premium dark-mode design system for a QA/UAT testing platform.
> Stack: Next.js 14, React 18, Tailwind CSS v3, TypeScript.

---

## 1. Design Parameters

| Parameter | Value | Rationale |
|---|---|---|
| DESIGN_VARIANCE | 6 | Professional offset layouts — not generic, not chaotic |
| MOTION_INTENSITY | 5 | Fluid CSS transitions, no heavy animation libraries needed |
| VISUAL_DENSITY | 5 | Daily-app density — balanced spacing for a tool used daily |

---

## 2. Color System

### Base Palette (Zinc-based, warm-neutral dark)

| Token | Value | Usage |
|---|---|---|
| `--bg` | `#09090b` (zinc-950) | Page background |
| `--surface-1` | `#18181b` (zinc-900) | Cards, panels |
| `--surface-2` | `#27272a` (zinc-800) | Elevated surfaces, inputs |
| `--border` | `#3f3f46` (zinc-700) | Default borders |
| `--border-subtle` | `#27272a` (zinc-800) | Subtle dividers |
| `--text` | `#fafafa` (zinc-50) | Primary text |
| `--text-secondary` | `#a1a1aa` (zinc-400) | Secondary text |
| `--text-tertiary` | `#71717a` (zinc-500) | Tertiary/muted text |

### Accent: Emerald (single accent, desaturated)

| Token | Value | Usage |
|---|---|---|
| `--accent` | `#10b981` (emerald-500) | Primary actions, links, active states |
| `--accent-hover` | `#059669` (emerald-600) | Hover state |
| `--accent-subtle` | `rgba(16,185,129,0.1)` | Accent backgrounds |

### Semantic Colors

| Token | Value | Usage |
|---|---|---|
| `--success` | `#22c55e` (green-500) | Test passed |
| `--warning` | `#eab308` (yellow-500) | Warnings, pending |
| `--error` | `#ef4444` (red-500) | Test failed, errors |
| `--info` | `#06b6d4` (cyan-500) | Running, info states |

> **Rule:** No purple, no indigo, no neon glows. Emerald is the only accent.

---

## 3. Typography

### Font Stack

```css
--font-sans: 'Geist', system-ui, -apple-system, sans-serif;
--font-mono: 'Geist Mono', 'JetBrains Mono', 'SF Mono', monospace;
```

Load via `next/font/google` or `next/font/local`.

### Type Scale

| Role | Classes | Usage |
|---|---|---|
| Page title | `text-2xl font-semibold tracking-tight` | Page headers |
| Section title | `text-lg font-medium` | Section headers |
| Card title | `text-base font-medium` | Card headers |
| Body | `text-sm text-zinc-300 leading-relaxed` | Paragraphs |
| Caption | `text-xs text-zinc-500` | Timestamps, metadata |
| Code/mono | `text-xs font-mono text-zinc-400` | Package names, IDs, technical data |
| Label | `text-xs font-medium uppercase tracking-wider text-zinc-500` | Form labels, section labels |

> **Rule:** No `text-3xl` or larger. Control hierarchy with weight and color, not massive scale.

---

## 4. Spacing & Layout

### Container
```
max-w-6xl mx-auto px-6
```

### Section Spacing
- Between page sections: `space-y-8`
- Between cards in a group: `gap-4`
- Card internal padding: `p-5` (default), `p-6` (large cards)

### Layout Patterns

**Project list (home page):** Use a 2-column asymmetric grid, not 3-column equal cards.
```
grid grid-cols-1 md:grid-cols-2 gap-4
```

**Detail pages:** Left-aligned content with generous right margin.
```
max-w-4xl
```

**Split layouts:** 60/40 or 70/30 for content + sidebar patterns.
```
grid grid-cols-1 lg:grid-cols-[1fr_340px] gap-6
```

---

## 5. Component Patterns

### Cards
```
bg-zinc-900 border border-zinc-800 rounded-xl p-5
hover:border-zinc-700 transition-colors duration-200
```
- Use `rounded-xl` (12px) for all cards
- No box-shadows by default; use border hierarchy for depth
- Hover: border lightens one step

### Buttons

**Primary:**
```
bg-emerald-600 hover:bg-emerald-500 text-white
px-4 py-2 rounded-lg font-medium text-sm
transition-colors duration-150
active:scale-[0.98] active:translate-y-[1px]
```

**Secondary:**
```
bg-zinc-800 hover:bg-zinc-700 text-zinc-300
border border-zinc-700 hover:border-zinc-600
px-4 py-2 rounded-lg font-medium text-sm
transition-colors duration-150
```

**Ghost:**
```
text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800/50
px-3 py-1.5 rounded-md text-sm
transition-colors duration-150
```

### Inputs
```
bg-zinc-900 border border-zinc-700 rounded-lg
px-4 py-2.5 text-sm text-white
placeholder:text-zinc-600
focus:outline-none focus:border-emerald-500/50 focus:ring-1 focus:ring-emerald-500/20
transition-colors duration-150
```
- Label above input, `gap-2` between label and input
- Error text below input in `text-red-400 text-xs`

### Status Badges
```
inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium
```

| Status | Colors |
|---|---|
| Passed | `bg-green-500/10 text-green-400 border border-green-500/20` |
| Failed | `bg-red-500/10 text-red-400 border border-red-500/20` |
| Running | `bg-cyan-500/10 text-cyan-400 border border-cyan-500/20` |
| Pending | `bg-zinc-500/10 text-zinc-400 border border-zinc-500/20` |

### Empty States
```
border border-dashed border-zinc-800 rounded-xl p-12 text-center
```
- Icon or illustration (SVG, not emoji)
- Title: `text-zinc-400 text-sm`
- CTA link: `text-emerald-400 hover:text-emerald-300 text-sm font-medium`

### Loading States
Use skeleton loaders matching layout dimensions:
```
bg-zinc-800 rounded-md animate-pulse
```
- Match the height/width of the content being loaded
- No circular spinners

---

## 6. Motion & Transitions

### Default Transition
```
transition-colors duration-200
```

### Interactive Elements
```
transition-all duration-200 ease-out
```

### Hover Effects
- Cards: border color shift (`border-zinc-800` -> `border-zinc-700`)
- Buttons: background shift + subtle press on active (`active:scale-[0.98]`)
- Links: color shift only

### Page Load
- Content fades in with CSS: `animate-in fade-in duration-300`
- Stagger card entries using `animation-delay` via inline styles:
  ```css
  animation: fadeInUp 0.4s ease-out both;
  animation-delay: calc(var(--index) * 80ms);
  ```

```css
@keyframes fadeInUp {
  from { opacity: 0; transform: translateY(8px); }
  to { opacity: 1; transform: translateY(0); }
}
```

---

## 7. Navigation / Header

### Structure
```
border-b border-zinc-800/80 bg-zinc-950/80 backdrop-blur-sm
sticky top-0 z-30
```

### Logo
- Use an SVG icon or styled text, not emoji
- Brand: `text-lg font-semibold tracking-tight`
- Version badge: `text-[10px] text-zinc-600 bg-zinc-800 px-1.5 py-0.5 rounded`

---

## 8. Forbidden Patterns

- No emoji anywhere in the UI (use Phosphor icons or SVG)
- No `#000000` pure black
- No purple/indigo accents
- No neon outer glows or `box-shadow` glows
- No 3-column equal card grids
- No Inter font
- No `h-screen` (use `min-h-[100dvh]` if needed)
- No generic circular spinners
- No gradient text on headers
- No oversaturated accent colors

---

## 9. Icon System

Use `@phosphor-icons/react` with consistent `weight="regular"` and `size={18}`.

Install: `npm install @phosphor-icons/react`

Common mappings:
- Navigation: `Compass` (logo), `ArrowLeft` (back)
- Projects: `Folder`, `FolderOpen`
- Tests: `CheckCircle` (pass), `XCircle` (fail), `Clock` (pending), `Spinner` (running)
- Actions: `Plus`, `Upload`, `Play`, `Trash`
- Status: `CheckCircle`, `Warning`, `XCircle`, `Info`
