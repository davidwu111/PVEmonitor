# PVEmonitor Plan Review — Findings & Improvements

Reviewed: 2026-06-06
Documents reviewed: `pve_ai_monitor_design.md`, `project-layout.md`

## Severity Key
- **Critical** — Would cause silent data loss, permanent deadlock, or unrecoverable failure in production
- **High** — Design gap that will cause incorrect data, operational pain, or require rework
- **Medium** — Quality/operational improvement worth addressing before first deploy
- **Low** — Nice-to-have, can defer

---

## Critical Issues

### C1. Stale lock detection is missing (locking.py)

**Problem:** The design requires a lock file at `runtime/locks/collector.lock` to prevent timer-stacking, but specifies no mechanism to detect or clear a stale lock. If the collector crashes (OOM, SIGKILL, power loss), the lock file remains. Every subsequent timer invocation sees the lock and exits silently. The collector is now **permanently dead** until an operator notices and manually removes the lock file.

**Fix:**
1. Write PID + timestamp into the lock file.
2. On acquisition failure, read the existing lock, check if the PID is still alive, and check if the timestamp exceeds a maximum age (e.g., 5 × the sample interval).
3. If stale, log a warning, remove the old lock, and acquire a new one.
4. Add a `--force` flag to `cli.py` for manual override.
5. Add `healthcheck.sh` logic to detect a stale lock and alert.

### C2. Systemd timer activation mode can cause stacking

**Problem:** The timer unit design says "runs every 10s or 30s" but doesn't specify `OnUnitActiveSec` vs `OnActiveSec`. If `OnActiveSec=10s` is used, the timer fires 10s after the timer starts, NOT 10s after the previous run finishes. If a collection takes 12s (e.g., slow pvesh call under load), the timer fires a second instance while the first is still running. The lock file prevents corruption, but **both instances after the first silently fail**, and you lose samples.

**Fix:**
```
[Timer]
OnUnitActiveSec=10s   # fires 10s after service FINISHES
AccuracySec=1s
```
Also set `RemainAfterExit=no` in the service unit (the default for oneshot) to ensure the timer tracks completion correctly.

### C3. guest_samples lacks FK to guests table

**Problem:** `guest_samples` has `(vmid, guest_type)` columns but no `FOREIGN KEY (vmid, guest_type) REFERENCES guests(vmid, guest_type)`. SQLite requires `PRAGMA foreign_keys=ON` and an explicit FK. Without it, guest_samples can reference guests that don't exist — and if a guest is deleted from Proxmox, its samples become orphaned with no referential consistency check.

**Fix:** Add to schema `001_init.sql`:
```sql
FOREIGN KEY (vmid, guest_type) REFERENCES guests(vmid, guest_type)
```

### C4. host_metrics FK missing ON DELETE CASCADE

**Problem:** `host_metrics.sample_id REFERENCES samples(id)` is the only FK in the schema that **doesn't** have `ON DELETE CASCADE`. If a sample row is deleted for retention, the host_metrics row becomes orphaned or the delete fails.

**Fix:** `REFERENCES samples(id) ON DELETE CASCADE`

### C5. Rate calculation: no maximum interval guard

**Problem:** The rate calculation queries the previous `guest_samples` row for the same vmid/type and computes `delta_bytes / delta_seconds`. If the collector was stopped for hours (maintenance, crash, reboot), on restart it computes a rate over a huge interval. The bytes-per-second value will be **misleadingly low** and useless for trend analysis.

**Fix:**
1. Define a maximum rate interval (e.g., 120s for 10s sampling).
2. If `delta_seconds > max_rate_interval_s`, set rate fields to NULL and log a note.
3. This prevents garbage data from silently polluting the rates.

---

## High-Priority Issues

### H1. Schema assumes single GPU

**Problem:** The `host_metrics` table has scalar `gpu_name`, `gpu_busy_pct`, `gpu_temp_c`, `gpu_power_w`, `gpu_vram_used_pct` columns. Proxmox hosts commonly have multiple GPUs (e.g., multiple AMD Instinct cards passed through to different VMs, or an iGPU + dGPU). The sysfs glob `/sys/class/drm/card*/` explicitly handles multiple cards, and `rocm-smi` natively lists all AMD GPUs. The schema blocks multi-GPU data.

**Fix (choose one):**

*Option A (recommended for MVP):* Explicitly document single-GPU limitation. Collect all GPUs but store only the first/primary. Log a warning if multiple GPUs are detected.

*Option B (future):* Create a separate `host_gpu_samples` table:
```sql
CREATE TABLE host_gpu_samples (
    sample_id INTEGER NOT NULL REFERENCES samples(id) ON DELETE CASCADE,
    gpu_index INTEGER NOT NULL,
    gpu_name TEXT,
    gpu_busy_pct REAL,
    gpu_temp_c REAL,
    gpu_power_w REAL,
    gpu_vram_used_pct REAL,
    PRIMARY KEY (sample_id, gpu_index)
);
```

**Recommendation:** Go with Option A for the first version, add Option B in Phase 2. Document the limitation explicitly in the design doc.

### H2. No database migration strategy

**Problem:** The SQL files are numbered (`001_init.sql`, `002_indexes.sql`, `003_views.sql`) but there is no migration tracking table, no version number stored in the database, and no logic in `db.py`/`schema.py` to check which migrations have been applied. When the schema evolves, there's no safe way to apply incremental changes without risking re-running DDL.

**Fix:**
1. Add a `schema_version` table:
```sql
CREATE TABLE schema_version (
    version INTEGER PRIMARY KEY,
    applied_ts TEXT NOT NULL DEFAULT (datetime('now')),
    filename TEXT NOT NULL
);
```
2. In `db.py`, read the current `MAX(version)` and apply only unapplied migration files in order.
3. Wrap each migration in a transaction.
4. Document the migration contract: migrations are append-only and never modified after being applied.

### H3. pvesh and rocm-smi calls have no timeout

**Problem:** `pvesh` can hang indefinitely if the Proxmox API daemon is overloaded or unresponsive. `rocm-smi` can hang on certain GPU firmware states. If these subprocess calls have no timeout, one hung call blocks the entire collection run forever. Since the lock is held, no further samples are collected.

**Fix:**
1. Wrap all subprocess calls with `subprocess.run(..., timeout=N)`.
2. Recommended timeouts:
   - `pvesh get /nodes/pve/status`: 5s
   - `pvesh get /cluster/resources --type vm`: 10s
   - `pvesh get /nodes/pve/{qemu,lxc}/<vmid>/status/current`: 5s per guest
   - `rocm-smi`: 5s
3. On timeout, catch `subprocess.TimeoutExpired`, log to `collector_errors`, and continue with partial data.

### H4. Counter wrap-around not handled (disk/network bytes)

**Problem:** Proxmox reports cumulative disk/network byte counters. These are 64-bit on modern systems, but if a VM is migrated or its config is reset, counters can jump backwards. The design says "if counters move backwards, store NULL" but doesn't handle the case where counters **wrap around** at 2^32 or 2^64 due to guest reboot or Proxmox internals. The symptom is a huge negative delta, not a simple backward step.

**Fix:**
1. Before computing delta, check if `current < previous`. If so, store NULL (counters reset).
2. Also check if delta is unreasonably large (e.g., more bytes than physically possible on the network/disk interface in the interval). This catches counter bugs. Define a sanity ceiling: `delta_bytes > (interface_max_Bps * interval_s * 2)` → store NULL.

### H5. Dynamic guest discovery missing

**Problem:** The design says "for each guest, collect or derive status" but the inventory is fetched once per run from `/cluster/resources`. If a guest is created or deleted **between** the inventory fetch and the per-guest detail loop, the script either skips a guest (new one created after inventory) or errors on a deleted guest.

**Fix:**
1. Fetch inventory first; that's the snapshot of guests to process for this sample.
2. If a guest disappears between inventory and detail fetch (pvesh returns error), still insert a `guest_samples` row with `status='unknown'` and log to `collector_errors`.
3. If a guest appears in the detail response that wasn't in the inventory snapshot, it's fine — skip it and it'll be caught next run.
4. Upsert into `guests` table on every run so new guests are tracked immediately.

### H6. No log rotation

**Problem:** `collector.log` at 10s intervals grows unbounded. With timestamp + status lines, a single run produces ~50-200 lines depending on guest count. At 6 runs/minute × 100 lines × 60 min × 24 hours = ~864,000 lines/day. This is unsustainable.

**Fix:**
1. Use Python's `RotatingFileHandler` with a max size (e.g., 10 MB) and 5 backups.
2. Or, since the project aims for stdlib-first, use `logging.handlers.RotatingFileHandler`.
3. Alternatively, since systemd captures stdout/stderr via `journald`, log to stdout only and let journald handle rotation. This also avoids duplicating the log (once in `collector.log`, once in journal). The design doc needs to choose one approach.

---

## Medium-Priority Issues

### M1. Config schema is undefined

**Problem:** `monitor.yaml` and `thresholds.yaml` are defined as files but their exact keys, types, and defaults are never specified. `config.py` is meant to validate YAML but without a schema, validation is weak.

**Fix:** Define the full config schema in the design doc:

```yaml
# monitor.yaml
monitor:
  sample_interval_s: 10
  hostname_override: null  # defaults to socket.gethostname()
  collection_timeout_s: 25  # max time for one full collection

paths:
  db: runtime/db/metrics.sqlite3
  log: runtime/logs/collector.log
  lock: runtime/locks/collector.lock

collection:
  host_enabled: true
  guests_enabled: true
  gpu_enabled: true
  psi_enabled: true
  top_process_enabled: true

gpu:
  rocm_smi_bin: /usr/bin/rocm-smi
  sysfs_fallback: true

rate_calculation:
  max_interval_s: 120

logging:
  level: INFO
  max_log_bytes: 10485760
  backup_count: 5
```

### M2. healthcheck.sh role unclear

**Problem:** `healthcheck.sh` is listed but its contract is undefined. Should it exit 0 on healthy, non-zero on unhealthy? Should it output JSON for monitoring tools? Should it be called by systemd or cron?

**Fix:**
1. Define the contract: exits 0 if collector is healthy, 1 if degraded, 2 if dead.
2. Checks to perform:
   - Is the lock file stale? (PID missing or too old)
   - Is the DB file present and readable?
   - When was the most recent sample? (query `MAX(epoch_s)` from samples)
   - Is the most recent sample within N × `sample_interval_s` seconds?
   - Is `rocm-smi` responding?
   - Is `pvesh` responding?
3. Output a one-line summary + optional JSON with `--json` flag.

### M3. WAL checkpoint strategy underspecified

**Problem:** WAL mode is enabled, but checkpoints are never triggered. The WAL file grows continuously. `wal_checkpoint.sql` exists in `sql/maintenance/` but there's no schedule or trigger for it.

**Fix:**
1. Add a periodic WAL checkpoint to the collector: after every N runs (e.g., every 60 runs = every 10 min at 10s interval), execute `PRAGMA wal_checkpoint(PASSIVE)`.
2. The `pvemonitor-maintenance.timer` can also run `PRAGMA wal_checkpoint(TRUNCATE)` and `PRAGMA optimize` daily.
3. Document the expected WAL size under normal operation.

### M4. tools/ vs scripts/ distinction is fuzzy

**Problem:** `tools/` has "operator notes" and `scripts/` has "shell wrappers." A new operator won't know which to reach for.

**Fix:** Merge them. Put all executable scripts in `scripts/` and move the reference notes (`sqlite-shell-notes.txt`, `example-commands.md`) into `docs/`. Delete `tools/`.

### M5. Missing pyproject.toml

**Problem:** Even for an internal-only stdlib project, lacking `pyproject.toml` means:
- No standard way to run tests (`python -m pytest`)
- No declared Python version requirement
- No way to declare the package for `pip install -e .`
- IDE tooling (pylance, mypy) won't recognize the package structure

**Fix:** Add a minimal `pyproject.toml`:
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

### M6. threshold.yaml inconsistency

**Problem:** `thresholds.yaml` is in the project layout and described as "Operator-editable settings" but the design document (section 13) says thresholds are "to consider later." This is contradictory — if thresholds aren't implemented, the file shouldn't exist.

**Fix:** Either remove `thresholds.yaml` from the first-version layout, or add a `thresholds.yaml` with a comment header: `# Thresholds not yet enforced — defined for future alerting.` The collector should parse it but only log warnings at threshold crossings, not take action.

### M7. Missing test infrastructure

**Problem:** `tests/` is listed with test files but no `conftest.py`, no shared fixtures, and no mention of how to get a test database or mock Proxmox responses.

**Fix:** Add to the first-version layout:
```
tests/
├── conftest.py          # shared fixtures: tmp DB, mock pvesh output
├── fixtures/
│   ├── pvesh_status.json
│   ├── pvesh_resources.json
│   ├── rocm-smi_output.txt
│   └── sensors_output.txt
├── test_schema.py
├── test_rate_calc.py
├── test_guest_state.py
├── test_gpu_parsing.py
└── test_locking.py
```

---

## Low-Priority Issues

### L1. VMID reuse not tracked historically

If VM 104 is deleted and a new VM 104 is created, the `guests` table loses the old VM's history. The `guests` table `PRIMARY KEY (vmid, guest_type)` means only one identity per VMID. You'd need a surrogate key + `first_seen`/`last_seen` to track multiple incarnations. This is a Phase 3 concern.

### L2. Clock skew / NTP adjustment not handled

If NTP adjusts the clock backwards, `epoch_s` in samples could go backwards. The `ORDER BY epoch_s` queries would return out-of-order results. Mitigation: use `INSERT` order (autoincrement `id`) as the canonical ordering, not `epoch_s`. The sample queries already use `s.id` for joins, which is correct.

### L3. Single top process instead of top-N

`top_cpu_process_name/pid/pct` records only one process. For AI workloads, you often want to see the top 3-5 processes to identify co-tenants. This is a Phase 2 improvement.

### L4. No Makefile or task runner

`scripts/bootstrap-venv.sh`, `run-collector.sh`, etc. require remembering script names. A `Makefile` or `justfile` with targets like `make setup`, `make collect`, `make health` would reduce operator error.

### L5. backup-db.sh uses sqlite3 .backup but no retention

Backups accumulate indefinitely under `runtime/backups/`. Add a retention policy (e.g., keep last 7 daily backups, delete older).

---

## Design Document — Section-by-Section Notes

### Section 3 (Metrics): Missing definition for `top_cpu_process_*`
The "Recommended extras" include top process fields but don't specify how they're collected. Add: "Use `ps aux --sort=-%cpu | head -2 | tail -1` or parse `/proc/loadavg` + iterate `/proc/*/stat`."

### Section 5 (Sampling): Add explicit timeout note
Add: "Each collection run must complete within 25 seconds. If a collection takes longer, the subprocess calls are timed out individually, and partial data is committed."

### Section 6 (Schema): Add explicit PRAGMAs in schema file
The PRAGMAs listed in section 12 should also appear in `sql/schema/001_init.sql` as a header comment block so the schema file is self-contained.

### Section 9 (Script behavior): Reorder step 5
Step 5 says "For each guest, collect or derive status and is_running." But step 4 already collects the full inventory including status. Clarify: the inventory provides baseline status; the per-guest detail call (step 6) provides richer runtime metrics for running guests only.

### Section 10 (Implementation): Missing module `cli.py` subcommands
The suggested modules list `cli.py` but doesn't specify the subcommand interface. Add:
```
pvemonitor collect        # one collection run
pvemonitor init-db        # initialize schema
pvemonitor report-latest  # latest host+guest summary
pvemonitor report-gpu     # recent GPU trend
pvemonitor health         # health check (same logic as healthcheck.sh)
```

### Section 12 (Retention): Add specific VACUUM schedule
"periodic auto-vacuum or scheduled VACUUM weekly" — make this specific: "Run `PRAGMA optimize` daily via maintenance timer. Run full VACUUM monthly during a low-activity window."

### Section 15 (Deployment): Add step 0 — validate host prerequisites
Before creating the project root, verify: Python 3.11+, `pvesh` functional, `rocm-smi` or sysfs GPU path readable, `sensors` returning data, write permission on target directory.

---

## Project Layout — Specific Corrections

1. **Remove `tools/`** — merge contents into `docs/` (reference notes) and `scripts/` (executable helpers).
2. **Add `pyproject.toml`** to the project root.
3. **Add `tests/conftest.py`** to the first-version layout.
4. **Add `tests/fixtures/`** with sample JSON/text output files.
5. **Rename `constraints.txt`** → remove it; use `base.txt` with pinned versions instead. Constraints files are for downstream consumers, not for the project itself.
6. **Add `Makefile`** as optional low-priority convenience.
7. **Add `config/thresholds.yaml`** only if thresholds are actually checked; otherwise omit from v1.
8. **Add `sql/schema/000_version.sql`** for the migration tracking table.
9. **Rename `runtime/tmp/`** → document that it's for `tempfile` module output, or remove it and use `tempfile.gettempdir()`.
10. **Clarification for systemd/**: Add a `README.md` inside `systemd/` explaining the install command: `sudo cp systemd/pvemonitor-* /etc/systemd/system/ && sudo systemctl daemon-reload`.

---

## Summary: What Must Be Fixed Before Coding

| # | Issue | Priority | Effort |
|---|-------|----------|--------|
| 1 | Stale lock detection + PID/timestamp in lock file | Critical | Small |
| 2 | Systemd timer `OnUnitActiveSec` + docs | Critical | Trivial |
| 3 | FK constraint guest_samples → guests | Critical | Trivial |
| 4 | FK ON DELETE CASCADE on host_metrics | Critical | Trivial |
| 5 | Max rate interval guard | Critical | Small |
| 6 | Subprocess timeouts on pvesh/rocm-smi | High | Small |
| 7 | Single-GPU limitation documented explicitly | High | Trivial |
| 8 | Migration versioning (schema_version table) | High | Medium |
| 9 | Counter wrap/backwards sanity check in rates.py | High | Small |
| 10 | Dynamic guest discovery handling | High | Small |
| 11 | Log rotation or journald-only strategy | High | Small |
| 12 | Config schema defined in design doc | Medium | Medium |
| 13 | healthcheck.sh contract defined | Medium | Small |
| 14 | WAL checkpoint schedule | Medium | Small |
| 15 | Add pyproject.toml | Medium | Small |
| 16 | Add conftest.py + fixtures | Medium | Small |
| 17 | Merge tools/ into docs/ + scripts/ | Medium | Trivial |
| 18 | Resolve threshold.yaml inconsistency | Medium | Trivial |

All 18 items are actionable and should be addressed before or during the first coding pass.

## Resolution Status (2026-06-06)

All 18 issues resolved during implementation:

| # | Issue | Resolution |
|---|-------|------------|
| 1 | Stale lock detection | `locking.py`: PID+timestamp, dead PID check, age timeout, `--force` flag |
| 2 | Systemd timer stacking | `OnUnitActiveSec=10s` in timer unit, `RemainAfterExit=no` in service |
| 3 | FK guest_samples → guests | Added `FOREIGN KEY (vmid, guest_type) REFERENCES guests(vmid, guest_type)` |
| 4 | FK ON DELETE CASCADE | Added `ON DELETE CASCADE` to host_metrics FK |
| 5 | Max rate interval guard | `rates.py`: 120s max interval, NULL suppression when exceeded |
| 6 | Subprocess timeouts | All pvesh/rocm-smi calls use `timeout=` (2-10s), catch `TimeoutExpired` |
| 7 | Single-GPU limitation | Documented in README and implementation-status.md |
| 8 | Migration versioning | `schema_version` table + `db.ensure_schema()` applies unapplied migrations in order |
| 9 | Counter wrap/backwards | `rates.py`: NULL on counter reset, physical ceiling sanity check |
| 10 | Dynamic guest discovery | Inventory snapshot per run; missing guests → `status='unknown'` |
| 11 | Log rotation | `RotatingFileHandler` (10 MB, 5 backups) in `util.setup_logging()` |
| 12 | Config schema | Full YAML schema in `monitor.yaml` with all keys documented |
| 13 | healthcheck contract | CLI `health` exits 0/1/2, `--json` flag; checks DB, lock, sample age |
| 14 | WAL checkpoint | Passive checkpoint every 60 runs; TRUNCATE + optimize daily via maintenance timer |
| 15 | pyproject.toml | Added with project metadata, entrypoint, build-system |
| 16 | conftest.py + fixtures | Added temp_db, seeded_db fixtures + 4 JSON/TXT sample files |
| 17 | tools/ merged | No `tools/` dir; all scripts in `scripts/`, docs in `docs/` |
| 18 | thresholds.yaml | Created with comment header noting v1 warning-only behavior |
