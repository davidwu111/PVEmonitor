-- 004_rollups.sql
-- Aggregated host/guest rollups for long-range charts and historical retention.

CREATE TABLE IF NOT EXISTS host_rollups (
    resolution_s         INTEGER NOT NULL,
    bucket_epoch_s       INTEGER NOT NULL,
    ts                   TEXT NOT NULL,
    hostname             TEXT,
    sample_count         INTEGER NOT NULL,

    load1                REAL,
    load5                REAL,
    load15               REAL,
    cpu_usage_pct        REAL,
    cpu_user_pct         REAL,
    cpu_system_pct       REAL,
    cpu_iowait_pct       REAL,
    cpu_idle_pct         REAL,
    cpu_temp_c           REAL,

    mem_total_bytes      INTEGER,
    mem_used_bytes       INTEGER,
    mem_free_bytes       INTEGER,
    swap_total_bytes     INTEGER,
    swap_used_bytes      INTEGER,

    rootfs_total_bytes   INTEGER,
    rootfs_used_bytes    INTEGER,
    rootfs_free_bytes    INTEGER,

    gpu_name             TEXT,
    gpu_busy_pct         REAL,
    gpu_temp_c           REAL,
    gpu_power_w          REAL,
    gpu_vram_used_pct    REAL,

    psi_cpu_some_avg10   REAL,
    psi_cpu_some_avg60   REAL,
    psi_cpu_some_avg300  REAL,
    psi_cpu_full_avg10   REAL,
    psi_cpu_full_avg60   REAL,
    psi_cpu_full_avg300  REAL,
    psi_io_some_avg10    REAL,
    psi_io_some_avg60    REAL,
    psi_io_some_avg300   REAL,
    psi_io_full_avg10    REAL,
    psi_io_full_avg60    REAL,
    psi_io_full_avg300   REAL,
    psi_mem_some_avg10   REAL,
    psi_mem_some_avg60   REAL,
    psi_mem_some_avg300  REAL,
    psi_mem_full_avg10   REAL,
    psi_mem_full_avg60   REAL,
    psi_mem_full_avg300  REAL,

    top_cpu_process_name TEXT,
    top_cpu_process_pid  INTEGER,
    top_cpu_process_pct  REAL,

    PRIMARY KEY (resolution_s, bucket_epoch_s)
);

CREATE INDEX IF NOT EXISTS idx_host_rollups_resolution_epoch
    ON host_rollups(resolution_s, bucket_epoch_s);

CREATE TABLE IF NOT EXISTS guest_rollups (
    resolution_s         INTEGER NOT NULL,
    bucket_epoch_s       INTEGER NOT NULL,
    ts                   TEXT NOT NULL,
    vmid                 INTEGER NOT NULL,
    guest_type           TEXT NOT NULL,
    name                 TEXT,
    status               TEXT,
    is_running           INTEGER,
    sample_count         INTEGER NOT NULL,
    uptime_s             INTEGER,
    maxcpu               INTEGER,
    cpu_host_pct         REAL,
    cpu_of_allocated_pct REAL,
    mem_bytes            INTEGER,
    maxmem_bytes         INTEGER,
    mem_pct              REAL,
    diskread_bps         REAL,
    diskwrite_bps        REAL,
    netin_bps            REAL,
    netout_bps           REAL,

    PRIMARY KEY (resolution_s, bucket_epoch_s, vmid, guest_type)
);

CREATE INDEX IF NOT EXISTS idx_guest_rollups_vmid_type_resolution_epoch
    ON guest_rollups(vmid, guest_type, resolution_s, bucket_epoch_s);
