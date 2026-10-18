"""Per-instance buff configuration, persistence callbacks and safe flow parameterization."""
import copy
import json
import threading
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from types import SimpleNamespace

from vendor_core.config import validate_dungeon_plan, validate
from vendor_core.unified import build_dungeon_profile, selected_buff
from vendor_core.runner import Runner
from tests.upstream.test_runner import FakeBackend, Clock

ROOT = Path(__file__).resolve().parents[2]

class Value:
    def __init__(self, value): self.value = value
    def get(self): return self.value
    def set(self, value): self.value = value

class BuffTests(unittest.TestCase):
    def setUp(self):
        self.plan = json.loads((ROOT/'config/dungeon_plan.json').read_text(encoding='utf-8'))
        self.base = json.loads((ROOT/'config/profiles/dungeon_unlimited.json').read_text(encoding='utf-8'))

    def test_legacy_defaults_unchanged(self):
        task = self.plan['tasks'][0]
        built = build_dungeon_profile(self.base, task, self.plan['tasks'])
        self.assertEqual(built['states']['OpeningBuff'], self.base['states']['OpeningBuff'])
        self.assertEqual(selected_buff(task)['label'], '经验 Buff')

    def test_damage_replaces_all_dependencies_and_only_selected_dungeon(self):
        task = dict(self.plan['tasks'][1], buff='damage')
        original = copy.deepcopy(self.base)
        built = build_dungeon_profile(self.base, task, self.plan['tasks'])
        validate(built, ROOT/'assets/image', True)
        self.assertNotIn('经验buff.png', json.dumps(built, ensure_ascii=False))
        for rule in built['states']['Progress']['rules']:
            if rule.get('next') == 'OpeningBuff' and 'success_match' in rule:
                self.assertEqual(rule['success_match']['template'], '伤害buff.png')
        select = built['states']['OpeningBuff']['rules'][0]
        self.assertEqual(select['template'], '伤害buff.png')
        self.assertEqual(select['offset'], [0,99])
        self.assertEqual(select['success_match']['template'], '确认buff.png')
        self.assertTrue(select['verify_change'])
        self.assertEqual(select['retry_max_clicks'], 4)
        self.assertEqual(self.base, original)
        other = build_dungeon_profile(self.base, self.plan['tasks'][0], self.plan['tasks'])
        self.assertEqual(other['states']['OpeningBuff']['rules'][0]['template'], '经验buff.png')

    def test_invalid_buff_rejected_even_if_task_disabled(self):
        for bad in ['unknown', '', None, [], True]:
            with self.subTest(bad=bad):
                plan = copy.deepcopy(self.plan)
                plan['tasks'][-1]['buff'] = bad
                with self.assertRaisesRegex(ValueError, 'buff 必须'):
                    validate_dungeon_plan(plan, ROOT/'config/profiles', ROOT/'assets/image')

    def test_mixed_plan_valid_with_real_assets(self):
        for i,t in enumerate(self.plan['tasks']): t['buff'] = 'experience' if i%2 == 0 else 'damage'
        validate_dungeon_plan(self.plan, ROOT/'config/profiles', ROOT/'assets/image', True)






    def test_damage_does_not_fallback_to_experience(self):
        data = build_dungeon_profile(self.base, dict(self.plan['tasks'][0],buff='damage'), self.plan['tasks'])
        data['initial_state'] = 'OpeningBuff'
        data.pop('watchdog')
        data['poll_seconds'] = .001
        data['states']['OpeningBuff']['timeout_seconds'] = .2
        backend = FakeBackend(['经验buff.png'])
        runner = Runner(backend, data, Mock(), False, clock=Clock())
        runner.run()
        self.assertEqual(backend.clicks, 0)
        self.assertEqual(runner.stats.state, 'OpeningBuff')
        self.assertIn('超时', runner.reason)

if __name__ == '__main__': unittest.main()
