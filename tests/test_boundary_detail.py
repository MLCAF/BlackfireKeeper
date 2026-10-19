"""An interrupted dungeon selection must not strand reward-only scheduling."""
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import numpy as np
from PIL import Image
from bk2.recovery import boundary_profile, RecoveryRunner
from vendor_core.backend import MaaRecognizer
from vendor_core.config import validate
from tests.test_unified_replay import Offline

ROOT=Path(__file__).resolve().parents[1]
FIX=ROOT/'tests/fixtures/boundary_detail_20261019/detail.png'


class BoundaryDetailTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        c=Offline();c.post_connection().wait();cls.reco=MaaRecognizer(ROOT/'assets',c)
        cls.rule=next(r for r in boundary_profile()['states']['Boundary']['rules']
                      if r['template']=='副本详情关闭.png')
    @classmethod
    def tearDownClass(cls):cls.reco.cancel().wait()
    def frame(self,parts):
        f=np.full((720,1395,3),35,np.uint8)
        for name,x,y in parts:
            with Image.open(ROOT/'assets/image'/name) as image:
                pixels=np.asarray(image.convert('RGB'))[:,:,::-1].copy()
            h,w=pixels.shape[:2];f[y:y+h,x:x+w]=pixels
        return f
    def detail(self):return self.frame([('副本详情关闭.png',910,130),('副本详情标识.png',568,359)])
    def hit(self,f):return self.reco.recognize(f,self.rule,threading.Event())
    def replay(self,first=None,stuck=False):
        first=self.detail() if first is None else first
        after=self.frame([('地下城导航选中.png',552,580)])
        test=self
        class Backend:
            def __init__(self):self.page=0;self.clicks=[];self.save_diagnostic=Mock(return_value='offline.json')
            def guard(self):pass
            def cancel(self):pass
            def capture_is_fresh(self,*args):return True
            def capture(self,stop):return first if self.page==0 else after
            def recognize(self,*args):return test.reco.recognize(*args)
            def click(self,match,offset,stop,pause):
                if stop.is_set() or pause.is_set():raise InterruptedError()
                self.clicks.append(match.box)
                if not stuck:self.page=1
        backend=Backend();clock=[100.]
        def now():clock[0]+=.1;return clock[0]
        config=validate(boundary_profile(),ROOT/'assets/image',True)
        runner=RecoveryRunner(backend,config,Mock(),False,now)
        with patch.object(runner.stop_event,'wait',return_value=False):runner.run()
        return runner,backend
    def test_context_and_button_both_required(self):
        self.assertIsNotNone(self.hit(self.detail()))
        for parts in ([('副本详情关闭.png',910,130)],[('副本详情标识.png',568,359)]):
            self.assertIsNone(self.hit(self.frame(parts)))
    def test_dimmed_modal_is_not_clicked(self):
        self.assertIsNone(self.hit((self.detail()*.5).astype(np.uint8)))
    def test_unrelated_close_position_is_rejected(self):
        self.assertIsNone(self.hit(self.frame([('副本详情关闭.png',1100,250),('副本详情标识.png',568,359)])))
    def test_closes_once_returns_without_starting_or_counting_dungeon(self):
        runner,b=self.replay()
        self.assertTrue(runner.completed,runner.reason)
        self.assertEqual(len(b.clicks),1)
        self.assertEqual(runner.stats.rounds,0)
        self.assertFalse(runner.guard_enabled)
        self.assertEqual(self.rule['event'],'none')
        self.assertEqual(self.rule['success_match']['threshold'],.95)
    def test_stuck_close_stops_after_two_clicks_with_diagnostic(self):
        runner,b=self.replay(stuck=True)
        self.assertFalse(runner.completed)
        self.assertEqual(len(b.clicks),2)
        self.assertEqual(runner.stats.rounds,0)
        b.save_diagnostic.assert_called_once()
    @unittest.skipUnless(FIX.is_file(),'Private interrupted-selection frame is not distributed')
    def test_actual_failure_frame_closes_and_confirms_list(self):
        with Image.open(FIX) as image:frame=np.asarray(image.convert('RGB'))[:,:,::-1].copy()
        self.assertIsNotNone(self.hit(frame))
        runner,b=self.replay(frame)
        self.assertTrue(runner.completed,runner.reason)
        self.assertEqual(len(b.clicks),1)
        self.assertEqual(runner.stats.rounds,0)
