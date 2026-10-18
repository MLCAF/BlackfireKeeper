"""Godot 4.4.1 x64 float ABI; validate every container before using it.

Offsets derived from official source saved under research/, verified against
this build at runtime. Unsupported types fail closed, never become fake values.
"""
import struct
from .memory import MemoryReadError


class GodotReader:
    def __init__(self, memory):
        self.m = memory

    def string(self, pointer):
        if not pointer:
            return ''
        n = self.m.u64(pointer - 8)
        if not 1 <= n <= 4096:
            raise MemoryReadError('String 长度不合法')
        data = self.m.read(pointer, n * 4)
        if data[-4:] != b'\0' * 4:
            raise MemoryReadError('String 终止符不合法')
        try:
            return data[:-4].decode('utf-32-le')
        except UnicodeError as exc:
            raise MemoryReadError('String 编码不合法') from exc

    def key(self, raw):
        kind = struct.unpack_from('<I', raw)[0]
        p = struct.unpack_from('<Q', raw, 8)[0]
        if kind == 4:
            return self.string(p)
        if kind == 2:
            return struct.unpack_from('<q', raw, 8)[0]
        raise MemoryReadError(f'不支持的字典键类型: {kind}')

    def entries(self, head, limit=2048):
        entries, visited, previous = {}, set(), 0
        while head:
            if head in visited or len(visited) >= limit:
                raise MemoryReadError('字典链表循环或过长')
            visited.add(head)
            raw = self.m.read(head, 64)
            nxt, prev = struct.unpack_from('<QQ', raw)
            if prev != previous:
                raise MemoryReadError('字典链表不一致')
            key = self.key(raw[16:40])
            if key in entries:
                raise MemoryReadError('字典重复键')
            entries[key] = (head + 40, raw[40:64])
            previous, head = head, nxt
        return entries

    def head_from_node(self, node):
        seen = set()
        while node:
            if node in seen or len(seen) >= 2048:
                raise MemoryReadError('反向链表循环')
            seen.add(node)
            nxt, prev = struct.unpack('<QQ', self.m.read(node, 16))
            if not prev:
                return node
            if self.m.u64(prev) != node:
                raise MemoryReadError('反向链表无效')
            node = prev
        raise MemoryReadError('空节点')

    def dictionary_entries(self, pointer):
        if not pointer:
            raise MemoryReadError('空 Dictionary 指针')
        before = self.m.read(pointer + 40, 24)
        head, tail, capacity, count = struct.unpack('<QQII', before)
        if count > 2048 or capacity > 32:
            raise MemoryReadError('Dictionary 头部不合法')
        entries = self.entries(head)
        if len(entries) != count or bool(head) != bool(tail):
            raise MemoryReadError('Dictionary 数量不一致')
        if entries and next(reversed(entries.values()))[0] - 40 != tail:
            raise MemoryReadError('Dictionary 尾节点不匹配')
        if tail and self.m.u64(tail) != 0:
            raise MemoryReadError('Dictionary 尾节点无效')
        if self.m.read(pointer + 40, 24) != before:
            raise MemoryReadError('Dictionary 在采样中发生变化')
        return entries

    def variant(self, raw, depth=0):
        if depth > 12:
            raise MemoryReadError('数据嵌套过深')
        kind = struct.unpack_from('<I', raw)[0]
        p = struct.unpack_from('<Q', raw, 8)[0]
        if kind == 0:
            return None
        if kind == 1:
            return bool(raw[8])
        if kind == 2:
            return struct.unpack_from('<q', raw, 8)[0]
        if kind == 3:
            return struct.unpack_from('<d', raw, 8)[0]
        if kind == 4:
            return self.string(p)
        if kind == 27:
            return {k: self.variant(v[1], depth + 1) for k, v in self.dictionary_entries(p).items()}
        if kind == 28:
            buf = self.m.u64(p + 16)
            if not buf:
                return []
            count = self.m.u64(buf - 8)
            if count > 4096:
                raise MemoryReadError('Array 长度超限')
            if not count:
                return []
            values = self.m.read(buf, count * 24)
            result = [self.variant(values[i:i+24], depth + 1) for i in range(0, len(values), 24)]
            if self.m.u64(p + 16) != buf or self.m.u64(buf - 8) != count:
                raise MemoryReadError('Array 在采样中发生变化')
            return result
        raise MemoryReadError(f'不支持的 Variant 类型: {kind}')

    def discover_player_heads(self, stop=None):
        pattern = 'dungeon_progress'.encode('utf-32-le') + b'\0' * 4
        hits = self.m.find([pattern], stop=stop)[pattern]
        pointers = []
        for hit in hits:
            try:
                if self.string(hit) == 'dungeon_progress':
                    pointers.append(struct.pack('<Q', hit))
            except MemoryReadError:
                pass
        if not pointers:
            return []
        references = self.m.find(pointers, stop=stop)
        heads = set()
        for addresses in references.values():
            for address in addresses:
                if address % 8:
                    continue
                try:
                    raw = self.m.read(address - 8, 24)
                    if self.key(raw) != 'dungeon_progress':
                        continue
                    head = self.head_from_node(address - 24)
                    entries = self.entries(head)
                    if {'heroes', 'bag', 'dungeon_progress', 'level_battle'} <= entries.keys():
                        heads.add(head)
                except (MemoryReadError, ValueError, struct.error):
                    continue
        return sorted(heads)
