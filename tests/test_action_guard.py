"""Fault injection for shared guards; no game input or live memory access."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from bk2.action_guard import GuardedRunner, guarded_profile
from bk2.recovery import RecoveryRunner
from bk2.battle import BattleRunner
from bk2.food_profile import build_food_profile
from bk2.diamond_profile import build_diamond_profile
from vendor_core.backend import Match
from vendor_core.config import validate
from tests.upstream.test_runner import FakeBackend, Clock

ROOT = Path(__file__).resolve().parents[1]
MATCH = Match((10, 20, 30, 40), .99)


def profile(single=False, positive=True):
    rule = dict(name='guarded action', template='钻石免费按钮.png' if single else 'button',
                action='click', verify_change=True, threshold=.95, offset=[0, 16], next='Done')
    if positive:
        rule['success_match'] = dict(template='destination', threshold=.96)
    return dict(initial_state='Action', max_minutes=0, poll_seconds=.001,
                states={'Action':dict(timeout_seconds=60, rules=[rule]),
                        'Done':dict(timeout_seconds=30, rules=[dict(name='done',template='destination',
                                    action='none',event='task_complete',confirm_frames=2)])})


class ActionGuardTests(unittest.TestCase):
    def make(self, frames, single=False, positive=True, backend=None):
        b = backend or FakeBackend(frames)
        r = GuardedRunner(b, profile(single, positive), Mock(), False, Clock())
        return r, b, r.config['states']['Action']['rules'][0]

    def invoke(self, r, rule):
        with patch.object(r.stop_event, 'wait', side_effect=lambda _:r.stop_event.is_set()):
            return r.click_with_change_confirmation(rule, MATCH)

    def test_navigation_lost_first_click_retries_only_once_without_spiral(self):
        r,b,rule = self.make(['button']*10+['destination']*2)
        self.assertTrue(self.invoke(r,rule))
        self.assertEqual(b.clicks,2)
        self.assertEqual(b.click_offsets,[[0,16],[0,16]])

    def test_slow_navigation_does_not_retry_while_waiting(self):
        r,b,rule = self.make(['button']*7+['destination']*2)
        self.assertTrue(self.invoke(r,rule))
        self.assertEqual(b.clicks,1)

    def test_navigation_two_lost_clicks_fail_closed(self):
        r,b,rule = self.make(['button'])
        with self.assertRaisesRegex(RuntimeError,'停止重试'):
            self.invoke(r,rule)
        self.assertEqual(b.clicks,2)
        self.assertEqual(r.action_guard['status'],'stopped')
        self.assertEqual(b.action_guard_context,r.action_guard)

    def test_single_submit_waits_for_delayed_reward(self):
        r,b,rule = self.make(['钻石免费按钮.png']*40+['destination']*2,single=True)
        self.assertTrue(self.invoke(r,rule))
        self.assertEqual(b.clicks,1)
        self.assertEqual(r.action_guard['wait_seconds'],15)

    def test_single_submit_never_retries_even_if_source_still_present(self):
        r,b,rule = self.make(['钻石免费按钮.png'],single=True)
        with self.assertRaisesRegex(RuntimeError,'不重复提交'):
            self.invoke(r,rule)
        self.assertEqual(b.clicks,1)

    def test_one_frame_successor_flash_does_not_complete(self):
        r,b,rule = self.make(['destination','button']*10)
        with self.assertRaises(RuntimeError): self.invoke(r,rule)
        self.assertEqual(b.clicks,1)
        self.assertNotEqual(r.action_guard.get('status'),'confirmed')

    def test_unknown_page_is_not_retried_or_certified_as_successor(self):
        r,b,rule = self.make(['unknown'])
        with self.assertRaises(RuntimeError): self.invoke(r,rule)
        self.assertEqual(b.clicks,1)

    def test_no_successor_requires_two_consecutive_absent_frames(self):
        r,b,rule = self.make([set(),'button',set(),set()],positive=False)
        self.assertTrue(self.invoke(r,rule))
        self.assertEqual(b.index,4)
        self.assertEqual(r.action_guard['confirmation'],'target_left_two_frames')

    def test_black_frames_cannot_certify_disappearance(self):
        import numpy as np
        r,b,rule = self.make([np.zeros((80,80,3),dtype='uint8')],positive=False)
        with self.assertRaises(RuntimeError): self.invoke(r,rule)
        self.assertEqual(b.clicks,1)

    def test_obscured_source_does_not_enable_retry(self):
        r,b,rule = self.make(['button'])
        rule['requires_match'] = {'template':'source-title'}
        with self.assertRaises(RuntimeError): self.invoke(r,rule)
        self.assertEqual(b.clicks,1)

    def test_dimmed_source_does_not_count_as_disappearance(self):
        r,b,rule = self.make(['button'],positive=False)
        rule['min_brightness_ratio'] = .85
        original = b.recognize
        b.recognize = lambda frame,rule,stop: None if 'min_brightness_ratio' in rule else original(frame,rule,stop)
        with self.assertRaises(RuntimeError): self.invoke(r,rule)
        self.assertEqual(b.clicks,1)

    def test_pause_or_stop_after_submit_prevents_retry(self):
        for action in ('pause','stop'):
            with self.subTest(action=action):
                r,b,rule = self.make(['button'])
                click = b.click
                def interrupt(*args):
                    click(*args)
                    (r.pause_event if action=='pause' else r.stop_event).set()
                b.click = interrupt
                with self.assertRaises((InterruptedError,RuntimeError)): self.invoke(r,rule)
                self.assertEqual(b.clicks,1)

    def test_stale_source_disappearing_before_click_sends_nothing(self):
        r,b,rule = self.make(['unknown'])
        b.fresh_results = [False]
        self.assertFalse(self.invoke(r,rule))
        self.assertEqual(b.clicks,0)

    def test_interrupt_retains_single_submit_budget_on_reentry(self):
        r,b,rule = self.make(['modal'],single=True)
        r.config['interrupts'] = [dict(name='modal',template='modal')]
        self.assertFalse(self.invoke(r,rule))
        b.frames = ['钻石免费按钮.png']
        with self.assertRaisesRegex(RuntimeError,'不重复提交'): self.invoke(r,rule)
        self.assertEqual(b.clicks,1)

    def test_late_success_after_interrupt_needs_no_extra_click(self):
        r,b,rule = self.make(['modal'],single=True)
        r.config['interrupts'] = [dict(name='modal',template='modal')]
        self.assertFalse(self.invoke(r,rule))
        b.frames = ['destination']
        self.assertTrue(self.invoke(r,rule))
        self.assertEqual(b.clicks,1)
        self.assertFalse(r._action_attempts)

    def test_aliases_share_budget_after_interrupt(self):
        r,b,rule = self.make(['modal'])
        r.config['interrupts'] = [dict(name='modal',template='modal')]
        self.assertFalse(self.invoke(r,rule))
        alias = dict(rule,name='alias',template='alternate')
        b.frames = ['alternate']
        with self.assertRaises(RuntimeError): self.invoke(r,alias)
        self.assertEqual(b.clicks,2)

    def test_uncertain_backend_failure_reserves_submission_budget(self):
        r,b,rule = self.make(['钻石免费按钮.png'],single=True)
        click = b.click
        def uncertain(*args):
            click(*args)
            raise RuntimeError('sent but backend response lost')
        b.click = uncertain
        with self.assertRaisesRegex(RuntimeError,'backend'): self.invoke(r,rule)
        with self.assertRaisesRegex(RuntimeError,'不重复提交'): self.invoke(r,rule)
        self.assertEqual(b.clicks,1)

    def test_delayed_two_frame_transition_releases_old_budget(self):
        r,b,rule = self.make(['modal'])
        r.config['interrupts'] = [dict(name='modal',template='modal')]
        self.assertFalse(self.invoke(r,rule))
        delayed, _ = r.pending_success('destination')
        r.record('transition',previous='Action',next='Done',rule=delayed['name'])
        self.assertFalse(r._action_attempts)
        self.assertEqual(r.action_guard['confirmation'],'delayed_successor')

    def test_non_food_recovery_and_battle_use_original_verifier(self):
        for runner_type in (RecoveryRunner,BattleRunner):
            with self.subTest(runner=runner_type.__name__):
                b = FakeBackend(['button']*2+['destination']*3)
                r = runner_type(b,profile(),Mock(),False,Clock())
                with patch.object(r.stop_event,'wait',return_value=False): r.run()
                self.assertTrue(r.completed,r.reason)
                self.assertEqual(b.clicks,1)
                self.assertFalse(r.guard_enabled)
                self.assertEqual(r.action_guard,{})
                self.assertNotIn('action_guard',r.config['states']['Action']['rules'][0])

    def test_all_dungeon_profiles_preserve_thresholds_and_offsets(self):
        from bk2.automation import load_plan
        from bk2.profiles import build_dungeon_profile
        plan = load_plan()
        base = json.loads((ROOT/'config/profiles/dungeon_unlimited.json').read_text(encoding='utf-8-sig'))
        for task in plan['tasks'][:6]:
            config = build_dungeon_profile(base,task,plan['tasks'])
            before = copy.deepcopy(config)
            guarded = guarded_profile(config)
            self.assertEqual(config,before)
            validate(guarded,ROOT/'assets/image',True)
            for state in config['states']:
                for old,new in zip(config['states'][state]['rules'],guarded['states'][state]['rules']):
                    for field in ('threshold','roi','offset','requires_match'):
                        self.assertEqual(old.get(field),new.get(field))
                    if new.get('action_guard'):
                        self.assertLessEqual(new['retry_max_clicks'],2)
                        self.assertEqual(new['retry_radius'],0)
                    if old.get('defeat_action') or old.get('recovery_action'):
                        self.assertEqual(old,new)
            interrupt = next(r for r in guarded['interrupts'] if r['template']=='任务领取奖励文字.png')
            self.assertEqual(interrupt['action_guard'],'single_submit')

    def test_food_own_submission_and_final_positive_evidence_preserved(self):
        original = build_food_profile()
        guarded = guarded_profile(original)
        self.assertEqual(original['states']['FoodCollect'],guarded['states']['FoodCollect'])
        self.assertEqual(guarded['states']['FoodAwaitReward']['timeout_seconds'],30)
        close = guarded['states']['FoodClose']['rules'][0]
        self.assertEqual({r['template'] for r in close['guard_success']},
                         {'主界面锚点.png','体力面包入口.png'})
        claim = next(r for r in guarded_profile(build_diamond_profile())['states']['InspectPack']['rules']
                     if r.get('action_guard'))
        self.assertEqual(claim['action_guard'],'single_submit')
        self.assertEqual(claim['retry_max_clicks'],1)

    def test_full_food_flow_retries_close_without_resubmitting_food(self):
        from tests.test_food_close import FoodCloseTests
        with patch('tests.test_food_close.Runner',RecoveryRunner):
            r,b = FoodCloseTests().run_flow(full=True,lost=1)
        self.assertTrue(r.completed,r.reason)
        self.assertEqual(b.clicks,4)
        self.assertEqual(r.counters['food_collection_requests'],1)
        self.assertEqual(r.counters['food_collections'],1)

    def test_food_unknown_destination_stops_at_close_without_false_done(self):
        from tests.test_food_close import FoodCloseTests
        with patch('tests.test_food_close.Runner',RecoveryRunner):
            r,b = FoodCloseTests().run_flow(lost=0,destination=set())
        self.assertFalse(r.completed)
        self.assertEqual(r.stats.state,'FoodClose')
        self.assertEqual(b.clicks,1)

    def test_delayed_food_response_keeps_existing_single_request_protocol(self):
        from tests.test_food_response import FoodResponseTests
        with patch('tests.test_food_response.Runner',RecoveryRunner):
            r,b = FoodResponseTests().run_flow(delay=5)
        self.assertTrue(r.completed,r.reason)
        self.assertEqual(b.clicks,3)
        self.assertEqual(r.counters['food_collection_requests'],1)

    def test_backend_diagnostic_keeps_action_guard_evidence(self):
        from bk2.backend import DiagnosticBackend
        from vendor_core.backend import MaaBackend
        backend = DiagnosticBackend.__new__(DiagnosticBackend)
        backend.action_guard_context = dict(status='stopped',attempts=1,max_attempts=1)
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder)/'report.json'
            target.write_text('{"reason":"example"}',encoding='utf-8')
            with patch.object(MaaBackend,'save_diagnostic',return_value=str(target)):
                backend.save_diagnostic('Action','example',{})
            report = json.loads(target.read_text(encoding='utf-8'))
        self.assertEqual(report['action_guard'],backend.action_guard_context)
        self.assertEqual(report['reason'],'example')

    def test_session_diagnostic_keeps_action_guard_evidence(self):
        from bk2.runtime import Session
        r,b,rule = self.make(['button'])
        with self.assertRaises(RuntimeError): self.invoke(r,rule)
        session = Session()
        session.runner = r
        with patch('bk2.diagnostics.save_failure',return_value=Path('mock.json')) as save, \
                patch.object(session,'publish'):
            session.save_diagnostic('test','unconfirmed')
        self.assertEqual(save.call_args.args[5]['action_guard'],r.action_guard)


@unittest.skipUnless((ROOT/'tests/fixtures/food_close_20261019/panel.png').is_file(),
                     'Private screenshots are not distributed')
class ActionGuardReplayTests(unittest.TestCase):
    def test_real_bakery_lost_close_then_verified_destination(self):
        from tests.test_food_close import FoodCloseReplayTests
        FoodCloseReplayTests.setUpClass()
        try:
            with patch('tests.test_food_close.Runner',RecoveryRunner):
                FoodCloseReplayTests().test_real_failure_frame_one_lost_click_then_verified_list()
        finally: FoodCloseReplayTests.tearDownClass()

    def test_real_diamond_full_claim_reward_confirmation_and_close(self):
        from tests.test_diamond_fix import DiamondFixTests
        DiamondFixTests.setUpClass()
        try:
            with patch('tests.test_diamond_fix.Runner',RecoveryRunner):
                DiamondFixTests().test_updated_evidence_preserves_old_video_full_claim()
        finally: DiamondFixTests.tearDownClass()

    def test_real_diamond_interrupted_reward_resume_and_close(self):
        from tests.test_diamond_fix import DiamondFixTests
        DiamondFixTests.setUpClass()
        try:
            with patch('tests.test_diamond_fix.Runner',RecoveryRunner):
                DiamondFixTests().test_current_reward_resume_confirms_then_closes_and_verifies_home()
        finally: DiamondFixTests.tearDownClass()


if __name__ == '__main__': unittest.main()
