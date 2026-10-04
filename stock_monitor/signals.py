"""Independent crossover rules, each requiring strict ADX14 > 25."""
import math
from .calendar import expected_latest

RULESET='independent_sma_macd_adx_gt25_v1'
SIGNALS={
 'sma5x20':{'type':'sma5_cross_above_sma20','fast':'sma5','slow':'sma20','flag':'golden_cross'},
 'macd12_26xsignal9':{'type':'macd_cross_above_signal','fast':'macd','slow':'macd_signal','flag':'macd_cross'}
}


def qualifying_signals(current):
    adx=current.get('adx14')
    if not isinstance(adx,(int,float)) or not math.isfinite(adx) or adx<=25:return []
    return [key for key,spec in SIGNALS.items() if current.get(spec['flag']) is True]


def event_qualifies(event):
    key=event.get('signal_key')
    if key is None:
        key=next((key for key,spec in SIGNALS.items() if event.get('type') in (spec['type'],'potential_daily_'+spec['type'])),None)
    if key not in SIGNALS:return False
    current=event.get('indicators',{});previous=event.get('previous_indicators',{})
    adx=current.get('adx14');spec=SIGNALS[key]
    vals=[adx,current.get(spec['fast']),current.get(spec['slow']),previous.get(spec['fast']),previous.get(spec['slow'])]
    if any(not isinstance(v,(int,float)) or not math.isfinite(v) for v in vals):return False
    return adx>25 and vals[3]<=vals[4] and vals[1]>vals[2]


def migrate_state(state,now,config):
    """Do not rewind ticker checkpoints or retroactively backfill new MACD rules."""
    if state.get('ruleset')==RULESET:return {'changed':False,'invalidated_pending':0}
    cutoffs={market:expected_latest(now,market,config['publication_delay_minutes']).isoformat() for market in ('us','jp')}
    count=0
    for event in state['events'].values():
        if event['delivery_status']=='pending':
            if not event_qualifies(event):
                event.update(delivery_status='invalidated',invalidation_reason='new_rules_require_cross_and_adx_strictly_above25',invalidated_at_utc=now.isoformat());count+=1
            else:event['ruleset']=RULESET
    state['ruleset']=RULESET
    state['macd_confirmed_after_by_market']=cutoffs
    state['rules_activated_at_utc']=now.isoformat()
    return {'changed':True,'invalidated_pending':count,'macd_confirmed_after_by_market':cutoffs}
