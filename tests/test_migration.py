import copy
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from bk2.automation import Batch, boundary_ready, input_lease, load_plan
from bk2.runtime import Session, ROOT
from bk2.memory import MemoryReadError
from vendor_core.unified import UnifiedScheduler, ScheduleStore, build_dungeon_profile
from vendor_core.config import validate_dungeon_plan, validate
from vendor_core.runner import Runner
from vendor_core.backend import Match, MaaBackend


def session():
    s = Session()
    s.snapshot = {'status':'ok','pid':10,'captured_monotonic':time.monotonic(),
                  'data':{'dungeon_progress':{'dungeon_id':'slime_cave','result':'finished'}}}
    s.publish = Mock()
    return s


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.plan = load_plan()
        # Scheduler cases below assume all six dungeons, including the ready
        # fallback. Keep this fixture independent of the user's UI selection.
        for task in self.plan['tasks']:
            task['enabled'] = task['order'] <= 6
        self.plan['diamonds'].update(enabled=True, run_on_start=False)
        self.plan['max_total_runs'] = 1
        self.now = [1000.]

    def scheduler(self, dungeon, diamonds, store=None):
        return UnifiedScheduler(self.plan, dungeon, diamonds, threading.Event(), Mock(), store,
                                clock=lambda:self.now[0])

    def test_source_files_match_migration_manifest(self):
        import hashlib
        manifest=json.loads((ROOT/'tests/migration_manifest.json').read_text(encoding='utf-8'))
        patches = json.loads((ROOT/'tests/vendor_patches_20261020.json').read_text(encoding='utf-8'))
        self.assertEqual(set(patches), {'vendor_core/config.py', 'vendor_core/runner.py', 'vendor_core/windows.py'})
        background = json.loads((ROOT/'tests/vendor_patches_20261021.json').read_text(encoding='utf-8'))
        self.assertEqual(set(background), {'vendor_core/backend.py'})
        self.assertFalse(set(patches) & set(background))
        patches.update(background)
        late = json.loads((ROOT/'tests/vendor_patches_late_dungeons.json').read_text(encoding='utf-8'))
        self.assertEqual(set(late), {'vendor_core/unified.py'})
        self.assertFalse(set(patches) & set(late))
        patches.update(late)
        for entry in manifest:
            # Editable user plan is deliberately not frozen after migration.
            if entry['target']=='config/dungeon_plan.json': continue
            path = ROOT/entry['target']
            content = path.read_bytes()
            if path.suffix in ('.py', '.json', '.md', '.txt'):
                content = content.replace(b'\r\n', b'\n')
            expected = entry['sha256']
            if entry['target'] in patches:
                change = patches[entry['target']]
                self.assertEqual(change['base_sha256'], expected)
                expected = change['patched_sha256']
            self.assertEqual(hashlib.sha256(content).hexdigest(), expected, entry['target'])

    def test_all_six_profiles_validate_and_original_base_not_mutated(self):
        base=json.loads((ROOT/'config/profiles/dungeon_unlimited.json').read_text(encoding='utf-8'))
        original=copy.deepcopy(base)
        for task in self.plan['tasks'][:6]:
            built=build_dungeon_profile(base,task,self.plan['tasks'])
            validate(built,ROOT/'assets/image',True)
            self.assertEqual(built['max_rounds'],1)
            self.assertEqual(built['max_minutes'],0)
            if task['order']>1:self.assertNotIn('副本入口标题.png',json.dumps(built,ensure_ascii=False))
        self.assertEqual(base,original)

    def test_first_and_seventh_supported_but_missing_entry_rejected(self):
        for task in self.plan['tasks'][1:]:task['enabled']=False
        validate_dungeon_plan(self.plan,ROOT/'config/profiles',ROOT/'assets/image',True)
        self.plan['tasks'][6]['enabled']=True
        validate_dungeon_plan(self.plan,ROOT/'config/profiles',ROOT/'assets/image',True)
        self.plan['tasks'][6]['entrance_template']=None
        with self.assertRaises(ValueError):validate_dungeon_plan(self.plan,ROOT/'config/profiles',ROOT/'assets/image',True)

    def test_per_dungeon_buff_isolated(self):
        base=json.loads((ROOT/'config/profiles/dungeon_unlimited.json').read_text(encoding='utf-8'))
        self.plan['tasks'][1]['buff']='damage'
        one=build_dungeon_profile(base,self.plan['tasks'][0],self.plan['tasks'])
        two=build_dungeon_profile(base,self.plan['tasks'][1],self.plan['tasks'])
        self.assertIn('经验buff.png',json.dumps(one,ensure_ascii=False))
        self.assertNotIn('经验buff.png',json.dumps(two,ensure_ascii=False))
        self.assertIn('伤害buff.png',json.dumps(two,ensure_ascii=False))

    def test_unknown_cooldowns_wait_and_ready_order_is_oldest_first(self):
        s=self.scheduler(Mock(),Mock())
        self.assertEqual(s.choose()['order'],1)
        s.ready_at['dungeon_3']=5;s.ready_at['dungeon_2']=7
        self.assertEqual(s.choose()['order'],3)
        self.assertEqual(s.ready_at['dungeon_4'],2200)

    def test_due_during_final_round_runs_after_round(self):
        events=[]
        def dungeon(task):
            events.append('begin');self.now[0]+=7300;events.append('finished');return True,'ok'
        s=self.scheduler(dungeon,lambda:(events.append('diamonds') is None,'ack'))
        s.run()
        self.assertEqual(events,['begin','finished','diamonds'])
        self.assertEqual(s.diamond_due,15500)
        self.assertTrue(s.succeeded)

    def test_due_before_start_has_highest_priority(self):
        events=[]
        s=self.scheduler(lambda t:(events.append('dungeon') is None,'ok'),lambda:(events.append('diamonds') is None,'ack'))
        s.diamond_due=0;s.run()
        self.assertEqual(events,['diamonds','dungeon'])

    def test_partial_diamond_does_not_reset_deadline(self):
        dungeon=Mock();s=self.scheduler(dungeon,lambda:(False,'handoff incomplete'))
        s.diamond_due=0;s.run()
        dungeon.assert_not_called();self.assertEqual(s.diamond_due,0);self.assertFalse(s.succeeded)

    def test_failed_dungeon_does_not_release_due_diamond(self):
        diamond=Mock()
        def fail(task):self.now[0]+=7300;return False,'failed'
        s=self.scheduler(fail,diamond);s.run()
        diamond.assert_not_called();self.assertEqual(s.completed_runs,0)

    def test_cooldown_is_from_completion(self):
        def run(task):self.now[0]+=500;return True,'ok'
        s=self.scheduler(run,Mock());s.ready_at['dungeon_2']=0;s.run()
        self.assertEqual(s.ready_at['dungeon_2'],1800)

    def test_persisted_due_survives_restart_and_inflight_is_conservative(self):
        with tempfile.TemporaryDirectory() as d:
            store=ScheduleStore(Path(d)/'schedule.json')
            store.save({'version':1,'ready_at':{'dungeon_2':500},'diamond_due':1500,'in_flight':'dungeon_2'})
            s=self.scheduler(Mock(),Mock(),store)
            self.assertEqual(s.diamond_due,1500);self.assertEqual(s.ready_at['dungeon_2'],1300)
            s.checkpoint();self.now[0]+=10
            restarted=self.scheduler(Mock(),Mock(),store)
            self.assertEqual(restarted.diamond_due,1500)
            store.path.write_text('{}')
            with self.assertRaises(ValueError):store.load()

    def test_memory_in_progress_or_unknown_blocks_boundary(self):
        s=session();s.snapshot['data']['dungeon_progress']['result']='in progress'
        with self.assertRaises(RuntimeError):boundary_ready(s)
        s.snapshot={'status':'unknown'}
        with self.assertRaises(MemoryReadError):boundary_ready(s)

    def test_ui_round_without_memory_completion_is_not_success(self):
        s=session();batch=Batch(s,self.plan,Mock())
        batch.execute=Mock(return_value=SimpleNamespace(stats=SimpleNamespace(rounds=1),reason='达到完成轮数上限'))
        self.assertFalse(batch.dungeon(self.plan['tasks'][0])[0])
        def complete(*args,**kwargs):
            s.ledger.finished_transitions+=1
            return SimpleNamespace(stats=SimpleNamespace(rounds=1),reason='达到完成轮数上限')
        batch.execute=complete
        self.assertTrue(batch.dungeon(self.plan['tasks'][0])[0])

    def test_manual_wait_requires_new_ack_and_can_be_stopped(self):
        for acknowledge in (False,True):
            s=session();s.manual_ready.set()  # stale acknowledgment must be cleared
            batch=Batch(s,self.plan,Mock())
            batch.execute=Mock(return_value=SimpleNamespace(manual_handoff=True,reason='handoff'))
            waiting=threading.Event()
            s.publish=Mock(side_effect=lambda kind,data:waiting.set() if kind=='manual_diamonds' else None)
            result=[];thread=threading.Thread(target=lambda:result.append(batch.diamonds()))
            thread.start();self.assertTrue(waiting.wait(2));self.assertFalse(s.manual_ready.is_set())
            if acknowledge:self.assertTrue(s.acknowledge_diamonds())
            else:s.stop_automation()
            thread.join(2);self.assertFalse(thread.is_alive());self.assertEqual(result[0][0],acknowledge)
            self.assertFalse(s.waiting_for_diamonds)

    def test_preview_does_not_construct_scheduler_or_write_store(self):
        s=session();store=Mock();batch=Batch(s,self.plan,Mock(),True,store=store)
        batch.execute=Mock(return_value=SimpleNamespace(reason='preview ended'))
        with patch('bk2.automation.UnifiedScheduler') as scheduler:
            result=batch.run();scheduler.assert_not_called()
        store.save.assert_not_called();self.assertEqual(result['completed_runs'],0)

    def test_operation_lock_rejects_second_instance(self):
        # Exercise the real OS lock without contending with a running user's BK2.
        with tempfile.TemporaryDirectory() as tmp, patch('bk2.automation.ROOT',Path(tmp)):
            with input_lease():
                with self.assertRaises(RuntimeError):
                    with input_lease():pass


class DiamondRunnerTest(unittest.TestCase):
    def test_swipe_has_an_eight_attempt_limit_and_cannot_finish_round(self):
        backend=Mock()
        backend.capture.return_value='frame'
        backend.recognize.return_value=Match((10,20,30,40),1)
        backend.cancel.return_value=None
        config={'initial_state':'Select','max_minutes':0,'poll_seconds':.001,
                'states':{'Select':{'timeout_seconds':100,'rules':[
                    {'name':'drag','template':'二本名称.png','action':'swipe','direction':'right',
                     'confirm_frames':1,'cooldown_seconds':.001}]}}}
        clock=[0.]
        def tick():clock[0]+=.05;return clock[0]
        runner=Runner(backend,config,Mock(),False,clock=tick)
        with patch.object(runner.stop_event,'wait',return_value=False):runner.run()
        self.assertEqual(backend.swipe.call_count,8)
        self.assertEqual(runner.stats.rounds,0)
        self.assertIn('8 次',runner.reason)

    def test_one_click_handoff_never_claims_collection(self):
        backend=Mock()
        backend.capture.return_value='frame'
        backend.recognize.return_value=Match((10,20,30,40),1)
        backend.recognize_near.return_value=Match((10,20,30,40),1)
        backend.capture_is_fresh.return_value=True
        backend.cancel.return_value=None
        config=json.loads((ROOT/'tests/legacy_diamonds.json').read_text(encoding='utf-8'))
        config['initial_state']='OpenDiamond'
        runner=Runner(backend,config,Mock(),False)
        with patch.object(runner.stop_event,'wait',return_value=False):runner.run()
        self.assertEqual(backend.click.call_count,1)
        self.assertTrue(runner.manual_handoff)
        self.assertFalse(runner.completed)
        self.assertEqual(runner.stats.rounds,0)


if __name__=='__main__':unittest.main()
