import copy
import json
from pathlib import Path
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock,patch
import numpy as np
from bk2.backend import DiagnosticBackend,CaptureRecoveryTimeout
from bk2.state import ProgressLedger
from bk2.recovery import RecoveryRunner


class CaptureReconnectTests(unittest.TestCase):
    def backend(self):
        b=DiagnosticBackend.__new__(DiagnosticBackend)
        b.recognition_trace=[];b.last_image=None;b.last_shape=(720,1395);b.last_capture=100
        b.capture_method='FramePool';b.input_method='PostMessage';b.emit=Mock()
        b.capture_health={'status':'timed_out','rejected_frames':20}
        return b

    def test_black_timeout_rebuilds_once_and_requires_pair(self):
        b=self.backend();frame=np.ones((72,140,3),dtype=np.uint8)
        b._capture_valid=Mock(side_effect=[CaptureRecoveryTimeout('black'),frame]);b.reconnect_capture=Mock()
        stop=threading.Event();self.assertIs(b.capture(stop),frame)
        b.reconnect_capture.assert_called_once_with(stop)
        self.assertEqual(b._capture_valid.call_args.kwargs,{'require_pair':True})
        self.assertEqual(b.capture_health['initial_failure']['rejected_frames'],20)
        b._capture_valid.side_effect=CaptureRecoveryTimeout('still black')
        with self.assertRaises(CaptureRecoveryTimeout):b.capture(stop)
        b.reconnect_capture.assert_called_once()

    def test_guard_failure_and_cancellation_never_rebuild(self):
        for error in (RuntimeError('wrong pid'),InterruptedError('stopped')):
            b=self.backend();b._capture_valid=Mock(side_effect=error);b.reconnect_capture=Mock()
            with self.assertRaises(type(error)):b.capture(threading.Event())
            b.reconnect_capture.assert_not_called()

    def test_native_rebind_preserves_backend_input_and_720_mapping(self):
        b=self.backend();b.window=SimpleNamespace(hwnd=123);b.guard=Mock();b.wait=Mock()
        b.tasker=Mock();b.resource=object();controller=Mock()
        with patch('maa.controller.Win32Controller',return_value=controller) as make:
            b.reconnect_capture(threading.Event())
        from maa.define import MaaWin32ScreencapMethodEnum,MaaWin32InputMethodEnum
        self.assertEqual(make.call_args.kwargs,dict(screencap_method=MaaWin32ScreencapMethodEnum.FramePool,
            mouse_method=MaaWin32InputMethodEnum.PostMessage,keyboard_method=MaaWin32InputMethodEnum.PostMessage))
        controller.set_screenshot_target_short_side.assert_called_once_with(720)
        b.tasker.bind.assert_called_once_with(b.resource,controller)
        controller.post_click.assert_not_called()

    def test_rebuilt_capture_requires_two_valid_frames(self):
        b=self.backend();b.guard=Mock();b.wait=Mock()
        frame=np.indices((72,140))[0].astype(np.uint8);frame=np.repeat(frame[:,:,None],3,axis=2)
        b.controller=Mock();b.controller.post_screencap.return_value=Mock(succeeded=True,get=Mock(return_value=frame))
        b._capture_valid(threading.Event(),require_pair=True)
        self.assertEqual(b.controller.post_screencap.call_count,2)


class CompletedReturnTests(unittest.TestCase):
    def test_completed_return_counts_once_without_click_and_unverified_stops(self):
        from tests.test_recovery_020 import Scenes, Clock
        from bk2.list_guard import LIST_RULE
        from vendor_core.backend import Match
        for verified in (True, False):
            backend=Scenes(unfinished=False)
            backend.verified_dungeon_return=lambda expected: verified and expected=='forest_maze'
            config=dict(version=1,initial_state='ResumeStart',max_minutes=2,max_rounds=1,poll_seconds=.1,
                expected_dungeon_id='forest_maze',states={
                'ResumeStart':dict(timeout_seconds=10,rules=[dict(name='arm observed round',template='arm',
                    action='none',event='round_start',next='Progress',confirm_frames=2)]),
                'Progress':dict(timeout_seconds=10,rules=[dict(LIST_RULE,name='unexpected list',action='stop',confirm_frames=2)]),
                'ReturnEntry':dict(timeout_seconds=10,rules=[dict(LIST_RULE,name='verified return',action='none',
                    event='round_end',next='Entry',confirm_frames=2)]),
                'Entry':dict(timeout_seconds=10,rules=[])})
            runner=RecoveryRunner(backend,config,Mock(),False,Clock())
            backend.capture=lambda stop:runner.stats.state
            backend.recognize=lambda frame,rule,stop: Match((100,100,30,30),1) if (
                rule['template']==('arm' if frame=='ResumeStart' else LIST_RULE['template'])) else None
            with patch.object(runner.stop_event,'wait',side_effect=lambda _:runner.stop_event.is_set()):
                runner.run()
            self.assertEqual(runner.stats.rounds,1 if verified else 0,runner.reason)
            self.assertEqual(backend.clicks,[])
            if verified:self.assertEqual(runner.reason,'达到完成轮数上限')

    @unittest.skipUnless((Path(__file__).resolve().parents[1]/'logs/20261023-092822-201407.jsonl').is_file(),
                         'Private October 23 diagnosis is not distributed')
    def test_actual_report_black_capture_and_finished_route_replay(self):
        from PIL import Image
        root=Path(__file__).resolve().parents[1]
        with Image.open(root/'logs/diagnostics/20261023-092922-832382-c6570096/screen.png') as im:
            self.assertTrue(DiagnosticBackend.is_nearly_uniform(np.asarray(im.convert('RGB'))))
        ledger=ProgressLedger()
        for line in (root/'logs/20261023-092822-201407.jsonl').read_text(encoding='utf8').splitlines():
            event=json.loads(line)
            if event['kind']=='memory_change' and '09:46:' in event['time']:
                ledger.observe(event['data'])
        self.assertEqual(ledger.last_verified_completion,{'dungeon_id':'forest_maze','count':1})

    def snapshot(self,result='in progress',finished=True,index=2):
        return {'data':{'dungeon_progress':{'dungeon_id':'forest_maze','result':result,'current_room_index':index,
            'rooms':[{'id':'entrance'},{'id':'battle','data':{'has_finished':finished}},
                     {'id':'battle','data':{'has_finished':finished}}] if result=='in progress' else []}}}

    def test_ledger_requires_full_route_before_finished_transition(self):
        for finished,index,verified in [(True,2,True),(False,2,False),(True,1,False)]:
            ledger=ProgressLedger();ledger.observe(self.snapshot(finished=finished,index=index))
            event=ledger.observe(self.snapshot('finished',index=3))
            self.assertFalse(event['inventory_credit_verified'])
            self.assertEqual(ledger.last_verified_completion is not None,verified)
        ledger=ProgressLedger();ledger.observe(self.snapshot('finished',index=3))
        self.assertIsNone(ledger.last_verified_completion)

    def test_return_recovery_only_armed_expected_and_verified_route(self):
        from tests.test_recovery_020 import Scenes,run
        from bk2.list_guard import LIST_RULE
        class Returned(Scenes):
            def __init__(self,verified):
                super().__init__(unfinished=False);self.page='list';self.verified=verified
            def verified_dungeon_return(self,expected):return self.verified and expected=='forest_maze'
            def recognize(self,frame,rule,stop):
                from vendor_core.backend import Match
                if rule['template']==LIST_RULE['template']:return Match((535,590,108,122),1)
                return None
        config=dict(version=1,initial_state='Progress',max_minutes=2,poll_seconds=.1,
            expected_dungeon_id='forest_maze',states={
            'Progress':dict(timeout_seconds=10,rules=[dict(LIST_RULE,name='unexpected list',action='stop',confirm_frames=2)]),
            'ReturnEntry':dict(timeout_seconds=10,rules=[dict(LIST_RULE,name='returned',action='none',event='task_complete',confirm_frames=2)])})
        for verified,armed in [(True,True),(False,True),(True,False)]:
            b=Returned(verified)
            r=RecoveryRunner(b,config,Mock(),False);r.round_armed=armed
            candidate=r.pending_success('list')
            self.assertEqual(candidate is not None,verified and armed)
            if candidate:
                self.assertEqual(candidate[0]['next'],'ReturnEntry')
                self.assertEqual(candidate[0]['event'],'none')
                self.assertEqual(candidate[0]['confirm_frames'],3)
