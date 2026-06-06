-- vacuum.sql
-- Full database vacuum to reclaim space.
-- Run monthly during a known low-activity window.
-- This is a heavy operation that locks the database.

VACUUM;
