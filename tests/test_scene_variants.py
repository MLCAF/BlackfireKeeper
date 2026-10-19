"""Portable scene evidence: real Maa matching, no game capture or input."""
import copy
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image
from bk2.scene_variants import SceneVariantMixin, SCENE_VARIANTS
from bk2.diamond_profile import build_diamond_profile
from bk2.food_profile import build_food_profile
from bk2.recovery import RecoveryRunner, HOME
from vendor_core.backend import MaaRecognizer
from tests.test_unified_replay import Offline

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT/'tests/fixtures/external_portable_20261019'


class VariantRecognizer(SceneVariantMixin, MaaRecognizer):
    pass


class SceneVariantTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        controller = Offline(); controller.post_connection().wait()
        cls.reco = VariantRecognizer(ROOT/'assets', controller)

    @classmethod
    def tearDownClass(cls): cls.reco.cancel().wait()

    def hit(self, frame, template, **kw):
        return self.reco.recognize(frame, dict(name='test', template=template, threshold=.95, **kw), threading.Event())

    def read(self, path):
        with Image.open(path) as image: return np.asarray(image.convert('RGB'))[:, :, ::-1].copy()

    def frame(self, template, x, y):
        frame = np.full((720, 1382, 3), 35, np.uint8)
        image = self.read(ROOT/'assets/image'/template); h,w = image.shape[:2]
        frame[y:y+h, x:x+w] = image
        return frame

    def test_variants_reject_dimmed_wrong_region_and_preserve_requested_roi(self):
        for name,(template,roi) in SCENE_VARIANTS.items():
            x,y=roi[:2]; frame=self.frame(template,x,y)
            original=dict(name='probe',template=name,threshold=.95); frozen=copy.deepcopy(original)
            self.assertIsNotNone(self.reco.recognize(frame,original,threading.Event()),name)
            self.assertEqual(original,frozen)
            self.assertIsNone(self.hit((frame*.4).astype(np.uint8),name,min_brightness_ratio=.85),name)
            self.assertIsNone(self.hit(self.frame(template,1100,300),name),name)
            self.assertIsNone(self.hit(frame,name,roi=[900,100,400,450]),name)

    def test_original_templates_still_match_without_fallback(self):
        for name in SCENE_VARIANTS:
            frame=self.frame(name,700,300)
            self.assertIsNotNone(self.hit(frame,name))
            self.assertEqual(self.reco.recognition_trace[-1]['template'],name)

    def test_bakery_keeps_required_home_context(self):
        rule=next(r for r in build_food_profile()['states']['FoodEntry']['rules'] if r['template']=='主城面包工坊.png')
        frame=self.frame('主城面包工坊兼容.png',166,406)
        self.assertIsNone(self.reco.recognize(frame,rule,threading.Event()))
        home=self.read(ROOT/'assets/image/主界面锚点兼容.png')
        frame[194:324,312:451]=home
        self.assertIsNotNone(self.reco.recognize(frame,rule,threading.Event()))

    def test_near_revalidation_and_stop_still_apply(self):
        frame=self.frame('城镇兼容.png',13,583)
        rule=dict(name='town',template='城镇.png',threshold=.95)
        hit=self.reco.recognize(frame,rule,threading.Event())
        self.assertIsNotNone(self.reco.recognize_near(frame,rule,hit,threading.Event()))
        stop=threading.Event();stop.set()
        with self.assertRaises(InterruptedError):self.reco.recognize(frame,rule,stop)

    @unittest.skipUnless(FIX.is_dir(),'Private portable diagnostics are not distributed')
    def test_all_fourteen_report_frames_and_cross_page_negatives(self):
        paths=list(FIX.rglob('*.png'));self.assertEqual(len(paths),14)
        for path in paths:
            frame=self.read(path);is_list='125524' in path.name
            self.assertEqual(bool(self.hit(frame,'城镇.png')),is_list,path.name)
            self.assertEqual(bool(self.hit(frame,'主界面锚点.png')),not is_list,path.name)
            self.assertEqual(bool(self.hit(frame,'主城面包工坊.png')),not is_list,path.name)
            self.assertEqual(bool(self.hit(frame,'传送门.png')),not is_list,path.name)

    @unittest.skipUnless(FIX.is_dir(),'Private portable diagnostics are not distributed')
    def test_actual_list_returns_home_once_and_does_not_count_reward(self):
        first=self.read(FIX/'20261019-125524-592528.png')
        home=self.read(FIX/'20261019-130015-322135.png');test=self
        class Backend:
            def __init__(self):self.page=0;self.clicks=[];self.save_diagnostic=Mock()
            def guard(self):pass
            def cancel(self):pass
            def capture_is_fresh(self,*args):return True
            def capture(self,stop):return first if self.page==0 else home
            def recognize(self,*args):return test.reco.recognize(*args)
            def click(self,match,offset,stop,pause):self.clicks.append(match.box);self.page=1
        backend=Backend();clock=[100.]
        def now():clock[0]+=.1;return clock[0]
        config=build_diamond_profile()
        # Stop at the positively observed destination, not at a fabricated shop.
        config['states']['OpenDiamond']['rules']=[dict(HOME,name='verified home',action='none',event='task_complete',confirm_frames=2)]
        runner=RecoveryRunner(backend,config,Mock(),False,now)
        with patch.object(runner.stop_event,'wait',return_value=False):runner.run()
        self.assertTrue(runner.completed,runner.reason)
        self.assertEqual(len(backend.clicks),1)
        self.assertEqual(runner.counters.get('diamond_rewards',0),0)
        self.assertEqual(runner.stats.rounds,0)
