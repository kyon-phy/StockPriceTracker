"""Pure signal evaluation and resumable, durable event outbox."""
import copy,hashlib,json
from datetime import date
from .calendar import expected_latest,sessions,is_session
from .provider import normalize
from .indicators import indicators
from .signals import SIGNALS,RULESET,qualifying_signals


def empty_state():
    return {'schema_version':1,'ruleset':RULESET,'macd_confirmed_after_by_market':{},'revision':0,'tickers':{},'events':{},'updated_at_utc':None}


def evaluate(instrument,payload,source,now,config,state):
    ticker,symbol,market=(instrument[k] for k in ('ticker','symbol','market'))
    expected=expected_latest(now,market,config['publication_delay_minutes'])
    bars,quality=normalize(payload,symbol,market,expected)
    out={**instrument,'expected_bar_date':expected.isoformat(),'source':source,**quality,
         'bar_count':len(bars),'flags':[],'new_event_ids':[],'status':'error'}
    if not bars:
        out['flags'].append('no_completed_valid_bars');return out
    latest=bars[-1];out['bar_date']=latest['date'];out['ohlc']=latest
    if latest['date']!=expected.isoformat():out['flags'].append('stale_or_missing_latest_bar')
    if len(bars)<config['warmup_bars']:out['flags'].append('insufficient_warmup')
    if quality['split_inconsistency_dates']:out['flags'].append('split_inconsistency')
    # Validate all fetched sessions; pre-IPO dates are never expected.
    window=bars
    required=set(sessions(date.fromisoformat(window[0]['date']),expected,market))
    actual={b['date'] for b in window}
    out['missing_session_dates']=sorted(required-actual)
    out['non_session_bar_dates']=sorted(actual-required)
    if out['missing_session_dates']:out['flags'].append('missing_sessions_in_warmup')
    if out['non_session_bar_dates']:out['flags'].append('unexpected_non_session_bars')
    if any(x>=window[0]['date'] for x in quality['invalid_bar_dates']):out['flags'].append('invalid_ohlc_in_warmup')
    series=indicators(bars)
    out['indicators']=series[-1];out['qualifying_signals']=qualifying_signals(series[-1]);out['previous_bar_date']=bars[-2]['date'] if len(bars)>1 else None
    out['previous_indicators']=series[-2] if len(bars)>1 else None
    out['latest_cross_date']=next((bars[i]['date'] for i in range(len(bars)-1,19,-1) if series[i]['golden_cross']),None)
    out['recent_history']=[{**b,**v} for b,v in zip(bars[-30:],series[-30:])]
    if out['flags']:
        out['status']='blocked';return out
    previous=state['tickers'].get(symbol)
    if previous and previous.get('last_processed_bar_date','')>latest['date']:
        out['flags'].append('state_ahead_of_data');out['status']='blocked';return out
    if previous and previous.get('market')!=market:
        out['flags'].append('state_market_mismatch');out['status']='blocked';return out
    if not previous:
        out['baseline_initialized']=True
    else:
        checkpoint=previous['last_processed_bar_date']
        if checkpoint<bars[config['warmup_bars']-1]['date']:
            out['flags'].append('state_gap_exceeds_history');out['status']='blocked';return out
        for i in range(20,len(bars)):
            if bars[i]['date']<=checkpoint:continue
            for key in qualifying_signals(series[i]):
                if key=='macd12_26xsignal9' and bars[i]['date']<=state.get('macd_confirmed_after_by_market',{}).get(market,''):continue
                event_id=symbol+':'+bars[i]['date']+':'+key
                if event_id not in state['events']:
                    event={'id':event_id,'symbol':symbol,'ticker':ticker,'market':market,'bar_date':bars[i]['date'],
                           'type':SIGNALS[key]['type'],'signal_key':key,'ruleset':RULESET,'adx_condition':'ADX14 > 25',
                           'delivery_status':'pending','discovered_at_utc':now.isoformat(),
                           'is_catch_up':bars[i]['date']!=latest['date'],'ohlc':bars[i],'indicators':series[i],
                           'previous_indicators':series[i-1],'source':source}
                    state['events'][event_id]=event;out['new_event_ids'].append(event_id)
    state['tickers'][symbol]={'market':market,'last_processed_bar_date':latest['date'],
                              'baseline_bar_date':previous.get('baseline_bar_date') if previous else latest['date'],
                              'last_success_at_utc':now.isoformat()}
    out['status']='ok'
    return out


def acknowledge(state, event_ids, now):
    for eid in event_ids:
        if eid not in state['events']:raise ValueError('unknown event: '+eid)
        state['events'][eid]['delivery_status']='delivered'
        state['events'][eid]['delivered_at_utc']=now.isoformat()
    state['revision']+=1;state['updated_at_utc']=now.isoformat()
    return state
