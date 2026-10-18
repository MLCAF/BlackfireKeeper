"""Read only the two timestamp paths proved by this build's GDScript.

UI code divides milliseconds by 1000 and uses the local Unix clock. These
values describe the client's cache, not an independent server clock query.
"""
import math
import struct
import time
from .memory import MemoryReadError

# Ordered string Array in the packaged world scene, matching world.gdc's loop.
DUNGEON_IDS = ('slime_cave', 'shadowy_abyss', 'forest_maze', 'mysterious_ruins',
               'dark_prison', 'frozen_caverns', 'orc_bunker', 'dragon_lair',
               'ancient_tomb', 'abyss_hell')


def read_fields(godot, root):
    """Traverse dictionaries without deserializing store purchases or account data."""
    def dictionary(entries, key):
        if key not in entries:
            return {}
        raw = entries[key][1]
        if struct.unpack_from('<I', raw)[0] != 27:
            raise MemoryReadError(f'冷却路径 {key} 不是 Dictionary')
        return godot.dictionary_entries(struct.unpack_from('<Q', raw, 8)[0])

    def scalar(entries, key):
        if key not in entries:
            return None
        raw = entries[key][1]
        if struct.unpack_from('<I', raw)[0] not in (1, 2, 3):
            return None
        return godot.variant(raw)

    result = {'dungeons': {}, 'free_gem_time': None}
    try:
        dungeons = dictionary(dictionary(root, 'dungeons'), 'dungeons')
        for key in DUNGEON_IDS:
            if key in dungeons:
                item = dictionary(dungeons, key)
                result['dungeons'][key] = {
                    'refresh_time': scalar(item, 'refresh_time'),
                    'has_unlocked': scalar(item, 'has_unlocked')}
        result['free_gem_time'] = scalar(dictionary(root, 'store'), 'free_gem_time')
    except MemoryReadError:
        # A failed traversal must participate in StateReader's retry, never pass
        # a partial dictionary off as a complete observation.
        raise
    return result


def deadline(raw, now, max_future):
    if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not math.isfinite(raw):
        return {'status': 'unknown', 'reason': '缺失或非有限数值'}
    # Zero is an explicit no-cooldown sentinel; missing values never become zero.
    if raw != 0 and not 946684800000 <= raw <= 4102444800000:
        return {'status': 'unknown', 'reason': '不是合理的 Unix 毫秒时间戳'}
    end = raw / 1000.
    if end - now > max_future:
        return {'status': 'unknown', 'reason': '超出冷却范围或系统时间异常'}
    return {'status': 'ready' if now > end else 'cooling', 'deadline_unix': end,
            'remaining_seconds': max(0., end - now), 'source': 'process_memory'}


def summarize(fields, now=None):
    now = time.time() if now is None else now
    fields = fields or {}
    items = fields.get('dungeons', {})
    result = {'clock': 'local_unix_like_game', 'dungeons': {},
              'diamonds': deadline(fields.get('free_gem_time'), now, 7205)}
    for order, key in enumerate(DUNGEON_IDS, 1):
        item = items.get(key, {})
        value = deadline(item.get('refresh_time'), now, 86400)
        value.update(game_id=key, order=order)
        if item.get('has_unlocked') is not True:
            value.update(status='unknown', reason='未确认副本已解锁')
        result['dungeons'][f'dungeon_{order}'] = value
    return result
