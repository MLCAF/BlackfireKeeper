"""Golden Kingdom action budgets, reward boundaries and real Maa negatives."""
from pathlib import Path
import threading
import unittest
from unittest.mock import Mock, patch
import numpy as np
from PIL import Image
from bk2.forbidden import ForbiddenFlow, ForbiddenOptions, ForbiddenRunner
from bk2.forbidden_vision import ForbiddenVision, RULES
from vendor_core.backend import Match, MaaRecognizer
from tests.test_unified_replay import Offline

ROOT=Path(__file__).resolve().parents[1]
VIDEO=ROOT/'evidence/forbidden_20261021'


class FlowTests(unittest.TestCase):
    def flow(self, rounds=1):
        f=ForbiddenFlow(ForbiddenOptions(rounds),0)
        self.assertEqual(f.step('explore',True,320,1),'explore')
        return f

    def test_free_chest_single_submission_and_return_confirmation(self):
        f=self.flow()
        self.assertEqual(f.step('chest_free',True,317,2),'chest_free')
        for kind in ('chest_free','unknown','chest_free'):
            self.assertIsNone(f.step(kind,True,317,3))
        self.assertEqual(f.completed,0)
        self.assertEqual(f.step('chest_claimed',True,317,4),'chest_claimed')
        self.assertIsNone(f.step('chest_claimed',True,317,5))
        self.assertEqual(f.completed,0)
        f.step('explore',True,317,6)
        self.assertEqual(f.completed,1);self.assertTrue(f.done)
        self.assertIsNone(f.step('explore',True,317,7))

    def test_paid_only_missing_free_button_never_clicks(self):
        f=self.flow()
        self.assertIsNone(f.step('chest_free',False,317,2))
        self.assertIsNone(f.step('chest_claimed',False,317,3))
        self.assertEqual(f.completed,0)
        with self.assertRaises(TimeoutError):f.step('chest_free',False,317,62)

    def test_delayed_explore_and_lost_open_do_not_resubmit(self):
        f=self.flow()
        for t in range(2,20):self.assertIsNone(f.step('explore',True,320,t))
        f.step('chest_free',True,317,20)
        with self.assertRaises(TimeoutError):f.step('chest_free',True,317,41)
        self.assertEqual(f.completed,0)

    def test_battle_both_rewards_and_auto_toggle_once(self):
        f=self.flow()
        self.assertEqual(f.step('auto_off',True,317,2),'auto_off')
        self.assertIsNone(f.step('auto_off',True,317,3))
        self.assertIsNone(f.step('auto_on',True,317,4))
        self.assertIsNone(f.step('auto_on',True,317,5))
        self.assertEqual(f.step('xp',True,317,6),'xp')
        self.assertIsNone(f.step('xp',True,317,7))
        self.assertIsNone(f.step('explore',True,317,8))
        self.assertEqual(f.completed,0)
        self.assertEqual(f.step('loot',True,317,9),'loot')
        self.assertIsNone(f.step('loot',True,317,10))
        f.step('explore',True,317,11)
        self.assertTrue(f.done)

    def test_already_active_auto_not_toggled_and_fast_battle(self):
        f=self.flow();self.assertIsNone(f.step('auto_on',True,317,2))
        self.assertEqual(f.step('loot',True,317,3),'loot')
        f.step('explore',True,317,4);self.assertTrue(f.done)

    def test_two_encounters_reset_budget_only_after_return(self):
        f=self.flow(2)
        f.step('chest_free',True,317,2);f.step('chest_claimed',True,317,3)
        f.step('explore',True,317,4)
        self.assertFalse(f.done)
        self.assertEqual(f.step('explore',True,317,5),'explore')
        self.assertEqual(f.step('chest_free',True,314,6),'chest_free')
        f.step('chest_claimed',True,314,7);f.step('explore',True,314,8)
        self.assertEqual(f.completed,2);self.assertTrue(f.done)

    def test_unknown_start_chest_and_other_battle_are_not_adopted(self):
        f=ForbiddenFlow(ForbiddenOptions(),0)
        for kind in ('chest_free','chest_claimed','auto_off'):
            self.assertIsNone(f.step(kind,True,320,1))
        with self.assertRaises(TimeoutError):f.check_time(46)

    def test_navigation_requires_positive_successor(self):
        f=ForbiddenFlow(ForbiddenOptions(),0)
        for before,after in [('home','menu'),('menu','map'),('map','detail'),('detail','explore')]:
            self.assertEqual(f.step(before,True,320,1),before)
            self.assertIsNone(f.step(before,True,320,2))
        self.assertEqual(f.step('explore',True,320,3),'explore')

    def test_stamina_and_invalid_options(self):
        f=ForbiddenFlow(ForbiddenOptions(minimum_stamina=100),0)
        f.step('explore',True,99,1);self.assertTrue(f.done)
        self.assertEqual(f.completed,0)
        f=ForbiddenFlow(ForbiddenOptions(),0)
        with self.assertRaises(RuntimeError):f.step('explore',True,None,1)
        for options in [ForbiddenOptions(0),ForbiddenOptions(True),ForbiddenOptions(1,2)]:
            with self.assertRaises(ValueError):options.validate()

    def test_defeat_stops_without_retry_or_exit(self):
        f=self.flow()
        with self.assertRaises(RuntimeError):f.step('defeat',False,317,2)

    def test_runner_requires_two_frames_and_honors_stale_capture(self):
        stop=threading.Event();backend=Mock();backend.capture_is_fresh.return_value=True
        clock=[0.]
        def now():clock[0]+=.1;return clock[0]
        runner=ForbiddenRunner(backend,ForbiddenOptions(),lambda:320,stop,Mock(),True,now)
        with patch('bk2.forbidden_vision.ForbiddenVision.observe',return_value=('explore',Match((1,2,3,4),1))),patch.object(stop,'wait'):
            result=runner.run()
        self.assertTrue(result['preview']);self.assertEqual(backend.capture.call_count,2)
        backend.click.assert_not_called()
        backend.reset_mock();backend.capture_is_fresh.return_value=False
        runner=ForbiddenRunner(backend,ForbiddenOptions(),lambda:320,stop,Mock(),False,now)
        with patch('bk2.forbidden_vision.ForbiddenVision.observe',return_value=('explore',Match((1,2,3,4),1))),patch.object(stop,'wait'):
            with self.assertRaises(TimeoutError):runner.run()
        backend.click.assert_not_called()

    def test_stop_prevents_capture_and_input(self):
        stop=threading.Event();stop.set();backend=Mock()
        result=ForbiddenRunner(backend,ForbiddenOptions(),lambda:320,stop,Mock()).run()
        self.assertFalse(result['succeeded']);backend.click.assert_not_called();backend.capture.assert_not_called()


class VisionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        c=Offline();c.post_connection().wait();cls.reco=MaaRecognizer(ROOT/'assets',c)
        cls.stop=threading.Event();cls.vision=ForbiddenVision(cls.reco,cls.stop)
    @classmethod
    def tearDownClass(cls):cls.reco.cancel().wait()
    def frame(self):return np.full((720,1395,3),35,np.uint8)
    def put(self,frame,name,x,y):
        im=np.asarray(Image.open(ROOT/'assets/image/forbidden'/f'{name}.png').convert('RGB'))[:,:,::-1]
        h,w=im.shape[:2];frame[y:y+h,x:x+w]=im

    def test_random_free_chest_counts_and_positions_require_modal(self):
        for count in (1,2,3,4,5,6):
            with self.subTest(count=count):
                frame=self.frame();self.put(frame,'treasure_title',650,260)
                for i in range(count):self.put(frame,'free_open',150+i*180,465)
                kind,hit=self.vision.observe(frame)
                self.assertEqual(kind,'chest_free');self.assertIsNotNone(hit)
                self.put(frame,'claimed',600,465);self.put(frame,'close',1100,200)
                kind,hit=self.vision.observe(frame)
                self.assertEqual(kind,'chest_claimed');self.assertEqual(hit.box[:2],(1100,200))
        frame=self.frame();self.put(frame,'free_open',600,465)
        self.assertEqual(self.vision.observe(frame)[0],'unknown')

    def test_auto_enabled_is_not_disabled_and_modal_masks_targets(self):
        frame=self.frame();self.put(frame,'auto_on',1060,15)
        self.assertIsNone(self.reco.recognize(frame,RULES['auto_off'],self.stop))
        self.assertEqual(self.vision.observe(frame)[0],'auto_on')
        self.assertEqual(self.vision.observe((frame*.35).astype(np.uint8))[0],'unknown')

    def test_native_window_variants_keep_context_and_threshold(self):
        cases=[('gold_header','explore',650,30,565,682,'explore'),
               ('detail','enter',650,163,657,557,'detail'),
               ('treasure_title','free_open',650,252,492,458,'chest_free')]
        for context,button,cx,cy,bx,by,expected in cases:
            with self.subTest(expected=expected):
                frame=self.frame()
                self.put(frame,context+'_1600',cx,cy)
                self.put(frame,button+'_1600',bx,by)
                self.assertEqual(self.vision.observe(frame)[0],expected)
                self.assertEqual(self.vision.observe((frame*.35).astype(np.uint8))[0],'unknown')
        self.assertTrue(all(r['threshold']==.95 for r in RULES.values()))

    @unittest.skipUnless((VIDEO/'live_detail_1600.png').is_file(),'Private live screenshots are not distributed')
    def test_real_native_screens_include_masked_map_negative(self):
        for name,expected in [('live_map_1600.png','map'),('live_detail_1600.png','detail'),
                              ('live_three_chests_1600.png','chest_free'),
                              ('live_three_claimed_1600.png','chest_claimed')]:
            frame=np.asarray(Image.open(VIDEO/name).convert('RGB'))[:,:,::-1].copy()
            kind,hit=self.vision.observe(frame)
            self.assertEqual(kind,expected);self.assertIsNotNone(hit)
            if expected=='detail':
                for name in ('map','entrance'):
                    r=dict(RULES[name],template=f'forbidden/{name}_1600.png')
                    self.assertIsNone(self.reco.recognize(frame,r,self.stop))

    @unittest.skipUnless((VIDEO/'battle/000.png').is_file(),'Private user videos are not distributed')
    def test_real_video_positive_and_paid_negative_frames(self):
        cases=[('chests/000.png','chest_free'),('chests/012.png','chest_claimed'),
               ('chests/017.png','explore'),('battle/000.png','explore'),('battle/007.png','auto_off'),
               ('battle/012.png','auto_on'),('battle/015.png','xp'),('battle/018.png','loot')]
        for filename,expected in cases:
            with self.subTest(filename=filename):
                im=Image.open(VIDEO/filename).convert('RGB')
                im=im.resize((round(im.width*720/im.height),720),Image.Resampling.LANCZOS)
                frame=np.asarray(im)[:,:,::-1].copy()
                kind,hit=self.vision.observe(frame)
                self.assertEqual(kind,expected);self.assertIsNotNone(hit)
                if expected=='chest_claimed':
                    self.assertIsNone(self.reco.recognize(frame,RULES['free_open'],self.stop))
                if expected in ('xp','loot','chest_free','chest_claimed'):
                    self.assertIsNone(self.reco.recognize(frame,RULES['explore'],self.stop))
