"""python -m stock_monitor --help"""
import argparse,json,sys,time,os,tempfile,copy,hashlib
from pathlib import Path
from datetime import datetime,timezone
from zoneinfo import ZoneInfo
from . import __version__
from .provider import fetch,ProviderError
from .engine import evaluate,empty_state,acknowledge
from .provisional import evaluate_provisional
from .calendar import session_is_open
from .cycle import plan_cycle,idle_result,evaluate_cycle,eligible_events
from .signals import migrate_state,RULESET


def write_json(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    # Atomic replacement within the explicitly selected scanner output location.
    with tempfile.NamedTemporaryFile('w',dir=path.parent,delete=False,encoding='utf-8') as f:
        json.dump(value,f,indent=2,ensure_ascii=False,allow_nan=False);f.write('\n');tmp=f.name
    os.replace(tmp,path)


def main():
    p=argparse.ArgumentParser(description='End-of-day SMA5/SMA20 scanner; report-only, no sending or trading.')
    p.add_argument('--config',default=str(Path(__file__).resolve().parent.parent/'config.json'))
    p.add_argument('--market',choices=['all','us','jp'],default='all')
    p.add_argument('--mode',choices=['confirmed','provisional','cycle'],default='confirmed')
    p.add_argument('--initialize',action='store_true',help='Explicit first baseline only; never use to recover missing durable state')
    p.add_argument('--state',help='Durable input state JSON; omit only for first intentional baseline')
    p.add_argument('--state-out',default='state-next.json')
    p.add_argument('--output',default='latest.json')
    p.add_argument('--raw-dir',help='Write timestamped raw provider records for audit')
    p.add_argument('--replay-dir',help='OFFLINE TEST ONLY: directory containing SYMBOL.json payload/source records')
    p.add_argument('--as-of',help='OFFLINE TEST ONLY: ISO UTC timestamp, requires --replay-dir')
    p.add_argument('--ack',nargs='+',help='Mark exact pending event IDs delivered; requires --state, no fetching')
    args=p.parse_args()
    if not args.state and not args.initialize:p.error('provide --state, or --initialize only for the first intentional baseline')
    if args.state and args.initialize:p.error('--state and --initialize are mutually exclusive')
    if args.as_of and not args.replay_dir:p.error('--as-of requires --replay-dir to avoid fake live evaluation times')
    now=datetime.fromisoformat(args.as_of.replace('Z','+00:00')) if args.as_of else datetime.now(timezone.utc)
    if now.tzinfo is None:p.error('--as-of must include timezone')
    config=json.loads(Path(args.config).read_text())
    raw_state=Path(args.state).read_bytes() if args.state else None
    state=json.loads(raw_state) if raw_state is not None else empty_state()
    if state.get('schema_version')!=1 or not isinstance(state.get('tickers'),dict) or not isinstance(state.get('events'),dict):p.error('invalid state schema')
    if args.ack:
        if not args.state:p.error('--ack requires --state')
        write_json(args.state_out,acknowledge(state,args.ack,now));return 0
    universe=[x for x in config['universe'] if args.market=='all' or x['market']==args.market]
    if len({x['symbol'] for x in config['universe']})!=len(config['universe']):p.error('duplicate universe symbols')
    initial_state=copy.deepcopy(state)
    migration=migrate_state(state,now,config)
    results=[]
    network_fetch_count=0
    fatal_access=False
    for i,item in enumerate(universe):
        symbol=item['symbol']
        try:
            plan=None
            if args.mode=='cycle':
                plan=plan_cycle(item,now,config,state)
                if not plan['network_fetch_needed']:
                    results.append(idle_result(item,plan));continue
            if args.mode=='provisional' and not session_is_open(now,item['market']):
                results.append({**item,'status':'skipped','flags':['regular_session_closed'],'market_open':False});continue
            if args.replay_dir:
                record=json.loads((Path(args.replay_dir)/(symbol+'.json')).read_text())
                payload,source=record['payload'],record['source']
            elif fatal_access:
                raise ProviderError('SKIPPED_AFTER_ACCESS_DENIED_OR_RATE_LIMIT')
            else:
                if network_fetch_count:time.sleep(config['request_spacing_seconds'])
                network_fetch_count+=1
                payload,source=fetch(symbol)
                if args.raw_dir:write_json(Path(args.raw_dir)/(symbol+'.json'),{'payload':payload,'source':source})
            if args.mode=='cycle':
                observed_now=now if args.replay_dir else datetime.now(timezone.utc)
                row=evaluate_cycle(item,payload,source,now,config,state,plan,observed_now)
            elif args.mode=='provisional':
                observed_now=now if args.replay_dir else datetime.now(timezone.utc)
                row=evaluate_provisional(item,payload,source,observed_now,config,state)
            else:
                row=evaluate(item,payload,source,now,config,state)
        except Exception as e:
            message=str(e)
            if message in ('HTTP_401','HTTP_403','HTTP_429'):fatal_access=True
            row={**item,'status':'error','flags':[message],'error_type':type(e).__name__}
        results.append(row)
        print(symbol,row['status'],row.get('bar_date',''),','.join(row.get('flags',[])),file=sys.stderr,flush=True)
    pending,deferred_count,notification_window_open=eligible_events(state,results,args.market,args.mode,now)
    # A nonempty outbox forces a guarded state commit even if its events were
    # already pending, preventing simultaneous stale-version deliveries.
    state_changed=(state!=initial_state or bool(pending))
    if state_changed:
        state['revision']+=1;state['updated_at_utc']=now.isoformat()
    report={'schema_version':1,'scanner_version':__version__,'ruleset':RULESET,'state_migration':migration,'as_of_utc':now.isoformat(),'completed_at_utc':datetime.now(timezone.utc).isoformat(),
            'mode':'offline_replay' if args.replay_dir else 'live','signal_mode':args.mode,'market':args.market,'configured_count':len(universe),
            'healthy_count':sum(x['status']=='ok' for x in results),'skipped_count':sum(x['status']=='skipped' for x in results),'results':results,'pending_events':pending,
            'input_state_sha256':hashlib.sha256(raw_state).hexdigest() if raw_state else None,
            'state_revision':state['revision'],'state_persistence_required':state_changed,'network_fetch_count':network_fetch_count,'notification_window_open':notification_window_open,
            'notification_timezone':'Asia/Tokyo','notification_window':'10:00 inclusive through next 03:00 exclusive',
            'deferred_event_count':deferred_count,
            'notice':'Unofficial free source; indicative data can be delayed or revised. This is a technical-condition report, not investment advice.'}
    write_json(args.state_out,state);write_json(args.output,report)
    print(json.dumps({k:report[k] for k in ('mode','market','configured_count','healthy_count','state_revision')})+' pending='+str(len(pending)))
    return 0 if report['healthy_count']+report['skipped_count']==len(universe) else 2

if __name__=='__main__':sys.exit(main())
