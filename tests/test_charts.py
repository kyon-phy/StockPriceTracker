"""Local chart data uses the scanner's exact formulas and synthetic caches only."""

import copy
from datetime import datetime, timezone
from html.parser import HTMLParser
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from examples.synthetic_charts import AS_OF, synthetic_record
from stock_monitor.charts import (DEFAULT_CONFIG, INDICATOR_FIELDS, TEMPLATE,
                                  build_dashboard, chart_record, render_html, write_dashboard)
from stock_monitor.indicators import indicators
from stock_monitor.provider import normalize
from stock_monitor.signals import qualifying_signals


class ChartTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads(DEFAULT_CONFIG.read_text())
        self.us = self.config['universe'][0]
        self.jp = next(item for item in self.config['universe'] if item['market'] == 'jp')

    def chart(self, record=None, item=None):
        item = item or self.us
        return chart_record(item, record or synthetic_record(item), self.config, AS_OF)

    def test_all_values_and_matches_use_scanner_formulas(self):
        for item in (self.us, self.jp):
            with self.subTest(market=item['market']):
                record = synthetic_record(item)
                result = self.chart(record, item)
                bars, _ = normalize(record['payload'], item['symbol'], item['market'], AS_OF.date())
                calculated = indicators(bars)
                self.assertEqual(len(result['rows']), 360)
                for actual, expected in zip(result['rows'], calculated):
                    self.assertEqual({key: actual[key] for key in INDICATOR_FIELDS},
                                     {key: expected[key] for key in INDICATOR_FIELDS})
                expected_matches = [(bar['date'], qualifying_signals(row))
                                    for i, (bar, row) in enumerate(zip(bars, calculated))
                                    if i >= 249 and qualifying_signals(row)]
                self.assertTrue(expected_matches)
                self.assertEqual([(event['date'], event['signals']) for event in result['matches']], expected_matches)
                self.assertTrue(all(event['adx14'] > 25 and event['completed'] for event in result['matches']))
                self.assertEqual(result['currency'], 'JPY' if item['market'] == 'jp' else 'USD')
                self.assertEqual(result['latest_bar_date'], '2026-10-02')

    def test_gap_inserts_empty_price_row_and_hides_indicators(self):
        record = synthetic_record(self.us)
        root = record['payload']['chart']['result'][0]
        missing_date = datetime.fromtimestamp(root['timestamp'][300], timezone.utc).date().isoformat()
        del root['timestamp'][300]
        for values in root['indicators']['quote'][0].values():
            del values[300]
        result = self.chart(record)
        self.assertEqual(result['status'], 'blocked')
        self.assertIn(missing_date, result['missing_sessions'])
        self.assertTrue(next(row for row in result['rows'] if row['date'] == missing_date)['missing'])
        self.assertTrue(all(row[key] is None for row in result['rows'] for key in INDICATOR_FIELDS))
        self.assertEqual(result['matches'], [])

    def test_short_history_has_initialized_values_without_markers(self):
        record = synthetic_record(self.us)
        root = record['payload']['chart']['result'][0]
        root['timestamp'] = root['timestamp'][-15:]
        for key, values in root['indicators']['quote'][0].items():
            root['indicators']['quote'][0][key] = values[-15:]
        result = self.chart(record)
        self.assertIsNotNone(result['rows'][-1]['sma5'])
        self.assertIsNone(result['rows'][-1]['sma20'])
        self.assertIsNone(result['rows'][-1]['macd'])
        self.assertIsNone(result['rows'][-1]['adx14'])
        self.assertFalse(result['matches'])
        self.assertIn('15/250', ' '.join(result['warnings']))

    def test_japanese_dates_are_exchange_local(self):
        record = synthetic_record(self.jp)
        root = record['payload']['chart']['result'][0]
        root['timestamp'] = [int(datetime(2026, 10, 1, 23, 30, tzinfo=timezone.utc).timestamp())]
        for key, values in root['indicators']['quote'][0].items():
            root['indicators']['quote'][0][key] = values[-1:]
        self.assertEqual(self.chart(record, self.jp)['rows'][0]['date'], '2026-10-02')

    def test_invalid_ohlc_hides_all_indicators(self):
        record = synthetic_record(self.us)
        record['payload']['chart']['result'][0]['indicators']['quote'][0]['high'][300] = 0
        result = self.chart(record)
        self.assertEqual(result['status'], 'blocked')
        self.assertFalse(result['matches'])
        self.assertTrue(all(row['adx14'] is None for row in result['rows']))

    def test_suspicious_split_hides_indicators(self):
        record = synthetic_record(self.us)
        root = record['payload']['chart']['result'][0]
        root['events'] = {'splits': {'synthetic': {'date': root['timestamp'][-3], 'numerator': 3, 'denominator': 1}}}
        for key in ('open', 'high', 'low', 'close'):
            values = root['indicators']['quote'][0][key]
            values[:-3] = [value * 3 for value in values[:-3]]
        self.assertEqual(self.chart(record)['status'], 'blocked')

    def test_capture_and_age_are_explicit_without_live_claim(self):
        record = synthetic_record(self.us)
        record['payload']['chart']['result'][0]['meta']['regularMarketTime'] = AS_OF.timestamp() - 1200
        result = self.chart(record)
        self.assertEqual(result['quote_age_seconds'], 1200)
        self.assertEqual(result['quote_status'], 'Stale at generation')
        self.assertEqual(result['captured_at_utc'], record['source']['retrieved_at_utc'])
        del record['source']['retrieved_at_utc']
        result = self.chart(record)
        self.assertIsNone(result['completed_through'])
        self.assertFalse(result['matches'])
        self.assertTrue(result['rows'])

    def test_unfinished_candles_are_not_labeled_completed(self):
        record = synthetic_record(self.us)
        record['source']['retrieved_at_utc'] = '2026-10-02T15:00:00Z'
        result = self.chart(record)
        self.assertEqual(result['completed_through'], '2026-10-01')
        self.assertFalse(result['rows'][-1]['completed'])

    def test_missing_invalid_and_wrong_currency_are_isolated(self):
        with tempfile.TemporaryDirectory() as directory:
            raw = Path(directory)
            record = synthetic_record(self.us)
            record['payload']['chart']['result'][0]['meta']['currency'] = 'JPY'
            (raw / (self.us['symbol'] + '.json')).write_text(json.dumps(record))
            (raw / (self.config['universe'][1]['symbol'] + '.json')).write_text('null')
            result = build_dashboard(self.config, raw, AS_OF)
            self.assertEqual(len(result['instruments']), 25)
            self.assertTrue(all(not item['rows'] for item in result['instruments']))
            self.assertIn('currency', result['instruments'][0]['warnings'][0])
            self.assertIn('missing', result['instruments'][2]['warnings'][0])

    def test_build_is_read_only_and_does_not_fetch(self):
        with tempfile.TemporaryDirectory() as directory:
            raw = Path(directory)
            path = raw / (self.us['symbol'] + '.json')
            path.write_text(json.dumps(synthetic_record(self.us)))
            before = path.read_bytes()
            with patch('stock_monitor.provider.urlopen', side_effect=AssertionError('Network forbidden')):
                result = build_dashboard(self.config, raw, AS_OF)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(list(raw.iterdir()), [path])
            self.assertTrue(result['instruments'][0]['rows'])

    def test_unsafe_paths_and_duplicate_symbols_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            raw = Path(directory)
            config = copy.deepcopy(self.config)
            config['universe'][0]['symbol'] = '../secret'
            with self.assertRaises(ValueError):
                build_dashboard(config, raw, AS_OF)
            config = copy.deepcopy(self.config)
            config['universe'].append(config['universe'][0])
            with self.assertRaises(ValueError):
                build_dashboard(config, raw, AS_OF)
            (raw / (self.us['symbol'] + '.json')).symlink_to(raw / 'outside.json')
            self.assertFalse(build_dashboard(self.config, raw, AS_OF)['instruments'][0]['rows'])

    def test_embedded_cache_text_cannot_inject_markup(self):
        record = synthetic_record(self.us)
        attack = '</script><img src="https://invalid.example/" onerror="alert(1)">'
        record['source']['provider'] = attack
        data = dict(instruments=[self.chart(record)], generated_at_utc=AS_OF.isoformat(),
                    scanner_version='1.3.0', synthetic=True)
        html = render_html(data)
        self.assertNotIn(attack, html)
        self.assertIn('\\u003c/script>', html)
        self.assertIn("connect-src 'none'", html)
        class ExternalAssets(HTMLParser):
            def handle_starttag(inner, tag, attrs):
                self.assertNotEqual(tag, 'img')
                self.assertFalse(any(key in ('src', 'href') for key, _ in attrs))
        ExternalAssets().feed(html)

    def test_output_is_self_contained_private_and_atomic(self):
        data = dict(instruments=[self.chart()], generated_at_utc=AS_OF.isoformat(),
                    scanner_version='1.3.0', synthetic=True)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'view.html'
            write_dashboard(data, output)
            before = output.read_bytes()
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            self.assertNotIn('__CHART_DATA__', before.decode())
            bad = copy.deepcopy(data)
            bad['instruments'][0]['rows'][0]['close'] = float('nan')
            with self.assertRaises(ValueError):
                write_dashboard(bad, output)
            self.assertEqual(output.read_bytes(), before)
            self.assertEqual(list(Path(directory).iterdir()), [output])
            with self.assertRaises(ValueError):
                write_dashboard(data, TEMPLATE)

    def test_nonfinite_calculation_is_not_embedded(self):
        with patch('stock_monitor.charts.indicators', return_value=[{'adx14': float('nan')} for _ in range(360)]):
            with self.assertRaisesRegex(ValueError, 'Non-finite'):
                self.chart()


if __name__ == '__main__':
    unittest.main()
