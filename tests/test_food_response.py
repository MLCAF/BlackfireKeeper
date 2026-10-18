"""Delayed food responses never cause a second collection request."""
import unittest
from unittest.mock import Mock, patch
from pathlib import Path

from bk2.food_profile import build_food_profile
from vendor_core.runner import Runner
from tests.upstream.test_runner import Clock, FakeBackend

PANEL = {'体力工坊标题.png', '体力收集文字.png', '体力工坊关闭.png'}
REWARD = {'体力奖励面包.png', '体力奖励确认.png'}
HOME = {'体力面包入口.png'}
ROOT = Path(__file__).resolve().parents[1]


class FoodResponseTests(unittest.TestCase):
    def run_flow(self, delay=5, never=False, wrong_reward=False, stop=False,
                 memory_error=False, click_error=False, flash=False):
        clock = Clock()
        class Backend(FakeBackend):
            submitted_at = None
            shown_frames = 0

            def capture(self, event):
                if not self.clicks:
                    return PANEL
                if self.clicks == 1:
                    if stop:
                        event.set()
                    if never or clock.value-self.submitted_at < delay:
                        return PANEL
                    self.shown_frames += 1
                    if flash and self.shown_frames > 1:
                        return PANEL
                    return {'体力奖励确认.png'} if wrong_reward else REWARD
                return PANEL if self.clicks == 2 else HOME

            def click(self, *args):
                if click_error:
                    raise RuntimeError('点击发送失败')
                super().click(*args)
                if self.clicks == 1:
                    self.submitted_at = clock.value

            def guard(self):
                if memory_error and self.clicks:
                    raise RuntimeError('内存状态超过 3 秒未更新，停止操作')

            def save_diagnostic(self, *args):
                return 'mock-diagnostic.json'

        backend = Backend([])
        profile = build_food_profile()
        profile['initial_state'] = 'FoodCollect'
        profile.pop('watchdog')  # Fake frames are labels; pixel tests run separately.
        profile['poll_seconds'] = .001
        runner = Runner(backend, profile, Mock(), False, clock=clock)
        with patch.object(runner.stop_event, 'wait', side_effect=lambda _: runner.stop_event.is_set()):
            runner.run()
        return runner, backend

    def test_delayed_reward_waits_more_than_old_point_six_seconds_then_completes(self):
        runner, backend = self.run_flow(delay=5)
        self.assertTrue(runner.completed, runner.reason)
        self.assertEqual(backend.clicks, 3)  # collect, confirm, close
        self.assertEqual(runner.counters['food_collection_requests'], 1)
        self.assertEqual(runner.counters['food_collections'], 1)
        reward = next(e for e in runner.history if e.get('next') == 'FoodReward')
        self.assertGreaterEqual(reward['elapsed']-backend.submitted_at, 4.9)

    def test_immediate_response_keeps_the_normal_flow(self):
        runner, backend = self.run_flow(delay=0)
        self.assertTrue(runner.completed, runner.reason)
        self.assertEqual(backend.clicks, 3)

    def test_no_response_times_out_without_resubmitting_or_reporting_collection(self):
        runner, backend = self.run_flow(never=True)
        self.assertFalse(runner.completed)
        self.assertIn('FoodAwaitReward 等待超时', runner.reason)
        self.assertEqual(backend.clicks, 1)
        self.assertEqual(runner.counters['food_collection_requests'], 1)
        self.assertNotIn('food_collections', runner.counters)
        self.assertLess(runner.stats.active, 32)

    def test_unrelated_confirmation_never_gets_clicked(self):
        runner, backend = self.run_flow(wrong_reward=True)
        self.assertFalse(runner.completed)
        self.assertEqual(backend.clicks, 1)
        self.assertNotIn('food_collections', runner.counters)

    def test_single_reward_frame_does_not_confirm_response(self):
        runner, backend = self.run_flow(flash=True)
        self.assertFalse(runner.completed)
        self.assertEqual(backend.clicks, 1)
        self.assertNotIn('food_collections', runner.counters)

    def test_stop_while_waiting_sends_no_more_input(self):
        runner, backend = self.run_flow(stop=True)
        self.assertFalse(runner.completed)
        self.assertEqual(backend.clicks, 1)

    def test_memory_failure_while_waiting_sends_no_more_input(self):
        runner, backend = self.run_flow(memory_error=True)
        self.assertFalse(runner.completed)
        self.assertIn('内存状态', runner.reason)
        self.assertEqual(backend.clicks, 1)

    def test_input_failure_is_not_treated_as_a_submitted_request(self):
        runner, backend = self.run_flow(click_error=True)
        self.assertFalse(runner.completed)
        self.assertEqual(backend.clicks, 0)
        self.assertNotIn('food_collection_requests', runner.counters)

    def test_only_reading_can_happen_in_response_wait_state(self):
        from bk2.runtime import ROOT
        from vendor_core.config import validate
        profile = build_food_profile()
        validate(profile, ROOT/'assets/image', True)
        rules = profile['states']['FoodAwaitReward']['rules']
        self.assertEqual(profile['states']['FoodAwaitReward']['timeout_seconds'], 30)
        self.assertTrue(all(r['action'] == 'none' for r in rules))
        self.assertTrue(all(r['threshold'] == .95 and r['confirm_frames'] == 2 for r in rules))


class FoodClaimDiagnosticTests(unittest.TestCase):
    def test_timeout_context_distinguishes_request_from_confirmed_reward(self):
        import time
        from types import SimpleNamespace
        from bk2.automation import Batch, load_plan
        from tests.test_migration import session
        s = session()
        s.snapshot['data']['food_fields'] = dict(bread=305, start_time=(time.time()-5000)*1000)
        plan = load_plan()
        plan['food']['enabled'] = True
        batch = Batch(s, plan, Mock())
        batch.execute = Mock(return_value=SimpleNamespace(completed=False,
            counters={'food_collection_requests': 1}, reason='FoodAwaitReward timeout'))
        with self.assertRaisesRegex(RuntimeError, '流程未确认完成'):
            batch.maybe_food(force=True)
        claim = s.diagnostic_context['food_claim']
        self.assertEqual(claim['before']['count'], 305)
        self.assertEqual(claim['after']['count'], 305)
        self.assertEqual(claim['counters'], {'food_collection_requests': 1})
        self.assertFalse(claim['ui_completed'])

    def test_memory_failure_during_execution_keeps_original_stop(self):
        import time
        from types import SimpleNamespace
        from bk2.automation import Batch, load_plan
        from tests.test_migration import session
        s = session()
        s.snapshot['data']['food_fields'] = dict(bread=305, start_time=(time.time()-5000)*1000)
        plan = load_plan()
        plan['food']['enabled'] = True
        batch = Batch(s, plan, Mock())
        def execute(config):
            s.snapshot = {'status': 'unknown'}
            s.operation_stop.set()
            return SimpleNamespace(completed=False, counters={'food_collection_requests': 1})
        batch.execute = execute
        with self.assertRaisesRegex(InterruptedError, '体力领取期间停止'):
            batch.maybe_food(force=True)
        self.assertEqual(s.diagnostic_context['food_claim']['after']['status'], 'unknown')


@unittest.skipUnless((ROOT/'tests/fixtures/food_response_20261018/panel.png').is_file(),
                     'Local failure screenshot is not distributed')
class FoodResponseReplayTests(unittest.TestCase):
    def test_actual_failure_frame_with_delayed_reward_old_stops_new_completes(self):
        import copy
        import threading
        import numpy as np
        from PIL import Image
        from vendor_core.backend import MaaRecognizer
        from tests.test_unified_replay import Offline

        def read(path):
            with Image.open(ROOT/path) as image:
                return np.asarray(image.convert('RGB'))[:, :, ::-1].copy()
        panel = read('tests/fixtures/food_response_20261018/panel.png')
        reward = read('tests/fixtures/food_20261018/reward-maa.png')
        home = read('tests/fixtures/food_20261018/list.png')
        controller = Offline()
        controller.post_connection().wait()
        reco = MaaRecognizer(ROOT/'assets', controller)
        try:
            self.assertIsNotNone(reco.recognize(panel,
                build_food_profile()['states']['FoodCollect']['rules'][0], threading.Event()))
            for legacy in (True, False):
                with self.subTest(legacy=legacy):
                    class Backend:
                        clicks = 0
                        pending_frames = 0
                        def guard(self): pass
                        def cancel(self): pass
                        def capture(self, stop):
                            if self.clicks == 1:
                                self.pending_frames += 1
                                return panel if self.pending_frames <= 5 else reward
                            return home if self.clicks >= 3 else panel
                        def recognize(self, image, rule, stop):
                            return reco.recognize(image, rule, stop)
                        def click(self, *args): self.clicks += 1
                    profile = build_food_profile()
                    profile.update(initial_state='FoodCollect', poll_seconds=.001)
                    profile.pop('watchdog')
                    if legacy:
                        collect = profile['states']['FoodCollect']['rules'][0]
                        collect.update(verify_change=True, success_match=copy.deepcopy(
                            profile['states']['FoodReward']['rules'][0]['requires_match']),
                            next='FoodReward', retry_max_clicks=1, retry_center_clicks=1,
                            retry_radius=0, increment_counter='food_collections')
                    backend = Backend()
                    runner = Runner(backend, profile, Mock(), False, clock=Clock())
                    with patch.object(runner.stop_event, 'wait', return_value=False):
                        runner.run()
                    self.assertEqual(runner.completed, not legacy, runner.reason)
                    self.assertEqual(backend.clicks, 1 if legacy else 3)
                    if legacy:
                        self.assertIn('连续 1 次点击', runner.reason)
                    else:
                        self.assertEqual(runner.counters['food_collection_requests'], 1)
                        self.assertEqual(runner.counters['food_collections'], 1)
        finally:
            reco.cancel().wait()
