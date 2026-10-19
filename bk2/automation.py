"""BKCrusade scheduling with BK2 memory interlocks and an operation-wide lock."""
import copy
import json
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from .runtime import ROOT
from .state import require_fresh
from vendor_core.unified import UnifiedScheduler, ScheduleStore
from .profiles import build_dungeon_profile
from vendor_core.config import validate
from .plan import validate_dungeon_plan
from .recovery import RecoveryRunner as Runner
from .battle import BattleRunner
from vendor_core.diamonds import DiamondResult
from .list_guard import ListCooldownScanner, LIST_RULE, list_navigation_profile
from .cooldowns import summarize
from .scheduler import MemoryScheduler, MemoryCooldown, DungeonAbandoned
from .food import summarize as summarize_food
from .food_profile import build_food_profile
from .diamond_profile import build_diamond_profile


def validate_food_options(plan):
    options = plan.setdefault('food', {'enabled': True})
    if not isinstance(options, dict) or not isinstance(options.get('enabled'), bool):
        raise ValueError('food.enabled 必须为布尔值')
    if options['enabled']:
        validate(build_food_profile(), ROOT / 'assets/image', True)
    options.setdefault('interval_seconds', 1800)
    if options['interval_seconds'] != 1800:
        raise ValueError('体力领取检查间隔固定为 1800 秒')
    control = plan.get('run_control', {'mode': 'rounds'})
    if not isinstance(control, dict) or control.get('mode') not in ('rounds','stamina'):
        raise ValueError('挂机方式无效')
    if control['mode'] == 'stamina':
        threshold = control.get('minimum_stamina')
        if isinstance(threshold, bool) or not isinstance(threshold, int) or not 5 <= threshold <= 320:
            raise ValueError('体力阈值必须为 5 到 320 的整数')
    return plan


def load_plan(path=None):
    path = Path(path) if path else ROOT / 'config/dungeon_plan.json'
    validate(build_diamond_profile(), ROOT / 'assets/image', True)
    from .recovery import boundary_profile, recovery_rules
    probe = boundary_profile()
    probe['interrupts'] = recovery_rules()
    validate(probe, ROOT / 'assets/image', True)
    plan = validate_food_options(validate_dungeon_plan(json.loads(path.read_text(encoding='utf-8-sig')),
                                 ROOT / 'config/profiles', ROOT / 'assets/image', True))
    base = json.loads((ROOT/'config/profiles/dungeon_unlimited.json').read_text(encoding='utf-8-sig'))
    for task in plan['tasks']:
        if task['enabled']:
            validate(build_dungeon_profile(base, task, plan['tasks']), ROOT/'assets/image', True)
    return plan


def save_plan(plan, path=None):
    validate_food_options(plan)
    validate_dungeon_plan(plan, ROOT / 'config/profiles', ROOT / 'assets/image', True)
    path = Path(path) if path else ROOT / 'config/dungeon_plan.json'
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)


def boundary_ready(session):
    require_fresh(session.snapshot, (session.snapshot or {}).get('pid'))
    if (session.snapshot['data'].get('dungeon_progress') or {}).get('result') == 'in progress':
        raise RuntimeError('内存显示副本仍在进行；请先完成当前副本再启动轮换，不会中途执行钻石任务')


@contextmanager
def input_lease():
    import msvcrt
    (ROOT / 'logs').mkdir(exist_ok=True)
    with (ROOT / 'logs/input.lock').open('a+b') as lock:
        lock.seek(0, 2)
        if not lock.tell():
            lock.write(b'0'); lock.flush()
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise RuntimeError('另一个 BK2 已在操作游戏') from exc
        try:
            yield
        finally:
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)


class Batch:
    def __init__(self, session, plan, backend_factory, dry_run=False, clock=time.time, store=None, food_store=None):
        self.session, self.plan, self.factory = session, copy.deepcopy(plan), backend_factory
        self.dry_run, self.clock, self.store = dry_run, clock, store
        self.backend = None
        self.scheduler = None
        self.last_diamond_deadline = None
        self.await_changed_cooldown = {}
        self.food_store = food_store
        self.food_due = food_store.load(self.clock()) if food_store else 0
        self.next_boundary_check = 0.

    def service_boundary(self):
        if self.stopped(): raise InterruptedError('操作已停止')
        if self.clock() >= self.next_boundary_check or time.monotonic() < getattr(self.session,'memory_recovery_until',0):
            from .recovery import boundary_profile
            runner = self.execute(boundary_profile())
            if not runner.completed:
                raise RuntimeError('等待领取期间未确认主城/副本列表：'+runner.reason)
            self.next_boundary_check = self.clock()+30
        boundary_ready(self.session)
        self.maybe_food()

    def stopped(self):
        return self.session.stop_event.is_set() or self.session.operation_stop.is_set()

    def execute(self, config, dungeon=False):
        if self.stopped():
            raise InterruptedError('操作已停止')
        self.session.diagnostic_context.update(config=config, dry_run=self.dry_run)
        self.session.diagnostic_context.pop('runner', None)
        self.backend = None
        self.backend = self.factory()
        self.backend.required_completion = self.session.ledger.finished_transitions + 1 if dungeon else None
        runner_type = BattleRunner if dungeon else Runner
        runner = runner_type(self.backend, validate(config, ROOT / 'assets/image', True),
                        self.session.publish, self.dry_run)
        self.session.runner = runner
        try:
            if self.stopped():
                runner.stop('操作已停止')
            runner.run()
            self.session.diagnostic_context['runner'] = dict(
                state=runner.stats.state, reason=runner.reason,
                abandoned=getattr(runner, 'abandoned', False),
                history=list(runner.history), counters=runner.counters,
                pending_action=runner.pending_action[1]['name'] if runner.pending_action else None)
            if getattr(runner, 'abandoned', False) and not self.stopped() and not self.dry_run:
                self.session.save_diagnostic('dungeon_abandoned', runner.reason)
            elif not (runner.completed or runner.manual_handoff or runner.deferred
                    or runner.reason == '达到完成轮数上限') and not self.stopped() and not self.dry_run:
                self.session.save_diagnostic('runner_stopped', runner.reason)
            return runner
        finally:
            self.session.runner = None

    def dungeon(self, task, resume=False):
        if time.monotonic() < getattr(self.session,'memory_recovery_until',0):
            self.service_boundary()
        if resume:
            from .recovery import active_task
            require_fresh(self.session.snapshot,self.session.snapshot['pid'])
            current = active_task(self.session.snapshot['data'],self.plan['tasks'])
            if current is None or current['id'] != task['id']:
                raise RuntimeError('接续副本与当前内存状态不一致')
        else:
            boundary_ready(self.session)
            self.maybe_food()
        before = self.memory_times()['dungeons'].get(task['id'], {}).get('deadline_unix')
        base = json.loads((ROOT / 'config/profiles/dungeon_unlimited.json').read_text(encoding='utf-8-sig'))
        config = build_dungeon_profile(base, task, self.plan['tasks'])
        if resume:
            from .recovery import add_resume_start
            add_resume_start(config)
        baseline = self.session.ledger.finished_transitions
        runner = self.execute(config, dungeon=True)
        if getattr(runner, 'abandoned', False) and not self.stopped() and not self.dry_run:
            # UI exit is confirmed. Allow the background memory reader to catch up
            # before exposing the boundary to diamonds, food or another dungeon.
            deadline = time.monotonic()+5
            while not self.stopped():
                require_fresh(self.session.snapshot, self.session.snapshot['pid'])
                data = self.session.snapshot['data']
                if 'dungeon_progress' not in data:
                    raise RuntimeError('放弃后副本内存状态缺失，未继续调度')
                if (data['dungeon_progress'] or {}).get('result') != 'in progress':
                    self.sync_diamond_deadline()
                    return DungeonAbandoned(), runner.reason
                if time.monotonic() >= deadline:
                    raise RuntimeError('放弃后内存仍显示副本进行中，未继续调度；请检查诊断')
                self.session.operation_stop.wait(.2)
            return False, '放弃后核验期间用户停止'
        if getattr(runner, 'deferred', False) and not self.stopped() and not self.dry_run:
            estimate = getattr(runner, 'cooldown_estimate', None)
            return (estimate.to_schedule(time.monotonic(), self.clock()) if estimate else None), runner.reason
        ok = (not self.stopped() and not self.dry_run and runner.stats.rounds == 1
              and runner.reason == '达到完成轮数上限'
              and self.session.ledger.finished_transitions > baseline)
        if ok:
            self.await_changed_cooldown[task['id']] = before
            self.sync_diamond_deadline()
            self.maybe_food()
        return ok, runner.reason

    def diamonds(self):
        if time.monotonic() < getattr(self.session,'memory_recovery_until',0):
            self.service_boundary()
        boundary_ready(self.session)
        config = build_diamond_profile()
        runner = self.execute(config)
        if (not self.stopped() and not self.dry_run and getattr(runner, 'completed', False)
                and getattr(runner, 'cooldown_estimate', None) is not None):
            outcome = DiamondResult(runner.cooldown_estimate.to_schedule(time.monotonic(), self.clock()),
                                    runner.counters.get('diamond_rewards', 0) > 0)
            if outcome.valid_at(self.clock()):
                self.maybe_food(after_diamonds=True)
                return outcome, '已确认20钻石奖励并返回主界面' if outcome.collected else '尚在冷却，已返回主界面'
            return False, '钻石冷却结果未通过校验'
        if self.stopped() or self.dry_run or not runner.manual_handoff:
            return False, runner.reason
        self.session.manual_ready.clear()
        self.session.waiting_for_diamonds = True
        self.session.publish('manual_diamonds', '已点击钻石入口，挂机暂停。请手动领取并返回地下城列表，再点击“已手动领取，继续”。')
        try:
            while not self.session.operation_stop.wait(0.1):
                if self.session.stop_event.is_set():
                    break
                require_fresh(self.session.snapshot, self.session.snapshot['pid'])
                if self.session.manual_ready.is_set():
                    boundary_ready(self.session)
                    self.session.publish('log', '钻石由用户确认已手动领取；重新计时两小时。')
                    return True, '用户确认手动领取'
            return False, '钻石交接期间停止'
        finally:
            self.session.waiting_for_diamonds = False
            self.session.manual_ready.clear()

    def memory_times(self):
        require_fresh(self.session.snapshot, self.session.snapshot['pid'])
        return summarize(self.session.snapshot['data'].get('cooldown_fields'), self.clock())

    def maybe_food(self, after_diamonds=False, force=False, recover_before=None):
        if self.dry_run or self.stopped() or not self.plan.get('food', {}).get('enabled', False):
            return None
        boundary_ready(self.session)
        if not force and self.clock() < self.food_due:
            return None
        if (not after_diamonds and self.scheduler is not None
                and self.plan['diamonds']['enabled'] and self.clock() >= self.scheduler.diamond_due):
            return None
        current = summarize_food(self.session.snapshot['data'].get('food_fields'), self.clock())
        before = recover_before or current
        if recover_before and not (current['count'] is not None
                and current['count'] > before['count'] and current.get('start_time',0) > before['start_time']):
            raise RuntimeError('恢复领取前必须核验原记录对应的体力增加和制作时间更新')
        if current['status'] == 'unknown':
            return None
        self.food_due = self.clock()+1800
        if self.food_store:
            self.food_store.save(self.food_due)
        self.session.publish('food_schedule', {'next_check':self.food_due})
        if not recover_before and (before['status'] != 'ready' or before['claimable'] <= 0):
            return None
        self.session.publish('schedule', dict(kind='food', phase='running', task='免费体力',
            completed_runs=self.scheduler.completed_runs if self.scheduler else 0))
        self.session.publish('log', f"体力 {before['count']}/{before['capacity']}，预计可领取 {before['claimable']}；开始面包工坊流程")
        config = build_food_profile()
        claim = dict(before=before, response_timeout_seconds=config['states']['FoodAwaitReward']['timeout_seconds'],
                     recovering=bool(recover_before))
        self.session.diagnostic_context['food_claim'] = claim
        if recover_before:
            config['initial_state'] = 'FoodReward'
        runner = self.execute(config)
        claim.update(counters=dict(runner.counters), ui_completed=runner.completed,
                     after=summarize_food((self.session.snapshot or {}).get('data', {}).get('food_fields'), self.clock()))
        if self.stopped():
            raise InterruptedError('体力领取期间停止')
        if not runner.completed or (not recover_before and runner.counters.get('food_collections') != 1):
            raise RuntimeError('体力领取界面流程未确认完成：' + runner.reason)
        until = time.monotonic()+5
        while time.monotonic() < until:
            boundary_ready(self.session)
            after = summarize_food(self.session.snapshot['data'].get('food_fields'), self.clock())
            delta = None if after['count'] is None else after['count']-before['count']
            # A partial collection advances start_time by 80 seconds per bread;
            # a fully matured batch may reset start_time to the collection time.
            if (delta and 0 < delta <= before['capacity']-before['count']
                    and after.get('start_time', 0) > before['start_time']):
                outcome = dict(before=before, after=after, received=delta,
                               recovered=bool(recover_before),
                               verified_by='bread_increase_and_bakery_start_advance')
                self.session.publish('food_collected', outcome)
                self.session.publish('log', f"体力领取已验证：{before['count']} → {after['count']}，增加 {delta} 点")
                return outcome
            if self.session.operation_stop.wait(.2):
                raise InterruptedError('体力核验期间停止')
        raise RuntimeError('面板已关闭，但体力数量/制作时间未确认更新；停止并保存诊断')

    def stamina_count(self):
        boundary_ready(self.session)
        value = summarize_food(self.session.snapshot['data'].get('food_fields'),self.clock())
        if value['count'] is None:
            raise RuntimeError('体力数值未知，停止进入副本')
        return value['count']

    def sync_diamond_deadline(self):
        if self.scheduler is None or not self.plan['diamonds']['enabled']:
            return
        value = self.memory_times()['diamonds']
        if value['status'] == 'unknown':
            return
        deadline = value['deadline_unix']
        if deadline != self.last_diamond_deadline:
            self.last_diamond_deadline = deadline
            self.scheduler.diamond_due = max(self.clock(), deadline + 3)
            self.scheduler.checkpoint()
            self.session.publish('log', f"免费钻石内存冷却剩余 {value['remaining_seconds']:.1f} 秒；只在副本边界处理")

    def scan_cooldowns(self):
        boundary_ready(self.session)
        self.sync_diamond_deadline()
        self.maybe_food()
        known = {}
        times = self.memory_times()['dungeons']
        unknown = []
        for task in self.plan['tasks']:
            if (not task['enabled'] or task['fallback']
                    or (self.scheduler is not None and task['id'] in self.scheduler.skipped_dungeons)):
                continue
            value = times.get(task['id'], {})
            deadline = value.get('deadline_unix')
            unchanged = (task['id'] in self.await_changed_cooldown
                         and deadline == self.await_changed_cooldown[task['id']])
            if value.get('status') in ('ready', 'cooling') and not unchanged:
                known[task['id']] = MemoryCooldown(deadline)
                self.await_changed_cooldown.pop(task['id'], None)
            else:
                unknown.append(task['id'])
        if not unknown or self.stopped():
            return known
        self.session.publish('log', '部分已选副本内存冷却未知，使用原版列表 OCR 兜底：' + ', '.join(unknown))
        tasks = copy.deepcopy(self.plan['tasks'])
        for task in tasks:
            task['enabled'] = task['id'] in unknown
        pause = threading.Event()
        self.backend = self.factory()
        try:
            image = self.backend.capture(self.session.operation_stop)
            self.session.publish('image', image)
            if not self.backend.recognize(image, LIST_RULE, self.session.operation_stop):
                self.backend.cancel()
                base = json.loads((ROOT/'config/profiles/dungeon_unlimited.json').read_text(encoding='utf-8-sig'))
                selected = next(t for t in tasks if t['enabled'])
                nav = self.execute(list_navigation_profile(base, selected, tasks))
                if self.stopped():
                    return known
                if not nav.completed:
                    raise RuntimeError('无法安全到达地下城列表：' + nav.reason)
                self.backend = self.factory()
            known.update(ListCooldownScanner(self.backend, tasks, self.session.operation_stop,
                                            pause, self.session.publish).scan())
            return known
        finally:
            self.backend.cancel()

    def run(self):
        from .recovery import active_task, add_resume_start
        require_fresh(self.session.snapshot,self.session.snapshot['pid'])
        unfinished = active_task(self.session.snapshot['data'],self.plan['tasks'])
        if self.dry_run:
            # Preview never constructs/persists cooldown state or enters manual handoff.
            task = unfinished or next((t for t in self.plan['tasks'] if t['enabled']),None)
            if task is None:
                config = build_food_profile() if self.plan.get('food',{}).get('enabled') else build_diamond_profile()
                config['max_minutes'] = 1
                runner = self.execute(config)
                return {'succeeded':False,'preview':True,'completed_runs':0,'reason':runner.reason}
            base = json.loads((ROOT / 'config/profiles/dungeon_unlimited.json').read_text(encoding='utf-8-sig'))
            config = build_dungeon_profile(base, task, self.plan['tasks'])
            if unfinished: add_resume_start(config)
            config['max_minutes'] = 1
            runner = self.execute(config, dungeon=True)
            return {'succeeded': False, 'preview': True, 'completed_runs': 0, 'reason': runner.reason}
        self.session.diagnostic_context['plan'] = self.plan
        self.scheduler = MemoryScheduler(self.plan, self.dungeon, self.diamonds,
            self.session.operation_stop, self.session.publish, self.store, self.clock,
            scan_cooldowns=self.scan_cooldowns)
        self.scheduler.read_stamina = self.stamina_count
        self.scheduler.service_boundary = self.service_boundary
        if unfinished:
            self.session.publish('log',f"先接续未完成的{unfinished['display_name']}；完成后再处理领取和已勾选副本")
            ok, reason = self.dungeon(unfinished,resume=True)
            if isinstance(ok,DungeonAbandoned):
                self.scheduler.skipped_dungeons.add(unfinished['id'])
            elif ok:
                self.scheduler.completed_runs += 1
                self.scheduler.ready_at[unfinished['id']] = self.clock()+unfinished['cooldown_seconds']
            else:
                raise RuntimeError('接续副本未确认结束：'+reason)
            self.scheduler.in_flight = None
            self.scheduler.checkpoint()
        self.sync_diamond_deadline()
        self.scheduler.run()
        if not self.scheduler.succeeded and not self.stopped():
            self.session.save_diagnostic('scheduler_failed', self.scheduler.reason)
        return {'succeeded': self.scheduler.succeeded and not self.stopped(), 'preview': False,
                'completed_runs': self.scheduler.completed_runs, 'reason': self.scheduler.reason,
                'skipped_dungeons': sorted(self.scheduler.skipped_dungeons),
                'diamond_due': self.scheduler.diamond_due}


def run_plan(session, plan=None, dry_run=False, capture='FramePool', input_method='PostMessage', food_only=False, food_recovery=None, diamond_cleanup_only=False):
    from .backend import DiagnosticBackend
    from .window_ready import connect_backend
    from vendor_core.windows import Windows
    plan = load_plan() if plan is None else copy.deepcopy(plan)
    session.diagnostic_context = dict(plan=plan, capture=capture, input_method=input_method, dry_run=dry_run)
    validate_dungeon_plan(plan, ROOT / 'config/profiles', ROOT / 'assets/image', True)
    validate_food_options(plan)
    session.wait_ready()
    require_fresh(session.snapshot,session.snapshot['pid'])
    if food_only or diamond_cleanup_only:
        boundary_ready(session)
    with input_lease():
        pid = session.snapshot['pid']
        win = Windows()

        class MemoryBackend(DiagnosticBackend):
            required_completion = None

            def recovery_data(self):
                from .memory import MemoryReadError
                try:
                    require_fresh(session.snapshot,self.window.pid)
                    return session.snapshot['data']
                except MemoryReadError:
                    if time.monotonic() < getattr(session,'memory_recovery_until',0):
                        return None
                    raise

            def food_credit_verified(self):
                data = self.recovery_data()
                if data is None: return False
                before = session.diagnostic_context.get('food_claim',{}).get('before',{})
                after = summarize_food(data.get('food_fields'))
                return (isinstance(before.get('count'),int) and after.get('count') is not None
                        and after['count'] > before['count']
                        and after.get('start_time',0) > before.get('start_time',0))

            def guard(self):
                super().guard()
                if session.stop_event.is_set() or session.operation_stop.is_set():
                    raise InterruptedError('操作已停止')
                self.recovery_data()

            def click(self, *args, **kwargs):
                if not getattr(self,'recovery_click_allowed',False):
                    require_fresh(session.snapshot,self.window.pid)
                return super().click(*args,**kwargs)

            def swipe(self, *args, **kwargs):
                require_fresh(session.snapshot,self.window.pid)
                return super().swipe(*args,**kwargs)

            def recognize(self, frame, rule, stop):
                if self.recovery_data() is None and rule.get('template') not in {
                        '游戏timeout提示.png','提示确认文字.png','重连登录标识.png','重连开始冒险.png',
                        '会话超时提示.png',
                        '未完成地下城提示.png','未完成地下城好的.png'}:
                    return None
                if rule.get('food_collect'):
                    value = summarize_food(session.snapshot['data'].get('food_fields'))
                    if value['status'] != 'ready' or value['claimable'] <= 0:
                        return None
                if (rule.get('event') == 'round_end' and self.required_completion is not None
                        and session.ledger.finished_transitions < self.required_completion):
                    return None
                return super().recognize(frame, rule, stop)

        finished = threading.Event()
        session.automation_active = not dry_run
        def emergency():
            while not finished.wait(.04):
                if win.emergency_pressed():
                    session.stop_automation('F8 紧急停止')
                    break
        threading.Thread(target=emergency, daemon=True).start()
        try:
            def factory():
                backend = connect_backend(session, win, pid,
                    lambda window: MemoryBackend(ROOT, window, capture, input_method))
                backend.emit = session.publish
                return backend
            store = None if dry_run else ScheduleStore(ROOT / 'config/runtime/dungeon_schedule.json')
            from .food import FoodTimerStore
            food_store = None if dry_run else FoodTimerStore(ROOT / 'config/runtime/food_schedule.json')
            batch = Batch(session, plan, factory, dry_run, store=store, food_store=food_store)
            if not dry_run:
                session.publish('food_schedule', {'next_check':batch.food_due})
            if diamond_cleanup_only:
                from .diamond_profile import build_cleanup_profile
                runner = batch.execute(build_cleanup_profile())
                result = dict(succeeded=runner.completed and not batch.stopped() and not dry_run,
                              completed_runs=0,reason=runner.reason,
                              recovery='仅确认遗留钻石奖励、关闭商店并核验主界面')
            elif food_only:
                outcome = batch.maybe_food(force=True, recover_before=food_recovery)
                result = dict(succeeded=not batch.stopped(), completed_runs=0, food=outcome,
                              reason='体力领取已验证' if outcome else '当前无需领取体力（已满、未制作、未知或未启用）')
            else:
                result = batch.run()
            session.publish('batch_result', result)
            return result
        finally:
            session.automation_active = False
            finished.set()
            session.runner = None
            session.waiting_for_diamonds = False
