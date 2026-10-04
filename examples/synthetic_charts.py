"""Create a local 25-stock demo with invented prices; never fetch market data."""

import argparse
from datetime import date, datetime, timedelta, timezone
import json
import math
from pathlib import Path
import random
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from stock_monitor.calendar import session_close, sessions
from stock_monitor.charts import DEFAULT_CONFIG, build_dashboard, write_dashboard


AS_OF = datetime(2026, 10, 4, 7, tzinfo=timezone.utc)
CAPTURED = '2026-10-02T22:00:00+00:00'


def synthetic_record(item, seed=0):
    rng = random.Random(seed)
    market = item['market']
    days = sessions(date(2024, 1, 2), date(2026, 10, 2), market)[-360:]
    scale = 12 if market == 'jp' else 1
    quote = {key: [] for key in ('open', 'high', 'low', 'close', 'volume')}
    for i in range(len(days)):
        close = (100 + seed * 3 + i * .045 + 18 * math.sin(i / 18 + seed / 5) + rng.uniform(-.5, .5)) * scale
        opened = close + rng.uniform(-1.2, 1.2) * scale
        values = dict(open=opened, close=close, high=max(opened, close) + rng.uniform(.3, 1.8) * scale,
                      low=min(opened, close) - rng.uniform(.3, 1.8) * scale, volume=10000 + i * 7)
        for key in quote:
            quote[key].append(round(values[key], 4))
    timestamps = [int((session_close(date.fromisoformat(day), market) - timedelta(hours=2)).timestamp()) for day in days]
    meta = dict(symbol=item['symbol'], currency='JPY' if market == 'jp' else 'USD',
                exchangeTimezoneName='Asia/Tokyo' if market == 'jp' else 'America/New_York',
                dataGranularity='1d', regularMarketTime=timestamps[-1], regularMarketPrice=quote['close'][-1])
    return dict(payload={'chart': {'result': [dict(meta=meta, timestamp=timestamps,
                                                  indicators={'quote': [quote]})], 'error': None}},
                source={'provider': 'SYNTHETIC DEMO — invented prices, not market observations',
                        'retrieved_at_utc': CAPTURED})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('local-charts/demo.html'))
    args = parser.parse_args()
    config = json.loads(DEFAULT_CONFIG.read_text())
    with tempfile.TemporaryDirectory(prefix='stockprice-synthetic-') as directory:
        raw = Path(directory)
        for seed, item in enumerate(config['universe']):
            (raw / (item['symbol'] + '.json')).write_text(json.dumps(synthetic_record(item, seed)))
        data = build_dashboard(config, raw, AS_OF, synthetic=True)
        write_dashboard(data, args.output)
    print(f'Wrote synthetic 25-stock demo to {args.output}. No real prices or network requests.')


if __name__ == '__main__':
    main()
