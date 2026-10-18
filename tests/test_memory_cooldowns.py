import copy
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import numpy as np

from bk2.cooldowns import deadline, summarize, read_fields, DUNGEON_IDS
from bk2.automation import Batch, load_plan
from bk2.runtime import Session
from bk2.scheduler import MemoryScheduler, MemoryCooldown
from bk2.diagnostics import save_failure
from bk2.memory import MemoryReadError
from vendor_core.cooldown import CooldownEstimate, DeferredCooldown
from vendor_core.diamonds import DiamondResult

NOW = 1792247600.


def make_session():
    session = Session()
    session.snapshot = {'status': 'ok', 'pid': 10, 'captured_monotonic': time.monotonic(),
        'data': {'dungeon_progress': {'result': 'finished'}, 'cooldown_fields': {
            'dungeons': {key: {'refresh_time': (NOW + order * 100) * 1000,
                              'has_unlocked': True} for order, key in enumerate(DUNGEON_IDS)},
            'free_gem_time': (NOW + 500) * 1000}}}
    session.publish = Mock()
    return session


class MemoryCooldownTests(unittest.TestCase):
    def test_milliseconds_expiry_and_buffer_separate_from_display(self):
        self.assertEqual(deadline((NOW+25)*1000, NOW, 7205)['remaining_seconds'], 25)
        self.assertEqual(deadline((NOW-10)*1000, NOW, 7205)['status'], 'ready')
        self.assertEqual(deadline(0, NOW, 7205)['status'], 'ready')
        self.assertEqual(deadline(NOW*1000, NOW, 7205)['status'], 'cooling')

    def test_unknown_never_means_ready(self):
        for raw in [None, True, False, '123', float('nan'), float('inf'), -1, NOW, (NOW+8000)*1000]:
            self.assertEqual(deadline(raw, NOW, 7205)['status'], 'unknown', raw)

    def test_locked_missing_and_malformed_dungeons_not_selected(self):
        values = summarize({'dungeons': {'shadowy_abyss': {'refresh_time': 0, 'has_unlocked': False}}}, NOW)
        self.assertEqual(values['dungeons']['dungeon_2']['status'], 'unknown')
        self.assertEqual(values['diamonds']['status'], 'unknown')
        self.assertEqual(values['dungeons']['dungeon_6']['game_id'], 'frozen_caverns')

    def test_traversal_only_decodes_allowlisted_scalars(self):
        import struct
        def dic(n): return (0, struct.pack('<IIQQ', 27, 0, n, 0))
        def val(n): return (0, struct.pack('<IIdQ', 3, 0, n, 0))
        g = Mock()
        maps = {1: {'dungeons': dic(2)}, 2: {'shadowy_abyss': dic(3)},
                3: {'refresh_time': val(NOW*1000), 'account': dic(999)},
                4: {'free_gem_time': val(NOW*1000), 'purchases': dic(999)}}
        g.dictionary_entries.side_effect = maps.__getitem__
        g.variant.side_effect = lambda raw: struct.unpack_from('<d', raw, 8)[0]
        result = read_fields(g, {'dungeons': dic(1), 'store': dic(4), 'client_token': dic(999)})
        self.assertEqual(result['free_gem_time'], NOW*1000)
        self.assertEqual(g.variant.call_count, 2)
        self.assertNotIn(999, [c.args[0] for c in g.dictionary_entries.call_args_list])

    def test_read_failure_retries_instead_of_partial_data(self):
        import struct
        g = Mock(); g.dictionary_entries.side_effect = MemoryReadError('changed')
        with self.assertRaises(MemoryReadError):
            read_fields(g, {'dungeons': (0, struct.pack('<IIQQ', 27, 0, 1, 0))})

    def batch(self, selected=(2, 3)):
        session = make_session(); plan = load_plan()
        for task in plan['tasks']: task['enabled'] = task['order'] in selected
        plan['diamonds']['enabled'] = False
        return Batch(session, plan, Mock(), clock=lambda: NOW)

    def test_memory_scan_needs_no_capture_navigation_or_swipe(self):
        batch = self.batch()
        readings = batch.scan_cooldowns()
        self.assertEqual(set(readings), {'dungeon_2', 'dungeon_3'})
        self.assertIsInstance(readings['dungeon_2'], MemoryCooldown)
        batch.factory.assert_not_called()

    def test_stale_memory_cannot_update_schedule(self):
        batch = self.batch()
        batch.session.snapshot['captured_monotonic'] -= 4
        with self.assertRaises(MemoryReadError): batch.scan_cooldowns()
        batch.factory.assert_not_called()

    def test_whitelist_and_explicit_buffer(self):
        batch = self.batch()
        scheduler = MemoryScheduler(batch.plan, Mock(), Mock(), threading.Event(), Mock(), clock=lambda: NOW)
        scheduler.apply_list_readings({'dungeon_2': MemoryCooldown(NOW+27),
                                       'dungeon_1': MemoryCooldown(0)})
        self.assertEqual(scheduler.ready_at['dungeon_2'], NOW+30)
        self.assertNotIn('dungeon_1', scheduler.ready_at)
        self.assertIsNone(scheduler.choose())

    def test_diamond_memory_overrides_estimate_once_not_each_scan(self):
        batch = self.batch(); batch.plan['diamonds']['enabled'] = True
        batch.scheduler = SimpleNamespace(diamond_due=0, checkpoint=Mock())
        batch.sync_diamond_deadline()
        self.assertEqual(batch.scheduler.diamond_due, NOW+503)
        batch.scheduler.diamond_due = NOW+7200  # freshly validated OCR after claim
        batch.sync_diamond_deadline()
        self.assertEqual(batch.scheduler.diamond_due, NOW+7200)
        batch.session.snapshot['data']['cooldown_fields']['free_gem_time'] = (NOW+7198)*1000
        batch.sync_diamond_deadline()
        self.assertEqual(batch.scheduler.diamond_due, NOW+7201)

    def test_preview_with_only_second_selected_has_no_scheduler(self):
        batch = self.batch((2,)); batch.dry_run = True
        batch.execute = Mock(return_value=SimpleNamespace(reason='preview'))
        with patch('bk2.automation.MemoryScheduler') as scheduler:
            result = batch.run(); scheduler.assert_not_called()
        self.assertTrue(result['preview'])
        self.assertEqual(batch.execute.call_args.args[0]['profile']['id'], 'dungeon_2')

    def test_deferred_dungeon_not_a_completion(self):
        batch = self.batch()
        batch.execute = Mock(return_value=SimpleNamespace(deferred=True, cooldown_estimate=None, reason='cooling'))
        value, reason = batch.dungeon(batch.plan['tasks'][1])
        self.assertIsNone(value)
        self.assertEqual(batch.session.ledger.finished_transitions, 0)

    def test_auto_diamond_typed_outcome_and_preview_rejection(self):
        batch = self.batch()
        estimate = CooldownEstimate(time.monotonic()+200, '00:03:17', 1)
        batch.execute = Mock(return_value=SimpleNamespace(completed=True, cooldown_estimate=estimate,
            counters={'diamond_rewards': 1}, manual_handoff=False, reason='done'))
        result, _ = batch.diamonds()
        self.assertIsInstance(result, DiamondResult)
        self.assertTrue(result.collected)
        batch.dry_run = True
        self.assertIs(batch.diamonds()[0], False)

    def test_completion_requires_timer_and_confirmed_home(self):
        batch = self.batch()
        for complete, estimate in [(True, None), (False, CooldownEstimate(999, '00:00:03', 1))]:
            batch.execute = Mock(return_value=SimpleNamespace(completed=complete, cooldown_estimate=estimate,
                counters={}, manual_handoff=False, reason='unconfirmed'))
            self.assertIs(batch.diamonds()[0], False)


class DiagnosticTests(unittest.TestCase):
    def test_exception_frame_and_redaction_are_durable_and_unique(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            try: raise ValueError('example failure')
            except ValueError as exc:
                first = save_failure(root, 'test', 'worker', exc,
                    {'status': 'ok', 'client_token': 'not-for-report'},
                    {'plan': {'password': 'not-for-report'}},
                    image=np.zeros((4, 5, 3), dtype=np.uint8), image_at=time.monotonic())
            second = save_failure(root, 'test', 'worker', 'another')
            self.assertNotEqual(first, second)
            report = json.loads(first.read_text(encoding='utf-8'))
            self.assertIn('ValueError: example failure', report['traceback'])
            self.assertNotIn('not-for-report', first.read_text(encoding='utf-8'))
            self.assertTrue((first.parent/'screen.png').is_file())
            self.assertIn('screenshot_unavailable', json.loads(second.read_text()))
            self.assertEqual((root/'logs/diagnostics/latest.txt').read_text(), str(second))

    def test_session_errors_and_memory_startup_failure_saved(self):
        with tempfile.TemporaryDirectory() as d, patch('bk2.runtime.ROOT', Path(d)):
            session = Session()
            session.last_valid_snapshot = {'status': 'ok', 'pid': 42}
            with patch('bk2.runtime.verify_build', side_effect=RuntimeError('build changed')):
                session._observe()
            report = json.loads(session.last_diagnostic.read_text(encoding='utf-8'))
            self.assertEqual(report['last_valid_memory']['pid'], 42)
            self.assertIn('build changed', report['traceback'])
            self.assertTrue(session.operation_stop.is_set())
            self.assertEqual(session.snapshot['status'], 'unknown')
            self.assertIn('memory_error', session.log_path.read_text(encoding='utf-8'))

    def test_diagnostic_write_failure_keeps_original_error_visible(self):
        with tempfile.TemporaryDirectory() as d, patch('bk2.runtime.ROOT', Path(d)):
            emit = Mock(); session = Session(emit)
            with patch('bk2.diagnostics.save_failure', side_effect=OSError('disk full')):
                session.publish('error', 'original error')
            self.assertIn(('error', 'original error'), [c.args for c in emit.call_args_list])
            self.assertTrue(any(c.args[0]=='diagnostic_save_error' for c in emit.call_args_list))

    def test_event_log_failure_stops_input_and_still_reports_original_error(self):
        with tempfile.TemporaryDirectory() as d, patch('bk2.runtime.ROOT', Path(d)):
            emit = Mock(); session = Session(emit)
            session.log_path = Path(d)  # directory cannot be opened as a JSONL file
            session.publish('error', 'original remains visible')
            self.assertTrue(session.operation_stop.is_set())
            self.assertIn(('error', 'original remains visible'), [c.args for c in emit.call_args_list])
            self.assertTrue(session.last_diagnostic.is_file())

    def test_gui_worker_error_uses_session_diagnostics(self):
        from bk2.gui import App
        with tempfile.TemporaryDirectory() as d, patch('bk2.runtime.ROOT', Path(d)):
            app = App.__new__(App); app.session = Session()
            try: raise RuntimeError('GUI worker failure')
            except RuntimeError as exc: app.report_error(exc)
            report = json.loads(app.session.last_diagnostic.read_text(encoding='utf-8'))
            self.assertIn('GUI worker failure', report['traceback'])


if __name__ == '__main__': unittest.main()
