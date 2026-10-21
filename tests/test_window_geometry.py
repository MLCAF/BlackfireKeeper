"""Physical-pixel geometry policy; no real window or game input."""
from contextlib import nullcontext
import unittest
from unittest.mock import Mock
from bk2.window_geometry import standardize, fit_client_size, select_client_size, STANDARD_CLIENT_SIZE


class Surface:
    def __init__(self, size=(1280,720), work=(0,0,2560,1400), origin=(100,100)):
        self.client=size;self.work=work;self.origin=origin
        self.frame=(16,39);self.inset=(8,31)
        self.hidden=False;self.normal=True;self.refuse=False;self.clipped=False
        self.requests=[];self.restores=0
    def pixels(self):return nullcontext()
    def validate(self,w):pass
    def size(self,w):return self.client
    def restored(self,w):return self.normal
    def minimized(self,w):return self.hidden
    def client_origin(self,w):return tuple(self.origin[i]+self.inset[i] for i in range(2))
    def outer_and_work(self,w):
        x,y=self.origin;cw,ch=self.client;fw,fh=self.frame
        return (x,y,x+cw+fw,y+ch+fh),self.work
    def resize(self,w,rect):
        self.requests.append(rect)
        if not self.refuse:
            x,y,ow,oh=rect;self.client=(ow-self.frame[0],oh-self.frame[1])
            self.origin=(x+2000 if self.clipped else x,y)
    def restore(self,w):self.restores+=1;self.hidden=False;self.normal=True


class WindowGeometryTests(unittest.TestCase):
    def setUp(self):
        self.now=0.;self.stop=Mock();self.stop.is_set.return_value=False
        self.stop.wait.side_effect=lambda s:setattr(self,'now',self.now+s)
        self.a=Surface()
    def run_geometry(self):return standardize(self.a,None,self.stop,lambda:self.now)
    def assert_visible(self,report):
        l,t,r,b=report['actual_client_rect'];wl,wt,wr,wb=self.a.work
        self.assertGreaterEqual(l,wl);self.assertGreaterEqual(t,wt)
        self.assertLessEqual(r,wr);self.assertLessEqual(b,wb)
    def test_correct_visible_standard_unchanged(self):
        self.a.client=STANDARD_CLIENT_SIZE
        self.assertEqual(self.run_geometry()['status'],'already_standard')
        self.assertFalse(self.a.requests)
    def test_accounts_for_frame(self):
        report=self.run_geometry()
        self.assertEqual(self.a.requests,[(100,100,1936,1030)])
        self.assertEqual(report['actual_client_size'],[1920,991]);self.assert_visible(report)
    def test_standard_but_clipped_is_repositioned(self):
        self.a.client=STANDARD_CLIENT_SIZE;self.a.origin=(2000,100)
        self.assert_visible(self.run_geometry());self.assertEqual(len(self.a.requests),1)
    def test_minimized_restores_before_measurement(self):
        self.a.hidden=True
        self.assert_visible(self.run_geometry());self.assertEqual(self.a.restores,1)
    def test_minimized_maximized_exact_visible_is_accepted(self):
        self.a.hidden=True;self.a.normal=False;self.a.client=STANDARD_CLIENT_SIZE
        self.a.restore=lambda w:(setattr(self.a,'hidden',False),setattr(self.a,'restores',self.a.restores+1))
        report=self.run_geometry()
        self.assertEqual(report['status'],'restored_standard');self.assertEqual(self.a.restores,1)
        self.assertFalse(self.a.requests)
    def test_wrong_maximized_restore_tries_only_twice(self):
        self.a.hidden=True;self.a.normal=False
        def restore(w):
            self.a.hidden=False;self.a.restores+=1
            if self.a.restores==2:self.a.normal=True
        self.a.restore=restore
        self.assert_visible(self.run_geometry());self.assertEqual(self.a.restores,2)
    def test_restore_refused_is_bounded(self):
        self.a.hidden=True;self.a.normal=False;self.a.restore=Mock()
        with self.assertRaisesRegex(RuntimeError,'普通窗口超时') as ctx:self.run_geometry()
        self.a.restore.assert_called_once();self.assertLess(self.now,3.2)
        self.assertEqual(ctx.exception.window_geometry['phase'],'restore')
    def test_small_work_area_scales_instead_of_failing(self):
        self.a.work=(0,0,1366,728)
        report=self.run_geometry()
        self.assertEqual(report['actual_client_size'],[1280,661]);self.assert_visible(report)
        self.assertEqual(report['calculated_client_ceiling'],[1335,689])
        self.assertEqual(self.a.requests,[(70,28,1296,700)])
    def test_already_fitted_small_window_unchanged(self):
        self.a.work=(0,0,1366,728);self.a.client=(1280,661);self.a.origin=(0,0)
        self.assertEqual(self.run_geometry()['status'],'already_fitted');self.assertFalse(self.a.requests)
    def test_negative_origin(self):
        self.a.work=(-2560,0,0,1400);self.a.origin=(-500,900)
        self.assert_visible(self.run_geometry())
        self.assertEqual(self.a.requests,[(-1936,370,1936,1030)])
    def test_height_shortfall_scales_without_maximizing(self):
        self.a.work=(0,0,1920,1020)
        report=self.run_geometry();self.assertEqual(report['target_client_size'],[1600,826])
        self.assert_visible(report)
    def test_1920_monitor_keeps_reference_with_invisible_side_borders(self):
        self.a.work=(0,0,1920,1040);self.a.client=(822,1008);self.a.origin=(100,0)
        report=self.run_geometry();self.assertEqual(self.a.requests,[(-8,0,1936,1030)])
        self.assertEqual(report['placement'],'client_edges');self.assert_visible(report)
    def test_negative_monitor_client_edge_alignment(self):
        self.a.work=(-1920,0,0,1040);self.a.client=(822,1008);self.a.origin=(-1820,0)
        self.assert_visible(self.run_geometry())
        self.assertEqual(self.a.requests,[(-1928,0,1936,1030)])
    def test_refused_size_or_clipped_target_fails_once(self):
        for mode in ('refuse','clipped'):
            with self.subTest(mode=mode):
                self.setUp();setattr(self.a,mode,True)
                with self.assertRaisesRegex(RuntimeError,'未能稳定') as ctx:self.run_geometry()
                self.assertEqual(len(self.a.requests),1);self.assertLess(self.now,3.2)
                self.assertEqual(ctx.exception.window_geometry['status'],'failed')
    def test_monitor_change_during_resize_not_accepted(self):
        resize=self.a.resize
        def move(w,r):resize(w,r);self.a.work=(0,0,2600,1400)
        self.a.resize=move
        with self.assertRaisesRegex(RuntimeError,'未能稳定'):self.run_geometry()
    def test_cancel_or_identity_change_prevents_resize(self):
        self.stop.is_set.return_value=True
        with self.assertRaises(InterruptedError):self.run_geometry()
        self.assertFalse(self.a.requests)
        self.stop.is_set.return_value=False;self.a.validate=Mock(side_effect=RuntimeError('identity'))
        with self.assertRaisesRegex(RuntimeError,'identity'):self.run_geometry()
        self.assertFalse(self.a.requests)
    def test_cancel_after_resize_prevents_confirmation(self):
        resize=self.a.resize
        def cancel(w,r):resize(w,r);self.stop.is_set.return_value=True
        self.a.resize=cancel
        with self.assertRaises(InterruptedError):self.run_geometry()
        self.assertEqual(len(self.a.requests),1)
    def test_fit_policy_varied_work_areas_dpi_and_aspect(self):
        for work in ((3840,2100),(1920,1040),(1600,860),(1440,860),(1366,728),(1280,720),(1024,600)):
            for frame in ((16,39),(24,58),(32,78)):
                with self.subTest(work=work,frame=frame):
                    w,h=fit_client_size(work,frame)
                    self.assertLessEqual(w+frame[0],work[0]);self.assertLessEqual(h+frame[1],work[1])
                    self.assertLessEqual(w,1920);self.assertLessEqual(h,991)
                    self.assertLessEqual(abs(w-h*1920/991),1.5)
    def test_invalid_frame_or_empty_workspace_rejected(self):
        for work,frame in [((10,20),(16,39)),((1920,1080),(-1,39))]:
            with self.assertRaisesRegex(RuntimeError,'尺寸异常'):fit_client_size(work,frame)
    def test_select_largest_tested_size_that_really_fits(self):
        for work,expected in [((2560,1400),(1920,991)),((1680,970),(1600,826)),
                              ((1600,900),(1440,743)),((1366,728),(1280,661))]:
            self.assertEqual(select_client_size(work,(16,39)),expected)
        with self.assertRaisesRegex(RuntimeError,'最小已验证'):select_client_size((1280,680),(16,39))
