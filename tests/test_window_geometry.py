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
        self.adapter.minimized.return_value=True
        self.adapter.size.return_value=STANDARD_CLIENT_SIZE
        self.run_geometry();self.adapter.restore.assert_called_once_with(self.window)

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
