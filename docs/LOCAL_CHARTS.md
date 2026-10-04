# Local daily indicator charts

The chart generator reads existing local cache files and writes one self-contained
HTML file. It never fetches quotes, reads delivery state, sends notifications,
starts a server or uploads anything. It uses the scanner's existing
`normalize`, `indicators` and `qualifying_signals` functions.

## Generate from a private cache

Run with Python 3.11+ from the repository root:

```sh
python3 -m stock_monitor.charts \
  --raw-dir /path/to/private/raw-cache \
  --output local-charts/indicators.html
```

Use `--config /path/to/config.json` for a separate universe. The default config
contains 25 instruments. Open `local-charts/indicators.html` in a modern browser;
the page needs JavaScript but no network. Generated output is Git-ignored and
created with private file permissions. The output can instead be an absolute
path outside the repository. An existing chosen output is atomically replaced.

The raw directory must contain `SYMBOL.json` records, such as `NVDA.json` and
`6857.T.json`, matching the scanner's `--raw-dir` output:

```text
{
  "payload": { "chart": { ...the cached daily chart response... } },
  "source": {
    "provider": "provider description",
    "retrieved_at_utc": "timezone-aware ISO capture timestamp"
  }
}
```

Input files are read-only. Missing or rejected files remain visible in the stock
selector with an explanation; the generator does not fill them from the network.
It prints how many configured instruments have usable price rows. A generated
file does not mean every configured instrument had a valid cache. Symlinked
cache files and unsafe symbol paths are rejected.

The optional `--as-of 2026-10-04T07:00:00Z` sets an explicit generation clock for
offline replay. It changes the snapshot's age/completion comparisons, not prices
or indicator formulas. The page states its generation time; it does not update
freshness automatically when opened later.

## Try the synthetic demo

```sh
python3 examples/synthetic_charts.py --output local-charts/demo.html
```

This generates invented OHLC records for all 25 configured instruments in a
temporary cache, builds the page, then removes the temporary input. The resulting
page is labeled **SYNTHETIC DEMO**. No real prices are included, and nothing is
downloaded. Real-data charts must be generated separately from your actual cache.

## Read and interact

- Select a stock and a visible range: 1M/3M/6M/1Y correspond to 21/63/126/252
  daily rows, not fixed calendar-month boundaries. All shows the entire cache.
- Price: candles or a closing-price line, plus SMA5 and SMA20.
- MACD: the existing EMA12-minus-EMA26 series, EMA9 signal and histogram.
- Directional strength: ADX14, +DI14, -DI14 and the reference line at 25.
- Hover or touch to synchronize the three charts' crosshairs and readout. Use
  the daily-bar slider with arrow keys for keyboard access to every visible row.
- Currency comes from validated market metadata (USD/JPY). Bar dates use the
  exchange's local timezone; capture and last-trade timestamps are shown in UTC.

The complete cache is used for indicator calculations before the visible window
is selected. Short histories show only initialized indicator values. The configured
250-bar warmup gates historical markers; it does not fabricate missing values.

## Historical matches are not monitor deliveries

Triangles mark SMA upward crosses; circles mark MACD upward crosses. A marker
requires that signal's original cross rule and strict ADX14 > 25. Both can occur
on the same daily bar. Previous equality may qualify; current equality does not.

Matches reconstruct the **current rules over cached history**, after warmup.
They do not reproduce notification history, baseline initialization, migration
cutoffs, deduplication, delivery acknowledgements or the notification time window.
No monitor state is read or changed. Hollow markers denote unfinished daily
observations at capture. Hollow candles are unfinished or have unverified
completion; neither should be treated as confirmed daily events.

Candle completion is checked at the source capture timestamp with the original
exchange calendar and publication delay. Missing, future or out-of-calendar
capture timestamps leave completion unverified and suppress markers. Price and
initialized indicator data can still be inspected with warnings.

## Quality and timeliness limits

Missing trading sessions are inserted as empty rows, so price lines break at the
gap. Missing/non-session bars, invalid OHLC or suspicious split adjustment hide
all indicators and markers for that instrument; valid price rows remain visible
with a warning. No gap is interpolated or silently bridged for indicator use.
The original calendar covers 2024–2027; out-of-range data cannot be certified.

The page shows the latest cached bar, capture timestamp, last trade timestamp
and quote age **at generation**. Its offline/cached/stale labels never assert
live health. A timestamp within the configured age limit alone does not certify
current session coverage or prove that a live alert would be delivered.

As documented in the README, Yahoo lists Tokyo `.T` feeds with a 20-minute
delay. The monitor's unchanged 600-second provisional limit can suppress such
quotes. Charting historical matches does not apply a current-quote freshness
gate to past bars; neither those markers nor closed-market skips validate live
Japanese active-session alerts. Completed daily bars are a separate use case.

## Keep outputs private

All CSS, JavaScript and displayed data are embedded in the generated file. The
viewer has no external assets, analytics, uploads or fetch calls, and its content
security policy blocks connections. No hosting or automation is enabled.

**The HTML contains the displayed prices.** Keep real-data HTML local, outside
public Git, Library sharing and hosted sites. `.gitignore` permits the audited
viewer template and demo source, but excludes generated HTML, caches, state and
screenshots. Never force-add these outputs. A local cloud-workspace file is not
a guaranteed persistent backup; use your own private durable storage as needed.

## Verification

```sh
python3 -m unittest discover -s tests -v
```

Chart tests compare values and matches directly with the scanner functions on
synthetic series, and exercise gaps, warmup, currency, exchange-local dates,
invalid caches, source timestamps, safe HTML embedding and read-only input.
Browser QA uses synthetic data only. It cannot establish live Yahoo availability
or prove that any real-data cache contains all 25 instruments.
