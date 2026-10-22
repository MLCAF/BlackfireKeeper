"""Known acknowledgements: actual Maa matching and bounded offline flows."""
import copy
import hashlib
import json
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock,patch

import numpy as np
from PIL import Image
from bk2.confirmation import notification_rules
from bk2.forbidden import ForbiddenFlow,ForbiddenOptions
from bk2.forbidden_vision import ForbiddenVision
from bk2.profiles import build_dungeon_profile
from bk2.recovery import RecoveryRunner,HOME
from tests.test_scene_variants import VariantRecognizer
from tests.test_unified_replay import Offline
from vendor_core.backend import MaaRecognizer
from vendor_core.config import validate

ROOT=Path(__file__).resolve().parents[1]
FIX=ROOT/'evidence/dialogs_20261022/replay'


class DialogPixels(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        controller=Offline();controller.post_connection().wait()
        cls.reco=VariantRecognizer(ROOT/'assets',controller)
        planpath=ROOT/'config/dungeon_plan.example.json'
        if not planpath.exists():planpath=ROOT/'config/dungeon_plan.json'
        plan=json.loads(planpath.read_text(encoding='utf-8-sig'))
        base=json.loads((ROOT/'config/profiles/dungeon_unlimited.json').read_text(encoding='utf-8'))
        cls.profile=build_dungeon_profile(base,plan['tasks'][0],plan['tasks'])
        cls.welcome=notification_rules()[0]
        cls.buff=cls.profile['states']['OpeningBuffConfirm']['rules'][0]

    @classmethod
    def tearDownClass(cls):cls.reco.cancel().wait()

    def read(self,path):
        with Image.open(path) as im:return np.asarray(im.convert('RGB'))[:,:,::-1].copy()

    def frame(self,parts):
        image=np.full((720,1394,3),35,np.uint8)
        for name,x,y in parts:
            tile=self.read(ROOT/'assets/image'/name);h,w=tile.shape[:2]
            image[y:y+h,x:x+w]=tile
        return image

    def welcome_parts(self):
        return [('欢迎归队标题.png',621,179),('欢迎归队称呼.png',395,240),('欢迎归队确认.png',662,536)]

    def buff_parts(self):
        return [('经验祝福确认文字.png',668,400),('经验祝福确认标题.png',650,248)]

    def hit(self,image,rule):return self.reco.recognize(image,rule,threading.Event())

    def test_welcome_requires_title_greeting_button_and_rejects_mask(self):
        parts=self.welcome_parts();image=self.frame(parts)
        self.assertIsNotNone(self.hit(image,self.welcome))
        for missing in range(3):
            self.assertIsNone(self.hit(self.frame(parts[:missing]+parts[missing+1:]),self.welcome))
        self.assertIsNone(self.hit((image*.4).astype(np.uint8),self.welcome))
        self.assertIsNone(self.hit(self.frame([('欢迎归队确认.png',662,536)]),self.welcome))

    def test_buff_fallback_keeps_threshold_roi_and_extra_condition(self):
        image=self.frame(self.buff_parts());frozen=copy.deepcopy(self.buff)
        self.assertGreaterEqual(self.hit(image,self.buff).score,.96)
        self.assertEqual(self.buff,frozen)
        self.assertIsNone(self.hit(self.frame(self.buff_parts()[:1]),self.buff))
        self.assertIsNone(self.hit((image*.4).astype(np.uint8),self.buff))
        self.assertIsNone(self.hit(image,dict(self.buff,roi=[0,0,400,200])))
        self.assertIsNone(self.hit(image,dict(self.buff,requires_match=HOME)))

    @unittest.skipUnless((FIX/'buff.png').exists(),'Private dialog evidence is not distributed')
    def test_actual_buff_and_welcome_crop_with_cross_page_negatives(self):
        buff=self.read(FIX/'buff.png')
        # Plain recognizer reproduces the reported miss at the unchanged .96.
        self.assertIsNone(MaaRecognizer.recognize(self.reco,buff,self.buff,threading.Event()))
        self.assertGreaterEqual(self.hit(buff,self.buff).score,.96)
        welcome=self.frame([]);crop=self.read(FIX/'welcome_crop_normalized.png')
        h,w=crop.shape[:2];welcome[7:7+h,42:42+w]=crop
        self.assertIsNotNone(self.hit(welcome,self.welcome))
        self.assertIsNone(self.hit(buff,self.welcome))
        self.assertIsNone(self.hit(welcome,self.buff))
        self.assertEqual(ForbiddenVision(self.reco,threading.Event()).observe(welcome)[0],'welcome')

    def backend(self,frames):
        test=self
        class Backend:
            def __init__(self):self.page=0;self.clicks=[];self.save_diagnostic=Mock(return_value='offline.json')
            def guard(self):pass
            def cancel(self):pass
            def capture_is_fresh(self,*args):return True
            def capture(self,stop):return frames[self.page]
            def recognize(self,*args):return test.reco.recognize(*args)
            def recovery_data(self):return {'dungeon_progress':{'result':'finished'}}
            def click(self,match,offset,stop,pause):
                if stop.is_set() or pause.is_set():raise InterruptedError()
                self.clicks.append(match.box);self.page=min(self.page+1,len(frames)-1)
        return Backend()

    def runner(self,backend,config):
        now=[100.]
        def clock():now[0]+=.1;return now[0]
        return RecoveryRunner(backend,validate(config,ROOT/'assets/image',True),Mock(),False,clock)

    def test_welcome_closes_once_then_verifies_home_without_reward_credit(self):
        home=self.frame([('主界面锚点.png',240,180)])
        backend=self.backend([self.frame(self.welcome_parts()),home])
        config=dict(version=1,initial_state='Home',max_minutes=2,poll_seconds=.1,
            states={'Home':dict(timeout_seconds=60,rules=[dict(HOME,name='home',action='none',event='task_complete',confirm_frames=2)])})
        runner=self.runner(backend,config)
        with patch.object(runner.stop_event,'wait',return_value=False):runner.run()
        self.assertTrue(runner.completed,runner.reason)
        self.assertEqual(len(backend.clicks),1)
        self.assertEqual(runner.stats.rounds,0)
        self.assertFalse(runner.counters)

    def test_pending_buff_recovers_without_reselecting_or_purchasing(self):
        buff=self.read(FIX/'buff.png') if (FIX/'buff.png').exists() else self.frame(self.buff_parts())
        after=self.frame([('主界面锚点.png',240,180)])
        backend=self.backend([buff,after])
        config=copy.deepcopy(self.profile);config['initial_state']='OpeningBuff'
        config['states']['OpeningBuffClose']=dict(timeout_seconds=30,rules=[
            dict(HOME,name='test next page',action='none',event='task_complete',confirm_frames=2)])
        runner=self.runner(backend,config)
        runner.pending_action=('OpeningBuff',runner.config['states']['OpeningBuff']['rules'][0])
        with patch.object(runner.stop_event,'wait',return_value=False):runner.run()
        self.assertTrue(runner.completed,runner.reason)
        self.assertEqual(len(backend.clicks),1)
        x,y,w,h=backend.clicks[0];self.assertTrue(650<=x+w//2<=760 and 380<=y+h//2<=465)
        self.assertEqual(runner.stats.rounds,0)

    def test_crops_match_provenance_manifest(self):
        for item in json.loads((ROOT/'tests/dialog_template_manifest.json').read_text(encoding='utf-8')):
            p=ROOT/'assets/image'/item['template']
            self.assertEqual(hashlib.sha256(p.read_bytes()).hexdigest(),item['sha256'])
            with Image.open(p) as im:self.assertEqual(list(im.size),item['crop'][2:])


class NoticeFlowTests(unittest.TestCase):
    def test_stuck_welcome_clicks_once_and_missing_memory_never_resumes(self):
        from tests.test_recovery_020 import Scenes,run
        class Welcome(Scenes):
            def __init__(self,stuck=False,unknown=False):
                super().__init__(unfinished=False);self.page='welcome';self.stuck=stuck;self.unknown=unknown
            def recognize(self,frame,rule,stop):
                if frame=='welcome':
                    from vendor_core.backend import Match
                    return Match((662,536,71,44),1) if rule.get('recovery_action')=='welcome' else None
                return super().recognize(frame,rule,stop)
            def click(self,*args):
                self.clicks.append(self.page)
                if not self.stuck:self.page='home'
            def recovery_data(self):return None if self.unknown else super().recovery_data()
        config=dict(version=1,initial_state='Home',max_minutes=2,poll_seconds=.1,
            states={'Home':dict(timeout_seconds=60,rules=[dict(HOME,name='home',action='none',event='task_complete',confirm_frames=2)])})
        for kwargs in ({'stuck':True},{'unknown':True}):
            b=Welcome(**kwargs);r=run(b,config)
            self.assertFalse(r.completed);self.assertEqual(b.clicks,['welcome'])
            b.save_diagnostic.assert_called_once()

    def test_forbidden_welcome_waits_for_home_and_cannot_complete_exploration(self):
        flow=ForbiddenFlow(ForbiddenOptions(1),0)
        self.assertEqual(flow.step('welcome',True,320,1),'welcome')
        self.assertIsNone(flow.step('welcome',True,320,2))
        self.assertIsNone(flow.step('explore',True,320,3))
        self.assertEqual(flow.completed,0)
        self.assertEqual(flow.step('home',True,320,4),'home')

    def test_forbidden_welcome_is_not_adopted_during_battle(self):
        flow=ForbiddenFlow(ForbiddenOptions(1),0);flow.step('explore',True,320,1)
        self.assertIsNone(flow.step('welcome',True,317,2))
        self.assertEqual(flow.completed,0)
