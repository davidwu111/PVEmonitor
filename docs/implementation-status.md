# PVEmonitor Implementation Status

Last updated: 2026-06-06

## Revision 2026-08-10 — In-memory telemetry

- [x] All telemetry moved into a RAM-resident SQLite store (`storage.py`)
- [x] Configurable memory cap (`storage.memory_limit_mb`, default 256 MB) with
      oldest-first raw-sample eviction (newest sample always kept)
- [x] Periodic full snapshots (configurable interval + shutdown) with startup
      load and snapshot retention (`snapshot_keep`)
- [x] One-time read-only import of the legacy `runtime/db/metrics.sqlite3`
- [x] Single `pvemonitor serve` process runs collection loop + API; old
      collector timer / API / maintenance units replaced by `pvemonitor.service`
- [x] CLI reports and health read the newest snapshot (live API fallback for health)
- [x] Storage config exposed via `pvemonitor config` and `/api/health`

## Phase 1 — Complete ✓

### Core Infrastructure
- [x] Project skeleton (pyproject.toml, requirements, .gitignore, README)
- [x] Virtual environment setup script (bootstrap-venv.sh)
- [x] Configuration system (config.py + monitor.yaml + thresholds.yaml)
- [x] Logging with RotatingFileHandler (10 MB, 5 backups)

### Database Layer
- [x] SQLite schema with 4 migration files (versioned, append-only)
- [x] Tables: samples, host_metrics, guests, guest_samples, collector_errors
- [x] Views: latest_sample, current_guest_status, host_metrics_ts
- [x] All FOREIGN KEYs with ON DELETE CASCADE
- [x] PRAGMAs: WAL, synchronous=NORMAL, foreign_keys=ON, temp_store=MEMORY, busy_timeout=5000
- [x] Migration tracking via schema_version table
- [x] Periodic WAL checkpoint (every 60 collection runs)

### Data Collection
- [x] Host metrics: CPU, load, memory, swap, rootfs, PSI pressure
- [x] CPU temperature (sensors command with sysfs fallback)
- [x] Top CPU process attribution
- [x] GPU metrics: rocm-smi primary, sysfs fallback (busy% + temp)
- [x] Unsupported rocm-smi fields → NULL
- [x] Guest inventory from pvesh /cluster/resources
- [x] Per-guest detail from pvesh status/current
- [x] Running/stopped/paused/unknown state handling
- [x] NULL runtime fields for non-running guests
- [x] LXC pressure metrics
- [x] QEMU guest-agent fields (freemem, balloon, agent status)
- [x] Subprocess timeouts on all external calls (2-10s)
- [x] Partial failure handling (failed guest doesn't block others)

### Rate Calculation
- [x] Delta computation from previous DB rows (no state file)
- [x] Counter reset detection (current < previous → NULL)
- [x] Maximum interval guard (default 120s)
- [x] Physical sanity ceiling for disk/network rates
- [x] Clock skew handling (negative delta → NULL)

### Locking
- [x] Atomic lock file acquisition (O_CREAT | O_EXCL)
- [x] PID + timestamp written into lock file
- [x] Stale lock detection (dead PID or age > 5× interval)
- [x] --force flag for manual override
- [x] PID defense on release (only remove own lock)
- [x] Systemd timer uses OnUnitActiveSec (fires after service finishes)

### CLI
- [x] `pvemonitor collect` — one full collection run
- [x] `pvemonitor collect --force` — skip stale lock
- [x] `pvemonitor init-db` — create/upgrade schema
- [x] `pvemonitor serve` — start FastAPI server
- [x] `pvemonitor report-latest` — host + guest summary
- [x] `pvemonitor report-gpu` — recent GPU trend
- [x] `pvemonitor health` — health check (exit 0/1/2, --json)
- [x] `pvemonitor config` — print resolved config

### REST API
- [x] GET / → redirect to /dashboard
- [x] GET /dashboard → dashboard.html
- [x] GET /api/health — status, latest sample age, total samples, DB size
- [x] GET /api/host/latest — most recent host metrics
- [x] GET /api/host/range?from=&to=&fields= — time-series
- [x] GET /api/host/summary?window_s=3600 — min/max/avg
- [x] GET /api/guests — current guest status
- [x] GET /api/guests/<vmid>/range?from=&to= — guest time-series
- [x] GET /api/guests/<vmid>/transitions — up/down history
- [x] Optional shared-secret token auth
- [x] CORS headers for LAN access

### Dashboard
- [x] Single HTML file, Chart.js 4.x from CDN
- [x] Host CPU usage chart (stacked area: us/sy/wa/id)
- [x] GPU busy% + temp dual-axis chart
- [x] Load average (1/5/15) chart
- [x] Memory used/free/swap chart
- [x] CPU temperature chart
- [x] Guest overview table with status dots
- [x] Guest drill-down (CPU, memory, network, disk charts)
- [x] PSI pressure charts (CPU/IO/Memory)
- [x] Time range controls (15m, 1h, 6h, 24h)
- [x] Auto-refresh (configurable interval)
- [x] Token input bar for auth

### Systemd
- [x] pvemonitor-collector.service (oneshot)
- [x] pvemonitor-collector.timer (OnUnitActiveSec=10s)
- [x] pvemonitor-api.service (simple, Restart=always)
- [x] pvemonitor-maintenance.service (WAL checkpoint + optimize)
- [x] pvemonitor-maintenance.timer (daily at 03:07)
- [x] systemd/README.md with install instructions

### Shell Scripts
- [x] bootstrap-venv.sh (create venv, install deps, editable install)
- [x] run-collector.sh
- [x] report-latest.sh
- [x] report-host-gpu.sh
- [x] healthcheck.sh
- [x] backup-db.sh (sqlite3 .backup + 7-day retention)

### Tests — 55/55 passing
- [x] Schema creation and migration (10 tests)
- [x] Rate calculation (10 tests)
- [x] Guest state handling (6 tests)
- [x] GPU parsing (9 tests)
- [x] Locking (15 tests)
- [x] Cascade delete / FK enforcement
- [x] View creation

## Known Limitations (documented in README)

1. **Single GPU**: host_metrics stores only the first GPU. Multi-GPU support planned for Phase 2.
2. **Guest-internal metrics**: Not available from host side (temps, in-guest GPU).
3. **GPU passthrough**: Host can't see GPU metrics for devices passed through to VMs.

## Plan Review Issues — All 18 Resolved

See [plan-review.md](plan-review.md) for the original findings. All 18 issues
(4 critical, 6 high, 8 medium) have been addressed in the implementation.
