"""October 22 regressions; real matcher, offline controller, no game input."""
import copy
import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image
from bk2.automation import Batch, load_plan
from bk2.memory import MemoryReadError
from bk2.profiles import build_dungeon_profile
from bk2.recovery import recovery_rules, boundary_profile, RecoveryRunner
from bk2.loot_profile import boundary_loot_rules
from bk2.runtime import Session
from tests.test_scene_variants import VariantRecognizer
from tests.test_unified_replay import Offline

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT/'evidence/bugs_20261022/replay/bugs_20261022'


class OctoberRecognitionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        controller = Offline(); controller.post_connection().wait()
        cls.reco = VariantRecognizer(ROOT/'assets', controller)
        cls.unfinished = recovery_rules()[2]
        cls.loot = boundary_loot_rules()[0]
        plan_path=ROOT/'config/dungeon_plan.example.json'
        if not plan_path.exists():plan_path=ROOT/'config/dungeon_plan.json'
        plan = json.loads(plan_path.read_text(encoding='utf8'))
        base = json.loads((ROOT/'config/profiles/dungeon_unlimited.json').read_text(encoding='utf8'))
        cls.profile = build_dungeon_profile(base, plan['tasks'][2], plan['tasks'])
        cls.entrance = next(r for r in cls.profile['states']['Explore']['rules']
                            if r['template']=='副本入口进度.png')
        cls.inventory = next(r for r in boundary_profile()['states']['Boundary']['rules']
                             if r['template']=='物品栏关闭核心.png')

    @classmethod
    def tearDownClass(cls): cls.reco.cancel().wait()

    def read(self, path):
        with Image.open(path) as im: return np.asarray(im.convert('RGB'))[:,:,::-1].copy()

    def canvas(self, parts):
        frame = np.full((720,1395,3),35,np.uint8)
        for name,x,y in parts:
            tile = self.read(ROOT/'assets/image'/name); h,w=tile.shape[:2]
            frame[y:y+h,x:x+w]=tile
        return frame

    def hit(self, frame, rule):
        return self.reco.recognize(frame,rule,threading.Event())

    def test_unfinished_button_still_requires_the_specific_message(self):
        parts=[('未完成地下城好的.png',672,396),('未完成地下城提示兼容.png',528,316)]
        frame=self.canvas(parts); frozen=copy.deepcopy(self.unfinished)
        self.assertIsNotNone(self.hit(frame,self.unfinished))
        self.assertEqual(self.unfinished,frozen)
        for partial in ([parts[0]],[parts[1]]):
            self.assertIsNone(self.hit(self.canvas(partial),self.unfinished))
        self.assertIsNone(self.hit((frame*.4).astype(np.uint8),self.unfinished))

    def test_loot_confirmation_requires_title_and_does_not_increment_counters(self):
        parts=[('战利品小字确认.png',676,527),('战利品小字标题.png',644,130)]
        frame=self.canvas(parts)
        self.assertIsNotNone(self.hit(frame,self.loot))
        self.assertIsNone(self.hit(self.canvas(parts[:1]),self.loot))
        self.assertIsNone(self.hit((frame*.4).astype(np.uint8),self.loot))
        self.assertNotIn('increment_counter',self.loot)
        self.assertEqual(self.loot['next'],'Boundary')

    def test_entrance_fallback_preserves_original_threshold_and_success_dependency(self):
        frame=self.canvas([('副本入口进度小窗.png',573,83)])
        frozen=copy.deepcopy(self.entrance)
        self.assertIsNotNone(self.hit(frame,self.entrance))
        self.assertEqual(self.entrance,frozen)
        self.assertEqual(self.entrance['threshold'],.94)
        explore=next(r for r in self.profile['states']['Explore']['rules'] if r['template']=='去探险.png')
        self.assertIsNotNone(self.hit(frame,dict(explore['success_match'],name='success')))
        self.assertIsNone(self.hit(self.canvas([('副本入口进度小窗.png',800,300)]),self.entrance))
        self.assertIsNone(self.hit((frame*.4).astype(np.uint8),self.entrance))

    @unittest.skipUnless(FIX.is_dir(),'Private October 22 diagnostics are not distributed')
    def test_actual_failures_and_cross_page_negatives(self):
        rules={'unfinished':self.unfinished,'loot':self.loot,'entrance':self.entrance,'inventory':self.inventory}
        for scene in ['unfinished','loot','entrance','battle','inventory']:
            frame=self.read(FIX/(scene+'.png'))
            for name,rule in rules.items():
                with self.subTest(scene=scene,rule=name):
                    self.assertEqual(self.hit(frame,rule) is not None,scene==name)

    def test_inventory_close_requires_title_and_ignores_dimmed_background(self):
        parts=[('物品栏关闭核心.png',994,49),('物品栏标题.png',661,47)]
        frame=self.canvas(parts)
        self.assertIsNotNone(self.hit(frame,self.inventory))
        for partial in ([parts[0]],[parts[1]]):
            self.assertIsNone(self.hit(self.canvas(partial),self.inventory))
        self.assertIsNone(self.hit((frame*.4).astype(np.uint8),self.inventory))

    def replay_boundary(self, frames, stuck=False):
        test=self
        class Backend:
            def __init__(self):self.page=0;self.clicks=[];self.save_diagnostic=Mock(return_value='offline.json')
            def guard(self):pass
            def cancel(self):pass
            def capture_is_fresh(self,*args):return True
            def capture(self,stop):return frames[self.page]
            def recognize(self,*args):return test.reco.recognize(*args)
            def click(self,match,offset,stop,pause):
                if stop.is_set() or pause.is_set():raise InterruptedError()
                self.clicks.append(match.box)
                if not stuck:self.page=min(self.page+1,len(frames)-1)
        from vendor_core.config import validate
        backend=Backend();now=[100.]
        def clock():now[0]+=.1;return now[0]
        runner=RecoveryRunner(backend,validate(boundary_profile(),ROOT/'assets/image',True),Mock(),False,clock)
        with patch.object(runner.stop_event,'wait',return_value=False):runner.run()
        return runner,backend

    @unittest.skipUnless(FIX.is_dir(),'Private October 22 diagnostics are not distributed')
    def test_actual_loot_then_inventory_then_list_replay(self):
        frames=[self.read(FIX/'loot.png'),self.read(FIX/'inventory.png'),
                self.canvas([('地下城导航选中.png',552,580)])]
        runner,backend=self.replay_boundary(frames)
        self.assertTrue(runner.completed,runner.reason)
        self.assertEqual(len(backend.clicks),2)
        self.assertEqual(runner.stats.rounds,0)
        self.assertFalse(runner.counters)

    def test_stuck_inventory_stops_after_bounded_clicks(self):
        frame=self.canvas([('物品栏关闭核心.png',994,49),('物品栏标题.png',661,47)])
        runner,backend=self.replay_boundary([frame],stuck=True)
        self.assertFalse(runner.completed)
        self.assertEqual(len(backend.clicks),2)
        backend.save_diagnostic.assert_called_once()


class OctoberCancellationTests(unittest.TestCase):
    def test_stop_during_boundary_does_not_become_a_scheduler_error(self):
        from tests.test_migration import session
        s=session(); batch=Batch(s,load_plan(),Mock()); batch.next_boundary_check=0
        def execute(config):
            s.operation_stop.set()
            return SimpleNamespace(completed=False,reason='用户停止')
        batch.execute=execute; batch.maybe_food=Mock()
        with self.assertRaises(InterruptedError):batch.service_boundary()
        batch.maybe_food.assert_not_called()

    def test_real_boundary_timeout_is_still_an_error(self):
        from tests.test_migration import session
        s=session(); batch=Batch(s,load_plan(),Mock()); batch.next_boundary_check=0
        batch.execute=Mock(return_value=SimpleNamespace(completed=False,reason='超时'))
        with self.assertRaisesRegex(RuntimeError,'超时'):batch.service_boundary()

    def observe_error(self, exc, cancel=False):
        with tempfile.TemporaryDirectory() as folder, patch('bk2.runtime.ROOT',Path(folder)), \
             patch('bk2.runtime.verify_build'), patch('bk2.runtime.find_game_pid',return_value=42), \
             patch('bk2.runtime.ProcessReader'), patch('bk2.runtime.StateReader'), \
             patch('bk2.runtime.bind_when_ready',side_effect=exc):
            s=Session(); s.publish=Mock(); s.stop_automation=Mock()
            if cancel:s.operation_stop.set()
            s._observe()
        return s

    def test_cancelled_binding_publishes_no_memory_error(self):
        s=self.observe_error(InterruptedError('内存重连已停止'),True)
        self.assertTrue(s.ready.is_set());self.assertEqual(s.snapshot['status'],'unknown')
        self.assertIsNone(s.error);s.stop_automation.assert_not_called()
        self.assertNotIn('memory_error',[c.args[0] for c in s.publish.call_args_list])
        self.assertIn('memory_cancelled',[c.args[0] for c in s.publish.call_args_list])
        s.operation_stop.clear()
        with self.assertRaises(MemoryReadError):s.wait_ready()

    def test_gui_does_not_turn_requested_interrupt_into_error_sound(self):
        from bk2.gui import App
        from bk2.notifications import TaskSounds
        app=App.__new__(App); app.session=Session();app.emit=Mock()
        app.session.publish=Mock();app.session.operation_stop.set()
        app.report_error(InterruptedError('用户停止'))
        app.session.publish.assert_not_called()
        app.emit.assert_called_once_with('log','用户停止')
        player=Mock();sound=TaskSounds(ROOT,player=player)
        sound.observe('memory_cancelled','取消');player.assert_not_called()
        app.session.operation_stop.clear()
        app.report_error(InterruptedError('unexpected'))
        app.session.publish.assert_called_once_with('error','unexpected')

    def test_unrequested_interrupt_and_real_memory_fault_remain_errors(self):
        for exc in [InterruptedError('scan timeout'),MemoryReadError('对象失效')]:
            s=self.observe_error(exc)
            self.assertEqual(s.error,str(exc));s.stop_automation.assert_called_once()
            self.assertIn('memory_error',[c.args[0] for c in s.publish.call_args_list])
