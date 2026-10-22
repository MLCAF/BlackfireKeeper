"""Explicit window selection, state isolation and UI apply boundaries."""
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
from contextlib import nullcontext
from types import SimpleNamespace
from bk2.window_geometry import standardize
from bk2.window_options import load_resolution,save_resolution,validate_size
from bk2.window_panel import WindowPanel
from tests.test_window_geometry import Surface

class ResolutionTests(unittest.TestCase):
    def test_manual_choice_overrides_standard_and_auto_reselects(self):
        a=Surface(size=(1920,991));stop=threading.Event()
        for size in [(1600,826),(1440,743),(1280,661)]:
            r=standardize(a,None,stop,preferred=size)
            self.assertEqual(tuple(r['actual_client_size']),size)
            self.assertEqual(r['size_policy'],'manual')
        self.assertEqual(standardize(a,None,stop)['actual_client_size'],[1920,991])
    def test_unfittable_manual_size_does_not_resize_or_silently_downgrade(self):
        a=Surface(size=(1280,661),work=(0,0,1366,728))
        with self.assertRaisesRegex(RuntimeError,'请选择更小'):standardize(a,None,threading.Event(),preferred=(1600,826))
        self.assertEqual(a.requests,[])
    def test_invalid_size_and_missing_preference(self):
        for value in [(1024,768),[True,991],'1920x991']:
            with self.assertRaises(ValueError):validate_size(value)
        with tempfile.TemporaryDirectory() as root:self.assertIsNone(load_resolution(root))
    def test_save_is_independent_preserves_other_keys_and_plan(self):
        with tempfile.TemporaryDirectory() as root:
            root=Path(root);(root/'config/runtime').mkdir(parents=True)
            (root/'config/dungeon_plan.json').write_bytes(b'original-plan')
            (root/'config/runtime/food_schedule.json').write_bytes(b'original-cooldown')
            (root/'config/runtime/window_options.json').write_text('{"other": 7}')
            save_resolution(root,(1440,743));self.assertEqual(load_resolution(root),(1440,743))
            self.assertEqual(json.loads((root/'config/runtime/window_options.json').read_text())['other'],7)
            self.assertEqual((root/'config/dungeon_plan.json').read_bytes(),b'original-plan')
            self.assertEqual((root/'config/runtime/food_schedule.json').read_bytes(),b'original-cooldown')
            save_resolution(root,None);self.assertIsNone(load_resolution(root))
    def panel(self):
        app=Mock(worker=None,preparation_thread=None,closing=False)
        app.shutdown_blocked.return_value=False
        app.preparation_stop=threading.Event()
        panel=WindowPanel.__new__(WindowPanel);panel.app=app;panel.value=Mock()
        panel.value.get.return_value='1600×826'
        return panel
    def test_apply_saves_only_after_success_under_input_lease(self):
        panel=self.panel()
        def thread(**kw):return SimpleNamespace(start=kw['target'])
        with patch('bk2.window_panel.threading.Thread',side_effect=thread),patch('bk2.automation.input_lease',return_value=nullcontext()) as lease,patch('bk2.window_geometry.standardize_open_game',return_value={'actual_client_size':[1600,826]}) as resize,patch('bk2.window_panel.save_resolution') as save:
            panel.apply();lease.assert_called_once();resize.assert_called_once()
            self.assertEqual(resize.call_args.kwargs['preferred'],(1600,826))
            self.assertEqual(save.call_args.args[1],(1600,826))
        panel=self.panel()
        with patch('bk2.window_panel.threading.Thread',side_effect=thread),patch('bk2.automation.input_lease',return_value=nullcontext()),patch('bk2.window_geometry.standardize_open_game',side_effect=RuntimeError('too large')),patch('bk2.window_panel.save_resolution') as save:
            panel.apply();save.assert_not_called();panel.app.report_error.assert_called_once()
    def test_busy_apply_does_not_start_thread(self):
        panel=self.panel();panel.app.worker=Mock();panel.app.worker.is_alive.return_value=True
        with patch('bk2.window_panel.threading.Thread') as thread:panel.apply();thread.assert_not_called()
