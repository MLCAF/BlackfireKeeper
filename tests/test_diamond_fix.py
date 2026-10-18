"""Current diamond failure frames, real recognition/OCR, simulated input only."""
import copy
import json
from pathlib import Path
import threading
import unittest
from unittest.mock import Mock,patch
import numpy as np
from PIL import Image
from bk2.diamond_profile import build_diamond_profile,entry_recovery_rules,build_cleanup_profile
from bk2.profiles import build_dungeon_profile
from bk2.automation import load_plan
from vendor_core.backend import MaaRecognizer
from vendor_core.runner import Runner
from tests.upstream.test_diamonds_auto import VideoBackend
from tests.upstream.test_town_portal import Offline

ROOT=Path(__file__).resolve().parents[1]

@unittest.skipUnless((ROOT/'tests/fixtures').is_dir(), 'Local replay screenshots are not distributed; see tests/README.md')
class DiamondFixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        c=Offline();c.post_connection().wait();cls.reco=MaaRecognizer(ROOT/'assets',c)
        def read(path):
            with Image.open(ROOT/path) as im:return np.asarray(im.convert('RGB'))[:,:,::-1].copy()
        cls.frames={n:read(f'tests/fixtures/diamonds_20261017/frame_{n:03}.jpg') for n in (4,5,6,7)}
        cls.actual={**cls.frames,5:read('tests/fixtures/diamond_fix_20261018/reward.png'),
                    6:read('tests/fixtures/diamond_fix_20261018/shop.png')}
        cls.food=read('tests/fixtures/food_20261018/reward-maa.png')

    @classmethod
    def tearDownClass(cls):cls.reco.cancel().wait()

    def run_replay(self,frames,initial,config=None):
        backend=VideoBackend(self.reco,frames,initial)
        now=[100.]
        def clock():now[0]+=.3;return now[0]
        runner=Runner(backend,config or build_diamond_profile(),Mock(),False,clock)
        with patch.object(runner.stop_event,'wait',return_value=False):runner.run()
        return runner,backend

    def test_old_reward_rule_reproduces_reported_miss(self):
        old=json.loads((ROOT/'config/profiles/diamonds.json').read_text(encoding='utf8'))
        self.assertIsNone(self.reco.recognize(self.actual[5],old['states']['ReturnHome']['rules'][0],threading.Event()))

    def test_current_reward_resume_confirms_then_closes_and_verifies_home(self):
        runner,backend=self.run_replay(self.actual,5)
        self.assertTrue(runner.completed,runner.reason)
        self.assertEqual([p for p,_ in backend.clicks],[5,6])
        self.assertEqual(runner.counters.get('diamond_rewards'),1)
        self.assertEqual(runner.cooldown_estimate.text,'01:57:50')
        self.assertEqual(backend.page,7)
        self.assertEqual(runner.stats.rounds,0)

    def test_current_shop_cooling_only_closes(self):
        runner,backend=self.run_replay(self.actual,6)
        self.assertTrue(runner.completed,runner.reason)
        self.assertEqual([p for p,_ in backend.clicks],[6])
        self.assertEqual(runner.counters.get('diamond_rewards',0),0)

    def test_updated_evidence_preserves_old_video_full_claim(self):
        runner,backend=self.run_replay(self.frames,7)
        self.assertTrue(runner.completed,runner.reason)
        self.assertEqual([p for p,_ in backend.clicks],[7,4,5,6])

    def test_reward_overlay_blocks_shop_close_and_free_claim(self):
        profile=build_diamond_profile()
        for rule in (profile['states']['InspectPack']['rules'][1],profile['states']['CloseShop']['rules'][0]):
            self.assertIsNone(self.reco.recognize(self.actual[5],rule,threading.Event()))

    def test_food_reward_and_wrong_diamond_quantity_cannot_be_acknowledged(self):
        wrong=self.actual[5].copy();wrong[381:405,710:745]=0
        rule=build_diamond_profile()['states']['Reward']['rules'][0]
        for frame in (self.food,wrong,self.actual[6]):
            self.assertIsNone(self.reco.recognize(frame,rule,threading.Event()))

    def test_restart_dungeon_entry_recovers_reward_and_shop_without_completing_dungeon(self):
        base=json.loads((ROOT/'config/profiles/dungeon_unlimited.json').read_text(encoding='utf8'))
        plan=load_plan();profile=build_dungeon_profile(base,plan['tasks'][0],plan['tasks'])
        rules=profile['states']['Entry']['rules'][:len(entry_recovery_rules())]
        self.assertEqual(rules,entry_recovery_rules())
        # Stop this offline replay at home; do not enter a dungeon in the fixture.
        config=build_cleanup_profile()
        runner,backend=self.run_replay(self.actual,5,config)
        self.assertTrue(runner.completed,runner.reason)
        self.assertEqual([p for p,_ in backend.clicks],[5,6])
        self.assertEqual(runner.stats.rounds,0)
        self.assertEqual(runner.counters,{})

if __name__=='__main__':unittest.main()
