"""Shared dungeon flow and boundary-only scheduling; no game input in this module."""
from __future__ import annotations

import copy
import json
import time
from pathlib import Path
from .cooldown import DeferredCooldown
from .diamonds import DiamondResult

BUFF_OPTIONS = {
    'experience': {'label': '经验 Buff', 'template': '经验buff.png', 'offset': [0, 99]},
    'damage': {'label': '伤害 Buff', 'template': '伤害buff.png', 'offset': [0, 99]},
}


def selected_buff(task):
    key = task.get('buff', 'experience')
    if not isinstance(key, str) or key not in BUFF_OPTIONS:
        raise ValueError(f"{task.get('display_name', '副本')} 的 buff 必须为 experience 或 damage")
    return BUFF_OPTIONS[key]


def entrance_click_offset(task):
    """Nameplate recognition is an anchor, not the clickable dungeon building.

    Bundled nameplate crops share the same 720-short-side layout: door interior is
    150 px above the label center. Custom crops must explicitly calibrate their offset.
    """
    from .config import number
    value = task.get('entrance_offset')
    if value is None:
        known = {name + '名称.png' for name in ('一本','二本','三本','四本','五本','六本')}
        if task.get('entrance_template') not in known:
            raise ValueError(f"{task.get('display_name', '副本')} 的自定义入口模板需要 entrance_offset=[dx,dy]")
        return [0, -150]
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError('entrance_offset 必须为 [dx,dy] 两个整数')
    for component in value:
        number(component, 'entrance_offset', -2000, 2000)
        if int(component) != component:
            raise ValueError('entrance_offset 必须为整数')
    return list(value)


def build_dungeon_profile(base, task, tasks):
    data = copy.deepcopy(base)
    data['max_rounds'] = 1
    data['profile'] = dict(id=task['id'], display_name=task['display_name'], kind='repeat')
    states = data['states']
    buff = selected_buff(task)
    # Replace every recognition dependency, including room-entry success and resume rules.
    # Keep confirmation guards and bounded retries identical to the proven experience flow.
    for state_name, state in states.items():
        for rule in state['rules']:
            if rule['template'] == '经验buff.png' and rule.get('action') == 'click':
                rule['offset'] = list(buff['offset'])
            rule['name'] = rule['name'].replace('经验 Buff', buff['label'])
            for obj in [rule, rule.get('requires_match', {}), rule.get('success_match', {})]:
                if obj.get('template') == '经验buff.png':
                    obj['template'] = buff['template']
    marker = task['entrance_template']
    # Nameplates exclude the changing countdown and work after horizontal scrolling.
    for state in states.values():
        state['rules'] = [r for r in state['rules'] if r['template'] != '一本高亮.png']
        for rule in state['rules']:
            if rule['template'] == '一本.png':
                rule['template'] = marker
                rule.pop('roi', None)
                if rule.get('action') == 'click':
                    rule['offset'] = entrance_click_offset(task)
                    rule['requires_match'] = dict(template='地下城列表通用标识.png',
                        threshold=.95, min_contrast_ratio=.65)
            for obj in [rule, rule.get('requires_match', {}), rule.get('success_match', {})]:
                if obj.get('template') == '地下城列表标识.png':
                    obj['template'] = '地下城列表通用标识.png'
                    obj.pop('roi', None)
                if obj.get('template') == '副本入口标题.png' and task['order'] != 1:
                    obj['template'] = '副本入口进度.png'
                    obj['roi'] = [550, 65, 100, 80]
    for rule in states['SelectDungeon']['rules']:
        if rule.get('template') == marker:
            rule['requires_match'] = dict(template='地下城列表通用标识.png', threshold=.95, min_contrast_ratio=.65)
    for rule in states['ReturnEntry']['rules']:
        rule['template'] = '地下城列表通用标识.png'
        rule.pop('roi', None)
    # Never accept another dungeon's already-open detail panel as the selected task.
    states['SelectDungeon']['rules'] = [r for r in states['SelectDungeon']['rules']
        if r['name'] != '副本详情已经打开']
    for other in tasks:
        if other['order'] == task['order'] or not other.get('entrance_template'):
            continue
        states['SelectDungeon']['rules'].append(dict(
            name=f"寻找{task['display_name']}：从{other['display_name']}拖动列表",
            template=other['entrance_template'], threshold=.95, min_contrast_ratio=.65,
            requires_match=dict(template='地下城列表通用标识.png', threshold=.95, min_contrast_ratio=.65),
            action='swipe', direction='left' if other['order'] < task['order'] else 'right',
            after_idle_seconds=3, confirm_frames=2, cooldown_seconds=1,
        ))
    states['Entry']['rules'].append(dict(
        name='主界面点击传送门返回副本界面', template='传送门.png',
        threshold=.95, min_contrast_ratio=.65, foreground=True,
        requires_match=dict(template='主界面锚点.png', threshold=.94, min_contrast_ratio=.65),
        action='click', next='Entry', verify_change=True,
        success_match=dict(template='地下城入口主体.png', threshold=.94, min_contrast_ratio=.65),
        retry_center_clicks=1, retry_max_clicks=2, retry_radius=0,
        confirm_frames=2, cooldown_seconds=1,
    ))
    detail = dict(template='副本详情标识.png', roi=[450, 320, 500, 100],
                  threshold=.95, min_contrast_ratio=.65)
    listing = dict(template='地下城列表通用标识.png', threshold=.95, min_contrast_ratio=.65)
    close_detail = dict(name='关闭遗留副本详情后重新选择', template='副本详情关闭.png',
        roi=[870, 100, 120, 110], threshold=.96, min_contrast_ratio=.65,
        requires_match=detail, foreground=True, action='click', next='Entry',
        verify_change=True, success_match=listing, retry_center_clicks=1,
        retry_max_clicks=2, retry_radius=0, confirm_frames=2, cooldown_seconds=1)
    # A new task must not launch a previous task's already-open detail modal.
    for state_name in ('Entry', 'SelectDungeon'):
        states[state_name]['rules'].append(copy.deepcopy(close_detail))
    for state_name in ('SelectDungeon', 'Explore'):
        for rule in states[state_name]['rules']:
            if rule['template'] == marker and rule.get('action') == 'click':
                # Opening details is successful even if its button is still cooling.
                rule['success_match'] = copy.deepcopy(detail)
    for rule in states['Explore']['rules']:
        if rule['template'] == '去探险.png':
            rule['requires_match'] = copy.deepcopy(detail)
            rule['min_contrast_ratio'] = .65
    cooling = copy.deepcopy(close_detail)
    cooling.update(name='游戏仍在冷却：关闭详情并延后调度', event='dungeon_deferred',
        requires_match=dict(template='副本冷却付费标识.png', roi=[460, 495, 100, 90],
                            threshold=.96, min_contrast_ratio=.65))
    cooling.pop('next')
    states['Explore']['rules'].insert(0, cooling)
    return data


def validate_plan(data, profiles_root, image_root, check_files=False):
    from .config import number, template_path, validate
    if data.get('version') != 2 or data.get('scheduler') != 'unified_dungeon':
        raise ValueError('合并地下城计划必须为 version=2 / unified_dungeon')
    total = number(data.get('max_total_runs'), 'max_total_runs', 1, 100000)
    if int(total) != total:
        raise ValueError('完成次数必须为整数')
    tasks = data.get('tasks')
    if not isinstance(tasks, list) or not tasks:
        raise ValueError('至少配置一本')
    ids, orders = set(), set()
    for task in tasks:
        if not isinstance(task, dict):
            raise ValueError('副本必须为对象')
        if not isinstance(task.get('id'), str) or not task['id'] or task['id'] in ids:
            raise ValueError('副本 id 不得为空或重复')
        ids.add(task['id'])
        selected_buff(task)
        rank = number(task.get('order'), 'order', 1, 10)
        if int(rank) != rank or rank in orders:
            raise ValueError('副本顺序必须为不重复的 1 到 10 整数')
        orders.add(rank)
        if not isinstance(task.get('display_name'), str) or not task['display_name']:
            raise ValueError('副本名称不能为空')
        if not isinstance(task.get('enabled'), bool):
            raise ValueError('enabled 必须为布尔值')
        if task.get('fallback') is not (rank == 1):
            raise ValueError('只有一本可以设为无冷却兜底')
        cd = number(task.get('cooldown_seconds'), 'cooldown_seconds', 0, 86400)
        if (rank == 1 and cd != 0) or (rank > 1 and cd <= 0):
            raise ValueError('一本冷却为 0，其他副本冷却必须大于 0')
        marker = task.get('entrance_template')
        if task['enabled'] and not marker:
            raise ValueError(f"{task['display_name']}缺少入口模板，暂不能启用")
        if marker:
            path = template_path(image_root, marker)
            if check_files and not path.is_file():
                raise ValueError(f'缺少副本入口模板：{marker}')
    if not any(t['enabled'] for t in tasks):
        raise ValueError('请至少选择一个要刷的副本')
    diamonds = data.get('diamonds', {})
    if not isinstance(diamonds, dict) or not isinstance(diamonds.get('enabled'), bool):
        raise ValueError('diamonds.enabled 必须为布尔值')
    if diamonds.get('interval_seconds') != 7200:
        raise ValueError('钻石周期固定为 7200 秒')
    if not isinstance(diamonds.get('run_on_start'), bool):
        raise ValueError('diamonds.run_on_start 必须为布尔值')
    base = json.loads((profiles_root / 'dungeon_unlimited.json').read_text(encoding='utf-8-sig'))
    for task in tasks:
        if task['enabled']:
            validate(build_dungeon_profile(base, task, tasks), image_root, check_files)
    if diamonds['enabled']:
        validate(json.loads((profiles_root / 'diamonds.json').read_text(encoding='utf-8-sig')),
                 image_root, check_files)
    return data


class ScheduleStore:
    """Atomic wall-clock checkpoints survive restarts. Corruption is not 'ready'."""
    def __init__(self, path):
        self.path = Path(path)

    def load(self):
        if not self.path.exists():
            return {}
        data = json.loads(self.path.read_text(encoding='utf-8'))
        if not isinstance(data, dict) or data.get('version') != 1:
            raise ValueError('地下城计时记录格式错误，请保留文件并检查')
        from .config import number
        if not isinstance(data.get('ready_at'), dict):
            raise ValueError('副本计时记录格式错误')
        for value in [*data['ready_at'].values(), data.get('diamond_due')]:
            number(value, '持久化时间', 0, 1e12)
        return data

    def save(self, data):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix('.tmp')
        try:
            temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
            temp.replace(self.path)
        finally:
            temp.unlink(missing_ok=True)


class UnifiedScheduler:
    """A diamond callback runs only outside a dungeon, including after the final round.

    Callback must return True only after actual collection or explicit human acknowledgement.
    Unknown cooldowns conservatively start at one full cooldown, never blindly re-enter.
    """
    def __init__(self, plan, run_dungeon, run_diamonds, stop, emit, store=None, clock=time.time,
                 wait=None, pause_event=None, scan_cooldowns=None):
        self.plan, self.run_dungeon, self.run_diamonds = plan, run_dungeon, run_diamonds
        self.stop, self.emit, self.store, self.clock = stop, emit, store, clock
        self.wait = wait or stop.wait
        self.pause_event = pause_event
        self.scan_cooldowns = scan_cooldowns
        self.tasks = sorted((t for t in plan['tasks'] if t['enabled']), key=lambda t: t['order'])
        if not self.tasks:
            raise ValueError('请至少选择一个要刷的副本')
        saved = store.load() if store else {}
        now = clock()
        self.ready_at = {t['id']: saved.get('ready_at', {}).get(t['id'], now + t['cooldown_seconds'])
                         for t in self.tasks}
        for task in self.tasks:
            if task['id'] == saved.get('in_flight') and not task['fallback']:
                self.ready_at[task['id']] = max(self.ready_at[task['id']], now + task['cooldown_seconds'])
        self.in_flight = None
        self.diamond_due = saved.get('diamond_due', now + (0 if plan['diamonds']['run_on_start'] else 7200))
        self.completed_runs = 0
        self.reason = ''
        self.succeeded = False
        self.cooldown_deferrals = {}

    def checkpoint(self):
        if self.store:
            self.store.save(dict(version=1, ready_at=self.ready_at, diamond_due=self.diamond_due,
                                 in_flight=self.in_flight))

    def choose(self):
        ready = [t for t in self.tasks if not t['fallback'] and self.ready_at[t['id']] <= self.clock()]
        # Oldest due first avoids starving long-cooldown instances behind a fast second dungeon.
        return min(ready, key=lambda t: (self.ready_at[t['id']], t['order'])) if ready else next((t for t in self.tasks if t['fallback']), None)

    def apply_list_readings(self, readings):
        """Ignore unselected ids and unknown/malformed observations, not their deadlines."""
        if not isinstance(readings, dict): return
        now=self.clock()
        for task in self.tasks:
            if task['fallback']: continue
            value=readings.get(task['id'])
            if value=='ready':
                self.ready_at[task['id']]=min(self.ready_at[task['id']],now)
                self.emit('log',f"{task['display_name']}：列表连续两帧确认门口可用")
            elif isinstance(value,DeferredCooldown) and value.valid_at(now):
                self.ready_at[task['id']]=max(now+1,value.ready_at)
                self.emit('log',f"{task['display_name']}：列表剩余 {value.text}，含 3 秒缓冲，约 {self.ready_at[task['id']]-now:.1f} 秒后可再尝试")
        self.checkpoint()

    def run(self):
        self.checkpoint()
        last_wait_notice = None
        next_scan = 0.
        while not self.stop.is_set():
            if self.pause_event is not None and self.pause_event.is_set():
                if self.wait(.2):
                    break
                continue
            if self.plan['diamonds']['enabled'] and self.clock() >= self.diamond_due:
                self.emit('log', '钻石任务已到期，当前无副本进行中，优先执行。')
                ok, reason = self.run_diamonds()
                if self.stop.is_set() or not ok:
                    self.reason = reason
                    break
                if isinstance(ok, DiamondResult):
                    if not ok.valid_at(self.clock()):
                        self.reason = '钻石冷却结果无效，停止而非伪报领取成功'
                        break
                    self.diamond_due = max(self.clock()+1, ok.cooldown.ready_at)
                    self.emit('log', ('钻石领取已验证' if ok.collected else '钻石尚在冷却，未领取')
                              + f'；下次检查按剩余 {ok.cooldown.text} 调度，继续地下城')
                elif ok is True:
                    self.diamond_due = self.clock() + 7200  # legacy explicit acknowledgement
                else:
                    self.reason = '未知钻石任务结果，停止'
                    break
                self.checkpoint()
                next_scan = 0.
            if self.completed_runs >= self.plan['max_total_runs']:
                self.succeeded = True
                self.reason = '地下城预定次数已完成'
                break
            if self.scan_cooldowns is not None and self.clock() >= next_scan:
                try:
                    readings = self.scan_cooldowns()
                except InterruptedError:
                    if self.stop.is_set(): break
                    continue
                if self.stop.is_set(): break
                self.apply_list_readings(readings)
                next_scan = self.clock() + 60
                # A scan may cross the diamond deadline or the user's pause request.
                if ((self.pause_event is not None and self.pause_event.is_set())
                        or (self.plan['diamonds']['enabled'] and self.clock() >= self.diamond_due)):
                    continue
            task = self.choose()
            if task is None:
                now = self.clock()
                next_task = min(self.tasks, key=lambda t: (self.ready_at[t['id']], t['order']))
                deadline = self.ready_at[next_task['id']]
                next_label = next_task['display_name']
                if self.plan['diamonds']['enabled'] and self.diamond_due <= deadline:
                    deadline, next_label = self.diamond_due, '钻石任务'
                if last_wait_notice is None or now - last_wait_notice >= 30 or now < last_wait_notice:
                    self.emit('schedule', dict(kind='dungeon', phase='waiting', task=next_label,
                        next_in=max(0, deadline-now), completed_runs=self.completed_runs))
                    last_wait_notice = now
                # Between bounded list scans, wake for stop/pause/diamonds/deadlines.
                if self.wait(min(1., max(.05, deadline-now))):
                    break
                continue
            last_wait_notice = None
            self.emit('schedule', dict(kind='dungeon', phase='running', task=task['display_name'],
                completed_runs=self.completed_runs, fallback=task['fallback']))
            # Checkpoint a conservative reservation before entering; a crash must not erase cooldown.
            if not task['fallback']:
                self.in_flight = task['id']
                self.ready_at[task['id']] = self.clock() + task['cooldown_seconds']
                self.checkpoint()
            ok, reason = self.run_dungeon(task)
            next_scan = 0.
            # None (OCR fallback) or DeferredCooldown (two-frame OCR deadline) requires
            # confirmed cooldown + confirmed return to the list.
            # It is neither a completed run nor an unclassified failure.
            if not self.stop.is_set() and (ok is None or isinstance(ok, DeferredCooldown)):
                count = self.cooldown_deferrals.get(task['id'], 0) + 1
                self.cooldown_deferrals[task['id']] = count
                self.in_flight = None
                now = self.clock()
                exact = isinstance(ok, DeferredCooldown) and ok.valid_at(now)
                if exact:
                    self.ready_at[task['id']] = max(now + 1, ok.ready_at)
                else:
                    self.ready_at[task['id']] = now + max(60, task['cooldown_seconds'])
                delay = self.ready_at[task['id']] - now
                self.checkpoint()
                if task['fallback'] or count >= 3:
                    self.reason = f"{task['display_name']} 冷却状态异常（连续 {count} 次），请检查冷却配置；未计完成、未花钻石"
                    self.emit('schedule', dict(kind='dungeon', phase='failed', task=task['display_name'], reason=self.reason))
                    break
                timing = (f'OCR 剩余 {ok.text}，扣除已耗时并含 3 秒缓冲'
                          if exact else 'OCR 不可用，按配置冷却保守等待')
                self.emit('log', f"{task['display_name']}：{timing}；约 {delay:.1f} 秒后可再尝试，不计完成，不点击钻石跳过。")
                continue
            if self.stop.is_set() or not ok:
                self.reason = reason
                self.emit('schedule', dict(kind='dungeon', phase='failed', task=task['display_name'], reason=reason))
                break
            self.completed_runs += 1
            self.cooldown_deferrals.pop(task['id'], None)
            self.in_flight = None
            self.ready_at[task['id']] = self.clock() + task['cooldown_seconds']
            self.checkpoint()
            self.emit('schedule', dict(kind='dungeon', phase='completed', task=task['display_name'],
                completed_runs=self.completed_runs, cooldown_until=self.ready_at[task['id']]))
        self.emit('log', self.reason or '地下城调度已停止')
