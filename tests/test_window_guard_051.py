"""DPI changes must not mask or invent real client-size changes."""
from contextlib import contextmanager
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from bk2.backend import DiagnosticBackend


class PhysicalGuardTests(unittest.TestCase):
    def setUp(self):
        self.backend = object.__new__(DiagnosticBackend)
        self.backend.window = SimpleNamespace(hwnd=123)
        self.backend.original_size = (1536, 792)  # logical size at 125% DPI
        self.backend.window_geometry = dict(actual_client_size=[1920, 991])
        self.backend.win = Mock()
        self.physical = False
        self.actual = (1920, 991)
        self.backend.win.size.side_effect = lambda hwnd: self.actual if self.physical else (1536, 792)
        def check(window, expected):
            if self.backend.win.size(window.hwnd) != expected:
                raise RuntimeError('运行中窗口尺寸发生变化，请停止并重新连接')
        self.backend.win.check.side_effect = check

    @contextmanager
    def pixels(self):
        self.physical = True
        try:
            yield
        finally:
            self.physical = False

    def run_guard(self):
        with patch('bk2.window_geometry.Geometry') as geometry:
            geometry.return_value.pixels.side_effect = self.pixels
            self.backend.guard()

    def test_logical_baseline_does_not_cause_false_resize(self):
        self.run_guard()
        self.backend.win.check.assert_called_once_with(self.backend.window, (1920, 991))
        self.assertFalse(self.physical)

    def test_real_resize_still_stops_and_records_physical_evidence(self):
        self.actual = (1280, 720)
        with self.assertRaisesRegex(RuntimeError, '窗口尺寸发生变化'):
            self.run_guard()
        report = self.backend.window_geometry['runtime_check']
        self.assertEqual(report['actual_client_size'], [1280, 720])
        self.assertEqual(report['expected_client_size'], [1920, 991])
        self.assertFalse(self.physical)

    def test_identity_and_minimized_checks_are_not_bypassed(self):
        self.backend.win.check.side_effect = RuntimeError('目标窗口的进程身份发生变化')
        with self.assertRaisesRegex(RuntimeError, '进程身份发生变化'):
            self.run_guard()
        self.assertFalse(self.physical)
