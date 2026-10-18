"""Replay actual Oct 17 failures. No real capture, input, paid skip, or OCR."""
import copy
import json
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image
from vendor_core.backend import MaaRecognizer
from vendor_core.config import validate
from vendor_core.runner import Runner
from vendor_core.unified import build_dungeon_profile, UnifiedScheduler
from tests.upstream.test_town_portal import Offline
from tests.upstream.test_runner import FakeBackend, Clock
from tests.upstream.test_selected_dungeons import plan_for

ROOT=Path(__file__).resolve().parents[2]

@unittest.skipUnless((ROOT/'tests/fixtures').is_dir(), 'Local replay screenshots are not distributed; see tests/README.md')
class DetailReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.controller=Offline(); cls.controller.post_connection().wait()
        cls.reco=MaaRecognizer(ROOT/'assets',cls.controller)
        cls.plan=plan_for(1,2,3)
        cls.base=json.loads((ROOT/'config/profiles/dungeon_unlimited.json').read_text(encoding='utf-8'))

    @classmethod
    def tearDownClass(cls): cls.reco.cancel().wait()

    def profile(self,index=1):
        return build_dungeon_profile(self.base,self.plan['tasks'][index],self.plan['tasks'])

    def frame(self,name):
        with Image.open(ROOT/'tests/fixtures'/name) as im:
            return np.asarray(im.convert('RGB'))[:,:,::-1].copy()

    def hit(self,image,rule): return self.reco.recognize(image,rule,threading.Event())

    def first_hit(self,image,data,state):
        r=Runner(self.reco,data,Mock()); r.stats.state=state
        for rule in r.ordered_rules():
            hit=self.hit(image,rule)
            if hit: return rule,hit
        return None,None

    def test_all_three_failures_close_old_details_on_fresh_entry(self):
        for name in ['20261017-102156-207946','20261017-102220-947571','20261017-102326-610305']:
            image=self.frame(f'failures_20261017/{name}.png')
            rule,hit=self.first_hit(image,self.profile(2),'Entry')
            self.assertIsNotNone(hit,name)
            self.assertEqual(rule['template'],'副本详情关闭.png')
            self.assertEqual(rule['next'],'Entry')
            self.assertNotIn('event',rule)
            self.assertEqual((hit.box[0]+hit.box[2]//2,hit.box[1]+hit.box[3]//2),(932,149))
            print('DETAIL_RECOVERY',name,hit.score,hit.box)

    def test_existing_detail_is_not_treated_as_selected_target(self):
        image=self.frame('failures_20261017/20261017-102326-610305.png')
        for state in ['Entry','SelectDungeon']:
            rule,hit=self.first_hit(image,self.profile(2),state)
            self.assertEqual(rule['template'],'副本详情关闭.png')
            self.assertNotEqual(rule.get('event'),'round_start')

    def test_ready_button_is_unchanged_and_not_a_width_bug(self):
        rule=next(r for r in self.profile()['states']['Explore']['rules'] if r['template']=='去探险.png')
        ready=self.frame('failures_20261017/20261017-102326-610305.png')
        self.assertGreater(self.hit(ready,rule).score,.99)
        for stem in ['20261017-102156-207946','20261017-102220-947571']:
            self.assertIsNone(self.hit(self.frame(f'failures_20261017/{stem}.png'),rule))

    def test_cooling_details_choose_close_not_paid_button(self):
        for stem in ['20261017-102156-207946','20261017-102220-947571']:
            rule,hit=self.first_hit(self.frame(f'failures_20261017/{stem}.png'),self.profile(),'Explore')
            self.assertEqual(rule.get('event'),'dungeon_deferred')
            self.assertEqual(rule['template'],'副本详情关闭.png')
            self.assertLess(hit.box[1],200)
        rule,hit=self.first_hit(self.frame('failures_20261017/20261017-102326-610305.png'),self.profile(),'Explore')
        self.assertEqual(rule['template'],'去探险.png')

    def test_selection_confirms_details_including_cooling_then_transitions_without_reclick(self):
        data=self.profile(); rule=data['states']['SelectDungeon']['rules'][0]
        image=self.frame('failures_20261017/20261017-102220-947571.png')
        self.assertIsNotNone(self.hit(image,dict(rule['success_match'],name='detail')))
        runner=Runner(self.reco,data,Mock(),False); runner.stats.state='SelectDungeon'
        runner.remember_click(rule)
        recovered,hit=runner.pending_success(image)
        self.assertEqual(recovered['action'],'none')
        self.assertEqual(recovered['next'],'Explore')
        self.assertEqual(runner.stats.rounds,0)

    def test_dimmed_and_unrelated_screens_never_dismiss_details(self):
        data=self.profile()
        close=next(r for r in data['states']['Entry']['rules'] if r['template']=='副本详情关闭.png')
        image=self.frame('failures_20261017/20261017-102326-610305.png')
        self.assertIsNone(self.hit((image.astype(float)*.2+30).astype(np.uint8),close))
        for name in ['failures_20261003/dungeon_list.png','failures_20261008/quest.png',
                     'failures_20261008/expired_session.png','failures_20261008/other_dungeon.png']:
            self.assertIsNone(self.hit(self.frame(name),close),name)

    def test_cooling_event_requires_confirmed_close_and_does_not_count(self):
        data=self.profile(); data['initial_state']='Explore'; data.pop('watchdog',None)
        rule=data['states']['Explore']['rules'][0]
        rule.update(confirm_frames=1,retry_wait_seconds=.001)
        data['poll_seconds']=.001
        backend=FakeBackend([{'副本详情关闭.png','副本冷却付费标识.png'},
                             '地下城列表通用标识.png','地下城列表通用标识.png'])
        r=Runner(backend,data,Mock(),False,Clock())
        with patch.object(r.stop_event,'wait',return_value=False): r.run()
        self.assertTrue(r.deferred); self.assertFalse(r.completed)
        self.assertEqual(r.stats.rounds,0); self.assertEqual(backend.clicks,1)
        self.assertTrue(any(h['event']=='success_evidence' for h in r.history))

    def test_failed_close_does_not_defer_or_count(self):
        data=self.profile(); data['initial_state']='Explore'; data.pop('watchdog',None)
        data['states']['Explore']['timeout_seconds']=2
        rule=data['states']['Explore']['rules'][0]; rule['confirm_frames']=1
        backend=FakeBackend([{'副本详情关闭.png','副本冷却付费标识.png'}])
        r=Runner(backend,data,Mock(),False,Clock())
        with patch.object(r.stop_event,'wait',return_value=False): r.run()
        self.assertFalse(r.deferred); self.assertEqual(r.stats.rounds,0)
        self.assertEqual(backend.clicks,2)

    def test_invalid_defer_rule_is_rejected(self):
        for field in ['success_match','requires_match','verify_change']:
            data=self.profile(); data['states']['Explore']['rules'][0].pop(field)
            with self.assertRaisesRegex(ValueError,'副本延后|verify_change'): validate(data,ROOT/'assets/image',True)

    def test_actual_frame_sequence_recovers_reselects_and_starts_once(self):
        detail=self.frame('failures_20261017/20261017-102326-610305.png')
        listing=self.frame('failures_20261016/title_click.png')
        entrance=self.frame('failures_20261003/buff_hover.png')
        frames=[detail,listing,listing,detail,entrance]
        clicks=[]
        backend=Mock()
        backend.capture.side_effect=lambda stop:frames[min(len(clicks),len(frames)-1)]
        backend.recognize.side_effect=self.hit_with_stop
        backend.capture_is_fresh.return_value=True
        backend.cancel.return_value=None
        def click(match,offset,stop,pause):
            x,y,w,h=match.box
            clicks.append((x+w//2+offset[0],y+h//2+offset[1]))
        backend.click.side_effect=click
        data=self.profile(); data.pop('watchdog',None)
        def emit(kind,value):
            if kind=='log' and value=='状态转换：Explore → Progress': runner.stop_event.set()
        runner=Runner(backend,data,emit,False,Clock())
        with patch.object(runner.stop_event,'wait',return_value=False): runner.run()
        self.assertEqual(runner.stats.state,'Progress',runner.reason)
        self.assertEqual(len(clicks),4,clicks)
        self.assertEqual(clicks[0],(932,149))
        self.assertEqual(clicks[2],(633,325))
        self.assertEqual(clicks[3],(697,538))
        self.assertEqual(runner.stats.rounds,0)
        self.assertFalse(runner.deferred)

    def hit_with_stop(self,image,rule,stop):
        return self.reco.recognize(image,rule,stop)

class CooldownSchedulingTests(unittest.TestCase):
    def scheduler(self,plan,callback,diamond=None):
        now=[0.]; stop=threading.Event()
        def wait(s): now[0]+=s; return stop.is_set()
        obj=UnifiedScheduler(plan,callback,diamond or Mock(return_value=(True,'ack')),stop,Mock(),clock=lambda:now[0],wait=wait)
        obj.ready_at={t['id']:0 for t in obj.tasks}
        return obj,now

    def test_cooling_defers_without_count_then_waits_and_retries_selected_only(self):
        plan=plan_for(2); plan['max_total_runs']=1
        seen=[]
        def run(t):
            seen.append(t['order']); return (None,'cooling') if len(seen)==1 else (True,'done')
        s,now=self.scheduler(plan,run); s.run()
        self.assertEqual(seen,[2,2]); self.assertEqual(now[0],300)
        self.assertEqual(s.completed_runs,1); self.assertTrue(s.succeeded)
        self.assertIsNone(s.in_flight)

    def test_other_ready_selected_dungeon_can_run_after_deferral(self):
        plan=plan_for(2,3); plan['max_total_runs']=1; seen=[]
        def run(t):
            seen.append(t['order']); return (None,'cooling') if t['order']==2 else (True,'done')
        s,_=self.scheduler(plan,run); s.run()
        self.assertEqual(seen,[2,3]); self.assertEqual(s.completed_runs,1)

    def test_due_diamonds_preempt_retry_wait(self):
        plan=plan_for(2); plan['max_total_runs']=1; plan['diamonds']['enabled']=True
        calls=[]
        def run(t):
            calls.append('dungeon'); return (None,'cooling') if calls==['dungeon'] else (True,'done')
        s,_=self.scheduler(plan,run,lambda:(calls.append('diamond') is None,'ack'))
        s.diamond_due=10; s.run()
        self.assertEqual(calls,['dungeon','diamond','dungeon'])

    def test_repeated_cooling_is_bounded_and_never_success(self):
        plan=plan_for(2); callback=Mock(return_value=(None,'cooling'))
        s,_=self.scheduler(plan,callback); s.run()
        self.assertEqual(callback.call_count,3); self.assertEqual(s.completed_runs,0)
        self.assertFalse(s.succeeded); self.assertIn('连续 3 次',s.reason)

    def test_unlimited_cooling_is_an_error_not_a_hot_loop(self):
        callback=Mock(return_value=(None,'cooling'))
        s,_=self.scheduler(plan_for(1),callback); s.run()
        self.assertEqual(callback.call_count,1); self.assertFalse(s.succeeded)

    def test_stop_during_cooldown_wait_does_not_retry(self):
        callback=Mock(return_value=(None,'cooling'))
        s,_=self.scheduler(plan_for(2),callback)
        s.wait=lambda delay:(s.stop.set() or True); s.run()
        self.assertEqual(callback.call_count,1); self.assertFalse(s.succeeded)


if __name__=='__main__': unittest.main()
