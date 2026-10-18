"""Scroll-independent list evidence; actual failures stay in local fixtures."""
import copy
import json
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image

from bk2.automation import load_plan
from bk2.list_guard import LIST_RULE, ListCooldownScanner, list_navigation_profile
from bk2.profiles import build_dungeon_profile
from vendor_core.backend import MaaRecognizer
from vendor_core.runner import Runner
from vendor_core.config import validate
from tests.test_unified_replay import Offline
from tests.test_defeat_recovery import Clock

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    with Image.open(ROOT/path) as image:
        return np.asarray(image.convert('RGB'))[:, :, ::-1].copy()


class ListGuardTests(unittest.TestCase):
    def setUp(self):
        self.plan = load_plan()
        self.base = json.loads((ROOT/'config/profiles/dungeon_unlimited.json').read_text(encoding='utf8'))

    def test_all_six_profiles_replace_every_list_guard_without_mutating_base(self):
        before = copy.deepcopy(self.base)
        for task in self.plan['tasks'][:6]:
            profile = build_dungeon_profile(self.base, task, self.plan['tasks'])
            validate(profile, ROOT/'assets/image', True)
            self.assertNotIn('地下城列表通用标识.png', json.dumps(profile, ensure_ascii=False))
            select = next(r for r in profile['states']['SelectDungeon']['rules']
                          if r['template'] == task['entrance_template'])
            self.assertEqual(select['requires_match']['template'], LIST_RULE['template'])
            self.assertEqual(select['offset'], [0, -150])
            self.assertEqual(select['confirm_frames'], 2)
            self.assertEqual(select['success_match']['template'], '副本详情标识.png')
            exit_rule = profile['states']['DefeatReturn']['rules'][0]
            self.assertEqual(exit_rule['template'], LIST_RULE['template'])
            self.assertEqual(exit_rule['event'], 'task_complete')
        self.assertEqual(self.base, before)

    def test_ocr_navigation_uses_same_guard_and_preserves_single_task_completion(self):
        profile = list_navigation_profile(self.base, self.plan['tasks'][5], self.plan['tasks'])
        validate(profile, ROOT/'assets/image', True)
        self.assertNotIn('地下城列表通用标识.png', json.dumps(profile, ensure_ascii=False))
        self.assertEqual(profile['states']['ListReady']['rules'][0]['template'], LIST_RULE['template'])

    def test_scanner_requires_guard_and_honors_stop_and_pause(self):
        backend = Mock()
        stop, pause = threading.Event(), threading.Event()
        scanner = ListCooldownScanner(backend, self.plan['tasks'], stop, pause, Mock(), clock=lambda:123.)
        backend.recognize.return_value = None
        self.assertIsNone(scanner.capture())
        self.assertEqual(backend.recognize.call_args.args[1], LIST_RULE)
        backend.recognize.return_value = object()
        self.assertEqual(scanner.capture(), (backend.capture.return_value, 123.))
        for event in (pause, stop):
            backend.capture.reset_mock()
            event.set()
            self.assertIsNone(scanner.capture())
            backend.capture.assert_not_called()
            event.clear()


@unittest.skipUnless((ROOT/'tests/fixtures/list_guard_20261018').is_dir(),
                     'Local diagnostic screenshots are not distributed')
class ListGuardReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        controller = Offline()
        controller.post_connection().wait()
        cls.reco = MaaRecognizer(ROOT/'assets', controller)
        cls.frames = {name:read(f'tests/fixtures/list_guard_20261018/{name}.png')
                      for name in ('warning','timeout','battle')}
        plan = load_plan()
        base = json.loads((ROOT/'config/profiles/dungeon_unlimited.json').read_text(encoding='utf8'))
        cls.profile = build_dungeon_profile(base, plan['tasks'][5], plan['tasks'])
        cls.select = next(r for r in cls.profile['states']['SelectDungeon']['rules']
                          if r['template'] == '六本名称.png')

    @classmethod
    def tearDownClass(cls):
        cls.reco.cancel().wait()

    def hit(self, frame, rule):
        return self.reco.recognize(frame, rule, threading.Event())

    def test_reproduce_old_guard_miss_and_new_guard_accepts_both_failure_frames(self):
        old = dict(name='old',template='地下城列表通用标识.png',threshold=.95,min_contrast_ratio=.65)
        for name in ('warning','timeout'):
            frame = self.frames[name]
            self.assertIsNone(self.hit(frame, old))
            match = self.hit(frame, self.select)
            self.assertIsNotNone(match)
            self.assertGreaterEqual(match.score, .99)
            self.assertEqual(match.box, (579,463,160,28))

    def test_wrong_tab_battle_popups_and_dimmed_list_are_rejected(self):
        negatives = [self.frames['battle'], (self.frames['timeout']*.5).astype(np.uint8)]
        negatives += [read(f'tests/fixtures/failures_20261008/{name}.png')
                      for name in ('wrong_tab','other_dungeon','quest','expired_session')]
        for frame in negatives:
            self.assertIsNone(self.hit(frame, LIST_RULE))
            self.assertIsNone(self.hit(frame, self.select))
        no_nav = self.frames['timeout'].copy()
        no_nav[560:720,400:750] = 0
        self.assertIsNone(self.hit(no_nav, self.select))

    def test_original_first_three_list_and_sixth_exit_remain_recognizable(self):
        old_list = read('tests/fixtures/failures_20261003/dungeon_list.png')
        self.assertIsNotNone(self.hit(old_list, LIST_RULE))
        for frame in (old_list, self.frames['timeout']):
            self.assertIsNotNone(self.hit(frame, self.profile['states']['DefeatReturn']['rules'][0]))

    def test_select_sixth_clicks_once_after_confirmation_and_verifies_details(self):
        outer = self
        detail = np.zeros_like(self.frames['timeout'])
        pixels = read('assets/image/副本详情标识.png')
        h,w = pixels.shape[:2]
        detail[340:340+h,480:480+w] = pixels
        class Backend:
            clicked = False
            captures = 0
            clicks = []
            def guard(self): pass
            def cancel(self): pass
            def capture_is_fresh(self): return True
            def capture(self, stop):
                self.captures += 1
                return detail if self.clicked else outer.frames['timeout']
            def recognize(self, image, rule, stop): return outer.reco.recognize(image, rule, stop)
            def recognize_near(self, image, rule, match, stop): return self.recognize(image, rule, stop)
            def click(self, match, offset, stop, pause):
                self.clicks.append((match.box, offset, self.captures))
                self.clicked = True
        backend = Backend()
        config = copy.deepcopy(self.profile)
        config.update(initial_state='SelectDungeon',max_rounds=0)
        config['states']['Explore'] = dict(timeout_seconds=10, rules=[
            dict(name='已打开详情', template='副本详情标识.png', threshold=.95,
                 action='none', event='task_complete', confirm_frames=2)])
        runner = Runner(backend, config, Mock(), False, clock=Clock())
        with patch.object(runner.stop_event,'wait',return_value=False):
            runner.run()
        self.assertTrue(runner.completed,runner.reason)
        self.assertEqual(len(backend.clicks),1)
        self.assertEqual(backend.clicks[0][:2], ((579,463,160,28),(0,-150)))
        self.assertGreaterEqual(backend.clicks[0][2],2)
        self.assertEqual(runner.stats.rounds,0)
