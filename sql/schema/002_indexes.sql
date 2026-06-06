-- 002_indexes.sql
-- Additional indexes for common query patterns.

-- Fast lookup of latest guest sample per guest
CREATE INDEX IF NOT EXISTS idx_guest_samples_vmid_type_sample_desc
    ON guest_samples(vmid, guest_type, sample_id DESC);

-- Fast time-range queries on guest samples
CREATE INDEX IF NOT EXISTS idx_guest_samples_sample_vmid
    ON guest_samples(sample_id, vmid, guest_type);
