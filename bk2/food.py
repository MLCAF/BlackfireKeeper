"""Allowlisted bread stamina and bakery clock, derived from this build's scripts."""
import math
import struct
import time
from .memory import MemoryReadError

CAPACITY = 320
BATCH_SIZE = 160
SECONDS_PER_BREAD = 80


class FoodTimerStore:
    def __init__(self, path):
        self.path = path

    def load(self, now):
        import json
        if not self.path.exists():
            return 0
        record = json.loads(self.path.read_text(encoding='utf-8'))
        if not isinstance(record, dict) or record.get('version') != 1:
            raise ValueError('体力领取计时记录格式错误')
        value = record.get('next_check')
        if (isinstance(value, bool) or not isinstance(value, (int,float))
                or not math.isfinite(value) or not 0 <= value <= now+1803):
            raise ValueError('体力领取计时记录无效或系统时钟异常')
        return value

    def save(self, next_check):
        import json
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix('.tmp')
        temp.write_text(json.dumps(dict(version=1,next_check=next_check)),encoding='utf-8')
        temp.replace(self.path)


def read_fields(godot, root, bag, totals):
    def dictionary(entries, key):
        if key not in entries:
            return None
        raw = entries[key][1]
        if struct.unpack_from('<I', raw)[0] != 27:
            raise MemoryReadError(f'体力路径 {key} 不是 Dictionary')
        return godot.dictionary_entries(struct.unpack_from('<Q', raw, 8)[0])

    def number(entries, key):
        if entries is None or key not in entries:
            return None
        raw = entries[key][1]
        return godot.variant(raw) if struct.unpack_from('<I', raw)[0] in (2, 3) else None

    result = {}
    for name in ('bread', 'food'):
        value = None
        complete = True
        for category in ('currencies', 'foods'):
            entries = dictionary(bag, category)
            complete = complete and entries is not None
            if entries is not None and name in entries:
                value = number(entries, name)
                break
        else:
            value = totals.get(name, 0) if complete else None
        result[name] = value
    bakery = dictionary(root, 'bakery')
    result.update(start_time=number(bakery, 'start_time'), finish_time=number(bakery, 'finish_time'))
    return result


def summarize(fields, now=None):
    now = time.time() if now is None else now
    fields = fields or {}
    result = dict(status='unknown', count=None, capacity=CAPACITY, produced=None,
                  batch_size=BATCH_SIZE, claimable=0, remaining_seconds=None,
                  reason='没有有效体力或制作时间', source='process_memory')
    bread = fields.get('bread')
    if (isinstance(bread, bool) or not isinstance(bread, (int, float))
            or not math.isfinite(bread) or int(bread) != bread or not 0 <= bread <= CAPACITY):
        return result
    result['count'] = int(bread)
    start = fields.get('start_time')
    for stamp in (start,):
        if (isinstance(stamp, bool) or not isinstance(stamp, (int, float))
                or not math.isfinite(stamp) or not 946684800000 <= stamp <= 4102444800000):
            return result
    start = start/1000
    # BakeryPanel derives completion from start + production time. The live
    # finish_time field is stale in this build; never use it as the UI deadline.
    finish = start+BATCH_SIZE*SECONDS_PER_BREAD
    if start > now+3:
        result['reason'] = '制作开始时间异常'
        return result
    produced = min(BATCH_SIZE, max(0, math.floor((now-start)/SECONDS_PER_BREAD)))
    room = CAPACITY-int(bread)
    result.update(status='full' if room == 0 else ('ready' if produced > 0 else 'making'),
                  produced=produced, claimable=min(produced, room),
                  remaining_seconds=max(0., finish-now), start_time=start, finish_time=finish,
                  reason='已满，暂不领取' if room == 0 else '按实际制作进度领取')
    return result
