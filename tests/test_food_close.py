"""Lost bakery-close clicks must not become a completed collection or be spammed."""
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from bk2.food_profile import build_food_profile
from vendor_core.config import validate
from vendor_core.runner import Runner
from tests.test_food_response import PANEL, REWARD, HOME
from tests.upstream.test_runner import Clock, FakeBackend

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / 'tests/fixtures/food_close_20261019'


class FoodCloseTests(unittest.TestCase):
    def run_flow(self, lost=1, destination=HOME, stop=False, full=False, legacy=False):
        class Backend(FakeBackend):
            phase = 0 if full else 2
            closes = 0
            def capture(self, event):
                return REWARD if self.phase == 1 else destination if self.phase == 3 else PANEL
            def click(self, match, offset, event, pause):
                super().click(match, offset, event, pause)
                if self.phase < 2:
                    self.phase += 1
                else:
                    self.closes += 1
                    if stop:
                        event.set()
                    if self.closes > lost:
                        self.phase = 3
            def save_diagnostic(self, *args): return 'mock-diagnostic.json'
        backend = Backend([])
        profile = build_food_profile()
        profile.update(initial_state='FoodCollect' if full else 'FoodClose', poll_seconds=.001)
        profile.pop('watchdog')
        if legacy:
            close = profile['states']['FoodClose']['rules'][0]
            close['verify_change'] = False
            for field in ('retry_center_clicks','retry_max_clicks','retry_radius','retry_wait_seconds'):
                close.pop(field, None)
            profile['states']['FoodClose']['rules'] = [close]
        runner = Runner(backend, profile, Mock(), False, clock=Clock())
        with patch.object(runner.stop_event, 'wait', side_effect=lambda _: runner.stop_event.is_set()):
            runner.run()
        return runner, backend

    def test_lost_first_click_retries_once_then_completes(self):
        runner, backend = self.run_flow()
        self.assertTrue(runner.completed, runner.reason)
        self.assertEqual(backend.closes, 2)
        self.assertEqual(backend.click_offsets, [[0, 0], [0, 0]])

    def test_both_close_clicks_lost_stop_without_passive_done_state(self):
        runner, backend = self.run_flow(lost=99)
        self.assertFalse(runner.completed)
        self.assertEqual(backend.closes, 2)
        self.assertEqual(runner.stats.state, 'FoodClose')
        self.assertIn('连续 2 次点击', runner.reason)

    def test_normal_close_sends_one_click_for_both_destinations(self):
        for destination in (HOME, {'主界面锚点.png'}):
            with self.subTest(destination=destination):
                runner, backend = self.run_flow(lost=0, destination=destination)
                self.assertTrue(runner.completed, runner.reason)
                self.assertEqual(backend.closes, 1)

    def test_disappeared_button_without_positive_destination_does_not_complete(self):
        runner, backend = self.run_flow(lost=0, destination=set())
        self.assertFalse(runner.completed)
        self.assertIn('FoodDone 等待超时', runner.reason)
        self.assertEqual(backend.closes, 1)

    def test_stop_after_first_close_prevents_second_click(self):
        runner, backend = self.run_flow(stop=True)
        self.assertFalse(runner.completed)
        self.assertEqual(backend.closes, 1)

    def test_close_retry_does_not_repeat_collection_or_reward_confirmation(self):
        runner, backend = self.run_flow(full=True)
        self.assertTrue(runner.completed, runner.reason)
        self.assertEqual(backend.clicks, 4)
        self.assertEqual(runner.counters['food_collection_requests'], 1)
        self.assertEqual(runner.counters['food_collections'], 1)

    def test_legacy_flow_reproduces_food_done_timeout_after_one_lost_click(self):
        runner, backend = self.run_flow(legacy=True)
        self.assertFalse(runner.completed)
        self.assertIn('FoodDone 等待超时', runner.reason)
        self.assertEqual(backend.closes, 1)

    def test_already_closed_panel_does_not_click_old_close_position(self):
        for destination in (HOME, {'主界面锚点.png'}):
            backend = FakeBackend([destination])
            profile = build_food_profile()
            profile.update(initial_state='FoodClose', poll_seconds=.001)
            profile.pop('watchdog')
            runner = Runner(backend, profile, Mock(), False, clock=Clock())
            with patch.object(runner.stop_event, 'wait', return_value=False):
                runner.run()
            self.assertTrue(runner.completed, runner.reason)
            self.assertEqual(backend.clicks, 0)

    def test_profile_keeps_guards_and_bounded_center_retries(self):
        profile = build_food_profile()
        validate(profile, ROOT/'assets/image', True)
        close = profile['states']['FoodClose']['rules'][0]
        self.assertTrue(close['verify_change'])
        self.assertEqual(close['retry_max_clicks'], 2)
        self.assertEqual(close['retry_radius'], 0)
        self.assertEqual(close['threshold'], .95)
        self.assertEqual(close['requires_match']['template'], '体力工坊标题.png')
        self.assertEqual(close['requires_match']['threshold'], .95)
        self.assertNotIn('event', close)


@unittest.skipUnless((FIX/'panel.png').is_file(), 'Private failure screenshot is not distributed')
class FoodCloseReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.test_unified_replay import Offline
        from vendor_core.backend import MaaRecognizer
        cls.controller = Offline(); cls.controller.post_connection().wait()
        cls.reco = MaaRecognizer(ROOT/'assets', cls.controller)
    @classmethod
    def tearDownClass(cls): cls.reco.cancel().wait()

    def frame(self, path):
        import numpy as np
        from PIL import Image
        with Image.open(path) as image:
            return np.asarray(image.convert('RGB'))[:, :, ::-1].copy()

    def test_failure_panel_matches_close_but_not_done_and_wrong_panels_rejected(self):
        profile = build_food_profile()
        close = profile['states']['FoodClose']['rules'][0]
        frame = self.frame(FIX/'panel.png')
        match = self.reco.recognize(frame, close, threading.Event())
        self.assertIsNotNone(match)
        self.assertGreaterEqual(match.score, .95)
        print('FOOD_CLOSE_FAILURE', match.score, match.box)
        for done in profile['states']['FoodDone']['rules']:
            self.assertIsNone(self.reco.recognize(frame, done, threading.Event()))
        for name in ('reward-maa', 'list'):
            wrong = self.frame(ROOT/f'tests/fixtures/food_20261018/{name}.png')
            self.assertIsNone(self.reco.recognize(wrong, close, threading.Event()))
        self.assertIsNone(self.reco.recognize((frame*.5).astype('uint8'), close, threading.Event()))

    def test_real_failure_frame_one_lost_click_then_verified_list(self):
        panel = self.frame(FIX/'panel.png')
        home = self.frame(ROOT/'tests/fixtures/food_20261018/list.png')
        reco = self.reco
        class Backend(FakeBackend):
            def capture(self, event): return panel if self.clicks < 2 else home
            def recognize(self, image, rule, stop): return reco.recognize(image, rule, stop)
        profile = build_food_profile()
        profile.update(initial_state='FoodClose', poll_seconds=.001)
        profile.pop('watchdog')
        backend = Backend([])
        runner = Runner(backend, profile, Mock(), False, clock=Clock())
        with patch.object(runner.stop_event, 'wait', return_value=False):
            runner.run()
        self.assertTrue(runner.completed, runner.reason)
        self.assertEqual(backend.clicks, 2)
        self.assertEqual(runner.counters.get('food_collection_requests', 0), 0)


if __name__ == '__main__':
    unittest.main()
