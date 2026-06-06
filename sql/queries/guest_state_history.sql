-- guest_state_history.sql
-- Up/down state transitions for all guests.

WITH ordered AS (
  SELECT s.ts, s.epoch_s, gs.vmid, gs.guest_type, gs.name,
         gs.status, gs.is_running, gs.uptime_s,
         LAG(gs.is_running) OVER (
             PARTITION BY gs.vmid, gs.guest_type ORDER BY s.epoch_s
         ) AS prev_running
  FROM guest_samples gs
  JOIN samples s ON s.id = gs.sample_id
)
SELECT ts, vmid, guest_type, name, status, uptime_s,
       CASE
         WHEN prev_running IS NULL THEN 'first_seen'
         WHEN prev_running = 0 AND is_running = 1 THEN 'came_up'
         WHEN prev_running = 1 AND is_running = 0 THEN 'went_down'
         ELSE 'no_change'
       END AS transition
FROM ordered
WHERE prev_running IS NULL OR prev_running != is_running
ORDER BY ts;
