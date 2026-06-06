-- latest_guest_status.sql
-- Current status summary for all guests (from the most recent sample).

SELECT s.ts, gs.vmid, gs.guest_type, gs.name, gs.status, gs.is_running,
       gs.uptime_s, gs.cpu_host_pct, gs.cpu_of_allocated_pct,
       gs.mem_bytes, gs.mem_pct
FROM guest_samples gs
JOIN samples s ON s.id = gs.sample_id
WHERE s.id = (SELECT MAX(id) FROM samples)
ORDER BY gs.guest_type, gs.vmid;
