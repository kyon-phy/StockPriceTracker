# Scanner verification contract

The original scanner modules, configuration and calendar files are unchanged
from scanner 1.3.0. The local chart generator reuses those functions. The only
original test change replaces an excluded private state
file with generated temporary state; all its assertions remain intact.

## Source-grounded behavior

- The configured universe contains 25 instruments (12 US, 13 Japan).
- Provider normalization requires `dataGranularity == "1d"`, the configured
  symbol, exchange timezone and currency. Poll frequency does not change the
  indicator timeframe.
- SMA5/SMA20 and MACD12/26 versus EMA9 signal crosses are independent upward
  event families. Both may trigger. Downward crosses do not create alerts.
- A cross requires previous fast <= slow and current fast > slow. Each event
  also requires finite ADX14 strictly greater than 25; equality does not qualify.
- Provisional freshness uses the provider's `regularMarketTime`, on the same
  local session date: age <= 600 seconds, with the existing -60-second clock-skew
  allowance. Age 601 and future age -61 are blocked without state mutation.
- Confirmed history instead requires the latest completed exchange session,
  sufficient warmup, complete daily history and valid OHLC/split handling.
- SMA-seeded EMA12/26 and EMA9 define MACD. ADX14 uses Wilder smoothing with
  DI/DX first available at index 14 and ADX at index 27. These conventions are
  independently checked against synthetic numerical references.

## Tests

Run `python3 -m unittest discover -s tests -v` from the repository root.
The full suite contains 78 tests and uses no real prices or production state.

The existing suites verify indicator calculations, warmup, sessions and calendar
expiry; independent signals and strict ADX gating; provisional revalidation and
invalidation; baseline suppression, migration, catch-up, deduplication and
acknowledgement; and fail-closed handling of stale, missing, malformed or
split-inconsistent history.

`test_cli_cycle_no_reads_when_closed_state_current` builds schema-valid synthetic
state for all configured symbols in a temporary directory. It keeps the original
assertions for 25 skipped instruments, zero fetches, one-time migration to revision
2, and no persistence requirement on the repeated cycle. Its empty replay
directory proves no provider payload is read. No private baseline file is needed.

The publication tests add exact 600/601-second freshness boundaries for both
signals, existing -60/-61-second clock-skew boundaries, rejection of missing trade
timestamps/non-daily/duplicate daily input, non-finite event checks, failed JSON
serialization preserving the prior state file, and Git exclusion boundaries.
The handwritten `tests/fixtures/signal_cases.json` values are synthetic. Numeric
cases exercise `event_qualifies`; timing and interval cases are covered through
provider/provisional tests because numeric event eligibility has no clock input.

Local chart tests compare every displayed indicator field and historical match
with the existing scanner functions, and cover gaps, short histories, currency,
exchange-local dates, cache timestamps, malformed input, safe HTML embedding,
private atomic output and read-only operation. Chart markers never establish
that a monitor notification was delivered; no delivery state is read.

The full verification run also guards provider network entry points in the test
process. The CLI subprocess test explicitly uses offline replay. No Yahoo
requests, production updates, or automation activation are part of verification.

## Persistence limits

A successful JSON write replaces one file atomically. This is not a lock, a
multi-file transaction, a backup, or an fsync guarantee. Failed serialization
preserves an existing destination but can leave a temporary file in its private
output directory. Serialize runs and keep state/report/raw files outside Git.

Keep all safety, data, deduplication and persistence validation during future
edits. Ponytail complexity review is subordinate to these requirements and is
used only as plain-text review guidance, without installing agent behavior.
