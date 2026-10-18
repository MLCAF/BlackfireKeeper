import copy
import json
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from vendor_core.config import validate_dungeon_plan
from vendor_core.unified import UnifiedScheduler
from tests.upstream.test_dungeon_buffs import Value

ROOT = Path(__file__).resolve().parents[2]

def plan_for(*ranks):
    plan = json.loads((ROOT/'config/dungeon_plan.json').read_text(encoding='utf-8'))
    for task in plan['tasks']: task['enabled'] = task['order'] in ranks
    plan['max_total_runs'] = 2
    plan['diamonds']['enabled'] = False
    return plan

class SelectionTests(unittest.TestCase):
    def test_cooled_only_selection_valid(self):
        for ranks in [(2,), (3,5), (1,), (2,3,4,5,6)]:
            validate_dungeon_plan(plan_for(*ranks), ROOT/'config/profiles', ROOT/'assets/image', True)

    def test_empty_selection_rejected(self):
        with self.assertRaisesRegex(ValueError, '至少选择'):
            validate_dungeon_plan(plan_for(), ROOT/'config/profiles', ROOT/'assets/image')
        with self.assertRaisesRegex(ValueError, '至少选择'):
            UnifiedScheduler(plan_for(), Mock(), Mock(), threading.Event(), Mock())

    def scheduler(self, plan, run, diamond=None, now=None, pause=None, wait=None):
        now = now if now is not None else [0.]
        stop = threading.Event()
        def advance(seconds):
            now[0] += seconds
            return stop.is_set()
        s = UnifiedScheduler(plan, run, diamond or Mock(return_value=(True,'ok')), stop, Mock(),
            clock=lambda: now[0], wait=wait or advance, pause_event=pause)
        return s, now

    def test_only_second_waits_and_never_uses_unlimited(self):
        calls, now = [], [0.]
        def run(t):
            calls.append((t['order'],now[0]))
            now[0] += 10
            return True,'ok'
        s,_ = self.scheduler(plan_for(2), run, now=now)
        self.assertIsNone(s.choose())
        s.run()
        self.assertEqual(calls, [(2,300.),(2,610.)])
        self.assertTrue(s.succeeded)
        self.assertEqual(s.completed_runs, 2)
        notices = [c.args[1] for c in s.emit.call_args_list if c.args[0] == 'schedule' and c.args[1]['phase'] == 'waiting']
        self.assertTrue(notices)
        self.assertTrue(all(n['task'] == '二本' for n in notices))

    def test_mixed_subset_never_enters_unselected_even_if_due(self):
        seen = []
        s,_ = self.scheduler(plan_for(3,5), lambda t:(seen.append(t['order']) is None,'ok'))
        s.ready_at.update(dungeon_1=0, dungeon_2=0, dungeon_3=0, dungeon_5=0)
        s.run()
        self.assertEqual(seen,[3,5])

    def test_selected_unlimited_still_fills_cooldown(self):
        seen=[]
        s,_=self.scheduler(plan_for(1,2), lambda t:(seen.append(t['order']) is None,'ok'))
        s.run()
        self.assertEqual(seen,[1,1])

    def test_diamond_due_while_waiting_preempts_dungeon(self):
        events,now=[],[0.]
        plan=plan_for(2)
        plan['max_total_runs']=1
        plan['diamonds']['enabled']=True
        s,_=self.scheduler(plan,lambda t:(events.append(('dungeon',now[0])) is None,'ok'),
            lambda:(events.append(('diamond',now[0])) is None,'ok'),now=now)
        s.diamond_due=10
        s.run()
        self.assertEqual(events,[('diamond',10.),('dungeon',300.)])

    def test_stop_during_wait_never_starts_task(self):
        run,diamond=Mock(),Mock()
        s,_=self.scheduler(plan_for(2),run,diamond)
        s.wait=lambda seconds:(s.stop.set() or True)
        s.run()
        run.assert_not_called()
        diamond.assert_not_called()
        self.assertFalse(s.succeeded)

    def test_pause_prevents_due_tasks_until_resumed(self):
        pause,now,events=threading.Event(),[0.],[]
        pause.set()
        def wait(seconds):
            now[0]+=seconds
            if now[0]>=301: pause.clear()
            return False
        plan=plan_for(2)
        plan['max_total_runs']=1
        plan['diamonds']['enabled']=True
        s,_=self.scheduler(plan,lambda t:(events.append(('dungeon',pause.is_set())) is None,'ok'),
            lambda:(events.append(('diamond',pause.is_set())) is None,'ok'),now=now,pause=pause,wait=wait)
        s.diamond_due=1
        s.run()
        self.assertEqual(events,[('diamond',False),('dungeon',False)])



if __name__=='__main__': unittest.main()
