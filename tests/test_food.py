import copy
import json
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import numpy as np
from PIL import Image
from bk2.food import summarize
from bk2.food_profile import build_food_profile
from bk2.automation import Batch, load_plan, validate_food_options
from vendor_core.backend import MaaRecognizer
from vendor_core.config import validate
from vendor_core.runner import Runner
from tests.test_unified_replay import Offline
from tests.upstream.test_runner import Clock

ROOT=Path(__file__).resolve().parents[1]

class FoodStateTests(unittest.TestCase):
    def test_memory_traversal_decodes_only_bread_food_and_bakery_times(self):
        import struct
        from bk2.food import read_fields
        def dic(n): return (0,struct.pack('<IIQQ',27,0,n,0))
        def val(n): return (0,struct.pack('<IIdQ',3,0,n,0))
        g=Mock()
        maps={1:{'gem':val(99),'account':dic(999)},2:{'bread':val(315)},
              3:{'start_time':val(1792291304724),'finish_time':val(1789744222821),'secret':dic(999)}}
        g.dictionary_entries.side_effect=maps.__getitem__
        g.variant.side_effect=lambda raw:struct.unpack_from('<d',raw,8)[0]
        result=read_fields(g,{'bakery':dic(3),'client_token':dic(999)},
                           {'currencies':dic(1),'foods':dic(2)},{})
        self.assertEqual(result['bread'],315)
        self.assertEqual(g.variant.call_count,3)
        self.assertNotIn(999,[c.args[0] for c in g.dictionary_entries.call_args_list])
        self.assertIsNone(read_fields(g,{}, {}, {})['bread'])

    def test_partial_making_claimable_and_stale_finish_ignored(self):
        now=1792300000
        value=summarize(dict(bread=315, start_time=(now-67*80)*1000,finish_time=1789744222821),now)
        self.assertEqual((value['count'],value['produced'],value['claimable']),(315,67,5))
        self.assertEqual(value['remaining_seconds'],93*80)

    def test_full_empty_matured_and_unknown(self):
        now=1792300000
        for count,age,expected,claim in [(320,800,'full',0),(200,5,'making',0),(0,20000,'ready',160)]:
            v=summarize(dict(bread=count,start_time=(now-age)*1000),now)
            self.assertEqual((v['status'],v['claimable']),(expected,claim))
        for bad in [True,None,-1,321,float('nan'),2.5]:
            self.assertEqual(summarize(dict(bread=bad,start_time=(now-100)*1000),now)['status'],'unknown')
        self.assertIsNone(summarize({})['count'])
        self.assertEqual(summarize(dict(bread=10,start_time=(now+50)*1000),now)['status'],'unknown')

    def test_plan_boolean_validation(self):
        plan=load_plan()
        for bad in ['yes',None,1]:
            plan['food']={'enabled':bad}
            with self.assertRaises(ValueError):validate_food_options(plan)

    def test_unknown_full_disabled_and_diamond_priority_never_execute(self):
        from tests.test_migration import session
        from types import SimpleNamespace
        s=session(); plan=load_plan(); plan['food']={'enabled':True}
        batch=Batch(s,plan,Mock(),clock=lambda:1792300000)
        batch.execute=Mock()
        batch.maybe_food()
        s.snapshot['data']['food_fields']=dict(bread=320,start_time=1792290000000)
        batch.maybe_food()
        s.snapshot['data']['food_fields']['bread']=100
        batch.scheduler=SimpleNamespace(diamond_due=0)
        batch.plan['diamonds']['enabled']=True
        batch.maybe_food()
        batch.plan['food']['enabled']=False
        batch.maybe_food(after_diamonds=True)
        batch.execute.assert_not_called()

    def test_in_progress_blocks_collection(self):
        from tests.test_migration import session
        s=session();s.snapshot['data']['dungeon_progress']['result']='in progress'
        p=load_plan();p['food']={'enabled':True}
        b=Batch(s,p,Mock())
        with self.assertRaisesRegex(RuntimeError,'副本仍在进行'): b.maybe_food()

    def test_collection_requires_interface_and_memory_changes(self):
        from tests.test_migration import session
        from types import SimpleNamespace
        now=time.time();s=session();p=load_plan();p['food']={'enabled':True}
        s.snapshot['data']['food_fields']=dict(bread=315,start_time=(now-5000)*1000)
        b=Batch(s,p,Mock())
        def execute(config):
            s.snapshot['data']['food_fields']=dict(bread=320,start_time=(now-4600)*1000)
            return SimpleNamespace(completed=True,counters={'food_collections':1},reason='done')
        b.execute=execute
        self.assertEqual(b.maybe_food()['received'],5)

    def test_closed_panel_without_credit_fails(self):
        from tests.test_migration import session
        from types import SimpleNamespace
        now=time.time();s=session();p=load_plan();p['food']={'enabled':True}
        s.snapshot['data']['food_fields']=dict(bread=315,start_time=(now-5000)*1000)
        b=Batch(s,p,Mock());b.execute=Mock(return_value=SimpleNamespace(completed=True,counters={'food_collections':1},reason='done'))
        # No additional polling iteration: make the bounded verification expire.
        with patch('bk2.automation.time.monotonic',side_effect=[s.snapshot['captured_monotonic'],0,6]):
            with self.assertRaisesRegex(RuntimeError,'未确认更新'): b.maybe_food()

@unittest.skipUnless((ROOT/'tests/fixtures').is_dir(), 'Local replay screenshots are not distributed; see tests/README.md')
class FoodReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.controller=Offline();cls.controller.post_connection().wait()
        cls.reco=MaaRecognizer(ROOT/'assets',cls.controller)
    @classmethod
    def tearDownClass(cls):cls.reco.cancel().wait()
    def frame(self,name):
        if name in ('panel','reward'): name += '-maa'
        with Image.open(ROOT/'tests/fixtures/food_20261018'/f'{name}.png') as im:
            return np.asarray(im.convert('RGB'))[:,:,::-1].copy()

    def test_four_steps_on_real_frames_with_fake_input(self):
        profile=build_food_profile();validate(profile,ROOT/'assets/image',True)
        frames=[self.frame(name) for name in ('list','panel','reward','panel','list')]
        reco=self.reco
        class Replay:
            index=0
            points=[]
            def guard(self):pass
            def capture(self,stop):return frames[self.index]
            def recognize(self,image,rule,stop):return reco.recognize(image,rule,stop)
            def click(self,match,offset,stop,pause):
                x,y,w,h=match.box;self.points.append((x+w//2,y+h//2));self.index+=1
            def cancel(self):pass
        for state in profile['states'].values():
            for rule in state['rules']:
                if rule.get('action')=='click': rule['retry_wait_seconds']=.001
                rule['cooldown_seconds']=.001
        profile.pop('watchdog');profile['poll_seconds']=.001
        backend=Replay();runner=Runner(backend,profile,Mock(),False,clock=Clock())
        runner.run()
        self.assertTrue(runner.completed,runner.reason)
        self.assertEqual(backend.index,4)
        self.assertEqual(runner.counters.get('food_collections'),1)
        print('FOOD_REPLAY four clicks',backend.points)

    def test_reward_confirmation_rejects_panel_and_list(self):
        rule=build_food_profile()['states']['FoodReward']['rules'][0]
        for name in ('list','panel'):
            self.assertIsNone(self.reco.recognize(self.frame(name),rule,threading.Event()))
        self.assertIsNotNone(self.reco.recognize(self.frame('reward'),rule,threading.Event()))

    def test_dimmed_entrance_and_reward_block_collect(self):
        profile=build_food_profile()
        icon=profile['states']['FoodEntry']['rules'][1]
        self.assertIsNone(self.reco.recognize(self.frame('panel'),icon,threading.Event()))
        collect=profile['states']['FoodCollect']['rules'][0]
        self.assertIsNone(self.reco.recognize(self.frame('reward'),collect,threading.Event()))

if __name__=='__main__':unittest.main()
