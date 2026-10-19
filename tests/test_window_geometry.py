"""Geometry policy with no native game window or screenshot access."""
from contextlib import nullcontext
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
from bk2.window_geometry import standardize, STANDARD_CLIENT_SIZE


class WindowGeometryTests(unittest.TestCase):
    def setUp(self):
        self.now=0.;self.stop=Mock();self.stop.is_set.return_value=False
        self.stop.wait.side_effect=lambda duration:setattr(self,'now',self.now+duration)
        self.adapter=Mock();self.adapter.pixels.side_effect=nullcontext
        self.adapter.restored.return_value=True
        self.adapter.minimized.return_value=False
        self.adapter.outer_and_work.return_value=((100,100,1396,859),(0,0,2560,1400))
        self.window=SimpleNamespace(hwnd=1)

    def run_geometry(self):return standardize(self.adapter,self.window,self.stop,lambda:self.now)

    def test_correct_client_is_unchanged(self):
        self.adapter.size.return_value=STANDARD_CLIENT_SIZE
        report=self.run_geometry()
        self.assertEqual(report['status'],'already_standard')
        self.adapter.resize.assert_not_called()

    def test_accounts_for_frame_and_moves_inside_work_area(self):
        self.adapter.size.side_effect=[(1280,720),STANDARD_CLIENT_SIZE,STANDARD_CLIENT_SIZE]
        report=self.run_geometry()
        self.adapter.resize.assert_called_once_with(self.window,(100,100,1936,1030))
        self.assertEqual(report['actual_client_size'],[1920,991])

    def test_restore_before_resize(self):
        self.adapter.restored.side_effect=[False,False,True]
        self.adapter.minimized.side_effect=[True, True, False]
        self.adapter.size.return_value=STANDARD_CLIENT_SIZE
        self.run_geometry();self.adapter.restore.assert_called_once_with(self.window)

    def test_minimized_maximized_window_accepts_target_after_restore(self):
        self.adapter.minimized.side_effect=[True, False, False]
        self.adapter.restored.return_value=False
        self.adapter.size.return_value=STANDARD_CLIENT_SIZE
        result=self.run_geometry()
        self.assertEqual(result['status'],'restored_standard')
        self.assertEqual(len(result['observations']),2)
        self.adapter.restore.assert_called_once()
        self.adapter.resize.assert_not_called()
        self.adapter.maximize.assert_not_called()

    def test_wrong_maximized_size_after_unminimize_restores_once_more(self):
        self.adapter.minimized.side_effect=[True, False, False]
        self.adapter.restored.side_effect=[False, False, True]
        self.adapter.size.side_effect=[(1500,800),(1280,720),STANDARD_CLIENT_SIZE,STANDARD_CLIENT_SIZE]
        result=self.run_geometry()
        self.assertEqual(result['status'],'resized')
        self.assertEqual(self.adapter.restore.call_count,2)
        self.adapter.resize.assert_called_once()

    def test_restore_refused_is_bounded_and_attaches_diagnostic_evidence(self):
        self.adapter.minimized.return_value=True
        self.adapter.restored.return_value=False
        with self.assertRaisesRegex(RuntimeError,'普通窗口超时') as result:self.run_geometry()
        self.adapter.restore.assert_called_once()
        report=result.exception.window_geometry
        self.assertEqual(report['status'],'failed')
        self.assertEqual(report['phase'],'restore')
        self.assertTrue(report['observations'][-1]['minimized'])
        self.assertLess(self.now,3.2)

    def test_cancel_while_restoring_does_not_send_second_request(self):
        self.adapter.minimized.side_effect=[True,False]
        self.adapter.restored.return_value=False
        self.adapter.size.return_value=STANDARD_CLIENT_SIZE
        self.stop.wait.side_effect=lambda seconds:setattr(self.stop.is_set,'return_value',True)
        with self.assertRaises(InterruptedError):self.run_geometry()
        self.adapter.restore.assert_called_once()

    def test_one_target_frame_followed_by_wrong_size_is_not_success(self):
        self.adapter.minimized.side_effect=[True]+[False]*40
        self.adapter.restored.return_value=False
        self.adapter.size.side_effect=[STANDARD_CLIENT_SIZE]+[(1500,800)]*40
        with self.assertRaisesRegex(RuntimeError,'普通窗口超时'):self.run_geometry()
        self.assertEqual(self.adapter.restore.call_count,2)

    def test_small_screen_fails_without_resizing(self):
        self.adapter.size.return_value=(1280,720)
        self.adapter.outer_and_work.return_value=((0,0,1296,759),(0,0,1366,728))
        with self.assertRaisesRegex(RuntimeError,'容不下'):self.run_geometry()
        self.adapter.resize.assert_not_called()

    def test_nonzero_monitor_origin_is_respected(self):
        self.adapter.size.side_effect=[(1280,720),STANDARD_CLIENT_SIZE,STANDARD_CLIENT_SIZE]
        self.adapter.outer_and_work.return_value=((-500,900,796,1659),(-2560,0,0,1400))
        self.run_geometry();self.adapter.resize.assert_called_once_with(self.window,(-1936,370,1936,1030))

    def test_reference_monitor_uses_maximize_and_requires_exact_size(self):
        self.adapter.outer_and_work.return_value=((100,100,1396,859),(0,0,1920,1020))
        self.adapter.size.side_effect=[(1280,720),STANDARD_CLIENT_SIZE,STANDARD_CLIENT_SIZE]
        report=self.run_geometry();self.adapter.maximize.assert_called_once()
        self.adapter.resize.assert_not_called();self.assertTrue(report['maximize_requested'])

    def test_wrong_maximized_dimensions_are_not_accepted(self):
        self.adapter.outer_and_work.return_value=((100,100,1396,859),(0,0,1920,1020))
        self.adapter.size.return_value=(1280,720)
        with self.assertRaisesRegex(RuntimeError,'未能稳定'):self.run_geometry()
        self.adapter.maximize.assert_called_once()

    def test_external_1920_screen_uses_exact_client_edges_not_maximize(self):
        self.adapter.outer_and_work.return_value=((100,0,938,1047),(0,0,1920,1040))
        self.adapter.client_origin.side_effect=[(108,31),(0,31),(0,31)]
        self.adapter.size.side_effect=[(822,1008),STANDARD_CLIENT_SIZE,STANDARD_CLIENT_SIZE]
        report=self.run_geometry()
        self.adapter.resize.assert_called_once_with(self.window,(-8,0,1936,1030))
        self.adapter.maximize.assert_not_called()
        self.assertEqual(report['actual_client_rect'],[0,31,1920,1022])
        self.assertEqual(report['placement'],'client_edges')

    def test_client_edges_support_negative_monitor_origin(self):
        self.adapter.outer_and_work.return_value=((-1820,0,-982,1047),(-1920,0,0,1040))
        self.adapter.client_origin.side_effect=[(-1812,31),(-1920,31),(-1920,31)]
        self.adapter.size.side_effect=[(822,1008),STANDARD_CLIENT_SIZE,STANDARD_CLIENT_SIZE]
        self.run_geometry()
        self.adapter.resize.assert_called_once_with(self.window,(-1928,0,1936,1030))

    def test_exact_size_but_clipped_client_cannot_pass(self):
        self.adapter.outer_and_work.return_value=((100,0,938,1047),(0,0,1920,1040))
        self.adapter.client_origin.return_value=(108,31)
        self.adapter.size.side_effect=[(822,1008)]+[STANDARD_CLIENT_SIZE]*40
        with self.assertRaisesRegex(RuntimeError,'未完整显示'):self.run_geometry()
        self.adapter.resize.assert_called_once()
        self.adapter.maximize.assert_not_called()

    def test_resize_refused_times_out_without_repeated_requests(self):
        self.adapter.size.return_value=(1280,720)
        with self.assertRaisesRegex(RuntimeError,'未能稳定'):self.run_geometry()
        self.adapter.resize.assert_called_once();self.assertLess(self.now,3.2)

    def test_stop_and_identity_change_prevent_resize(self):
        self.stop.is_set.return_value=True
        with self.assertRaises(InterruptedError):self.run_geometry()
        self.adapter.resize.assert_not_called()
        self.stop.is_set.return_value=False
        self.adapter.validate.side_effect=RuntimeError('identity')
        with self.assertRaisesRegex(RuntimeError,'identity'):self.run_geometry()
        self.adapter.resize.assert_not_called()

    def test_stop_after_request_prevents_confirmation(self):
        self.adapter.size.return_value=(1280,720)
        self.adapter.resize.side_effect=lambda *args:setattr(self.stop.is_set,'return_value',True)
        with self.assertRaises(InterruptedError):self.run_geometry()
        self.adapter.resize.assert_called_once()
