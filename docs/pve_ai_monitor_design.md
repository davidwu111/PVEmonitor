# PVE AI Monitor Script Design

Goal: log host CPU/GPU load and temperatures, plus per-VM/LXC state, uptime, CPU, and common resource metrics, into a durable SQLite database on the Proxmox host for long-term monitoring and easy future analysis during AI workloads.

## 1. Design summary

Use one lightweight Python project on the PVE host, stored under a single self-contained project root.

Recommended project root:
- /opt/PVEmonitor

Core runtime paths under that root:
- Python interpreter: /opt/PVEmonitor/.venv/bin/python
- Script entrypoint: /opt/PVEmonitor/src/pvemonitor/collector.py
- Main command form: `/opt/PVEmonitor/.venv/bin/python -m pvemonitor collect`
- Database path: /opt/PVEmonitor/runtime/db/metrics.sqlite3
- Collector log path: /opt/PVEmonitor/runtime/logs/collector.log
- Lock path: /opt/PVEmonitor/runtime/locks/collector.lock
- Requirements path: /opt/PVEmonitor/requirements/
- Run cadence: every 10s for short AI runs, or every 30s for continuous monitoring
- Storage format: SQLite database with normalized host/guest sample tables

See also:
- <PVEMONITOR_HOME>/docs/project-layout.md for the full folder structure design

Reason for SQLite instead of flat logs:
- much better for long retention
- easy to query by time range, guest, or metric
- avoids parsing large JSONL files later
- supports indexes, rollups, and simple dashboards
- Python has sqlite3 built in on this host
- sqlite3 CLI is installed on this host for ad hoc queries

## 2. Data sources available on this host

Verified on pve.lan:
- Host status: pvesh get /nodes/pve/status
- Guest inventory and live metrics: pvesh get /cluster/resources --type vm
- Per-guest details: pvesh get /nodes/pve/qemu/<vmid>/status/current and /nodes/pve/lxc/<vmid>/status/current
- CPU summary: /proc/loadavg, top -bn1
- CPU pressure / IO pressure / memory pressure: /proc/pressure/{cpu,io,memory}
- CPU temperature: sensors, k10temp Tctl
- GPU busy/temp fallback: /sys/class/drm/card*/device/gpu_busy_percent and /sys/class/drm/card*/device/hwmon/hwmon*/temp1_input
- GPU detailed metrics primary source: rocm-smi
- GPU identity: lspci / amdgpu
- Scheduler available: systemd
- Python available: Python 3.13.5
- sqlite3 CLI available: 3.46.1
- Python sqlite3 module available: 3.46.1

Current limitations verified:
- guest-internal metrics are limited to what Proxmox exposes from the host side
- guest temperatures are not available from the host unless collected inside each guest separately
- some rocm-smi memory fields are not supported on this Phoenix3 iGPU, so the collector should store NULL when rocm-smi reports unsupported values

## 3. Metrics to store each sample

### Host metrics
Required:
- timestamp
- hostname
- load1, load5, load15
- cpu_usage_pct
- cpu_user_pct
- cpu_system_pct
- cpu_iowait_pct
- cpu_idle_pct
- cpu_temp_c
- mem_total_bytes
- mem_used_bytes
- mem_free_bytes
- swap_total_bytes
- swap_used_bytes
- rootfs_total_bytes
- rootfs_used_bytes
- rootfs_free_bytes
- gpu_name
- gpu_busy_pct
- gpu_temp_c
- gpu_power_w
- gpu_vram_used_pct

Recommended extras:
- pressure cpu some/full avg10/avg60/avg300
- pressure io some/full avg10/avg60/avg300
- pressure memory some/full avg10/avg60/avg300
- top_cpu_process_name
- top_cpu_process_pid
- top_cpu_process_pct

### Per-VM/LXC metrics
For each guest sample, store exactly one row whether it is up or down:
- sample_id
- vmid
- type: qemu or lxc
- name
- status: running, stopped, paused, suspended, or unknown
- is_running: 1 or 0
- uptime_s
- cpu_host_pct
- cpu_of_allocated_pct
- maxcpu
- mem_bytes
- maxmem_bytes
- mem_pct
- diskread_bytes_total
- diskwrite_bytes_total
- netin_bytes_total
- netout_bytes_total
- diskread_bps
- diskwrite_bps
- netin_bps
- netout_bps

Rules:
- always insert a guest_samples row for every known guest on every collection run
- when a guest is not running, status must still be recorded, but runtime/resource metrics must be NULL
- uptime_s is populated only when the guest is running; otherwise NULL
- this makes up/down history and reboot gaps queryable directly from SQLite

Available for LXC only or where exposed:
- swap_bytes
- pressurecpusome / pressurecpufull
- pressureiosome / pressureiofull
- pressurememorysome / pressurememoryfull

Available for some QEMU VMs if guest agent / balloon info exists:
- freemem
- balloon_actual
- guest_agent_enabled

## 4. CPU interpretation rules

Use two CPU fields for guests because Proxmox CPU values confuse people:
- cpu_host_pct = cpu * 100
  - percent of one full host system capacity unit as exposed by Proxmox
- cpu_of_allocated_pct = (cpu / maxcpu) * 100
  - how busy the guest is relative to its assigned vCPU count

Example:
- if cpu=0.50 and maxcpu=4
- cpu_host_pct = 50.0
- cpu_of_allocated_pct = 12.5

Keep both in the database so later analysis is unambiguous.

## 5. Sampling strategy

Default recommendations:
- AI benchmark/training/inference session: every 10 seconds
- Always-on background trending: every 30 or 60 seconds

Why not faster than 5 seconds:
- too much write volume for little added value
- pvesh polling overhead rises
- temperatures and load trends usually do not need sub-second granularity

## 6. SQLite schema design

Use a small normalized schema.

### Table: samples
One row per collection run.

Columns:
- id INTEGER PRIMARY KEY
- ts TEXT NOT NULL
- epoch_s INTEGER NOT NULL
- hostname TEXT NOT NULL
- collector_version TEXT
- collection_ms INTEGER
- error_count INTEGER DEFAULT 0

Rules:
- store `ts` in RFC3339 UTC form, for example `2026-06-06T04:12:30Z`
- store `epoch_s` as the matching Unix timestamp in UTC

Indexes:
- INDEX idx_samples_ts ON samples(ts)
- INDEX idx_samples_epoch ON samples(epoch_s)

### Table: host_metrics
One row per sample.

Columns:
- sample_id INTEGER PRIMARY KEY REFERENCES samples(id) ON DELETE CASCADE
- load1 REAL
- load5 REAL
- load15 REAL
- cpu_usage_pct REAL
- cpu_user_pct REAL
- cpu_system_pct REAL
- cpu_iowait_pct REAL
- cpu_idle_pct REAL
- cpu_temp_c REAL
- mem_total_bytes INTEGER
- mem_used_bytes INTEGER
- mem_free_bytes INTEGER
- swap_total_bytes INTEGER
- swap_used_bytes INTEGER
- rootfs_total_bytes INTEGER
- rootfs_used_bytes INTEGER
- rootfs_free_bytes INTEGER
- gpu_name TEXT
- gpu_busy_pct REAL
- gpu_temp_c REAL
- gpu_power_w REAL
- gpu_vram_used_pct REAL
- psi_cpu_some_avg10 REAL
- psi_cpu_some_avg60 REAL
- psi_cpu_some_avg300 REAL
- psi_cpu_full_avg10 REAL
- psi_cpu_full_avg60 REAL
- psi_cpu_full_avg300 REAL
- psi_io_some_avg10 REAL
- psi_io_some_avg60 REAL
- psi_io_some_avg300 REAL
- psi_io_full_avg10 REAL
- psi_io_full_avg60 REAL
- psi_io_full_avg300 REAL
- psi_mem_some_avg10 REAL
- psi_mem_some_avg60 REAL
- psi_mem_some_avg300 REAL
- psi_mem_full_avg10 REAL
- psi_mem_full_avg60 REAL
- psi_mem_full_avg300 REAL
- top_cpu_process_name TEXT
- top_cpu_process_pid INTEGER
- top_cpu_process_pct REAL

### Table: guests
Dimension table for guest identity.

Columns:
- vmid INTEGER NOT NULL
- guest_type TEXT NOT NULL
- first_seen_ts TEXT NOT NULL
- last_seen_ts TEXT NOT NULL
- current_name TEXT
- PRIMARY KEY (vmid, guest_type)

### Table: guest_samples
One row per guest per sample.

Columns:
- sample_id INTEGER NOT NULL REFERENCES samples(id) ON DELETE CASCADE
- vmid INTEGER NOT NULL
- guest_type TEXT NOT NULL
- name TEXT
- status TEXT
- is_running INTEGER NOT NULL
- uptime_s INTEGER
- maxcpu INTEGER
- cpu_host_pct REAL
- cpu_of_allocated_pct REAL
- mem_bytes INTEGER
- maxmem_bytes INTEGER
- mem_pct REAL
- swap_bytes INTEGER
- diskread_bytes_total INTEGER
- diskwrite_bytes_total INTEGER
- netin_bytes_total INTEGER
- netout_bytes_total INTEGER
- diskread_bps REAL
- diskwrite_bps REAL
- netin_bps REAL
- netout_bps REAL
- pressurecpusome REAL
- pressurecpufull REAL
- pressureiosome REAL
- pressureiofull REAL
- pressurememorysome REAL
- pressurememoryfull REAL
- freemem_bytes INTEGER
- balloon_actual_bytes INTEGER
- guest_agent_enabled INTEGER
- PRIMARY KEY (sample_id, vmid, guest_type)
- FOREIGN KEY (vmid, guest_type) REFERENCES guests(vmid, guest_type)

Indexes:
- INDEX idx_guest_samples_vmid_type_sample ON guest_samples(vmid, guest_type, sample_id)
- INDEX idx_guest_samples_status ON guest_samples(status)

### Table: collector_errors
Optional table for partial failures.

Columns:
- sample_id INTEGER NOT NULL REFERENCES samples(id) ON DELETE CASCADE
- scope TEXT NOT NULL
- vmid INTEGER
- guest_type TEXT
- message TEXT NOT NULL

### Table: schema_version
Migration tracking table.

Columns:
- version INTEGER PRIMARY KEY
- applied_ts TEXT NOT NULL DEFAULT (datetime('now'))
- filename TEXT NOT NULL

Rules:
- migrations are append-only; never modify a migration after it has been applied
- on startup, query MAX(version) and apply only unapplied migration files in order
- each migration runs inside its own transaction

## 7. Why this schema

This split is intentional:
- samples = run boundary and timing
- host_metrics = one-to-one host snapshot
- guests = stable identity table for names and inventory history
- guest_samples = time series for each VM/LXC, including explicit up/down state rows
- collector_errors = keep partial failures without losing the sample

This makes common queries easy:
- host GPU temp over time
- top CPU-consuming guests in the last hour
- memory trend for LXC 104
- average host load during a benchmark window
- network throughput of a specific VM during an AI run
- when a VM/LXC went down or came back up
- how long a guest had been continuously up before a stop or reboot

## 8. Example queries you will want later

Host GPU temp trend:
```sql
SELECT ts, gpu_temp_c, gpu_busy_pct, gpu_power_w, gpu_vram_used_pct
FROM samples s
JOIN host_metrics h ON h.sample_id = s.id
WHERE ts >= datetime('now', '-2 hours')
ORDER BY ts;
```

Top guest CPU consumers in the last hour:
```sql
SELECT gs.vmid, gs.guest_type, gs.name,
       ROUND(AVG(gs.cpu_host_pct), 2) AS avg_cpu_host_pct,
       ROUND(MAX(gs.cpu_host_pct), 2) AS max_cpu_host_pct
FROM guest_samples gs
JOIN samples s ON s.id = gs.sample_id
WHERE s.epoch_s >= strftime('%s','now') - 3600
  AND gs.status = 'running'
GROUP BY gs.vmid, gs.guest_type, gs.name
ORDER BY avg_cpu_host_pct DESC;
```

LXC 104 memory and CPU trend:
```sql
SELECT s.ts, gs.status, gs.is_running, gs.uptime_s,
       gs.cpu_host_pct, gs.cpu_of_allocated_pct, gs.mem_bytes, gs.mem_pct
FROM guest_samples gs
JOIN samples s ON s.id = gs.sample_id
WHERE gs.vmid = 104 AND gs.guest_type = 'lxc'
ORDER BY s.ts;
```

Guest up/down transition history:
```sql
WITH ordered AS (
  SELECT s.ts, s.epoch_s, gs.vmid, gs.guest_type, gs.name, gs.status, gs.is_running, gs.uptime_s,
         LAG(gs.is_running) OVER (PARTITION BY gs.vmid, gs.guest_type ORDER BY s.epoch_s) AS prev_running
  FROM guest_samples gs
  JOIN samples s ON s.id = gs.sample_id
)
SELECT ts, vmid, guest_type, name, status, uptime_s,
       CASE
         WHEN prev_running IS NULL THEN 'first_seen'
         WHEN prev_running = 0 AND is_running = 1 THEN 'came_up'
         WHEN prev_running = 1 AND is_running = 0 THEN 'went_down'
         ELSE 'no_change'
       END AS transition
FROM ordered
WHERE prev_running IS NULL OR prev_running != is_running
ORDER BY ts;
```

Current up/down view for all guests:
```sql
SELECT s.ts, gs.vmid, gs.guest_type, gs.name, gs.status, gs.is_running, gs.uptime_s
FROM guest_samples gs
JOIN samples s ON s.id = gs.sample_id
WHERE s.id = (SELECT MAX(id) FROM samples)
ORDER BY gs.guest_type, gs.vmid;
```

Host pressure during AI run:
```sql
SELECT s.ts,
       h.psi_cpu_some_avg10,
       h.psi_io_some_avg10,
       h.psi_mem_some_avg10
FROM samples s
JOIN host_metrics h ON h.sample_id = s.id
WHERE s.epoch_s BETWEEN ? AND ?
ORDER BY s.epoch_s;
```

## 9. Script behavior

Per run:
1. Acquire a non-overlap lock so timer-triggered runs cannot stack
2. Start transaction
3. Collect host metrics
4. Collect full VM/LXC inventory from pvesh, including stopped guests
5. For each guest, collect or derive status and is_running
6. For each running guest, collect detailed status/current JSON
7. For each non-running guest, insert a guest_samples row with status set and runtime/resource fields as NULL
8. Compute rates from previous sample already stored in SQLite, only for running intervals with valid counters
9. Insert one row into samples
10. Insert one row into host_metrics
11. Upsert guest identity rows into guests
12. Insert one row per guest into guest_samples
13. Insert any partial failures into collector_errors
14. Commit transaction
15. Release lock
16. Exit fast and quietly

Failure handling:
- if one guest query fails, still commit host and other guest data
- store the error in collector_errors
- never delete historical data because of partial failure

Subprocess timeout policy:
- all external command calls (pvesh, rocm-smi, sensors, ps) must use a timeout
- recommended timeouts:
  - pvesh get /nodes/pve/status: 5s
  - pvesh get /cluster/resources: 10s
  - pvesh get .../status/current (per guest): 5s each
  - rocm-smi: 5s
  - sensors: 2s
- on timeout, catch subprocess.TimeoutExpired, log to collector_errors, continue with partial data
- a hung subprocess must never block the lock or prevent a partial sample from being committed

## 10. Suggested implementation approach

Recommended implementation: full Python project using a local virtual environment and stdlib-first code

Why:
- Python already exists on the host
- sqlite3 is built in and available
- a project-local `.venv` isolates future dependencies from the PVE host
- requirements files make bootstrap explicit even if runtime deps stay minimal at first
- easier than shell for schema creation, transactions, locking, and delta calculations
- no jq dependency needed
- easier to extend later with retention and rollups

Internal structure:
- `.venv/`
- `requirements/base.txt` (runtime: fastapi, uvicorn[standard])
- `requirements/dev.txt` (dev: pytest; includes `-r base.txt`)
- collect_host_metrics()
- collect_guest_inventory()
- collect_guest_details(vmid, guest_type)
- compute_guest_rates_from_previous_sample()
- ensure_schema()
- write_sample_transaction()
- acquire_collection_lock()
- optional vacuum_or_checkpoint_maintenance()
- FastAPI application with endpoint routers
- static/dashboard.html served by FastAPI

CLI subcommands (via `python -m pvemonitor <command>`):
- `collect` — one full collection run (host + guests + rates)
- `init-db` — create/upgrade schema (safe to run repeatedly)
- `serve` — start FastAPI server on 0.0.0.0:8806
- `report-latest` — latest host + guest status summary
- `report-gpu` — recent GPU temp/load/power trend
- `health` — check lock freshness, DB readability, latest sample age; exit 0/1/2
- `config` — print resolved config with paths

Config schema (`config/monitor.yaml`):
- `monitor.sample_interval_s` — default 10
- `monitor.hostname_override` — null (use socket.gethostname())
- `monitor.collection_timeout_s` — default 25 (max time for one full run)
- `paths.db` — relative to PVEMONITOR_HOME, default `runtime/db/metrics.sqlite3`
- `paths.log` — relative, default `runtime/logs/collector.log`
- `paths.lock` — relative, default `runtime/locks/collector.lock`
- `collection.host_enabled`, `collection.guests_enabled`, `collection.gpu_enabled`, `collection.psi_enabled`, `collection.top_process_enabled` — all default true
- `gpu.rocm_smi_bin` — default `/usr/bin/rocm-smi`
- `gpu.sysfs_fallback` — default true
- `rate_calculation.max_interval_s` — default 120
- `logging.level` — default INFO
- `logging.max_log_bytes` — default 10485760 (10 MB)
- `logging.backup_count` — default 5
- `api.host` — default `0.0.0.0`
- `api.port` — default `8806`
- `api.auth_token` — default empty (no auth); set to a shared secret string for LAN access control

## 11. Rate calculation design

Do not use a separate state file anymore.

Instead:
- after collecting current guest cumulative counters, query the previous guest_samples row for the same vmid/type
- compute delta bytes / delta seconds only when both the previous and current rows are running samples
- if previous sample missing, store NULL for rate fields
- if either side is a non-running sample, store NULL for rate fields
- if counters move backwards due to reboot/reset, store NULL for that interval

Maximum interval guard:
- define max_rate_interval_s (default 120s)
- if delta_seconds exceeds max_rate_interval_s, store NULL for rate fields
- this prevents misleadingly low rates after collector downtime (crash, maintenance, reboot)
- log a note when rates are suppressed due to interval exceeded

Counter sanity checks:
- if delta_bytes is negative, store NULL (counter reset)
- if delta_bytes exceeds a physically impossible ceiling for the interface in the interval, store NULL (counter bug or wrap-around)
- sanity ceiling: interface_max_Bps * interval_s * 2 (generous margin)

Benefits:
- no duplicated state management
- database remains the source of truth
- simpler recovery after reboot or service restart

## 12. Retention, migrations, and database maintenance

SQLite handles long-term storage well, but keep it tidy.

### Migration strategy

- use a `schema_version` table to track applied migrations
- SQL migration files in `sql/schema/` are numbered: `000_version.sql`, `001_init.sql`, `002_indexes.sql`, `003_views.sql`, etc.
- on startup, `db.py` queries `MAX(version)` and applies only unapplied files in order
- each migration runs inside a transaction
- migrations are append-only: never modify a migration file after it has been applied
- schema creation is idempotent via `CREATE TABLE IF NOT EXISTS`

### Recommended pragmas

Run on every new connection:
- PRAGMA journal_mode=WAL;
- PRAGMA synchronous=NORMAL;
- PRAGMA foreign_keys=ON;
- PRAGMA temp_store=MEMORY;
- PRAGMA busy_timeout=5000;

### WAL maintenance

- after every 60 collection runs (≈10 min at 10s interval), execute `PRAGMA wal_checkpoint(PASSIVE)` inline in the collector
- via `pvemonitor-maintenance.timer`, run daily: `PRAGMA wal_checkpoint(TRUNCATE)` followed by `PRAGMA optimize`
- run full `VACUUM` monthly during a known low-activity window (not scheduled automatically by the collector)

### Expected size

- at 10s sampling with a small number of guests, this should still be manageable for a long time
- old raw data can be summarized later if needed

## 13. Locking design

Purpose: prevent timer-triggered collection runs from stacking.

Lock file: `<PVEMONITOR_HOME>/runtime/locks/collector.lock`

Acquisition logic in `locking.py`:
1. Open lock file with `O_CREAT | O_EXCL` (atomic creation).
2. If creation succeeds, write `PID TIMESTAMP` into the file and proceed.
3. If creation fails (file already exists), read the existing lock file.
4. Parse the stored PID and timestamp.
5. Check if PID is still alive via `os.kill(pid, 0)`.
6. Check if the lock age exceeds `stale_lock_timeout_s` (default: 5 × sample_interval_s).
7. If PID is dead OR lock is stale: log a warning, remove the old lock file, acquire a new one.
8. If PID is alive and lock is fresh: exit 0 immediately (another collector is running).

On exit:
- collector removes its own lock file in a `finally` block
- collector verifies the lock file still contains its own PID before removing (defense against stealing)

Systemd timer integration:
- timer uses `OnUnitActiveSec=10s` (fires 10s after service FINISHES, not 10s after timer starts)
- combined with the file lock, this guarantees at most one collector invocation at a time
- service type is `oneshot` with `RemainAfterExit=no`

CLI override:
- `pvemonitor collect --force` skips the PID liveness check and takes the lock unconditionally
- use only when the operator has verified no collector is actually running

## 14. Common metrics to watch during AI runs

Critical:
- host cpu_usage_pct
- load1/load5 trend
- cpu_temp_c
- gpu_busy_pct
- gpu_temp_c
- mem_used_bytes / free bytes
- swap_used_bytes
- iowait
- PSI cpu/io/memory if available

For VM/LXC impact:
- which guest has rising cpu_host_pct
- diskread_bps and diskwrite_bps spikes
- netin_bps and netout_bps spikes
- memory pressure on active LXCs
- guests with sustained high cpu_of_allocated_pct

Alert thresholds to consider later:
- CPU temp > 85 C sustained
- GPU temp > 85 C sustained
- host cpu_usage_pct > 90% for 5+ minutes
- iowait > 10% sustained
- swap usage non-zero and climbing
- rootfs free space < 15%
- one guest pinned near 100% of allocated CPU for long periods

## 15. Limitations to state clearly

This host-side logger can see:
- node load and temperatures
- GPU use, temperature, power, and some memory data from rocm-smi, with sysfs fallback for basic busy/temp fields
- Proxmox-reported VM/LXC CPU/memory/network/disk counters

It cannot reliably see from the host alone:
- guest-internal CPU temp
- exact process using GPU inside a guest
- guest GPU metrics if the GPU is fully passed through to a VM
- application-level AI stats such as tokens/s, VRAM used, model name, batch size

Multi-GPU limitation (v1):
- the host_metrics table stores a single set of GPU columns (gpu_name, gpu_busy_pct, etc.)
- v1 collects all GPUs but stores only the first/primary GPU's metrics
- if multiple GPUs are detected, log a warning with the count of GPUs found
- a separate host_gpu_samples table with a (sample_id, gpu_index) PK is planned for Phase 2
- this limitation must be documented in the project README

If you want that later, add a second guest-side agent in the AI VM/LXC and store those into separate tables keyed by timestamp and guest.

## 16. Deployment plan

Phase 0: validate host prerequisites
- verify Python 3.11+ (`python3 --version`)
- verify pvesh functional (`pvesh get /nodes/pve/status`)
- verify rocm-smi available or sysfs GPU path readable
- verify sensors returning CPU temperature data
- verify write permission on /opt/PVEmonitor (or chosen root)
- verify systemd available for timer scheduling
- run `scripts/bootstrap-venv.sh` to create venv and install deps

Phase 1: baseline SQLite logger + API + dashboard
- create project root under /opt/PVEmonitor
- create `.venv`
- add `requirements/base.txt` (fastapi, uvicorn[standard]) and `requirements/dev.txt` (pytest)
- create runtime directories
- implement schema creation and migrations
- implement collector (host + guest metrics + rate calculation)
- implement REST API (FastAPI, all endpoints from Section 18)
- implement dashboard (single HTML, Chart.js from CDN)
- test one-shot collection run
- test API endpoints with curl
- validate rows with sqlite3 queries
- run collector every 10s via systemd timer
- run API as persistent systemd service on 0.0.0.0:8806
- install systemd units:
  ```sh
  sudo cp /opt/PVEmonitor/systemd/pvemonitor-*.service /etc/systemd/system/
  sudo cp /opt/PVEmonitor/systemd/pvemonitor-*.timer /etc/systemd/system/
  sudo systemctl daemon-reload
  sudo systemctl enable --now pvemonitor-collector.timer
  sudo systemctl enable --now pvemonitor-api.service
  ```
- verify: `curl http://192.168.1.2:8806/api/health` returns healthy
- configure log rotation (RotatingFileHandler or journald)
- open firewall port 8806 for LAN subnet

Phase 2: improve analytics
- add delta rates from previous DB rows (if not in v1)
- add PSI metrics (if not in v1)
- add top process attribution
- add threshold checking against thresholds.yaml
- add a helper script with common sqlite3 reports

Phase 3: optional guest-side telemetry
- install small guest script inside AI VM/LXC
- log guest-internal load, temp, GPU details, and app stats
- write into separate guest-side tables or a second DB
- correlate timestamps with host DB

## 17. Recommended first implementation

I recommend this exact first version:
- Python project under /opt/PVEmonitor
- local venv at /opt/PVEmonitor/.venv
- requirements files under /opt/PVEmonitor/requirements/
  - `base.txt`: fastapi, uvicorn[standard] (only two non-stdlib deps)
- SQLite database at /opt/PVEmonitor/runtime/db/metrics.sqlite3
- 10-second systemd timer for the collector
- persistent systemd service for the FastAPI server on 0.0.0.0:8806
- non-overlap locking for collector runs
- collect host load/temp/GPU + guest CPU/mem/net/disk
- compute rate fields from the previous stored sample in SQLite
- REST API with all endpoints from Section 18
- single-page dashboard (Chart.js from CDN) with:
  - host CPU/GPU/memory/load charts
  - guest overview table with drill-down
  - PSI pressure charts
  - time range controls (15m / 1h / 6h / 24h)
- optional shared-secret token for LAN access

That gives you a self-contained, low-friction monitoring system: collect, store, query, and visualize — all from a browser on the LAN, no SSH tunnel needed.

## 18. REST API design

The API layer sits between the SQLite database and the dashboard. It serves JSON over HTTP and lives in the same project as the collector — same venv, same config, same DB connection.

### Runtime model

- The API server runs as a persistent systemd service: `pvemonitor-api.service`
- It binds to `0.0.0.0:8806` for LAN accessibility (no SSH tunnel needed)
- The SQLite DB is read-only from the API's perspective — only the collector writes
- Sqlite3 WAL mode allows concurrent reads from the API while the collector writes

### Dependencies

Just two packages added to `requirements/base.txt`:
```
fastapi>=0.115.0,<1.0.0
uvicorn[standard]>=0.34.0,<1.0.0
```

No ORM needed — FastAPI reads SQLite via Python's built-in `sqlite3` module.

### Endpoints

| Method | Path | Returns |
|--------|------|---------|
| `GET` | `/` | Redirects to `/dashboard` |
| `GET` | `/dashboard` | Serves `static/dashboard.html` |
| `GET` | `/api/health` | `{"status":"ok","latest_sample_ts":"...","latest_sample_age_s":N,"total_samples":N,"db_size_bytes":N}` |
| `GET` | `/api/host/latest` | Most recent host_metrics row joined with samples.ts |
| `GET` | `/api/host/range?from=&to=&fields=` | Host metrics array for time range; `fields` parses comma-separated column names to limit payload |
| `GET` | `/api/host/summary?window_s=3600` | Min/max/avg for GPU temp, CPU usage, load, memory over the window |
| `GET` | `/api/guests` | Current guest status summary (latest is_running, name, cpu, mem for each guest) |
| `GET` | `/api/guests/<vmid>/range?from=&to=` | Guest time-series (cpu, mem, disk_bps, net_bps) joined with samples.ts |
| `GET` | `/api/guests/<vmid>/transitions` | Up/down state transitions for a guest |
| `GET` | `/api/thresholds` | Current threshold definitions and which are currently breached |

Date formats: `from` and `to` accept ISO 8601 (`2026-06-06T04:00:00Z`) or relative (`-2h`, `-30m`).

### Auth

For LAN exposure, include a simple shared-secret token check:

- `config/monitor.yaml` holds an `api.auth_token` field (default: empty = no auth)
- If set, clients must pass `?token=<value>` or `Authorization: Bearer <value>`
- The dashboard HTML reads the token from `localStorage` and appends it to all fetch calls
- Not meant as strong security — just keeps a curious LAN neighbor from hitting the API

### Module location

```
src/pvemonitor/
├── api.py              # FastAPI app creation, lifespan, static file mount
├── api_routes/
│   ├── __init__.py
│   ├── health.py       # GET /api/health
│   ├── host.py         # GET /api/host/*
│   └── guests.py       # GET /api/guests/*
```

### New CLI entrypoint

`pvemonitor serve` — starts the API server via uvicorn. Alias: `python -m pvemonitor serve`.

### Systemd unit

```
systemd/pvemonitor-api.service
```
- Type: `simple` (long-running process)
- ExecStart: `<PVEMONITOR_HOME>/.venv/bin/python -m pvemonitor serve`
- After: `network.target`
- Restart: `always`, RestartSec: `5s`

## 19. Dashboard frontend

A single static HTML file served by the API. No build step, no npm, no node_modules.

### Stack

- **Chart.js 4.x** loaded from CDN (`cdn.jsdelivr.net`) — zero local JS dependencies
- **Single HTML file** at `src/pvemonitor/static/dashboard.html`
- Fetches all data from the API endpoints above
- Auto-refreshes every 10s (configurable via `?interval=N` query param)

### Dashboard layout

```
┌─────────────────────────────────────────────────────┐
│  PVEmonitor                              [10s ↻]    │
│  host: pve.lan    samples: 8,423    db: 14 MB       │
├──────────────────────┬──────────────────────────────┤
│                      │                              │
│  CPU Usage %         │  GPU Busy % + Temp °C        │
│  (gauge + sparkline) │  (dual-axis line chart)      │
│                      │                              │
├──────────────────────┼──────────────────────────────┤
│                      │                              │
│  Load 1/5/15         │  Memory Used / Total          │
│  (line chart)        │  (area chart)                │
│                      │                              │
├──────────────────────┴──────────────────────────────┤
│                                                      │
│  Guest Overview (table)                              │
│  ┌────────┬──────┬──────┬──────┬───────┬────────┐   │
│  │ VMID   │ Type │ Name │ CPU% │ Mem%  │ Status │   │
│  │ 100    │ qemu │ ai   │ 87.3 │ 62.1  │ run ✓  │   │
│  │ 104    │ lxc  │ db   │ 12.0 │ 34.5  │ run ✓  │   │
│  │ 105    │ lxc  │ web  │  0.5 │ 18.2  │ run ✓  │   │
│  └────────┴──────┴──────┴──────┴───────┴────────┘   │
│                                                      │
│  (click a guest row → drill-down chart below)        │
│                                                      │
│  Guest Detail: VM 100 — ai                           │
│  ┌──────────────────────────────────────────────────┐│
│  │ CPU % (line)     │  Network Bps (line)            ││
│  │ Disk Bps (line)  │  Memory (area)                 ││
│  └──────────────────────────────────────────────────┘│
│                                                      │
├──────────────────────────────────────────────────────┤
│  Pressure Stall (PSI) — 10s/60s/300s (bar chart)     │
│  CPU some/full  │  IO some/full  │  Mem some/full    │
│                                                      │
└──────────────────────────────────────────────────────┘
```

### Time range controls

- Preset buttons: 15m | 1h | 6h | 24h
- Custom from/to picker
- All charts re-fetch on range change

### Network topology

```
192.168.1.2 (pve.lan)                   Any LAN browser
┌─────────────────────────┐             ┌──────────────┐
│ Collector (systemd)     │             │              │
│   ↓ writes              │             │  http://     │
│ SQLite DB               │             │  192.168.1.2 │
│   ↓ reads               │   LAN      │  :8806       │
│ FastAPI :8806 ──────────┼────────────→│              │
│   ↓ serves              │             │  dashboard   │
│ dashboard.html          │             │  + charts    │
│ + /api/* JSON           │             │              │
└─────────────────────────┘             └──────────────┘
```

The API binds to `0.0.0.0:8806` so any machine on the LAN can open the dashboard. The PVE host firewall (iptables/nftables) must allow inbound TCP to port 8806 from the LAN subnet.

### Firewall note

On the PVE host, open port 8806 for the LAN:
```sh
iptables -A INPUT -p tcp --dport 8806 -s 192.168.1.0/24 -j ACCEPT
# persist with iptables-persistent or Proxmox firewall GUI
```

Alternatively, if the PVE firewall is managed via the Proxmox web UI (Datacenter → Firewall), add a rule there for port 8806.