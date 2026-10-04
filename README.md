# StockPriceTracker

A Python 3.9+ daily stock scanner for 25 US and Japanese instruments. It reads
public Yahoo Finance daily charts and produces local JSON reports and a durable
event outbox. It does not send notifications, place trades, or install a schedule.
The imported scanner version is **1.3.0**; its formulas and configuration are
unchanged by this publication.

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

The scanner uses the Python standard library. IANA timezone data must be
available for `zoneinfo`. Run from this checkout:

```sh
python3 -m unittest discover -s tests -v
python3 -m stock_monitor --help
```

Tests generate synthetic prices and temporary state. They need no production
files, provider account, or network requests. The numerical reference tests
check SMA, DI/ADX, MACD and seeding independently. See
[the verification contract](docs/SCANNER_CONTRACT.md) for boundary and safety tests.

For an offline CLI replay, provide a private directory containing one
`SYMBOL.json` per required symbol, with `payload` and `source` keys. Use
`--replay-dir` and a timezone-aware `--as-of`; a fake evaluation time is rejected
without replay mode. The closed-market CLI test demonstrates a complete cycle
with synthetic state and an empty replay directory.

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

## Licensing

No license file or license grant was included in the imported source. This
publication does not invent one. Public visibility alone is not an additional
license grant; retain any applicable notices when distributing future changes.
