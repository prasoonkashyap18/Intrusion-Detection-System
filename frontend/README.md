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
| Backend calls | Native `fetch` behind a typed service layer (no HTTP library) |
| Linting | oxlint (React, TypeScript and `jsx-a11y` accessibility rules) |
| Testing | Vitest + jsdom + React Testing Library |

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
| `npm test` | Run the Vitest suite once |
| `npm run test:watch` | Run Vitest in watch mode |
| `npm run preview` | Serve the production build locally (port 4173 — add `http://localhost:4173` to the backend's `CORS_ORIGINS` if the preview needs to reach the API) |

## Environment Configuration

Copy `.env.example` to `.env` (git-ignored) to override defaults:

| Variable | Default | Purpose |
|---|---|---|
| `VITE_API_BASE_URL` | `http://localhost:8000` | Base URL of the FastAPI backend |

**Every `VITE_*` variable is embedded in the JavaScript bundle and is visible to anyone using the app.** Never put API secrets, passwords, tokens, or database credentials in frontend environment variables.

The base URL is read once, in `src/services/config.ts`. Nothing else in the app hard-codes a backend URL, so pointing the frontend at another backend is a single `.env` change.

The backend must allow this app's origin. Its default `CORS_ORIGINS` already contains `http://localhost:5173`, which is why the dev server pins that port (`strictPort`).

## API Service Layer

All backend communication goes through `src/services/`, so no React component calls `fetch` directly:

```
UI component → hook (useApiHealth) → service (getHealth) → client (request) → FastAPI
```

| File | Responsibility |
|---|---|
| `services/config.ts` | Base URL from `VITE_API_BASE_URL`, `/api/v1` prefix, default timeout |
| `services/api.ts` | `request()` / `getJson()` — URL resolution, methods, JSON encoding, timeout, validation |
| `services/apiError.ts` | `ApiError` and its `code` union |
| `services/health.ts` | `getHealth()` for `GET /api/v1/health` |
| `services/detection.ts` | `uploadDetectionCsv()` for `POST /api/v1/detection/upload` |
| `services/index.ts` | Barrel re-export — import from `@/services` or `../services` |

`request()` supports GET/POST/PUT/PATCH/DELETE and JSON or `FormData` bodies, so a new endpoint adds a service module rather than new transport code (`services/detection.ts` is the CSV upload). Every response is narrowed by a type guard, so callers never receive unchecked data. All endpoints return JSON today; a no-content (204) endpoint would need explicit handling.

Two backend endpoints exist — `GET /api/v1/health` and `POST /api/v1/detection/upload` — and those are the only services implemented. No other endpoint is stubbed or faked.

### Error handling

Every failure leaves the service layer as an `ApiError` with a `code`, so raw `fetch`/DOM exceptions never reach the UI:

| `code` | Meaning |
|---|---|
| `http_error` | Backend responded with a non-2xx status (`status` is set) |
| `network_error` | Backend unreachable — refused connection, DNS, CORS, offline |
| `timeout` | No response within the timeout (5 s for health, 8 s default) |
| `invalid_response` | Body was not JSON, or did not match the expected shape |
| `aborted` | Caller cancelled, e.g. React effect cleanup — not a backend fault |

`isUnreachable` groups `network_error` and `timeout`. A cancelled request never changes the displayed status.

### Health check in the UI

`useApiHealth` owns the lifecycle and feeds the header badge and the Platform Status panel. It reports **Connecting** until the first response, then only what the backend actually returns — **Online** (`status: "healthy"`), **Degraded** (responded, different status), or **Offline** (unreachable or an error status). Nothing assumes a healthy backend.

It re-checks every 30 s, pauses in background tabs, re-checks on tab focus, and the panel's **Re-check** button forces one. A 30-second interval keeps a backend that went down from showing stale state without polling aggressively.

## CSV Upload

The Command Center's **Traffic ingestion** panel uploads a network-flow CSV to `POST /api/v1/detection/upload`, which registers it as a **pending detection batch**. **The traffic is not analyzed** — no model exists yet — and the UI says so everywhere it shows a batch.

```
CsvUpload (component) → useCsvUpload (hook) → uploadDetectionCsv (service) → request() → FastAPI
```

- **Select or drop:** a "Choose file" button (keyboard- and screen-reader-reachable) and a drop zone scoped to the upload panel only. Drag feedback is a subtle cyan border.
- **Client checks** (fast feedback only, never security): `.csv` extension, non-empty, at most 50 MB (`MAX_UPLOAD_BYTES`, mirroring the backend default). The backend validates everything again.
- **Progress:** `fetch` cannot report upload progress, so the UI shows a truthful indeterminate "Uploading…" — never a percentage. The buttons are disabled and the hook ignores repeat submissions while a request is in flight.
- **Result:** on success, the batch ID, filename, record count and `pending` status exactly as the backend returned them. On failure, the backend's own user-facing message (via `ApiError.detail`) or a plain-language fallback; **Retry** is offered only when resending could help (network errors, timeouts, 5xx), not for a file the server rejected.
- **After an upload,** the batch list re-fetches page 1 from the backend, so the list never has a second, competing source of truth.

## Detection Batches

The **Detection batches** panel shows batches persisted in SQLite via `GET /api/v1/detection/batches`, so a browser refresh or restart never loses them.

```
DetectionBatchesPanel → useDetectionBatches (hook) → getDetectionBatches (service) → request() → FastAPI → SQLite
```

- **Rows:** filename (truncated, full name in a tooltip), status in words (`Pending · registered, waiting for processing` — pending never reads as "analyzing"), record / processed / failed counts, a local-time stamp (`03 Oct 2026, 02:45 PM`, in a semantic `<time>` with the exact UTC instant as its tooltip), and the batch ID.
- **States:** loading, list, empty (the backend answered: zero batches), error (the backend answered with a failure), and offline (it could not be reached) are distinct, using the shared state components.
- **Refresh:** a manual button (no polling). The existing list stays visible while it runs, the button is disabled to prevent duplicate requests, and a failed refresh keeps the old list with an inline "Could not refresh" notice and Retry.
- **Pagination:** 10 per page with Previous / Next, shown only when there is more than one page. If the current page disappears (batches removed elsewhere), the hook falls back to the last page.

## UI State Components

`src/components/states/` holds the reusable states every data-backed view needs. They are presentational: an optional `onRetry` callback is wired by the caller, so the components never know how the API works.

| Component | Use |
|---|---|
| `LoadingState` | A request is in flight. Polite live region; static indicator under reduced motion |
| `ErrorState` | A request failed. Takes a clean `message` — never raw exception text |
| `EmptyState` | A request succeeded and genuinely returned nothing |
| `OfflineState` | The backend is unreachable, so nothing could be asked |

`EmptyState` and `OfflineState` are deliberately separate: *"there is nothing"* and *"we could not ask"* are different claims, and only the first says anything about the data. Each takes `compact` for tight spaces, and failures carry `role="alert"` with a text title so meaning never depends on colour.

There is no success state. A successful request renders the real content, and no toast system exists because nothing needs one yet.

## Testing

Vitest with jsdom and React Testing Library. Tests live in `frontend/tests/` — see [`tests/frontend/README.md`](../tests/frontend/README.md) for why they sit inside this package rather than beside the Python suites.

```bash
npm test
```

`fetch` is stubbed in every test; no test contacts a real backend, and no fake detection data exists anywhere in the application.

## Project Structure

```
src/
├── components/
│   ├── brand/          # Logo mark
│   ├── navigation/     # Sidebar + navigation config
│   ├── states/         # Loading / Error / Empty / Offline states
│   ├── system/         # API status badge, UTC clock
│   ├── topology/       # NetworkTopology3D, its Canvas engine, preview geometry, TopologyViewport panel
│   └── ui/             # Design-system primitives (Panel, Button, StatusDot, SectionHeading, EmptyState, NoData)
├── hooks/              # useApiHealth, useMediaQuery, useSurfaceInteraction, useMagnetic, useNow
├── layouts/            # AppShell, TopBar, MobileNavDrawer
├── pages/
│   └── command-center/ # Command Center page and its panels
├── services/           # API client, error type, config, health service, barrel
├── types/              # Frontend types (API health, severity levels, topology graph)
├── utils/              # cn, color, formatting, shared pointer tracker
├── index.css           # Tailwind entry + design tokens
└── main.tsx            # Application entry

tests/                  # Vitest suites for the service layer and health hook
```

`@/` is an alias for `src/`, available in both application and test code.

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
- A typed API service layer ready for the detection endpoints, with structured errors and request timeouts (see [API Service Layer](#api-service-layer)).

## Current Limitations

- Uploaded CSVs are registered but not analyzed. Detection results, analytics and model-performance views do not exist yet; those navigation sections are marked **Planned**.
- Batches are listed but not individually viewable (no single-batch endpoint or detail view yet), and the list does not poll — use Refresh.
- Upload progress is indeterminate, because `fetch` offers no upload progress events.
- Telemetry and pipeline panels are structural placeholders — they show no data because no analysis has run.
- The 3D view renders preview geometry only; real hosts, flows, severity tints and entity details arrive with the detection API. A data-table alternative to the visualization should accompany real data for accessibility.
- No routing library yet (there is only one page).
- Tests cover the service layer, the hooks, the state and upload components, and the Command Center upload flow; the topology renderer is not covered.
- `ErrorState` is used by the upload flow for failed uploads.
- Backdrop click-to-close on the mobile drawer uses the native `closedby` dialog attribute; in browsers without support, Escape and the close button still work.
