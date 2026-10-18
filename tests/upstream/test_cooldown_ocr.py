"""Local OCR, fresh-frame guards and actual-deadline scheduling; never game IO."""
import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image
from vendor_core.backend import MaaRecognizer
from vendor_core.cooldown import (parse_timer, timer_sample, confirm_countdown,
                          CooldownEstimate, DeferredCooldown, TimerSample)
from vendor_core.runner import Runner
from vendor_core.unified import build_dungeon_profile, UnifiedScheduler, ScheduleStore
from tests.upstream.test_runner import FakeBackend, Clock
from tests.upstream.test_town_portal import Offline
from tests.upstream.test_selected_dungeons import plan_for

ROOT=Path(__file__).resolve().parents[2]

def profile():
    plan=plan_for(2)
    base=json.loads((ROOT/'config/profiles/dungeon_unlimited.json').read_text(encoding='utf-8'))
    return build_dungeon_profile(base,plan['tasks'][1],plan['tasks'])

class CountdownValueTests(unittest.TestCase):
    def test_hh_mm_ss_and_buffer(self):
        for text,seconds in [('00:00:04',4),('00:00:27',27),('00:15:00',900),('01:00:00',3600),('00:00:00',0),('23:59:59',86399)]:
            self.assertEqual(parse_timer(text),seconds)
        self.assertEqual(parse_timer(' 00：15：00 '),900)

    def test_malformed_text_never_means_zero(self):
        for text in ['','0','00:27','00:60:00','00:00:60','24:00:00','O0:00:04',
                     '00:00:O4','00:00:04 1钻石','去探险！','-00:00:01','００:００:０４',None,4]:
            self.assertIsNone(parse_timer(text),text)

    def test_low_confidence_and_nonfinite_rejected(self):
        for score in [.949,float('nan'),float('inf'),True,'1',1.01]:
            self.assertIsNone(timer_sample('00:00:04',score,100))

    def test_pair_accepts_same_second_and_countdown(self):
        first=timer_sample('00:00:27',.99,100)
        same=timer_sample('00:00:27',.99,100.3)
        self.assertAlmostEqual(confirm_countdown(first,same).deadline,130.3)
        later=timer_sample('00:00:25',.98,102.1)
        estimate=confirm_countdown(first,later)
        self.assertAlmostEqual(estimate.deadline,130.1)
        self.assertAlmostEqual(estimate.to_schedule(105,1000).ready_at,1025.1)

    def test_pair_rejects_jump_increase_old_or_invalid_frame(self):
        first=timer_sample('00:00:27',.99,100)
        for second in [None,timer_sample('00:00:28',.99,100.5),timer_sample('00:00:04',.99,100.5),
                       timer_sample('00:00:27',.99,100),timer_sample('00:00:27',.99,120),
                       TimerSample('00:00:27',27,.5,100.3)]:
            self.assertIsNone(confirm_countdown(first,second))

    def test_clock_conversion_counts_close_time_not_a_new_delay(self):
        estimate=confirm_countdown(timer_sample('00:00:04',.99,10),timer_sample('00:00:03',.99,11))
        self.assertEqual(estimate.deadline,17)
        self.assertEqual(estimate.to_schedule(14,1000).ready_at,1003)
        self.assertEqual(estimate.to_schedule(20,1000).ready_at,1001)

@unittest.skipUnless((ROOT/'tests/fixtures').is_dir(), 'Local replay screenshots are not distributed; see tests/README.md')
class CountdownReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.controller=Offline(); cls.controller.post_connection().wait()
        cls.reco=MaaRecognizer(ROOT/'assets',cls.controller)

    @classmethod
    def tearDownClass(cls): cls.reco.cancel().wait()

    def frame(self,name):
        with Image.open(ROOT/f'tests/fixtures/failures_20261017/{name}.png') as im:
            return np.asarray(im.convert('RGB'))[:,:,::-1].copy()

    def test_real_27_seconds_and_4_seconds(self):
        for stem,expected in [('20261017-102156-207946',27),('20261017-102220-947571',4)]:
            image=self.frame(stem)
            first=self.reco.read_cooldown(image,threading.Event(),100)
            second=self.reco.read_cooldown(image,threading.Event(),100.3)
            self.assertIsNotNone(first)
            self.assertEqual(first.seconds,expected)
            estimate=confirm_countdown(first,second)
            self.assertIsNotNone(estimate)
            print(f'COOLDOWN_OCR {stem}: {first.text}, score={first.score:.6f}, buffered_delay={estimate.deadline-100.3:.1f}')

    def test_ready_label_and_blank_not_zero_cooldown(self):
        image=self.frame('20261017-102326-610305')
        self.assertIsNone(self.reco.read_cooldown(image,threading.Event(),100))
        self.assertIsNone(self.reco.read_cooldown(np.zeros_like(image),threading.Event(),100))

    def test_missing_model_returns_none(self):
        with patch.object(Path,'is_file',return_value=False):
            self.assertIsNone(self.reco.read_cooldown(self.frame('20261017-102220-947571'),threading.Event(),100))

    def test_cropped_or_small_screenshot_not_accepted(self):
        self.assertIsNone(self.reco.read_cooldown(np.zeros((50,50,3),dtype=np.uint8),threading.Event(),100))

class CountdownRunnerTests(unittest.TestCase):
    def make(self,texts=('00:00:27','00:00:26'),frames=None):
        cooling={'副本详情关闭.png','副本冷却付费标识.png'}
        backend=FakeBackend(frames or [cooling,cooling])
        values=iter(texts)
        backend.read_cooldown=Mock(side_effect=lambda image,stop,captured:timer_sample(next(values),.99,captured))
        runner=Runner(backend,profile(),Mock(),False,Clock(step=.5))
        runner.stats.state='Explore'
        return runner,backend,runner.config['states']['Explore']['rules'][0]

    def test_two_fresh_reads_before_close(self):
        r,b,rule=self.make()
        with patch.object(r.stop_event,'wait',return_value=False): self.assertTrue(r.read_cooldown_before_close(rule))
        self.assertIsNotNone(r.cooldown_estimate)
        self.assertEqual(b.index,2); self.assertEqual(b.read_cooldown.call_count,2)
        self.assertEqual(b.clicks,0)

    def test_scene_changes_or_timer_ready_returns_to_discovery(self):
        cooling={'副本详情关闭.png','副本冷却付费标识.png'}
        r,b,rule=self.make(frames=[cooling,'去探险.png'])
        with patch.object(r.stop_event,'wait',return_value=False): self.assertFalse(r.read_cooldown_before_close(rule))
        self.assertIsNone(r.cooldown_estimate); self.assertEqual(b.clicks,0)

    def test_bad_ocr_falls_back_without_guessing(self):
        r,b,rule=self.make(('00:00:27','00:00:04'))
        with patch.object(r.stop_event,'wait',return_value=False): self.assertTrue(r.read_cooldown_before_close(rule))
        self.assertIsNone(r.cooldown_estimate)

    def test_ocr_exception_falls_back_but_capture_error_propagates(self):
        r,b,rule=self.make(); b.read_cooldown.side_effect=RuntimeError('model unavailable')
        self.assertTrue(r.read_cooldown_before_close(rule)); self.assertIsNone(r.cooldown_estimate)
        b.capture=Mock(side_effect=RuntimeError('window gone'))
        with self.assertRaisesRegex(RuntimeError,'window gone'): r.read_cooldown_before_close(rule)

    def test_stop_and_pause_send_no_input(self):
        r,b,rule=self.make(); r.pause_event.set()
        self.assertFalse(r.read_cooldown_before_close(rule)); self.assertEqual(b.index,0)
        r.pause_event.clear(); r.stop_event.set()
        with self.assertRaises(InterruptedError): r.read_cooldown_before_close(rule)
        self.assertEqual(b.clicks,0)

    def test_confirmed_close_retains_estimate_but_does_not_count(self):
        cooling={'副本详情关闭.png','副本冷却付费标识.png'}
        frames=[cooling]*3+['地下城列表通用标识.png']*2
        r,b,rule=self.make(frames=frames); rule['confirm_frames']=1
        r.config['initial_state']='Explore'; r.watchdog=None
        with patch.object(r.stop_event,'wait',return_value=False): r.run()
        self.assertTrue(r.deferred,r.reason); self.assertIsNotNone(r.cooldown_estimate)
        self.assertEqual(r.stats.rounds,0); self.assertEqual(b.clicks,1)

    def test_read_success_but_close_failure_never_defers(self):
        r,b,rule=self.make()
        rule['confirm_frames']=1; r.config['initial_state']='Explore'; r.watchdog=None
        with patch.object(r.stop_event,'wait',return_value=False): r.run()
        self.assertIsNotNone(r.cooldown_estimate)
        self.assertFalse(r.deferred); self.assertFalse(r.completed)
        self.assertEqual(r.stats.rounds,0); self.assertEqual(b.clicks,2)

    def test_observation_mode_never_reads_or_closes(self):
        r,b,rule=self.make(); r.dry_run=True; r.config['initial_state']='Explore'
        r.config['max_minutes']=.01; r.watchdog=None
        with patch.object(r.stop_event,'wait',return_value=False): r.run()
        b.read_cooldown.assert_not_called(); self.assertEqual(b.clicks,0)

class CountdownScheduleTests(unittest.TestCase):
    def scheduler(self,callback,diamond=None,plan=None,store=None):
        plan=plan or plan_for(2); plan['max_total_runs']=1
        now=[0.]; stop=threading.Event()
        def wait(seconds): now[0]+=seconds; return stop.is_set()
        s=UnifiedScheduler(plan,callback,diamond or Mock(return_value=(True,'ack')),stop,Mock(),
                           store=store,clock=lambda:now[0],wait=wait)
        s.ready_at.update({t['id']:0 for t in s.tasks})
        return s,now

    def test_27_seconds_plus_buffer_not_full_300_seconds(self):
        calls=[]
        def run(task):
            calls.append(now[0]); return (DeferredCooldown(30,'00:00:27',.99),'cooling') if len(calls)==1 else (True,'done')
        s,now=self.scheduler(run); s.run()
        self.assertEqual(calls,[0,30]); self.assertEqual(s.completed_runs,1)

    def test_four_seconds_plus_buffer(self):
        calls=[]
        def run(task):
            calls.append(now[0]); return (DeferredCooldown(7,'00:00:04',.99),'cooling') if len(calls)==1 else (True,'done')
        s,now=self.scheduler(run); s.run()
        self.assertEqual(calls,[0,7]); self.assertTrue(s.succeeded)

    def test_deadline_accounts_for_time_already_spent_closing(self):
        calls=[]
        def run(task):
            calls.append(now[0])
            if len(calls)==1:
                now[0]=3
                return DeferredCooldown(7,'00:00:04',.99),'cooling'
            return True,'done'
        s,now=self.scheduler(run); s.run()
        self.assertEqual(calls,[0,7])

    def test_due_diamonds_run_first_while_waiting_for_exact_deadline(self):
        plan=plan_for(2); plan['diamonds']['enabled']=True; events=[]
        def run(task):
            events.append(('dungeon',now[0]))
            return (DeferredCooldown(30,'00:00:27',.99),'cooling') if len(events)==1 else (True,'done')
        s,now=self.scheduler(run,lambda:(events.append(('diamond',now[0])) is None,'ack'),plan)
        s.diamond_due=10; s.run()
        self.assertEqual(events,[('dungeon',0),('diamond',10),('dungeon',30)])

    def test_invalid_ocr_deadline_uses_existing_fallback(self):
        for deadline in [float('nan'),1e20,-1000]:
            calls=[]
            def run(task):
                calls.append(now[0]); return (DeferredCooldown(deadline,'00:00:04',.99),'bad') if len(calls)==1 else (True,'done')
            s,now=self.scheduler(run); s.run()
            self.assertEqual(calls,[0,300])

    def test_deadline_persisted_and_no_inflight_reservation_after_safe_close(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as folder:
            store=ScheduleStore(Path(folder)/'state.json')
            s,now=self.scheduler(Mock(return_value=(DeferredCooldown(30,'00:00:27',.99),'cooling')),store=store)
            s.wait=lambda seconds:(s.stop.set() or True)
            s.run(); saved=store.load()
            self.assertEqual(saved['ready_at']['dungeon_2'],30); self.assertIsNone(saved['in_flight'])
            restarted=UnifiedScheduler(plan_for(2),Mock(),Mock(),threading.Event(),Mock(),store,clock=lambda:10)
            self.assertEqual(restarted.ready_at['dungeon_2'],30)


if __name__=='__main__': unittest.main()
