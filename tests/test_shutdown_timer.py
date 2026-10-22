import datetime as dt
import os
import threading
import tkinter as tk
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from bk2.shutdown_timer import ShutdownTimer, deadline, request_shutdown


class ShutdownTests(unittest.TestCase):
    def timer(self):
        wall, mono = [1000.0], [100.0]
        stop, idle, submit = Mock(), Mock(return_value=True), Mock()
        timer = ShutdownTimer(stop, idle, submit, lambda: wall[0], lambda: mono[0])
        return timer, wall, mono, stop, idle, submit

    def test_validation_and_next_day(self):
        now = dt.datetime(2026, 10, 23, 23, 31).timestamp()
        self.assertEqual(dt.datetime.fromtimestamp(deadline('指定时间（HH:MM）', '23:30', now)), dt.datetime(2026,10,24,23,30))
        self.assertEqual(deadline('延时（分钟）', '120', now), now+7200)
        for mode, value in [('延时（分钟）','0'),('延时（分钟）','10081'),('延时（分钟）','nan'),('延时（分钟）','1.5'),('指定时间（HH:MM）','24:00'),('指定时间（HH:MM）','1:5')]:
            with self.assertRaises(ValueError):deadline(mode,value,now)

    def test_due_stops_once_grace_uses_monotonic_and_submits_once(self):
        t,w,m,stop,idle,submit=self.timer()
        t.arm('延时（分钟）','1');t.tick();stop.assert_not_called()
        w[0]+=60;t.tick();stop.assert_called_once();self.assertTrue(t.blocking)
        w[0]+=86400;t.tick();submit.assert_not_called()
        m[0]+=60;t.tick();t.tick();submit.assert_called_once()
        t.submitted();self.assertEqual(t.state,'submitted')
        self.assertFalse(t.cancel())

    def test_cancel_before_or_during_grace_and_restart_is_off(self):
        for grace in (False,True):
            t,w,m,stop,idle,submit=self.timer();t.arm('延时（分钟）','1')
            if grace:w[0]+=60;t.tick()
            self.assertTrue(t.cancel());w[0]+=10000;m[0]+=10000;t.tick()
            submit.assert_not_called();self.assertFalse(t.blocking)
        self.assertEqual(self.timer()[0].state,'off')

    def test_invalid_replacement_keeps_previous_schedule(self):
        t,*_=self.timer();t.arm('延时（分钟）','2');target=t.target
        with self.assertRaises(ValueError):t.arm('延时（分钟）','bad')
        self.assertEqual(t.target,target);self.assertEqual(t.state,'armed')

    def test_worker_busy_or_stop_failure_prevents_os_call(self):
        for fail_stop in (False,True):
            t,w,m,stop,idle,submit=self.timer()
            if fail_stop:stop.side_effect=RuntimeError('stop failed')
            else:idle.return_value=False
            t.arm('延时（分钟）','1');w[0]+=60;t.tick();m[0]+=60;t.tick()
            self.assertEqual(t.state,'failed');submit.assert_not_called()

    def test_submission_failure_not_retried(self):
        t,w,m,stop,idle,submit=self.timer()
        t.arm('延时（分钟）','1');w[0]+=60;t.tick();m[0]+=60;t.tick()
        t.submitted(OSError('denied'));t.tick()
        self.assertEqual(t.state,'failed');submit.assert_called_once()

    @unittest.skipUnless(os.name=='nt','Windows command contract')
    def test_windows_command_never_force_closes_or_creates_os_countdown(self):
        with patch('bk2.shutdown_timer.subprocess.run',return_value=SimpleNamespace(returncode=0)) as run:
            request_shutdown()
        args=run.call_args.args[0]
        self.assertTrue(args[0].lower().endswith('system32\\shutdown.exe'))
        self.assertEqual(args[1:],['/s','/t','0'])
        self.assertNotIn('/f',args);self.assertNotIn('shell',run.call_args.kwargs)
        with patch('bk2.shutdown_timer.subprocess.run',return_value=SimpleNamespace(returncode=5,stderr=b'denied',stdout=b'')):
            with self.assertRaises(RuntimeError):request_shutdown()


class ShutdownUITests(unittest.TestCase):
    def test_real_panel_due_submission_and_failure_are_mocked(self):
        from bk2.shutdown_panel import ShutdownPanel
        from tkinter import ttk
        for failure in (None, OSError('permission denied')):
            root=tk.Tk();root.withdraw()
            try:
                app=SimpleNamespace(root=root,tabs=ttk.Notebook(root),closing=False,
                    worker=None,preparation_thread=None,preparation_stop=threading.Event(),
                    stop_run=Mock(),emit=Mock())
                submit=Mock(side_effect=failure)
                panel=ShutdownPanel(app,submit=submit)
                wall,mono=[1000.],[100.]
                panel.timer.clock=lambda:wall[0];panel.timer.monotonic=lambda:mono[0]
                panel.arm_button.invoke();wall[0]+=7200;panel.refresh()
                app.stop_run.assert_called_once_with(cancel_shutdown=False)
                self.assertTrue(panel.blocking);submit.assert_not_called()
                mono[0]+=60
                with patch('bk2.shutdown_panel.threading.Thread',side_effect=lambda **kw:SimpleNamespace(start=kw['target'])):
                    panel.refresh()
                panel.refresh();submit.assert_called_once()
                self.assertEqual(panel.timer.state,'failed' if failure else 'submitted')
            finally:root.destroy()

    @unittest.skipUnless(os.name=='nt','Windows global F8')
    def test_f8_cancels_before_submitting(self):
        from bk2.shutdown_panel import ShutdownPanel
        panel=ShutdownPanel.__new__(ShutdownPanel)
        panel.timer=self.make_armed_timer()
        panel.app=SimpleNamespace(stop_run=Mock(side_effect=panel.timer.cancel))
        with patch('ctypes.windll.user32.GetAsyncKeyState',return_value=0x8000):
            panel.refresh()
        self.assertEqual(panel.timer.state,'off')

    @staticmethod
    def make_armed_timer():
        timer=ShutdownTimer(Mock(),Mock(),Mock());timer.arm('延时（分钟）','120')
        return timer

    def test_panel_buttons_close_stop_and_no_real_shutdown(self):
        from bk2.gui import App
        # Construct the real UI, but do not call launch(), connect(), or game IO.
        root=tk.Tk();root.withdraw()
        with patch('bk2.shutdown_panel.request_shutdown') as os_call:
            app=App(root)
            try:
                panel=app.shutdown_panel
                root.update_idletasks()
                self.assertEqual(panel.timer.state,'off')
                panel.arm_button.invoke();self.assertEqual(panel.timer.state,'armed')
                panel.cancel_button.invoke();self.assertEqual(panel.timer.state,'off')
                panel.arm_button.invoke();app.stop_run();self.assertEqual(panel.timer.state,'off')
                panel.arm_button.invoke();app.close();self.assertEqual(panel.timer.state,'off')
                os_call.assert_not_called()
            finally:
                if getattr(app,'ce_panel',None):app.ce_panel.close()
                root.destroy()

    def test_countdown_blocks_new_game_operations(self):
        from bk2.gui import App
        app=App.__new__(App);app.shutdown_panel=SimpleNamespace(blocking=True)
        self.assertIn('取消',app.start_block_reason())
        # Early return must not touch a game/session or instantiate connectors.
        app.connect();app.start(False)

    def test_stop_and_close_cancel_timer_without_saving_plan(self):
        from bk2.gui import App
        app=App.__new__(App);app.shutdown_panel=Mock();app.ce_panel=None
        app.session=Mock();app.status=Mock();app.preparation_stop=threading.Event()
        app.stop_run();app.shutdown_panel.cancel.assert_called_once()
        app.shutdown_panel.reset_mock();app.stop_run(cancel_shutdown=False)
        app.shutdown_panel.cancel.assert_not_called()
        app.close();app.shutdown_panel.cancel.assert_called_once()
