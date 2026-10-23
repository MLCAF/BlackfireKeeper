"""October 23 forbidden-list boundary and claimed chest regression, no game IO."""
import json
from pathlib import Path
import threading
import unittest
from unittest.mock import Mock,patch
import numpy as np
from PIL import Image
from bk2.forbidden import ForbiddenFlow,ForbiddenOptions
from bk2.forbidden_vision import ForbiddenVision,RULES
from bk2.recovery import boundary_profile,RecoveryRunner
from bk2.list_guard import LIST_RULE
from tests.test_unified_replay import Offline
from tests.test_recovery_020 import Clock
from vendor_core.backend import MaaRecognizer
from vendor_core.config import validate

ROOT=Path(__file__).resolve().parents[1]
BOUNDARY=ROOT/'logs/diagnostics/20261023-135506-854768-c38a40db/screen.png'
CHEST=ROOT/'logs/diagnostics/20261023-133949-518559-24328db4/screen.png'

class LatestForbiddenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        c=Offline();c.post_connection().wait();cls.reco=MaaRecognizer(ROOT/'assets',c)
        cls.stop=threading.Event();cls.vision=ForbiddenVision(cls.reco,cls.stop)
        cls.rule=next(r for r in boundary_profile()['states']['Boundary']['rules'] if r['name']=='领取边界从禁域切回地下城')
    @classmethod
    def tearDownClass(cls):cls.reco.cancel().wait()
    def frame(self,parts):
        image=np.full((720,1395,3),35,np.uint8)
        for name,x,y in parts:
            with Image.open(ROOT/'assets/image'/name) as f:p=np.asarray(f.convert('RGB'))[:,:,::-1].copy()
            h,w=p.shape[:2];image[y:y+h,x:x+w]=p
        return image
    def boundary(self):return self.frame([('地下城入口主体.png',540,595),('禁域导航选中.png',641,588)])
    def hit(self,image):return self.reco.recognize(image,self.rule,self.stop)
    def replay_boundary(self,first,stuck=False):
        after=self.frame([('地下城导航选中.png',535,590)])
        b=Mock();b.page=first;b.capture.side_effect=lambda stop:b.page
        b.capture_is_fresh.return_value=True
        relevant={self.rule['template'],LIST_RULE['template']}
        b.recognize.side_effect=lambda frame,rule,stop:self.reco.recognize(frame,rule,stop) if rule['template'] in relevant else None
        def click(*args):
            if not stuck:b.page=after
        b.click.side_effect=click
        config=validate(boundary_profile(),ROOT/'assets/image',True)
        r=RecoveryRunner(b,config,Mock(),False,Clock())
        with patch.object(r.stop_event,'wait',return_value=False):r.run()
        return r,b
    def test_forbidden_selection_and_target_required_and_dimmed_rejected(self):
        self.assertIsNotNone(self.hit(self.boundary()))
        for image in [self.frame([('地下城入口主体.png',540,595)]),
                      self.frame([('禁域导航选中.png',641,588)]),
                      self.frame([('地下城入口主体.png',540,595),('禁域导航选中.png',800,588)]),
                      (self.boundary()*.5).astype(np.uint8)]:
            self.assertIsNone(self.hit(image))
        self.assertEqual(self.rule['threshold'],.95)
        self.assertEqual(self.rule['requires_match']['threshold'],.95)
        self.assertEqual(self.rule['success_match']['threshold'],.95)
    def test_boundary_switch_once_requires_selected_dungeon_no_round_credit(self):
        r,b=self.replay_boundary(self.boundary())
        self.assertTrue(r.completed,r.reason);b.click.assert_called_once()
        self.assertEqual(r.stats.rounds,0)
        self.assertEqual(self.rule['event'],'none')
        self.assertFalse(self.rule.get('foreground',False))
    def test_failed_tab_switch_is_bounded_and_never_claims_success(self):
        r,b=self.replay_boundary(self.boundary(),stuck=True)
        self.assertFalse(r.completed);self.assertEqual(r.stats.rounds,0)
        self.assertEqual(b.click.call_count,2)
        b.save_diagnostic.assert_called_once()
    def test_claimed_variant_random_slots_closes_only_with_modal_and_close(self):
        for x in (125,303,483,663,843,1023):
            parts=[('forbidden/treasure_title.png',653,254),
                   ('forbidden/claimed_1920.png',x,458),('forbidden/close_1600.png',1151,197)]
            frame=self.frame(parts)
            kind,match=self.vision.observe(frame)
            self.assertEqual(kind,'chest_claimed')
            bx,by,bw,bh=match.box
            self.assertLessEqual(abs(bx+bw/2-1164.5),1)
            self.assertLessEqual(abs(by+bh/2-210.5),1)
            self.assertIsNone(self.reco.recognize(frame,RULES['free_open'],self.stop))
        for missing in (0,1):
            kind,match=self.vision.observe(self.frame([p for i,p in enumerate(parts) if i!=missing]))
            self.assertFalse(kind=='chest_claimed' and match is not None)
        kind,match=self.vision.observe(self.frame(parts[:-1]))
        self.assertEqual(kind,'chest_claimed');self.assertIsNone(match)
        self.assertEqual(self.vision.observe((frame*.5).astype(np.uint8))[0],'unknown')
    def test_assets_provenance_and_package_profile(self):
        import hashlib
        manifest=json.loads((ROOT/'tests/latest_forbidden_manifest.json').read_text(encoding='utf8'))
        for row in manifest:
            self.assertEqual(hashlib.sha256((ROOT/'assets/image'/row['template']).read_bytes()).hexdigest(),row['sha256'])
        validate(boundary_profile(),ROOT/'assets/image',True)
    @unittest.skipUnless(BOUNDARY.is_file() and CHEST.is_file(),'Private diagnostic screens are not distributed')
    def test_actual_failures_and_paid_buttons_not_retried(self):
        def read(p):
            with Image.open(p) as im:return np.asarray(im.convert('RGB'))[:,:,::-1].copy()
        r,b=self.replay_boundary(read(BOUNDARY));self.assertTrue(r.completed,r.reason)
        b.click.assert_called_once();self.assertEqual(r.stats.rounds,0)
        frame=read(CHEST)
        self.assertIsNone(self.reco.recognize(frame,RULES['claimed'],self.stop))
        kind,close=self.vision.observe(frame)
        self.assertEqual(kind,'chest_claimed');self.assertIsNotNone(close)
        flow=ForbiddenFlow(ForbiddenOptions(1),0)
        flow.step('explore',True,300,1);flow.step('chest_free',True,297,2)
        self.assertEqual(flow.step(kind,True,297,3),'chest_claimed')
        self.assertIsNone(flow.step(kind,True,297,4));self.assertEqual(flow.completed,0)
        flow.step('explore',True,297,5);self.assertTrue(flow.done);self.assertEqual(flow.completed,1)
        # Leave all actual 20-diamond buttons visible, remove only the claimed label.
        frame[455:490,480:555]=frame[455:490,300:375]
        kind,match=self.vision.observe(frame)
        self.assertEqual(kind,'chest_free');self.assertIsNone(match)
        self.assertIsNone(self.hit(read(CHEST)))
