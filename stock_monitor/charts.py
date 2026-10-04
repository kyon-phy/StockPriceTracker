"""Generate a self-contained, offline dashboard from local daily-chart caches."""

import argparse
from datetime import date, datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import tempfile
from zoneinfo import ZoneInfo

from . import __version__
from .calendar import expected_latest, sessions
from .indicators import indicators
from .provider import ProviderError, normalize
from .signals import qualifying_signals


TEMPLATE = Path(__file__).with_name('chart_view.html')
DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / 'config.json'
INDICATOR_FIELDS = ('sma5', 'sma20', 'macd', 'macd_signal', 'macd_histogram',
                    'adx14', 'plus_di14', 'minus_di14')


def parse_time(value):
    """Accept an aware ISO timestamp; an unknown capture is never assumed fresh."""
    if not isinstance(value, str):
        return None
    try:
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return stamp.astimezone(timezone.utc) if stamp.tzinfo else None
    except ValueError:
        return None


def chart_record(item, record, config, now):
    """Read-only chart interpretation. No scanner state or delivery outbox is read."""
    symbol, market = item['symbol'], item['market']
    currency = 'JPY' if market == 'jp' else 'USD'
    result = dict(symbol=symbol, ticker=item['ticker'], market=market, currency=currency,
                  rows=[], matches=[], warnings=[], status='unavailable')
    if not isinstance(record, dict) or not isinstance(record.get('source', {}), dict):
        raise ValueError('Invalid cache record')
    source = record.get('source') or {}
    capture = parse_time(source.get('retrieved_at_utc'))
    result['captured_at_utc'] = capture.isoformat() if capture else None
    result['provider'] = str(source.get('provider') or 'Unspecified cached provider')
    warnings = result['warnings']
    completed = None
    if capture is None:
        warnings.append('Capture timestamp missing or invalid; candle completion and markers are unverified.')
    elif capture > now:
        warnings.append('Capture timestamp is later than generation time; markers are withheld.')
    else:
        try:
            completed = expected_latest(capture, market, config['publication_delay_minutes']).isoformat()
        except ValueError:
            warnings.append('Capture is outside calendar coverage; markers are withheld.')
    result['completed_through'] = completed
    zone = ZoneInfo('Asia/Tokyo' if market == 'jp' else 'America/New_York')
    bars, quality = normalize(record['payload'], symbol, market, now.astimezone(zone).date())
    if not bars:
        result['warnings'].append('No valid daily OHLC bars in this cache.')
        return result

    result['latest_bar_date'] = bars[-1]['date']
    result['bar_count'] = len(bars)
    result['warmup_bars'] = config['warmup_bars']
    result['quote_age_seconds'] = None
    result['last_trade_at_utc'] = None
    stamp = quality['source_meta'].get('regularMarketTime')
    if isinstance(stamp, (int, float)) and not isinstance(stamp, bool) and math.isfinite(stamp):
        try:
            trade = datetime.fromtimestamp(stamp, timezone.utc)
            result['last_trade_at_utc'] = trade.isoformat()
            result['quote_age_seconds'] = round((now - trade).total_seconds(), 1)
        except (ValueError, OverflowError, OSError):
            pass
    age = result['quote_age_seconds']
    limit = config.get('maximum_quote_age_seconds', 600)
    result['quote_age_limit_seconds'] = limit
    result['quote_status'] = ('Timestamp unknown' if age is None else
                              'Stale at generation' if age > limit else
                              'Future timestamp' if age < -60 else 'Within age limit (cached)')
    try:
        expected = expected_latest(now, market, config['publication_delay_minutes']).isoformat()
        result['expected_completed_date'] = expected
        if bars[-1]['date'] < expected:
            warnings.append('Latest cached bar precedes the expected completed session.')
    except ValueError:
        warnings.append('Generation time is outside calendar coverage; latest-session comparison unavailable.')

    actual = {bar['date'] for bar in bars}
    blocked = False
    try:
        required = set(sessions(date.fromisoformat(bars[0]['date']), date.fromisoformat(bars[-1]['date']), market))
    except ValueError:
        required = actual
        blocked = True
        warnings.append('Bar dates outside calendar coverage; indicators and markers hidden.')
    missing, extra = sorted(required - actual), sorted(actual - required)
    result['missing_sessions'] = missing
    result['non_session_dates'] = extra
    if missing or extra:
        blocked = True
        warnings.append(f'{len(missing)} missing sessions; {len(extra)} non-session bars. Indicators and markers hidden.')
    if quality['invalid_bar_dates'] or quality['split_inconsistency_dates']:
        blocked = True
        warnings.append('Invalid OHLC or suspicious split adjustment; indicators and markers hidden.')
    if quality['excluded_unfinished_dates']:
        warnings.append('Bars dated after generation time were excluded.')
    if len(bars) < config['warmup_bars']:
        warnings.append(f"Only {len(bars)}/{config['warmup_bars']} warmup bars; early indicators may be absent and markers are withheld.")
    rows = indicators(bars) if not blocked else [{} for _ in bars]
    if any(row.get(key) is not None and not math.isfinite(row[key])
           for row in rows for key in INDICATOR_FIELDS):
        raise ValueError('Non-finite calculated indicator')
    by_date = {}
    for index, (bar, calculated) in enumerate(zip(bars, rows)):
        values = {key: calculated.get(key) for key in INDICATOR_FIELDS}
        row = {key: bar[key] for key in ('date', 'open', 'high', 'low', 'close')}
        row.update(values, missing=False, completed=(bar['date'] <= completed if completed else None))
        by_date[bar['date']] = row
        if not blocked and completed and index >= config['warmup_bars'] - 1:
            keys = qualifying_signals(calculated)
            if keys:
                result['matches'].append(dict(date=bar['date'], signals=keys,
                                               adx14=calculated['adx14'], completed=row['completed']))
    for day in missing:
        by_date[day] = dict(date=day, missing=True, completed=None,
                            **{key: None for key in ('open', 'high', 'low', 'close', *INDICATOR_FIELDS)})
    result['rows'] = [by_date[day] for day in sorted(by_date)]
    result['status'] = 'blocked' if blocked else 'limited' if warnings else 'available'
    return result


def build_dashboard(config, raw_dir, now, *, synthetic=False):
    """Load SYMBOL.json records, retaining missing instruments in the selector."""
    if now.tzinfo is None:
        raise ValueError('Generation time must include a timezone')
    if not isinstance(config.get('warmup_bars'), int) or config['warmup_bars'] < 20:
        raise ValueError('warmup_bars must be an integer of at least 20')
    universe = config['universe']
    if not universe or len({item['symbol'] for item in universe}) != len(universe):
        raise ValueError('Universe must be nonempty with unique symbols')
    raw_dir = Path(raw_dir)
    if not raw_dir.is_dir():
        raise ValueError('Raw cache directory does not exist')
    instruments = []
    for item in universe:
        symbol = item['symbol']
        if not re.fullmatch(r'[A-Za-z0-9^][A-Za-z0-9.^=_-]{0,49}', symbol) or item['market'] not in ('us', 'jp'):
            raise ValueError('Invalid cache symbol or market')
        fallback = dict(symbol=symbol, ticker=item['ticker'], market=item['market'],
                        currency='JPY' if item['market'] == 'jp' else 'USD',
                        status='unavailable', rows=[], matches=[], warnings=[])
        try:
            path = raw_dir / (symbol + '.json')
            if path.is_symlink():
                raise ValueError('Cache symlinks are not accepted')
            record = json.loads(path.read_text(encoding='utf-8'))
            instruments.append(chart_record(item, record, config, now))
        except FileNotFoundError:
            fallback['warnings'] = ['Local cache missing. No network request was attempted.']
            instruments.append(fallback)
        except (ProviderError, ValueError, KeyError, TypeError, AttributeError,
                IndexError, OverflowError, ZeroDivisionError, OSError):
            fallback['warnings'] = ['Cache rejected: check JSON, symbol, currency, timezone, daily interval and OHLC.']
            instruments.append(fallback)
    return dict(schema_version=1, scanner_version=__version__,
                generated_at_utc=now.astimezone(timezone.utc).isoformat(),
                synthetic=synthetic, instruments=instruments)


def render_html(data):
    # Prevent a cached string from closing the non-executable JSON script tag.
    encoded = json.dumps(data, ensure_ascii=True, allow_nan=False).replace('<', '\\u003c')
    return TEMPLATE.read_text(encoding='utf-8').replace('__CHART_DATA__', encoded)


def write_dashboard(data, output):
    output = Path(output)
    if output.suffix.lower() != '.html' or output.resolve() == TEMPLATE.resolve():
        raise ValueError('Choose a separate .html output file')
    content = render_html(data)
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=output.parent, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(content)
        os.replace(temporary, output)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=DEFAULT_CONFIG)
    parser.add_argument('--raw-dir', type=Path, required=True, help='Read-only local SYMBOL.json payload/source cache')
    parser.add_argument('--output', type=Path, default=Path('local-charts/index.html'))
    parser.add_argument('--as-of', help='Offline generation clock (aware ISO timestamp); never fetches quotes')
    args = parser.parse_args()
    now = parse_time(args.as_of) if args.as_of else datetime.now(timezone.utc)
    if now is None:
        parser.error('--as-of must be an ISO timestamp with timezone')
    try:
        config = json.loads(args.config.read_text(encoding='utf-8'))
        data = build_dashboard(config, args.raw_dir, now)
        output = write_dashboard(data, args.output)
    except (ValueError, KeyError, TypeError, OSError) as error:
        parser.error(str(error))
    available = sum(bool(item['rows']) for item in data['instruments'])
    print(f'Wrote {output}: {available}/{len(data["instruments"])} caches with price rows. Offline snapshot only.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
