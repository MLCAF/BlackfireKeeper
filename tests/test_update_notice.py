import unittest
from unittest.mock import Mock, patch
from bk2.update_notice import show_update_notice, UPDATE_TEXT
from bk2.version import DISPLAY_NAME


class UpdateNoticeTests(unittest.TestCase):
    def test_every_call_shows_current_version_without_read_once_flag(self):
        parent=Mock()
        with patch('bk2.update_notice.messagebox.showinfo') as show:
            show_update_notice(parent);show_update_notice(parent)
        self.assertEqual(show.call_count,2)
        show.assert_called_with(f'{DISPLAY_NAME} · 本次更新',UPDATE_TEXT,parent=parent)
        self.assertIn('Windows 系统任务',UPDATE_TEXT)
        self.assertIn('定时关机',UPDATE_TEXT)

    def test_gui_launch_shows_notice_before_window_preparation(self):
        from bk2.gui import launch
        events=[];app=Mock(closing=False)
        app.prepare_startup.side_effect=lambda:events.append('prepare')
        with patch('bk2.gui.tk.Tk') as root, patch('bk2.gui.App',return_value=app), \
             patch('bk2.update_notice.show_update_notice',side_effect=lambda _:events.append('notice')) as notice, \
             patch('bk2.gui.threading.Thread') as thread:
            thread.return_value.start.side_effect=lambda:events.append('thread')
            launch();launch()
        self.assertEqual(events,['notice','thread','notice','thread'])
        self.assertEqual(notice.call_count,2)
