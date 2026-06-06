# PVEmonitor Project Folder Design

Goal: keep code, database, logs, reports, config, systemd files, SQL helpers, and documentation under one self-contained project root so the monitor is easy to deploy, back up, move, and inspect later.

Recommended deployment root on the PVE host:
- /opt/PVEmonitor

Why /opt/PVEmonitor:
- clear dedicated application location
- avoids mixing project files into /root directly
- still easy for a root-run systemd service to access
- keeps "all monitor data in one folder" as requested

If you prefer a home-directory deployment, the same structure also works under:
- /root/PVEmonitor

The design below uses a generic root variable:
- <PVEMONITOR_HOME>

## Recommended top-level layout

```text
<PVEMONITOR_HOME>/
├── README.md
├── .gitignore
├── pyproject.toml
├── .venv/
├── requirements/
│   ├── base.txt
│   └── dev.txt
├── docs/
│   ├── design.md
│   ├── project-layout.md
│   ├── schema.md
│   ├── queries.md
│   ├── operations.md
│   └── plan-review.md
├── config/
│   ├── monitor.yaml
│   └── thresholds.yaml
├── src/
│   └── pvemonitor/
│       ├── __init__.py
│       ├── __main__.py
│       ├── cli.py
│       ├── collector.py
│       ├── config.py
│       ├── db.py
│       ├── gpu.py
│       ├── host.py
│       ├── guests.py
│       ├── rates.py
│       ├── schema.py
│       ├── locking.py
│       ├── reporting.py
│       ├── util.py
│       ├── api.py
│       ├── api_routes/
│       │   ├── __init__.py
│       │   ├── health.py
│       │   ├── host.py
│       │   └── guests.py
│       └── static/
│           └── dashboard.html
├── sql/
│   ├── schema/
│   │   ├── 000_version.sql
│   │   ├── 001_init.sql
│   │   ├── 002_indexes.sql
│   │   └── 003_views.sql
│   ├── queries/
│   │   ├── host_gpu_trend.sql
│   │   ├── guest_cpu_top.sql
│   │   ├── guest_state_history.sql
│   │   └── latest_guest_status.sql
│   └── maintenance/
│       ├── wal_checkpoint.sql
│       ├── optimize.sql
│       └── vacuum.sql
├── scripts/
│   ├── bootstrap-venv.sh
│   ├── run-collector.sh
│   ├── report-latest.sh
│   ├── report-host-gpu.sh
│   ├── backup-db.sh
│   └── healthcheck.sh
├── systemd/
│   ├── README.md
│   ├── pvemonitor-collector.service
│   ├── pvemonitor-collector.timer
│   ├── pvemonitor-api.service
│   ├── pvemonitor-maintenance.service
│   └── pvemonitor-maintenance.timer
├── runtime/
│   ├── db/
│   │   ├── metrics.sqlite3
│   │   ├── metrics.sqlite3-shm
│   │   └── metrics.sqlite3-wal
│   ├── logs/
│   │   ├── collector.log
│   │   ├── collector.err.log
│   │   └── maintenance.log
│   ├── exports/
│   │   ├── csv/
│   │   └── snapshots/
│   ├── backups/
│   │   └── metrics-YYYYMMDD.sqlite3
│   ├── locks/
│   │   └── collector.lock
├── tests/
│   ├── conftest.py
│   ├── test_schema.py
│   ├── test_rate_calc.py
│   ├── test_guest_state_handling.py
│   ├── test_gpu_parsing.py
│   ├── test_locking.py
│   └── fixtures/
│       ├── pvesh_status.json
│       ├── pvesh_resources.json
│       ├── rocm-smi_output.txt
│       └── sensors_output.txt
```

## Directory purpose

### .venv/
Local Python virtual environment for this project.

Rules:
- keep the virtual environment inside the project root at `<PVEMONITOR_HOME>/.venv/`
- do not commit `.venv/` to Git
- systemd and helper scripts should call the interpreter explicitly from `.venv/bin/python`
- all package installation should happen inside this venv, never into the system Python

Reason:
- keeps Python dependencies isolated from the PVE host
- makes deployment reproducible
- avoids surprises from distro package changes

### pyproject.toml
Python project metadata file at the project root.

Purpose:
- declares package name (`pvemonitor`) and version
- sets `requires-python >= 3.11`
- defines entrypoint script: `pvemonitor = pvemonitor.cli:main`
- enables `pip install -e .` for editable installs
- enables IDE tooling (pylance, mypy) to recognize the package

Minimal content:
```toml
[project]
name = "pvemonitor"
version = "0.1.0"
requires-python = ">=3.11"

[project.scripts]
pvemonitor = "pvemonitor.cli:main"

[build-system]
requires = ["setuptools"]
build-backend = "setuptools.build_meta"
```

### requirements/
Pinned Python dependency definitions.

Recommended files:
- base.txt
  - runtime dependencies only; pin exact versions with `==`
- dev.txt
  - test and developer tooling; should include `-r base.txt`

For this project's first version, `base.txt` stays minimal (stdlib-first).
Still keep the file, because it documents that dependency intent explicitly.

Reason:
- makes bootstrap predictable
- allows adding pytest or future libraries cleanly
- gives the project a standard Python structure from day one

### docs/
Human documentation only.

Files to keep here:
- design.md: full monitoring design
- project-layout.md: this file
- schema.md: table-by-table schema notes
- queries.md: common analysis queries
- operations.md: install, start, stop, rotate, backup, restore

Reason:
- separates durable design from executable code
- makes future maintenance easier

### config/
Operator-editable settings.

Recommended files:
- monitor.yaml
  - sample interval
  - DB path
  - log path
  - rocm-smi enable/disable
  - sysfs fallback paths
- thresholds.yaml
  - CPU temp thresholds
  - GPU temp thresholds
  - high load thresholds
  - free space thresholds

Reason:
- avoid hardcoding environment-specific values into Python files

### src/pvemonitor/
All application code.

Recommended module split:
- __main__.py
  - supports `python -m pvemonitor`
- cli.py
  - command-line entrypoints such as `collect`, `report-latest`, `init-db`
- collector.py
  - main collection loop for one sample
- config.py
  - load and validate YAML config, derive project-relative paths
- db.py
  - sqlite connection, inserts, transactions, migrations bootstrap
- gpu.py
  - rocm-smi and sysfs collection
- host.py
  - host load, memory, PSI, sensors, top process
- guests.py
  - Proxmox inventory and per-guest status collection
- rates.py
  - delta/rate computation from previous DB rows
- schema.py
  - schema creation / migration helpers if not using raw SQL only
- locking.py
  - non-overlap lock handling for timer-driven collection
- reporting.py
  - reusable report functions
- util.py
  - parsing helpers, subprocess wrappers, null handling
- api.py
  - FastAPI application creation, lifespan handler, static file mount, CORS
- api_routes/
  - health.py — `GET /api/health`, DB connection check, latest sample freshness
  - host.py — `GET /api/host/latest`, `/api/host/range`, `/api/host/summary`
  - guests.py — `GET /api/guests`, `/api/guests/<vmid>/range`, `/api/guests/<vmid>/transitions`
- static/
  - dashboard.html — single-page app: Chart.js charts, guest drill-down, auto-refresh

Reason:
- keeps collector maintainable
- avoids one giant script
- makes testing easier
- API layer cleanly separated from collection logic

### sql/
All SQL in one place.

Use three subfolders:
- schema/
  - `000_version.sql` — migration tracking table (`schema_version`)
  - `001_init.sql` — initial table creation and index DDL
  - `002_indexes.sql` — additional indexes
  - `003_views.sql` — derived views
  - migrations are append-only; never modify an applied migration
- queries/
  - reusable analysis queries
- maintenance/
  - checkpoint, vacuum, retention helpers

Reason:
- SQL remains inspectable without reading Python
- makes ad hoc sqlite3 use much easier later

### scripts/
Thin shell wrappers only.

Examples:
- bootstrap-venv.sh
  - create `.venv`, upgrade pip, install `requirements/base.txt`, optionally install `requirements/dev.txt`
- run-collector.sh
  - export `PVEMONITOR_HOME`
  - call `.venv/bin/python -m pvemonitor collect`
- report-latest.sh
  - show latest host/guest status summary
- report-host-gpu.sh
  - show recent GPU temp/load/power trend
- backup-db.sh
  - use sqlite3 `.backup` safely into runtime/backups/
- healthcheck.sh
  - verify venv, DB readability, rocm-smi availability, and latest sample freshness

Reason:
- keeps operational commands short and consistent
- avoids long systemd ExecStart command lines

### systemd/
Checked-in unit files.

Recommended units:
- README.md
  - install and troubleshooting notes for the operator
- pvemonitor-collector.service
  - oneshot service for one collection pass using `<PVEMONITOR_HOME>/.venv/bin/python`
  - Type=oneshot, RemainAfterExit=no
- pvemonitor-collector.timer
  - OnUnitActiveSec=10s (fires 10s after service FINISHES, preventing stacking)
  - AccuracySec=1s
- pvemonitor-api.service
  - persistent service for the FastAPI + dashboard server
  - Type=simple, Restart=always, RestartSec=5s
  - After=network.target
  - ExecStart: `<PVEMONITOR_HOME>/.venv/bin/python -m pvemonitor serve`
  - listens on 0.0.0.0:8806 for LAN access
- pvemonitor-maintenance.service
  - periodic WAL checkpoint / backup / vacuum tasks
- pvemonitor-maintenance.timer
  - runs lower-frequency housekeeping jobs (daily)

Installation:
- keep the canonical unit files in `<PVEMONITOR_HOME>/systemd/`
- install as **copies** (not symlinks) into `/etc/systemd/system/`:
  ```sh
  sudo cp /opt/PVEmonitor/systemd/pvemonitor-*.service /etc/systemd/system/
  sudo cp /opt/PVEmonitor/systemd/pvemonitor-*.timer /etc/systemd/system/
  sudo systemctl daemon-reload
  sudo systemctl enable --now pvemonitor-collector.timer
  ```
- use copies (not symlinks) because systemd warns about symlinked unit files
- the checked-in copies in `<PVEMONITOR_HOME>/systemd/` are the source of truth; re-copy after editing them
- after editing, run `sudo systemctl daemon-reload && sudo systemctl restart pvemonitor-collector.timer`

### runtime/
All mutable runtime data under one subtree.

This is the most important requirement from your note.
Nothing runtime-related should spill into /var/lib, /var/log, or /tmp if you want a fully self-contained project folder. Use Python's `tempfile` module for any temporary file needs.

Subfolders:
- db/
  - SQLite DB and WAL/SHM sidecar files
- logs/
  - collector/service logs you want to keep in-project
  - log rotation via Python RotatingFileHandler (10 MB, 5 backups) or journald
- exports/
  - generated CSV exports or point-in-time JSON snapshots
- backups/
  - DB backup copies
  - retention: keep last 7 daily backups, delete older
  - also plan an off-host backup target (rsync/scp)

Reason:
- one directory tree to back up
- easy to rsync/move the entire monitor project
- easy to inspect current state without hunting through the system

Important SQLite note:
- WAL mode creates `metrics.sqlite3-wal` and `metrics.sqlite3-shm`
- these must live beside the DB file in runtime/db/
- do not separate them into another log directory

### tests/
Automated tests.

Test files:
- conftest.py — shared fixtures: temporary DB, mock subprocess output, sample data factories
- fixtures/ — canned command output for reproducible tests:
  - pvesh_status.json, pvesh_resources.json
  - rocm-smi_output.txt, sensors_output.txt

Focus tests on:
- schema creation and migration application
- guest up/down row rules
- NULL metrics for stopped guests
- rate calculations across reboots/stops and max interval guard
- rocm-smi parsing and unsupported-field handling
- lock acquisition, stale detection, and cleanup

Reason:
- these are the most error-prone parts of the collector
- fixtures make tests deterministic and fast without a real Proxmox host

## Recommended file path policy

Use these concrete runtime paths inside the project root:
- DB: `<PVEMONITOR_HOME>/runtime/db/metrics.sqlite3`
- collector log: `<PVEMONITOR_HOME>/runtime/logs/collector.log`
- collector error log: `<PVEMONITOR_HOME>/runtime/logs/collector.err.log`
- lock file: `<PVEMONITOR_HOME>/runtime/locks/collector.lock`
- exports: `<PVEMONITOR_HOME>/runtime/exports/`
- backups: `<PVEMONITOR_HOME>/runtime/backups/`

Use these code/config paths:
- venv Python: `<PVEMONITOR_HOME>/.venv/bin/python`
- requirements: `<PVEMONITOR_HOME>/requirements/`
- main code: `<PVEMONITOR_HOME>/src/pvemonitor/`
- SQL files: `<PVEMONITOR_HOME>/sql/`
- systemd units: `<PVEMONITOR_HOME>/systemd/`
- config files: `<PVEMONITOR_HOME>/config/`
- docs: `<PVEMONITOR_HOME>/docs/`

## Recommended project root contract

The project should depend on one environment variable:
- PVEMONITOR_HOME

Everything else should be derived from it.

Example:
- DB_PATH = $PVEMONITOR_HOME/runtime/db/metrics.sqlite3
- PYTHON_BIN = $PVEMONITOR_HOME/.venv/bin/python
- CONFIG_PATH = $PVEMONITOR_HOME/config/monitor.yaml
- QUERY_DIR = $PVEMONITOR_HOME/sql/queries

Reason:
- easier relocation
- easier backup/restore
- avoids hardcoded absolute paths across the codebase

## Runtime ownership and permissions

Because the collector will likely run as root on PVE, use a simple ownership model:
- owner: root:root
- project root mode: 0755
- runtime/db mode: 0750 or 0700
- database file mode: 0640 or 0600
- logs mode: 0640

If you later want non-root read-only reporting, you can relax read permissions on:
- runtime/db/
- runtime/logs/
- scripts/report-*.sh

## What should not go into the project root

Avoid placing these at the top level:
- loose .sqlite files
- ad hoc shell scripts
- random exports
- copied query snippets
- temporary debugging files

Everything should go into its matching subfolder so the tree stays understandable over time.

## Full review findings and improvements

I reviewed the earlier folder plan and found these issues to fix:

1. Python environment isolation was underspecified
- earlier plan had code but no explicit venv location or requirements files
- improvement: add `.venv/` and `requirements/` as first-class project components

2. Timer overlap risk was not addressed
- a 10-second systemd timer can trigger a new run before the prior one finishes
- improvement: add `locking.py`, `runtime/locks/collector.lock`, and require non-overlapping collection

3. CLI entrypoint shape was vague
- earlier plan depended on direct script execution only
- improvement: add `__main__.py` and `cli.py` so the project can run as `python -m pvemonitor`

4. Config loading was too implicit
- earlier plan mentioned YAML files but no module boundary for validation
- improvement: add `config.py` and make all paths derive from `PVEMONITOR_HOME`

5. SQL lifecycle was slightly incomplete
- earlier plan had schema and indexes but no room for derived views or optimization helpers
- improvement: add `003_views.sql` and `maintenance/optimize.sql`

6. Health and operational checks were too thin
- earlier plan had run and report scripts but no explicit health check
- improvement: add `scripts/healthcheck.sh`

7. Canonical unit files vs installed unit files needed clarification
- saying everything lives in one folder can conflict with how systemd actually works
- improvement: document that the source-of-truth units live in the project folder and are installed into `/etc/systemd/system/`

8. Backups inside the same root are convenient but not sufficient alone
- keeping backups only under `runtime/backups/` does not protect against project-root loss
- improvement: keep in-project backups for convenience, but plan an external backup target later

9. First-version layout was missing explicit dependency files
- improvement: include `.venv/` and `requirements/` in the minimum viable structure from day one

10. Root-owned project operations need explicit interpreter paths
- improvement: scripts and systemd should call `<PVEMONITOR_HOME>/.venv/bin/python` directly rather than relying on PATH or shell activation

These changes make the layout safer for long-term use on a Proxmox host and reduce the chance of deployment drift.

## Recommended first version for this project

If we build this now, I recommend these minimum files first:

```text
<PVEMONITOR_HOME>/
├── README.md
├── pyproject.toml
├── .gitignore
├── .venv/
├── requirements/
│   ├── base.txt
│   └── dev.txt
├── docs/
│   ├── design.md
│   ├── project-layout.md
│   └── plan-review.md
├── config/
│   ├── monitor.yaml
│   └── thresholds.yaml
├── src/pvemonitor/
│   ├── __init__.py
│   ├── __main__.py
│   ├── cli.py
│   ├── collector.py
│   ├── config.py
│   ├── db.py
│   ├── gpu.py
│   ├── host.py
│   ├── guests.py
│   ├── locking.py
│   ├── rates.py
│   ├── schema.py
│   ├── reporting.py
│   ├── api.py
│   ├── api_routes/
│   │   ├── __init__.py
│   │   ├── health.py
│   │   ├── host.py
│   │   └── guests.py
│   └── static/
│       └── dashboard.html
├── sql/
│   ├── schema/
│   │   ├── 000_version.sql
│   │   └── 001_init.sql
│   └── queries/
│       └── latest_guest_status.sql
├── scripts/
│   ├── bootstrap-venv.sh
│   ├── run-collector.sh
│   └── healthcheck.sh
├── systemd/
│   ├── README.md
│   ├── pvemonitor-collector.service
│   ├── pvemonitor-collector.timer
│   ├── pvemonitor-api.service
│   ├── pvemonitor-maintenance.service
│   └── pvemonitor-maintenance.timer
├── runtime/
│   ├── db/
│   ├── logs/
│   ├── backups/
│   └── locks/
└── tests/
    ├── conftest.py
    ├── test_schema.py
    ├── test_rate_calc.py
    ├── test_guest_state_handling.py
    ├── test_gpu_parsing.py
    ├── test_locking.py
    └── fixtures/
        ├── pvesh_status.json
        ├── pvesh_resources.json
        ├── rocm-smi_output.txt
        └── sensors_output.txt
```

This keeps the first implementation small while preserving a clean long-term structure.

## Recommendation

Use this exact pattern:
- one self-contained root folder named `PVEmonitor`
- `pyproject.toml` at the root for Python project metadata
- code under `src/`
- SQL under `sql/` (with migration versioning)
- systemd files under `systemd/` (with install README)
- all mutable data under `runtime/`
- tests with fixtures under `tests/`

That is the cleanest way to satisfy your requirement that everything lives in one project folder without turning the project root into a dump of mixed files.