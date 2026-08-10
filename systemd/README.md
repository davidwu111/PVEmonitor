# PVEmonitor Systemd Units

The `pvemonitor.service` unit is the **source of truth** for the PVEmonitor
systemd configuration. A single always-on service runs both the collector
loop and the FastAPI server, keeping all telemetry in memory.

## Installation

Use the install script (handles placeholder substitution, legacy unit
cleanup, enable, and start):

```sh
sudo ./scripts/install-systemd.sh
```

Or manually:

```sh
PROJECT_ROOT="/opt/PVEmonitor"  # or wherever PVEmonitor lives

# Replace @@PVEMONITOR_HOME@@ with the actual path
sed "s|@@PVEMONITOR_HOME@@|$PROJECT_ROOT|g" systemd/pvemonitor.service \
    | sudo tee /etc/systemd/system/pvemonitor.service >/dev/null

sudo systemctl daemon-reload
sudo systemctl enable --now pvemonitor.service
```

4. Verify:

```sh
systemctl status pvemonitor.service
curl http://localhost:8806/api/health
```

## Updating

After editing the unit file in `systemd/`:

```sh
sudo cp systemd/pvemonitor.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl restart pvemonitor.service
```

**Important:** Use `cp` (not symlinks). Systemd warns about symlinked unit
files and may refuse to enable them.

## Units

| Unit | Type | Purpose |
|------|------|---------|
| `pvemonitor.service` | simple | Collection loop + FastAPI server on :8806 (all telemetry in memory) |

The legacy `pvemonitor-collector.*`, `pvemonitor-api.service`, and
`pvemonitor-maintenance.*` units are removed. The install script cleans them
up automatically when upgrading.
