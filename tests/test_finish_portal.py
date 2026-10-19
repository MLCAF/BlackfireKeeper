"""Completion portal regression: real recognition with game input prohibited."""
import copy
import json
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import numpy as np
from PIL import Image
from bk2.profiles import build_dungeon_profile
from bk2.recovery import RecoveryRunner, add_resume_start
from vendor_core.backend import MaaRecognizer
from vendor_core.config import validate
from tests.test_unified_replay import Offline

ROOT=Path(__file__).resolve().parents[1]
FIX=ROOT/'tests/fixtures/finish_portal_20261019'


class Frames:
    @classmethod
    def setUpClass(cls):
        cls.controller=Offline();cls.controller.post_connection().wait()
        cls.reco=MaaRecognizer(ROOT/'assets',cls.controller)
        cls.plan=json.loads((ROOT/'config/dungeon_plan.json').read_text(encoding='utf-8-sig'))
        cls.base=json.loads((ROOT/'config/profiles/dungeon_unlimited.json').read_text(encoding='utf-8-sig'))
        cls.config=build_dungeon_profile(cls.base,cls.plan['tasks'][5],cls.plan['tasks'])
        cls.finish=next(r for r in cls.config['states']['Progress']['rules']
                        if r['template']=='完成冒险传送门核心.png')
    @classmethod
    def tearDownClass(cls):cls.reco.cancel().wait()
    def read(self,path):
        with Image.open(path) as image:return np.asarray(image.convert('RGB'))[:,:,::-1].copy()
    def frame(self,parts):
        frame=np.full((720,1395,3),30,np.uint8)
        for name,x,y in parts:
            im=self.read(ROOT/'assets/image'/name);h,w=im.shape[:2]
            frame[y:y+h,x:x+w]=im
        return frame
    def finish_frame(self):
        return self.frame([('完成冒险传送门核心.png',669,610),('完成冒险文字.png',656,684)])
    def hit(self,frame,rule=None):return self.reco.recognize(frame,rule or self.finish,threading.Event())
    def replay(self,first=None,after=None,stuck=False,resume=True):
        first=self.finish_frame() if first is None else first
        after=self.frame([('地下城导航选中.png',552,580)]) if after is None else after
        test=self
        class Backend:
            def __init__(self):self.clicks=[];self.page=0;self.save_diagnostic=Mock(return_value='offline.json')
            def guard(self):pass
            def cancel(self):pass
            def capture_is_fresh(self,*args):return True
            def capture(self,stop):return first if self.page==0 else after
            def recognize(self,*args):return test.reco.recognize(*args)
            def click(self,match,offset,stop,pause):
                if stop.is_set() or pause.is_set():raise InterruptedError()
                self.clicks.append(match.box)
                if not stuck:self.page=1
        backend=Backend();config=copy.deepcopy(self.config)
        config.update(initial_state='Progress',max_minutes=1,max_rounds=1)
        # Short virtual watchdog for the negative replay; production stays 30/120.
        config['watchdog']=dict(warn_seconds=5,stop_seconds=10,repeat_warning_seconds=5)
        if resume:add_resume_start(config)
        clock=[100.]
        def now():clock[0]+=.5;return clock[0]
        runner=RecoveryRunner(backend,validate(config,ROOT/'assets/image',True),Mock(),False,now)
        with patch.object(runner.stop_event,'wait',return_value=False):runner.run()
        return runner,backend


class FinishPortalTests(Frames,unittest.TestCase):
    def test_both_evidence_parts_required(self):
        self.assertIsNotNone(self.hit(self.finish_frame()))
        for parts in ([('完成冒险文字.png',656,684)],[('完成冒险传送门核心.png',669,610)]):
            self.assertIsNone(self.hit(self.frame(parts)))
    def test_dimmed_modal_background_rejected(self):
        self.assertIsNone(self.hit((self.finish_frame()*.5).astype(np.uint8)))
    def test_wrong_location_rejected(self):
        self.assertIsNone(self.hit(self.frame([('完成冒险传送门核心.png',100,200),('完成冒险文字.png',90,274)])))
    def test_staircase_and_mismatched_text_rejected(self):
        for parts in ([('下一层阶梯核心.png',672,610),('下一层文字.png',659,687)],
                      [('完成冒险传送门核心.png',669,610),('下一层文字.png',659,687)]):
            self.assertIsNone(self.hit(self.frame(parts)))
    def test_all_dungeons_keep_original_threshold_and_return_semantics(self):
        original=copy.deepcopy(self.base)
        for task in self.plan['tasks'][:6]:
            config=build_dungeon_profile(self.base,task,self.plan['tasks'])
            old=next(r for r in config['states']['Progress']['rules'] if r['template']=='完成地下城.png')
            new=next(r for r in config['states']['Progress']['rules'] if r['template']=='完成冒险传送门核心.png')
            self.assertEqual(old['threshold'],.95)
            self.assertEqual(new['threshold'],.95)
            self.assertEqual(new['requires_match']['threshold'],.95)
            for key in ('next','success_match','verify_change','event','confirm_frames'):
                self.assertEqual(new[key],old[key],key)
            self.assertEqual(new['event'],'none')
            self.assertEqual(new['next'],'ReturnEntry')
            add_resume_start(config)
            start=next(r for r in config['states']['ResumeStart']['rules'] if r['template']==new['template'])
            self.assertEqual(start['action'],'none')
            self.assertEqual(start['requires_match'],new['requires_match'])
            validate(config,ROOT/'assets/image',True)
        self.assertEqual(self.base,original)
    def test_resume_clicks_once_and_counts_only_after_return(self):
        runner,backend=self.replay()
        self.assertEqual(runner.reason,'达到完成轮数上限')
        self.assertEqual(runner.stats.rounds,1)
        self.assertEqual(len(backend.clicks),1)
        self.assertFalse(runner.guard_enabled)
    def test_stuck_portal_is_bounded_and_not_counted(self):
        runner,backend=self.replay(stuck=True)
        self.assertEqual(runner.stats.rounds,0)
        self.assertEqual(len(backend.clicks),2)
        self.assertIn('错误停止',runner.reason)
        backend.save_diagnostic.assert_called_once()
    def test_disappearance_without_return_page_cannot_count_win(self):
        runner,backend=self.replay(after=np.full((720,1395,3),30,np.uint8))
        self.assertEqual(runner.stats.rounds,0)
        self.assertEqual(len(backend.clicks),1)
        self.assertIn('错误停止',runner.reason)


@unittest.skipUnless((FIX/'stop.png').is_file(),'Private completion failure screenshots are not distributed')
class FinishPortalReplay(Frames,unittest.TestCase):
    def test_old_rule_misses_all_four_real_frames_new_rule_matches(self):
        old=next(r for r in self.config['states']['Progress']['rules'] if r['template']=='完成地下城.png')
        for name in ('stop.png','warning.png','warning-previous.png','stop-previous.png'):
            with self.subTest(frame=name):
                frame=self.read(FIX/name)
                self.assertIsNone(self.hit(frame,old))
                match=self.hit(frame)
                self.assertIsNotNone(match)
                self.assertGreaterEqual(match.score,.95)
    def test_real_failure_frame_can_resume_and_return_once(self):
        runner,backend=self.replay(first=self.read(FIX/'stop.png'))
        self.assertEqual(runner.stats.rounds,1,runner.reason)
        self.assertEqual(len(backend.clicks),1)
