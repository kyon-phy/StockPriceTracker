"""Synthetic regression checks against the unchanged scanner API; no network."""

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from stock_monitor.__main__ import write_json
from stock_monitor.engine import empty_state
from stock_monitor.indicators import indicators
from stock_monitor.provider import ProviderError, normalize
from stock_monitor.provisional import evaluate_provisional
from stock_monitor.signals import event_qualifies
from test_engine import CFG, ITEM
from test_provisional import setup


ROOT = Path(__file__).resolve().parents[1]


class PublicationContractTests(unittest.TestCase):
    def test_synthetic_numeric_event_cases(self):
        fixture = json.loads((ROOT / 'tests/fixtures/signal_cases.json').read_text())
        for case in fixture['cases']:
            # Timing and interval eligibility are tested through the provider
            # and provisional evaluator below; event_qualifies is numeric only.
            if case['age_seconds'] != 0 or case['interval'] != '1d':
                continue
            with self.subTest(case=case['name']):
                values = {}
                for when in ('previous', 'current'):
                    row = case[when]
                    values[when] = dict(sma5=row['sma5'], sma20=row['sma20'],
                                        macd=row['macd12_26'], macd_signal=row['signal_ema9'],
                                        adx14=case['adx14'])
                actual = []
                for key, label in (('sma5x20', 'SMA_UP'), ('macd12_26xsignal9', 'MACD_UP')):
                    if event_qualifies(dict(signal_key=key, indicators=values['current'],
                                            previous_indicators=values['previous'])):
                        actual.append(label)
                self.assertEqual(actual, case['expected_events'])

    def test_quote_freshness_boundaries_for_both_daily_signals(self):
        def above_threshold(bars):
            rows = indicators(bars)
            rows[-1]['adx14'] = 26
            return rows

        for age, allowed in ((0, True), (600, True), (601, False), (-60, True), (-61, False)):
            with self.subTest(age_seconds=age):
                payload, now, _ = setup()
                payload['chart']['result'][0]['meta']['regularMarketTime'] = now.timestamp() - age
                state = empty_state()
                original = copy.deepcopy(state)
                with patch('stock_monitor.provisional.indicators', side_effect=above_threshold):
                    result = evaluate_provisional(ITEM, payload, {}, now, CFG, state)
                self.assertEqual(result['status'], 'ok' if allowed else 'blocked')
                if allowed:
                    self.assertEqual(result['potential_signals'], ['sma5x20', 'macd12_26xsignal9'])
                    self.assertEqual(len(state['events']), 2)
                else:
                    self.assertEqual(result['flags'], ['quote_stale_or_time_inconsistent'])
                    self.assertEqual(state, original)

    def test_non_daily_input_rejected_without_state_change(self):
        payload, now, _ = setup()
        payload['chart']['result'][0]['meta']['dataGranularity'] = '1h'
        state = empty_state()
        original = copy.deepcopy(state)
        with self.assertRaisesRegex(ProviderError, 'INTERVAL_MISMATCH'):
            evaluate_provisional(ITEM, payload, {}, now, CFG, state)
        self.assertEqual(state, original)

    def test_missing_trade_timestamp_blocks_without_state_change(self):
        payload, now, _ = setup()
        del payload['chart']['result'][0]['meta']['regularMarketTime']
        state = empty_state()
        original = copy.deepcopy(state)
        result = evaluate_provisional(ITEM, payload, {}, now, CFG, state)
        self.assertEqual(result['flags'], ['last_trade_timestamp_missing'])
        self.assertEqual(state, original)

    def test_duplicate_daily_bars_rejected(self):
        payload, now, _ = setup()
        data = payload['chart']['result'][0]
        data['timestamp'].append(data['timestamp'][-1])
        with self.assertRaisesRegex(ProviderError, 'DUPLICATE_DAILY_BAR'):
            normalize(payload, ITEM['symbol'], ITEM['market'], now.date())

    def test_nonfinite_event_values_fail_closed(self):
        for value in (None, float('nan'), float('inf'), '26'):
            for key, fast, slow in (('sma5x20', 'sma5', 'sma20'),
                                    ('macd12_26xsignal9', 'macd', 'macd_signal')):
                with self.subTest(value=value, signal=key):
                    event = dict(signal_key=key, indicators={fast: 2, slow: 1, 'adx14': value},
                                 previous_indicators={fast: 1, slow: 1})
                    self.assertFalse(event_qualifies(event))
                    event['indicators'].update(adx14=26, **{fast: value})
                    self.assertFalse(event_qualifies(event))

    def test_failed_state_serialization_preserves_existing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.json'
            write_json(path, empty_state())
            original = path.read_bytes()
            with self.assertRaises(ValueError):
                write_json(path, {'invalid': float('nan')})
            self.assertEqual(path.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
