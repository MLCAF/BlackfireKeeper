"""Replay the reported black frame without game input or real waiting."""
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
import numpy as np
from PIL import Image
from bk2.backend import DiagnosticBackend

ROOT=Path(__file__).resolve().parents[1]

@unittest.skipUnless((ROOT/'tests/fixtures').is_dir(), 'Local replay screenshots are not distributed; see tests/README.md')
class CaptureRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        def read(name):
            with Image.open(ROOT/'tests/fixtures/capture_20261018'/name) as im:
                return np.array(im.convert('RGB'))[:,:,::-1].copy()
        cls.black=read('black.png');cls.room=read('room.png')

    def backend(self, frames):
        b=DiagnosticBackend.__new__(DiagnosticBackend)
        b.recognition_trace=[];b.last_image=None;b.last_capture=0;b.last_shape=(720,1396)
        b.capture_method='FramePool';b.input_method='PostMessage';b.guard=Mock();b.emit=Mock()
        b.controller=Mock();b.wait=Mock()
        b.controller.post_screencap.side_effect=[Mock(succeeded=True,get=Mock(return_value=f)) for f in frames]
        return b

    def clock(self):
        self.now=100.
        stop=Mock();stop.is_set.return_value=False
        def wait(seconds):self.now+=seconds;return False
        stop.wait.side_effect=wait
        return stop,patch('bk2.backend.time.monotonic',side_effect=lambda:self.now)

    def test_real_diagnostic_is_black_but_dark_room_is_valid(self):
        self.assertTrue(DiagnosticBackend.is_nearly_uniform(self.black))
        self.assertFalse(DiagnosticBackend.is_nearly_uniform(self.room))

    def test_three_black_frames_recover_only_after_two_valid_frames(self):
        b=self.backend([self.black]*3+[self.room]*2)
        stop,clock=self.clock()
        with clock:
            result=b.capture(stop)
            self.assertTrue(b.capture_is_fresh())
        np.testing.assert_array_equal(result,self.room)
        self.assertEqual(b.controller.post_screencap.call_count,5)
        self.assertEqual(b.capture_health['status'],'recovered')
        self.assertEqual(b.capture_health['consecutive_valid'],2)
        b.controller.post_click.assert_not_called()

    def test_black_between_valid_frames_resets_recovery_confirmation(self):
        b=self.backend([self.black,self.room,self.black,self.room,self.room])
        stop,clock=self.clock()
        with clock:b.capture(stop)
        self.assertEqual(b.controller.post_screencap.call_count,5)
        self.assertEqual(b.capture_health['rejected_frames'],2)

    def test_persistent_black_stops_bounded_and_saves_capture_health(self):
        b=self.backend([self.black]*30);stop,clock=self.clock()
        with clock:
            with self.assertRaisesRegex(RuntimeError,'截图恢复超时'):b.capture(stop)
            self.assertLessEqual(self.now,106.00001)
            self.assertFalse(b.capture_is_fresh())
            with tempfile.TemporaryDirectory() as folder:
                b.diagnostic_root=Path(folder)
                record=json.loads(Path(b.save_diagnostic('OpeningBuffLeaveConfirm','failure',{})).read_text(encoding='utf8'))
                self.assertEqual(record['capture_health']['status'],'timed_out')
                self.assertGreater(record['capture_health']['rejected_frames'],3)
                self.assertIsNotNone(record['screenshot_age_seconds'])
        b.controller.post_click.assert_not_called()

    def test_stop_during_blank_wait_does_not_retry(self):
        b=self.backend([self.black]);stop,clock=self.clock();stop.wait.side_effect=lambda _:True
        with clock:
            with self.assertRaises(InterruptedError):b.capture(stop)
        self.assertEqual(b.controller.post_screencap.call_count,1)

    def test_memory_or_window_guard_failure_is_not_retried(self):
        b=self.backend([self.black,self.room]);stop,clock=self.clock()
        b.guard.side_effect=[None,None,None,RuntimeError('内存失效')]
        with clock:
            with self.assertRaisesRegex(RuntimeError,'内存失效'):b.capture(stop)
        self.assertEqual(b.controller.post_screencap.call_count,1)

    def test_normal_frame_has_no_extra_wait(self):
        b=self.backend([self.room]);stop,clock=self.clock()
        with clock:b.capture(stop)
        stop.wait.assert_not_called()
        self.assertEqual(b.capture_health['status'],'valid')

    def test_empty_or_failed_capture_is_not_treated_as_a_transition(self):
        for frame in (None,np.zeros((10,10,3),dtype=np.uint8)):
            b=self.backend([frame]);stop,clock=self.clock()
            with clock:
                with self.assertRaisesRegex(RuntimeError,'为空或尺寸异常'):b.capture(stop)
            self.assertEqual(b.controller.post_screencap.call_count,1)

if __name__=='__main__':unittest.main()
