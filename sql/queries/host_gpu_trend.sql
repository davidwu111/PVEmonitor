-- host_gpu_trend.sql
-- GPU temperature, busy%, power, and VRAM over a time window.

SELECT s.ts, h.gpu_temp_c, h.gpu_busy_pct, h.gpu_power_w, h.gpu_vram_used_pct
FROM samples s
JOIN host_metrics h ON h.sample_id = s.id
WHERE s.ts >= datetime('now', '-2 hours')
ORDER BY s.ts;
