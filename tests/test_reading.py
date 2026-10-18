import struct
import unittest
from unittest.mock import Mock, patch
from bk2.godot import GodotReader
from bk2.memory import MemoryReadError
from bk2.state import ProgressLedger, StateReader, require_fresh


class Memory:
    def __init__(self):
        self.data = bytearray(0x20000)
    def read(self, address, size):
        if address < 0 or address + size > len(self.data):
            raise MemoryReadError('unmapped')
        return bytes(self.data[address:address+size])
    def put(self, address, data):
        self.data[address:address+len(data)] = data
    def u64(self, address):
        return struct.unpack('<Q', self.read(address, 8))[0]
    def string(self, address, text):
        raw = text.encode('utf-32-le') + bytes(4)
        self.put(address-8, struct.pack('<Q', len(raw)//4))
        self.put(address, raw)
        return struct.pack('<IIQQ', 4, 0, address, 0)


def var(kind, value):
    return struct.pack('<IIQQ', kind, 0, value, 0)


class ParserTests(unittest.TestCase):
    def setUp(self):
        self.m = Memory(); self.g = GodotReader(self.m)

    def make_dict(self):
        key = self.m.string(0x2000, 'count')
        self.m.put(0x3000, bytes(16) + key + var(2, 17))
        self.m.put(0x1000+40, struct.pack('<QQII', 0x3000, 0x3000, 2, 1))
        return var(27, 0x1000)

    def test_valid_dictionary_and_int(self):
        self.assertEqual(self.g.variant(self.make_dict()), {'count': 17})

    def test_array_and_float_from_json(self):
        self.m.put(0x1000+16, struct.pack('<Q', 0x4000))
        self.m.put(0x4000-8, struct.pack('<Q', 2))
        self.m.put(0x4000, var(2, 7) + struct.pack('<IIdQ', 3, 0, 5.0, 0))
        self.assertEqual(self.g.variant(var(28, 0x1000)), [7, 5.0])

    def test_nonterminated_string_rejected(self):
        raw = self.m.string(0x2000, 'hi')
        self.m.put(0x2008, b'x\0\0\0')
        with self.assertRaises(MemoryReadError): self.g.variant(raw)

    def test_huge_string_rejected(self):
        self.m.put(0x2000-8, struct.pack('<Q', 100000000))
        with self.assertRaises(MemoryReadError): self.g.string(0x2000)

    def test_cycle_rejected(self):
        raw = self.make_dict()
        self.m.put(0x3000, struct.pack('<Q', 0x3000))
        with self.assertRaises(MemoryReadError): self.g.variant(raw)

    def test_wrong_count_rejected(self):
        raw = self.make_dict()
        self.m.put(0x1000+60, struct.pack('<I', 2))
        with self.assertRaises(MemoryReadError): self.g.variant(raw)

    def test_wrong_tail_rejected(self):
        raw = self.make_dict()
        self.m.put(0x1000+48, struct.pack('<Q', 0x8000))
        with self.assertRaises(MemoryReadError): self.g.variant(raw)

    def test_unsupported_object_rejected(self):
        with self.assertRaises(MemoryReadError): self.g.variant(var(24, 12345))

    def test_negative_integer_preserved(self):
        self.assertEqual(self.g.variant(struct.pack('<IIqQ',2,0,-5,0)), -5)

    def test_null_dictionary_rejected(self):
        with self.assertRaises(MemoryReadError): self.g.variant(var(27,0))


class InterlockTests(unittest.TestCase):
    def sample(self, status='in progress', name='slime_cave'):
        return {'data': {'dungeon_progress': {'dungeon_id': name, 'result': status}}}

    def test_initial_finished_is_not_new_completion(self):
        ledger = ProgressLedger()
        self.assertIsNone(ledger.observe(self.sample('finished')))
        self.assertEqual(ledger.finished_transitions, 0)

    def test_completion_counted_once(self):
        ledger = ProgressLedger()
        ledger.observe(self.sample()); event = ledger.observe(self.sample('finished'))
        self.assertFalse(event['inventory_credit_verified'])
        self.assertIsNone(ledger.observe(self.sample('finished')))
        self.assertEqual(ledger.finished_transitions, 1)

    def test_other_dungeon_not_counted(self):
        ledger = ProgressLedger(); ledger.observe(self.sample())
        self.assertIsNone(ledger.observe(self.sample('finished', 'other')))

    def test_failure_not_counted(self):
        ledger = ProgressLedger(); ledger.observe(self.sample())
        ledger.observe(self.sample('defeat'))
        self.assertIsNone(ledger.observe(self.sample('finished')))

    def test_stale_or_wrong_process_blocks(self):
        s = {'status': 'ok', 'pid': 10, 'captured_monotonic': 100}
        require_fresh(s,10,101)
        for pid,now in ((11,101),(10,104),(10,99)):
            with self.assertRaises(MemoryReadError): require_fresh(s,pid,now)
        with self.assertRaises(MemoryReadError): require_fresh({'status':'unknown'},10,101)

    def test_ambiguous_candidates_never_selected(self):
        reader=StateReader(None)
        class Fake:
            def discover_player_heads(self,stop): return [1,2]
        reader.godot=Fake(); reader._sample=lambda head: {'valid':True}
        with patch('bk2.state.resolve_player_bindings', return_value=[Mock(), Mock()]):
            with self.assertRaises(MemoryReadError): reader.bind()
        self.assertIsNone(reader.head)

    def test_torn_snapshots_fail_closed(self):
        reader=StateReader(None); reader.head=1; reader.binding=Mock()
        values=iter(range(6)); reader._sample=lambda head: next(values)
        with self.assertRaises(MemoryReadError): reader.snapshot()


if __name__ == '__main__':
    unittest.main()
