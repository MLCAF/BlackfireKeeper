"""Abandonment is a verified boundary outcome, distinct from success and errors."""
import copy
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from bk2.automation import Batch, load_plan
from bk2.memory import MemoryReadError
from bk2.scheduler import DungeonAbandoned, MemoryCooldown, MemoryScheduler
from tests.test_migration import session


class AbandonSchedulingTests(unittest.TestCase):
    def make(self, mode='stamina', ranks=(2,3), run=None):
        self.plan = load_plan()
        self.plan['run_control'] = dict(mode=mode,minimum_stamina=100)
        self.plan['max_total_runs'] = 1
        self.plan['diamonds'].update(enabled=True,run_on_start=False)
        for task in self.plan['tasks']: task['enabled'] = task['order'] in ranks
        self.now = 10000.
        self.stop = threading.Event()
        self.events = []
        self.waits = []
        def wait(seconds):
            self.waits.append(seconds)
            self.now += seconds
            if len(self.waits)>100:
                raise AssertionError('Unbounded/busy scheduling')
            return self.stop.is_set()
        s = MemoryScheduler(self.plan,run or Mock(),Mock(return_value=(True,'ok')),self.stop,
            lambda k,d:self.events.append((k,d)),clock=lambda:self.now,wait=wait)
        s.service_boundary = Mock()
        s.read_stamina = lambda:200
        for task in s.tasks: s.ready_at[task['id']] = self.now
        return s

    def test_abandon_then_due_diamonds_food_other_selected_dungeon_in_both_modes(self):
        for mode in ('stamina','rounds'):
            with self.subTest(mode=mode):
                s = self.make(mode)
                original = copy.deepcopy(self.plan)
                calls = []
                def run(task):
                    calls.append(task['id'])
                    if task['order']==2:
                        self.now += 1801
                        return DungeonAbandoned(),'exit verified'
                    return True,'won'
                def diamonds(): calls.append('diamonds'); return True,'ok'
                def food():
                    if self.now>=11800 and 'food' not in calls: calls.append('food')
                    if mode=='stamina' and s.completed_runs==1: self.stop.set()
                s.run_dungeon=run; s.run_diamonds=diamonds; s.service_boundary=food
                s.diamond_due=11800
                s.scan_cooldowns=lambda:{'dungeon_2':MemoryCooldown(0),'dungeon_3':MemoryCooldown(0)}
                s.run()
                self.assertEqual(calls,['dungeon_2','diamonds','food','dungeon_3'])
                self.assertEqual(s.completed_runs,1)
                self.assertEqual(s.skipped_dungeons,{'dungeon_2'})
                self.assertEqual(self.plan,original)
                self.assertTrue(any(k=='schedule' and d['phase']=='skipped' for k,d in self.events))

    def test_all_skipped_including_fallback_keeps_reward_tasks_in_both_modes(self):
        for mode in ('stamina','rounds'):
            with self.subTest(mode=mode):
                s=self.make(mode,(1,2)); calls=[]
                s.run_dungeon=lambda task:(calls.append(task['id']) or DungeonAbandoned(),'abandoned')
                s.diamond_due=self.now+5
                s.run_diamonds=lambda:(calls.append('diamonds') or True,'ok')
                def food():
                    if self.now>=10005:
                        calls.append('food'); self.stop.set()
                s.service_boundary=food
                s.run()
                self.assertEqual(calls,['dungeon_2','dungeon_1','diamonds','food'])
                self.assertEqual(s.completed_runs,0)
                self.assertEqual(s.skipped_dungeons,{'dungeon_1','dungeon_2'})
                self.assertEqual(self.waits,[1.]*5)
                self.assertTrue(any(k=='schedule' and d['phase']=='rewards_only' for k,d in self.events))

    def test_skipped_task_not_reenabled_by_memory_or_ocr_or_fallback(self):
        s=self.make(ranks=(1,2,3))
        s.skipped_dungeons.update(('dungeon_1','dungeon_2'))
        s.apply_list_readings({'dungeon_2':MemoryCooldown(0),'dungeon_3':'ready'})
        self.assertEqual(s.choose()['id'],'dungeon_3')
        s.ready_at['dungeon_3']=self.now+60
        self.assertIsNone(s.choose())

    def test_other_dungeon_cooling_waits_without_reentering_skipped(self):
        s=self.make(); calls=[]
        s.ready_at['dungeon_3']=self.now+10
        def run(task):
            calls.append(task['id'])
            if task['order']==2:return DungeonAbandoned(),'abandoned'
            self.stop.set(); return True,'stopped'
        s.run_dungeon=run; s.run()
        self.assertEqual(calls,['dungeon_2','dungeon_3'])
        waits=[d for k,d in self.events if k=='schedule' and d['phase']=='waiting']
        self.assertEqual(waits[0]['task'],'三本')
        self.assertEqual(len(self.waits),10)

    def test_low_stamina_after_abandon_still_services_rewards_before_next_dungeon(self):
        s=self.make(); calls=[]; stamina=[200]
        s.read_stamina=lambda:stamina[0]
        def run(task):
            calls.append(task['id']); stamina[0]=99
            s.diamond_due=self.now
            return DungeonAbandoned(),'abandoned'
        def food():
            if stamina[0]==99: calls.append('food'); self.stop.set()
        s.run_dungeon=run; s.run_diamonds=lambda:(calls.append('diamonds') or True,'ok')
        s.service_boundary=food; s.run()
        self.assertEqual(calls,['dungeon_2','diamonds','food'])
        self.assertEqual(s.completed_runs,0)

    def test_unconfirmed_failure_and_user_stop_do_not_release_followup_tasks(self):
        for user_stop in (False,True):
            s=self.make(); calls=[]
            def run(task):
                s.diamond_due=self.now
                if user_stop:self.stop.set()
                return (DungeonAbandoned() if user_stop else False),'unconfirmed/stopped'
            s.run_dungeon=run; s.run_diamonds=Mock()
            s.service_boundary=lambda:calls.append('boundary')
            s.run()
            s.run_diamonds.assert_not_called()
            self.assertEqual(calls,['boundary'])
            self.assertEqual(s.skipped_dungeons,set())
            self.assertEqual(s.completed_runs,0)

    def test_skip_lifetime_is_one_run_and_checkpoint_clears_inflight(self):
        s=self.make(); store=Mock(); saved={}
        store.save=lambda data:saved.update(copy.deepcopy(data))
        store.load=lambda:copy.deepcopy(saved)
        s.store=store
        s.run_dungeon=lambda task:(DungeonAbandoned(),'abandoned')
        def boundary():
            if s.skipped_dungeons:self.stop.set()
        s.service_boundary=boundary; s.run()
        self.assertIsNone(saved['in_flight'])
        self.assertNotIn('skipped_dungeons',saved)
        next_run=MemoryScheduler(self.plan,Mock(),Mock(),threading.Event(),Mock(),clock=lambda:self.now)
        self.assertEqual(next_run.skipped_dungeons,set())
        self.assertTrue(next(t for t in self.plan['tasks'] if t['id']=='dungeon_2')['enabled'])


class AbandonBatchTests(unittest.TestCase):
    def make(self):
        s=session(); p=load_plan()
        b=Batch(s,p,Mock())
        b.maybe_food=Mock(); b.sync_diamond_deadline=Mock()
        b.memory_times=Mock(return_value={'dungeons':{}})
        b.execute=Mock(return_value=SimpleNamespace(abandoned=True,stats=SimpleNamespace(rounds=0),reason='exit verified'))
        return s,p,b

    def test_ui_and_memory_exit_returns_distinct_result_without_success_ledger(self):
        s,p,b=self.make()
        result,reason=b.dungeon(p['tasks'][1])
        self.assertIsInstance(result,DungeonAbandoned)
        self.assertEqual(s.ledger.finished_transitions,0)
        b.sync_diamond_deadline.assert_called_once()
        self.assertEqual(b.maybe_food.call_count,1)  # Only pre-run boundary; scheduler owns post-abandon priority.

    def test_delayed_memory_exit_is_waited_for(self):
        s,p,b=self.make()
        def execute(*args,**kwargs):
            s.snapshot['data']['dungeon_progress']['result']='in progress'
            return SimpleNamespace(abandoned=True,reason='exit verified')
        b.execute=execute
        s.operation_stop.wait=Mock(side_effect=lambda _:s.snapshot['data'].update(dungeon_progress=None) or False)
        result,_=b.dungeon(p['tasks'][1])
        self.assertIsInstance(result,DungeonAbandoned)
        s.operation_stop.wait.assert_called_once()

    def test_missing_or_stale_memory_after_ui_exit_cannot_release_scheduler(self):
        for stale in (False,True):
            s,p,b=self.make()
            def execute(*args,**kwargs):
                if stale:s.snapshot['captured_monotonic']=time.monotonic()-10
                else:s.snapshot['data'].pop('dungeon_progress')
                return SimpleNamespace(abandoned=True,reason='exit verified')
            b.execute=execute
            with self.assertRaises((RuntimeError,MemoryReadError)):b.dungeon(p['tasks'][1])
            b.sync_diamond_deadline.assert_not_called()

    def test_skip_cooldown_scan_does_not_navigate_to_abandoned_task(self):
        s,p,b=self.make()
        b.scheduler=SimpleNamespace(skipped_dungeons={t['id'] for t in p['tasks']})
        b.execute.reset_mock()
        self.assertEqual(b.scan_cooldowns(),{})
        b.execute.assert_not_called()


if __name__=='__main__': unittest.main()
