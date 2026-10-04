"""Explicit, deterministic daily indicators; no third-party dependencies."""
from math import fsum


def sma(values, period):
    return [None if i < period - 1 else fsum(values[i+1-period:i+1])/period
            for i in range(len(values))]


def ema(values, period):
    """SMA seed at first period valid values; alpha=2/(period+1)."""
    out = [None] * len(values)
    first = next((i for i, v in enumerate(values) if v is not None), len(values))
    seed = first + period - 1
    if seed >= len(values):
        return out
    out[seed] = fsum(values[first:seed+1])/period
    alpha = 2/(period+1)
    for i in range(seed+1, len(values)):
        if values[i] is None:
            raise ValueError('internal gap in EMA input')
        out[i] = out[i-1] + alpha*(values[i]-out[i-1])
    return out


def indicators(bars):
    """ADX14 uses sums of TR/DM bars1..14 then Wilder recurrence.

    DI and DX first valid at index14, ADX = mean(DX14..27) at index27.
    This seed is specified, rather than assumed equal to every chart vendor.
    MACD has SMA-seeded EMA12 and26; signal is SMA-seeded EMA9 of MACD.
    """
    c = [b['close'] for b in bars]
    n = len(bars)
    a, b = ema(c, 12), ema(c, 26)
    macd = [None if x is None or y is None else x-y for x, y in zip(a, b)]
    signal = ema(macd, 9)
    hist = [None if y is None else x-y for x,y in zip(macd,signal)]
    pdi, mdi, dx, adx = ([None]*n for _ in range(4))
    tr, up, dn = [], [], []
    for i in range(1, n):
        cur, prev = bars[i], bars[i-1]
        u = cur['high']-prev['high']
        d = prev['low']-cur['low']
        up.append(u if u>d and u>0 else 0.0)
        dn.append(d if d>u and d>0 else 0.0)
        tr.append(max(cur['high']-cur['low'],abs(cur['high']-prev['close']),abs(cur['low']-prev['close'])))
    if n > 14:
        st, sp, sm = fsum(tr[:14]), fsum(up[:14]), fsum(dn[:14])
        for i in range(14, n):
            if i>14:
                st = st-st/14+tr[i-1]
                sp = sp-sp/14+up[i-1]
                sm = sm-sm/14+dn[i-1]
            pdi[i] = 100*sp/st if st else 0.0
            mdi[i] = 100*sm/st if st else 0.0
            denom = pdi[i]+mdi[i]
            dx[i] = 100*abs(pdi[i]-mdi[i])/denom if denom else 0.0
        if n>27:
            adx[27] = fsum(dx[14:28])/14
            for i in range(28, n):
                adx[i] = (adx[i-1]*13+dx[i])/14
    fast, slow = sma(c,5), sma(c,20)
    return [dict(sma5=fast[i],sma20=slow[i],adx14=adx[i],plus_di14=pdi[i],minus_di14=mdi[i],
                 macd=macd[i],macd_signal=signal[i],macd_histogram=hist[i],
                 golden_cross=(i>19 and fast[i-1]<=slow[i-1] and fast[i]>slow[i]),
                 macd_cross=(i>0 and signal[i-1] is not None and signal[i] is not None and macd[i-1]<=signal[i-1] and macd[i]>signal[i])) for i in range(n)]
