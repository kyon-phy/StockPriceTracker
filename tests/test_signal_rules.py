import copy,unittest
from unittest.mock import patch
from datetime import datetime,timedelta
from test_engine import fixture,CFG,ITEM
from test_provisional import setup
from stock_monitor.indicators import indicators
from stock_monitor.engine import empty_state,evaluate
from stock_monitor.provisional import evaluate_provisional
from stock_monitor.signals import qualifying_signals,migrate_state,event_qualifies,RULESET
from stock_monitor.cycle import eligible_events

class TestSignalRules(unittest.TestCase):
    def test_actual_macd_equality_cross_and_no_repeated_cross(self):
        closes=[100.0]*60+[110.0,110.0]
        bars=[{'open':c,'high':c+1,'low':c-1,'close':c} for c in closes]
        rows=indicators(bars)
        self.assertEqual(rows[59]['macd'],rows[59]['macd_signal'])
        self.assertTrue(rows[60]['macd_cross']);self.assertFalse(rows[61]['macd_cross'])
        self.assertFalse(rows[59]['macd_cross'])
    def test_strict_adx_threshold(self):
        for value,want in [(24.9,[]),(25,[]),(25.00001,['sma5x20'])]:
            self.assertEqual(qualifying_signals({'adx14':value,'golden_cross':True,'macd_cross':False}),want)
    def test_independent_signals_and_both(self):
        for sma,macd,want in [(True,False,['sma5x20']),(False,True,['macd12_26xsignal9']),(True,True,['sma5x20','macd12_26xsignal9']),(False,False,[])]:
            self.assertEqual(qualifying_signals({'adx14':26,'golden_cross':sma,'macd_cross':macd}),want)
    def series(self,bars,adx=26,sma=True,macd=True):
        rows=indicators(bars)
        for row in rows:row.update(golden_cross=False,macd_cross=False,adx14=adx)
        rows[-2].update(sma5=100,sma20=100,macd=0,macd_signal=0)
        rows[-1].update(golden_cross=sma,macd_cross=macd,sma5=101 if sma else 99,sma20=100,macd=1 if macd else -1,macd_signal=0)
        return rows
    def test_confirmed_independent_events_and_dedup(self):
        for sma,macd,count in [(True,False,1),(False,True,1),(True,True,2)]:
            payload,now,ds=fixture(final_cross=True);state=empty_state();state['tickers']['NVDA']={'market':'us','last_processed_bar_date':ds[-2]}
            with patch('stock_monitor.engine.indicators',side_effect=lambda b:self.series(b,sma=sma,macd=macd)):
                r=evaluate(ITEM,payload,{},now,CFG,state);self.assertEqual(len(r['new_event_ids']),count)
                self.assertTrue(all(event_qualifies(e) for e in state['events'].values()))
                self.assertEqual(evaluate(ITEM,payload,{},now,CFG,state)['new_event_ids'],[])
    def test_below_threshold_cross_not_delayed_until_adx_rises(self):
        payload,now,ds=fixture(final_cross=True);state=empty_state();state['tickers']['NVDA']={'market':'us','last_processed_bar_date':ds[-2]}
        with patch('stock_monitor.engine.indicators',side_effect=lambda b:self.series(b,adx=25)):
            evaluate(ITEM,payload,{},now,CFG,state)
        self.assertFalse(state['events'])
        with patch('stock_monitor.engine.indicators',side_effect=lambda b:self.series(b,adx=30,sma=False,macd=False)):
            evaluate(ITEM,payload,{},now,CFG,state)
        self.assertFalse(state['events'])
    def test_provisional_independent_and_threshold_revalidation(self):
        payload,now,ds=setup();state=empty_state()
        with patch('stock_monitor.provisional.indicators',side_effect=lambda b:self.series(b)):
            r=evaluate_provisional(ITEM,payload,{},now,CFG,state)
        self.assertEqual(len(r['new_event_ids']),2)
        with patch('stock_monitor.provisional.indicators',side_effect=lambda b:self.series(b,adx=25)):
            r=evaluate_provisional(ITEM,payload,{},now,CFG,state)
        self.assertEqual(r['potential_signals'],[]);self.assertTrue(all(e['delivery_status']=='invalidated' for e in state['events'].values()))
    def test_provisional_below_threshold_no_late_alert(self):
        payload,now,ds=setup();state=empty_state()
        with patch('stock_monitor.provisional.indicators',side_effect=lambda b:self.series(b,adx=25)):
            evaluate_provisional(ITEM,payload,{},now,CFG,state)
        with patch('stock_monitor.provisional.indicators',side_effect=lambda b:self.series(b,adx=26)):
            r=evaluate_provisional(ITEM,payload,{},now,CFG,state)
        self.assertEqual(r['new_event_ids'],[]);self.assertFalse(state['events'])
    def test_migration_invalidates_old_pending_without_rewinding(self):
        now=datetime.fromisoformat('2026-10-02T08:00:00+00:00');state={'schema_version':1,'revision':1,'tickers':{'NVDA':{'market':'us','last_processed_bar_date':'2026-10-01'}},'events':{}}
        for adx in (25,26):
            eid=str(adx);state['events'][eid]={'id':eid,'type':'sma5_cross_above_sma20','delivery_status':'pending','indicators':{'adx14':adx,'sma5':2,'sma20':1},'previous_indicators':{'sma5':1,'sma20':1}}
        r=migrate_state(state,now,CFG);self.assertEqual(r['invalidated_pending'],1);self.assertEqual(state['events']['25']['delivery_status'],'invalidated');self.assertEqual(state['events']['26']['delivery_status'],'pending')
        self.assertEqual(state['tickers']['NVDA']['last_processed_bar_date'],'2026-10-01');self.assertEqual(state['macd_confirmed_after_by_market']['us'],'2026-10-01')
        self.assertFalse(migrate_state(state,now,CFG)['changed'])
    def test_new_macd_rules_do_not_backfill_existing_completed_bars(self):
        payload,now,ds=fixture(final_cross=True);state=empty_state();state.pop('ruleset');state['tickers']['NVDA']={'market':'us','last_processed_bar_date':ds[-2]}
        migrate_state(state,now,CFG)
        with patch('stock_monitor.engine.indicators',side_effect=lambda b:self.series(b,sma=False,macd=True)):
            r=evaluate(ITEM,payload,{},now,CFG,state)
        self.assertEqual(r['new_event_ids'],[])
    def test_quiet_outbox_filters_invalid_old_event(self):
        now=datetime.fromisoformat('2026-10-02T22:00:00+00:00');state=empty_state()
        for adx in (25,26):
            state['events'][str(adx)]={'id':str(adx),'market':'us','signal_key':'macd12_26xsignal9','delivery_status':'pending','indicators':{'adx14':adx,'macd':1,'macd_signal':0},'previous_indicators':{'macd':0,'macd_signal':0}}
        pending,deferred,opened=eligible_events(state,[],'all','cycle',now)
        self.assertFalse(opened);self.assertEqual(deferred,1);self.assertEqual(pending,[])
        self.assertEqual(len(eligible_events(state,[],'all','cycle',now+timedelta(hours=3))[0]),1)
if __name__=='__main__':unittest.main()
