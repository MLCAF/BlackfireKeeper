"""Allowlisted player-state snapshots. Never log account/session dictionaries."""
import time
from .godot import GodotReader
from .memory import MemoryReadError
from .player_binding import check_stop, resolve_player_bindings

ANCHORS = {'heroes', 'bag', 'dungeon_progress', 'level_battle'}


class StateReader:
    def __init__(self, memory):
        self.memory = memory
        self.godot = GodotReader(memory)
        self.head = None
        self.binding = None
        self.binding_diagnostics = {}

    def _sample(self, head):
        entries = self.godot.entries(head)
        if not ANCHORS <= entries.keys():
            raise MemoryReadError('玩家字典锚点丢失')
        result = {}
        from .cooldowns import read_fields
        result['cooldown_fields'] = read_fields(self.godot, entries)
        for key in ('dungeon_progress', 'level_battle'):
            result[key] = self.godot.variant(entries[key][1])
            if result[key] is not None and not isinstance(result[key], dict):
                raise MemoryReadError(f'{key} 不是字典或空值')
        heroes = self.godot.variant(entries['heroes'][1])
        if not isinstance(heroes, dict) or not isinstance(heroes.get('heroes'), list):
            raise MemoryReadError('角色数据结构不一致')
        result['heroes'] = {
            'count': len(heroes['heroes']),
            'parties': heroes.get('parties', []),
            'list': [{k: h[k] for k in ('id', 'level', 'exp', 'star') if k in h}
                     for h in heroes['heroes'] if isinstance(h, dict)],
        }
        bag_raw = entries['bag'][1]
        import struct
        if struct.unpack_from('<I', bag_raw)[0] != 27:
            raise MemoryReadError('背包字段类型错误')
        bag = self.godot.dictionary_entries(struct.unpack_from('<Q', bag_raw, 8)[0])
        totals = {}
        if 'items' in bag:
            items = self.godot.variant(bag['items'][1])
            if not isinstance(items, list):
                raise MemoryReadError('背包物品列表类型错误')
            for item in items:
                if isinstance(item, dict) and isinstance(item.get('id'), str) and isinstance(item.get('count'), (int, float)):
                    totals[item['id']] = totals.get(item['id'], 0) + item['count']
        result['bag_item_totals'] = totals
        from .food import read_fields as read_food_fields
        result['food_fields'] = read_food_fields(self.godot, entries, bag, totals)
        # This is the last cached reward list, not evidence of a new award.
        if 'items' in entries:
            items = self.godot.variant(entries['items'][1])
            if isinstance(items, list):
                result['cached_reward_items'] = [
                    {k: item[k] for k in ('id', 'count') if k in item}
                    for item in items if isinstance(item, dict)]
        return result

    def bind(self, stop=None):
        # A failed rebind must never leave the old head usable.
        self.head = None
        self.binding = None
        self.binding_diagnostics = {'stage': 'discover'}
        valid = []
        heads = self.godot.discover_player_heads(stop)
        self.binding_diagnostics.update(discovered_heads=len(heads),
                                        sample_failures=0, unstable_samples=0)
        for head in heads:
            check_stop(stop)
            for _ in range(3):
                check_stop(stop)
                try:
                    first = self._sample(head)
                    if first == self._sample(head):
                        valid.append(head)
                        break
                    self.binding_diagnostics['unstable_samples'] += 1
                except MemoryReadError:
                    self.binding_diagnostics['sample_failures'] += 1
                    # Do not put player values or raw dictionary contents in diagnostics.
        self.binding_diagnostics['stage'] = 'ownership'
        bindings = resolve_player_bindings(self.godot, valid, self.binding_diagnostics, stop)
        if len(bindings) != 1:
            raise MemoryReadError(f'结构候选 {len(valid)} 个，已核验 PlayerData 归属 {len(bindings)} 个；'
                                  '需要唯一归属，未选择任意候选；请在已登录界面重连并保留诊断')
        self.binding = bindings[0]
        self.head = self.binding.head
        try:
            check_stop(stop)
            result = self.snapshot()
            self.binding_diagnostics['stage'] = 'bound'
            return result
        except Exception:
            self.head = self.binding = None
            raise

    def snapshot(self):
        if not self.head or not self.binding:
            raise MemoryReadError('尚未绑定玩家字典')
        for _ in range(3):
            try:
                self.binding.validate(self.godot)
                first = self._sample(self.head)
                if first == self._sample(self.head):
                    self.binding.validate(self.godot)
                    return {'status': 'ok', 'source': 'process_memory',
                            'binding': 'verified_playerdata_script_member',
                            'pid': self.memory.pid, 'process_creation': self.memory.creation,
                            'captured_monotonic': time.monotonic(), 'data': first}
            except MemoryReadError:
                continue
        raise MemoryReadError('状态连续读取不一致或对象失效，请重新连接')


def bind_when_ready(reader, stop, emit, seconds=30, clock=time.monotonic):
    """Wait for login data, still requiring a unique verified PlayerData owner."""
    deadline = clock() + seconds
    attempts = 0
    class BoundedStop:
        def is_set(self):
            return stop.is_set() or clock() >= deadline
    last_error = '玩家数据尚未就绪'
    while not stop.is_set() and clock() < deadline:
        attempts += 1
        try:
            result = reader.bind(BoundedStop())
            if BoundedStop().is_set():
                break
            reader.binding_diagnostics['startup_attempts'] = attempts
            return result
        except InterruptedError:
            if stop.is_set():
                raise
            break
        except MemoryReadError as exc:
            last_error = str(exc)
            # Ambiguity and malformed scans must not be hidden as login delay.
            if reader.binding_diagnostics.get('structural_candidates') != 0:
                raise
            if attempts == 1:
                emit('log', '玩家数据尚未就绪，最多等待 30 秒；请保持游戏已登录，不执行点击')
            stop.wait(.5)
    reader.binding_diagnostics['startup_attempts'] = attempts
    if stop.is_set():
        raise InterruptedError('玩家绑定已取消')
    raise MemoryReadError('等待玩家数据超时：' + last_error)


def require_fresh(snapshot, pid, now=None):
    now = time.monotonic() if now is None else now
    if not snapshot or snapshot.get('status') != 'ok' or snapshot.get('pid') != pid:
        raise MemoryReadError('没有与游戏窗口匹配的有效内存状态')
    age = now - snapshot['captured_monotonic']
    if not 0 <= age <= 3:
        raise MemoryReadError('内存状态超过 3 秒未更新，停止操作')


class ProgressLedger:
    """Report observed transitions, never equate reward-panel clicks with credit."""
    def __init__(self):
        self.previous = None
        self.active_id = None
        self.finished_transitions = 0

    def observe(self, snapshot):
        d = snapshot['data'].get('dungeon_progress') or {}
        current = (d.get('dungeon_id'), d.get('result'))
        event = None
        if current[1] == 'in progress':
            self.active_id = current[0]
        if (current[1] == 'finished' and self.previous == (current[0], 'in progress')
                and current[0] == self.active_id):
            self.finished_transitions += 1
            event = {'event': 'dungeon_finished_transition', 'dungeon_id': current[0],
                     'count': self.finished_transitions, 'inventory_credit_verified': False}
            self.active_id = None
        self.previous = current
        return event
