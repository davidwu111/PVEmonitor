# PVEmonitor

Lightweight Proxmox VE host and guest monitor for AI workloads.

Collects host CPU/GPU load and temperatures, plus per-VM/LXC state, uptime, CPU,
and common resource metrics. All telemetry is kept **in memory** (configurable
cap) so the disk is only touched by optional periodic snapshots — great for
reducing HDD/SSD wear. A single `pvemonitor serve` process runs the collector
loop and the REST API.

## Quick start

```sh
# 1. Bootstrap Python virtual environment
./scripts/bootstrap-venv.sh

# 2. Verify a single collection works (debug one-shot)
.venv/bin/python -m pvemonitor collect

# 3. Install systemd units (starts the collector + API service)
sudo ./scripts/install-systemd.sh

# 4. Open dashboard at http://<host>:8806
```

To try the API without installing systemd:

```sh
.venv/bin/python -m pvemonitor serve
```

To install systemd units without auto-starting:

```sh
sudo ./scripts/install-systemd.sh --no-enable
```

To remove systemd units:

```sh
sudo ./scripts/uninstall-systemd.sh           # remove units, keep data
sudo ./scripts/uninstall-systemd.sh --purge   # also delete project directory
```

## Supported hardware

### CPU

| Source | Metrics | Notes |
|--------|---------|-------|
| `/proc/loadavg` | load1, load5, load15 | All Linux kernels |
| `/proc/meminfo` | Memory total/used/free, swap | All Linux kernels |
| `/proc/pressure/{cpu,io,memory}` | PSI some/full avg10/60/300 | Kernel 4.20+ with `CONFIG_PSI=y` |
| `/proc/stat` (via `top -bn1`) | CPU us/sy/wa/id breakdown | All Linux kernels |
| `sensors` (lm-sensors) | CPU temperature | k10temp (AMD), coretemp (Intel) |
| `/sys/class/hwmon/` | CPU temperature fallback | When sensors command unavailable |
| `ps` | Top CPU process name/PID/% | All Linux |

### GPU

The collector auto-detects GPU vendor and selects the best available data source
in this order:

| # | Source | Vendor | Metrics | Requirements |
|---|--------|--------|---------|--------------|
| 1 | **rocm-smi** | AMD | name, busy%, temp, power (W), VRAM% | ROCm driver installed |
| 2 | **nvidia-smi** | NVIDIA | name, busy%, temp, power (W), VRAM% | NVIDIA driver installed |
| 3 | **sysfs** | AMD iGPU, Intel Arc, others | name, busy%, temp | Standard DRM interfaces |

**GPU metrics collected per vendor:**

| Metric | AMD (rocm-smi) | NVIDIA (nvidia-smi) | sysfs fallback |
|--------|:---:|:---:|:---:|
| GPU name | ✓ | ✓ | partial |
| GPU busy % | ✓ | ✓ | ✓ |
| GPU temperature °C | ✓ | ✓ | ✓ |
| GPU power (W) | ✓ | ✓ | — |
| VRAM used % | ✓ | ✓ | — |

**sysfs fallback** works with any GPU that exposes standard DRM interfaces
(`/sys/class/drm/card*/device/gpu_busy_percent` and hwmon `temp1_input`).
This covers most AMD integrated GPUs (Radeon 780M, etc.), Intel Arc GPUs,
and some workstation cards even without vendor tools installed.

**Multi-GPU note:** v1 stores only the first GPU's metrics in `host_metrics`.
If multiple GPUs are detected, a warning is logged. A per-GPU table
(`host_gpu_samples`) is planned for Phase 2.

### Guests

| Type | Source | Metrics |
|------|--------|---------|
| **QEMU VMs** | `pvesh get /nodes/pve/qemu/<vmid>/status/current` | CPU, memory, disk, network, uptime, guest-agent info |
| **LXC containers** | `pvesh get /nodes/pve/lxc/<vmid>/status/current` | CPU, memory, disk, network, uptime, swap, pressure |

All guests are recorded every sample — running, stopped, paused, or unknown —
so up/down history is always queryable.

## Features

| Feature | Description |
|---------|-------------|
| **Host monitoring** | CPU usage/breakdown, load average, memory, swap, rootfs, CPU temperature |
| **GPU monitoring** | Multi-vendor: AMD (rocm-smi), NVIDIA (nvidia-smi), sysfs for basic metrics |
| **Guest monitoring** | Per-VM/LXC CPU, memory, disk I/O rates, network throughput |
| **Pressure Stall (PSI)** | CPU, IO, memory pressure (some/full, avg10/60/300) |
| **In-memory storage** | All telemetry lives in RAM (configurable cap); no per-sample disk writes |
| **Snapshots** | Full store copies written on a configurable interval and on shutdown; loaded on startup |
| **REST API** | FastAPI on :8806, optional shared-secret auth, CORS enabled |
| **Dashboard** | Single-page Chart.js app — live view + historical (yesterday, 7d, 30d, custom range) with automatic raw/rollup selection |
| **Systemd integration** | Single always-on service running collection + API |
| **Safe collection** | Subprocess timeouts, partial failure tolerance |
| **Rate calculation** | Disk/network bps from cumulative counters with max interval guard, counter reset detection |
| **Migrations** | Versioned, append-only SQL files applied in order on startup (in-memory schema) |

## CLI reference

| Command | Description |
|---------|-------------|
| `pvemonitor collect` | Debug one-shot collection (throwaway store, prints summary) |
| `pvemonitor serve` | Start the collector + API service (all telemetry in memory) |
| `pvemonitor init-db` | Compatibility no-op (schema is created automatically) |
| `pvemonitor report-latest` | Latest host + guest status (from newest snapshot) |
| `pvemonitor report-gpu` | Recent GPU trend (from newest snapshot) |
| `pvemonitor health` | Health check (exit 0/1/2) — live API, snapshot fallback |
| `pvemonitor config` | Print resolved config |

## Scripts

| Script | Purpose |
|--------|---------|
| `bootstrap-venv.sh` | Create .venv, install dependencies, editable install |
| `install-systemd.sh` | Copy units to /etc/systemd/system/, enable and start |
| `uninstall-systemd.sh` | Stop services, remove units (--purge to delete project) |
| `run-collector.sh` | Run one debug collection cycle |
| `report-latest.sh` | Print latest host + guest status |
| `report-host-gpu.sh` | Print recent GPU trend |
| `healthcheck.sh` | Health check (exit 0/1/2) |
| `backup-db.sh` | Copy newest telemetry snapshot to runtime/backups (7-day retention) |

## Retention and rollups

Default behavior:
- raw `samples` + `host_metrics` + `guest_samples`: keep 30 days
- 1-minute host/guest rollups: keep 90 days
- 5-minute host/guest rollups: keep 365 days
- pruning runs roughly hourly (`maintenance_interval_samples: 360` at 10s sampling)

Storage is bounded by `storage.memory_limit_mb` (default 256 MB). Estimated
usage is checked after every sample and the oldest raw samples are evicted
first (the newest sample is always kept), so the store never exceeds the cap.
Time-based retention still applies for rollups.

```yaml
storage:
  memory_limit_mb: 256           # hard cap on estimated telemetry memory usage
  snapshot_enabled: true         # false = memory-only, no disk snapshots
  snapshot_interval_minutes: 60  # how often a full snapshot is written
  snapshot_keep: 7               # number of snapshot files to retain
  snapshot_dir: runtime/exports/snapshots
  import_legacy_db: true         # one-time import of runtime/db/metrics.sqlite3
```

Snapshots are full SQLite copies written by `serve` on the configured interval
and on graceful shutdown, then loaded back on startup. CLI reports read the
newest snapshot, so they are at most one snapshot interval stale. On first
startup after upgrading, an existing `runtime/db/metrics.sqlite3` is imported
once (read-only) if no snapshot exists yet.

Range selection behavior:
- `<= 24h`: raw samples
- `> 24h` and `<= 7d`: 1-minute rollups
- `> 7d`: 5-minute rollups

The dashboard uses API auto-resolution selection by default, so long-range charts stay responsive while recent views remain full-resolution.

## Known limitations

- **Single GPU**: The host_metrics table stores only the first GPU's metrics.
  If multiple GPUs are detected, a warning is logged. Multi-GPU support is
  planned for Phase 2.
- **Guest-internal metrics**: Guest temperatures and GPU metrics inside VMs
  are not available from the host side.
- **GPU passthrough**: If a GPU is fully passed through to a VM, host-side
  GPU metrics for that device will be unavailable.
- **Downsampling**: For very long time ranges (30d+) with 10s sampling,
  the API/dashboard now switches automatically to rollups (1m, then 5m).
  Adjust retention windows in `config/monitor.yaml` if you want longer raw history.
- **Memory-only history**: Telemetry is not written per-sample, so history
  beyond the newest snapshot is lost if the service is restarted without a
  snapshot (set `snapshot_interval_minutes` to your desired recovery window).
- **CLI reports are snapshot-based**: `report-latest`, `report-gpu`, and the
  `health` fallback read the newest snapshot, not the live store.

## Documentation

- [Design document](docs/pve_ai_monitor_design.md)
- [Project layout](docs/project-layout.md)
- [Plan review](docs/plan-review.md)
- [Implementation status](docs/implementation-status.md)
