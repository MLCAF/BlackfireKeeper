"""Only food collection opts into the v0.3.0 action protocol in production."""
import copy
import json
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from bk2.automation import load_plan
from bk2.battle import BattleRunner
from bk2.recovery import RecoveryRunner, boundary_profile
from bk2.food_profile import build_food_profile
from bk2.diamond_profile import build_diamond_profile, build_cleanup_profile
from bk2.profiles import build_dungeon_profile
from tests.upstream.test_runner import FakeBackend, Clock
from vendor_core.runner import Runner
from vendor_core.backend import Match

ROOT=Path(__file__).resolve().parents[1]


class GuardScopeTests(unittest.TestCase):
    def assert_original(self,kind,config):
        before=copy.deepcopy(config)
        r=kind(FakeBackend(['none']),config,Mock(),False,Clock())
        self.assertEqual(config,before)
        self.assertFalse(r.guard_enabled)
        self.assertEqual(r.config['states'],before['states'])
        self.assertEqual(r.action_guard,{})
        # Original task interrupts keep their exact click policy.
        for rule in before.get('interrupts',[]):
            if rule.get('requires_match',{}).get('template')=='游戏timeout提示.png':continue
            self.assertIn(rule,r.config['interrupts'])
        return r

    def test_all_six_dungeons_and_rewards_keep_original_rules(self):
        plan=load_plan(); base=json.loads((ROOT/'config/profiles/dungeon_unlimited.json').read_text(encoding='utf-8-sig'))
        for task in plan['tasks'][:6]:
            with self.subTest(task=task['id']):
                c=build_dungeon_profile(base,task,plan['tasks'])
                r=BattleRunner(FakeBackend(['none']),c,Mock(),False,Clock())
                self.assertFalse(r.guard_enabled)
                self.assertEqual(r.config['states'],c['states'])
                self.assertFalse(any(x.get('action_guard') for s in r.config['states'].values() for x in s['rules']))
        for c in [build_diamond_profile(),build_cleanup_profile(),boundary_profile()]:
            self.assert_original(RecoveryRunner,c)

    def test_food_still_has_bounded_close_and_single_collection(self):
        c=build_food_profile(); r=RecoveryRunner(FakeBackend(['none']),c,Mock(),False,Clock())
        self.assertTrue(r.guard_enabled)
        close=r.config['states']['FoodClose']['rules'][0]
        self.assertEqual(close['retry_max_clicks'],2)
        self.assertEqual(close['action_guard'],'navigation')
        self.assertTrue(close['guard_success'])
        self.assertEqual(r.config['states']['FoodCollect'],c['states']['FoodCollect'])
        self.assertEqual(r.config['states']['FoodAwaitReward'],c['states']['FoodAwaitReward'])

    def test_non_food_dispatch_bypasses_guard_even_if_rule_has_old_metadata(self):
        c=build_diamond_profile(); r=self.assert_original(RecoveryRunner,c)
        rule=dict(c['states']['InspectPack']['rules'][1],action_guard='single_submit')
        match=Match((10,20,30,40),1.)
        with patch.object(Runner,'click_with_change_confirmation',return_value=True) as original:
            self.assertTrue(r.click_with_change_confirmation(rule,match))
        original.assert_called_once_with(rule,match)
        self.assertEqual(r.action_guard,{})

    def test_original_delayed_success_is_not_recorded_as_generic_guard(self):
        c=build_diamond_profile();r=self.assert_original(RecoveryRunner,c)
        rule=c['states']['InspectPack']['rules'][1]
        r.pending_action=('InspectPack',rule)
        r.record('transition',previous='InspectPack',next='Reward',rule=rule['name']+' / 延迟完成确认')
        self.assertEqual(r.action_guard,{})


if __name__=='__main__':unittest.main()
