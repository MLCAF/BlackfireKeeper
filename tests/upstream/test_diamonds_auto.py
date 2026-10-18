"""Real video pixels and Maa OCR, but fake inputs/clock: no live game or purchases."""
import copy,json,threading,unittest,time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
import numpy as np
from PIL import Image
from vendor_core.backend import MaaRecognizer,Match
from vendor_core.config import validate
from vendor_core.cooldown import DeferredCooldown,TimerSample,CooldownEstimate
from vendor_core.diamonds import DiamondResult,read_free_pack_timer
from vendor_core.runner import Runner
from vendor_core.unified import UnifiedScheduler
from tests.upstream.test_town_portal import Offline
from tests.upstream.test_selected_dungeons import plan_for

ROOT=Path(__file__).resolve().parents[2]
def profile():return json.loads((ROOT/'config/profiles/diamonds.json').read_text(encoding='utf-8'))

class VideoBackend:
    def __init__(self,reco,frames,initial=7,fail_click=False):
        self.reco,self.frames,self.page=reco,frames,initial
        self.fail_click=fail_click;self.clicks=[]
    def capture(self,stop):return self.frames[self.page]
    def recognize(self,*args):return self.reco.recognize(*args)
    def read_timer_roi(self,*args):return self.reco.read_timer_roi(*args)
    def capture_is_fresh(self,*args):return True
    def guard(self):pass
    def cancel(self):pass
    def click(self,match,offset,stop,pause):
        if stop.is_set() or pause.is_set():raise InterruptedError()
        x,y,w,h=match.box;point=(x+w//2+offset[0],y+h//2+offset[1])
        self.clicks.append((self.page,point))
        # The only permitted points are the diamond icon, FREE, reward confirm, close.
        bounds={7:(960,0,1060,100),4:(330,495,440,555),5:(490,450,910,510),6:(1160,110,1220,175)}
        a,b,c,d=bounds[self.page]
        assert a<=point[0]<=c and b<=point[1]<=d,('Unexpected/paid click',self.page,point)
        if not self.fail_click:self.page={7:4,4:5,5:6,6:7}[self.page]

@unittest.skipUnless((ROOT/'tests/fixtures').is_dir(), 'Local replay screenshots are not distributed; see tests/README.md')
class DiamondVideoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        c=Offline();c.post_connection().wait();cls.reco=MaaRecognizer(ROOT/'assets',c)
        cls.frames={}
        for n in (0,1,3,4,5,6,7):
            with Image.open(ROOT/f'tests/fixtures/diamonds_20261017/frame_{n:03}.jpg') as im:
                cls.frames[n]=np.asarray(im.convert('RGB'))[:,:,::-1].copy()
    @classmethod
    def tearDownClass(cls):cls.reco.cancel().wait()
    def run_video(self,initial=7,fail=False):
        now=[100.]
        def clock():now[0]+=.3;return now[0]
        backend=VideoBackend(self.reco,self.frames,initial,fail)
        runner=Runner(backend,profile(),Mock(),False,clock)
        with patch.object(runner.stop_event,'wait',return_value=False):runner.run()
        return runner,backend
    def test_full_claim_then_verified_close(self):
        runner,backend=self.run_video()
        self.assertTrue(runner.completed,runner.reason)
        self.assertEqual(runner.counters.get('diamond_rewards'),1)
        self.assertEqual(runner.cooldown_estimate.text,'01:59:57')
        self.assertEqual([page for page,point in backend.clicks],[7,4,5,6])
        self.assertEqual(runner.stats.rounds,0)
    def test_already_cooling_only_closes_without_free_or_paid_click(self):
        runner,backend=self.run_video(6)
        self.assertTrue(runner.completed,runner.reason)
        self.assertEqual(runner.counters.get('diamond_rewards',0),0)
        self.assertEqual([p for p,_ in backend.clicks],[6])
    def test_resume_reward_acknowledges_exact_diamond_reward(self):
        runner,backend=self.run_video(5)
        self.assertTrue(runner.completed,runner.reason)
        self.assertEqual([p for p,_ in backend.clicks],[5,6])
        self.assertEqual(runner.counters['diamond_rewards'],1)
    def test_unchanged_click_stops_bounded_without_success(self):
        runner,backend=self.run_video(4,True)
        self.assertFalse(runner.completed)
        self.assertLessEqual(len(backend.clicks),2)
        self.assertGreater(len(backend.clicks),0)
    def test_reward_modal_blocks_free_pack_and_close(self):
        for state in ('InspectPack','CloseShop'):
            for rule in profile()['states'][state]['rules']:
                if rule.get('action')=='click' or rule.get('event')=='diamond_timer':
                    self.assertIsNone(self.reco.recognize(self.frames[5],rule,threading.Event()))
    def test_free_template_rejects_paid_cards_and_countdown(self):
        rule=profile()['states']['InspectPack']['rules'][1]
        self.assertIsNone(self.reco.recognize(self.frames[6],rule,threading.Event()))
        # Search paid buttons explicitly: FREE text must not match either price.
        rule=copy.deepcopy(rule);rule['roi']=[530,480,620,90]
        self.assertIsNone(self.reco.recognize(self.frames[4],rule,threading.Event()))
    def test_countdown_from_real_video(self):
        sample=self.reco.read_timer_roi(self.frames[6],threading.Event(),100,(325,507,110,32))
        self.assertEqual(sample.text,'01:59:57');self.assertEqual(sample.seconds,7197)
    def test_all_profile_templates_exist_and_validate(self):validate(profile(),ROOT/'assets/image',True)
    def test_wrong_or_missing_reward_icon_cannot_be_confirmed(self):
        frame=self.frames[5].copy();frame[310:408,650:750]=0
        rule=profile()['states']['Reward']['rules'][0]
        self.assertIsNone(self.reco.recognize(frame,rule,threading.Event()))

class DiamondEvidenceTests(unittest.TestCase):
    def runner(self,samples):
        backend=Mock();backend.recognize.return_value=Match((245,240,270,41),1)
        backend.read_timer_roi.side_effect=samples
        runner=Runner(backend,profile(),Mock(),False,clock=iter([100.,100.3]).__next__)
        runner.stop_event.wait=Mock(return_value=False)
        rule=profile()['states']['InspectPack']['rules'][2]
        return runner,rule
    def test_unknown_ocr_never_advances_to_close(self):
        runner,rule=self.runner([None])
        self.assertFalse(read_free_pack_timer(runner,rule));self.assertIsNone(runner.cooldown_estimate)
    def test_increasing_timer_rejected(self):
        runner,rule=self.runner([TimerSample('00:10:00',600,.99,100),TimerSample('00:10:01',601,.99,100.3)])
        self.assertFalse(read_free_pack_timer(runner,rule))
    def test_more_than_two_hours_rejected(self):
        runner,rule=self.runner([TimerSample('03:00:00',10800,.99,100)])
        self.assertFalse(read_free_pack_timer(runner,rule))
    def test_pause_or_stop_never_reads(self):
        for field in ('pause_event','stop_event'):
            runner,rule=self.runner([]);getattr(runner,field).set()
            if field=='stop_event':
                with self.assertRaises(InterruptedError):read_free_pack_timer(runner,rule)
            else:self.assertFalse(read_free_pack_timer(runner,rule))
            runner.backend.read_timer_roi.assert_not_called()
    def test_moving_card_rejected(self):
        runner,rule=self.runner([TimerSample('00:10:00',600,.99,100)])
        runner.backend.recognize.side_effect=[Match((245,240,270,41),1),Match((255,240,270,41),1)]
        self.assertFalse(read_free_pack_timer(runner,rule))
    def test_config_rejects_timer_with_click(self):
        data=profile();data['states']['InspectPack']['rules'][2]['action']='click'
        with self.assertRaises(ValueError):validate(data,ROOT/'assets/image',True)
    def test_unknown_ocr_eventually_stops_without_close_or_success(self):
        from tests.upstream.test_runner import FakeBackend,Clock
        backend=FakeBackend([{'钻石免费包标识.png'}]);backend.read_timer_roi=Mock(return_value=None)
        data=profile();data['initial_state']='InspectPack'
        runner=Runner(backend,data,Mock(),False,Clock(step=1))
        with patch.object(runner.stop_event,'wait',return_value=False):runner.run()
        self.assertFalse(runner.completed);self.assertEqual(backend.clicks,0)
        self.assertIsNone(runner.cooldown_estimate)
        self.assertTrue('超时' in runner.reason or '无进展' in runner.reason,runner.reason)

class DiamondScheduleTests(unittest.TestCase):
    def test_exact_deadline_persisted_and_dungeon_continues(self):
        plan=plan_for(1);plan['max_total_runs']=1;plan['diamonds'].update(enabled=True,run_on_start=True)
        events=[];store=Mock();store.load.return_value={}
        result=DiamondResult(DeferredCooldown(703,'00:10:00',.99),False)
        scheduler=UnifiedScheduler(plan,lambda t:(events.append('dungeon') is None,'done'),
            lambda:(events.append('diamond') is None and result,'cooling'),threading.Event(),Mock(),store,clock=lambda:100)
        scheduler.run()
        self.assertEqual(events,['diamond','dungeon']);self.assertEqual(scheduler.diamond_due,703)
        self.assertEqual(store.save.call_args.args[0]['diamond_due'],703)
        self.assertTrue(scheduler.succeeded)
    def test_invalid_diamond_deadline_stops_without_running_dungeon(self):
        plan=plan_for(1);plan['diamonds'].update(enabled=True,run_on_start=True)
        run=Mock();scheduler=UnifiedScheduler(plan,run,lambda:(DiamondResult(DeferredCooldown(20000,'03:00:00',.99),False),'bad'),threading.Event(),Mock(),clock=lambda:100)
        scheduler.run();run.assert_not_called();self.assertFalse(scheduler.succeeded)
    def test_stop_after_diamond_does_not_update_deadline(self):
        plan=plan_for(1);plan['diamonds'].update(enabled=True,run_on_start=True);stop=threading.Event()
        def diamond():stop.set();return DiamondResult(DeferredCooldown(703,'00:10:00',.99),True),'done'
        scheduler=UnifiedScheduler(plan,Mock(),diamond,stop,Mock(),clock=lambda:100)
        scheduler.run();self.assertEqual(scheduler.diamond_due,100)
    def test_diamond_due_inside_dungeon_waits_for_boundary_even_final_round(self):
        now=[0.];events=[];plan=plan_for(1);plan['max_total_runs']=1
        plan['diamonds'].update(enabled=True,run_on_start=False)
        def dungeon(task):events.append('dungeon_start');now[0]=7201;events.append('dungeon_end');return True,'done'
        def diamond():events.append('diamond');return DiamondResult(DeferredCooldown(7804,'00:10:00',.99),False),'cooling'
        scheduler=UnifiedScheduler(plan,dungeon,diamond,threading.Event(),Mock(),clock=lambda:now[0])
        scheduler.run();self.assertEqual(events,['dungeon_start','dungeon_end','diamond']);self.assertTrue(scheduler.succeeded)

if __name__=='__main__':unittest.main()
