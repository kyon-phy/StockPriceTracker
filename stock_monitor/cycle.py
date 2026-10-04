"""One scheduled cycle: no redundant confirmed reads; one payload per active symbol."""
from .calendar import expected_latest,session_is_open
from .engine import evaluate
from .provisional import evaluate_provisional
from .signals import event_qualifies


def plan_cycle(item,now,config,state):
    market,symbol=item['market'],item['symbol']
    expected=expected_latest(now,market,config['publication_delay_minutes']).isoformat()
    previous=state['tickers'].get(symbol)
    checkpoint=previous.get('last_processed_bar_date') if previous else None
    if checkpoint and checkpoint>expected:
        raise ValueError('state_ahead_of_expected_completed_session')
    confirmed_due=checkpoint!=expected
    provisional_due=session_is_open(now,market)
    return {'confirmed_due':confirmed_due,'provisional_due':provisional_due,
            'expected_completed_bar_date':expected,'state_completed_bar_date':checkpoint,
            'network_fetch_needed':confirmed_due or provisional_due}


def idle_result(item,plan):
    return {**item,'status':'skipped','flags':['completed_session_already_processed_and_market_closed'],
            'market_open':False,'checks':[],'new_event_ids':[],'cycle_plan':plan}


def evaluate_cycle(item,payload,source,now,config,state,plan,observed_now=None):
    checks=[]
    if plan['confirmed_due']:
        row=evaluate(item,payload,source,now,config,state);row['signal_mode']='confirmed';checks.append(row)
    if plan['provisional_due']:
        row=evaluate_provisional(item,payload,source,observed_now or now,config,state)
        row['signal_mode']='provisional';checks.append(row)
    status='blocked' if any(x['status'] in ('blocked','error') for x in checks) else 'ok' if any(x['status']=='ok' for x in checks) else 'skipped'
    return {**item,'status':status,'flags':sorted({f for x in checks for f in x.get('flags',[])}),
            'market_open':plan['provisional_due'],'cycle_plan':plan,'checks':checks,
            'new_event_ids':[eid for x in checks for eid in x.get('new_event_ids',[])]}


def eligible_events(state,results,market,mode,now):
    """Return currently deliverable outbox, quiet-hours count and window status."""
    from zoneinfo import ZoneInfo
    flattened=[check for row in results for check in (row['checks'] if 'checks' in row else [row])]
    fresh_provisional={eid for x in flattened if x['status']=='ok' for eid in x.get('revalidated_event_ids',[])}
    pending=[v for v in state['events'].values()
        if v['delivery_status']=='pending' and event_qualifies(v) and (market=='all' or v['market']==market)
        and ((mode in ('confirmed','cycle') and v.get('signal_status')!='provisional_intraday')
             or (mode in ('provisional','cycle') and v['id'] in fresh_provisional))]
    hour=now.astimezone(ZoneInfo('Asia/Tokyo')).hour
    window_open=hour>=10 or hour<3
    return (pending,0,True) if window_open else ([],len(pending),False)
