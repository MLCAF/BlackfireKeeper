"""Actual failure frames plus deterministic fake-input retry boundaries."""
import copy
import json
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image

from bk2.automation import Batch, load_plan
from bk2.battle import BattleRunner, DEFEAT
from bk2.profiles import build_dungeon_profile
from bk2.scheduler import MemoryScheduler
from vendor_core.backend import MaaRecognizer, Match
from vendor_core.config import validate
from tests.test_unified_replay import Offline

ROOT = Path(__file__).resolve().parents[1]


def profile():
    plan = load_plan()
    base = json.loads((ROOT/'config/profiles/dungeon_unlimited.json').read_text(encoding='utf8'))
    config = build_dungeon_profile(base, plan['tasks'][0], plan['tasks'])
    config.update(initial_state='Progress', max_minutes=1)
    return config


class Clock:
    def __init__(self): self.now = 100.
    def __call__(self): self.now += .2; return self.now


class DefeatBackend:
    """Fake battles end after two post-click frames; never accesses the game."""
    def __init__(self, reco=None, frames=None, failures=4, stuck=False, exit_page='list'):
        self.reco, self.frames = reco, frames
        self.page, self.count = 'defeat', 0
        self.failures, self.stuck, self.exit_page = failures, stuck, exit_page
        self.transient = 0
        self.clicks = []
        self.runner = None
        self.save_diagnostic = Mock(return_value='offline-diagnostic.json')
    def guard(self): pass
    def cancel(self): pass
    def capture_is_fresh(self): return True
    def capture(self, stop):
        page = self.page
        if page == 'battle':
            self.transient -= 1
            if self.transient == 0:
                self.page = 'defeat' if self.count < self.failures else 'victory'
        if self.frames: return self.frames[page]
        return np.full((720,1395,3), {'defeat':1,'battle':2,'list':3,'victory':4,'unknown':5,'black':0}[page],np.uint8)
    def recognize(self, frame, rule, stop):
        if self.reco: return self.reco.recognize(frame,rule,stop)
        page = int(frame[0,0,0])
        template = rule['template']
        if page == 1 and template in (DEFEAT['template'],'战败重新开始文字.png','战败放弃冒险文字.png'):
            return Match((750 if template=='战败放弃冒险文字.png' else 565,580,80,24),1.)
        if page == 3 and template == '地下城导航选中.png': return Match((535,590,108,122),1.)
        if page == 4 and rule.get('battle_victory'): return Match((600,450,150,40),1.)
        return None
    def click(self, match, offset, stop, pause):
        if stop.is_set() or pause.is_set(): raise InterruptedError()
        if self.page == 'defeat':
            action = 'abandon' if match.box[0] > 700 else 'restart'
            self.clicks.append(action)
            if action == 'restart':
                self.count += 1
                if not self.stuck: self.page, self.transient = 'battle', 2
            elif not self.stuck: self.page = self.exit_page
        elif self.page == 'victory':
            self.clicks.append('victory')
            self.page = 'list'
        else: raise AssertionError('Unexpected click '+self.page)


@unittest.skipUnless((ROOT/'tests/fixtures').is_dir(), 'Local replay screenshots are not distributed; see tests/README.md')
class DefeatRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        controller = Offline(); controller.post_connection().wait()
        cls.reco = MaaRecognizer(ROOT/'assets',controller)
        def read(name):
            with Image.open(ROOT/name) as im: return np.asarray(im.convert('RGB'))[:,:,::-1].copy()
        cls.frames = {
            'defeat':read('tests/fixtures/defeat_20261018/defeat.png'),
            'previous':read('tests/fixtures/defeat_20261018/defeat_previous.png'),
            'battle':read('tests/fixtures/capture_20261018/room.png'),
            'list':read('tests/fixtures/failures_20261003/dungeon_list.png'),
            'reward':read('tests/fixtures/diamond_fix_20261018/reward.png'),
            'food':read('tests/fixtures/food_20261018/reward-maa.png'),
        }
    @classmethod
    def tearDownClass(cls): cls.reco.cancel().wait()

    def run_fake(self, backend, config=None, dry=False):
        runner = BattleRunner(backend,config or profile(),Mock(),dry,Clock())
        backend.runner = runner
        with patch.object(runner.stop_event,'wait',return_value=False), \
                patch('bk2.battle.MaaBackend.is_nearly_uniform',side_effect=lambda im:not im.any()):
            runner.run()
        return runner

    def test_actual_and_previous_defeat_frames_match_both_guarded_buttons(self):
        for frame in ('defeat','previous'):
            for rule in profile()['states']['Progress']['rules'][:2]:
                match = self.reco.recognize(self.frames[frame],rule,threading.Event())
                self.assertIsNotNone(match,(frame,rule['name']))
                self.assertGreater(match.box[1],560)

    def test_other_pages_and_buttons_without_defeat_badge_rejected(self):
        no_badge = self.frames['defeat'].copy(); no_badge[280:350,580:820] = 0
        for frame in [self.frames[k] for k in ('battle','list','reward','food')]+[no_badge]:
            for rule in profile()['states']['Progress']['rules'][:2]:
                self.assertIsNone(self.reco.recognize(frame,rule,threading.Event()))

    def test_real_recognition_three_restarts_then_abandon_no_completed_round(self):
        backend = DefeatBackend(self.reco,self.frames)
        runner = self.run_fake(backend)
        self.assertEqual(backend.clicks,['restart']*3+['abandon'],runner.reason)
        self.assertIn('已确认放弃冒险并返回',runner.reason)
        self.assertEqual(runner.stats.rounds,0)
        self.assertFalse(runner.completed)
        self.assertTrue(runner.abandoned)
        self.assertEqual(runner.battle_restarts,3)
        backend.save_diagnostic.assert_not_called()  # Batch saves the classified abandonment report.

    def test_success_on_any_retry_does_not_abandon_and_resets_next_battle_budget(self):
        for failures in (1,2,3):
            backend = DefeatBackend(failures=failures)
            runner = self.run_fake(backend)
            self.assertEqual(backend.clicks,['restart']*failures+['victory'],runner.reason)
            self.assertEqual(runner.battle_restarts,0)
            self.assertEqual(runner.stats.rounds,0)
            self.assertIn('restart',[r.get('defeat_action') for r in runner.ordered_rules()])
            self.assertNotIn('abandon',[r.get('defeat_action') for r in runner.ordered_rules()])

    def test_unchanged_defeat_gets_one_click_and_diagnostic(self):
        backend = DefeatBackend(stuck=True)
        runner = self.run_fake(backend)
        self.assertEqual(backend.clicks,['restart'])
        self.assertEqual(runner.battle_restarts,1)
        self.assertIn('未连续两帧',runner.reason)
        backend.save_diagnostic.assert_called_once()

    def test_abandon_unknown_destination_does_not_report_exit_or_success(self):
        backend = DefeatBackend(exit_page='unknown')
        runner = self.run_fake(backend)
        self.assertEqual(backend.clicks,['restart']*3+['abandon'])
        self.assertIn('超时',runner.reason)
        self.assertNotIn('已放弃冒险并返回',runner.reason)
        self.assertEqual(runner.stats.rounds,0)
        self.assertFalse(runner.completed)

    def test_black_after_click_is_not_new_attempt(self):
        backend = DefeatBackend()
        original = backend.click
        def click(*args): original(*args); backend.page='black'
        backend.click = click
        runner = self.run_fake(backend)
        self.assertEqual(backend.clicks,['restart'])
        self.assertIn('未连续两帧',runner.reason)

    def test_preview_never_clicks_or_counts(self):
        backend = DefeatBackend()
        runner = self.run_fake(backend,dry=True)
        self.assertEqual(backend.clicks,[])
        self.assertEqual(runner.battle_restarts,0)

    def test_pause_after_click_stops_without_submitting_a_second_restart(self):
        backend = DefeatBackend()
        original = backend.click
        def click(*args): original(*args); backend.runner.pause_event.set()
        backend.click = click
        runner = self.run_fake(backend)
        self.assertEqual(backend.clicks,['restart'])
        self.assertIn('暂停',runner.reason)

    def test_all_enabled_profiles_validate_and_base_is_unchanged(self):
        plan = load_plan()
        base = json.loads((ROOT/'config/profiles/dungeon_unlimited.json').read_text(encoding='utf8'))
        before = copy.deepcopy(base)
        for task in plan['tasks']:
            if task['enabled']: validate(build_dungeon_profile(base,task,plan['tasks']),ROOT/'assets/image',True)
        self.assertEqual(base,before)

    def test_unverified_failure_still_stops_without_counting(self):
        plan = load_plan(); plan['diamonds']['enabled']=False
        run = Mock(return_value=(False,'放弃冒险后未确认退出'))
        scheduler = MemoryScheduler(plan,run,Mock(),threading.Event(),Mock(),clock=lambda:1000.)
        for task in scheduler.tasks:
            scheduler.ready_at[task['id']] = 0
        scheduler.service_boundary = Mock(); scheduler.read_stamina = lambda:200
        scheduler.run()
        run.assert_called_once()
        self.assertEqual(scheduler.completed_runs,0)
        self.assertFalse(scheduler.succeeded)

    def test_batch_uses_battle_runner_for_dungeons_only(self):
        session = SimpleNamespace(stop_event=threading.Event(),operation_stop=threading.Event(),
            diagnostic_context={},ledger=SimpleNamespace(finished_transitions=0),publish=Mock(),
            runner=None,save_diagnostic=Mock())
        batch = Batch(session,load_plan(),Mock(),dry_run=True)
        for dungeon in (True,False):
            with patch('bk2.automation.BattleRunner') as battle, patch('bk2.automation.Runner') as plain:
                batch.execute(profile(),dungeon=dungeon)
                self.assertEqual(battle.call_count, int(dungeon))
                self.assertEqual(plain.call_count, int(not dungeon))


if __name__=='__main__': unittest.main()
