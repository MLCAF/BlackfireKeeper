"""Actual standard-window abandon popup, with separate warning evidence."""
from pathlib import Path
import threading
import unittest
import numpy as np
from PIL import Image
from tests.test_unified_replay import Offline
from tests.test_defeat_recovery import profile
from tests.test_scene_variants import VariantRecognizer

ROOT=Path(__file__).resolve().parents[1]
SAVED=ROOT/'tests/fixtures/abandon_051/popup.png'


class AbandonVariantTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        controller=Offline();controller.post_connection().wait()
        cls.reco=VariantRecognizer(ROOT/'assets',controller)
        cls.rule=next(r for r in profile()['states']['DefeatReturn']['rules'] if r.get('defeat_action'))
    @classmethod
    def tearDownClass(cls):cls.reco.cancel().wait()
    def hit(self,frame):return self.reco.recognize(frame,self.rule,threading.Event())
    def frame(self,button=True,prompt=True):
        result=Image.new('RGB',(1395,720),(35,35,35))
        for name,origin,enabled in [('放弃冒险确认文字兼容.png',(741,414),button),
                                    ('放弃冒险提示正文兼容.png',(620,313),prompt)]:
            if enabled:
                with Image.open(ROOT/'assets/image'/name) as im:result.paste(im,origin)
        return np.asarray(result)[:,:,::-1].copy()
    def test_visible_popup_recognizes_confirm_without_lowering_threshold(self):
        hit=self.hit(self.frame())
        self.assertIsNotNone(hit)
        self.assertGreaterEqual(hit.score,.95)
        self.assertEqual(hit.box[:2],(741,414))
    def test_button_alone_warning_alone_and_dimmed_background_rejected(self):
        for frame in [self.frame(prompt=False),self.frame(button=False),(self.frame()*.45).astype(np.uint8)]:
            self.assertIsNone(self.hit(frame))
    def test_button_outside_modal_region_rejected(self):
        frame=self.frame(button=False)
        with Image.open(ROOT/'assets/image/放弃冒险确认文字兼容.png') as im:
            pixels=np.asarray(im.convert('RGB'))[:,:,::-1].copy()
        frame[500:500+pixels.shape[0],100:100+pixels.shape[1]]=pixels
        self.assertIsNone(self.hit(frame))
    @unittest.skipUnless(SAVED.exists(),'Private abandon diagnostic is not distributed')
    def test_saved_failure_matches_but_missing_warning_rejects(self):
        with Image.open(SAVED) as im:frame=np.asarray(im.convert('RGB'))[:,:,::-1].copy()
        self.assertIsNotNone(self.hit(frame))
        frame[300:380,615:890]=35
        self.assertIsNone(self.hit(frame))
