-- 000_version.sql
-- Migration tracking table. Must be the first migration applied.

CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER PRIMARY KEY,
    applied_ts  TEXT NOT NULL DEFAULT (datetime('now')),
    filename    TEXT NOT NULL
);
