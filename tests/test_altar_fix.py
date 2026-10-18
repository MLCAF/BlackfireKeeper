"""Replay the real second-dungeon failure with Maa and strictly fake input."""
import copy
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image
from bk2.backend import DiagnosticBackend
from bk2.profiles import build_dungeon_profile
from vendor_core.backend import MaaBackend, MaaRecognizer
from vendor_core.config import validate
from vendor_core.runner import Runner
from tests.test_unified_replay import Offline
from tests.upstream.test_runner import Clock, FakeBackend

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = 'tests/fixtures/failures_20261018/'


@unittest.skipUnless((ROOT/'tests/fixtures').is_dir(), 'Local replay screenshots are not distributed; see tests/README.md')
class AltarReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.controller = Offline()
        cls.controller.post_connection().wait()
        cls.recognizer = MaaRecognizer(ROOT/'assets', cls.controller)
        cls.base = json.loads((ROOT/'config/profiles/dungeon_unlimited.json').read_text(encoding='utf-8'))
        cls.plan = json.loads((ROOT/'config/dungeon_plan.json').read_text(encoding='utf-8'))

    @classmethod
    def tearDownClass(cls):
        cls.recognizer.cancel().wait()

    def frame(self, path):
        with Image.open(ROOT/path) as im:
            return np.asarray(im.convert('RGB'))[:, :, ::-1].copy()

    def profile(self, index=1, buff='experience'):
        return build_dungeon_profile(self.base, dict(self.plan['tasks'][index], buff=buff), self.plan['tasks'])

    def altar(self, profile=None):
        return next(r for r in (profile or self.profile())['states']['Progress']['rules']
                    if r['template'] == '开局祭坛核心.png')

    def hit(self, image, rule):
        return self.recognizer.recognize(image, rule, threading.Event())

    def test_original_failure_and_core_on_actual_hover_and_nonhover_frames(self):
        blue = self.frame(FIXTURE+'second_dungeon_altar.png')
        old = next(r for r in self.base['states']['Progress']['rules'] if r['template']=='开局buff.png')
        self.assertIsNone(self.hit(blue, old))
        measured = self.hit(blue, dict(old, threshold=.1))
        self.assertLess(measured.score, .95)
        print('ALTAR_OLD', measured)
        for path in [FIXTURE+'second_dungeon_altar.png',
                     'tests/fixtures/failures_20261003/buff_hover.png']:
            hit = self.hit(self.frame(path), self.altar())
            self.assertIsNotNone(hit, path)
            self.assertGreater(hit.score, .99)
            print('ALTAR_CORE', path, hit)

    def test_rejects_no_entrance_marker_and_dimmed_altar(self):
        blue = self.frame(FIXTURE+'second_dungeon_altar.png')
        no_marker = blue.copy()
        no_marker[65:145, 550:650] = 0
        self.assertIsNone(self.hit(no_marker, self.altar()))
        dimmed = (blue.astype(float)*.4).astype(np.uint8)
        self.assertIsNone(self.hit(dimmed, self.altar()))

    def test_real_negative_screens_and_open_buff_modal(self):
        paths = ['failures_20261003/dungeon_list.png', 'failures_20261003/timeout_battle.png',
                 'failures_20261008/other_dungeon.png', 'failures_20261008/quest.png',
                 'failures_20261008/expired_session.png', 'failures_20261008/wrong_tab.png',
                 'failures_20261018/buff_selection.png']
        for path in paths:
            self.assertIsNone(self.hit(self.frame('tests/fixtures/'+path), self.altar()), path)

    def test_all_six_profiles_validate_preserve_selected_buff_and_base(self):
        before = copy.deepcopy(self.base)
        for i in range(6):
            for buff, template in [('experience','经验buff.png'), ('damage','伤害buff.png')]:
                profile = self.profile(i, buff)
                validate(profile, ROOT/'assets/image', True)
                self.assertEqual(self.altar(profile)['success_match']['template'], template)
                self.assertEqual(profile['states']['OpeningBuff']['rules'][0]['template'], template)
        self.assertEqual(self.base, before)

    def test_actual_pixels_progress_to_opening_buff_with_fake_click(self):
        altar = self.frame(FIXTURE+'second_dungeon_altar.png')
        modal = self.frame(FIXTURE+'buff_selection.png')
        reco = self.recognizer
        for buff in ['experience', 'damage']:
            with self.subTest(buff=buff):
                class Replay:
                    clicks = 0
                    captures_after_click = 0
                    def guard(self): pass
                    def capture(self, stop):
                        if self.clicks:
                            self.captures_after_click += 1
                        return modal if self.clicks else altar
                    def recognize(self, frame, rule, stop): return reco.recognize(frame, rule, stop)
                    def click(self, match, offset, stop, pause):
                        x,y,w,h = match.box
                        point = (x+w//2+offset[0],y+h//2+offset[1])
                        assert 1000 <= point[0] <= 1090 and 300 <= point[1] <= 400, point
                        self.clicks += 1
                    def cancel(self): pass
                data = self.profile(buff=buff)
                data.update(initial_state='Progress', poll_seconds=.001)
                data.pop('watchdog')
                data['states']['Progress']['timeout_seconds'] = 10
                self.altar(data)['retry_wait_seconds'] = .001
                backend = Replay()
                events = []
                def emit(kind, value):
                    if kind == 'log':
                        events.append(value)
                        if '状态转换：Progress → OpeningBuff' in value:
                            runner.stop('回放到达 Buff 页面')
                runner = Runner(backend, data, emit, False, clock=Clock())
                runner.run()
                self.assertEqual(runner.stats.state, 'OpeningBuff', runner.reason)
                self.assertEqual(backend.clicks, 1)
                self.assertGreaterEqual(backend.captures_after_click, 2)
                self.assertTrue(any('连续两帧确认后继页面' in v for v in events))

    def test_ignored_altar_click_stops_after_two_attempts(self):
        data = self.profile()
        data.update(initial_state='Progress', poll_seconds=.001)
        data.pop('watchdog')
        self.altar(data)['retry_wait_seconds'] = .001
        backend = FakeBackend([{'开局祭坛核心.png', '副本入口进度.png'}])
        runner = Runner(backend, data, Mock(), False, clock=Clock())
        runner.run()
        self.assertEqual(backend.clicks, 2)
        self.assertEqual(runner.stats.state, 'Progress')
        self.assertIn('连续 2 次点击', runner.reason)

    def test_missing_damage_never_selects_experience(self):
        data = self.profile(buff='damage')
        data.update(initial_state='OpeningBuff', poll_seconds=.001)
        data.pop('watchdog')
        data['states']['OpeningBuff']['timeout_seconds'] = .2
        backend = FakeBackend(['经验buff.png'])
        runner = Runner(backend, data, Mock(), False, clock=Clock())
        runner.run()
        self.assertEqual(backend.clicks, 0)
        self.assertIn('超时', runner.reason)


class DiagnosticTraceTests(unittest.TestCase):
    def test_watchdog_new_capture_keeps_previous_recognition_and_matching_pixels(self):
        backend = DiagnosticBackend.__new__(DiagnosticBackend)
        backend.recognition_trace = [dict(name='altar', template='开局祭坛核心.png', accepted=False)]
        previous = np.full((80,80,3), [12,34,56], dtype=np.uint8)
        backend.last_image = previous.copy()
        backend.last_capture = time.monotonic()-1
        backend.capture_method, backend.input_method = 'offline', 'none'
        def fake_capture(instance, stop):
            instance.recognition_trace.clear()
            instance.last_image = np.zeros((80,80,3), dtype=np.uint8)
            instance.last_capture = time.monotonic()
            return instance.last_image
        with tempfile.TemporaryDirectory() as tmp, patch.object(DiagnosticBackend,'_capture_valid',fake_capture):
            backend.diagnostic_root = Path(tmp)
            backend.capture(threading.Event())
            report_path = Path(backend.save_diagnostic('Progress','warning',{}))
            report = json.loads(report_path.read_text(encoding='utf-8'))
            self.assertEqual(report['recognition'], [])
            prior = report['previous_frame_recognition']
            self.assertEqual(prior['recognition'][0]['name'], 'altar')
            self.assertGreaterEqual(prior['capture_age_seconds'], 1)
            with Image.open(report_path.parent/prior['screenshot']) as im:
                np.testing.assert_array_equal(np.asarray(im)[:,:,::-1], previous)

    def test_first_capture_report_has_no_invented_previous_recognition(self):
        backend = DiagnosticBackend.__new__(DiagnosticBackend)
        backend.last_image = None
        backend.last_capture = time.monotonic()
        backend.recognition_trace = []
        backend.capture_method, backend.input_method = 'offline', 'none'
        with tempfile.TemporaryDirectory() as tmp:
            backend.diagnostic_root = Path(tmp)
            report = json.loads(Path(backend.save_diagnostic('Entry','warning',{})).read_text(encoding='utf-8'))
            self.assertNotIn('previous_frame_recognition', report)


if __name__ == '__main__':
    unittest.main()
