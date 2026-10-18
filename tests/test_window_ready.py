"""Window recovery regression without native window changes or game input."""
import ctypes
from ctypes import wintypes as W
from contextlib import nullcontext
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from bk2.window_ready import prepare_window, connect_backend
from vendor_core.windows import Window


class WindowReadyTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.session = SimpleNamespace(
            snapshot={'status': 'ok', 'pid': 42, 'captured_monotonic': self.now,
                      'data': {'dungeon_progress': {'result': 'finished'}}},
            operation_stop=Mock(), stop_event=Mock(), diagnostic_context={}, publish=Mock(),
            wait_ready=Mock(), runner=None, waiting_for_diamonds=False)
        self.session.operation_stop.is_set.return_value = False
        self.session.stop_event.is_set.return_value = False
        self.session.operation_stop.wait.side_effect = self.advance
        self.clock = patch('bk2.window_ready.time.monotonic', side_effect=lambda: self.now)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.window = Window(12, 'Blackfire Crusade', 42, 'E:/game/Blackfire Crusade.exe')

    def advance(self, seconds):
        self.now += seconds
        self.session.snapshot['captured_monotonic'] = self.now
        return False

    def windows(self, frames):
        win = Mock()
        frames = iter(frames)
        current = {}
        def enumerate_windows():
            nonlocal current
            current = next(frames, current)
            return current.get('candidates', [self.window])
        def rect(hwnd, pointer):
            r = ctypes.cast(pointer, ctypes.POINTER(W.RECT)).contents
            r.left = r.top = 0
            r.right, r.bottom = current.get('size', (1536, 792))
            return current.get('rect_ok', True)
        win.list_game_windows.side_effect = enumerate_windows
        win.identity.return_value = (42, self.window.executable)
        win.u.GetClientRect.side_effect = rect
        win.u.IsIconic.side_effect = lambda hwnd: current.get('minimized', False)
        win.u.IsWindowVisible.side_effect = lambda hwnd: current.get('visible', True)
        win.u.ShowWindowAsync.return_value = True
        return win

    def test_normal_window_requires_two_stable_samples_without_restore(self):
        win = self.windows([{}, {}])
        self.assertEqual(prepare_window(self.session, win, 42), self.window)
        self.assertEqual(win.list_game_windows.call_count, 2)
        win.u.ShowWindowAsync.assert_not_called()

    def test_minimized_after_cooldown_is_restored_on_next_connection(self):
        win = self.windows([{}, {}, {'minimized': True, 'size': (0, 0)},
                            {'minimized': True}, {}, {}])
        constructor = Mock()
        connect_backend(self.session, win, 42, constructor)
        connect_backend(self.session, win, 42, constructor)
        self.assertEqual(constructor.call_count, 2)
        win.u.ShowWindowAsync.assert_called_once_with(self.window.hwnd, 9)
        self.assertEqual(self.session.diagnostic_context['window_connection']['status'], 'connected')

    def test_zero_rect_and_resize_wait_for_matching_dimensions(self):
        win = self.windows([{'size': (0, 0)}, {'size': (800, 600)},
                            {'size': (1536, 792)}, {'size': (1536, 792)}])
        prepare_window(self.session, win, 42)
        self.assertEqual(win.list_game_windows.call_count, 4)
        win.u.ShowWindowAsync.assert_not_called()

    def test_client_rect_read_failure_can_recover(self):
        win = self.windows([{'rect_ok': False}, {}, {}])
        prepare_window(self.session, win, 42)
        self.assertEqual(win.list_game_windows.call_count, 3)

    def test_missing_window_can_reappear_but_other_pid_is_never_selected(self):
        other = Window(99, 'other', 77, self.window.executable)
        win = self.windows([{'candidates': [other]}, {}, {}])
        self.assertEqual(prepare_window(self.session, win, 42), self.window)
        self.assertTrue(all(c.args == (12,) for c in win.identity.call_args_list))

    def test_replaced_handle_needs_its_own_two_samples(self):
        replacement = Window(14, 'Blackfire Crusade', 42, self.window.executable)
        win = self.windows([{}, {'candidates': [replacement]}, {'candidates': [replacement]}])
        self.assertEqual(prepare_window(self.session, win, 42), replacement)
        self.assertEqual(win.list_game_windows.call_count, 3)

    def test_persistent_invalid_rect_stops_bounded_and_records_evidence(self):
        win = self.windows([{'size': (0, 0)}])
        constructor = Mock()
        with self.assertRaisesRegex(RuntimeError, '窗口恢复超时'):
            connect_backend(self.session, win, 42, constructor)
        constructor.assert_not_called()
        self.assertLessEqual(self.now, 103.001)
        report = self.session.diagnostic_context['window_connection']
        self.assertEqual(report['status'], 'timed_out')
        self.assertEqual(report['observations'][-1]['client_size'], [0, 0])
        from bk2.diagnostics import save_failure
        with tempfile.TemporaryDirectory() as d:
            saved = save_failure(Path(d), 'test', 'window', 'timeout',
                                 context=self.session.diagnostic_context)
            data = json.loads(saved.read_text(encoding='utf-8'))
        self.assertEqual(data['context']['window_connection'], report)

    def test_restore_failure_is_not_repeated_forever(self):
        win = self.windows([{'minimized': True}])
        win.u.ShowWindowAsync.return_value = False
        with self.assertRaisesRegex(RuntimeError, '窗口恢复超时'):
            prepare_window(self.session, win, 42)
        win.u.ShowWindowAsync.assert_called_once()
        self.assertFalse(self.session.diagnostic_context['window_connection']['observations'][0]['restore_accepted'])

    def test_ambiguous_and_identity_changed_windows_fail_without_restore(self):
        for mode in ('ambiguous', 'identity'):
            with self.subTest(mode=mode):
                win = self.windows([{'candidates': [self.window]*2}] if mode == 'ambiguous' else [{}])
                if mode == 'identity':
                    win.identity.return_value = (43, self.window.executable)
                with self.assertRaises(RuntimeError):
                    prepare_window(self.session, win, 42)
                win.u.ShowWindowAsync.assert_not_called()

    def test_stopped_or_stale_memory_blocks_all_window_actions(self):
        for mode in ('stop', 'stale'):
            with self.subTest(mode=mode):
                win = self.windows([{'minimized': True}])
                self.session.operation_stop.is_set.return_value = mode == 'stop'
                self.session.snapshot['captured_monotonic'] = self.now-4
                with self.assertRaises((RuntimeError, InterruptedError)):
                    prepare_window(self.session, win, 42)
                win.list_game_windows.assert_not_called()
                win.u.ShowWindowAsync.assert_not_called()

    def test_stop_during_wait_prevents_constructor(self):
        win = self.windows([{'size': (0, 0)}, {}, {}])
        def stop(seconds):
            self.session.operation_stop.is_set.return_value = True
            return True
        self.session.operation_stop.wait.side_effect = stop
        constructor = Mock()
        with self.assertRaises(InterruptedError):
            connect_backend(self.session, win, 42, constructor)
        constructor.assert_not_called()

    def test_memory_failure_during_wait_prevents_constructor(self):
        win = self.windows([{'size': (0, 0)}, {}, {}])
        def stale(seconds):
            self.session.snapshot['status'] = 'unknown'
            return self.advance(seconds)
        self.session.operation_stop.wait.side_effect = stale
        constructor = Mock()
        with self.assertRaises(RuntimeError):
            connect_backend(self.session, win, 42, constructor)
        constructor.assert_not_called()

    def test_pre_connection_race_retries_once_and_keeps_both_attempts(self):
        win = self.windows([{}])
        backend = object()
        constructor = Mock(side_effect=[RuntimeError('游戏窗口客户区无效'), backend])
        self.assertIs(connect_backend(self.session, win, 42, constructor), backend)
        attempts = self.session.diagnostic_context['window_connection_attempts']
        self.assertEqual([a['status'] for a in attempts], ['connection_failed', 'connected'])
        self.assertEqual(constructor.call_count, 2)

    def test_repeated_window_race_stops_after_two_constructor_attempts(self):
        constructor = Mock(side_effect=RuntimeError('游戏窗口客户区无效'))
        with self.assertRaisesRegex(RuntimeError, '客户区无效'):
            connect_backend(self.session, self.windows([{}]), 42, constructor)
        self.assertEqual(constructor.call_count, 2)

    def test_non_window_connection_error_is_never_retried(self):
        constructor = Mock(side_effect=RuntimeError('连接游戏失败'))
        with self.assertRaisesRegex(RuntimeError, '连接游戏失败'):
            connect_backend(self.session, self.windows([{}]), 42, constructor)
        constructor.assert_called_once()

    def test_factory_failure_records_current_profile_not_previous_runner(self):
        from bk2.automation import Batch
        self.session.diagnostic_context = {'config': {'profile': {'id': 'dungeon_3'}},
                                           'runner': {'state': 'old'}}
        batch = Batch(self.session, {}, Mock(side_effect=RuntimeError('窗口恢复超时')))
        config = {'profile': {'id': 'dungeon_6'}}
        with self.assertRaises(RuntimeError):
            batch.execute(config)
        self.assertEqual(self.session.diagnostic_context['config'], config)
        self.assertNotIn('runner', self.session.diagnostic_context)
        self.assertIsNone(batch.backend)

    def test_run_plan_factory_prepares_each_backend_without_real_input_or_state_writes(self):
        from bk2.automation import run_plan
        win = self.windows([{}, {}, {'minimized': True}, {}, {}])
        win.emergency_pressed.return_value = False
        def batch(session, plan, factory, *args, **kwargs):
            def run():
                factory()
                factory()
                return {'succeeded': True, 'completed_runs': 0}
            return SimpleNamespace(food_due=0, run=run)
        with patch('vendor_core.windows.Windows', return_value=win), \
                patch('bk2.automation.input_lease', return_value=nullcontext()), \
                patch('bk2.automation.validate_dungeon_plan'), \
                patch('bk2.automation.validate_food_options'), \
                patch('bk2.automation.Batch', side_effect=batch), \
                patch('bk2.automation.ScheduleStore') as store, \
                patch('bk2.food.FoodTimerStore') as food_store, \
                patch('bk2.backend.DiagnosticBackend.__init__', return_value=None) as init:
            result = run_plan(self.session, {}, dry_run=True)
        self.assertTrue(result['succeeded'])
        self.assertEqual(init.call_count, 2)
        win.u.ShowWindowAsync.assert_called_once()
        store.assert_not_called()
        food_store.assert_not_called()
