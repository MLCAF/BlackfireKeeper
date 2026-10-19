"""Release identity reaches user-visible entry points without game operations."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from bk2 import __version__
from bk2.version import DISPLAY_NAME, VERSION, VERSION_TAG, version_info


class VersionIntegrationTests(unittest.TestCase):
    def test_cli_version_exits_without_session_or_game_check(self):
        from bk2.__main__ import main
        output = io.StringIO()
        with patch('sys.argv', ['bk2', '--version']), \
                patch('bk2.__main__.Session') as session, \
                patch('bk2.runtime.verify_build') as verify, \
                patch('bk2.__main__.load_plan') as plan, \
                contextlib.redirect_stdout(output):
            with self.assertRaises(SystemExit) as exited:
                main()
        self.assertEqual(exited.exception.code, 0)
        self.assertEqual(output.getvalue().strip(), DISPLAY_NAME)
        session.assert_not_called()
        verify.assert_not_called()
        plan.assert_not_called()
        self.assertEqual(__version__, VERSION)
        self.assertEqual(VERSION_TAG, f'v{__version__}')

    def test_check_json_includes_application_and_game_build_separately(self):
        from bk2.__main__ import main
        output = io.StringIO()
        with patch('sys.argv', ['bk2', 'check']), \
                patch('bk2.runtime.verify_build', return_value='game-build-hash'), \
                patch('bk2.__main__.load_plan', return_value={
                    'scheduler': {}, 'tasks': [{'enabled': True}]}), \
                patch('bk2.__main__.Session') as session, \
                contextlib.redirect_stdout(output):
            main()
        report = json.loads(output.getvalue())
        self.assertEqual(report['application'], version_info())
        self.assertEqual(report['build_sha256'], 'game-build-hash')
        session.assert_not_called()

    def test_failure_report_preserves_schema_and_includes_app_version(self):
        from bk2.diagnostics import save_failure
        with tempfile.TemporaryDirectory() as folder:
            target = save_failure(Path(folder), 'version-test', 'test', 'example')
            report = json.loads(target.read_text(encoding='utf-8'))
        self.assertEqual(report['version'], 1)
        self.assertEqual(report['application'], version_info())

    def test_backend_report_adds_identity_without_losing_evidence(self):
        from bk2.backend import DiagnosticBackend
        backend = DiagnosticBackend.__new__(DiagnosticBackend)
        with tempfile.TemporaryDirectory() as folder:
            backend.diagnostic_root = Path(folder)
            backend.recognition_trace = [1]
            result = backend.save_diagnostic('Entry', 'timeout', {})
            report = json.loads(Path(result).read_text(encoding='utf-8'))
        self.assertEqual(report['application'], version_info())
        self.assertEqual(report['recognition'], [1])
        self.assertEqual(report['reason'], 'timeout')

    def test_dashboard_title_and_brand_use_same_release(self):
        import tkinter as tk
        from bk2.gui import App
        root = tk.Tk()
        root.withdraw()
        try:
            with patch('bk2.gui.Session') as session:
                app = App(root)
                root.update_idletasks()
                self.assertIn(DISPLAY_NAME, root.title())
                def labels(widget):
                    for child in widget.winfo_children():
                        if isinstance(child, tk.Label):
                            yield child.cget('text')
                        yield from labels(child)
                self.assertIn(DISPLAY_NAME, list(labels(root)))
                self.assertIsNone(app.session)
                session.assert_not_called()
        finally:
            for callback in root.tk.call('after', 'info'):
                root.after_cancel(callback)
            root.destroy()
