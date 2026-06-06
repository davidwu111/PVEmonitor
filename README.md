# PVEmonitor

Lightweight Proxmox VE host and guest monitor for AI workloads.

Collects host CPU/GPU load and temperatures, plus per-VM/LXC state, uptime, CPU,
and common resource metrics into a durable SQLite database for long-term
monitoring and easy analysis.

## Quick start

```sh
# 1. Bootstrap Python virtual environment
./scripts/bootstrap-venv.sh

# 2. Initialize the database
.venv/bin/python -m pvemonitor init-db

# 3. Verify a single collection works
.venv/bin/python -m pvemonitor collect

# 4. Install systemd units (starts collector timer + API server)
sudo ./scripts/install-systemd.sh

# 5. Open dashboard at http://<host>:8806
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
| **SQLite storage** | WAL mode, migration-tracked schema, FK cascades, indexed for time-range queries |
| **REST API** | FastAPI on :8806, optional shared-secret auth, CORS enabled |
| **Dashboard** | Single-page Chart.js app — live view + historical (yesterday, 7d, 30d, custom range) |
| **Systemd integration** | Timer-driven collection (10s), persistent API service, daily maintenance |
| **Safe collection** | Non-overlap lock with stale detection, subprocess timeouts, partial failure tolerance |
| **Rate calculation** | Disk/network bps from cumulative counters with max interval guard, counter reset detection |
| **Migrations** | Versioned, append-only SQL files applied in order on startup |

## CLI reference

| Command | Description |
|---------|-------------|
| `pvemonitor collect` | Run one full collection (host + guests) |
| `pvemonitor collect --force` | Skip stale lock check |
| `pvemonitor init-db` | Create or upgrade schema |
| `pvemonitor serve` | Start FastAPI server on 0.0.0.0:8806 |
| `pvemonitor report-latest` | Latest host + guest status |
| `pvemonitor report-gpu` | Recent GPU trend |
| `pvemonitor health` | Health check (exit 0/1/2) |
| `pvemonitor config` | Print resolved config |

## Scripts

| Script | Purpose |
|--------|---------|
| `bootstrap-venv.sh` | Create .venv, install dependencies, editable install |
| `install-systemd.sh` | Copy units to /etc/systemd/system/, enable and start |
| `uninstall-systemd.sh` | Stop services, remove units (--purge to delete project) |
| `run-collector.sh` | Run one collection cycle |
| `report-latest.sh` | Print latest host + guest status |
| `report-host-gpu.sh` | Print recent GPU trend |
| `healthcheck.sh` | Health check (exit 0/1/2) |
| `backup-db.sh` | SQLite .backup with 7-day retention |

## Known limitations

- **Single GPU**: The host_metrics table stores only the first GPU's metrics.
  If multiple GPUs are detected, a warning is logged. Multi-GPU support is
  planned for Phase 2.
- **Guest-internal metrics**: Guest temperatures and GPU metrics inside VMs
  are not available from the host side.
- **GPU passthrough**: If a GPU is fully passed through to a VM, host-side
  GPU metrics for that device will be unavailable.
- **Downsampling**: For very long time ranges (30d+) with 10s sampling,
  chart rendering may slow. Server-side downsampling is planned.

## Documentation

- [Design document](docs/pve_ai_monitor_design.md)
- [Project layout](docs/project-layout.md)
- [Plan review](docs/plan-review.md)
- [Implementation status](docs/implementation-status.md)
