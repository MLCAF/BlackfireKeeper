"""Regression cases from October 20 diagnostics; no player data or game input."""
import threading
import tempfile
from pathlib import Path
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from bk2.backend import DiagnosticBackend
from bk2.memory import MemoryReadError
from bk2.state import bind_when_ready
from vendor_core.windows import Windows
from vendor_core.runner import Runner
from vendor_core.backend import Match


class WindowRecoveryTests(unittest.TestCase):
    def backend(self):
        b = object.__new__(DiagnosticBackend)
        b.window = SimpleNamespace(hwnd=123, pid=42, executable='game.exe')
        b.window_geometry = {}
        b.win = Mock()
        b.win.identity.return_value = (42, 'game.exe')
        b.win.size.return_value = (1920, 991)
        b.geometry_stop = Mock()
        b.geometry_stop.is_set.return_value = False
        b.last_shape = (720, 1395)
        b.last_capture = 100
        return b

    def test_transient_client_loss_requires_two_stable_checks_and_new_capture(self):
        b = self.backend()
        b.win.size.side_effect = [RuntimeError('游戏窗口客户区无效'), (1920, 991), (1920, 991)]
        b._guard_physical((1920, 991))
        self.assertEqual(b.win.check.call_count, 2)
        self.assertEqual(b.window_geometry['runtime_check']['status'], 'recovered')
        self.assertIsNone(b.last_shape)
        self.assertEqual(b.last_capture, 0)

    def test_invalid_client_does_not_mask_reused_hwnd(self):
        b = self.backend()
        b.win.size.side_effect = RuntimeError('游戏窗口客户区无效')
        b.win.identity.return_value = (99, 'game.exe')
        with self.assertRaisesRegex(RuntimeError, '身份发生变化'):
            b._guard_physical((1920, 991))
        b.geometry_stop.wait.assert_not_called()

    def test_persistent_loss_is_bounded(self):
        b = self.backend()
        b.win.size.side_effect = RuntimeError('游戏窗口客户区无效')
        with patch('bk2.backend.time.monotonic', side_effect=[0, 1, 2, 3]):
            with self.assertRaisesRegex(RuntimeError, '3 秒'):
                b._guard_physical((1920, 991))
        self.assertEqual(b.window_geometry['runtime_check']['status'], 'timed_out')

    def test_stop_during_wait_cancels(self):
        b = self.backend()
        b.win.size.side_effect = RuntimeError('游戏窗口客户区无效')
        b.geometry_stop.is_set.side_effect = [False, True]
        with self.assertRaises(InterruptedError):
            b._guard_physical((1920, 991))

    def test_foreground_waits_for_real_focus(self):
        w = object.__new__(Windows)
        w.u = Mock()
        w.u.GetForegroundWindow.side_effect = [1, 1, 123]
        with patch('vendor_core.windows.time.sleep') as sleep:
            w.foreground(123)
        sleep.assert_called_once()
        w.u.SetForegroundWindow.assert_called_once_with(123)

    def test_foreground_failure_still_raises(self):
        w = object.__new__(Windows)
        w.u = Mock()
        w.u.GetForegroundWindow.return_value = 1
        with patch('vendor_core.windows.time.monotonic', side_effect=[0, 1]):
            with self.assertRaisesRegex(RuntimeError, '无法激活'):
                w.foreground(123)


class StartupBindingTests(unittest.TestCase):
    def test_login_not_ready_then_unique_binding(self):
        reader = Mock(binding_diagnostics={'structural_candidates': 0})
        reader.bind.side_effect = [MemoryReadError('zero candidates'), {'status': 'ok'}]
        stop = Mock()
        stop.is_set.return_value = False
        result = bind_when_ready(reader, stop, Mock())
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(reader.binding_diagnostics['startup_attempts'], 2)

    def test_ambiguous_owner_never_retried(self):
        reader = Mock(binding_diagnostics={'structural_candidates': 2})
        reader.bind.side_effect = MemoryReadError('ambiguous')
        with self.assertRaisesRegex(MemoryReadError, 'ambiguous'):
            bind_when_ready(reader, threading.Event(), Mock())
        self.assertEqual(reader.bind.call_count, 1)

    def test_deadline_and_cancellation_are_distinct(self):
        reader = Mock(binding_diagnostics={'structural_candidates': 0})
        reader.bind.side_effect = InterruptedError('deadline')
        with self.assertRaisesRegex(MemoryReadError, '超时'):
            bind_when_ready(reader, threading.Event(), Mock())
        stop = threading.Event()
        stop.set()
        with self.assertRaises(InterruptedError):
            bind_when_ready(reader, stop, Mock())


class RoomTransitionTests(unittest.TestCase):
    def runner(self):
        backend = Mock(spec=['click', 'capture', 'recognize'])
        runner = Runner(backend, dict(initial_state='Progress',
                        states={'Progress': {'rules': []}}), Mock(), False)
        runner.refresh_match_before_click = lambda rule, match: match
        runner.interrupt_visible = lambda *args: False
        runner.higher_priority_visible = lambda *args: False
        runner.stop_event = Mock()
        runner.stop_event.wait.return_value = False
        match = Match((672, 610, 59, 61), 1.)
        rule = dict(name='stairs', template='stairs.png', retry_max_clicks=2,
                    retry_center_clicks=1, retry_radius=0, transition_wait_seconds=8)
        return runner, backend, match, rule

    def test_delayed_server_change_does_not_send_second_click(self):
        runner, backend, match, rule = self.runner()
        backend.recognize.side_effect = [match, match, None, None]
        self.assertTrue(runner.click_with_change_confirmation(rule, match))
        self.assertEqual(backend.click.call_count, 1)
        self.assertEqual(backend.capture.call_count, 3)

    def test_no_server_response_still_has_click_and_time_bound(self):
        runner, backend, match, rule = self.runner()
        backend.recognize.return_value = match
        with patch('vendor_core.runner.time.monotonic', side_effect=[0, 9, 10, 19]):
            with self.assertRaisesRegex(RuntimeError, '连续 2 次'):
                runner.click_with_change_confirmation(rule, match)
        self.assertEqual(backend.click.call_count, 2)


class DiagnosticPointerTests(unittest.TestCase):
    def test_locked_index_does_not_discard_saved_report(self):
        from bk2.diagnostics import write_bundle_report
        original = Path.replace
        def replace(path, target):
            if path.name == 'latest.tmp':
                raise PermissionError('external index reader')
            return original(path, target)
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)/'bundle'
            folder.mkdir()
            with patch.object(Path, 'replace', replace), \
                    patch('bk2.diagnostics.time.sleep') as sleep, \
                    patch('sys.stderr'):
                report = write_bundle_report(folder, {'stage': 'test'})
            self.assertTrue(report.is_file())
            self.assertEqual(sleep.call_count, 4)
