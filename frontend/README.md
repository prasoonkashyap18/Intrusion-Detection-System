# AI-IDS Frontend

The browser application for the AI-Based Network Intrusion Detection & Security Operations Platform. This is the **frontend foundation**: a premium light design system, application shell, interaction primitives, a 3D topology foundation, and an initial Command Center page. Detection features are not implemented yet — see [Current Limitations](#current-limitations).

## Stack

| Concern | Technology |
|---|---|
| UI | React 19 + TypeScript 6 (strict) |
| Build / dev server | Vite 8 |
| Styling | Tailwind CSS 4 (CSS-first `@theme` design tokens) |
| Icons | lucide-react |
| 3D topology | Custom Canvas 2D renderer (no 3D library dependency) |
| Typography | Geist / Geist Mono (self-hosted via Fontsource — no third-party font requests) |
| Linting | oxlint (React, TypeScript and `jsx-a11y` accessibility rules) |

## Prerequisites

- Node.js `^20.19.0` or `>=22.12.0` (required by Vite 8)
- npm
- Optional: the FastAPI backend running locally (see [../backend/README.md](../backend/README.md)). The frontend works without it and simply reports the API as offline.

## Installation

From the `frontend/` directory:

```bash
npm install
```

## Development

```bash
npm run dev
```

Open **http://localhost:5173**. The port is fixed (`strictPort`) because it is the origin the backend allows by default in its CORS configuration; if 5173 is busy, Vite exits instead of silently switching to a port the API would reject.

## Scripts

| Command | Purpose |
|---|---|
| `npm run dev` | Start the Vite development server |
| `npm run build` | Type-check (`tsc -b`) and build for production into `dist/` |
| `npm run typecheck` | Type-check only |
| `npm run lint` | Lint with oxlint (warnings fail the run) |
| `npm run preview` | Serve the production build locally (port 4173 — add `http://localhost:4173` to the backend's `CORS_ORIGINS` if the preview needs to reach the API) |

## Environment Configuration

Copy `.env.example` to `.env` (git-ignored) to override defaults:

| Variable | Default | Purpose |
|---|---|---|
| `VITE_API_BASE_URL` | `http://localhost:8000` | Base URL of the FastAPI backend |

**Every `VITE_*` variable is embedded in the JavaScript bundle and is visible to anyone using the app.** Never put API secrets, passwords, tokens, or database credentials in frontend environment variables.

## Project Structure

```
src/
├── components/
│   ├── brand/          # Logo mark
│   ├── navigation/     # Sidebar + navigation config
│   ├── system/         # API status badge, UTC clock
│   ├── topology/       # NetworkTopology3D, its Canvas engine, preview geometry, TopologyViewport panel
│   └── ui/             # Design-system primitives (Panel, Button, StatusDot, SectionHeading, EmptyState, NoData)
├── hooks/              # useApiHealth, useMediaQuery, useSurfaceInteraction, useMagnetic, useNow
├── layouts/            # AppShell, TopBar, MobileNavDrawer
├── pages/
│   └── command-center/ # Command Center page and its panels
├── services/           # API base config, typed fetch client, health service
├── types/              # Frontend types (API health, severity levels, topology graph)
├── utils/              # cn, color, formatting, shared pointer tracker
├── index.css           # Tailwind entry + design tokens
└── main.tsx            # Application entry
```

## Design System Direction

A **premium light** security command center: calm, precise and spatial. Premium comes from typography, spacing, hierarchy, materials and restrained interaction — not from glow, gradients or constant motion.

Tokens live in `src/index.css` (`@theme static`). Tailwind's default palette is cleared, so only named, semantic colors exist:

- **Canvas & surfaces** — `canvas` is a sophisticated off-white (never a flat pure-white page), `sidebar` a slightly lighter off-white, `surface` clean white for cards. Elevation comes from hairline graphite borders and soft graphite-tinted shadows (`shadow-control`, `shadow-surface`, `shadow-raised`).
- **Graphite** — `graphite-50…950` for text, hairlines and 3D node material. `graphite-500` and darker meet WCAG AA for small text.
- **Electric cyan** — `accent-*`: `500` for indicators, lines and 3D connections; `600` for icons/UI on white; `700` for text on white.
- **Ice blue** — `ice-*`: secondary accent and atmospheric depth.
- **Security semantics** — `sev-normal`, `sev-low` (green), `sev-medium` (amber), `sev-high` (orange), `sev-critical` (red). Reserved for real traffic classification and severity; red is never a decorative accent, and connectivity states never use these colors.
- **Type** — Geist for the interface, Geist Mono with tabular numerals for technical labels and telemetry; uppercase tracking is limited to small technical labels. Added `text-micro`, `text-display` and `text-metric` sizes.
- **Atmosphere** — barely-there radial light, ice-blue depth and a faint technical grid behind the shell.

## Interaction Architecture

- **One shared pointer listener** (`utils/pointerTracker.ts`), coalesced to one update per animation frame.
- Cursor effects **write CSS custom properties or transforms directly to the DOM**, so pointer movement never re-renders React.
- `Panel` interactions: `spotlight` (cursor-tracking border light) and `tilt` (subtle tilt, 2px lift, soft sheen) via `useSurfaceInteraction`.
- **Magnetic controls** (`useMagnetic`, `Button magnetic`): selected controls drift up to 3px toward the cursor.
- Smoothing uses short CSS transitions on `transform`/`translate`/`opacity`; effects are disabled for touch input, and motion is disabled for users who prefer reduced motion.

## 3D Foundation

`NetworkTopology3D` renders a 3D network on a **dependency-free Canvas 2D engine** (`components/topology/topologyEngine.ts`): perspective projection, graphite sphere materials with a restrained cyan rim, thin translucent cyan connections, soft ground shadows with hairline stems, depth-faded atmosphere and a damped camera.

- **Interaction:** subtle cursor parallax (only while the view is on screen), hover (node enlarges, its connections brighten, neighbors respond, tooltip appears), drag to rotate with momentum, Ctrl/⌘ + scroll to zoom (plain scroll zooms only after pressing inside the view, so page scrolling is never hijacked), click to focus a node, and a double-click hook (`onNodeOpen`) for future entity details. A labeled toolbar offers rotate, zoom and reset for keyboard and assistive-technology users.
- **Performance:** frames render only while something is changing, then the loop stops; the backing store is capped at 2× device pixel ratio.
- **Semantics:** nodes take a severity tint only when they carry a real detection `level`.
- **Honesty:** until detection data exists, the view shows clearly labeled **preview geometry** — deterministic, unlabeled, uncolored and static. It does not represent any network, host or traffic.
- **Extensibility:** the engine is framework-agnostic, so a WebGL renderer can replace it behind the same component if graphs grow large.

## Current Functionality

- Application shell: responsive sidebar (full on desktop, icon rail on tablet, modal drawer on mobile), sticky header with breadcrumb, skip link, landmarks and keyboard focus styles.
- Live backend connectivity: `GET /api/v1/health` is polled every 30 seconds (paused in background tabs). The header and Platform Status panel show the real result (Online / Connecting / Degraded / Offline), the measured round-trip time and the last check time in UTC.
- Command Center: technical label → title → summary → 3D topology preview → security telemetry → pipeline, platform status, batches and severity scale — all with honest empty states.

## Current Limitations

- No CSV upload, detection results, analytics or model-performance views exist yet; those navigation sections are marked **Planned**.
- Telemetry, batch and pipeline panels are structural placeholders — they show no data because none exists.
- The 3D view renders preview geometry only; real hosts, flows, severity tints and entity details arrive with the detection API. A data-table alternative to the visualization should accompany real data for accessibility.
- No routing library yet (there is only one page).
- No automated frontend tests yet.
- Backdrop click-to-close on the mobile drawer uses the native `closedby` dialog attribute; in browsers without support, Escape and the close button still work.
