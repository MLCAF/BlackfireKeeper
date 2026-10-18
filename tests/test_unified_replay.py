"""Real Maa template matching on stored/synthetic frames; the controller forbids game IO."""
import json
import threading
import unittest
from pathlib import Path
import numpy as np
from PIL import Image
from maa.controller import CustomController
from vendor_core.backend import MaaRecognizer
from vendor_core.unified import build_dungeon_profile

ROOT = Path(__file__).resolve().parents[1]

class Offline(CustomController):
    def connect(self): return True
    def request_uuid(self): return 'unified-offline'
    def get_features(self): return 0
    def screencap(self): raise AssertionError('Real screenshot prohibited')
    def click(self, *a): raise AssertionError('Real click prohibited')
    def swipe(self, *a): raise AssertionError('Real swipe prohibited')

@unittest.skipUnless((ROOT/'tests/fixtures').is_dir(), 'Local replay screenshots are not distributed; see tests/README.md')
class UnifiedReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.controller = Offline()
        cls.controller.post_connection().wait()
        cls.reco = MaaRecognizer(ROOT / 'assets', cls.controller)
        cls.plan = json.loads((ROOT / 'config/dungeon_plan.json').read_text(encoding='utf-8'))
        cls.base = json.loads((ROOT / 'config/profiles/dungeon_unlimited.json').read_text(encoding='utf-8'))

    @classmethod
    def tearDownClass(cls): cls.reco.cancel().wait()

    def frame(self, path):
        with Image.open(ROOT / path) as source:
            return np.asarray(source.convert('RGB'))[:,:,::-1].copy()

    def hit(self, image, template, **extra):
        return self.reco.recognize(image, dict(name='test', template=template, threshold=.95, **extra), threading.Event())

    def test_all_six_labels_reject_other_dungeons(self):
        names = ['一本','二本','三本','四本','五本','六本']
        for i, name in enumerate(names):
            frame = self.frame(f'assets/image/{name}.png')
            for j, target in enumerate(names):
                hit = self.hit(frame, target+'名称.png')
                self.assertEqual(hit is not None, i == j, (name, target, hit))
            # Cover countdown/door artwork; labels must still match.
            frame[:220] = 0
            self.assertIsNotNone(self.hit(frame, name+'名称.png'))

    def test_first_three_names_match_saved_actual_list(self):
        frame = self.frame('tests/fixtures/failures_20261003/dungeon_list.png')
        for name in ['一本','二本','三本']:
            hit = self.hit(frame, name+'名称.png')
            self.assertIsNotNone(hit, name)
            print(f'UNIFIED_REPLAY {name}: {hit.score:.6f}, box={hit.box}')

    def test_list_evidence_rejects_wrong_tab_and_covered_list(self):
        template = '地下城列表通用标识.png'
        self.assertIsNotNone(self.hit(self.frame('tests/fixtures/failures_20261003/dungeon_list.png'), template))
        for name in ['wrong_tab', 'other_dungeon', 'quest', 'expired_session']:
            self.assertIsNone(self.hit(self.frame(f'tests/fixtures/failures_20261008/{name}.png'),
                template, min_contrast_ratio=.65), name)

    def test_common_entrance_progress_rejects_non_entrance(self):
        self.assertIsNotNone(self.hit(self.frame('tests/fixtures/failures_20261003/buff_hover.png'), '副本入口进度.png'))
        for path in ['failures_20261008/other_dungeon.png','failures_20261003/dungeon_list.png']:
            self.assertIsNone(self.hit(self.frame('tests/fixtures/'+path), '副本入口进度.png'))

    def test_home_anchor_matches_supplied_home_but_not_dungeon(self):
        self.assertIsNotNone(self.hit(self.frame('assets/image/主界面.png'), '主界面锚点.png'))
        self.assertIsNone(self.hit(self.frame('tests/fixtures/failures_20261003/dungeon_list.png'), '主界面锚点.png'))

if __name__ == '__main__': unittest.main()
