-- wal_checkpoint.sql
-- Passive WAL checkpoint (non-blocking).
-- Run frequently (every ~60 collection runs).

PRAGMA wal_checkpoint(PASSIVE);
