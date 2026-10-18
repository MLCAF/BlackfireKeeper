"""Offline list OCR, name association, bounded dragging and schedule integration."""
import json
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import numpy as np
from PIL import Image
from vendor_core.backend import MaaRecognizer
from vendor_core.cooldown import DeferredCooldown
from vendor_core.config import validate
from vendor_core.list_cooldown import ListCooldownScanner, LIST_RULE, timer_roi, list_navigation_profile
from vendor_core.unified import UnifiedScheduler
from tests.upstream.test_town_portal import Offline
from tests.upstream.test_selected_dungeons import plan_for

ROOT=Path(__file__).resolve().parents[2]

class ReplayBackend:
    def __init__(self,reco,frames,index=0):
        self.reco,self.frames,self.index=reco,frames,index
        self.drags,self.ocr_rois=[],[]
    def capture(self,stop): return self.frames[self.index]
    def recognize(self,*args): return self.reco.recognize(*args)
    def read_timer_roi(self,image,stop,captured,roi):
        self.ocr_rois.append(roi)
        return self.reco.read_timer_roi(image,stop,captured,roi)
    def swipe(self,direction,stop,pause):
        if stop.is_set() or pause.is_set(): raise InterruptedError()
        self.drags.append(direction)
        self.index=max(0,min(len(self.frames)-1,self.index+(1 if direction=='left' else -1)))
    def click(self,*args): raise AssertionError('List scan must never click a building or detail')

@unittest.skipUnless((ROOT/'tests/fixtures').is_dir(), 'Local replay screenshots are not distributed; see tests/README.md')
class ListReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.controller=Offline(); cls.controller.post_connection().wait()
        cls.reco=MaaRecognizer(ROOT/'assets',cls.controller)
        cls.plan=plan_for(1,2,3,4,5,6)
    @classmethod
    def tearDownClass(cls): cls.reco.cancel().wait()
    def frame(self,path):
        with Image.open(ROOT/path) as im: return np.asarray(im.convert('RGB'))[:,:,::-1].copy()
    def synthetic_page(self,orders):
        frame=np.zeros((720,1394,3),dtype=np.uint8)
        for center,order in zip((250,650,1050),orders):
            task=self.plan['tasks'][order-1]
            crop=self.frame('assets/image/'+task['display_name']+'.png')
            match=self.reco.recognize(crop,dict(name='anchor',template=task['entrance_template'],threshold=.95),threading.Event())
            x,y,w,h=match.box; left=center-(x+w//2); top=475-(y+h//2)
            frame[top:top+crop.shape[0],left:left+crop.shape[1]]=crop
        return frame
    def scanner(self,frames,ranks,index=0):
        backend=ReplayBackend(self.reco,frames,index); now=[100.]
        stop,pause=threading.Event(),threading.Event()
        def wait(seconds): now[0]+=seconds; return stop.is_set()
        scanner=ListCooldownScanner(backend,plan_for(*ranks)['tasks'],stop,pause,Mock(),
                                    clock=lambda:now[0],wall=lambda:now[0]+1000,wait=wait)
        return scanner,backend,now
    def test_real_list_binds_third_timer_not_second(self):
        s,b,now=self.scanner([self.frame('tests/fixtures/failures_20261016/title_click.png')],(2,3))
        result=s.scan()
        self.assertEqual(result['dungeon_2'],'ready')
        self.assertEqual(result['dungeon_3'].text,'00:10:26')
        self.assertAlmostEqual(result['dungeon_3'].ready_at,1729.3)
        self.assertEqual(b.drags,[])
        print('LIST_REPLAY third',result['dungeon_3'])
    def test_user_supplied_crop_reads_001327(self):
        frame=self.frame('tests/fixtures/list_ocr_20261017/user_timer.png')
        sample=self.reco.read_timer_roi(frame,threading.Event(),100,(78,43,116,39))
        self.assertIsNotNone(sample)
        self.assertEqual(sample.seconds,807)
        print('USER_LIST_TIMER',sample)
    def test_left_drag_finds_sixth_without_reading_unselected_timers(self):
        s,b,now=self.scanner([self.synthetic_page((1,2,3)),self.synthetic_page((4,5,6))],(2,6))
        result=s.scan()
        self.assertEqual(set(result),{'dungeon_2','dungeon_6'})
        self.assertEqual(result['dungeon_2'],'ready')
        self.assertEqual(result['dungeon_6'].text,'00:18:51')
        self.assertEqual(b.drags,['left'])
        self.assertEqual(len(b.ocr_rois),4)
    def test_right_drag_returns_to_second(self):
        s,b,_=self.scanner([self.synthetic_page((1,2,3)),self.synthetic_page((4,5,6))],(2,6),1)
        result=s.scan(); self.assertEqual(set(result),{'dungeon_2','dungeon_6'})
        self.assertEqual(b.drags,['right'])
    def test_all_available_templates_have_positive_evidence(self):
        s,b,_=self.scanner([self.synthetic_page((1,2,3)),self.synthetic_page((4,5,6))],(2,3,4,5))
        self.assertEqual(s.scan(),{f'dungeon_{i}':'ready' for i in (2,3,4,5)})
    def test_shifted_names_move_timer_roi_with_them(self):
        frame=self.frame('tests/fixtures/failures_20261016/title_click.png')
        shifted=np.zeros_like(frame); shifted[:,:-120]=frame[:,120:]
        s,b,_=self.scanner([shifted],(3,))
        self.assertEqual(s.scan()['dungeon_3'].text,'00:10:26')
        self.assertEqual(b.ocr_rois[0][0],839)
    def test_unknown_timer_is_not_ready(self):
        frame=self.frame('tests/fixtures/failures_20261016/title_click.png')
        frame[275:330,950:1070]=0
        s,b,_=self.scanner([frame],(3,)); self.assertEqual(s.scan(),{})
    def test_overlay_or_detail_prevents_ocr_and_swipe(self):
        for frame in [self.frame('tests/fixtures/failures_20261017/20261017-102326-610305.png'),
                      (self.frame('tests/fixtures/failures_20261016/title_click.png')*.2+30).astype(np.uint8)]:
            s,b,_=self.scanner([frame],(2,6))
            self.assertEqual(s.scan(),{}); self.assertEqual(b.drags,[]); self.assertEqual(b.ocr_rois,[])
    def test_stuck_list_is_bounded(self):
        s,b,_=self.scanner([self.synthetic_page((1,2,3))],(6,))
        self.assertEqual(s.scan(),{}); self.assertLessEqual(len(b.drags),2)
    def test_pause_or_stop_does_not_touch_list(self):
        for event in ('pause','stop'):
            s,b,_=self.scanner([self.synthetic_page((1,2,3))],(6,))
            getattr(s,event).set(); self.assertEqual(s.scan(),{}); self.assertEqual(b.drags,[])
    def test_unselected_third_not_read(self):
        s,b,_=self.scanner([self.frame('tests/fixtures/failures_20261016/title_click.png')],(2,))
        self.assertEqual(s.scan(),{'dungeon_2':'ready'})
        self.assertTrue(all(roi[0]==583 for roi in b.ocr_rois))
    def test_navigation_profile_can_never_select_a_dungeon(self):
        base=json.loads((ROOT/'config/profiles/dungeon_unlimited.json').read_text(encoding='utf-8'))
        nav=list_navigation_profile(base,self.plan['tasks'][1],self.plan['tasks'])
        validate(nav,ROOT/'assets/image',True)
        self.assertEqual(set(nav['states']),{'Entry','ListReady'})
        for state in nav['states'].values():
            for rule in state['rules']:
                self.assertNotIn(rule.get('event'),('round_start','round_end'))
                self.assertNotIn(rule['template'],['去探险.png']+[t['entrance_template'] for t in self.plan['tasks']])

class ListScheduleTests(unittest.TestCase):
    def scheduler(self,plan,run,scan,diamond=None):
        now=[0.]; stop=threading.Event()
        def wait(seconds): now[0]+=seconds; return stop.is_set()
        s=UnifiedScheduler(plan,run,diamond or Mock(return_value=(True,'ack')),stop,Mock(),
            clock=lambda:now[0],wait=wait,scan_cooldowns=scan)
        return s,now
    def test_start_scan_replaces_initial_full_cooldown(self):
        plan=plan_for(3); plan['max_total_runs']=1; calls=[]
        scan=Mock(return_value={'dungeon_3':DeferredCooldown(30,'00:00:27',.99)})
        s,now=self.scheduler(plan,lambda task:(calls.append(now[0]) is None,'done'),scan); s.run()
        self.assertEqual(calls,[30]); self.assertEqual(scan.call_count,1)
    def test_ready_detection_runs_without_initial_wait(self):
        plan=plan_for(2); plan['max_total_runs']=1; calls=[]
        s,now=self.scheduler(plan,lambda task:(calls.append(now[0]) is None,'done'),lambda:{'dungeon_2':'ready'})
        s.run(); self.assertEqual(calls,[0])
    def test_unknown_and_unselected_updates_do_not_change_deadlines(self):
        s,_=self.scheduler(plan_for(2),Mock(),Mock())
        s.apply_list_readings({'dungeon_2':None,'dungeon_3':'ready','dungeon_1':'ready'})
        self.assertEqual(s.ready_at,{'dungeon_2':300})
    def test_scan_again_at_round_boundary(self):
        plan=plan_for(2); scan=Mock(return_value={'dungeon_2':'ready'})
        s,_=self.scheduler(plan,lambda task:(True,'done'),scan); s.run()
        self.assertEqual(scan.call_count,2); self.assertEqual(s.completed_runs,2)
    def test_diamonds_due_during_scan_preempt_dungeon(self):
        plan=plan_for(2); plan['max_total_runs']=1; plan['diamonds']['enabled']=True; events=[]
        def scan(): now[0]+=5; events.append('scan'); return {'dungeon_2':'ready'}
        s,now=self.scheduler(plan,lambda task:(events.append('dungeon') is None,'done'),scan,
            lambda:(events.append('diamond') is None,'ack'))
        s.diamond_due=3; s.run()
        self.assertEqual(events,['scan','diamond','scan','dungeon'])
    def test_waiting_rescan_interval_not_every_poll(self):
        plan=plan_for(2); plan['max_total_runs']=1; seen=[]
        def scan(): seen.append(now[0]); return {}
        s,now=self.scheduler(plan,lambda task:(True,'done'),scan); s.run()
        self.assertEqual(seen,[0,60,120,180,240,300])
    def test_stop_in_scan_prevents_run(self):
        run=Mock()
        def scan(): s.stop.set(); return {'dungeon_2':'ready'}
        s,_=self.scheduler(plan_for(2),run,scan); s.run(); run.assert_not_called()





if __name__=='__main__': unittest.main()
