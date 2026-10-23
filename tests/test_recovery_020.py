import copy
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from bk2.automation import Batch, load_plan, save_plan
from bk2.plan import validate_dungeon_plan
from bk2.recovery import RecoveryRunner, active_task, add_resume_start, recovery_rules, LIST_RULE
from bk2.profiles import build_dungeon_profile
from bk2.scheduler import MemoryScheduler
from bk2.runtime import Session, ROOT
from bk2.memory import MemoryReadError
from vendor_core.backend import Match
from vendor_core.unified import ScheduleStore
from vendor_core.config import validate


class Clock:
    def __init__(self): self.now=100.
    def __call__(self): self.now+=.1; return self.now


class Scenes:
    def __init__(self, midway=False, unfinished=True, mismatch=False, stuck=False, late=False):
        self.page='timeout' if midway else 'entry'
        self.midway=midway; self.unfinished=unfinished; self.mismatch=mismatch
        self.stuck=stuck; self.late=late; self.clicks=[]; self.swipes=[]
        self.save_diagnostic=Mock(return_value='offline.json')
    def guard(self): pass
    def cancel(self): pass
    def capture_is_fresh(self,*args): return True
    def capture(self,stop): return self.page
    def recognize(self,frame,rule,stop):
        targets={
          'entry':{'enter'},'timeout':{'提示确认文字.png','游戏timeout提示.png'},
          'login':{'重连开始冒险.png','重连登录标识.png'},
          'unfinished':{'未完成地下城好的.png','未完成地下城提示.png'},
          'room':{'finish'},'list':{LIST_RULE['template'],'地下城入口主体.png'},
          'home':{'主界面锚点.png'},'unknown':set(),
        }
        if rule['template'] not in targets.get(frame,set()): return None
        if rule.get('requires_match') and not self.recognize(frame,rule['requires_match'],stop): return None
        return Match((100,100,40,30),1.)
    def click(self,match,offset,stop,pause):
        if stop.is_set() or pause.is_set(): raise InterruptedError()
        self.clicks.append(self.page)
        if self.stuck: return
        if self.page=='entry': self.page='timeout'
        elif self.page=='timeout': self.page='login'
        elif self.page=='login': self.page='unfinished' if self.unfinished else 'home'
        elif self.page=='unfinished': self.page='room'
        elif self.page=='room': self.page='list'
    def swipe(self,direction,*args): self.swipes.append(direction)
    def recovery_data(self):
        return {'dungeon_progress':{'dungeon_id':'forest_maze' if self.mismatch else 'slime_cave',
                'result':'in progress' if self.unfinished else 'finished'}}


def minimal():
    return dict(version=1,initial_state='Explore',max_minutes=10,max_rounds=1,poll_seconds=.1,
        expected_dungeon_id='slime_cave',states={
         'Explore':dict(timeout_seconds=60,rules=[dict(name='进入',template='enter',action='click',
             verify_change=True,next='Progress',event='round_start',confirm_frames=2,retry_max_clicks=1,retry_center_clicks=1)]),
         'Progress':dict(timeout_seconds=120,rules=[dict(name='完成',template='finish',action='click',
             verify_change=False,next='ReturnEntry',confirm_frames=2)]),
         'ReturnEntry':dict(timeout_seconds=30,rules=[dict(name='已返回',template=LIST_RULE['template'],
             action='none',event='round_end',next='Explore',confirm_frames=2)])})


def run(backend,config):
    runner=RecoveryRunner(backend,config,Mock(),False,Clock())
    with patch.object(runner.stop_event,'wait',side_effect=lambda _:runner.stop_event.is_set()):
        runner.run()
    return runner


class ReconnectionTests(unittest.TestCase):
    def test_timeout_after_round_started_keeps_one_round_and_counters(self):
        b=Scenes()
        original=b.click
        def click(*args):
            if b.page=='entry': b.clicks.append('entry'); b.page='room'; return
            if b.page=='room' and 'timeout' not in b.clicks:
                b.clicks.append('room-before-timeout'); b.page='timeout'; return
            original(*args)
        b.click=click
        config=minimal();config['states']['Progress']['rules'][0]['verify_change']=True
        r=run(b,config)
        self.assertEqual(r.stats.rounds,1,r.reason)
        self.assertEqual(b.clicks,['entry','room-before-timeout','timeout','login','unfinished','room'])

    def test_timeout_during_entry_login_unfinished_and_exactly_one_round(self):
        b=Scenes(); r=run(b,minimal())
        self.assertEqual(b.clicks,['entry','timeout','login','unfinished','room'],r.reason)
        self.assertEqual(r.stats.rounds,1,r.reason)
        self.assertEqual(r.reason,'达到完成轮数上限')

    def test_timeout_before_resume_gate_arms_exactly_one_round(self):
        b=Scenes(midway=True)
        config=minimal(); add_resume_start(config)
        # Login interrupt is encountered before the resume gate arms the round.
        r=run(b,config)
        self.assertEqual(r.stats.rounds,1,r.reason)
        self.assertEqual(b.clicks,['timeout','login','unfinished','room'])

    def test_battle_retry_budget_survives_login_recovery(self):
        from bk2.battle import BattleRunner
        b=Scenes(midway=True);r=BattleRunner(b,minimal(),Mock(),False,Clock())
        r.battle_restarts=3;r.counters['battle_restart_attempts']=3;r.round_armed=True
        rule=r.config['interrupts'][0];match=b.recognize('timeout',rule,r.stop_event)
        with patch.object(r.stop_event,'wait',return_value=False):
            self.assertTrue(r.click_with_change_confirmation(rule,match))
        self.assertEqual(r.battle_restarts,3)
        self.assertEqual(r.counters['battle_restart_attempts'],3)
        self.assertEqual(rule['next'],'Progress')
        r.ordered_rules()
        self.assertNotIn('next',rule)

    def test_reconnect_resumes_each_supported_substate(self):
        for state in ('OpeningBuff','OpeningBuffConfirm','OpeningBuffClose','SkillCardSelect','SkillCardClose'):
            with self.subTest(state=state):
                b=Scenes(midway=True);c=minimal()
                c['states'][state]=c['states']['Progress'];c['states']['Progress']=dict(timeout_seconds=120,rules=[])
                r=run(b,add_resume_start(c))
                self.assertEqual(r.stats.rounds,1,r.reason)
                self.assertEqual(b.clicks,['timeout','login','unfinished','room'])

    def test_start_inside_dungeon_does_not_click_enter(self):
        b=Scenes(); b.page='room'
        config=add_resume_start(minimal()); r=run(b,config)
        self.assertEqual(b.clicks,['room'])
        self.assertEqual(r.stats.rounds,1,r.reason)

    def test_timeout_without_active_dungeon_returns_home(self):
        b=Scenes(midway=True,unfinished=False)
        config=dict(version=1,initial_state='Home',max_minutes=3,poll_seconds=.1,states={
            'Home':dict(timeout_seconds=45,rules=[dict(name='回到主城',template='主界面锚点.png',
                       action='none',event='task_complete',confirm_frames=2)])})
        r=run(b,config)
        self.assertTrue(r.completed,r.reason)
        self.assertEqual(b.clicks,['timeout','login'])

    def test_wrong_dungeon_stops_before_gameplay(self):
        b=Scenes(mismatch=True); r=run(b,minimal())
        self.assertEqual(b.clicks,['entry','timeout','login','unfinished'])
        self.assertEqual(r.stats.rounds,0)
        self.assertIn('不一致',r.reason)

    def test_stuck_login_submits_once_and_saves_diagnostic(self):
        b=Scenes(midway=True,stuck=True); r=run(b,minimal())
        self.assertEqual(b.clicks,['timeout'])
        self.assertIn('60 秒',r.reason)
        b.save_diagnostic.assert_called_once()

    def test_stop_in_reconnect_prevents_more_input(self):
        b=Scenes(midway=True)
        r=RecoveryRunner(b,minimal(),Mock(),False,Clock())
        with patch.object(r.stop_event,'wait',side_effect=lambda _:bool(b.clicks)):
            r.run()
        self.assertEqual(b.clicks,['timeout'])
        self.assertEqual(r.stats.rounds,0)

    def test_unrelated_confirmation_does_not_match_login(self):
        b=Scenes(); b.page='home'
        for rule in recovery_rules(): self.assertIsNone(b.recognize('home',rule,threading.Event()))

    def test_missing_memory_after_login_never_resumes(self):
        b=Scenes(); b.recovery_data=lambda:None
        r=run(b,minimal())
        self.assertNotIn('room',b.clicks)
        self.assertEqual(r.stats.rounds,0)


class ListResetTests(unittest.TestCase):
    def runner(self):
        b=Scenes(); b.page='list'
        config=dict(version=1,initial_state='SelectDungeon',max_minutes=2,poll_seconds=.1,
                    navigation_names=['一本名称.png','二本名称.png'],states={
                      'SelectDungeon':dict(timeout_seconds=60,rules=[dict(name='目标',template='三本名称.png',action='none')])})
        r=RecoveryRunner(b,config,Mock(),False,Clock()); r.stats.idle_seconds=6
        return r,b
    def test_no_names_clicks_tab_then_six_unconditional_right_drags(self):
        r,b=self.runner(); rule,match=r.pending_success('list')
        with patch.object(r.stop_event,'wait',return_value=False):
            self.assertTrue(r.click_with_change_confirmation(rule,match))
        self.assertEqual(b.clicks,['list']); self.assertEqual(b.swipes,['right']*6)
        self.assertEqual(r.list_resets,1)
    def test_any_known_name_blocks_reset(self):
        r,b=self.runner(); original=b.recognize
        b.recognize=lambda f,rule,s:Match((0,0,10,10),1.) if rule['template']=='二本名称.png' else original(f,rule,s)
        self.assertIsNone(r.pending_success('list'))
    def test_reset_budget_and_early_idle(self):
        r,b=self.runner(); r.stats.idle_seconds=1
        self.assertIsNone(r.pending_success('list'))
        r.stats.idle_seconds=6; r.list_resets=2
        self.assertIsNone(r.pending_success('list'))
    def test_tab_click_without_confirmed_list_never_drags(self):
        r,b=self.runner(); rule,match=r.pending_success('list')
        original=b.click
        def click(*args): original(*args); b.page='unknown'
        b.click=click
        with patch.object(r.stop_event,'wait',return_value=False):
            with self.assertRaisesRegex(RuntimeError,'未确认列表'):
                r.click_with_change_confirmation(rule,match)
        self.assertEqual(b.swipes,[])


class RewardsOnlyTests(unittest.TestCase):
    def test_wait_boundary_keeps_existing_reward_and_shop_cleanup(self):
        from bk2.recovery import boundary_profile
        config=boundary_profile();validate(config,ROOT/'assets/image',True)
        rules=config['states']['Boundary']['rules']
        clicks=[r for r in rules if r['action']=='click']
        self.assertEqual(len(clicks),8)
        navigation=[r for r in clicks if r['template']=='地下城入口主体.png']
        self.assertEqual({r['requires_match']['template'] for r in navigation},
                         {'关卡导航选中.png','禁域导航选中.png'})
        self.assertTrue(all(r['success_match']['template']==LIST_RULE['template'] for r in navigation))
        self.assertTrue(all(r['next']=='Boundary' for r in clicks))
        self.assertTrue(all(not r.get('increment_counter') for r in clicks))
        self.assertEqual({r['template'] for r in clicks},
                         {'物品栏关闭核心.png','战利品小字确认.png','钻石奖励确认核心.png','钻石奖励确认.png','钻石商店关闭核心.png','副本详情关闭.png','地下城入口主体.png'})

    def test_zero_selection_preview_uses_service_profile_without_timer_writes(self):
        from tests.test_migration import session
        s=session();p=self.plan();batch=Batch(s,p,Mock(),dry_run=True)
        batch.execute=Mock(return_value=SimpleNamespace(reason='preview'))
        result=batch.run()
        self.assertTrue(result['preview'])
        self.assertNotIn('expected_dungeon_id',batch.execute.call_args.args[0])
        self.assertEqual(result['completed_runs'],0)

    def test_batch_completes_unselected_active_dungeon_before_rewards_loop(self):
        from tests.test_migration import session
        s=session();s.snapshot['data']['dungeon_progress']=dict(dungeon_id='forest_maze',result='in progress')
        p=self.plan(); batch=Batch(s,p,Mock()); calls=[]
        def dungeon(task,resume=False):
            calls.append((task['id'],resume))
            s.snapshot['data']['dungeon_progress']['result']='finished'
            return True,'ok'
        batch.dungeon=dungeon;batch.sync_diamond_deadline=Mock()
        with patch.object(MemoryScheduler,'run',lambda scheduler:calls.append('scheduler')):
            result=batch.run()
        self.assertEqual(calls,[('dungeon_3',True),'scheduler'])
        self.assertEqual(result['completed_runs'],1)
        self.assertFalse(any(t['enabled'] for t in batch.plan['tasks']))

    def test_dashboard_accepts_zero_selection_and_known_active_dungeon(self):
        import tkinter as tk
        from bk2.gui import App
        root=tk.Tk();root.withdraw()
        try:
            app=App(root);app.select_dungeons('none')
            app.last_snapshot=dict(status='ok',pid=10,captured_monotonic=time.monotonic(),
                data={'dungeon_progress':{'dungeon_id':'forest_maze','result':'in progress'}})
            self.assertIsNone(app.start_block_reason())
            self.assertFalse(any(t['enabled'] for t in app.collect_plan()['tasks']))
            app.last_snapshot['data']['dungeon_progress']['dungeon_id']='not-supported'
            self.assertIn('无法映射',app.start_block_reason())
        finally:
            for callback in root.tk.call('after','info'):root.after_cancel(callback)
            root.destroy()

    def plan(self):
        p=load_plan()
        for t in p['tasks']: t['enabled']=False
        return p
    def test_zero_selection_roundtrips_without_enabling_dungeons(self):
        p=self.plan(); before=copy.deepcopy(p)
        self.assertIs(validate_dungeon_plan(p,ROOT/'config/profiles',ROOT/'assets/image',True),p)
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'plan.json'; save_plan(p,path)
            self.assertEqual(load_plan(path),before)
        self.assertEqual(p,before)
    def test_rewards_only_runs_both_services_and_never_scans_or_reads_stamina(self):
        p=self.plan(); p['run_control']['mode']='rounds'
        # This case explicitly tests both enabled services, independent of local defaults.
        p['diamonds']['enabled']=True
        p['food']['enabled']=True
        stop=threading.Event(); calls=[]; now=[1000.]
        def food():
            calls.append('food')
            if len(calls)>3: stop.set()
        def wait(seconds): now[0]+=seconds; return stop.is_set()
        dungeon=Mock(); scan=Mock()
        s=MemoryScheduler(p,dungeon,lambda:(calls.append('diamond') or True,'ok'),stop,Mock(),
                          clock=lambda:now[0],wait=wait,scan_cooldowns=scan)
        s.diamond_due=0; s.service_boundary=food; s.read_stamina=Mock(side_effect=AssertionError())
        s.run()
        self.assertIn('diamond',calls); self.assertIn('food',calls)
        dungeon.assert_not_called(); scan.assert_not_called();s.read_stamina.assert_not_called()
        self.assertEqual(s.completed_runs,0)
    def test_rewards_only_checkpoint_preserves_all_dungeon_cooldowns(self):
        p=self.plan()
        with tempfile.TemporaryDirectory() as folder:
            store=ScheduleStore(Path(folder)/'state.json')
            saved=dict(version=1,ready_at={'dungeon_2':9000,'dungeon_6':9500},diamond_due=3000,in_flight='dungeon_6')
            store.save(saved)
            s=MemoryScheduler(p,Mock(),Mock(),threading.Event(),Mock(),store,clock=lambda:1000)
            s.checkpoint(); self.assertEqual(store.load(),saved)
    def test_disabled_task_cooldown_survives_selected_mode_checkpoint(self):
        p=self.plan();p['tasks'][0]['enabled']=True
        with tempfile.TemporaryDirectory() as folder:
            store=ScheduleStore(Path(folder)/'state.json')
            store.save(dict(version=1,ready_at={'dungeon_6':9500},diamond_due=3000,in_flight=None))
            s=MemoryScheduler(p,Mock(),Mock(),threading.Event(),Mock(),store,clock=lambda:1000)
            s.checkpoint();self.assertEqual(store.load()['ready_at']['dungeon_6'],9500)
    def test_active_unselected_dungeon_maps_without_enabling_it(self):
        p=self.plan(); task=active_task({'dungeon_progress':{'dungeon_id':'forest_maze','result':'in progress'}},p['tasks'])
        self.assertEqual(task['id'],'dungeon_3');self.assertFalse(task['enabled'])
        with self.assertRaises(ValueError):
            active_task({'dungeon_progress':{'dungeon_id':'unknown','result':'in progress'}},p['tasks'])


class RebindTests(unittest.TestCase):
    def test_invalid_memory_allows_recovery_buttons_only_then_restores_normal_input(self):
        from contextlib import nullcontext
        from bk2.automation import run_plan
        from tests.test_migration import session
        s=session();s.wait_ready=Mock();win=Mock();win.emergency_pressed.return_value=False
        def batch(session,plan,factory,*args,**kwargs):
            def execute():
                backend=factory();backend.window=SimpleNamespace(pid=10)
                valid=s.snapshot;s.snapshot={'status':'unknown'};s.memory_recovery_until=time.monotonic()+60
                with self.assertRaises(MemoryReadError):backend.click(None,None,None,None)
                with self.assertRaises(MemoryReadError):backend.swipe('left',None,None)
                self.assertIsNone(backend.recognize(None,dict(template='去探险.png'),None))
                backend.recovery_click_allowed=True
                backend.click(None,None,None,None)
                backend.recovery_click_allowed=False
                s.snapshot=valid;s.memory_recovery_until=0
                backend.click(None,None,None,None)
                s.snapshot={'status':'unknown'}
                with self.assertRaises(MemoryReadError):backend.recognize(None,dict(template='重连开始冒险.png'),None)
                return dict(succeeded=True)
            return SimpleNamespace(run=execute)
        with patch('vendor_core.windows.Windows',return_value=win), \
             patch('bk2.automation.input_lease',return_value=nullcontext()), \
             patch('bk2.automation.Batch',side_effect=batch), \
             patch('bk2.window_ready.connect_backend',side_effect=lambda s,w,p,ctor:ctor(None)), \
             patch('bk2.backend.DiagnosticBackend.__init__',return_value=None), \
             patch('bk2.backend.DiagnosticBackend.click') as click:
            self.assertTrue(run_plan(s,load_plan(),dry_run=True)['succeeded'])
        self.assertEqual(click.call_count,2)

    def test_timeout_cancels_memory_scan_and_clears_grace(self):
        s=Session();s.publish=Mock();now=[100.]
        reader=Mock()
        def bind(stop):
            now[0]=161.
            self.assertTrue(stop.is_set())
            raise InterruptedError('scan cancelled')
        reader.bind=bind
        with patch('bk2.runtime.time.monotonic',side_effect=lambda:now[0]):
            with self.assertRaises(InterruptedError):s.rebind_after_disconnect(reader)
        self.assertEqual(s.memory_recovery_until,0)

    def test_rebind_recovers_same_process_with_fresh_snapshot(self):
        s=Session();s.publish=Mock()
        snap=dict(status='ok',pid=10,captured_monotonic=time.monotonic(),data={})
        s.last_valid_snapshot=snap
        reader=Mock(); reader.bind.side_effect=[MemoryReadError('loading'),snap]
        with patch.object(s.operation_stop,'wait',return_value=False):
            self.assertEqual(s.rebind_after_disconnect(reader),snap)
        self.assertEqual(s.memory_recovery_until,0)
        self.assertEqual(reader.bind.call_count,2)
    def test_rebind_stop_is_respected(self):
        s=Session(); s.publish=Mock();s.operation_stop.set();reader=Mock()
        with self.assertRaises(InterruptedError):s.rebind_after_disconnect(reader)
        reader.bind.assert_not_called();self.assertEqual(s.memory_recovery_until,0)


if __name__=='__main__':unittest.main()
