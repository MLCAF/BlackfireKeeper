"""Only one realm is persisted; unsupported slots cannot enter automation."""
import json
import tempfile
import tkinter as tk
from tkinter import ttk
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from bk2.forbidden import ForbiddenOptions, FORBIDDEN_REALMS, ForbiddenFlow
from bk2.forbidden_panel import ForbiddenPanel


class ForbiddenSelectionTests(unittest.TestCase):
    def test_legacy_settings_keep_limits_and_default_realm(self):
        options=ForbiddenOptions(**{'rounds':37,'minimum_stamina':45}).validate()
        self.assertEqual((options.rounds,options.minimum_stamina,options.realm_id),(37,45,'golden_kingdom'))

    def test_pending_and_unknown_maps_fail_before_any_flow(self):
        for key in ['pending_2','pending_3','pending_4','pending_5','unknown']:
            with self.subTest(key=key),self.assertRaisesRegex(ValueError,'待支持'):
                ForbiddenFlow(ForbiddenOptions(realm_id=key),0)

    def test_real_widgets_share_one_variable_preserve_pending_state_and_save(self):
        with tempfile.TemporaryDirectory() as folder,patch('bk2.forbidden_panel.ROOT',Path(folder)):
            path=Path(folder)/'config/runtime/forbidden_options.json'
            path.parent.mkdir(parents=True)
            path.write_text('{"rounds":37,"minimum_stamina":45}',encoding='utf8')
            window=tk.Tk();window.withdraw()
            try:
                tabs=ttk.Notebook(window);tabs.add(tk.Frame(tabs),text='挂机');tabs.add(tk.Frame(tabs),text='日志')
                app=SimpleNamespace(tabs=tabs,worker=None,closing=False,emit=Mock(),report_error=Mock(),stop_run=Mock())
                panel=ForbiddenPanel(app)
                self.assertEqual(len(panel.realm_buttons),5)
                self.assertEqual({str(r['variable']) for r,_ in panel.realm_buttons},{str(panel.realm)})
                for busy in [True,False]:
                    panel.refresh(True,busy)
                    for radio,supported in panel.realm_buttons:
                        self.assertEqual(radio.instate(['disabled']),busy or not supported)
                for radio,supported in panel.realm_buttons:
                    if not supported:radio.invoke()
                self.assertEqual(panel.realm.get(),'golden_kingdom')
                panel.save();app.report_error.assert_not_called()
                self.assertEqual(json.loads(path.read_text(encoding='utf8')),dict(rounds=37,minimum_stamina=45,realm_id='golden_kingdom'))
            finally:window.destroy()
