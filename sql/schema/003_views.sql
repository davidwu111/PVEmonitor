-- 003_views.sql
-- Convenience views for common queries.

-- Latest sample ID
CREATE VIEW IF NOT EXISTS latest_sample AS
SELECT MAX(id) AS id FROM samples;

-- Current guest status (latest sample for each guest)
CREATE VIEW IF NOT EXISTS current_guest_status AS
SELECT s.ts, gs.vmid, gs.guest_type, gs.name, gs.status, gs.is_running,
       gs.uptime_s, gs.cpu_host_pct, gs.cpu_of_allocated_pct,
       gs.mem_bytes, gs.mem_pct, gs.maxmem_bytes,
       gs.diskread_bps, gs.diskwrite_bps, gs.netin_bps, gs.netout_bps
FROM guest_samples gs
JOIN samples s ON s.id = gs.sample_id
WHERE gs.sample_id = (SELECT id FROM latest_sample)
ORDER BY gs.guest_type, gs.vmid;

-- Host metrics with timestamps (convenience join)
CREATE VIEW IF NOT EXISTS host_metrics_ts AS
SELECT s.ts, s.epoch_s, s.hostname, s.collection_ms,
       h.*
FROM samples s
JOIN host_metrics h ON h.sample_id = s.id;
