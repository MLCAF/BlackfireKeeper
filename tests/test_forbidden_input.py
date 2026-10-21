"""Entry points retain background forbidden input without changing dungeon plans."""
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace
from bk2.forbidden_panel import ForbiddenPanel
from bk2.forbidden import ForbiddenOptions
from bk2.__main__ import main

class BackgroundEntryTests(unittest.TestCase):
    def test_gui_uses_background_input_without_reading_dungeon_preference(self):
        app=Mock(worker=None,closing=False)
        app.session.error=None
        app.capture.get.return_value='ScreenDC'
        panel=ForbiddenPanel.__new__(ForbiddenPanel)
        panel.app=app;panel.progress=Mock()
        panel.options=lambda:ForbiddenOptions(2,30)
        def thread(**kwargs):
            return SimpleNamespace(start=kwargs['target'])
        with patch('bk2.automation.boundary_ready'), patch('bk2.forbidden_panel.threading.Thread',side_effect=thread), patch('bk2.forbidden_panel.run_forbidden') as run:
            panel.start(False)
        run.assert_called_once_with(app.session,ForbiddenOptions(2,30),False,'FramePool','SendMessage')
        app.input_method.get.assert_not_called()
        app.report_error.assert_not_called()

    def test_screen_dc_is_rejected_before_session_or_input(self):
        from bk2.forbidden import run_forbidden
        session=Mock()
        with self.assertRaisesRegex(ValueError,'FramePool'):
            run_forbidden(session,ForbiddenOptions(),capture='ScreenDC')
        session.wait_ready.assert_not_called()

    def test_cli_default_and_explicit_override(self):
        for extra,expected in [([], 'SendMessage'),(['--input','Seize'],'Seize')]:
            with self.subTest(extra=extra), patch('sys.argv',['bk2','forbidden',*extra]), patch('bk2.__main__.Session'), patch('bk2.forbidden.run_forbidden',return_value={'succeeded':True}) as run:
                main()
                self.assertEqual(run.call_args.args[-1],expected)


class HeldBackgroundClickTests(unittest.TestCase):
    def backend(self):
        import time
        from vendor_core.backend import MaaBackend
        b=MaaBackend.__new__(MaaBackend)
        b.guard=Mock();b.last_shape=(720,1395);b.last_capture=time.monotonic()
        b.input_method='SendMessage';b.click_hold_seconds=.15
        b.controller=Mock();b.win=Mock()
        for method in (b.controller.post_touch_down,b.controller.post_touch_up,b.controller.post_click):
            method.return_value=SimpleNamespace(done=True,succeeded=True)
        return b

    def test_background_hold_preserves_target_and_never_requests_foreground(self):
        import threading
        from vendor_core.backend import Match
        b=self.backend();stop=threading.Event()
        with patch.object(stop,'wait',return_value=False) as wait:
            b.click(Match((200,300,40,20),1),(0,-10),stop,threading.Event())
        b.controller.post_touch_down.assert_called_once_with(220,300)
        b.controller.post_touch_up.assert_called_once_with()
        b.controller.post_click.assert_not_called();b.win.foreground.assert_not_called()
        wait.assert_called_once_with(.15)

    def test_cancel_during_hold_releases_and_cancellation_before_down_sends_nothing(self):
        import threading
        from vendor_core.backend import Match
        b=self.backend();stop=threading.Event()
        def cancel(seconds):stop.set();return True
        with patch.object(stop,'wait',side_effect=cancel),self.assertRaises(InterruptedError):
            b.click(Match((1,2,4,4),1),(0,0),stop,threading.Event())
        b.controller.post_touch_up.assert_called_once_with()
        b.controller.reset_mock()
        with self.assertRaises(InterruptedError):b.click(Match((1,2,4,4),1),(0,0),stop,threading.Event())
        b.controller.post_touch_down.assert_not_called()

    def test_failed_down_ack_still_releases(self):
        import threading
        from vendor_core.backend import Match
        b=self.backend();b.controller.post_touch_down.return_value.succeeded=False
        with self.assertRaises(RuntimeError):b.click(Match((1,2,4,4),1),(0,0),threading.Event(),threading.Event())
        b.controller.post_touch_up.assert_called_once_with()

    def test_existing_dungeon_click_path_unchanged(self):
        import threading
        from vendor_core.backend import Match
        b=self.backend();del b.click_hold_seconds;b.input_method='PostMessage'
        b.click(Match((1,2,4,4),1),(0,0),threading.Event(),threading.Event())
        b.controller.post_click.assert_called_once_with(3,4)
        b.controller.post_touch_down.assert_not_called()
