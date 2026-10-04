# StockPriceTracker

A Python 3.11+ daily stock scanner for 25 US and Japanese instruments. It reads
public Yahoo Finance daily charts and produces local JSON reports and a durable
event outbox. It does not send notifications, place trades, or install a schedule.
The imported scanner version is **1.3.0**; its formulas and configuration are
unchanged by this publication.

This is a source repository, not an already deployed monitoring service.
The project keeps calculations deterministic and dependency-free, fails closed
when daily data or calendars are invalid, and separates event detection from
delivery. Explicit local state makes runs resumable and prevents duplicate
events; independent synthetic references check the numerical conventions.

## Signals and timing

Two upward-cross signals are evaluated independently: SMA5 crosses above SMA20,
and MACD12/26 crosses above its EMA9 signal. Each requires **ADX14 > 25**.
Previous equality permits a cross; current equality and downward crosses do not.
All indicators use daily candles, including during an open session.

- `confirmed`: evaluates completed sessions after a configured 30-minute delay.
- `provisional`: evaluates an unfinished daily candle during regular hours; the
  last trade must be no more than 600 seconds old. The existing implementation
  tolerates up to 60 seconds of future clock skew. These signals can disappear.
- `cycle`: combines due confirmed/provisional work using one payload per symbol
  and skips reads when the market is closed and its checkpoint is current.

The 600-second rule applies to provisional quotes. Completed-session freshness
uses exchange-session dates and history completeness. The default universe has
12 US and 13 Japanese symbols; see `config.json`. Bundled calendars cover
2024-01-01 through 2027-12-31 and fail closed outside that range. Their published
source references and limitations are retained in the calendar JSON files.

## Offline verification

Use Python 3.11 or newer. The scanner uses the Python standard library. IANA
timezone data must be available for `zoneinfo` (including `America/New_York` and
`Asia/Tokyo`). No third-party Python packages are required on a system with that
timezone data.
Obtain the source and run the synthetic checks before using a live provider:

```sh
git clone https://github.com/kyon-phy/StockPriceTracker.git
cd StockPriceTracker
python3 -m unittest discover -s tests -v
python3 -m stock_monitor --help
```

Tests generate synthetic prices and temporary state. They need no production
files, provider account, or network requests. The numerical reference tests
check SMA, DI/ADX, MACD and seeding independently. See
[the verification contract](docs/SCANNER_CONTRACT.md) for boundary and safety tests.
Publication validation ran on Python 3.12.14.

For an offline CLI replay, provide a private directory containing one
`SYMBOL.json` per required symbol, with `payload` and `source` keys. Use
`--replay-dir` and a timezone-aware `--as-of`; a fake evaluation time is rejected
without replay mode. The closed-market CLI test demonstrates a complete cycle
with synthetic state and an empty replay directory.

## Configuration

The default `config.json` defines each instrument's display ticker, provider
symbol and `us`/`jp` market. It also sets 250 warmup bars, a 30-minute publication
delay, 1.1-second spacing between live requests and a 600-second provisional
quote-age ceiling. Select a separate file with `--config /path/to/config.json`
or restrict a run with `--market us` / `--market jp`; the default is `all`.
The indicator periods and strict ADX threshold are defined in source, not
arbitrary configuration fields. Review and test any changes before operational use.

## Manual local use

These commands are examples for an intentional live run; they make provider
requests. No live run or automation was enabled as part of publication.
Select a private persistent directory outside this checkout:

```sh
umask 077
STOCKPRICE_DATA="$HOME/.local/share/stock-price-tracker"
mkdir -p "$STOCKPRICE_DATA/raw"

# First intentional baseline only; existing historical crosses are suppressed.
python3 -m stock_monitor --mode cycle --initialize \
  --state-out "$STOCKPRICE_DATA/state.json" \
  --output "$STOCKPRICE_DATA/latest.json" --raw-dir "$STOCKPRICE_DATA/raw"

# Subsequent runs must reuse the existing state.
python3 -m stock_monitor --mode cycle --state "$STOCKPRICE_DATA/state.json" \
  --state-out "$STOCKPRICE_DATA/state.json" \
  --output "$STOCKPRICE_DATA/latest.json" --raw-dir "$STOCKPRICE_DATA/raw"
```

Do not reinitialize to recover lost state: that discards delivery history and
checkpoints. Use one writer at a time. The CLI replaces each output atomically,
but provides no cross-process lock, compare-and-swap, multi-file transaction, or
filesystem durability guarantee. Back up state and inspect failures before
continuing. A report includes the input-state hash, revision and persistence
requirement; an external runner must honor them when coordinating delivery.

An event remains pending until explicitly acknowledged with `--ack EVENT_ID`
and `--state`. Acknowledge only after confirmed external delivery. Eligible
reports use a 10:00 inclusive to next-day 03:00 exclusive Asia/Tokyo window;
this filtering does not schedule or send anything. Provisional events require
fresh revalidation before inclusion in the deliverable outbox.

Copy an exact ID from the delivered report's `pending_events`, then acknowledge
it locally. The acknowledgement path makes no provider request:

```sh
# Replace the placeholder only after that exact event was successfully delivered.
EVENT_ID='PASTE_EXACT_DELIVERED_EVENT_ID'
python3 -m stock_monitor --state "$STOCKPRICE_DATA/state.json" \
  --state-out "$STOCKPRICE_DATA/state.json" --ack "$EVENT_ID"
```

Event IDs distinguish the symbol, daily bar, signal family and provisional mode.
Keeping the same state prevents rediscovery; acknowledgement changes delivery
status. Failed or unconfirmed deliveries must remain pending for later handling.

## Data retention and publication boundary

| Output | Local behavior |
| --- | --- |
| `--state-out` (default `state-next.json`) | Replaces the selected state file; retains checkpoints, event delivery status and provisional observations. No automatic backup or expiry. |
| `--output` (default `latest.json`) | Replaces the selected report. Reports can contain prices and recent history; keep them private. |
| `--raw-dir` | Optional. Writes `SYMBOL.json` with the provider payload and retrieval metadata. Later reads overwrite that symbol's file; filenames are not timestamped archives. |
| No `--raw-dir` | Full provider responses are not saved by the CLI. State/reports may still contain prices. |

Use explicit output paths outside Git and maintain your own private backups or
snapshots if historical retention is needed. Cloud workspace files may disappear
when an environment is reset or removed; a local path is not a durable backup
and is not a copy on a personal computer.

This repository contains code, static configuration/calendars and synthetic
fixtures only. Real prices, original archives, mutable state, caches, logs,
credentials, private identifiers and conversations are excluded. The default-deny
`.gitignore` allows individual reviewed paths. It cannot prevent sensitive
content being added to an allowed file or forced into Git: inspect every staged
blob and its history before publishing. No public data artifacts or workflows
are included.

## Source and licensing limits

The provider is an unofficial public Yahoo Finance endpoint. Prices may be
delayed, revised or unavailable, and the code establishes no availability or
data-licensing guarantee. HTTP 401/403/429 stop further live reads for that run;
the client does not bypass access controls. Offline tests validate calculations
and control flow, not live Yahoo access or future market-calendar changes.
No live Yahoo requests were used to validate this publication. Review the data
provider's applicable terms before collecting, storing or redistributing data.
The output describes technical conditions and is not investment advice.

No license file or license grant was included in the imported source. This
publication does not invent one. Public visibility alone is not an additional
license grant; retain any applicable notices when distributing future changes.
