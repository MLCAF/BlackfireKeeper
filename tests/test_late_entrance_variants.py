"""Replay late entrance failures without connecting to the game or sending input."""
import copy
import hashlib
import json
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock

import numpy as np
from PIL import Image

from bk2.automation import load_plan
from bk2.list_guard import LIST_RULE, ListCooldownScanner
from bk2.profiles import build_dungeon_profile
from bk2.recovery import RecoveryRunner
from bk2.scene_variants import SCENE_VARIANTS
from tests.test_scene_variants import VariantRecognizer
from tests.test_unified_replay import Offline
from vendor_core.backend import MaaRecognizer

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT/'tests/fixtures/late_entrance_20261027'


class LateEntranceVariants(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        controller = Offline(); controller.post_connection().wait()
        cls.reco = VariantRecognizer(ROOT/'assets', controller)
        cls.stop = threading.Event()
        cls.manifest = json.loads((ROOT/'tests/late_entrance_variants.json').read_text(encoding='utf8'))
        cls.plan = load_plan()
        base = json.loads((ROOT/'config/profiles/dungeon_unlimited.json').read_text(encoding='utf-8-sig'))
        cls.profiles = [build_dungeon_profile(base, t, cls.plan['tasks']) for t in cls.plan['tasks'][7:9]]
        cls.rules = [next(r for r in p['states']['SelectDungeon']['rules'] if r['name']=='选择目标副本') for p in cls.profiles]

    @classmethod
    def tearDownClass(cls): cls.reco.cancel().wait()

    @staticmethod
    def read(path):
        with Image.open(path) as frame:
            return np.asarray(frame.convert('RGB'))[:, :, ::-1].copy()

    def canvas(self, entry, guard=True):
        frame = np.full((720,1395,3),30,np.uint8)
        tile = self.read(ROOT/'assets/image'/entry['template'])
        x,y,w,h = entry['crop']; frame[y:y+h,x:x+w] = tile
        if guard:
            nav = self.read(ROOT/'assets/image'/LIST_RULE['template'])
            nh,nw = nav.shape[:2]; frame[570:570+nh,500:500+nw] = nav
        return frame

    def test_crop_hashes_sizes_and_unchanged_thresholds(self):
        for entry,rule in zip(self.manifest,self.rules):
            asset = ROOT/'assets/image'/entry['template']
            self.assertEqual(hashlib.sha256(asset.read_bytes()).hexdigest(),entry['sha256'])
            self.assertEqual(list(self.read(asset).shape[1::-1]),entry['crop'][2:])
            self.assertEqual(SCENE_VARIANTS[entry['original']][0],entry['template'])
            self.assertEqual(rule['threshold'],.95)
            self.assertEqual(rule['requires_match']['threshold'],.95)

    def test_cross_dungeon_missing_navigation_and_dimmed_panels_are_rejected(self):
        for index,entry in enumerate(self.manifest):
            frame = self.canvas(entry)
            rule = self.rules[index]; frozen = copy.deepcopy(rule)
            self.assertIsNotNone(self.reco.recognize(frame,rule,self.stop))
            self.assertIsNone(self.reco.recognize(frame,self.rules[1-index],self.stop))
            self.assertIsNone(self.reco.recognize(self.canvas(entry,False),rule,self.stop))
            self.assertIsNone(self.reco.recognize((frame*.4).astype(np.uint8),rule,self.stop))
            self.assertEqual(rule,frozen)

    def test_known_late_name_prevents_reset_on_idle_list(self):
        for entry,profile in zip(self.manifest,self.profiles):
            runner = RecoveryRunner(self.reco,profile,Mock(),False)
            runner.stats.state='SelectDungeon'; runner.stats.idle_seconds=6
            self.assertIsNone(runner.pending_success(self.canvas(entry)))
            self.assertEqual(runner.list_resets,0)

    def test_visible_name_does_not_invent_ready_cooldown(self):
        scanner = ListCooldownScanner(self.reco,self.plan['tasks'],self.stop,threading.Event(),Mock())
        for index,entry in enumerate(self.manifest):
            frame=self.canvas(entry); anchors=scanner.anchors(frame)
            task=self.plan['tasks'][index+7]
            self.assertIn(task['id'],anchors)
            self.assertIsNone(scanner.observe(frame,100.,task,anchors[task['id']]))

    @unittest.skipUnless(all((FIX/f'frame_{i}.png').is_file() for i in (1,2)), 'Private portable screenshots are not distributed')
    def test_actual_frames_reproduce_old_miss_and_fix_both_names(self):
        for i in (1,2):
            frame=self.read(FIX/f'frame_{i}.png')
            self.assertEqual(frame.shape[:2],(720,1395))
            for entry,rule,profile in zip(self.manifest,self.rules,self.profiles):
                self.assertIsNone(MaaRecognizer.recognize(self.reco,frame,rule,self.stop))
                hit=self.reco.recognize(frame,rule,self.stop)
                self.assertIsNotNone(hit); self.assertGreaterEqual(hit.score,.95)
                self.assertIsNotNone(self.reco.recognize_near(frame,rule,hit,self.stop))
                x,y,w,h=hit.box
                expected_x=entry['crop'][0]+84
                self.assertAlmostEqual(x+w/2+rule['offset'][0],expected_x,delta=2)
                self.assertAlmostEqual(y+h/2+rule['offset'][1],324.5,delta=2)
                runner=RecoveryRunner(self.reco,profile,Mock(),False)
                runner.stats.state='SelectDungeon'; runner.stats.idle_seconds=6
                self.assertIsNone(runner.pending_success(frame))
                removed=frame.copy(); left=entry['crop'][0]; removed[450:500,left-3:left+175]=30
                self.assertIsNone(self.reco.recognize(removed,rule,self.stop))


if __name__=='__main__': unittest.main()
