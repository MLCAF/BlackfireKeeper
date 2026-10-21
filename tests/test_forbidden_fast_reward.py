"""A fast battle can reach XP before auto-on is stable: recognize the 1920 modal."""
import unittest
import threading
import json
import hashlib
from pathlib import Path
import numpy as np
from PIL import Image
from vendor_core.backend import MaaRecognizer
from tests.test_unified_replay import Offline
from bk2.forbidden import ForbiddenFlow,ForbiddenOptions
from bk2.forbidden_vision import ForbiddenVision,RULES
ROOT=Path(__file__).resolve().parents[1]

class FastRewardTests(unittest.TestCase):
    def test_reward_template_manifest(self):
        for entry in json.loads((ROOT/'tests/forbidden_reward_manifest.json').read_text()):
            path=ROOT/'assets/image'/entry['template']
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),entry['sha256'])
            with Image.open(path) as template:self.assertEqual(template.size,tuple(entry['crop'][2:]))
    @classmethod
    def setUpClass(cls):
        cls.c=Offline();cls.c.post_connection().wait();cls.reco=MaaRecognizer(ROOT/'assets',cls.c)
        cls.stop=threading.Event();cls.vision=ForbiddenVision(cls.reco,cls.stop)
    @classmethod
    def tearDownClass(cls):cls.reco.cancel().wait()
    def test_fast_xp_successor_does_not_toggle_auto_again(self):
        f=ForbiddenFlow(ForbiddenOptions(1),0)
        self.assertEqual(f.step('explore',True,320,1),'explore')
        self.assertEqual(f.step('auto_off',True,317,2),'auto_off')
        self.assertEqual(f.step('xp',True,317,3),'xp')
        self.assertIsNone(f.step('xp',True,317,4))
        self.assertEqual(f.step('loot',True,317,5),'loot')
        f.step('explore',True,317,6);self.assertTrue(f.done)
    @unittest.skipUnless((ROOT/'evidence/resolution_20261021/xp_failure.png').exists(),'Private diagnostic is not distributed')
    def test_real_failed_frame_and_occluded_negative(self):
        frame=np.asarray(Image.open(ROOT/'evidence/resolution_20261021/xp_failure.png').convert('RGB'))[:,:,::-1].copy()
        kind,hit=self.vision.observe(frame)
        self.assertEqual(kind,'xp');self.assertIsNotNone(hit)
        self.assertIsNone(self.reco.recognize(frame,RULES['auto_on'],self.stop))
        kind,hit=self.vision.observe((frame*.45).astype(np.uint8));self.assertEqual(kind,'unknown');self.assertIsNone(hit)
