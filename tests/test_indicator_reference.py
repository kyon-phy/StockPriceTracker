"""Independent numerical regression tests for the documented indicator convention.

The reference uses closed-form, geometrically weighted convolution of an SMA
seed and later observations. It deliberately does not call production smoothing
helpers or reproduce their rolling-sum recurrence. Wilder ADX seed convention:
TR/DM observations 1..14, first DI/DX at bar 14, first ADX at bar 27.

Run without third-party packages:
    python -m unittest discover -s tests -p 'test_indicator_reference.py' -v
"""
import math
import random
import unittest

from stock_monitor.indicators import ema, indicators


NUMERIC_FIELDS = (
    'sma5', 'sma20', 'plus_di14', 'minus_di14', 'adx14',
    'macd', 'macd_signal', 'macd_histogram',
)


def reference_smoothing(values, period, alpha):
    """SMA-seeded exponential smoother via an explicit weighted finite sum."""
    result = [None] * len(values)
    first = next((i for i, value in enumerate(values) if value is not None), len(values))
    seed_index = first + period - 1
    if seed_index >= len(values):
        return result
    seed = math.fsum(values[first:seed_index + 1]) / period
    q = 1.0 - alpha
    for i in range(seed_index, len(values)):
        elapsed = i - seed_index
        terms = [seed * q ** elapsed]
        terms.extend(alpha * values[j] * q ** (i - j)
                     for j in range(seed_index + 1, i + 1))
        result[i] = math.fsum(terms)
    return result


def reference_indicators(bars):
    """Independent all-history reference for SMA, DI/ADX and MACD."""
    count = len(bars)
    close = [bar['close'] for bar in bars]
    tr, plus_dm, minus_dm = [None], [None], [None]
    for previous, current in zip(bars, bars[1:]):
        # The distance from the highest to the lowest of these three prices is
        # equivalent to true range, but independent of production's max(abs()).
        prices = (current['high'], current['low'], previous['close'])
        tr.append(max(prices) - min(prices))
        high_move = current['high'] - previous['high']
        low_move = previous['low'] - current['low']
        movement = max(0.0, high_move, low_move)
        plus_dm.append(movement if high_move > low_move else 0.0)
        minus_dm.append(movement if low_move > high_move else 0.0)
    if not count:
        return []
    smoothed_tr = reference_smoothing(tr, 14, 1.0 / 14)
    smoothed_plus = reference_smoothing(plus_dm, 14, 1.0 / 14)
    smoothed_minus = reference_smoothing(minus_dm, 14, 1.0 / 14)
    plus_di, minus_di, dx = [None] * count, [None] * count, [None] * count
    for i in range(14, count):
        if smoothed_tr[i] == 0.0:
            plus_di[i] = minus_di[i] = 0.0
        else:
            plus_di[i] = 100.0 * smoothed_plus[i] / smoothed_tr[i]
            minus_di[i] = 100.0 * smoothed_minus[i] / smoothed_tr[i]
        directional_sum = smoothed_plus[i] + smoothed_minus[i]
        dx[i] = (100.0 * abs(smoothed_plus[i] - smoothed_minus[i]) /
                 directional_sum if directional_sum else 0.0)
    adx = reference_smoothing(dx, 14, 1.0 / 14)
    ema12 = reference_smoothing(close, 12, 2.0 / 13)
    ema26 = reference_smoothing(close, 26, 2.0 / 27)
    macd = [None if i < 25 else ema12[i] - ema26[i] for i in range(count)]
    signal = reference_smoothing(macd, 9, 2.0 / 10)
    sma5 = [None if i < 4 else math.fsum(close[i - 4:i + 1]) / 5
            for i in range(count)]
    sma20 = [None if i < 19 else math.fsum(close[i - 19:i + 1]) / 20
             for i in range(count)]
    return [
        dict(sma5=sma5[i], sma20=sma20[i], plus_di14=plus_di[i],
             minus_di14=minus_di[i], adx14=adx[i], macd=macd[i],
             macd_signal=signal[i],
             macd_histogram=None if signal[i] is None else macd[i] - signal[i],
             golden_cross=(i >= 20 and sma5[i] > sma20[i] and
                           sma5[i - 1] <= sma20[i - 1]))
        for i in range(count)
    ]


def make_bars(closes, half_range=1.0):
    return [dict(open=c, high=c + half_range, low=c - half_range, close=c)
            for c in closes]


def synthetic_bars(seed=20261002, count=768):
    """Deterministic, valid OHLC with gaps, inside bars, plateaus and trends."""
    rng = random.Random(seed)
    bars = []
    last = 100.0
    for i in range(count):
        regime = (i // 48) % 6
        drift = (0.0, 0.7, -0.7, 0.0, 0.15, -0.15)[regime]
        if regime == 3 and i % 4 == 0:
            opening = closing = last
            high = low = last
        else:
            gap = rng.uniform(-4.0, 4.0) if i % 17 == 0 else 0.0
            opening = max(20.0, last + gap)
            closing = max(20.0, opening + drift + rng.uniform(-1.5, 1.5))
            high = max(opening, closing) + rng.uniform(0.0, 2.0)
            low = min(opening, closing) - rng.uniform(0.0, 2.0)
        bars.append(dict(open=opening, high=high, low=low, close=closing))
        last = closing
    return bars


class IndicatorReferenceTests(unittest.TestCase):
    def assert_number_equal(self, actual, expected, context=''):
        if expected is None:
            self.assertIsNone(actual, context)
        else:
            self.assertIsNotNone(actual, context)
            self.assertTrue(math.isfinite(actual), context)
            self.assertTrue(math.isclose(actual, expected, rel_tol=2e-11, abs_tol=2e-11),
                            f'{context}: actual={actual!r}, expected={expected!r}')

    def compare_to_reference(self, bars):
        actual = indicators(bars)
        expected = reference_indicators(bars)
        self.assertEqual(len(actual), len(expected))
        for i, (observed, reference) in enumerate(zip(actual, expected)):
            for field in NUMERIC_FIELDS:
                self.assert_number_equal(observed[field], reference[field], f'bar {i}, {field}')
            self.assertEqual(observed['golden_cross'], reference['golden_cross'], f'bar {i}, cross')
        return actual, expected

    def test_2304_deterministic_ohlc_observations_against_independent_reference(self):
        for seed in (7, 211, 20261002):
            with self.subTest(seed=seed):
                self.compare_to_reference(synthetic_bars(seed=seed, count=768))

    def test_empty_and_every_warmup_length(self):
        bars = synthetic_bars(count=40)
        for length in range(41):
            with self.subTest(length=length):
                self.compare_to_reference(bars[:length])

    def test_first_valid_indices(self):
        result = indicators(synthetic_bars(count=50))
        first_valid = dict(sma5=4, sma20=19, plus_di14=14, minus_di14=14,
                           adx14=27, macd=25, macd_signal=33, macd_histogram=33)
        for field, index in first_valid.items():
            self.assertTrue(all(row[field] is None for row in result[:index]), field)
            self.assertTrue(all(row[field] is not None for row in result[index:]), field)
        self.assertFalse(any(row['golden_cross'] for row in result[:20]))

    def test_flat_price_with_zero_and_nonzero_range(self):
        for half_range in (0.0, 2.0):
            with self.subTest(half_range=half_range):
                rows, _ = self.compare_to_reference(make_bars([100.0] * 600, half_range))
                for i, row in enumerate(rows):
                    for field in NUMERIC_FIELDS:
                        if row[field] is not None:
                            expected = 100.0 if field.startswith('sma') else 0.0
                            self.assertEqual(row[field], expected, (i, field))
                    self.assertFalse(row['golden_cross'])

    def test_monotonic_trends_have_analytic_di_adx_and_macd(self):
        for direction in (1, -1):
            with self.subTest(direction=direction):
                rows, _ = self.compare_to_reference(
                    make_bars([1000.0 + direction * i for i in range(600)]))
                active_di = 'plus_di14' if direction == 1 else 'minus_di14'
                inactive_di = 'minus_di14' if direction == 1 else 'plus_di14'
                for i, row in enumerate(rows):
                    if i >= 14:
                        self.assertEqual(row[active_di], 50.0)
                        self.assertEqual(row[inactive_di], 0.0)
                    if i >= 27:
                        self.assertEqual(row['adx14'], 100.0)
                    if i >= 25:
                        self.assertEqual(row['macd'], direction * 7.0)
                    if i >= 33:
                        self.assertEqual(row['macd_signal'], direction * 7.0)
                        self.assertEqual(row['macd_histogram'], 0.0)
                    self.assertFalse(row['golden_cross'])

    def test_equal_directional_moves_contribute_neither_direction(self):
        bars = [dict(open=100.0, close=100.0, high=101.0 + i, low=99.0 - i)
                for i in range(60)]
        rows, _ = self.compare_to_reference(bars)
        for row in rows[14:]:
            self.assertEqual(row['plus_di14'], 0.0)
            self.assertEqual(row['minus_di14'], 0.0)
        for row in rows[27:]:
            self.assertEqual(row['adx14'], 0.0)

    def test_exact_sma_equality_previous_day_permits_cross(self):
        rows, _ = self.compare_to_reference(make_bars([10.0] * 20 + [11.0, 11.0]))
        self.assertEqual(rows[19]['sma5'], rows[19]['sma20'])
        self.assertTrue(rows[20]['golden_cross'])
        self.assertFalse(rows[21]['golden_cross'])

    def test_exact_sma_equality_current_day_is_not_cross(self):
        rows, _ = self.compare_to_reference(make_bars([10.0] * 19 + [9.0, 11.0, 11.0]))
        self.assertLess(rows[19]['sma5'], rows[19]['sma20'])
        self.assertEqual(rows[20]['sma5'], rows[20]['sma20'])
        self.assertFalse(rows[20]['golden_cross'])
        self.assertTrue(rows[21]['golden_cross'])

    def test_split_adjusted_uniform_scale_invariance(self):
        # A fully split-adjusted history is uniformly rescaled. DI, ADX and
        # crosses are unitless; price-unit SMA/MACD values scale proportionally.
        # This does not assert invariance for an unadjusted split discontinuity.
        bars = synthetic_bars(count=640)
        original = indicators(bars)
        unitless = ('plus_di14', 'minus_di14', 'adx14')
        for factor in (0.125, 0.5, 3.0, 10.0):
            scaled = indicators([{key: value * factor for key, value in bar.items()}
                                 for bar in bars])
            for i, (before, after) in enumerate(zip(original, scaled)):
                for field in NUMERIC_FIELDS:
                    expected = before[field]
                    if expected is not None and field not in unitless:
                        expected *= factor
                    self.assert_number_equal(after[field], expected, (factor, i, field))
                self.assertEqual(before['golden_cross'], after['golden_cross'], (factor, i))

    def test_prefixes_do_not_use_future_observations(self):
        bars = synthetic_bars(count=640)
        complete = indicators(bars)
        for length in (1, 14, 15, 20, 21, 26, 28, 34, 117, 315, 639):
            self.assertEqual(indicators(bars[:length]), complete[:length], length)

    def test_sma_seeded_ema_with_leading_missing_values(self):
        values = [None] * 25 + [float(i * i - 3 * i) for i in range(560)]
        expected = reference_smoothing(values, 9, 2.0 / 10)
        actual = ema(values, 9)
        for i, (observed, reference) in enumerate(zip(actual, expected)):
            self.assert_number_equal(observed, reference, i)


if __name__ == '__main__':
    unittest.main()
