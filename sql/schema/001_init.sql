-- 001_init.sql
-- Initial schema: core tables for samples, host metrics, guests, and guest samples.
--
-- Recommended PRAGMAs (set by db.py on every connection):
--   PRAGMA journal_mode=WAL;
--   PRAGMA synchronous=NORMAL;
--   PRAGMA foreign_keys=ON;
--   PRAGMA temp_store=MEMORY;
--   PRAGMA busy_timeout=5000;

-- One row per collection run
CREATE TABLE IF NOT EXISTS samples (
    id                  INTEGER PRIMARY KEY,
    ts                  TEXT NOT NULL,              -- RFC3339 UTC, e.g. 2026-06-06T04:12:30Z
    epoch_s             INTEGER NOT NULL,           -- Unix timestamp in UTC
    hostname            TEXT NOT NULL,
    collector_version   TEXT,
    collection_ms       INTEGER,                    -- how long this collection took (ms)
    error_count         INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_samples_ts ON samples(ts);
CREATE INDEX IF NOT EXISTS idx_samples_epoch ON samples(epoch_s);

-- One row per sample: host-level metrics
CREATE TABLE IF NOT EXISTS host_metrics (
    sample_id           INTEGER PRIMARY KEY REFERENCES samples(id) ON DELETE CASCADE,

    -- Load
    load1               REAL,
    load5               REAL,
    load15              REAL,

    -- CPU breakdown (from top)
    cpu_usage_pct       REAL,
    cpu_user_pct        REAL,
    cpu_system_pct      REAL,
    cpu_iowait_pct      REAL,
    cpu_idle_pct        REAL,
    cpu_temp_c          REAL,

    -- Memory
    mem_total_bytes     INTEGER,
    mem_used_bytes      INTEGER,
    mem_free_bytes      INTEGER,
    swap_total_bytes    INTEGER,
    swap_used_bytes     INTEGER,

    -- Root filesystem
    rootfs_total_bytes  INTEGER,
    rootfs_used_bytes   INTEGER,
    rootfs_free_bytes   INTEGER,

    -- GPU (single GPU in v1; multi-GPU table planned for Phase 2)
    gpu_name            TEXT,
    gpu_busy_pct        REAL,
    gpu_temp_c          REAL,
    gpu_power_w         REAL,
    gpu_vram_used_pct   REAL,

    -- PSI (Pressure Stall Information)
    psi_cpu_some_avg10  REAL,
    psi_cpu_some_avg60  REAL,
    psi_cpu_some_avg300 REAL,
    psi_cpu_full_avg10  REAL,
    psi_cpu_full_avg60  REAL,
    psi_cpu_full_avg300 REAL,
    psi_io_some_avg10   REAL,
    psi_io_some_avg60   REAL,
    psi_io_some_avg300  REAL,
    psi_io_full_avg10   REAL,
    psi_io_full_avg60   REAL,
    psi_io_full_avg300  REAL,
    psi_mem_some_avg10  REAL,
    psi_mem_some_avg60  REAL,
    psi_mem_some_avg300 REAL,
    psi_mem_full_avg10  REAL,
    psi_mem_full_avg60  REAL,
    psi_mem_full_avg300 REAL,

    -- Top CPU process
    top_cpu_process_name TEXT,
    top_cpu_process_pid  INTEGER,
    top_cpu_process_pct  REAL
);

-- Guest identity (dimension table)
CREATE TABLE IF NOT EXISTS guests (
    vmid            INTEGER NOT NULL,
    guest_type      TEXT NOT NULL,                  -- 'qemu' or 'lxc'
    first_seen_ts   TEXT NOT NULL,
    last_seen_ts    TEXT NOT NULL,
    current_name    TEXT,
    PRIMARY KEY (vmid, guest_type)
);

-- One row per guest per sample
CREATE TABLE IF NOT EXISTS guest_samples (
    sample_id           INTEGER NOT NULL REFERENCES samples(id) ON DELETE CASCADE,
    vmid                INTEGER NOT NULL,
    guest_type          TEXT NOT NULL,
    name                TEXT,
    status              TEXT,                       -- running, stopped, paused, suspended, unknown
    is_running          INTEGER NOT NULL,           -- 1 or 0

    -- Runtime metrics (NULL when guest is not running)
    uptime_s            INTEGER,
    maxcpu              INTEGER,
    cpu_host_pct        REAL,
    cpu_of_allocated_pct REAL,
    mem_bytes           INTEGER,
    maxmem_bytes        INTEGER,
    mem_pct             REAL,
    swap_bytes          INTEGER,

    -- Cumulative counters
    diskread_bytes_total  INTEGER,
    diskwrite_bytes_total INTEGER,
    netin_bytes_total     INTEGER,
    netout_bytes_total    INTEGER,

    -- Computed rates (bytes per second)
    diskread_bps        REAL,
    diskwrite_bps       REAL,
    netin_bps           REAL,
    netout_bps          REAL,

    -- LXC pressure metrics (NULL for qemu)
    pressurecpusome     REAL,
    pressurecpufull     REAL,
    pressureiosome      REAL,
    pressureiofull      REAL,
    pressurememorysome  REAL,
    pressurememoryfull  REAL,

    -- QEMU guest-agent fields (NULL when not available)
    freemem_bytes       INTEGER,
    balloon_actual_bytes INTEGER,
    guest_agent_enabled INTEGER,

    PRIMARY KEY (sample_id, vmid, guest_type),
    FOREIGN KEY (vmid, guest_type) REFERENCES guests(vmid, guest_type)
);

CREATE INDEX IF NOT EXISTS idx_guest_samples_vmid_type_sample
    ON guest_samples(vmid, guest_type, sample_id);
CREATE INDEX IF NOT EXISTS idx_guest_samples_status
    ON guest_samples(status);

-- Partial failures during collection
CREATE TABLE IF NOT EXISTS collector_errors (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    sample_id   INTEGER NOT NULL REFERENCES samples(id) ON DELETE CASCADE,
    scope       TEXT NOT NULL,                      -- e.g. 'host', 'gpu', 'guest:104'
    vmid        INTEGER,
    guest_type  TEXT,
    message     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_collector_errors_sample
    ON collector_errors(sample_id);
