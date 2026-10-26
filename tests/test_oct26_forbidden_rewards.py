"""Replay October 26 reward stalls, with no real game input or settings writes."""
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image

from bk2.forbidden import ForbiddenFlow, ForbiddenOptions, ForbiddenRunner
from bk2.forbidden_vision import ForbiddenVision, RULES
from vendor_core.backend import MaaRecognizer, Match
from tests.test_unified_replay import Offline

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT/'tests/fixtures/forbidden_20261026'


class RewardRetryTests(unittest.TestCase):
    def test_same_panel_retry_has_interval_cap_and_unchanged_deadline(self):
        for kind in ('xp', 'loot'):
            with self.subTest(kind=kind):
                flow = ForbiddenFlow(ForbiddenOptions(1), 0)
                flow.step('explore', True, 300, 1)
                self.assertEqual(flow.step(kind, True, 297, 2), kind)
                deadline = flow.deadline
                self.assertIsNone(flow.step(kind, True, 297, 3))
                self.assertIsNone(flow.step('unknown', True, 297, 6))
                self.assertIsNone(flow.step(kind, False, 297, 6))
                self.assertEqual(flow.step(kind, True, 297, 6), kind)
                self.assertEqual(flow.step(kind, True, 297, 10), kind)
                self.assertIsNone(flow.step(kind, True, 297, 14))
                self.assertEqual(flow.reward_attempts, 3)
                self.assertEqual(flow.deadline, deadline)
                self.assertEqual(flow.completed, 0)
                with self.assertRaises(TimeoutError):
                    flow.step(kind, True, 297, deadline)

    def test_resumed_reward_retries_do_not_count_unowned_round(self):
        flow = ForbiddenFlow(ForbiddenOptions(1), 0)
        self.assertEqual(flow.step('xp', True, 300, 1), 'xp')
        self.assertEqual(flow.step('xp', True, 300, 5), 'xp')
        self.assertEqual(flow.step('loot', True, 300, 6), 'loot')
        self.assertEqual(flow.reward_attempts, 1)
        self.assertEqual(flow.step('loot', True, 300, 10), 'loot')
        flow.step('explore', True, 300, 11)
        self.assertEqual(flow.completed, 0)
        self.assertFalse(flow.done)

    def test_runner_recovers_two_lost_reward_clicks_and_counts_only_on_return(self):
        backend = Mock()
        backend.capture_is_fresh.return_value = True
        stop = threading.Event()
        clock = [0.]
        scene = ['xp']
        submitted = {'xp': 0, 'loot': 0}
        backend.capture.side_effect = lambda _: scene[0]
        def click(*args):
            submitted[scene[0]] += 1
            if submitted[scene[0]] == 2:
                scene[0] = 'loot' if scene[0] == 'xp' else 'explore'
        backend.click.side_effect = click
        def wait(seconds):
            clock[0] += seconds
            return False
        runner = ForbiddenRunner(backend, ForbiddenOptions(1), lambda:297, stop,
                                 Mock(), clock=lambda:clock[0])
        runner.flow.move('encounter', 0, 60)
        with patch('bk2.forbidden_vision.ForbiddenVision.observe',
                   side_effect=lambda frame:(frame, Match((675, 461, 45, 30), 1))), \
                patch.object(stop, 'wait', side_effect=wait):
            result = runner.run()
        self.assertTrue(result['succeeded'])
        self.assertEqual(result['completed_runs'], 1)
        self.assertEqual(submitted, {'xp': 2, 'loot': 2})
        self.assertEqual(backend.click.call_count, 4)

    def test_final_recheck_changed_panel_stale_frame_or_stop_never_sends_click(self):
        for reason in ('changed', 'stale', 'stopped'):
            with self.subTest(reason=reason):
                backend = Mock()
                stop = threading.Event()
                backend.capture_is_fresh.return_value = True
                runner = ForbiddenRunner(backend, ForbiddenOptions(), lambda:300,
                                         stop, Mock(), clock=lambda:1.)
                runner.flow.move('encounter', 1)
                seen = []
                def observe(frame):
                    seen.append(1)
                    if len(seen) == 3:
                        if reason == 'changed':
                            return 'loot', Match((675, 463, 45, 30), 1)
                        if reason == 'stale':
                            backend.capture_is_fresh.return_value = False
                        else:
                            stop.set()
                    return 'xp', Match((675, 461, 45, 30), 1)
                def wait(seconds):
                    if len(seen) >= 3:
                        stop.set()
                    return stop.is_set()
                with patch('bk2.forbidden_vision.ForbiddenVision.observe', side_effect=observe), \
                        patch.object(stop, 'wait', side_effect=wait):
                    runner.run()
                backend.click.assert_not_called()

    def test_notice_explains_ui_entry_and_complete_zip(self):
        from bk2.update_notice import UPDATE_TEXT
        for text in ('挂机面板', '打开诊断目录', '整个文件夹压缩为 ZIP',
                     'report.json', '不要只发单个', '完整战斗流程仍待用户验证'):
            self.assertIn(text, UPDATE_TEXT)


@unittest.skipUnless(FIXTURES.is_dir(), 'Private October 26 screenshots not distributed')
class RewardDiagnosticReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        controller = Offline()
        controller.post_connection().wait()
        cls.reco = MaaRecognizer(ROOT/'assets', controller)
        cls.stop = threading.Event()
        cls.vision = ForbiddenVision(cls.reco, cls.stop)

    @classmethod
    def tearDownClass(cls):
        cls.reco.cancel().wait()

    def test_all_four_reports_recognize_exact_reward_and_continue(self):
        files = sorted(FIXTURES.glob('*.png'))
        self.assertEqual(len(files), 4)
        for path in files:
            with self.subTest(report=path.stem):
                frame = np.asarray(Image.open(path).convert('RGB'))[:, :, ::-1].copy()
                expected = 'xp' if '230103' in path.name else 'loot'
                kind, hit = self.vision.observe(frame)
                self.assertEqual(kind, expected)
                self.assertIsNotNone(hit)
                self.assertGreaterEqual(hit.score, .95)
                self.assertEqual(hit.box[:2], (675, 461 if expected == 'xp' else 463))

    def test_dark_continue_reproduces_old_rejection_and_keeps_modal_guards(self):
        path = FIXTURES/'20261026-230418-074679-5cc0b0f7.png'
        frame = np.asarray(Image.open(path).convert('RGB'))[:, :, ::-1].copy()
        self.assertIsNone(self.reco.recognize(frame, RULES['continue'], self.stop))
        self.assertIsNone(self.reco.recognize(frame, dict(RULES['continue'],
            template='forbidden/continue_1920.png'), self.stop))
        kind, hit = self.vision.observe(frame)
        self.assertEqual(kind, 'loot')
        self.assertIsNotNone(hit)
        dim = (frame*.5).astype(np.uint8)
        self.assertIsNone(self.vision.observe(dim)[1])
        no_title = frame.copy()
        no_title[70:260, 500:900] = 0
        self.assertNotIn(self.vision.observe(no_title)[0], ('loot', 'xp'))
        dim_button = frame.copy()
        dim_button[450:500, 500:900] = (dim_button[450:500, 500:900]*.5).astype(np.uint8)
        self.assertEqual(self.vision.observe(dim_button), ('loot', None))


if __name__ == '__main__':
    unittest.main()
