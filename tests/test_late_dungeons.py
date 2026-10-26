"""Ten-dungeon migration and screenshot replay; no real game input."""
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
from bk2.plan import with_bundled_entrances, validate_dungeon_plan
from bk2.cooldowns import summarize
from bk2.scheduler import MemoryScheduler, MemoryCooldown
from bk2.profiles import build_dungeon_profile
from bk2.list_guard import LIST_RULE, ListCooldownScanner
from vendor_core.config import validate
from vendor_core.backend import MaaRecognizer
from vendor_core.list_cooldown import timer_roi
from tests.test_unified_replay import Offline

ROOT = Path(__file__).resolve().parents[1]
FRAME = ROOT/'tests/fixtures/late_dungeons/user_list.png'


class LatePlanTests(unittest.TestCase):
    def test_old_plan_adds_unselected_rows_without_rewriting_settings(self):
        path = ROOT/'config/dungeon_plan.json'
        if not path.exists():
            path = ROOT/'config/dungeon_plan.example.json'
        raw = path.read_bytes()
        old = json.loads(raw.decode('utf-8-sig'))
        old['tasks'] = old['tasks'][:7]
        old['tasks'][6].update(entrance_template='七本名称.png', entrance_offset=[0,-150])
        before = copy.deepcopy(old)
        updated = with_bundled_entrances(old)
        self.assertEqual(old, before)
        self.assertEqual(updated['tasks'][:7], old['tasks'])
        self.assertEqual(len(updated['tasks']), 10)
        self.assertTrue(all(not t['enabled'] for t in updated['tasks'][7:]))
        self.assertEqual([t['cooldown_seconds'] for t in updated['tasks'][7:]], [2400,2700,3000])
        self.assertEqual(with_bundled_entrances(updated), updated)
        self.assertEqual(path.read_bytes(), raw)

    def test_saved_late_choices_and_custom_templates_survive_reload(self):
        plan = load_plan()
        plan['tasks'][8].update(enabled=True, buff='damage', cooldown_seconds=4567,
                               entrance_template='my-tomb.png', entrance_offset=[5,-130])
        self.assertEqual(with_bundled_entrances(plan), plan)

    def test_all_ten_profiles_validate_and_map_correct_memory_ids(self):
        plan = load_plan()
        base = json.loads((ROOT/'config/profiles/dungeon_unlimited.json').read_text(encoding='utf-8-sig'))
        for task in plan['tasks']:
            task['enabled'] = True
            profile = build_dungeon_profile(base, task, plan['tasks'])
            validate(profile, ROOT/'assets/image', True)
            self.assertEqual(len(profile['navigation_names']), 10)
            if task['order'] >= 8:
                rule = next(r for r in profile['states']['SelectDungeon']['rules']
                            if r['template'] == task['entrance_template'])
                self.assertEqual(rule['offset'], [0,-150])
                self.assertEqual(rule['requires_match']['threshold'], .95)
                self.assertEqual(profile['expected_dungeon_id'],
                                 {8:'dragon_lair',9:'ancient_tomb',10:'abyss_hell'}[task['order']])
        validate_dungeon_plan(plan, ROOT/'config/profiles', ROOT/'assets/image', True)

    def test_validator_still_rejects_duplicate_fractional_and_out_of_range_orders(self):
        for order in (0, 11, 9.5, 9, True):
            plan = load_plan()
            plan['tasks'][-1]['order'] = order
            with self.subTest(order=order), self.assertRaises(ValueError):
                validate_dungeon_plan(plan, ROOT/'config/profiles', ROOT/'assets/image', True)

    def test_memory_cooldown_prevents_tenth_selection_until_actual_deadline(self):
        now = 1793090000.
        fields = dict(dungeons={'abyss_hell':dict(refresh_time=(now+739)*1000, has_unlocked=True)})
        state = summarize(fields, now)['dungeons']['dungeon_10']
        self.assertEqual(state['status'], 'cooling')
        self.assertEqual(state['remaining_seconds'], 739)
        plan = load_plan()
        for task in plan['tasks']:
            task['enabled'] = task['order'] == 10
        scheduler = MemoryScheduler(plan, Mock(), Mock(), threading.Event(), Mock(), clock=lambda:now)
        scheduler.apply_list_readings({'dungeon_10': MemoryCooldown(state['deadline_unix'])})
        self.assertIsNone(scheduler.choose())
        self.assertEqual(scheduler.ready_at['dungeon_10'], now+742)
        scheduler.apply_list_readings({})
        self.assertIsNone(scheduler.choose())

    def test_templates_match_recorded_crops(self):
        entries = json.loads((ROOT/'tests/late_dungeon_templates.json').read_text(encoding='utf8'))
        for entry in entries:
            path = ROOT/'assets/image'/entry['template']
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), entry['sha256'])
            with Image.open(path) as image:
                self.assertEqual(image.size, tuple(entry['crop'][2:]))


@unittest.skipUnless(FRAME.exists(), 'Private user screenshot not distributed')
class LateReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        controller = Offline()
        controller.post_connection().wait()
        cls.reco = MaaRecognizer(ROOT/'assets', controller)
        cls.stop = threading.Event()
        cls.frame = np.asarray(Image.open(FRAME).convert('RGB'))[:,:,::-1].copy()
        cls.plan = load_plan()
        base = json.loads((ROOT/'config/profiles/dungeon_unlimited.json').read_text(encoding='utf-8-sig'))
        cls.rules = []
        for task in cls.plan['tasks'][7:]:
            profile = build_dungeon_profile(base, task, cls.plan['tasks'])
            cls.rules.append(next(r for r in profile['states']['SelectDungeon']['rules']
                                 if r['template'] == task['entrance_template']))

    @classmethod
    def tearDownClass(cls):
        cls.reco.cancel().wait()

    def test_actual_three_entrances_and_door_coordinates(self):
        for rule, left in zip(self.rules, (300,674,1047)):
            hit = self.reco.recognize(self.frame, rule, self.stop)
            self.assertIsNotNone(hit)
            self.assertGreaterEqual(hit.score, .95)
            self.assertEqual(hit.box[:2], (left,445))
            x,y,w,h = hit.box
            self.assertTrue(left < x+w//2 < left+w)
            self.assertEqual(y+h//2+rule['offset'][1], 309)

    def test_independent_unoccluded_screenshot_matches_without_inventing_cooldown(self):
        with Image.open(FRAME.with_name('user_list_ready.png')) as source:
            frame = np.asarray(source.convert('RGB'))[:,:,::-1].copy()
        for rule in self.rules:
            self.assertIsNotNone(self.reco.recognize(frame, rule, self.stop))
        hit = self.reco.recognize(frame, self.rules[2], self.stop)
        self.assertIsNone(self.reco.read_timer_roi(frame, self.stop, 100., timer_roi(hit.box, frame.shape)))

    def test_tenth_clock_is_read_despite_building_occlusion(self):
        scanner = ListCooldownScanner(self.reco, self.plan['tasks'], self.stop,
                                      threading.Event(), Mock())
        anchors = scanner.anchors(self.frame)
        self.assertTrue({'dungeon_8','dungeon_9','dungeon_10'} <= anchors.keys())
        sample = scanner.observe(self.frame, 100., self.plan['tasks'][9], anchors['dungeon_10'])
        self.assertIsNotNone(sample)
        self.assertEqual(sample.text, '00:12:19')
        self.assertEqual(sample.seconds, 739)
        self.assertIsNone(scanner.observe(self.frame, 100., self.plan['tasks'][7], anchors['dungeon_8']))

    def test_other_labels_missing_selected_tab_and_dimmed_frames_are_rejected(self):
        for rule,left in zip(self.rules, (300,674,1047)):
            removed = self.frame.copy()
            removed[440:480, left:left+170] = 0
            self.assertIsNone(self.reco.recognize(removed, rule, self.stop))
            dim = (self.frame*.5).astype(np.uint8)
            self.assertIsNone(self.reco.recognize(dim, rule, self.stop))
            no_tab = self.frame.copy()
            no_tab[560:720,400:750] = 0
            self.assertIsNone(self.reco.recognize(no_tab, rule, self.stop))


if __name__ == '__main__':
    unittest.main()
