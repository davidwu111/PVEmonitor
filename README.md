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

## Known limitations

- **Single GPU**: The host_metrics table stores only the first GPU's metrics.
  If multiple GPUs are detected, a warning is logged. Multi-GPU support is
  planned for Phase 2.
- **Guest-internal metrics**: Guest temperatures and GPU metrics inside VMs
  are not available from the host side.
- **GPU passthrough**: If a GPU is fully passed through to a VM, host-side
  GPU metrics for that device will be unavailable.

## Documentation

- [Design document](docs/pve_ai_monitor_design.md)
- [Project layout](docs/project-layout.md)
- [Plan review](docs/plan-review.md)
