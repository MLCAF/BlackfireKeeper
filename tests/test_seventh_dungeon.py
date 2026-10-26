"""Seventh entrance integration; offline controller rejects all real game input."""
import copy
import json
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock

import numpy as np
from PIL import Image

from bk2.automation import load_plan
from bk2.plan import with_bundled_entrances
from bk2.profiles import build_dungeon_profile
from bk2.list_guard import LIST_RULE, ListCooldownScanner, list_navigation_profile
from vendor_core.backend import MaaRecognizer
from vendor_core.config import validate
from tests.test_unified_replay import Offline

ROOT = Path(__file__).resolve().parents[1]


class SeventhPlanTests(unittest.TestCase):
    def test_old_placeholder_adds_only_entrance_metadata_without_mutating_input(self):
        plan = load_plan()
        task = plan['tasks'][6]
        task.update(entrance_template=None, display_name='七本（待补入口图）',
                    enabled=False, buff='damage', cooldown_seconds=2222)
        task.pop('entrance_offset', None)
        before = copy.deepcopy(plan)
        result = with_bundled_entrances(plan)
        expected = copy.deepcopy(before)
        expected['tasks'][6].update(entrance_template='七本名称.png',
            display_name='七本（兽人地堡）', entrance_offset=[0, -150])
        self.assertEqual(result, expected)
        self.assertEqual(plan, before)
        self.assertEqual(with_bundled_entrances(result), result)

    def test_custom_entrance_and_saved_selection_are_preserved(self):
        plan = load_plan()
        plan['tasks'][6].update(entrance_template='custom.png', entrance_offset=[2, -90],
                               display_name='custom', enabled=True)
        self.assertEqual(with_bundled_entrances(plan), plan)

    def test_reading_local_plan_does_not_write_settings(self):
        path = ROOT/'config/dungeon_plan.json'
        if not path.exists():
            self.skipTest('No local user plan in clean checkout')
        before = path.read_bytes()
        raw = json.loads(before.decode('utf-8-sig'))
        loaded = load_plan(path)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(loaded['tasks'][:6], raw['tasks'][:6])
        for field in ('enabled', 'buff', 'cooldown_seconds'):
            self.assertEqual(loaded['tasks'][6].get(field), raw['tasks'][6].get(field))

    def test_seventh_profile_has_guard_offset_memory_id_and_valid_resources(self):
        plan = load_plan()
        task = plan['tasks'][6]
        base = json.loads((ROOT/'config/profiles/dungeon_unlimited.json').read_text(encoding='utf-8-sig'))
        for builder in (build_dungeon_profile, list_navigation_profile):
            profile = builder(base, task, plan['tasks'])
            validate(profile, ROOT/'assets/image', True)
            if builder is list_navigation_profile:
                self.assertEqual(profile['states']['ListReady']['rules'][0]['template'],
                                 LIST_RULE['template'])
                continue
            rule = next(r for r in profile['states']['SelectDungeon']['rules']
                        if r['template'] == '七本名称.png')
            self.assertEqual(rule['offset'], [0, -150])
            self.assertEqual(rule['requires_match']['template'], LIST_RULE['template'])
            self.assertEqual(rule['requires_match']['threshold'], .95)
            self.assertEqual(rule['success_match']['template'], '副本详情标识.png')
        self.assertEqual(build_dungeon_profile(base, task, plan['tasks'])['expected_dungeon_id'],
                         'orc_bunker')

    def test_seventh_participates_in_ocr_scan_but_absence_does_not_mean_ready(self):
        plan = load_plan()
        task = plan['tasks'][6]
        task['enabled'] = True
        backend = Mock()
        backend.read_timer_roi.return_value = None
        scanner = ListCooldownScanner(backend, plan['tasks'], threading.Event(),
                                      threading.Event(), Mock())
        self.assertIn(task, scanner.tasks)
        self.assertIn(task, scanner.selected)
        self.assertIsNone(scanner.observe(np.zeros((720, 1395, 3), dtype=np.uint8),
                                          123, task, (600, 470, 167, 29)))
        backend.recognize.assert_not_called()


class SeventhRecognitionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        controller = Offline()
        controller.post_connection().wait()
        cls.reco = MaaRecognizer(ROOT/'assets', controller)
        plan = load_plan()
        base = json.loads((ROOT/'config/profiles/dungeon_unlimited.json').read_text(encoding='utf-8-sig'))
        profile = build_dungeon_profile(base, plan['tasks'][6], plan['tasks'])
        cls.rule = next(r for r in profile['states']['SelectDungeon']['rules']
                        if r['template'] == '七本名称.png')

    @classmethod
    def tearDownClass(cls):
        cls.reco.cancel().wait()

    def frame(self, label='七本名称.png', guard=True):
        frame = np.zeros((720, 1395, 3), dtype=np.uint8)
        placements = [(label, 600, 470)]
        if guard:
            placements.append((LIST_RULE['template'], 500, 570))
        for name, x, y in placements:
            with Image.open(ROOT/'assets/image'/name) as source:
                pixels = np.asarray(source.convert('RGB'))[:, :, ::-1]
            h, w = pixels.shape[:2]
            frame[y:y+h, x:x+w] = pixels
        return frame

    def test_guarded_name_anchor_matches_and_door_click_is_above_name(self):
        hit = self.reco.recognize(self.frame(), self.rule, threading.Event())
        self.assertIsNotNone(hit)
        self.assertGreaterEqual(hit.score, .95)
        self.assertEqual(hit.box, (600, 470, 167, 29))
        x, y, w, h = hit.box
        self.assertTrue(620 < x+w/2 < 740)
        self.assertTrue(300 < y+h/2+self.rule['offset'][1] < 380)

    def test_other_names_missing_navigation_and_dimmed_dialog_are_rejected(self):
        for name in ('一本', '二本', '三本', '四本', '五本', '六本'):
            with self.subTest(name=name):
                self.assertIsNone(self.reco.recognize(self.frame(name+'名称.png'), self.rule,
                                                     threading.Event()))
        self.assertIsNone(self.reco.recognize(self.frame(guard=False), self.rule, threading.Event()))
        self.assertIsNone(self.reco.recognize((self.frame()*.5).astype(np.uint8), self.rule,
                                             threading.Event()))


if __name__ == '__main__':
    unittest.main()
