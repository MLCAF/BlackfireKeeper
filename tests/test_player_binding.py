"""Synthetic process-memory fixtures: no game, credentials or private captures."""
import struct
import threading
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from bk2.godot import GodotReader
from bk2.memory import MemoryReadError
from bk2.player_binding import resolve_player_bindings, PLAYER_SCRIPT
from bk2.state import StateReader
from tests.test_reading import Memory, var


class ScannableMemory(Memory):
    pid, creation = 42, 43

    def find(self, patterns, stop=None):
        result = {}
        for pattern in patterns:
            hits, start = [], 0
            while True:
                pos = self.data.find(pattern, start)
                if pos < 0:
                    break
                hits.append(pos)
                start = pos + 1
            result[pattern] = hits
        return result


class BindingTests(unittest.TestCase):
    def setUp(self):
        self.m = ScannableMemory()
        self.g = GodotReader(self.m)
        self.make_dictionary(0x1000, 0x3000)
        self.make_dictionary(0x1200, 0x3400)
        self.make_owner(0x1000)
        self.reader = StateReader(self.m)
        self.reader._sample = Mock(return_value={'safe': True})

    def q(self, address, value):
        self.m.put(address, struct.pack('<Q', value))

    def make_dictionary(self, pointer, head):
        self.q(pointer, 1)
        for index, key in enumerate(('bag', 'heroes', 'dungeon_progress', 'level_battle')):
            node = head + index * 64
            self.m.put(node, struct.pack('<QQ', node + 64 if index < 3 else 0,
                node - 64 if index else 0) + self.m.string(0x4000 + index * 128, key) + var(0, 0))
        self.m.put(pointer + 40, struct.pack('<QQII', head, head + 192, 2, 4))

    def make_owner(self, pointer, shift=0):
        buffer, instance, owner, script = (p + shift for p in (0x8000, 0x9000, 0x10000, 0x11000))
        self.q(buffer - 8, 3)
        self.m.put(buffer, struct.pack('<IIQQ', 24, 0, 123, 0x7000) + var(27, pointer) + var(28, 0x7100))
        self.m.put(instance + 8, struct.pack('<QQQ', 777, owner, script))
        self.q(instance + 40, buffer)
        self.q(owner + 88, 777)
        self.q(owner + 104, instance)
        self.m.string(0x18000, PLAYER_SCRIPT)
        self.q(script + 304, 0x18000)
        self.q(script + 1048, 0x18000)

    def resolve(self):
        self.evidence = {}
        return resolve_player_bindings(self.g, [0x3000, 0x3400], self.evidence)

    def test_two_identical_structures_select_only_verified_owner(self):
        snapshot = self.reader.bind()
        self.assertEqual(self.reader.head, 0x3000)
        self.assertEqual(snapshot['binding'], 'verified_playerdata_script_member')
        self.assertEqual(self.reader.binding_diagnostics['structural_candidates'], 2)
        self.assertEqual(self.reader.binding_diagnostics['verified_bindings'], 1)

    def test_address_order_does_not_select_candidate(self):
        self.q(0x8020, 0x1200)
        self.reader.bind()
        self.assertEqual(self.reader.head, 0x3400)

    def test_two_real_owners_remain_ambiguous(self):
        self.make_owner(0x1200, 0x2000)
        with self.assertRaisesRegex(MemoryReadError, '归属 2 个'):
            self.reader.bind()
        self.assertIsNone(self.reader.head)

    def test_unowned_unique_candidate_never_falls_back(self):
        self.q(0x10068, 0)
        with patch.object(self.reader.godot, 'discover_player_heads', return_value=[0x3000]):
            with self.assertRaisesRegex(MemoryReadError, '归属 0 个'):
                self.reader.bind()

    def test_same_shape_wrong_script_rejected(self):
        self.m.string(0x18000, 'res://datas/other.gd')
        self.assertEqual(self.resolve(), [])

    def test_both_script_paths_required(self):
        self.m.string(0x19000, 'res://other.gd')
        self.q(0x11000 + 1048, 0x19000)
        self.assertEqual(self.resolve(), [])

    def test_owner_id_and_backreference_both_required(self):
        for offset in (88, 104):
            with self.subTest(offset=offset):
                old = self.m.read(0x10000 + offset, 8)
                self.q(0x10000 + offset, 0)
                self.assertEqual(self.resolve(), [])
                self.m.put(0x10000 + offset, old)

    def test_bad_member_count_or_type_rejected(self):
        self.q(0x8000 - 8, 4)
        self.assertEqual(self.resolve(), [])
        self.q(0x8000 - 8, 3)
        self.q(0x8000, 27)
        self.assertEqual(self.resolve(), [])

    def test_dead_dictionary_header_rejected(self):
        self.q(0x1000, 0)
        self.assertEqual(self.resolve(), [])

    def test_invalid_dictionary_tail_rejected(self):
        self.q(0x1000 + 48, 0x3400)
        self.assertEqual(self.resolve(), [])

    def test_detached_owner_invalidates_snapshot(self):
        self.reader.bind()
        self.q(0x10000 + 104, 0)
        with self.assertRaises(MemoryReadError):
            self.reader.snapshot()

    def test_replaced_data_requires_rebind(self):
        self.reader.bind()
        self.q(0x8020, 0x1200)
        with self.assertRaises(MemoryReadError):
            self.reader.snapshot()
        self.reader.bind()
        self.assertEqual(self.reader.head, 0x3400)

    def test_failed_rebind_discards_previous_binding(self):
        self.reader.bind()
        self.q(0x10068, 0)
        with self.assertRaises(MemoryReadError):
            self.reader.bind()
        self.assertIsNone(self.reader.binding)
        with self.assertRaises(MemoryReadError):
            self.reader.snapshot()

    def test_owner_changes_during_sample_never_publish(self):
        self.reader.bind()
        def sample(head):
            self.q(0x10068, 0)
            return {'safe': True}
        self.reader._sample = sample
        with self.assertRaises(MemoryReadError):
            self.reader.snapshot()

    def test_cancel_before_binding_does_not_scan(self):
        stop = threading.Event(); stop.set()
        with patch.object(self.m, 'find') as find:
            with self.assertRaises(InterruptedError):
                resolve_player_bindings(self.g, [0x3000], {}, stop)
            find.assert_not_called()

    def test_incomplete_scan_is_not_accepted(self):
        with patch.object(self.m, 'find', side_effect=MemoryReadError('扫描超时')):
            with self.assertRaisesRegex(MemoryReadError, '扫描超时'):
                self.reader.bind()

    def test_evidence_contains_counts_without_values_or_keys(self):
        self.resolve()
        self.assertEqual(self.evidence, dict(strategy='playerdata_script_member',
            structural_candidates=2, owner_headers=2, member_arrays=1, verified_bindings=1))

    def test_binding_failure_is_saved_in_session_diagnostic(self):
        from bk2.runtime import Session
        evidence = dict(stage='ownership', structural_candidates=2, verified_bindings=0)
        reader = SimpleNamespace(binding_diagnostics=evidence,
            bind=Mock(side_effect=MemoryReadError('归属不唯一')))
        with tempfile.TemporaryDirectory() as d, patch('bk2.runtime.ROOT', Path(d)), \
                patch('bk2.runtime.verify_build'), patch('bk2.runtime.find_game_pid', return_value=42), \
                patch('bk2.runtime.ProcessReader'), patch('bk2.runtime.StateReader', return_value=reader):
            session = Session()
            session._observe()
            report = json.loads(session.last_diagnostic.read_text(encoding='utf-8'))
            self.assertEqual(report['context']['player_binding'], evidence)
            self.assertEqual(report['stage'], 'memory_error')
            self.assertEqual(session.snapshot['status'], 'unknown')
            self.assertTrue(session.operation_stop.is_set())


if __name__ == '__main__':
    unittest.main()
