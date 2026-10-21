"""Bounded, single-submit Golden Kingdom automation, independent of dungeon state."""
from collections import deque
from dataclasses import dataclass
import threading
import time
from types import SimpleNamespace

# Synchronous messages acknowledge map/detail controls without moving the cursor.
FORBIDDEN_INPUT = 'SendMessage'

LABELS = dict(navigate='定位禁域入口', navigation_wait='等待导航结果', ready='准备探索',
              encounter='等待探索事件', battle='自动战斗中', auto_wait='确认自动战斗开启',
              xp_wait='等待战利品', loot_wait='确认战斗结束', chest_wait='确认免费宝箱领取',
              chest_return='确认返回探索', home='进入传送门', menu='进入禁域', map='选择黄金古国',
              detail='去探险', explore='探索', auto_off='开启自动战斗', auto_on='自动战斗已开启',
              chest_free='免费打开一个宝箱', chest_claimed='关闭已领取宝箱', xp='领取经验',
              loot='领取战利品', unknown='未识别到支持的页面')

# Labels are stable recognition anchors; game input belongs to the icon/card
# above them. Same normalized coordinate convention as dungeon name navigation.
CLICK_OFFSETS = {'explore': (0,-55), 'menu': (0,-45), 'map': (0,-150)}


@dataclass(frozen=True)
class ForbiddenOptions:
    rounds: int = 10
    minimum_stamina: int = 3

    def validate(self):
        if type(self.rounds) is not int or not 1 <= self.rounds <= 10000:
            raise ValueError('禁域探索次数必须为 1 到 10000')
        if type(self.minimum_stamina) is not int or not 3 <= self.minimum_stamina <= 320:
            raise ValueError('禁域体力下限必须为 3 到 320')
        return self


class ForbiddenFlow:
    """Only positive successor observations discharge an input reservation.

    step receives a scene already confirmed on two fresh frames. A returned
    action is reserved BEFORE sending input; no timeout retries that action.
    """
    def __init__(self, options, now):
        self.options = options.validate()
        self.phase = 'navigate'
        self.deadline = now+45
        self.completed = 0
        self.reason = ''
        self.done = False
        self.expected = set()
        self.auto_submitted = False

    def move(self, phase, now, seconds=30):
        self.phase, self.deadline = phase, now+seconds

    def check_time(self, now):
        if not self.done and now >= self.deadline:
            raise TimeoutError(f'禁域 {LABELS[self.phase]} 等待超时；未重复点击，请检查诊断')

    def step(self, kind, clickable, stamina, now):
        self.check_time(now)
        if self.done:
            return None
        if kind == 'defeat':
            raise RuntimeError('禁域战斗失败，已停止；请手动处理战败页面')
        if self.phase == 'navigation_wait':
            if kind not in self.expected:
                return None
            self.move('navigate', now)
        if self.phase == 'navigate':
            if kind == 'explore':
                self.move('ready', now)
            elif clickable and kind in ('home','menu','map','detail'):
                self.expected = {'home':{'menu','map'}, 'menu':{'map','detail'},
                                 'map':{'detail'}, 'detail':{'explore'}}[kind]
                self.move('navigation_wait', now)
                return kind
            else:
                # Starting inside a battle or chest modal lacks map ownership.
                return None
        if self.phase == 'ready' and kind == 'explore' and clickable:
            if self.completed >= self.options.rounds:
                self.done, self.reason = True, '已完成设定的禁域探索次数'
                return None
            if stamina is None:
                raise RuntimeError('禁域探索前体力未知，未发送点击')
            if stamina < self.options.minimum_stamina:
                self.done, self.reason = True, '体力低于禁域下限，已停止探索'
                return None
            self.auto_submitted = False
            self.move('encounter', now, 60)
            return 'explore'
        if self.phase in ('encounter','battle','auto_wait'):
            if kind in ('auto_off','auto_on'):
                if self.phase == 'encounter':
                    self.move('battle', now, 180)
                if kind == 'auto_off' and clickable and not self.auto_submitted:
                    self.auto_submitted = True
                    self.move('auto_wait', now, 20)
                    return 'auto_off'
                if kind == 'auto_on' and self.phase == 'auto_wait':
                    self.move('battle', now, 180)
            elif kind == 'xp' and clickable:
                self.move('xp_wait', now)
                return 'xp'
            elif kind == 'loot' and clickable:
                # Very fast battles may finish before the first battle sample.
                self.move('loot_wait', now)
                return 'loot'
            elif self.phase == 'encounter' and kind == 'chest_free' and clickable:
                self.move('chest_wait', now, 20)
                return 'chest_free'
            elif self.phase == 'encounter' and kind == 'chest_claimed' and clickable:
                self.move('chest_return', now)
                return 'chest_claimed'
        elif self.phase == 'chest_wait':
            if kind == 'chest_claimed' and clickable:
                self.move('chest_return', now)
                return 'chest_claimed'
        elif self.phase == 'xp_wait':
            if kind == 'loot' and clickable:
                self.move('loot_wait', now)
                return 'loot'
        elif self.phase in ('chest_return','loot_wait') and kind == 'explore':
            self.completed += 1
            self.move('ready', now)
            if self.completed >= self.options.rounds:
                self.done, self.reason = True, '已完成设定的禁域探索次数'
        return None


class ForbiddenRunner:
    def __init__(self, backend, options, read_stamina, stop, emit, dry_run=False,
                 clock=time.monotonic):
        self.backend, self.stop_event, self.emit = backend, stop, emit
        self.clock, self.read_stamina, self.dry_run = clock, read_stamina, dry_run
        self.flow = ForbiddenFlow(options, clock())
        self.stats = SimpleNamespace(state='navigate', rounds=0, clicks=0)
        self.reason = ''
        self.history = deque(maxlen=100)
        self.counters = {}
        self.pending_action = None

    def stop(self, reason='用户停止'):
        self.reason = reason
        self.stop_event.set()

    def run(self):
        from .forbidden_vision import ForbiddenVision
        vision = ForbiddenVision(self.backend, self.stop_event)
        previous, stable = None, 0
        try:
            while not self.stop_event.is_set():
                self.flow.check_time(self.clock())
                frame = self.backend.capture(self.stop_event)
                self.emit('image', frame)
                kind, match = vision.observe(frame)
                signature = (kind, match.box if match else None)
                stable = stable+1 if signature == previous else 1
                previous = signature
                self.history.append(dict(scene=kind, phase=self.flow.phase,
                                         at=self.clock(), box=signature[1]))
                if stable >= 2 and self.backend.capture_is_fresh():
                    self.backend.guard()
                    if self.stop_event.is_set():
                        break
                    if self.dry_run:
                        self.reason = f'禁域识别预览：{LABELS.get(kind,kind)}；未点击游戏'
                        break
                    action = self.flow.step(kind, match is not None, self.read_stamina(), self.clock())
                    if action:
                        # Classification and context were both rechecked on this
                        # capture, and the backend rechecks age/identity at input.
                        self.emit('log', f'禁域：{LABELS[action]}，等待确认下一页面')
                        self.backend.click(match, CLICK_OFFSETS.get(action,(0,0)),
                                           self.stop_event, threading.Event())
                        self.stats.clicks += 1
                        stable, previous = 0, None
                    self.stats.state = self.flow.phase
                    self.stats.rounds = self.flow.completed
                    self.counters['forbidden_completed'] = self.flow.completed
                    self.emit('stats', dict(mode='禁域', state=LABELS[self.flow.phase],
                        rounds=self.flow.completed, clicks=self.stats.clicks))
                    if self.flow.done:
                        self.reason = self.flow.reason
                        break
                self.stop_event.wait(.25)
        except InterruptedError:
            if not self.stop_event.is_set():
                raise
        finally:
            if self.stop_event.is_set():
                self.reason = self.reason or '禁域挂机已停止'
        return dict(succeeded=self.flow.done and not self.stop_event.is_set(),
                    preview=self.dry_run, completed_runs=self.flow.completed, reason=self.reason)


def run_forbidden(session, options, dry_run=False, capture='FramePool', input_method=FORBIDDEN_INPUT):
    from .runtime import ROOT
    from .automation import input_lease, boundary_ready
    from .backend import DiagnosticBackend
    from .window_ready import connect_backend
    from .state import require_fresh
    from .food import summarize
    from vendor_core.windows import Windows

    options.validate()
    if input_method == FORBIDDEN_INPUT and capture != 'FramePool':
        raise ValueError('禁域后台点击需要 FramePool 截图；ScreenDC 会切换前台窗口')
    session.wait_ready()
    boundary_ready(session)
    pid = session.snapshot['pid']
    session.diagnostic_context = dict(mode='forbidden', options=vars(options), dry_run=dry_run,
                                      capture=capture, input_method=input_method)

    class MemoryBackend(DiagnosticBackend):
        click_hold_seconds = .15 if input_method == FORBIDDEN_INPUT else 0.0

        def guard(self):
            super().guard()
            if session.stop_event.is_set() or session.operation_stop.is_set():
                raise InterruptedError('禁域挂机已停止')
            require_fresh(session.snapshot, pid)

    with input_lease():
        win, finished = Windows(), threading.Event()
        backend = None
        session.automation_active = not dry_run
        def emergency():
            while not finished.wait(.04):
                if win.emergency_pressed():
                    session.stop_automation('F8 紧急停止')
                    break
        threading.Thread(target=emergency,daemon=True).start()
        try:
            backend = connect_backend(session, win, pid, lambda window:
                MemoryBackend(ROOT, window, capture, input_method, session.operation_stop))
            backend.emit = session.publish
            session.diagnostic_context['window_geometry'] = backend.window_geometry
            def stamina():
                require_fresh(session.snapshot,pid)
                return summarize(session.snapshot['data'].get('food_fields'))['count']
            runner = ForbiddenRunner(backend, options, stamina, session.operation_stop,
                                     session.publish, dry_run)
            session.runner = runner
            result = runner.run()
            session.diagnostic_context['forbidden_recognition'] = list(backend.recognition_trace)
            if dry_run:
                session.save_diagnostic('forbidden_preview', result['reason'])
            session.publish('batch_result', result)
            return result
        except Exception as exc:
            if backend:
                session.diagnostic_context['forbidden_recognition'] = list(backend.recognition_trace)
            session.save_diagnostic('forbidden',str(exc))
            raise
        finally:
            finished.set()
            session.automation_active = False
            session.runner = None
            if backend:
                backend.cancel()
