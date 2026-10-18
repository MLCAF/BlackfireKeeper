"""Direction regression on saved failure and synthetic home frames; no real game IO."""
import json
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock

import numpy as np
from PIL import Image
from maa.controller import CustomController
from vendor_core.backend import MaaRecognizer
from vendor_core.runner import Runner
from vendor_core.unified import build_dungeon_profile
from tests.upstream.test_runner import FakeBackend

ROOT=Path(__file__).resolve().parents[2]

class Offline(CustomController):
    def connect(self): return True
    def request_uuid(self): return 'town-portal-offline'
    def get_features(self): return 0
    def screencap(self): raise AssertionError('No game capture')
    def click(self,*a): raise AssertionError('No game click')

@unittest.skipUnless((ROOT/'tests/fixtures').is_dir(), 'Local replay screenshots are not distributed; see tests/README.md')
class TownPortalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.controller=Offline()
        cls.controller.post_connection().wait()
        cls.reco=MaaRecognizer(ROOT/'assets',cls.controller)
        cls.diamonds=json.loads((ROOT/'config/profiles/diamonds.json').read_text(encoding='utf-8'))
        plan=json.loads((ROOT/'config/dungeon_plan.json').read_text(encoding='utf-8'))
        base=json.loads((ROOT/'config/profiles/dungeon_unlimited.json').read_text(encoding='utf-8'))
        cls.dungeon=build_dungeon_profile(base,plan['tasks'][0],plan['tasks'])
        cls.portal=next(r for r in cls.dungeon['states']['Entry']['rules'] if r['template']=='传送门.png')
        cls.town=next(r for r in cls.diamonds['states']['ReturnHome']['rules'] if r['template']=='城镇.png')

    @classmethod
    def tearDownClass(cls): cls.reco.cancel().wait()

    def frame(self,path):
        with Image.open(ROOT/path) as im: return np.asarray(im.convert('RGB'))[:,:,::-1].copy()

    def hit(self,frame,rule): return self.reco.recognize(frame,rule,threading.Event())

    def home(self):
        canvas=np.zeros((720,1394,3),dtype=np.uint8)
        for name,x,y in [('主界面锚点.png',182,100),('传送门.png',10,590)]:
            pixels=self.frame('assets/image/'+name)
            h,w=pixels.shape[:2]
            canvas[y:y+h,x:x+w]=pixels
        return canvas

    def test_new_failure_selects_town_not_portal(self):
        frame=self.frame('tests/fixtures/failures_20261016/diamond_return_home.png')
        runner=Runner(self.reco,self.diamonds,Mock())
        hits=[(r,m) for r in runner.ordered_rules() if (m:=self.hit(frame,r))]
        self.assertTrue(hits)
        rule,match=hits[0]
        self.assertEqual(rule['template'],'城镇.png')
        x,y,w,h=match.box
        self.assertTrue(0<x+w//2<110 and 590<y+h//2<720)
        self.assertIsNone(self.hit(frame,self.portal))
        print(f'TOWN_REPLAY score={match.score:.6f}; point={(x+w//2,y+h//2)}')

    def test_home_selects_portal_and_requires_home_evidence(self):
        home=self.home()
        self.assertIsNotNone(self.hit(home,self.portal))
        self.assertIsNone(self.hit(home,self.town))
        home[100:230,182:321]=0
        self.assertIsNone(self.hit(home,self.portal))

    def test_diamonds_already_home_does_not_click_portal(self):
        runner=Runner(self.reco,self.diamonds,Mock())
        hits=[r for r in runner.ordered_rules() if self.hit(self.home(),r)]
        self.assertEqual(hits[0]['action'],'none')
        self.assertEqual(hits[0]['next'],'OpenDiamond')

    def test_dimmed_navigation_buttons_are_rejected(self):
        frame=self.frame('tests/fixtures/failures_20261016/diamond_return_home.png')
        self.assertIsNone(self.hit((frame.astype(float)*.2+30).astype(np.uint8),self.town))
        self.assertIsNone(self.hit((self.home().astype(float)*.2+30).astype(np.uint8),self.portal))

    def test_town_click_requires_positive_home_confirmation(self):
        backend=FakeBackend(['主界面锚点.png','主界面锚点.png'])
        runner=Runner(backend,self.diamonds,Mock(),False)
        match=self.hit(self.frame('tests/fixtures/failures_20261016/diamond_return_home.png'),self.town)
        self.assertTrue(runner.click_with_change_confirmation(self.town,match))
        self.assertEqual(backend.clicks,1)
        self.assertEqual(runner.history[-1]['template'],'主界面锚点.png')

    def test_portal_click_does_not_start_or_complete_dungeon_round(self):
        backend=FakeBackend(['地下城入口主体.png','地下城入口主体.png'])
        runner=Runner(backend,self.dungeon,Mock(),False)
        self.assertTrue(runner.click_with_change_confirmation(self.portal,self.hit(self.home(),self.portal)))
        self.assertEqual(self.portal['next'],'Entry')
        self.assertEqual(self.portal.get('event','none'),'none')
        self.assertEqual(runner.stats.rounds,0)
        self.assertFalse(runner.completed)

    def test_opening_shop_alone_does_not_complete_collection(self):
        rule=self.diamonds['states']['OpenDiamond']['rules'][0]
        self.assertEqual(rule.get('event','none'),'none')
        self.assertEqual(rule['next'],'InspectPack')
        self.assertEqual(rule['success_match']['template'],'钻石免费包标识.png')

if __name__=='__main__': unittest.main()
