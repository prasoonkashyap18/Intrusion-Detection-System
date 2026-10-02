# AI-IDS Frontend

The browser application for the AI-Based Network Intrusion Detection & Security Operations Platform. This is the **frontend foundation**: application shell, design system, interaction primitives, and an initial Command Center page. Detection features are not implemented yet — see [Current Limitations](#current-limitations).

## Stack

| Concern | Technology |
|---|---|
| UI | React 19 + TypeScript 6 (strict) |
| Build / dev server | Vite 8 |
| Styling | Tailwind CSS 4 (CSS-first `@theme` design tokens) |
| Icons | lucide-react |
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
│   ├── interaction/    # CursorLight (ambient cursor-following light)
│   ├── navigation/     # Sidebar + navigation config
│   ├── system/         # API status badge, UTC clock
│   ├── topology/       # TopologyViewport — mount point for the future 3D topology
│   └── ui/             # Design-system primitives (Panel, StatusDot, SectionHeading, EmptyState, NoData)
├── hooks/              # useApiHealth, useMediaQuery, useSurfaceInteraction, usePointerParallax, useNow
├── layouts/            # AppShell, TopBar, MobileNavDrawer
├── pages/
│   └── command-center/ # Command Center page and its panels
├── services/           # API base config, typed fetch client, health service
├── types/              # Frontend types (API health, severity levels)
├── utils/              # cn, formatting, shared pointer tracker
├── index.css           # Tailwind entry + design tokens
└── main.tsx            # Application entry
```

## Design System

Tokens live in `src/index.css` (`@theme static`). Tailwind's default palette is cleared, so only named, semantic colors are available:

- **Surfaces** — `obsidian-*`: layered graphite, never pure black.
- **Text** — `ink-*`: `ink-500` and lighter meet WCAG AA contrast on panels.
- **Brand** — `accent` (electric cyan) and `ice` (ice blue).
- **Security semantics** — `sev-normal`, `sev-low` (green), `sev-medium` (amber), `sev-high` (orange), `sev-critical` (red). These are reserved for traffic classification and severity; connectivity/system states never use them.
- **Type** — Geist for UI, Geist Mono with tabular numerals for telemetry; added `text-micro`, `text-display`, `text-telemetry` sizes.
- **Surfaces** — `.panel` (restrained glass), with optional `spotlight` (cursor-tracking border light) and `tilt` (subtle 3D response) interactions via the `Panel` component.

## Interaction Architecture

- **One shared pointer listener** (`utils/pointerTracker.ts`), coalesced to one update per animation frame.
- Cursor-driven effects **write CSS custom properties or transforms directly to the DOM** — pointer movement never triggers React re-renders.
- Smoothing comes from CSS transitions on `transform`/`opacity` (compositor-friendly), not JavaScript animation loops.
- All cursor effects are disabled for touch/coarse pointers, and motion is disabled when the user prefers reduced motion.

## 3D Foundation

`TopologyViewport` renders a dependency-free CSS 3D perspective stage (grid plane, orbit rings, beacon) with subtle cursor parallax. It accepts `children`, so a future WebGL `NetworkTopology3D` component can be mounted inside the same frame — ideally lazy-loaded with `React.lazy` so a 3D library never weighs down the initial bundle. No 3D library is installed yet.

## Current Functionality

- Application shell: responsive sidebar (full on desktop, icon rail on tablet, modal drawer on mobile), sticky header with breadcrumb, skip link, landmarks, and keyboard focus styles.
- Live backend connectivity: `GET /api/v1/health` is polled every 30 seconds; the header badge and Platform Status panel show the real result (online / degraded / offline), measured round-trip time, and last check time in UTC.
- Command Center page with honest empty states: no statistics are displayed until real detection data exists.

## Current Limitations

- No CSV upload, detection results, analytics, or model-performance views exist yet; those navigation entries are marked **Planned**.
- All telemetry, topology and batch panels are structural placeholders — they show no data because none exists.
- The 3D topology is a visual standby stage only; it does not render any network data.
- No routing library yet (there is only one page).
- No automated frontend tests yet.
- Backdrop click-to-close on the mobile drawer uses the native `closedby` dialog attribute; in browsers without support, Escape and the close button still work.
