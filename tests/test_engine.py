import copy,json,unittest
from unittest.mock import patch
from stock_monitor.indicators import indicators as real_indicators

def sma_test_indicators(bars):
    rows=real_indicators(bars)
    for row in rows:row.update(adx14=26.0,macd_cross=False)
    return rows

from datetime import datetime,date,timedelta,timezone
from stock_monitor.calendar import is_session,session_close,expected_latest,sessions
from stock_monitor.engine import empty_state,evaluate,acknowledge
from stock_monitor.provider import normalize,ProviderError

CFG={'warmup_bars':250,'publication_delay_minutes':30}
ITEM={'symbol':'NVDA','ticker':'NVDA','market':'us'}

def fixture(n=280,market='us',final_cross=False):
    ds=sessions(date(2025,1,2),date(2026,10,1),market)[:n]
    close=[100.0]*(n-1)+[110.0 if final_cross else 100.0]
    quote={k:[c+(1 if k=='high' else -1 if k=='low' else 0) for c in close] for k in ['open','high','low','close']}
    quote['volume']=[10000]*n
    sym='NVDA' if market=='us' else '6857.T'
    meta={'symbol':sym,'exchangeTimezoneName':'America/New_York' if market=='us' else 'Asia/Tokyo','dataGranularity':'1d','currency':'USD' if market=='us' else 'JPY'}
    ts=[int((session_close(date.fromisoformat(d),market)-timedelta(hours=2)).timestamp()) for d in ds]
    obj={'chart':{'result':[{'meta':meta,'timestamp':ts,'indicators':{'quote':[quote]}}],'error':None}}
    now=session_close(date.fromisoformat(ds[-1]),market)+timedelta(hours=1)
    return obj,now,ds

class TestCalendar(unittest.TestCase):
    def test_tokyo_close_change(self):
        self.assertEqual(session_close(date(2024,11,1),'jp').strftime('%H:%M'),'15:00')
        self.assertEqual(session_close(date(2024,11,5),'jp').strftime('%H:%M'),'15:30')
    def test_holidays(self):
        self.assertFalse(is_session(date(2025,1,9),'us'))
        self.assertFalse(is_session(date(2026,9,22),'jp'))
        self.assertTrue(is_session(date(2027,12,31),'us'))
        self.assertFalse(is_session(date(2027,12,31),'jp'))
    def test_early_close(self):
        self.assertEqual(session_close(date(2026,11,27),'us').hour,13)
        self.assertEqual(session_close(date(2026,7,2),'us').hour,16)
    def test_dst(self):
        self.assertEqual(session_close(date(2026,1,5),'us').astimezone(timezone.utc).hour,21)
        self.assertEqual(session_close(date(2026,7,6),'us').astimezone(timezone.utc).hour,20)
    def test_expiration(self):
        with self.assertRaisesRegex(ValueError,'CALENDAR_EXPIRED'):is_session(date(2028,1,3),'us')
    def test_end_delay(self):
        self.assertEqual(expected_latest(datetime.fromisoformat('2026-10-02T06:40:00+00:00'),'jp'),date(2026,10,1))
        self.assertEqual(expected_latest(datetime.fromisoformat('2026-10-02T07:00:00+00:00'),'jp'),date(2026,10,2))

class TestEngine(unittest.TestCase):
    def setUp(self):
        self.p=patch("stock_monitor.engine.indicators",side_effect=sma_test_indicators);self.p.start();self.addCleanup(self.p.stop)
    def test_baseline_suppresses_old_cross(self):
        payload,now,ds=fixture(final_cross=True);state=empty_state()
        result=evaluate(ITEM,payload,{},now,CFG,state)
        self.assertEqual(result['status'],'ok');self.assertTrue(result['indicators']['golden_cross']);self.assertEqual(state['events'],{})
        self.assertTrue(result['baseline_initialized'])
    def test_new_signal_dedup_and_ack(self):
        payload,now,ds=fixture(final_cross=True);state=empty_state()
        state['tickers']['NVDA']={'market':'us','baseline_bar_date':ds[-2],'last_processed_bar_date':ds[-2]}
        a=evaluate(ITEM,payload,{},now,CFG,state);eid='NVDA:'+ds[-1]+':sma5x20'
        self.assertEqual(a['new_event_ids'],[eid]);self.assertEqual(len(state['events']),1)
        b=evaluate(ITEM,payload,{},now,CFG,state);self.assertEqual(b['new_event_ids'],[])
        acknowledge(state,[eid],now);self.assertEqual(state['events'][eid]['delivery_status'],'delivered')
    def test_no_cross_when_already_above(self):
        payload,now,ds=fixture();q=payload['chart']['result'][0]['indicators']['quote'][0]
        for i in range(len(ds)):
            for k in ['open','high','low','close']:q[k][i]+=i/10
        state=empty_state();r=evaluate(ITEM,payload,{},now,CFG,state)
        self.assertGreater(r['indicators']['sma5'],r['indicators']['sma20']);self.assertFalse(r['indicators']['golden_cross'])
    def test_unfinished_bar_excluded(self):
        payload,now,ds=fixture(final_cross=True)
        cutoff=date.fromisoformat(ds[-2]);bars,info=normalize(payload,'NVDA','us',cutoff)
        self.assertEqual(bars[-1]['date'],ds[-2]);self.assertEqual(info['excluded_unfinished_dates'],[ds[-1]])
    def test_stale_no_state_advance(self):
        payload,now,ds=fixture();newnow=now+timedelta(days=5);state=empty_state()
        result=evaluate(ITEM,payload,{},newnow,CFG,state)
        self.assertEqual(result['status'],'blocked');self.assertIn('stale_or_missing_latest_bar',result['flags']);self.assertEqual(state['tickers'],{})
    def test_missing_bar_no_state_advance(self):
        payload,now,ds=fixture();root=payload['chart']['result'][0];del root['timestamp'][-20]
        for k in root['indicators']['quote'][0]:del root['indicators']['quote'][0][k][-20]
        state=empty_state();result=evaluate(ITEM,payload,{},now,CFG,state)
        self.assertIn('missing_sessions_in_warmup',result['flags']);self.assertEqual(state['tickers'],{})
    def test_insufficient_warmup(self):
        payload,now,ds=fixture(n=249);result=evaluate(ITEM,payload,{},now,CFG,empty_state())
        self.assertEqual(result['status'],'blocked');self.assertIn('insufficient_warmup',result['flags'])
    def test_split_not_double_adjusted(self):
        payload,now,ds=fixture();root=payload['chart']['result'][0]
        root['events']={'splits':{'x':{'date':root['timestamp'][-3],'numerator':3,'denominator':1}}}
        bars,quality=normalize(payload,'NVDA','us',date.fromisoformat(ds[-1]))
        self.assertEqual(bars[-4]['close'],100);self.assertEqual(bars[-1]['close'],100);self.assertEqual(quality['split_inconsistency_dates'],[])
    def test_unadjusted_split_blocks(self):
        payload,now,ds=fixture();root=payload['chart']['result'][0]
        root['events']={'splits':{'x':{'date':root['timestamp'][-3],'numerator':3,'denominator':1}}}
        for k in ['open','high','low','close']:
            for i in range(len(ds)-3):root['indicators']['quote'][0][k][i]*=3
        state=empty_state();r=evaluate(ITEM,payload,{},now,CFG,state)
        self.assertEqual(r['status'],'blocked');self.assertIn('split_inconsistency',r['flags']);self.assertFalse(state['tickers'])
    def test_ohlc_mixed_scale_rejected(self):
        payload,now,ds=fixture();root=payload['chart']['result'][0];root['indicators']['quote'][0]['high'][-1]=33
        r=evaluate(ITEM,payload,{},now,CFG,empty_state());self.assertIn('invalid_ohlc_in_warmup',r['flags'])
    def test_ticker_mismatch_rejected(self):
        payload,now,ds=fixture()
        with self.assertRaisesRegex(ProviderError,'MISMATCH'):normalize(payload,'AVGO','us',date.fromisoformat(ds[-1]))

if __name__=='__main__':unittest.main()
