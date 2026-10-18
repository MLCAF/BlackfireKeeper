"""Real Maa matching; private full screenshots are never distributed."""
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import numpy as np
from PIL import Image
from bk2.recovery import RecoveryRunner, recovery_rules
from bk2.food_profile import build_food_profile
from bk2.automation import load_plan
from bk2.profiles import build_dungeon_profile
from vendor_core.backend import MaaRecognizer
from vendor_core.config import validate
from tests.test_unified_replay import Offline
import json

ROOT=Path(__file__).resolve().parents[1]


class RecoveryPixelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        c=Offline();c.post_connection().wait();cls.reco=MaaRecognizer(ROOT/'assets',c)
    @classmethod
    def tearDownClass(cls):cls.reco.cancel().wait()
    def read(self,path):
        with Image.open(ROOT/path) as im:return np.asarray(im.convert('RGB'))[:,:,::-1].copy()
    def frame(self,items):
        frame=np.full((720,1395,3),35,np.uint8)
        for name,x,y in items:
            im=self.read('assets/image/'+name);h,w=im.shape[:2];frame[y:y+h,x:x+w]=im
        return frame

    @unittest.skipUnless((ROOT/'tests/fixtures/diamond_fix_20261018/reward.png').exists(),'Private reward recovery capture not distributed')
    def test_rewards_wait_boundary_clears_old_reward_and_shop_without_new_claim(self):
        from bk2.recovery import boundary_profile
        from tests.upstream.test_diamonds_auto import VideoBackend
        from tests.test_recovery_020 import Clock
        frames={5:self.read('tests/fixtures/diamond_fix_20261018/reward.png'),
                6:self.read('tests/fixtures/diamond_fix_20261018/shop.png'),
                7:self.read('tests/fixtures/diamonds_20261017/frame_007.jpg')}
        backend=VideoBackend(self.reco,frames,5)
        runner=RecoveryRunner(backend,boundary_profile(),Mock(),False,Clock())
        with patch.object(runner.stop_event,'wait',return_value=False):runner.run()
        self.assertTrue(runner.completed,runner.reason)
        self.assertEqual([p for p,_ in backend.clicks],[5,6])
        self.assertEqual(runner.counters,{})
        self.assertEqual(runner.stats.rounds,0)
    def test_login_and_unfinished_require_specific_context_not_just_button(self):
        for rule in recovery_rules()[1:]:
            frame=self.frame([(rule['template'],600,500),(rule['requires_match']['template'],450,260)])
            self.assertIsNotNone(self.reco.recognize(frame,rule,threading.Event()))
            self.assertIsNone(self.reco.recognize(self.frame([(rule['template'],600,500)]),rule,threading.Event()))
            self.assertIsNone(self.reco.recognize((frame*.4).astype(np.uint8),rule,threading.Event()))
    def test_bakery_variants_require_home_anchor(self):
        rules=[r for r in build_food_profile()['states']['FoodEntry']['rules'] if r['template'].startswith('主城面包')]
        for rule in rules:
            f=self.frame([(rule['template'],170,400),('主界面锚点.png',450,50)])
            self.assertIsNotNone(self.reco.recognize(f,rule,threading.Event()))
            self.assertIsNone(self.reco.recognize(self.frame([(rule['template'],170,400)]),rule,threading.Event()))
    def test_six_generated_profiles_and_resume_rules_validate(self):
        from bk2.recovery import add_resume_start
        plan=load_plan();base=json.loads((ROOT/'config/profiles/dungeon_unlimited.json').read_text(encoding='utf8'))
        for task in plan['tasks']:
            if not task.get('entrance_template'):continue
            config=add_resume_start(build_dungeon_profile(base,task,plan['tasks']))
            config['interrupts']=recovery_rules()
            validate(config,ROOT/'assets/image',True)
            self.assertFalse(any(r.get('action')=='click' for r in config['states']['ResumeStart']['rules']))
    @unittest.skipUnless((ROOT/'tests/fixtures/diamonds_20261017/frame_007.jpg').exists(),'Private town capture not distributed')
    def test_normal_bakery_on_saved_town_capture(self):
        f=self.read('tests/fixtures/diamonds_20261017/frame_007.jpg')
        r=next(r for r in build_food_profile()['states']['FoodEntry']['rules'] if r['template']=='主城面包工坊.png')
        hit=self.reco.recognize(f,r,threading.Event());self.assertIsNotNone(hit)
        self.assertTrue(150 <= hit.box[0] <= 280)
    @unittest.skipUnless((ROOT/'tests/fixtures/recovery_20261018/login.png').exists(),'Private uploaded recovery images not distributed')
    def test_uploaded_login_and_unfinished_after_scale_calibration(self):
        for name,rule in [('login',recovery_rules()[1]),('unfinished',recovery_rules()[2])]:
            im=self.read('tests/fixtures/recovery_20261018/'+name+'.png')
            h,w=im.shape[:2];f=np.full((720,1395,3),35,np.uint8);f[30:30+h,300:300+w]=im
            self.assertIsNotNone(self.reco.recognize(f,rule,threading.Event()))

    @unittest.skipUnless((ROOT/'tests/fixtures/list_guard_20261018/battle.png').exists(),'Private battle capture not distributed')
    def test_mid_battle_resume_recognizes_saved_capture_without_entry_click(self):
        from bk2.recovery import add_resume_start
        p=load_plan();base=json.loads((ROOT/'config/profiles/dungeon_unlimited.json').read_text(encoding='utf8'))
        c=add_resume_start(build_dungeon_profile(base,p['tasks'][0],p['tasks']))
        frame=self.read('tests/fixtures/list_guard_20261018/battle.png')
        hits=[r for r in c['states']['ResumeStart']['rules'] if self.reco.recognize(frame,r,threading.Event())]
        self.assertTrue(hits)
        self.assertTrue(all(r['action']=='none' and r['next']=='Progress' for r in hits))


if __name__=='__main__':unittest.main()
