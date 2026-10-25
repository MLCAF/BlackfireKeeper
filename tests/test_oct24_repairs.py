"""October 24 diagnostics: reward layout, lost input and idle reconnect."""
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image
from bk2.backend import DiagnosticBackend
from bk2.forbidden import ForbiddenFlow, ForbiddenOptions, ForbiddenRunner
from bk2.forbidden_vision import ForbiddenVision, RULES
from bk2.recovery import boundary_profile
from bk2.runtime import Session
from vendor_core.backend import MaaRecognizer, Match
from tests.test_unified_replay import Offline

ROOT = Path(__file__).resolve().parents[1]


class FlowRepairTests(unittest.TestCase):
    def test_auto_turns_on_at_final_recheck_no_toggle_is_sent(self):
        stop=threading.Event();b=Mock();b.capture_is_fresh.return_value=True
        runner=ForbiddenRunner(b,ForbiddenOptions(),lambda:300,stop,Mock(),clock=lambda:1.)
        runner.flow.phase='encounter'
        calls=[]
        def observe(frame):
            calls.append(1)
            if len(calls)==3:
                stop.set()
                return 'auto_on',Match((1059,15,65,66),1)
            return 'auto_off',Match((1059,15,65,66),1)
        with patch('bk2.forbidden_vision.ForbiddenVision.observe',side_effect=observe),patch.object(stop,'wait',return_value=False):
            runner.run()
        self.assertEqual(len(calls),3);b.click.assert_not_called()

    def test_lost_auto_retries_only_confirmed_off_with_cap_and_original_deadline(self):
        f = ForbiddenFlow(ForbiddenOptions(), 0)
        f.step('explore', True, 300, 1)
        self.assertEqual(f.step('auto_off', True, 297, 2), 'auto_off')
        deadline = f.deadline
        self.assertIsNone(f.step('auto_off', True, 297, 3))
        self.assertIsNone(f.step('unknown', True, 297, 6))
        self.assertIsNone(f.step('auto_off', False, 297, 6))
        self.assertEqual(f.step('auto_off', True, 297, 6), 'auto_off')
        self.assertEqual(f.step('auto_off', True, 297, 10), 'auto_off')
        self.assertIsNone(f.step('auto_off', True, 297, 14))
        self.assertEqual(f.deadline, deadline)
        with self.assertRaises(TimeoutError): f.check_time(deadline)
        self.assertEqual(f.completed, 0)

    def test_late_on_confirmation_never_toggles_again(self):
        f = ForbiddenFlow(ForbiddenOptions(), 0)
        f.step('explore', True, 300, 1)
        f.step('auto_off', True, 297, 2)
        f.step('auto_off', True, 297, 6)
        for t in (7, 12, 20): self.assertIsNone(f.step('auto_on', True, 297, t))
        self.assertEqual(f.phase, 'battle')

    def test_resume_rewards_never_count_an_unowned_round(self):
        for first in ('xp', 'loot'):
            f = ForbiddenFlow(ForbiddenOptions(1), 0)
            self.assertEqual(f.step(first, True, 300, 1), first)
            self.assertIsNone(f.step(first, True, 300, 2))
            if first == 'xp': self.assertEqual(f.step('loot', True, 300, 3), 'loot')
            self.assertIsNone(f.step('explore', True, 300, 4))
            self.assertEqual(f.completed, 0)
            self.assertFalse(f.done)
            self.assertEqual(f.step('explore', True, 300, 5), 'explore')


class BackendRepairTests(unittest.TestCase):
    def test_postmessage_held_input_keeps_coordinates_and_never_foregrounds(self):
        b = DiagnosticBackend.__new__(DiagnosticBackend)
        b.input_method = 'PostMessage'; b.last_shape = (720, 1395)
        b.last_capture = time.monotonic(); b.guard = Mock(); b.win = Mock()
        b.controller = Mock(); b.wait = Mock()
        stop = threading.Event()
        with patch.object(stop, 'wait', return_value=False) as hold:
            b.click(Match((557,258,281,248), .999945), (0,99), stop, threading.Event())
        b.controller.post_touch_down.assert_called_once_with(697,481)
        b.controller.post_touch_up.assert_called_once()
        b.controller.post_click.assert_not_called(); b.win.foreground.assert_not_called()
        hold.assert_called_once_with(.15)
        b.input_method='Seize'; self.assertEqual(b.click_hold_seconds,0)

    def test_recognition_timeout_discards_frame_and_only_recovers_once(self):
        b=DiagnosticBackend.__new__(DiagnosticBackend)
        b.cancel=Mock(return_value=SimpleNamespace(succeeded=True)); b.wait=Mock(); b.emit=Mock()
        frame=np.zeros((72,140,3),np.uint8); next_frame=frame.copy(); stop=threading.Event()
        with patch('bk2.scene_variants.SceneVariantMixin.recognize',side_effect=[TimeoutError('timeout'),Match((1,1,10,10),1),TimeoutError('again')]) as recog:
            self.assertIsNone(b.recognize(frame,{},stop))
            self.assertIsNone(b.recognize(frame,{},stop))
            self.assertIsNone(b.last_shape)
            self.assertEqual(recog.call_count,1)
            self.assertIsNotNone(b.recognize(next_frame,{},stop))
            with self.assertRaises(TimeoutError): b.recognize(next_frame,{},stop)
        b.cancel.assert_called_once()

    def test_failed_recognition_cancel_never_allows_reuse(self):
        b=DiagnosticBackend.__new__(DiagnosticBackend)
        b.cancel=Mock(return_value=SimpleNamespace(succeeded=False));b.wait=Mock()
        with patch('bk2.scene_variants.SceneVariantMixin.recognize',side_effect=TimeoutError()):
            with self.assertRaisesRegex(RuntimeError,'未确认停止'):
                b.recognize(np.zeros((72,140,3),np.uint8),{},threading.Event())


class IdleReconnectTests(unittest.TestCase):
    def test_stopped_session_rebinds_without_clearing_stop_or_allowing_input(self):
        with tempfile.TemporaryDirectory() as d, patch('bk2.runtime.ROOT',Path(d)):
            s=Session();s.operation_stop.set()
            snap=dict(status='ok',pid=42,process_creation=123,captured_monotonic=time.monotonic(),data={})
            s.last_valid_snapshot=snap
            def bind(stop):
                self.assertFalse(stop.is_set());self.assertEqual(s.memory_recovery_until,0)
                self.assertEqual(s.snapshot['status'],'unknown')
                return snap
            reader=SimpleNamespace(bind=Mock(side_effect=bind))
            self.assertEqual(s.rebind_after_disconnect(reader,passive=True),snap)
            self.assertTrue(s.operation_stop.is_set());self.assertIsNone(s.runner)
            self.assertEqual(s.memory_recovery_until,0)

    def test_close_cancels_passive_rebind(self):
        with tempfile.TemporaryDirectory() as d, patch('bk2.runtime.ROOT',Path(d)):
            s=Session();s.stop_event.set();reader=Mock()
            with self.assertRaises(InterruptedError):s.rebind_after_disconnect(reader,passive=True)
            reader.bind.assert_not_called()


class LayoutRepairTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        c=Offline();c.post_connection().wait()
        cls.reco=MaaRecognizer(ROOT/'assets',c); cls.stop=threading.Event()
        cls.vision=ForbiddenVision(cls.reco,cls.stop)
    @classmethod
    def tearDownClass(cls):cls.reco.cancel().wait()
    def frame(self, title_y, button_y):
        frame=np.full((720,1395,3),35,np.uint8)
        for name,x,y in [('loot_title',650,title_y),('continue',675,button_y)]:
            p=np.asarray(Image.open(ROOT/'assets/image/forbidden'/f'{name}.png').convert('RGB'))[:,:,::-1]
            h,w=p.shape[:2];frame[y:y+h,x:x+w]=p
        return frame
    def test_reward_heights_context_and_dimmed_negatives(self):
        for ty,by in [(195,475),(132,528),(75,600)]:
            frame=self.frame(ty,by);kind,hit=self.vision.observe(frame)
            self.assertEqual(kind,'loot');self.assertIsNotNone(hit)
            self.assertEqual(hit.box[1],by)
            self.assertEqual(self.vision.observe((frame*.4).astype(np.uint8))[0],'unknown')
            frame[ty:ty+50,640:800]=35
            self.assertNotEqual(self.vision.observe(frame)[0],'loot')
        self.assertEqual(RULES['loot_title']['threshold'],.95)

    def test_boundary_detects_battle_without_click_or_round_credit(self):
        rules=boundary_profile()['states']['Boundary']['rules']
        battle=[r for r in rules if r.get('action')=='stop' and r['template'].endswith('auto_on.png')][0]
        frame=np.full((720,1395,3),35,np.uint8)
        p=np.asarray(Image.open(ROOT/'assets/image/forbidden/auto_on.png').convert('RGB'))[:,:,::-1]
        h,w=p.shape[:2];frame[15:15+h,1059:1059+w]=p
        self.assertIsNotNone(self.reco.recognize(frame,battle,self.stop))
        self.assertNotIn('next',battle);self.assertNotIn('event',battle)
        self.assertIsNone(self.reco.recognize((frame*.4).astype(np.uint8),battle,self.stop))

    @unittest.skipUnless(os.environ.get('BKK_DIAGNOSTICS_ROOT'),'Private uploaded screens are not distributed')
    def test_uploaded_two_row_loot_and_restart(self):
        folder=Path(os.environ['BKK_DIAGNOSTICS_ROOT'])
        for stem in ('20261024-123246-985515-0879dea0','20261024-123435-683489-e7b10620','20261024-210451-773974-d6b50aea'):
            frame=np.asarray(Image.open(folder/stem/'screen.png').convert('RGB'))[:,:,::-1].copy()
            kind,hit=self.vision.observe(frame)
            self.assertEqual(kind,'loot',stem);self.assertIsNotNone(hit,stem)
            x,y,w,h=hit.box;self.assertTrue(490 <= x+w/2 <= 900)
            self.assertTrue(500 <= y+h/2 <= 575)

    @unittest.skipUnless((ROOT/'logs/diagnostics/20261024-231335-798855-9d8a7dd1/screen.png').is_file(),
                         'Private local diagnostic is not distributed')
    def test_actual_local_boundary_battle_gets_explicit_nonclick_stop(self):
        frame=np.asarray(Image.open(ROOT/'logs/diagnostics/20261024-231335-798855-9d8a7dd1/screen.png').convert('RGB'))[:,:,::-1].copy()
        matches=[r for r in boundary_profile()['states']['Boundary']['rules']
                 if self.reco.recognize(frame,r,self.stop)]
        self.assertTrue(matches)
        self.assertTrue(all(r.get('action')=='stop' for r in matches))


if __name__=='__main__':unittest.main()
