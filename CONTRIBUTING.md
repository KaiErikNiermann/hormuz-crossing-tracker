# Contributing

Thanks for your interest in contributing to the Strait of Hormuz Maritime Passage Tracker.

## Development Setup

### Requirements

- Python 3.12+
- [Poetry](https://python-poetry.org/) for Python dependency management
- [pnpm](https://pnpm.io/) for frontend dependencies
- Docker & Docker Compose for running PostgreSQL and workers
- API keys from [AISStream](https://aisstream.io/) and [Global Fishing Watch](https://globalfishingwatch.org/our-apis/)

### Getting Started

```bash
# Clone the repository
git clone https://github.com/yourusername/strait-of-hormuz.git
cd strait-of-hormuz

# Install Python dependencies
poetry install

# Install frontend dependencies
cd site && pnpm install && cd ..

# Copy environment template and fill in your API keys
cp .env.example .env

# Start the database
docker compose up -d postgres

# Build the frontend
cd site && pnpm run build
```

### Development Workflow

**Frontend** (TypeScript):

```bash
cd site
pnpm run watch     # rebuild on file changes
python3 -m http.server 8090   # serve in another terminal
```

The site loads at [http://localhost:8090](http://localhost:8090). The frontend source is in `site/src/app.ts` and builds to `site/dist/app.js`.

**Backend** (Python):

```bash
# Run a one-off GFW data fetch
poetry run fetch-gfw

# Run the export/merge pipeline
HORMUZ_DB_HOST=localhost HORMUZ_DB_PORT=5433 poetry run export-snapshot

# Start the AIS tracker directly (requires running PostgreSQL)
poetry run tracker
```

### Type Checking

The frontend uses strict TypeScript. Always check before committing:

```bash
cd site
pnpm run typecheck
```

## Project Layout

| Directory | Language | Purpose |
|-----------|----------|---------|
| `worker/` | Python | Persistent workers: AIS tracker, GFW fetcher, supervisor |
| `scripts/` | Python | Data pipeline scripts: fetch, merge, export |
| `site/` | TypeScript | Static frontend: MapLibre GL map, chart, UI |

## Code Style

### Python

- Target Python 3.12+
- Use type hints everywhere
- Prefer list comprehensions over `map()`/`filter()` with lambdas
- Use `from __future__ import annotations` for forward references
- Use built-in `sum()`, `max()`, `min()` — never `reduce()` with lambdas

### TypeScript

- Strict mode (`noEmit` type checking via `tsc`)
- Bundled with esbuild (IIFE format, MapLibre GL as external CDN dep)
- No unused variables — the build will fail
- Observable Plot is loaded lazily from CDN, not bundled

### CSS

- CSS custom properties for theming (`var(--fg)`, `var(--sidebar-bg)`, etc.)
- `[data-theme="dark"]` selector for dark mode overrides
- IBM Plex Sans as the primary font

## Making Changes

1. **Create a branch** from `main`
2. **Make your changes** — keep commits focused and atomic
3. **Test locally** — verify the frontend builds (`pnpm run build`), the type checker passes (`pnpm run typecheck`), and the site renders correctly
4. **Submit a pull request** with a clear description of what and why

### Areas That Could Use Help

- **GitHub Actions CI** — automated builds and deployment
- **Historical crossing data** — integration with ONS/IMO transit records
- **Worker heartbeat** — `heartbeat.json` writer for LIVE badge on the frontend
- **Mobile responsiveness** — the sidebar and chart panel need responsive layouts
- **Tests** — unit tests for crossing detection logic and data merge/dedup

## Data & API Notes

- **GFW data** is fetched from the Global Fishing Watch 4Wings API. It has a ~5 day lag from the current date. The periodic fetcher runs every 5 days.
- **AIS data** comes from AISStream's WebSocket API in real-time. The tracker persists vessel state and crossing events to PostgreSQL.
- The **merge pipeline** (`export_snapshot.py`) deduplicates vessels by MMSI — if a vessel appears in both GFW and AIS, it's tagged as `"both"` and the GFW vessel ID is used as the canonical identifier.
- **Position tuples** in `vessels_timeline.json` are 8 elements: `[vesselId, lat, lon, bearing, direction, transit, zone, source]`.

## Questions?

Open an issue on GitHub.
