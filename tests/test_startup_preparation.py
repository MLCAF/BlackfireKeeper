"""Startup is optional preparation; operation-wide input ownership stays strict."""
from contextlib import nullcontext
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from bk2.automation import InputLeaseBusy, input_lease
from bk2.gui import App
from bk2.diagnostics import save_failure


class StartupPreparationTests(unittest.TestCase):
    def app(self):
        return SimpleNamespace(preparation_stop=Mock(),emit=Mock(),report_error=Mock())

    def test_occupied_lease_skips_startup_without_window_operation_or_error(self):
        app=self.app()
        with patch('bk2.automation.input_lease',side_effect=InputLeaseBusy('busy')), \
                patch('bk2.window_geometry.standardize_open_game') as prepare:
            App.prepare_startup(app)
        prepare.assert_not_called();app.report_error.assert_not_called()
        self.assertEqual(app.emit.call_args.args[0],'log')
        self.assertIn('跳过',app.emit.call_args.args[1])

    def test_unrelated_failure_is_still_reported(self):
        app=self.app();error=OSError('disk failure')
        with patch('bk2.automation.input_lease',side_effect=error):App.prepare_startup(app)
        app.report_error.assert_called_once_with(error)

    def test_success_and_cancel_release_the_lease(self):
        for error in (None,InterruptedError()):
            app=self.app()
            with patch('bk2.automation.input_lease',return_value=nullcontext()), \
                    patch('bk2.window_geometry.standardize_open_game',side_effect=error) as prepare:
                App.prepare_startup(app)
            prepare.assert_called_once();app.report_error.assert_not_called()

    def test_actual_input_lease_still_rejects_second_owner(self):
        with tempfile.TemporaryDirectory() as folder,patch('bk2.automation.ROOT',Path(folder)):
            with input_lease():
                with self.assertRaises(InputLeaseBusy):
                    with input_lease():self.fail('Second owner must never enter')
            with input_lease():pass

    def test_geometry_error_details_survive_diagnostic_serialization(self):
        error=RuntimeError('restore timeout')
        error.window_geometry={'target_client_size':[1920,991],'status':'failed',
                               'restore_requests':1,'observations':[{'minimized':True}]}
        with tempfile.TemporaryDirectory() as folder:
            result=save_failure(Path(folder),'test','gui',error,context={'existing':'kept'})
            report=json.loads(result.read_text(encoding='utf-8'))
        self.assertEqual(report['context']['existing'],'kept')
        self.assertEqual(report['context']['window_geometry'],error.window_geometry)
