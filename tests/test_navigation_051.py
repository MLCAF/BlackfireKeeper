"""Regress the highlighted-home and wrong-tab diagnostic frames without game IO."""
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import numpy as np
from PIL import Image
from bk2.recovery import boundary_profile, RecoveryRunner, HOME
from bk2.list_guard import LIST_RULE
from tests.test_unified_replay import Offline
from tests.test_scene_variants import VariantRecognizer

ROOT=Path(__file__).resolve().parents[1]
FIX=ROOT/'tests/fixtures/navigation_051'


class Navigation051Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        c=Offline();c.post_connection().wait();cls.reco=VariantRecognizer(ROOT/'assets',c)
        cls.nav=next(r for r in boundary_profile()['states']['Boundary']['rules'] if r['template']=='地下城入口主体.png')
    @classmethod
    def tearDownClass(cls):cls.reco.cancel().wait()
    def read(self,path):
        with Image.open(path) as image:return np.asarray(image.convert('RGB'))[:,:,::-1].copy()
    def frame(self,parts):
        frame=np.full((720,1395,3),35,np.uint8)
        for name,x,y in parts:
            image=self.read(ROOT/'assets/image'/name);h,w=image.shape[:2];frame[y:y+h,x:x+w]=image
        return frame
    def hit(self,frame,rule):return self.reco.recognize(frame,rule,threading.Event())
    def replay(self,first,stuck=False):
        after=self.frame([('地下城导航选中.png',535,590)]);test=self
        class Backend:
            def __init__(self):self.page=0;self.clicks=[];self.save_diagnostic=Mock()
            def guard(self):pass
            def cancel(self):pass
            def capture_is_fresh(self,*a):return True
            def capture(self,stop):return first if self.page==0 else after
            def recognize(self,*a):return test.reco.recognize(*a)
            def click(self,match,offset,stop,pause):
                self.clicks.append(match.box)
                if not stuck:self.page=1
        backend=Backend();clock=[0.]
        def now():clock[0]+=.1;return clock[0]
        runner=RecoveryRunner(backend,boundary_profile(),Mock(),False,now)
        with patch.object(runner.stop_event,'wait',return_value=False):runner.run()
        return runner,backend
    def test_highlight_core_accepts_only_its_region_and_visible_brightness(self):
        rule=dict(HOME,name='home')
        frame=self.frame([('主界面高亮建筑核心.png',335,210)])
        self.assertIsNotNone(self.hit(frame,rule))
        self.assertIsNone(self.hit((frame*.4).astype(np.uint8),rule))
        self.assertIsNone(self.hit(self.frame([('主界面高亮建筑核心.png',800,300)]),rule))
    def test_wrong_tab_navigation_requires_both_icons(self):
        parts=[('地下城入口主体.png',552,599),('关卡导航选中.png',430,590)]
        self.assertIsNotNone(self.hit(self.frame(parts),self.nav))
        for partial in ([parts[0]],[parts[1]]):self.assertIsNone(self.hit(self.frame(partial),self.nav))
        self.assertIsNone(self.hit((self.frame(parts)*.4).astype(np.uint8),self.nav))
    def test_lost_navigation_does_not_certify_list_or_repeat_forever(self):
        frame=self.frame([('地下城入口主体.png',552,599),('关卡导航选中.png',430,590)])
        runner,backend=self.replay(frame,stuck=True)
        self.assertFalse(runner.completed)
        self.assertEqual(len(backend.clicks),2)
        self.assertEqual(runner.stats.rounds,0)
        backend.save_diagnostic.assert_called_once()
    @unittest.skipUnless(FIX.is_dir(),'Private navigation diagnostics are not distributed')
    def test_saved_home_and_levels_frames_have_correct_mutually_exclusive_evidence(self):
        paths=list(FIX.glob('*.png'));self.assertEqual(len(paths),8)
        for path in paths:
            frame=self.read(path);home=path.name.startswith('ReturnHome')
            self.assertEqual(bool(self.hit(frame,dict(HOME,name='home'))),home,path.name)
            self.assertEqual(bool(self.hit(frame,self.nav)),not home,path.name)
            self.assertIsNone(self.hit(frame,LIST_RULE),path.name)
    @unittest.skipUnless(FIX.is_dir(),'Private navigation diagnostics are not distributed')
    def test_actual_levels_page_switches_once_to_dungeons_without_run_or_claim(self):
        frame=self.read(next(FIX.glob('Boundary-*.png')))
        runner,backend=self.replay(frame)
        self.assertTrue(runner.completed,runner.reason)
        self.assertEqual(len(backend.clicks),1)
        self.assertEqual(runner.stats.rounds,0)
        self.assertEqual(runner.counters,{})
