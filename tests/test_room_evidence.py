"""Real recognition with simulated input; private failure frames stay local."""
import copy
import json
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import numpy as np
from PIL import Image
from bk2.automation import load_plan
from bk2.battle import BattleRunner
from bk2.profiles import build_dungeon_profile
from bk2.recovery import add_resume_start
from vendor_core.backend import MaaRecognizer
from vendor_core.config import validate
from tests.test_unified_replay import Offline

ROOT=Path(__file__).resolve().parents[1]
FIX=ROOT/'tests/fixtures/navigation_20261018'


class Frames:
    @classmethod
    def setUpClass(cls):
        c=Offline();c.post_connection().wait();cls.reco=MaaRecognizer(ROOT/'assets',c)
        cls.plan=load_plan()
        cls.base=json.loads((ROOT/'config/profiles/dungeon_unlimited.json').read_text(encoding='utf8'))
        cls.config=build_dungeon_profile(cls.base,cls.plan['tasks'][5],cls.plan['tasks'])
        cls.stairs=next(r for r in cls.config['states']['Progress']['rules'] if r['template']=='下一层阶梯核心.png')
        cls.cards=next(r for r in cls.config['states']['Progress']['rules'] if r['template']=='技能卡中牌核心.png')
    @classmethod
    def tearDownClass(cls):cls.reco.cancel().wait()
    def read(self,path):
        with Image.open(ROOT/path) as im:return np.asarray(im.convert('RGB'))[:,:,::-1].copy()
    def frame(self,items):
        f=np.full((720,1395,3),30,np.uint8)
        for name,x,y in items:
            im=self.read('assets/image/'+name);h,w=im.shape[:2];f[y:y+h,x:x+w]=im
        return f
    def hit(self,f,r):return self.reco.recognize(f,r,threading.Event())


class RoomEvidenceTests(Frames,unittest.TestCase):
    def test_two_parts_required_and_dim_overlay_rejected(self):
        for r,parts in [(self.stairs,[('下一层阶梯核心.png',672,610),('下一层文字.png',659,687)]),
                        (self.cards,[('技能卡中牌核心.png',1019,285),('技能卡门徽核心.png',1016,370)])]:
            with self.subTest(template=r['template']):
                self.assertIsNotNone(self.hit(self.frame(parts),r))
                for f in [self.frame(parts[:1]),self.frame(parts[1:]),(self.frame(parts)*.5).astype(np.uint8)]:
                    self.assertIsNone(self.hit(f,r))
    def test_wrong_position_and_unrelated_control_cannot_match(self):
        misplaced=self.frame([('下一层阶梯核心.png',100,300),('下一层文字.png',100,380),
                              ('技能卡中牌核心.png',100,400),('技能卡门徽核心.png',100,480)])
        for r in (self.stairs,self.cards):self.assertIsNone(self.hit(misplaced,r))
    def test_all_dungeons_and_resume_use_guarded_rules_without_lowering_old_thresholds(self):
        before=copy.deepcopy(self.base)
        for task in self.plan['tasks'][:6]:
            c=build_dungeon_profile(self.base,task,self.plan['tasks'])
            for state in ('Progress','OpeningBuffLeaveConfirm','SkillCardLeaveConfirm'):
                old=next(r for r in c['states'][state]['rules'] if r['template']=='去下一层.png')
                core=next(r for r in c['states'][state]['rules'] if r['template']=='下一层阶梯核心.png')
                self.assertEqual(old['threshold'],.95)
                self.assertEqual(core['threshold'],.95)
                self.assertEqual(core['requires_match']['threshold'],.95)
                self.assertEqual(core['next'],old['next'])
                self.assertEqual(core['retry_max_clicks'],2)
            card=next(r for r in c['states']['Progress']['rules'] if r['template']=='技能卡中牌核心.png')
            self.assertEqual(card['success_match']['template'],'选择技能卡.png')
            add_resume_start(c);validate(c,ROOT/'assets/image',True)
            starts=[r for r in c['states']['ResumeStart']['rules'] if r['template']=='下一层阶梯核心.png']
            self.assertTrue(starts)
            self.assertTrue(all(r['action']=='none' and r['requires_match'] for r in starts))
        self.assertEqual(self.base,before)


@unittest.skipUnless(FIX.exists(),'Private room failure screenshots are not distributed')
class RoomFailureReplay(Frames,unittest.TestCase):
    def test_previous_rules_reproduce_latest_two_misses(self):
        for name,state in [('stairs','OpeningBuffLeaveConfirm'),('cards','Progress')]:
            f=self.read(f'tests/fixtures/navigation_20261018/{name}.png')
            rules=[r for r in self.config['states'][state]['rules'] if r['template'] not in ('下一层阶梯核心.png','技能卡中牌核心.png')]
            self.assertFalse(any(self.hit(f,r) for r in rules),name)
    def test_stairs_match_stop_warning_and_both_previous_frames(self):
        for name in ('stairs','stairs-previous','stairs_warning','stairs_warning-previous'):
            h=self.hit(self.read(f'tests/fixtures/navigation_20261018/{name}.png'),self.stairs)
            self.assertIsNotNone(h,name);self.assertGreaterEqual(h.score,.95)
            x,y,w,hgt=h.box;self.assertTrue(650<x+w//2<750 and 595<y+hgt//2<690)
    def test_cards_match_both_animation_frames_and_click_same_door(self):
        for name in ('cards','cards-previous'):
            h=self.hit(self.read(f'tests/fixtures/navigation_20261018/{name}.png'),self.cards)
            self.assertIsNotNone(h);self.assertGreaterEqual(h.score,.95)
            x,y,w,hgt=h.box;self.assertTrue(1000<x+w//2<1090 and 360<y+hgt//2+64<410)
    def test_other_scenes_and_foreground_panels_rejected(self):
        paths=['list_guard_20261018/battle.png','defeat_20261018/defeat.png',
               'failures_20261018/buff_selection.png','list_guard_20261018/timeout.png',
               'diamond_fix_20261018/reward.png','food_20261018/reward-maa.png',
               'failures_20261003/dungeon_list.png','diamonds_20261017/frame_007.jpg']
        for path in paths:
            frame=self.read('tests/fixtures/'+path)
            for r in (self.stairs,self.cards):self.assertIsNone(self.hit(frame,r),(path,r['name']))
    def replay(self,state,card=False,stuck=False):
        test=self
        first=self.read('tests/fixtures/navigation_20261018/'+('cards.png' if card else 'stairs.png'))
        after=(self.frame([('选择技能卡.png',612,422)]) if card else
               self.read('tests/fixtures/diamonds_20261017/frame_007.jpg'))
        class Backend:
            def __init__(self):self.clicks=[];self.page=0;self.save_diagnostic=Mock(return_value='offline.json')
            def guard(self):pass
            def cancel(self):pass
            def capture_is_fresh(self,*args):return True
            def capture(self,stop):return first if self.page==0 else after
            def recognize(self,*args):return test.reco.recognize(*args)
            def click(self,match,offset,stop,pause):
                if stop.is_set() or pause.is_set():raise InterruptedError()
                self.clicks.append(match.box)
                if not stuck:self.page=1
        backend=Backend();c=copy.deepcopy(self.config);c.update(initial_state=state,max_minutes=1)
        end='SkillCardSelect' if card else 'Progress'
        # A terminal observation for this offline harness, never a claim that
        # the game returns to town after one staircase click.
        c['states'][end]['rules'].insert(0,dict(name='回放后继页面',template='选择技能卡.png' if card else '主界面锚点.png',
            threshold=.95,action='none',event='task_complete',confirm_frames=2))
        clock=[100.]
        def now():clock[0]+=.1;return clock[0]
        r=BattleRunner(backend,c,Mock(),False,now)
        with patch.object(r.stop_event,'wait',return_value=False):r.run()
        return r,backend
    def test_buff_skill_and_progress_stair_clicks_advance_once_without_counting_win(self):
        for state in ('OpeningBuffLeaveConfirm','SkillCardLeaveConfirm','Progress'):
            r,b=self.replay(state)
            self.assertTrue(r.completed,r.reason);self.assertEqual(len(b.clicks),1)
            self.assertEqual(r.stats.rounds,0)
    def test_skill_room_requires_selection_page_after_one_click(self):
        r,b=self.replay('Progress',card=True)
        self.assertTrue(r.completed,r.reason);self.assertEqual(len(b.clicks),1)
        self.assertEqual(r.stats.rounds,0)
    def test_stuck_stair_stops_after_two_attempts_and_keeps_diagnostic(self):
        r,b=self.replay('OpeningBuffLeaveConfirm',stuck=True)
        self.assertFalse(r.completed);self.assertEqual(len(b.clicks),2)
        b.save_diagnostic.assert_called_once()


if __name__=='__main__':unittest.main()
