"""Shop context and completion routing: real matcher, simulated input."""
import copy
import json
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image
from bk2.diamond_profile import build_diamond_profile, CLOSE
from bk2.recovery import RecoveryRunner, LIST_RULE
from tests.test_scene_variants import VariantRecognizer
from tests.test_unified_replay import Offline
from tests.test_recovery_020 import minimal, Scenes, Clock
from vendor_core.backend import Match

ROOT = Path(__file__).resolve().parents[1]


class CompletionRoutingTests(unittest.TestCase):
    def runner(self, stuck=False):
        class WorldReturn(Scenes):
            def recognize(self, frame, rule, stop):
                if frame == 'world':
                    if rule['template'] not in ('地下城入口主体.png', '关卡导航选中.png'):
                        return None
                    return Match((555,600,80,80),1.)
                return super().recognize(frame,rule,stop)
            def click(self, *args):
                self.clicks.append(self.page)
                if self.page == 'entry': self.page='room'
                elif self.page == 'room': self.page='world'
                elif self.page == 'world' and not stuck: self.page='list'
        b=WorldReturn()
        config=minimal()
        config['states']['Progress']['rules'][0].update(
            verify_change=True,success_match=copy.deepcopy(LIST_RULE),
            retry_max_clicks=2,retry_center_clicks=1,retry_radius=0)
        return RecoveryRunner(b,config,Mock(),False,Clock()),b

    def test_pending_completion_routes_world_to_list_and_counts_once(self):
        r,b=self.runner()
        with patch.object(r.stop_event,'wait',return_value=False): r.run()
        self.assertEqual(r.reason,"达到完成轮数上限")
        self.assertEqual(r.stats.rounds,1)
        self.assertEqual(b.clicks,['entry','room','world'])

    def test_failed_world_navigation_never_counts_and_is_bounded(self):
        r,b=self.runner(True)
        with patch.object(r.stop_event,'wait',return_value=False): r.run()
        self.assertFalse(r.completed)
        self.assertEqual(r.stats.rounds,0)
        self.assertLessEqual(b.clicks.count('world'),2)

    def test_unarmed_or_unrelated_pending_cannot_finish_on_world(self):
        r,b=self.runner();r.stats.state='Progress'
        completion=r.config['states']['Progress']['rules'][0]
        for armed,pending in [(False,('Progress',completion)),(True,None),
                              (True,('Explore',completion)),
                              (True,('Progress',dict(completion,next='Progress')))]:
            r.round_armed=armed;r.pending_action=pending
            self.assertIsNone(r.pending_success('world'))
        self.assertEqual(b.clicks,[])


class ShopContextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        controller=Offline();controller.post_connection().wait()
        cls.reco=VariantRecognizer(ROOT/'assets',controller)
    @classmethod
    def tearDownClass(cls): cls.reco.cancel().wait()

    def frame(self, controls):
        frame=np.full((720,1395,3),35,np.uint8)
        for name,x,y in controls:
            with Image.open(ROOT/'assets/image'/name) as im:
                tile=np.asarray(im.convert('RGB'))[:,:,::-1].copy()
            h,w=tile.shape[:2];frame[y:y+h,x:x+w]=tile
        return frame

    def test_shop_title_alone_is_not_a_shop_or_ocr_authorization(self):
        profile=build_diamond_profile()
        rules=[r for state in profile['states'].values() for r in state['rules']
               if r['template']=='钻石免费包标识.png']
        title=('钻石免费包标识.png',245,240)
        good=self.frame([title,(CLOSE,1170,120)])
        for rule in rules:
            with self.subTest(rule=rule['name']):
                self.assertIsNone(self.reco.recognize(self.frame([title]),rule,threading.Event()))
                self.assertIsNotNone(self.reco.recognize(good,rule,threading.Event()))
                self.assertIsNone(self.reco.recognize((good*.4).astype(np.uint8),rule,threading.Event()))
                wrong=self.frame([title,(CLOSE,900,400)])
                self.assertIsNone(self.reco.recognize(wrong,rule,threading.Event()))

    def test_shop_dependency_guard_applies_to_nested_success_evidence(self):
        from vendor_core.config import validate
        profile=build_diamond_profile()
        validate(profile,ROOT/'assets/image',True)
        def visit(value):
            if isinstance(value,dict):
                if value.get('template')=='钻石免费包标识.png':
                    self.assertEqual(value['requires_match']['template'],CLOSE)
                for child in value.values(): visit(child)
            elif isinstance(value,list):
                for child in value: visit(child)
        visit(profile)

    def test_nested_context_rejects_actions_invalid_values_missing_files_and_cycles(self):
        from vendor_core.config import validate
        for invalid in [dict(action='click'),dict(roi=[0,0,-1,10]),
                        dict(threshold=float('nan')),dict(template='../outside.png'),
                        dict(template='missing-context.png')]:
            with self.subTest(invalid=invalid):
                p=build_diamond_profile()
                guard=p['states']['OpenDiamond']['rules'][0]['success_match']['requires_match']
                guard.update(invalid)
                with self.assertRaises(ValueError):validate(p,ROOT/'assets/image',True)
        p=build_diamond_profile()
        guard=p['states']['OpenDiamond']['rules'][0]['success_match']['requires_match']
        guard['requires_match']=guard
        with self.assertRaises(ValueError):validate(p,ROOT/'assets/image',True)


if __name__=='__main__': unittest.main()
