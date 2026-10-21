"""Reduced-client assets retain thresholds, context checks and navigation state."""
import copy
import hashlib
import json
import threading
import unittest
import numpy as np
from PIL import Image
from pathlib import Path
from tests.test_scene_variants import VariantRecognizer
from tests.test_unified_replay import Offline
from bk2.scene_variants import SMALL_WINDOW_VARIANTS, VARIANT_GROUPS

ROOT=Path(__file__).resolve().parents[1]


class SmallWindowVariantTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        c=Offline();c.post_connection().wait();cls.reco=VariantRecognizer(ROOT/'assets',c)
    @classmethod
    def tearDownClass(cls):cls.reco.cancel().wait()
    def canvas(self,template,x,y):
        image=np.full((720,1395,3),30,np.uint8)
        tile=np.asarray(Image.open(ROOT/'assets/image'/template).convert('RGB'))[:,:,::-1].copy()
        h,w=tile.shape[:2];image[y:y+h,x:x+w]=tile
        return image
    def test_real_crops_preserve_threshold_roi_and_mask_rejection(self):
        for original,(variant,roi) in (item for group in VARIANT_GROUPS[1:] for item in group.items()):
            with self.subTest(template=original):
                x,y=roi[:2];frame=self.canvas(variant,x,y)
                rule=dict(name='small window',template=original,threshold=.96)
                frozen=copy.deepcopy(rule);stop=threading.Event()
                self.assertIsNotNone(self.reco.recognize(frame,rule,stop))
                self.assertEqual(rule,frozen)
                self.assertIsNone(self.reco.recognize((frame*.4).astype(np.uint8),rule,stop))
                opposite=[0,560,1395,160] if y<360 else [0,0,1395,160]
                self.assertIsNone(self.reco.recognize(frame,dict(rule,roi=opposite),stop))
                self.assertIsNone(self.reco.recognize(frame,dict(rule,requires_match={
                    'template':'任务完成标题.png','threshold':.95}),stop))
    def test_dungeon_names_are_not_interchangeable(self):
        names=[n for n in SMALL_WINDOW_VARIANTS if n.endswith('本名称.png')]
        for name in names:
            frame=self.canvas(SMALL_WINDOW_VARIANTS[name][0],200,460)
            for other in names:
                hit=self.reco.recognize(frame,dict(name='name identity',template=other,threshold=.95),threading.Event())
                self.assertEqual(bool(hit),name==other,(name,other))
    def test_crop_manifest_matches_shipped_assets(self):
        manifest=json.loads((ROOT/'tests/adaptive_template_manifest.json').read_text(encoding='utf-8'))
        expected={variant for group in VARIANT_GROUPS[1:] for variant,_ in group.values()}
        self.assertEqual({r['template'] for r in manifest},expected)
        for entry in manifest:
            p=ROOT/'assets/image'/entry['template']
            self.assertEqual(hashlib.sha256(p.read_bytes()).hexdigest(),entry['sha256'])
            with Image.open(p) as tile:self.assertEqual(list(tile.size),entry['crop'][2:])
