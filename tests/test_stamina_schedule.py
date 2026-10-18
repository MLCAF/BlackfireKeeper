"""Boundary scheduling with a virtual clock: never sends game input."""
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from bk2.automation import Batch, load_plan, validate_food_options
from bk2.food import FoodTimerStore
from bk2.scheduler import MemoryScheduler
from tests.test_migration import session


class StaminaScheduleTests(unittest.TestCase):
    def setUp(self):
        self.plan = load_plan()
        self.plan['run_control'] = dict(mode='stamina', minimum_stamina=100)
        self.plan['max_total_runs'] = 1
        self.plan['diamonds']['enabled'] = False
        for task in self.plan['tasks']:
            task['enabled'] = task['order'] == 1
        self.now = 1792300000.
        self.start = self.now
        self.stop = threading.Event()
        self.events = []

    def scheduler(self, run):
        def wait(seconds):
            self.now += seconds
            if self.now > self.start+4000:
                raise AssertionError('Unexpected unbounded scheduling')
            return self.stop.is_set()
        s = MemoryScheduler(self.plan, run, Mock(return_value=(True,'ok')), self.stop,
            lambda kind, value:self.events.append((kind,value)), clock=lambda:self.now, wait=wait)
        s.service_boundary = Mock()
        s.read_stamina = Mock(return_value=100)
        return s

    def test_below_threshold_waits_exact_threshold_runs_and_refill_resumes_without_round_limit(self):
        state = {'count':99,'runs':0,'next_food':self.start+1800}
        calls = []
        def boundary():
            if self.now >= state['next_food']:
                calls.append(('food', self.now))
                state['count'] = 100
                state['next_food'] += 1800
            if state['runs'] == 2:
                self.stop.set()
        def run(task):
            self.assertEqual(state['count'],100)
            calls.append(('dungeon',self.now))
            state['count'] -= 5
            state['runs'] += 1
            return True,'ok'
        s=self.scheduler(run);s.service_boundary=boundary;s.read_stamina=lambda:state['count']
        s.run()
        self.assertEqual(calls,[('food',self.start+1800),('dungeon',self.start+1800),
                                ('food',self.start+3600),('dungeon',self.start+3600)])
        self.assertEqual(s.completed_runs,2)  # legacy max_total_runs=1 is ignored
        self.assertTrue(any(k=='schedule' and v.get('kind')=='stamina' for k,v in self.events))

    def test_diamonds_due_during_dungeon_run_before_food_even_when_low(self):
        calls=[];stamina=[100]
        def run(task):
            calls.append('dungeon');self.now+=1801;stamina[0]=95
            return True,'ok'
        s=self.scheduler(run);self.plan['diamonds']['enabled']=True;s.diamond_due=self.start+1800
        s.read_stamina=lambda:stamina[0]
        def diamonds():calls.append('diamonds');return True,'ok'
        def food():
            if self.now>=self.start+1800:
                calls.append('food');self.stop.set()
        s.run_diamonds=diamonds;s.service_boundary=food;s.run()
        self.assertEqual(calls,['dungeon','diamonds','food'])

    def test_diamond_deadline_crossed_in_food_is_rechecked_before_dungeon(self):
        calls=[]
        s=self.scheduler(lambda task:(calls.append('dungeon') or self.stop.set() or True,'ok'))
        self.plan['diamonds']['enabled']=True;s.diamond_due=self.start+1
        def food():
            if not calls: calls.append('food');self.now+=2
        s.service_boundary=food
        s.run_diamonds=lambda:(calls.append('diamonds') or True,'ok')
        s.run()
        self.assertEqual(calls,['food','diamonds','dungeon'])

    def test_unknown_stamina_stops_before_dungeon(self):
        run=Mock();s=self.scheduler(run)
        s.read_stamina=Mock(side_effect=RuntimeError('体力数值未知'))
        with self.assertRaisesRegex(RuntimeError,'体力数值未知'):s.run()
        run.assert_not_called()

    def test_stop_while_waiting_prevents_food_and_dungeon_after_stop(self):
        run=Mock();s=self.scheduler(run);s.read_stamina=lambda:99
        s.wait=lambda _:self.stop.set() or True
        s.run();run.assert_not_called();self.assertEqual(s.service_boundary.call_count,1)

    def test_food_interrupt_on_user_stop_is_clean(self):
        run=Mock();s=self.scheduler(run)
        def food():self.stop.set();raise InterruptedError('用户停止')
        s.service_boundary=food;s.run();run.assert_not_called()

    def test_threshold_validation(self):
        for value in (True,0,4,321,100.5,'100',None):
            self.plan['run_control']['minimum_stamina']=value
            with self.assertRaises(ValueError):validate_food_options(self.plan)
        for value in (5,100,200,320):
            self.plan['run_control']['minimum_stamina']=value
            self.assertEqual(validate_food_options(self.plan)['run_control']['minimum_stamina'],value)


class FoodIntervalTests(unittest.TestCase):
    def test_half_hour_check_survives_restart_and_claims_after_due(self):
        now=[time.time()];s=session();p=load_plan()
        s.snapshot['data']['food_fields']=dict(bread=320,start_time=(now[0]-5000)*1000)
        with tempfile.TemporaryDirectory() as folder:
            store=FoodTimerStore(Path(folder)/'food.json')
            b=Batch(s,p,Mock(),clock=lambda:now[0],food_store=store)
            b.execute=Mock();b.maybe_food();b.execute.assert_not_called()
            due=now[0]+1800
            self.assertEqual(store.load(now[0]),due)
            s.snapshot['data']['food_fields']['bread']=95
            restarted=Batch(s,p,Mock(),clock=lambda:now[0],food_store=store)
            def execute(config):
                s.snapshot['data']['food_fields']=dict(bread=100,start_time=(now[0]-400)*1000)
                return SimpleNamespace(completed=True,counters={'food_collections':1},reason='ok')
            restarted.execute=Mock(side_effect=execute)
            now[0]=due-1;restarted.maybe_food();restarted.execute.assert_not_called()
            now[0]=due
            self.assertEqual(restarted.maybe_food()['received'],5)
            restarted.maybe_food();self.assertEqual(restarted.execute.call_count,1)
            self.assertEqual(store.load(now[0]),due+1800)

    def test_failed_claim_is_not_repeated_immediately_after_restart(self):
        now=time.time();s=session();p=load_plan()
        s.snapshot['data']['food_fields']=dict(bread=95,start_time=(now-5000)*1000)
        with tempfile.TemporaryDirectory() as folder:
            store=FoodTimerStore(Path(folder)/'food.json')
            b=Batch(s,p,Mock(),clock=lambda:now,food_store=store)
            b.execute=Mock(side_effect=RuntimeError('未确认'))
            with self.assertRaisesRegex(RuntimeError,'未确认'):b.maybe_food()
            restarted=Batch(s,p,Mock(),clock=lambda:now,food_store=store)
            restarted.execute=Mock();restarted.maybe_food();restarted.execute.assert_not_called()

    def test_corrupt_timer_does_not_mean_ready(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'food.json';store=FoodTimerStore(path)
            self.assertEqual(store.load(10000),0)
            for record in ([],{},dict(version=2,next_check=1),dict(version=1,next_check=True),
                           dict(version=1,next_check=-1),dict(version=1,next_check=99999)):
                path.write_text(json.dumps(record),encoding='utf-8')
                with self.assertRaises(ValueError):store.load(10000)


if __name__=='__main__':unittest.main()
