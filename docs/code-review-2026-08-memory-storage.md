# Code Review — In-Memory Telemetry Storage (2026-08-10)

Reviewed after implementation of the in-memory storage change
(`storage.py`, collector/API refactor, config, systemd, scripts, tests).

## Severity Key
- **Critical** — Silent data loss or unrecoverable failure in production
- **High** — Wrong behavior, lost data, or operational breakage
- **Medium** — Quality/robustness issue worth fixing
- **Low** — Cosmetic or nice-to-have

## Findings

### H1. Debug `collect` could shadow the live snapshot history — Fixed

**Problem:** A one-shot `pvemonitor collect` (throwaway store) called
`maybe_snapshot()`, which would write a snapshot of the debug run into the
snapshot directory. On the next service restart, `serve` would load that
snapshot, replacing the real history with a single throwaway sample.

**Fix:** `collect_once()` gained a `write_snapshot` flag; the debug path
(`run_collection()`) passes `False` and can never write a snapshot. Covered by
`test_debug_collect_does_not_write_snapshot`.

### H2. Service never configured file logging — Fixed

**Problem:** The old timer-driven collector called `setup_logging()` on every
run. The new `serve` process did not, so collector logs were not written to
`collector.log`.

**Fix:** `api.py` lifespan calls `setup_logging()` before the collection loop
starts (idempotent, so one-shot `collect` is unaffected).

### M1. CLI health/reporting crashed on unreadable snapshots — Fixed

**Problem:** `report-latest`, `report-gpu`, and the `health` snapshot fallback
would raise and traceback if the newest snapshot file was corrupt.

**Fix:** `_open_latest_snapshot()` catches `sqlite3.Error` and returns None
(report commands show a friendly message); CLI `health` marks the store dead
with the snapshot path instead of crashing.

### M2. Unused imports left behind — Fixed

**Problem:** `collector.py` imported the unused module `__version__`;
`reporting.py` imported unused `typing.Any`.

**Fix:** Removed both.

### L1. Dashboard element id `db-size` now shows memory — Left as-is

The element id is unchanged but the label reads `mem: X MB` from
`memory_used_bytes`. Cosmetic only; renaming the id would add HTML churn with
no functional benefit.

## Verification

- Full test suite: **89 passed** (76 baseline + 13 new: storage unit tests,
  service/API integration tests, config validation, snapshot round-trip,
  memory-cap eviction, debug-collect regression).
- End-to-end smoke test on the host: `serve` collected real host metrics,
  wrote a shutdown snapshot, and `report-latest`/`health` read it correctly.
- Shell scripts pass `bash -n`; `config/monitor.yaml` parses; all modules
  compile and import cleanly.

## Residual risks (accepted, documented)

- The memory cap is enforced with per-row **estimates**, not exact RSS; the
  health endpoint reports `memory_used_bytes` as an estimate.
- History beyond the newest snapshot is lost on restart without a snapshot
  (documented in README "Known limitations").
- CLI reports are at most one snapshot interval stale by design.
