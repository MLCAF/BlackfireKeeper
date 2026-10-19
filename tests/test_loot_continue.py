"""Replay the 14:03 five-dungeon loot stall without accessing the game."""
import copy
import json
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock,patch

import numpy as np
from PIL import Image

from bk2.automation import load_plan
from bk2.battle import BattleRunner
from bk2.profiles import build_dungeon_profile
from vendor_core.backend import MaaRecognizer
from vendor_core.config import validate
from tests.test_unified_replay import Offline

ROOT=Path(__file__).resolve().parents[1]


class LootContinueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        controller=Offline(); controller.post_connection().wait()
        cls.reco=MaaRecognizer(ROOT/'assets',controller)
        cls.plan=load_plan()
        cls.base=json.loads((ROOT/'config/profiles/dungeon_unlimited.json').read_text(encoding='utf8'))
        cls.config=build_dungeon_profile(cls.base,cls.plan['tasks'][4],cls.plan['tasks'])
        def read(name):
            with Image.open(ROOT/name) as im:return np.asarray(im.convert('RGB'))[:,:,::-1].copy()
        cls.frames={name:read('tests/fixtures/loot_continue_20261018/'+name+'.png')
                    for name in ('reward','reward_previous','reward_warning')}
        cls.home=read('tests/fixtures/diamonds_20261017/frame_007.jpg')
        cls.negatives=[read(path) for path in (
            'tests/fixtures/diamond_fix_20261018/reward.png',
            'tests/fixtures/food_20261018/reward-maa.png',
            'tests/fixtures/defeat_20261018/defeat.png',
            'tests/fixtures/capture_20261018/room.png')]
        cls.rule=next(r for r in cls.config['states']['Progress']['rules'] if r['template']=='战利品小字继续.png')
    @classmethod
    def tearDownClass(cls):cls.reco.cancel().wait()

    def test_old_rule_reproduces_miss_on_all_three_failure_frames(self):
        old=next(r for r in self.base['states']['Progress']['rules'] if r['template']=='战利品继续文字.png')
        for frame in self.frames.values():
            self.assertIsNone(self.reco.recognize(frame,old,threading.Event()))

    def test_new_guarded_rule_matches_current_previous_and_warning_frames(self):
        for name,frame in self.frames.items():
            match=self.reco.recognize(frame,self.rule,threading.Event())
            self.assertIsNotNone(match,name)
            self.assertGreaterEqual(match.score,.95)
            x,y,w,h=match.box
            self.assertTrue(490<=x+w//2<=904 and 515<=y+h//2<=565)
            print(f'LOOT_REPLAY {name}: {match.score:.6f} box={match.box}')

    def test_missing_title_missing_button_dim_overlay_and_other_rewards_rejected(self):
        no_title=self.frames['reward'].copy(); no_title[125:160,640:755]=0
        no_button=self.frames['reward'].copy(); no_button[520:555,670:725]=0
        dim=(self.frames['reward']*.3).astype(np.uint8)
        for frame in self.negatives+[self.home,no_title,no_button,dim]:
            self.assertIsNone(self.reco.recognize(frame,self.rule,threading.Event()))

    def replay(self, entry=False, stuck=False):
        outer=self
        class Backend:
            def __init__(self):self.page='reward';self.clicks=[];self.save_diagnostic=Mock(return_value='offline.json')
            def guard(self):pass
            def cancel(self):pass
            def capture_is_fresh(self):return True
            def capture(self,stop):return outer.frames['reward'] if self.page=='reward' else outer.home
            def recognize(self,*args):return outer.reco.recognize(*args)
            def click(self,match,offset,stop,pause):
                if stop.is_set() or pause.is_set():raise InterruptedError()
                x,y,w,h=match.box;point=(x+w//2+offset[0],y+h//2+offset[1])
                assert 490<=point[0]<=904 and 515<=point[1]<=565,point
                self.clicks.append(point)
                if not stuck:self.page='home'
        backend=Backend();config=copy.deepcopy(self.config)
        state='Entry' if entry else 'Progress'
        config.update(initial_state=state,max_minutes=1)
        # Synthetic successor to finish the offline harness, not a claim about
        # the real game's next page. The production rule stays in its state.
        config['states'][state]['rules'].insert(0,dict(name='回放终点',template='主界面锚点.png',
            threshold=.95,min_contrast_ratio=.65,action='none',event='task_complete',foreground=True,confirm_frames=2))
        now=[100.]
        def clock():now[0]+=.2;return now[0]
        runner=BattleRunner(backend,config,Mock(),False,clock)
        runner.battle_restarts=2
        with patch.object(runner.stop_event,'wait',return_value=False):runner.run()
        return runner,backend

    def test_real_recognition_clicks_once_counts_battle_not_dungeon_and_resets_retry(self):
        runner,backend=self.replay()
        self.assertTrue(runner.completed,runner.reason)
        self.assertEqual(len(backend.clicks),1)
        self.assertEqual(runner.counters.get('battles_completed'),1)
        self.assertEqual(runner.stats.rounds,0)
        self.assertEqual(runner.battle_restarts,0)

    def test_entry_cleanup_does_not_count_old_loot_as_a_new_battle(self):
        runner,backend=self.replay(entry=True)
        self.assertTrue(runner.completed,runner.reason)
        self.assertEqual(len(backend.clicks),1)
        self.assertEqual(runner.counters,{})
        self.assertEqual(runner.stats.rounds,0)

    def test_unchanged_button_stops_after_two_clicks_without_counting(self):
        runner,backend=self.replay(stuck=True)
        self.assertEqual(len(backend.clicks),2)
        self.assertFalse(runner.completed)
        self.assertEqual(runner.counters.get('battles_completed',0),0)
        self.assertIn('连续 2 次',runner.reason)
        self.assertFalse(runner.guard_enabled)
        self.assertEqual(runner.action_guard,{})
        backend.save_diagnostic.assert_called_once()

    def test_original_variant_and_source_are_preserved_all_profiles_validate(self):
        old=next(r for r in self.base['states']['Progress']['rules'] if r['template']=='战利品继续文字.png')
        actual=copy.deepcopy(next(r for r in self.config['states']['Progress']['rules'] if r['template']=='战利品继续文字.png'))
        actual.pop('battle_victory',None)
        self.assertEqual(old,actual)
        for task in self.plan['tasks']:
            if task['enabled']:validate(build_dungeon_profile(self.base,task,self.plan['tasks']),ROOT/'assets/image',True)
        self.assertEqual(self.base,json.loads((ROOT/'config/profiles/dungeon_unlimited.json').read_text(encoding='utf8')))


if __name__=='__main__':unittest.main()
