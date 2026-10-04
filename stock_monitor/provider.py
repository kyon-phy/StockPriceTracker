"""Anonymous public Yahoo Finance chart reads. No cookies, credentials or proxy bypass."""
import json, math, time, hashlib
from datetime import datetime,timezone,date
from urllib.request import Request,urlopen
from urllib.error import HTTPError,URLError
from zoneinfo import ZoneInfo

class ProviderError(Exception): pass


def fetch(symbol, timeout=35):
    url='https://query1.finance.yahoo.com/v8/finance/chart/'+symbol+'?range=2y&interval=1d&events=splits%2Cdiv&includeAdjustedClose=true'
    request=Request(url,headers={'User-Agent':'Mozilla/5.0 (compatible; personal daily chart reader)','Accept':'application/json'})
    # HTTP401/403 are not retried or evaded; 429 is reported and left for a later scheduled run.
    for attempt in range(2):
        try:
            with urlopen(request,timeout=timeout) as r:
                raw=r.read()
                stamp=datetime.now(timezone.utc).isoformat()
                record={'provider':'Yahoo Finance public chart (unofficial endpoint)','url':url,
                        'retrieved_at_utc':stamp,'http_date':r.headers.get('Date'),'http_status':r.status,
                        'sha256':hashlib.sha256(raw).hexdigest()}
            return json.loads(raw),record
        except HTTPError as e:
            if e.code in (500,502,503,504) and attempt==0: time.sleep(2);continue
            raise ProviderError('HTTP_'+str(e.code)) from e
        except (URLError,TimeoutError) as e:
            if attempt==0: time.sleep(2);continue
            raise ProviderError('NETWORK_ERROR: '+str(e)) from e
        except (ValueError,KeyError) as e:
            raise ProviderError('INVALID_JSON') from e


def normalize(payload,symbol,market,last_session):
    root=payload.get('chart',{})
    if root.get('error'): raise ProviderError('PROVIDER_ERROR: '+str(root['error']))
    data=root.get('result') or []
    if len(data)!=1: raise ProviderError('NO_CHART_RESULT')
    data=data[0];meta=data.get('meta',{})
    expected_tz='Asia/Tokyo' if market=='jp' else 'America/New_York'
    if meta.get('symbol')!=symbol or meta.get('exchangeTimezoneName')!=expected_tz or meta.get('dataGranularity')!='1d':
        raise ProviderError('SYMBOL_TIMEZONE_OR_INTERVAL_MISMATCH')
    expected_currency='JPY' if market=='jp' else 'USD'
    if meta.get('currency')!=expected_currency: raise ProviderError('CURRENCY_MISMATCH')
    timestamps=data.get('timestamp') or []
    quotes=data.get('indicators',{}).get('quote') or []
    if not timestamps or not quotes: raise ProviderError('MISSING_OHLC')
    q=quotes[0];tz=ZoneInfo(expected_tz);bars=[];seen=set();invalid=[];excluded=[]
    for i,ts in enumerate(timestamps):
        dt=datetime.fromtimestamp(ts,timezone.utc).astimezone(tz).date()
        if dt>last_session:
            excluded.append(dt.isoformat());continue
        if dt in seen: raise ProviderError('DUPLICATE_DAILY_BAR: '+dt.isoformat())
        seen.add(dt)
        values={k:(q.get(k) or [])[i] if i<len(q.get(k) or []) else None for k in ('open','high','low','close','volume')}
        nums=[values[k] for k in ('open','high','low','close')]
        if any(not isinstance(v,(int,float)) or not math.isfinite(v) or v<=0 for v in nums):
            invalid.append(dt.isoformat());continue
        o,h,l,c=nums
        if l>min(o,c) or h<max(o,c) or l>h:
            invalid.append(dt.isoformat());continue
        bars.append(dict(date=dt.isoformat(),timestamp=ts,**values))
    bars.sort(key=lambda x:x['date'])
    splits=[]
    for event in data.get('events',{}).get('splits',{}).values():
        d=datetime.fromtimestamp(event['date'],timezone.utc).astimezone(tz).date().isoformat()
        ratio=event.get('numerator',0)/event.get('denominator',1)
        splits.append({'date':d,'ratio':ratio})
    # Yahoo chart OHLC is already split-adjusted. Never multiply again. A gross
    # discontinuity consistent with a split is flagged for review, not "repaired".
    suspicious=[]
    for split in splits:
        for i in range(1,len(bars)):
            if bars[i-1]['date']<split['date']<=bars[i]['date'] and split['ratio']>0:
                ratio=bars[i-1]['close']/bars[i]['open'];r=split['ratio']
                if abs(r-1)>.4 and abs(math.log(ratio/r))<.15 and abs(math.log(ratio))>.3:
                    suspicious.append(split['date'])
    source_meta={k:meta.get(k) for k in ('symbol','currency','fullExchangeName','exchangeTimezoneName','regularMarketTime','regularMarketPrice','firstTradeDate')}
    return bars,dict(source_meta=source_meta,invalid_bar_dates=invalid,excluded_unfinished_dates=excluded,split_events=splits,split_inconsistency_dates=suspicious,
                     adjustment='Yahoo split-adjusted OHLC; dividends not adjusted; AdjClose intentionally unused')
