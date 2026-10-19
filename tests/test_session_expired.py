"""Session-expired recognition and bounded recovery; never send game input."""
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image
from bk2.recovery import RecoveryRunner, recovery_rules
from vendor_core.backend import MaaRecognizer, Match
from tests.test_unified_replay import Offline
from tests.test_recovery_020 import Scenes, Clock, minimal, run

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT/'tests/fixtures/session_expired_20261019/session.png'


class SessionScenes(Scenes):
    def recognize(self, frame, rule, stop):
        if frame == 'timeout':
            if rule['template'] not in {'提示确认文字.png','会话超时提示.png'}:
                return None
            condition = rule.get('requires_match')
            if condition and not self.recognize(frame, condition, stop): return None
            return Match((666,401,64,36),1.)
        return super().recognize(frame,rule,stop)


class SessionRecoveryTests(unittest.TestCase):
    def test_session_expired_during_entry_reconnects_and_finishes_one_existing_round(self):
        b=SessionScenes(); r=run(b,minimal())
        self.assertEqual(b.clicks,['entry','timeout','login','unfinished','room'],r.reason)
        self.assertEqual(r.stats.rounds,1)

    def test_rewards_only_returns_home_without_starting_dungeon(self):
        b=SessionScenes(midway=True,unfinished=False)
        c=dict(initial_state='Home',max_minutes=0,poll_seconds=.1,states={
            'Home':dict(timeout_seconds=45,rules=[dict(name='home',template='主界面锚点.png',
                       action='none',event='task_complete',confirm_frames=2)])})
        r=run(b,c)
        self.assertTrue(r.completed,r.reason)
        self.assertEqual(b.clicks,['timeout','login'])
        self.assertEqual(r.stats.rounds,0)

    def test_missing_memory_does_not_resume_or_click_gameplay(self):
        b=SessionScenes(); b.recovery_data=lambda:None
        r=run(b,minimal())
        self.assertNotIn('room',b.clicks)
        self.assertIn('60 秒',r.reason)

    def test_stuck_modal_only_confirmed_once_then_diagnosed(self):
        b=SessionScenes(midway=True,stuck=True); r=run(b,minimal())
        self.assertEqual(b.clicks,['timeout'])
        b.save_diagnostic.assert_called_once()
        self.assertIn('60 秒',r.reason)

    def test_diamond_reconnect_returns_to_original_flow_without_generic_guard(self):
        b=SessionScenes(midway=True,unfinished=False)
        c=dict(initial_state='Home',max_minutes=0,states={
            'Home':dict(timeout_seconds=60,rules=[dict(name='claim',template='钻石免费按钮.png',
                action='click',verify_change=True,success_match={'template':'reward'},next='Home')])})
        r=RecoveryRunner(b,c,Mock(),False,Clock())
        claim=r.config['states']['Home']['rules'][0]
        r.pending_action=('Home',claim)
        rule=next(x for x in r.config['interrupts'] if x.get('requires_match',{}).get('template')=='会话超时提示.png')
        with patch.object(r.stop_event,'wait',return_value=False):
            self.assertTrue(r.click_with_change_confirmation(rule,Match((666,401,64,36),1)))
        self.assertFalse(r.guard_enabled)
        self.assertNotIn('action_guard',claim)
        self.assertEqual(r._action_attempts,{})
        self.assertEqual(rule['next'],'Home')
        self.assertEqual(b.clicks,['timeout','login'])


class SessionPixelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        c=Offline(); c.post_connection().wait(); cls.reco=MaaRecognizer(ROOT/'assets',c)
        cls.rule=next(r for r in recovery_rules() if r.get('requires_match',{}).get('template')=='会话超时提示.png')
    @classmethod
    def tearDownClass(cls): cls.reco.cancel().wait()
    def read(self,path):
        with Image.open(path) as im: return np.asarray(im.convert('RGB'))[:,:,::-1].copy()
    def frame(self):
        frame=np.full((720,1395,3),35,np.uint8)
        for name,x,y in [('会话超时提示.png',523,251),('提示确认文字.png',666,401)]:
            im=self.read(ROOT/'assets/image'/name); h,w=im.shape[:2]; frame[y:y+h,x:x+w]=im
        return frame
    def hit(self,frame,rule=None):return self.reco.recognize(frame,rule or self.rule,threading.Event())

    def test_context_and_button_required_and_not_dimmed(self):
        frame=self.frame(); self.assertIsNotNone(self.hit(frame))
        for box in [(650,251,746,278),(523,303,873,375),(660,395,740,445)]:
            wrong=frame.copy(); x,y,x2,y2=box; wrong[y:y2,x:x2]=35
            self.assertIsNone(self.hit(wrong),box)
        self.assertIsNone(self.hit((frame*.4).astype(np.uint8)))

    def test_original_timeout_and_unrelated_confirm_are_not_session_expired(self):
        frame=self.frame();frame[235:385,510:890]=35
        timeout=self.read(ROOT/'assets/image/游戏timeout提示.png')
        h,w=timeout.shape[:2];frame[310:310+h,600:600+w]=timeout
        self.assertIsNone(self.hit(frame))

    @unittest.skipUnless(FIX.exists(),'Private session-expired screenshot not distributed')
    def test_actual_modal_clicks_confirm_then_login_and_verifies_home(self):
        frame=self.read(FIX); reco=self.reco
        class Backend(SessionScenes):
            def capture(self,stop):return frame if self.page=='timeout' else self.page
            def recognize(self,image,rule,stop):
                if hasattr(image,'shape'):return reco.recognize(image,rule,stop)
                return super().recognize(image,rule,stop)
            def click(self,match,offset,stop,pause):
                if self.page=='timeout':
                    x,y,w,h=match.box
                    assert 510<=x+w//2<=885 and 395<=y+h//2<=440
                return super().click(match,offset,stop,pause)
        b=Backend(midway=True,unfinished=False)
        c=dict(initial_state='Home',max_minutes=0,poll_seconds=.1,states={
            'Home':dict(timeout_seconds=45,rules=[dict(name='home',template='主界面锚点.png',
                       action='none',event='task_complete',confirm_frames=2)])})
        r=run(b,c)
        self.assertTrue(r.completed,r.reason)
        self.assertEqual(b.clicks,['timeout','login'])
        self.assertEqual(r.stats.rounds,0)

    @unittest.skipUnless(FIX.exists(),'Private session-expired screenshot not distributed')
    def test_actual_failure_interrupts_original_claim_verifier(self):
        frame=self.read(FIX)
        self.assertIsNone(self.hit(frame,recovery_rules()[0]))
        hit=self.hit(frame); self.assertIsNotNone(hit)
        self.assertGreaterEqual(hit.score,.94)
        print('SESSION_EXPIRED_MATCH',hit.score,hit.box)
        # A submitted click sees the actual modal and yields to recovery before
        # retrying the dimmed source. No v0.3.0 action protocol is active.
        from tests.upstream.test_runner import FakeBackend
        from bk2.diamond_profile import build_diamond_profile
        reco=self.reco
        class Backend(FakeBackend):
            def recognize(self,image,rule,stop):return reco.recognize(image,rule,stop)
        b=Backend([frame]); r=RecoveryRunner(b,build_diamond_profile(),Mock(),False,Clock())
        r.stats.state='InspectPack'
        claim=next(x for x in r.config['states']['InspectPack']['rules'] if x['template']=='钻石免费按钮.png')
        with patch.object(r.stop_event,'wait',return_value=False):
            self.assertFalse(r.click_with_change_confirmation(claim,Match((344,503,69,35),.99)))
        self.assertEqual(b.clicks,1)
        self.assertFalse(r.guard_enabled)
        self.assertEqual(r.action_guard,{})

    @unittest.skipUnless((ROOT/'tests/fixtures/foreground_handoff_20261019/buff.png').exists(),
                         'Private negative screenshots not distributed')
    def test_unrelated_rewards_and_leave_prompts_rejected(self):
        for name in ['foreground_handoff_20261019/buff.png','foreground_handoff_20261019/skill.png',
                     'diamond_fix_20261018/reward.png','diamond_fix_20261018/shop.png',
                     'food_20261018/reward-maa.png']:
            self.assertIsNone(self.hit(self.read(ROOT/'tests/fixtures'/name)),name)


if __name__=='__main__': unittest.main()
