# Changelog

## Unreleased — Linux and macOS launchers

- Add a Bash seven-option menu launcher for Linux and a Finder-friendly macOS `.command` wrapper.
- Keep the Windows launcher and shared Python download core unchanged.
- Add four offline Unix launcher tests covering CLI startup, a path containing spaces, safe full-download cancellation, and invalid menu choices.
- Expand CI from Ubuntu/Windows to Ubuntu/Windows/macOS for Python 3.10 and 3.13.
- Document Python requirements and OS-specific startup with explicit limits on unverified live Mac/Linux downloads.


## 5.0.2-rc3 (2026-11-16) — Offline cache metadata quality correction

- Validate numeric Status badges in new HTML metadata and report uncorrected numeric Status values alongside Function/Collection anomalies.
- Repair malformed metadata already stored in SQLite using unambiguous saved Function-listing evidence; never guess unknown Collections or Statuses.
- Preserve cached metadata's original last-check timestamp, file URLs, local files, and all downloaded editions.
- Keep an inspectable CSV log of field-level corrections, with safe, repeatable cleanup on subsequent runs.
- Normalize existing selection manifests and merged HTML catalog entries without requiring re-download or site requests.
- Rebuild an explicitly offline-scoped coverage report when rebuilding the offline catalog (Windows menu 6).
- Six new regression tests covering repairs, ambiguity, numeric Status, unknown textual Status, old JSON manifests, and offline reporting (48 tests total).


## 5.0.1-rc2 (2026-11-15) — Corrections from user-supplied Windows reports

- Exclude the non-manual `/field-manual-vault/citations` page from discovery and old discovery caches.
- Validate numeric Function and Collection metadata badges; repair older cache entries from their selected Function filter when appropriate.
- Flag suspicious cached metadata in coverage TXT/JSON reports instead of counting numbers as valid site Functions.
- Remove stale metadata-error CSVs after a clean metadata build.
- Document that the local hash auditor may report identical hard links as duplicates without indicating extra disk usage.
- Added offline regression tests for these cases.


## 5.0.0-rc1 (2026-11-15) — GitHub release candidate

- Add incremental source listing refresh for selective mode with automatic seven-day freshness checks; menu 7 forces a selected-category refresh without binary downloads.
- Fetch only new detail URLs during incremental updates; optionally recheck a bounded batch of old metadata (default 25 entries at least 30 days old).
- Persist page-by-page listing progress in SQLite and resume from the next page after an interruption; keep a bounded metadata worker queue.
- Display listing/metadata/download progress with counts, elapsed time, retries, and the per-run actual-byte budget.
- Add Windows discovery pacing profiles, still globally rate-limited to no more than one new HTML request start every 0.5 seconds.
- Add pre-download size estimation from local files, cached sizes, and optional bounded HTTP HEAD samples; stop probing when HEAD appears unsupported.
- Produce inspectable JSON/TXT catalog coverage reports showing known parse gaps and listing failures without claiming complete enumeration of 4,215 titles.
- Preserve older known URLs when site filters fluctuate; mark incomplete runs to ensure retry rather than treating a partial refresh as fresh.
- Preserve V2–V4 manifest compatibility and the existing organized folder hierarchy; retain menu choices 1–6.
- Add extensive offline regression tests, documentation, privacy/release notes, and the Windows menu 7 option.

**Validation:** Offline tests pass; real-site V5 metadata/download performance and HTTP HEAD support have not been verified in the build environment. Do not claim a measured speedup or complete coverage until tested live.

## 4.0.0 (2026-11-15)

- Shared cross-category SQLite metadata cache and import of earlier JSON manifests.
- Seven-day category listing cache; bounded two-worker metadata fetches with global polite pacing.
- Reuse unchanged local SHA-256 file hashes.
- Cache known missing 404/410 file failures for seven days.
- Preserve unrecognized existing files rather than silently overwriting them.
- Add a completely offline HTML catalog rebuild, GitHub CI and documentation.

**Known limits:** Source listings may omit some editions. No public-domain manuals are distributed with this code.
