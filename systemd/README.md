# PVEmonitor Systemd Units

These unit files are the **source of truth** for the PVEmonitor systemd configuration.

## Installation

1. Replace the placeholder path in the unit files:

```sh
PROJECT_ROOT="/opt/PVEmonitor"  # or wherever PVEmonitor lives

# Update @@PVEMONITOR_HOME@@ to the actual path
sed -i "s|@@PVEMONITOR_HOME@@|$PROJECT_ROOT|g" systemd/*.service
```

2. Copy the unit files into systemd's directory:

```sh
sudo cp /opt/PVEmonitor/systemd/pvemonitor-*.service /etc/systemd/system/
sudo cp /opt/PVEmonitor/systemd/pvemonitor-*.timer /etc/systemd/system/
sudo systemctl daemon-reload
```

3. Enable and start:

```sh
sudo systemctl enable --now pvemonitor-collector.timer
sudo systemctl enable --now pvemonitor-api.service
sudo systemctl enable --now pvemonitor-maintenance.timer
```

4. Verify:

```sh
systemctl status pvemonitor-collector.timer
systemctl status pvemonitor-api.service
curl http://localhost:8806/api/health
```

## Updating

After editing a unit file in `systemd/`:

```sh
sudo cp /opt/PVEmonitor/systemd/pvemonitor-*.service /etc/systemd/system/
sudo cp /opt/PVEmonitor/systemd/pvemonitor-*.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl restart pvemonitor-collector.timer
sudo systemctl restart pvemonitor-api.service
```

**Important:** Use `cp` (not symlinks). Systemd warns about symlinked unit files
and may refuse to enable them.

## Units

| Unit | Type | Purpose |
|------|------|---------|
| `pvemonitor-collector.service` | oneshot | One collection run |
| `pvemonitor-collector.timer` | timer | Fires collector every 10s after completion |
| `pvemonitor-api.service` | simple | Persistent FastAPI server on :8806 |
| `pvemonitor-maintenance.service` | oneshot | WAL checkpoint + optimize |
| `pvemonitor-maintenance.timer` | timer | Daily at 03:07 |
