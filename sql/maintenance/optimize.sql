-- optimize.sql
-- Rebuild indexes and update query planner statistics.
-- Run daily via maintenance timer.

PRAGMA wal_checkpoint(TRUNCATE);
PRAGMA optimize;
