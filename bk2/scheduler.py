"""Memory timestamp adapter over the migrated, boundary-only scheduler."""
from dataclasses import dataclass
from vendor_core.unified import UnifiedScheduler
from vendor_core.cooldown import DeferredCooldown
from vendor_core.diamonds import DiamondResult


@dataclass(frozen=True)
class MemoryCooldown:
    deadline: float


@dataclass(frozen=True)
class DungeonAbandoned:
    """Exit verified by the battle UI and the memory boundary; not a win."""


class MemoryScheduler(UnifiedScheduler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.skipped_dungeons = set()

    def available_tasks(self):
        return [t for t in self.tasks if t['id'] not in self.skipped_dungeons]

    def choose(self):
        available = self.available_tasks()
        ready = [t for t in available if not t['fallback'] and self.ready_at[t['id']] <= self.clock()]
        return (min(ready, key=lambda t: (self.ready_at[t['id']], t['order'])) if ready
                else next((t for t in available if t['fallback']), None))

    def apply_list_readings(self, readings):
        if not isinstance(readings, dict):
            return
        # Keep OCR handling unchanged and give memory a distinct provenance.
        super().apply_list_readings({k: v for k, v in readings.items()
                                     if not isinstance(v, MemoryCooldown)})
        now = self.clock()
        for task in self.tasks:
            reading = readings.get(task['id'])
            if isinstance(reading, MemoryCooldown) and not task['fallback']:
                self.ready_at[task['id']] = max(now, reading.deadline + 3)
                self.emit('log', f"{task['display_name']}：内存冷却剩余 {max(0, reading.deadline-now):.1f} 秒；到期加 3 秒缓冲")
        self.checkpoint()

    def run(self):
        stamina_mode = self.plan.get('run_control', {}).get('mode') == 'stamina'
        threshold = self.plan['run_control']['minimum_stamina'] if stamina_mode else 0
        last_low_notice = None
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
            try:
                service = getattr(self, 'service_boundary', None)
                if service:
                    service()
            except InterruptedError:
                if self.stop.is_set():
                    break
                raise
            if self.stop.is_set():
                break
            # Food navigation can cross a diamond deadline; recheck priority.
            if ((self.pause_event is not None and self.pause_event.is_set())
                    or (self.plan['diamonds']['enabled'] and self.clock() >= self.diamond_due)):
                continue
            if not stamina_mode and self.completed_runs >= self.plan['max_total_runs']:
                self.succeeded = True
                self.reason = '地下城预定次数已完成'
                break
            if not self.available_tasks():
                now = self.clock()
                if last_wait_notice is None or now-last_wait_notice >= 30:
                    self.emit('schedule', dict(kind='dungeon', phase='rewards_only',
                        task='已勾选副本均已跳过，继续钻石和体力领取',
                        skipped_dungeons=sorted(self.skipped_dungeons), completed_runs=self.completed_runs))
                    last_wait_notice = now
                if self.wait(1.):
                    break
                continue
            current_stamina = self.read_stamina() if stamina_mode else threshold
            if current_stamina < threshold:
                now = self.clock()
                if last_low_notice is None or now-last_low_notice >= 30:
                    self.emit('schedule', dict(kind='stamina',phase='waiting',task='等待体力恢复',
                        stamina=current_stamina,threshold=threshold,completed_runs=self.completed_runs))
                    last_low_notice = now
                if self.wait(1.):
                    break
                continue
            if last_low_notice is not None:
                self.emit('log', f'体力已恢复到 {current_stamina}，达到阈值 {threshold}，继续挂机')
                last_low_notice = None
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
            if stamina_mode and self.read_stamina() < threshold:
                continue
            task = self.choose()
            if task is None:
                now = self.clock()
                next_task = min(self.available_tasks(), key=lambda t: (self.ready_at[t['id']], t['order']))
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
            if not self.stop.is_set() and isinstance(ok, DungeonAbandoned):
                self.skipped_dungeons.add(task['id'])
                self.in_flight = None
                self.cooldown_deferrals.pop(task['id'], None)
                self.checkpoint()
                self.emit('log', f"{task['display_name']}：已放弃，本次挂机跳过此副本；不计通关，继续其他已勾选副本及钻石、体力任务")
                self.emit('schedule', dict(kind='dungeon', phase='skipped', task=task['display_name'],
                    task_id=task['id'], reason=reason, completed_runs=self.completed_runs,
                    skipped_dungeons=sorted(self.skipped_dungeons)))
                continue
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
