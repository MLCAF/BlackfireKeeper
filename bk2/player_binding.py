"""Read-only PlayerData ownership proof for the hash-pinned Godot 4.4.1 build.

GDScriptInstance (release x64): owner_id +8, owner +16, script +24,
members CowData +40. Object: instance_id +88, script_instance +104.
Resource.path_cache +304 and GDScript.path +1048 must both identify the
PlayerData script. Its three members are meta_datas, data, item_temp_tip_keys.
Offsets are verified against engine sources and the supported executable;
there is deliberately no fallback to the first structurally valid dictionary.
"""
from dataclasses import dataclass
import struct
from .memory import MemoryReadError

PLAYER_SCRIPT = 'res://datas/player_data.gd'


def check_stop(stop):
    if stop is not None and stop.is_set():
        raise InterruptedError('玩家绑定已取消')


def member_dictionary(godot, buffer):
    m = godot.m
    if buffer % 8 or m.u64(buffer - 8) != 3:
        raise MemoryReadError('PlayerData 成员数组长度不符')
    raw = m.read(buffer, 72)
    if tuple(struct.unpack_from('<I', raw, n)[0] for n in (0, 24, 48)) != (24, 27, 28):
        raise MemoryReadError('PlayerData 成员类型不符')
    if not m.u64(buffer + 16) or not m.u64(buffer + 56):
        raise MemoryReadError('PlayerData 成员引用为空')
    return struct.unpack_from('<Q', raw, 32)[0]


@dataclass(frozen=True)
class PlayerBinding:
    instance: int
    owner: int
    owner_id: int
    script: int
    buffer: int
    dictionary: int
    head: int

    def validate(self, godot):
        m = godot.m
        if (not self.owner_id or m.u64(self.instance + 8) != self.owner_id
                or m.u64(self.instance + 16) != self.owner
                or m.u64(self.instance + 24) != self.script
                or m.u64(self.instance + 40) != self.buffer
                or m.u64(self.owner + 88) != self.owner_id
                or m.u64(self.owner + 104) != self.instance):
            raise MemoryReadError('PlayerData 节点归属已变化')
        if any(godot.string(m.u64(self.script + offset)) != PLAYER_SCRIPT
               for offset in (304, 1048)):
            raise MemoryReadError('PlayerData 脚本路径不匹配')
        if member_dictionary(godot, self.buffer) != self.dictionary:
            raise MemoryReadError('PlayerData.data 已更换，必须重新绑定')
        if (not 0 < struct.unpack('<I', m.read(self.dictionary, 4))[0] < 0x80000000
                or m.u64(self.dictionary + 8) != 0
                or m.u64(self.dictionary + 40) != self.head):
            raise MemoryReadError('PlayerData 字典对象已失效')
        # Validate the owning header, not only leftover linked nodes.
        godot.dictionary_entries(self.dictionary)
        return self.head


def resolve_player_bindings(godot, heads, evidence, stop=None):
    """Prove ownership in three bounded scans. Evidence contains no player values."""
    m = godot.m
    heads = sorted(set(heads))
    evidence.update(strategy='playerdata_script_member', structural_candidates=len(heads),
                    owner_headers=0, member_arrays=0, verified_bindings=0)
    check_stop(stop)
    if not heads:
        return []
    if len(heads) > 64:
        raise MemoryReadError('玩家字典候选过多，拒绝截断后选择')
    patterns = {struct.pack('<Q', h): h for h in heads}
    owners = {}
    for pattern, addresses in m.find(patterns, stop=stop).items():
        for address in addresses:
            check_stop(stop)
            p = address - 40
            if p % 8:
                continue
            try:
                if not 0 < struct.unpack('<I', m.read(p, 4))[0] < 0x80000000 or m.u64(p + 8):
                    continue
                entries = godot.dictionary_entries(p)
                if {'heroes', 'bag', 'dungeon_progress', 'level_battle'} <= entries.keys():
                    owners[p] = patterns[pattern]
            except (MemoryReadError, ValueError, struct.error):
                continue
    evidence['owner_headers'] = len(owners)
    if not owners:
        return []
    patterns = {struct.pack('<Q', p): p for p in owners}
    buffers = {}
    for pattern, addresses in m.find(patterns, stop=stop).items():
        for address in addresses:
            check_stop(stop)
            buffer = address - 32
            try:
                if member_dictionary(godot, buffer) == patterns[pattern]:
                    buffers[buffer] = patterns[pattern]
            except (MemoryReadError, ValueError, struct.error):
                continue
    evidence['member_arrays'] = len(buffers)
    if not buffers:
        return []
    patterns = {struct.pack('<Q', b): b for b in buffers}
    verified = set()
    for pattern, addresses in m.find(patterns, stop=stop).items():
        for address in addresses:
            check_stop(stop)
            instance, buffer = address - 40, patterns[pattern]
            if instance % 8:
                continue
            try:
                owner_id, owner, script = struct.unpack('<QQQ', m.read(instance + 8, 24))
                binding = PlayerBinding(instance, owner, owner_id, script,
                                        buffer, buffers[buffer], owners[buffers[buffer]])
                binding.validate(godot)
                verified.add(binding)
            except (MemoryReadError, ValueError, struct.error):
                continue
    check_stop(stop)
    evidence['verified_bindings'] = len(verified)
    return sorted(verified, key=lambda b: b.instance)
