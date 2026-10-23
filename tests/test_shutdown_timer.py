import base64
import datetime as dt
import json
import os
import threading
import tkinter as tk
import unittest
from tkinter import ttk
from types import SimpleNamespace
from unittest.mock import Mock, patch
from bk2.shutdown_timer import WindowsShutdownSchedule, TASK_SCRIPT, deadline


class ShutdownTests(unittest.TestCase):
    def test_validation_and_next_day(self):
        now=dt.datetime(2026,10,23,23,31).timestamp()
        self.assertEqual(dt.datetime.fromtimestamp(deadline('指定时间（HH:MM）','23:30',now)),dt.datetime(2026,10,24,23,30))
        self.assertEqual(deadline('延时（分钟）','120',now),now+7200)
        for mode,value in [('延时（分钟）','0'),('延时（分钟）','10081'),('延时（分钟）','nan'),('延时（分钟）','1.5'),('指定时间（HH:MM）','24:00')]:
            with self.assertRaises(ValueError):deadline(mode,value,now)

    @unittest.skipUnless(os.name=='nt','Windows task contract')
    def test_registers_system_task_through_fixed_code_and_separate_json(self):
        run=Mock(return_value=SimpleNamespace(returncode=0,stdout=b'{"ok":true,"registered":true}'))
        service=WindowsShutdownSchedule(run)
        with patch('bk2.shutdown_timer.time.time',return_value=1000):service.arm('延时（分钟）','1')
        args=run.call_args.args[0]
        self.assertEqual(base64.b64decode(args[-1]).decode('utf-16le'),TASK_SCRIPT)
        self.assertEqual(json.loads(run.call_args.kwargs['input'])['operation'],'arm')
        self.assertNotIn('shell',run.call_args.kwargs)
        self.assertIn('Register-ScheduledTask',TASK_SCRIPT)
        self.assertIn("'/s /f /t 0'",TASK_SCRIPT)
        self.assertIn('$task.Description -ne $marker',TASK_SCRIPT)
        self.assertNotIn('Stop-Process',TASK_SCRIPT)

    @unittest.skipUnless(os.name=='nt','Windows task contract')
    def test_errors_are_not_reported_as_armed_or_retried(self):
        for code,payload in [(1,b'{"ok":false,"error":"denied"}'),(0,b'invalid'),(0,b'{"ok":true,"registered":false}')]:
            run=Mock(return_value=SimpleNamespace(returncode=code,stdout=payload))
            with self.assertRaises(RuntimeError):WindowsShutdownSchedule(run).arm('延时（分钟）','1')
            run.assert_called_once()

    @unittest.skipUnless(os.name=='nt','Windows task contract')
    def test_query_and_explicit_cancel_use_distinct_operations(self):
        run=Mock(return_value=SimpleNamespace(returncode=0,stdout=b'{"ok":true,"registered":false}'))
        service=WindowsShutdownSchedule(run);service.query();service.cancel()
        self.assertEqual([json.loads(c.kwargs['input'])['operation'] for c in run.call_args_list],['query','cancel'])

    @unittest.skipUnless(os.name=='nt','Windows task contract')
    def test_access_denied_requests_elevation_only_for_mutations(self):
        error=b'{"ok":false,"error":"Access denied","owner_sid":"S-1-5-21-500"}'
        for op in ('arm','cancel','query'):
            run=Mock(return_value=SimpleNamespace(returncode=1,stdout=error))
            service=WindowsShutdownSchedule(run)
            service.elevated=Mock(return_value={'ok':True,'registered':op=='arm'})
            if op=='query':
                with self.assertRaises(RuntimeError):service.execute(op)
                service.elevated.assert_not_called()
            else:
                service.execute(op,2000000000 if op=='arm' else None)
                service.elevated.assert_called_once()
                self.assertEqual(service.elevated.call_args.args[1]['owner_sid'],'S-1-5-21-500')


class ShutdownUITests(unittest.TestCase):
    def test_os_plan_survives_stop_close_and_new_panel_until_explicit_cancel(self):
        from bk2.shutdown_panel import ShutdownPanel
        from bk2.gui import App
        stored={'ok':True,'registered':False}
        service=Mock()
        service.query.side_effect=lambda:dict(stored)
        def arm(*args):
            stored.update(registered=True,at='2099-10-23T23:30:00',state='Ready')
            return dict(stored)
        def cancel():stored.update(registered=False);return dict(stored)
        service.arm.side_effect=arm;service.cancel.side_effect=cancel
        root=tk.Tk();root.withdraw()
        app=App.__new__(App);app.root=root;app.tabs=ttk.Notebook(root);app.closing=False
        app.emit=Mock();app.session=Mock();app.ce_panel=None;app.status=Mock();app.preparation_stop=threading.Event()
        try:
            with patch('bk2.shutdown_panel.threading.Thread',side_effect=lambda **kw:SimpleNamespace(start=kw['target'])):
                app.shutdown_panel=ShutdownPanel(app,service);panel=app.shutdown_panel;panel.refresh()
                panel.arm_button.invoke();panel.refresh();self.assertTrue(panel.record['registered'])
                App.stop_run(app);App.close(app);service.cancel.assert_not_called()
                self.assertTrue(stored['registered'])
                app.closing=False
                reopened=ShutdownPanel(app,service);reopened.refresh()
                self.assertTrue(reopened.record['registered'])
                reopened.cancel_button.invoke();reopened.refresh()
                service.cancel.assert_called_once();self.assertFalse(stored['registered'])
        finally:root.destroy()

    def test_gui_constructs_without_registering_or_cancelling_task(self):
        from bk2.gui import App
        service=Mock();service.query.return_value={'ok':True,'registered':False}
        root=tk.Tk();root.withdraw()
        with patch('bk2.shutdown_panel.WindowsShutdownSchedule',return_value=service):
            app=App(root)
            try:
                root.update_idletasks();app.stop_run();app.close()
                service.arm.assert_not_called();service.cancel.assert_not_called()
            finally:
                if getattr(app,'ce_panel',None):app.ce_panel.close()
                root.destroy()

    def test_registration_failure_shown_without_changing_previous_record(self):
        from bk2.shutdown_panel import ShutdownPanel
        service=Mock();service.query.return_value={'ok':True,'registered':False}
        service.arm.side_effect=RuntimeError('permission denied')
        root=tk.Tk();root.withdraw()
        app=SimpleNamespace(tabs=ttk.Notebook(root),closing=False,emit=Mock())
        try:
            with patch('bk2.shutdown_panel.threading.Thread',side_effect=lambda **kw:SimpleNamespace(start=kw['target'])):
                panel=ShutdownPanel(app,service);panel.refresh();panel.arm_button.invoke();panel.refresh()
                self.assertIn('permission denied',panel.status.get())
                self.assertFalse(panel.record['registered']);self.assertFalse(panel.blocking)
        finally:root.destroy()
