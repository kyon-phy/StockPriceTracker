import unittest,copy
from datetime import datetime,date,timedelta,timezone
from test_engine import fixture,CFG,ITEM,sma_test_indicators
from unittest.mock import patch
from stock_monitor.calendar import session_is_open,session_close
from stock_monitor.provisional import evaluate_provisional
from stock_monitor.engine import empty_state

def setup():
    payload,closed,dates=fixture(final_cross=True)
    now=session_close(date.fromisoformat(dates[-1]),'us')-timedelta(hours=2)
    root=payload['chart']['result'][0]
    root['meta']['regularMarketTime']=int(now.timestamp())-30
    root['meta']['regularMarketPrice']=root['indicators']['quote'][0]['close'][-1]
    return payload,now,dates

class TestProvisional(unittest.TestCase):
    def setUp(self):
        self.p=patch("stock_monitor.provisional.indicators",side_effect=sma_test_indicators);self.p.start();self.addCleanup(self.p.stop)
    def test_regular_hours_and_lunch(self):
        for stamp,market,want in [('2026-10-02T01:00:00+00:00','jp',True),('2026-10-02T03:00:00+00:00','jp',False),('2026-10-02T04:00:00+00:00','jp',True),('2026-10-02T06:30:00+00:00','jp',False),('2026-10-02T13:29:59+00:00','us',False),('2026-10-02T13:30:00+00:00','us',True),('2026-10-02T20:00:00+00:00','us',False)]:
            self.assertEqual(session_is_open(datetime.fromisoformat(stamp),market),want)
    def test_provisional_daily_cross_separate_state(self):
        payload,now,ds=setup();state=empty_state();r=evaluate_provisional(ITEM,payload,{},now,CFG,state)
        self.assertEqual(r['status'],'ok');self.assertTrue(r['potential_golden_cross']);self.assertEqual(state['tickers'],{})
        self.assertEqual(len(state['events']),1);eid=next(iter(state['events']));self.assertTrue(eid.endswith(':provisional'))
        r=evaluate_provisional(ITEM,payload,{},now,CFG,state);self.assertEqual(r['new_event_ids'],[])
    def test_delayed_quote_fails_safe(self):
        payload,now,ds=setup();payload['chart']['result'][0]['meta']['regularMarketTime']-=1200
        state=empty_state();r=evaluate_provisional(ITEM,payload,{},now,CFG,state)
        self.assertEqual(r['status'],'blocked');self.assertIn('quote_stale_or_time_inconsistent',r['flags']);self.assertFalse(state['events'])
    def test_quote_close_mismatch(self):
        payload,now,ds=setup();payload['chart']['result'][0]['meta']['regularMarketPrice']=111
        r=evaluate_provisional(ITEM,payload,{},now,CFG,empty_state());self.assertIn('quote_daily_close_mismatch',r['flags'])
    def test_disappearing_cross_invalidates_unsent_event(self):
        payload,now,ds=setup();state=empty_state();r=evaluate_provisional(ITEM,payload,{},now,CFG,state);eid=next(iter(state['events']))
        root=payload['chart']['result'][0];q=root['indicators']['quote'][0]
        for k in ['open','close']:q[k][-1]=99
        q['low'][-1]=98;root['meta']['regularMarketPrice']=99
        r=evaluate_provisional(ITEM,payload,{},now,CFG,state);self.assertFalse(r['potential_golden_cross']);self.assertEqual(state['events'][eid]['delivery_status'],'invalidated')
        q['open'][-1]=q['close'][-1]=110;root['meta']['regularMarketPrice']=110
        r=evaluate_provisional(ITEM,payload,{},now,CFG,state);self.assertTrue(r['potential_golden_cross']);self.assertEqual(r['new_event_ids'],[])
    def test_closed_market_skipped(self):
        payload,now,ds=setup();r=evaluate_provisional(ITEM,payload,{},now+timedelta(hours=3),CFG,empty_state())
        self.assertEqual(r['status'],'skipped');self.assertFalse(r['market_open'])
    def test_twenty_day_windows_not_ten_minute_bars(self):
        payload,now,ds=setup();r=evaluate_provisional(ITEM,payload,{},now,CFG,empty_state())
        self.assertEqual(r['indicators']['sma5'],102);self.assertEqual(r['indicators']['sma20'],100.5)
        self.assertEqual(r['completed_bar_count'],279)
if __name__=='__main__':unittest.main()
