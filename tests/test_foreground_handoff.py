"""A staircase may open a known leave prompt; never click through its overlay."""
import copy
import json
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from bk2.action_guard import GuardedRunner, guarded_profile
from bk2.battle import BattleRunner
from bk2.profiles import build_dungeon_profile
from bk2.recovery import add_resume_start
from tests.upstream.test_runner import Clock, FakeBackend
from vendor_core.backend import Match

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT/'tests/fixtures/foreground_handoff_20261019'
MATCH = Match((10,20,30,40),.99)


def config():
    prompt = dict(name='confirm leave',template='离开祝福弹窗.png',foreground=True,
                  action='click',verify_change=True,threshold=.96,next='Progress')
    stair = dict(name='stairs',template='去下一层.png',action='click',verify_change=True,
                 threshold=.95,next='Progress',min_contrast_ratio=.65,confirm_frames=2)
    return dict(initial_state='Leave',max_minutes=0,poll_seconds=.001,states={
        'Leave':dict(timeout_seconds=60,rules=[prompt,stair]),
        'Progress':dict(timeout_seconds=30,rules=[dict(name='fixture end',template='end',
                       action='none',event='task_complete',confirm_frames=2)])})


class ForegroundHandoffTests(unittest.TestCase):
    def runner(self, frames):
        b=FakeBackend(frames); r=GuardedRunner(b,config(),Mock(),False,Clock())
        return r,b,r.config['states']['Leave']['rules'][1]

    def invoke(self,r,rule):
        with patch.object(r.stop_event,'wait',side_effect=lambda _:r.stop_event.is_set()):
            return r.click_with_change_confirmation(rule,MATCH)

    def test_two_frame_prompt_hands_back_without_success_or_extra_stair_click(self):
        r,b,rule=self.runner([{'离开祝福弹窗.png','去下一层.png'}])
        self.assertFalse(self.invoke(r,rule))
        self.assertEqual(b.clicks,1)
        self.assertEqual(b.index,2)
        self.assertEqual(r.action_guard['status'],'foreground_handoff')
        self.assertEqual(r.action_guard['foreground_template'],'离开祝福弹窗.png')
        self.assertEqual(r.counters,{})
        self.assertFalse(r.completed)
        self.assertTrue(r._action_attempts)

    def test_confirmed_handler_releases_original_budget_without_counting_a_win(self):
        r,b,rule=self.runner(['离开祝福弹窗.png'])
        self.assertFalse(self.invoke(r,rule))
        b.frames=['end']
        self.assertTrue(self.invoke(r,r.config['states']['Leave']['rules'][0]))
        self.assertEqual(b.clicks,2)
        self.assertFalse(r._action_attempts)
        self.assertFalse(r._foreground_handoffs)
        self.assertEqual(r.stats.rounds,0)
        self.assertTrue(any(e['event']=='foreground_handoff_resolved' for e in r.history))

    def test_failed_handler_cannot_release_stair_budget(self):
        r,b,rule=self.runner(['离开祝福弹窗.png'])
        self.assertFalse(self.invoke(r,rule))
        with self.assertRaises(RuntimeError): self.invoke(r,r.config['states']['Leave']['rules'][0])
        self.assertEqual(b.clicks,3) # one stair + at most two confirmation attempts
        self.assertTrue(r._foreground_handoffs)
        self.assertEqual(r._action_attempts[r._budget_key('Leave',rule)],1)

    def test_one_frame_popup_is_not_enough_and_does_not_click_background(self):
        r,b,rule=self.runner(['离开祝福弹窗.png',{'去下一层.png'}])
        # The target is visible but fails its complete source guard (dimmed).
        original=b.recognize
        def recognize(frame,rule,stop):
            if rule['template']=='去下一层.png' and 'min_contrast_ratio' in rule: return None
            return original(frame,rule,stop)
        b.recognize=recognize
        with self.assertRaises(RuntimeError): self.invoke(r,rule)
        self.assertEqual(b.clicks,1)
        self.assertFalse(r._foreground_handoffs)

    def test_different_popup_is_not_handed_to_wrong_handler(self):
        r,b,rule=self.runner([{'不要剩余技能卡确定离开.png','去下一层.png'}])
        rule['requires_match']={'template':'unobscured-source'}
        with self.assertRaises(RuntimeError): self.invoke(r,rule)
        self.assertFalse(r._foreground_handoffs)
        self.assertEqual(b.clicks,1)

    def test_global_interrupt_remains_higher_priority(self):
        r,b,rule=self.runner([{'timeout','离开祝福弹窗.png'}])
        r.config['interrupts']=[dict(name='timeout',template='timeout')]
        self.assertFalse(self.invoke(r,rule))
        self.assertEqual(r.action_guard['status'],'interrupted_budget_retained')
        self.assertFalse(r._foreground_handoffs)

    def test_only_stairs_get_existing_local_prompt_handlers(self):
        c=config(); c['states']['Leave']['rules'].append(dict(c['states']['Leave']['rules'][1],
            name='unrelated navigation',template='other'))
        before=copy.deepcopy(c); g=guarded_profile(c)
        self.assertEqual(c,before)
        self.assertEqual(g['states']['Leave']['rules'][1]['guard_handoffs'],['confirm leave'])
        self.assertNotIn('guard_handoffs',g['states']['Leave']['rules'][2])
        self.assertEqual(g['states']['Leave']['rules'][0]['threshold'],.96)
        self.assertEqual(g['states']['Leave']['rules'][1]['threshold'],.95)

    def test_stop_after_handoff_prevents_confirmation(self):
        r,b,rule=self.runner(['离开祝福弹窗.png'])
        self.assertFalse(self.invoke(r,rule))
        r.stop_event.set()
        with self.assertRaises(InterruptedError): self.invoke(r,r.config['states']['Leave']['rules'][0])
        self.assertEqual(b.clicks,1)


@unittest.skipUnless(FIX.is_dir(),'Private failure screenshots are not distributed')
class ForegroundHandoffReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import numpy as np
        from PIL import Image
        from bk2.automation import load_plan
        from vendor_core.backend import MaaRecognizer
        from tests.test_unified_replay import Offline
        cls.frames={name:np.asarray(Image.open(FIX/(name+'.png')).convert('RGB'))[:,:,::-1].copy()
                    for name in ('buff','skill')}
        cls.plan=load_plan()
        cls.base=json.loads((ROOT/'config/profiles/dungeon_unlimited.json').read_text(encoding='utf-8-sig'))
        controller=Offline();controller.post_connection().wait()
        cls.reco=MaaRecognizer(ROOT/'assets',controller)
    @classmethod
    def tearDownClass(cls): cls.reco.cancel().wait()

    def profile(self):
        return build_dungeon_profile(self.base,self.plan['tasks'][3],self.plan['tasks'])

    def replay(self, name, legacy=False, resume=False, stuck=False):
        frame=self.frames[name]; reco=self.reco
        state='OpeningBuffLeaveConfirm' if name=='buff' else 'SkillCardLeaveConfirm'
        c=self.profile(); c.update(initial_state=state,max_minutes=0,poll_seconds=.001)
        c.pop('watchdog',None)
        c['states']['Progress']['rules'].insert(0,dict(name='fixture next room',template='fixture-end',
            action='none',event='task_complete',confirm_frames=2))
        if resume: add_resume_start(c)
        class Backend(FakeBackend):
            page='popup' if resume else 'stairs'
            actions=[]
            def capture(self,stop):
                return frame if self.page=='popup' else {'去下一层.png'} if self.page=='stairs' else {'fixture-end'}
            def recognize(self,image,rule,stop):
                if hasattr(image,'shape'):
                    # Fixture-only terminal evidence has no PNG template.
                    if rule['template']=='fixture-end': return None
                    return reco.recognize(image,rule,stop)
                match=super().recognize(image,rule,stop)
                # Use the actual discovery coordinates so the dimmed staircase
                # behind the modal cannot masquerade as a moved target.
                if match and rule['template']=='去下一层.png':
                    return Match((633,584,130,123),.99049)
                return match
            def click(self,match,offset,stop,pause):
                super().click(match,offset,stop,pause)
                self.actions.append(self.page)
                if self.page=='stairs': self.page='popup'
                else:
                    x,y,w,h=match.box; point=(x+w//2+offset[0],y+h//2+offset[1])
                    if not (650<=point[0]<=890 and 380<=point[1]<=445):
                        raise AssertionError(f'Click outside the confirmation button: {point}')
                    if not stuck: self.page='after'
            def save_diagnostic(self,*args):return 'offline.json'
        b=Backend([]); b.actions=[]
        r=BattleRunner(b,c,Mock(),False,Clock())
        if legacy:
            for s in r.config['states'].values():
                for rule in s['rules']:rule.pop('guard_handoffs',None)
        with patch.object(r.stop_event,'wait',return_value=False):r.run()
        return r,b

    def test_actual_two_failures_reproduce_without_handoff(self):
        for name in self.frames:
            with self.subTest(name=name):
                r,b=self.replay(name,legacy=True)
                self.assertFalse(r.completed)
                self.assertIn('已提交 1/2 次',r.reason)
                self.assertEqual(b.actions,['stairs'])

    def test_actual_two_prompts_handoff_confirm_then_continue(self):
        for name in self.frames:
            with self.subTest(name=name):
                r,b=self.replay(name)
                self.assertTrue(r.completed,r.reason)
                self.assertEqual(b.actions,['stairs','popup'])
                self.assertEqual(r.stats.rounds,0)
                self.assertFalse(r._foreground_handoffs)
                self.assertTrue(any(e.get('status')=='foreground_handoff' for e in r.history))

    def test_resume_at_actual_prompt_only_clicks_confirmation(self):
        for name in self.frames:
            with self.subTest(name=name):
                r,b=self.replay(name,resume=True)
                self.assertTrue(r.completed,r.reason)
                self.assertEqual(b.actions,['popup'])
                self.assertEqual(r.stats.rounds,0)

    def test_actual_prompts_block_stairs_and_each_other(self):
        c=self.profile()
        buff=c['states']['OpeningBuffLeaveConfirm']['rules'][0]
        skill=c['states']['SkillCardLeaveConfirm']['rules'][0]
        for name,handler,wrong in [('buff',buff,skill),('skill',skill,buff)]:
            frame=self.frames[name]
            hit=self.reco.recognize(frame,handler,threading.Event())
            self.assertIsNotNone(hit,name)
            print('LEAVE_PROMPT',name,hit.score,hit.box)
            self.assertGreaterEqual(hit.score,.96)
            self.assertIsNone(self.reco.recognize(frame,wrong,threading.Event()))
            for state in ('OpeningBuffLeaveConfirm','SkillCardLeaveConfirm','Progress'):
                for rule in c['states'][state]['rules']:
                    if rule['template'] in ('去下一层.png','下一层阶梯核心.png'):
                        self.assertIsNone(self.reco.recognize(frame,rule,threading.Event()))


if __name__=='__main__': unittest.main()
