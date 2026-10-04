import copy,json,unittest,subprocess,sys,tempfile
from pathlib import Path
from datetime import datetime,date,timedelta,timezone
from test_engine import CFG,ITEM,fixture,sma_test_indicators
from unittest.mock import patch
from test_provisional import setup
from stock_monitor.engine import empty_state
from stock_monitor.calendar import expected_latest
from stock_monitor.cycle import plan_cycle,evaluate_cycle,idle_result,eligible_events

class TestCycle(unittest.TestCase):
    def setUp(self):
        self.p=patch("stock_monitor.provisional.indicators",side_effect=sma_test_indicators);self.p.start();self.addCleanup(self.p.stop)
    def state_at(self,now,item=ITEM):
        state=empty_state();d=expected_latest(now,item['market'],30).isoformat()
        state['tickers'][item['symbol']]={'market':item['market'],'last_processed_bar_date':d,'baseline_bar_date':d}
        return state
    def test_closed_and_current_needs_no_fetch(self):
        now=datetime.fromisoformat('2026-10-02T08:00:00+00:00');state=self.state_at(now)
        p=plan_cycle(ITEM,now,CFG,state);self.assertFalse(p['network_fetch_needed']);self.assertEqual(idle_result(ITEM,p)['status'],'skipped')
    def test_open_and_current_only_provisional(self):
        now=datetime.fromisoformat('2026-10-02T15:00:00+00:00');state=self.state_at(now)
        p=plan_cycle(ITEM,now,CFG,state);self.assertFalse(p['confirmed_due']);self.assertTrue(p['provisional_due'])
    def test_unprocessed_close_needs_confirmed(self):
        now=datetime.fromisoformat('2026-10-02T21:00:00+00:00');state=self.state_at(now)
        state['tickers']['NVDA']['last_processed_bar_date']='2026-10-01'
        p=plan_cycle(ITEM,now,CFG,state);self.assertTrue(p['confirmed_due']);self.assertFalse(p['provisional_due'])
    def test_one_payload_serves_both_checks(self):
        payload,now,ds=setup();state=empty_state();state['tickers']['NVDA']={'market':'us','last_processed_bar_date':ds[-3],'baseline_bar_date':ds[-3]}
        p=plan_cycle(ITEM,now,CFG,state);self.assertTrue(p['confirmed_due']);self.assertTrue(p['provisional_due'])
        r=evaluate_cycle(ITEM,payload,{},now,CFG,state,p)
        self.assertEqual([x['signal_mode'] for x in r['checks']],['confirmed','provisional'])
        self.assertEqual(state['tickers']['NVDA']['last_processed_bar_date'],ds[-2])
        self.assertEqual(len(state['events']),1);self.assertTrue(r['checks'][1]['potential_golden_cross'])
    def test_closed_market_releases_confirmed_outbox_without_data(self):
        now=datetime.fromisoformat('2026-10-03T01:00:00+00:00') # Saturday10JST, both markets closed
        state=self.state_at(now);eid='NVDA:2026-10-02:sma5x20'
        state['events'][eid]={'id':eid,'market':'us','delivery_status':'pending','signal_key':'sma5x20','indicators':{'adx14':26,'sma5':2,'sma20':1},'previous_indicators':{'sma5':1,'sma20':1}}
        p=plan_cycle(ITEM,now,CFG,state);self.assertFalse(p['network_fetch_needed'])
        pending,deferred,window=eligible_events(state,[idle_result(ITEM,p)],'all','cycle',now)
        self.assertTrue(window);self.assertEqual(pending,[state['events'][eid]]);self.assertEqual(deferred,0)
    def test_quiet_hours_keep_confirmed_pending(self):
        now=datetime.fromisoformat('2026-10-02T22:00:00+00:00') #07JST
        state=empty_state();eid='NVDA:2026-10-02:sma5x20';state['events'][eid]={'id':eid,'market':'us','delivery_status':'pending','signal_key':'sma5x20','indicators':{'adx14':26,'sma5':2,'sma20':1},'previous_indicators':{'sma5':1,'sma20':1}}
        pending,deferred,window=eligible_events(state,[],'all','cycle',now)
        self.assertFalse(window);self.assertEqual(pending,[]);self.assertEqual(deferred,1);self.assertEqual(state['events'][eid]['delivery_status'],'pending')
    def test_no_stale_provisional_delivery_when_closed(self):
        now=datetime.fromisoformat('2026-10-03T01:00:00+00:00');state=empty_state();eid='NVDA:2026-10-02:sma5x20:provisional'
        state['events'][eid]={'id':eid,'market':'us','delivery_status':'pending','signal_status':'provisional_intraday'}
        self.assertEqual(eligible_events(state,[],'all','cycle',now)[0],[])
    def test_state_ahead_fail_closed(self):
        now=datetime.fromisoformat('2026-10-02T08:00:00+00:00');state=self.state_at(now);state['tickers']['NVDA']['last_processed_bar_date']='2026-10-02'
        with self.assertRaisesRegex(ValueError,'state_ahead'):plan_cycle(ITEM,now,CFG,state)
    def test_cli_cycle_no_reads_when_closed_state_current(self):
        root=Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as d:
            d=Path(d);(d/'empty-replay').mkdir()
            # Empty replay directory proves no provider payload is accessed.
            cmd=[sys.executable,'-m','stock_monitor','--mode','cycle','--state',str(root/'data/baseline-state.json'),
                 '--replay-dir',str(d/'empty-replay'),'--as-of','2026-10-02T08:00:00Z','--state-out',str(d/'state.json'),'--output',str(d/'out.json')]
            run=subprocess.run(cmd,cwd=root,capture_output=True,text=True);self.assertEqual(run.returncode,0,run.stderr)
            r=json.loads((d/'out.json').read_text());self.assertEqual(r['skipped_count'],25);self.assertEqual(r['network_fetch_count'],0)
            self.assertTrue(r['state_persistence_required']);self.assertEqual(r['state_revision'],2) # one-time ruleset migration
            cmd[cmd.index('--state')+1]=str(d/'state.json')
            run=subprocess.run(cmd,cwd=root,capture_output=True,text=True);self.assertEqual(run.returncode,0,run.stderr)
            r=json.loads((d/'out.json').read_text());self.assertFalse(r['state_persistence_required']);self.assertEqual(r['state_revision'],2)
if __name__=='__main__':unittest.main()
