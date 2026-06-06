-- guest_cpu_top.sql
-- Top guest CPU consumers in the last hour (running guests only).

SELECT gs.vmid, gs.guest_type, gs.name,
       ROUND(AVG(gs.cpu_host_pct), 2) AS avg_cpu_host_pct,
       ROUND(MAX(gs.cpu_host_pct), 2) AS max_cpu_host_pct
FROM guest_samples gs
JOIN samples s ON s.id = gs.sample_id
WHERE s.epoch_s >= strftime('%s','now') - 3600
  AND gs.status = 'running'
GROUP BY gs.vmid, gs.guest_type, gs.name
ORDER BY avg_cpu_host_pct DESC;
