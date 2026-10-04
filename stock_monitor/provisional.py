"""Potential DAILY SMA crossing during an active regular session.

Ten-minute polling does not turn the indicators into ten-minute indicators.
These observations can disappear before the exchange close.
"""
import math
from datetime import datetime,timezone
from zoneinfo import ZoneInfo
from .calendar import session_is_open
from .engine import evaluate,empty_state
from .provider import normalize
from .indicators import indicators
from .signals import SIGNALS,RULESET,qualifying_signals


def evaluate_provisional(item,payload,source,now,config,state):
    symbol,market=item['symbol'],item['market']
    out={**item,'status':'blocked','flags':[],'evaluated_at_utc':now.isoformat(),
         'signal_status':'provisional_intraday','source':source,'new_event_ids':[],
         'market_open':session_is_open(now,market)}
    if not out['market_open']:
        out['status']='skipped';out['flags']=['regular_session_closed'];return out
    zone=ZoneInfo('Asia/Tokyo' if market=='jp' else 'America/New_York')
    today=now.astimezone(zone).date()
    # Read-only completed-history health evaluation with a throwaway baseline.
    hist=evaluate(item,payload,source,now,config,empty_state())
    if hist['status']!='ok':
        out['flags']=['completed_history_unhealthy']+hist['flags'];return out
    bars,quality=normalize(payload,symbol,market,today);out.update(quality)
    if not bars or bars[-1]['date']!=today.isoformat():
        out['flags']=['current_daily_bar_missing'];return out
    if len(bars)<config['warmup_bars']+1:
        out['flags']=['insufficient_completed_warmup'];return out
    if quality['split_inconsistency_dates'] or today.isoformat() in quality['invalid_bar_dates']:
        out['flags']=['current_ohlc_or_split_inconsistency'];return out
    meta=quality['source_meta'];timestamp=meta.get('regularMarketTime')
    if not isinstance(timestamp,(int,float)):
        out['flags']=['last_trade_timestamp_missing'];return out
    trade=datetime.fromtimestamp(timestamp,timezone.utc);age=(now-trade).total_seconds()
    out['source_last_trade_at_utc']=trade.isoformat();out['source_age_seconds']=round(age,3)
    out['maximum_quote_age_seconds']=config.get('maximum_quote_age_seconds',600)
    out['provider_delay_guarantee']='unknown; measured last-trade age only'
    if trade.astimezone(zone).date()!=today or age< -60 or age>out['maximum_quote_age_seconds']:
        out['flags']=['quote_stale_or_time_inconsistent'];return out
    price=meta.get('regularMarketPrice');close=bars[-1]['close']
    if not isinstance(price,(int,float)) or not math.isfinite(price) or abs(price-close)>max(0.02,abs(close)*1e-6):
        out['flags']=['quote_daily_close_mismatch'];return out
    series=indicators(bars);latest=series[-1];qualified=qualifying_signals(latest)
    prior_observation=state.get('provisional_observations',{}).get(symbol,{})
    # A raw cross observed below the ADX gate does not become a delayed alert
    # merely because ADX later rises. Require a new observed recross instead.
    if prior_observation.get('bar_date')==today.isoformat():
        qualified=[key for key in qualified if (symbol+':'+today.isoformat()+':'+key+':provisional') in state['events']
                   or not prior_observation.get('raw_crosses',{}).get(key,False)]
    out.update(status='ok',bar_date=today.isoformat(),bar_count=len(bars),completed_bar_count=len(bars)-1,
               ohlc=bars[-1],indicators=latest,previous_indicators=series[-2],
               potential_golden_cross='sma5x20' in qualified,potential_macd_cross='macd12_26xsignal9' in qualified,
               potential_signals=qualified,revalidated_event_ids=[])
    for key,spec in SIGNALS.items():
        eid=symbol+':'+today.isoformat()+':'+key+':provisional'
        existing=state['events'].get(eid)
        if key in qualified:
            out['revalidated_event_ids'].append(eid)
            if existing is None:
                event={'id':eid,**item,'bar_date':today.isoformat(),'type':'potential_daily_'+spec['type'],
                       'signal_key':key,'ruleset':RULESET,'adx_condition':'ADX14 > 25',
                       'signal_status':'provisional_intraday','delivery_status':'pending','discovered_at_utc':now.isoformat(),
                       'source_last_trade_at_utc':trade.isoformat(),'source_age_seconds':round(age,3),
                       'ohlc':bars[-1],'indicators':latest,'previous_indicators':series[-2],'source':source,
                       'warning':'Uses an unfinished DAILY candle; crossover and ADX condition can disappear before the close.'}
                state['events'][eid]=event;out['new_event_ids'].append(eid)
            elif existing['delivery_status']=='pending':
                existing.update(revalidated_at_utc=now.isoformat(),source_last_trade_at_utc=trade.isoformat(),
                                source_age_seconds=round(age,3),ohlc=bars[-1],indicators=latest,source=source)
        elif existing and existing['delivery_status']=='pending':
            existing['delivery_status']='invalidated';existing['invalidated_at_utc']=now.isoformat()
            existing['invalidation_reason']='cross_or_adx_above25_no_longer_present'
    state.setdefault('provisional_observations',{})[symbol]={'bar_date':today.isoformat(),
        'observed_at_utc':now.isoformat(),'source_last_trade_at_utc':trade.isoformat(),'potential_signals':qualified,
        'raw_crosses':{key:bool(latest.get(spec['flag'])) for key,spec in SIGNALS.items()}}
    return out
