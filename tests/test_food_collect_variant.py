"""1600px bakery regression: real failure frames, modal and paid-button negatives."""
import threading
import unittest
from pathlib import Path
import numpy as np
from PIL import Image
from bk2.scene_variants import SceneVariantMixin
from bk2.food_profile import build_food_profile
from vendor_core.backend import MaaRecognizer
from tests.test_unified_replay import Offline
ROOT=Path(__file__).resolve().parents[1]
FIX=ROOT/'evidence/food_collect_replay_20261021/food_collect_20261021'
class Recognizer(SceneVariantMixin,MaaRecognizer):pass

class BakeryTemplateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.controller=Offline();cls.controller.post_connection().wait()
        cls.reco=Recognizer(ROOT/'assets',cls.controller)
        cls.rule=build_food_profile()['states']['FoodCollect']['rules'][0]
        cls.stop=threading.Event()
    @classmethod
    def tearDownClass(cls):cls.reco.cancel().wait()
    def test_new_label_without_bakery_title_does_not_authorize_click(self):
        frame=np.full((720,1395,3),50,np.uint8)
        crop=np.asarray(Image.open(ROOT/'assets/image/体力收集文字1600.png').convert('RGB'))[:,:,::-1]
        frame[472:500,754:841]=crop
        self.assertIsNone(self.reco.recognize(frame,self.rule,self.stop))
    @unittest.skipUnless((FIX/'timeout.png').exists(),'Private failure frames are not distributed')
    def test_failure_frames_and_dimmed_or_missing_label(self):
        for name in ['timeout','warning']:
            with self.subTest(frame=name):
                frame=np.asarray(Image.open(FIX/(name+'.png')).convert('RGB'))[:,:,::-1].copy()
                hit=self.reco.recognize(frame,self.rule,self.stop)
                self.assertIsNotNone(hit);self.assertGreaterEqual(hit.score,.95)
                self.assertGreater(hit.box[0],700) # Never the paid left-hand button.
                self.assertIsNone(self.reco.recognize((frame*.45).astype(np.uint8),self.rule,self.stop))
                frame[460:515,685:912]=40
                self.assertIsNone(self.reco.recognize(frame,self.rule,self.stop))
