"""Bounded scene recovery, with fresh memory required before gameplay resumes."""
import copy
import time
from .action_guard import GuardedRunner as Runner
from .cooldowns import DUNGEON_IDS
from .list_guard import LIST_RULE


def evidence(template, **kw):
    return dict(template=template, threshold=.95, min_contrast_ratio=.85,
                min_brightness_ratio=.85, **kw)


HOME = evidence('主界面锚点.png')
LOGIN = evidence('重连登录标识.png')
UNFINISHED = evidence('未完成地下城提示.png')
TIMEOUT = dict(template='游戏timeout提示.png', threshold=.94, roi=[560,280,280,110])
SESSION_EXPIRED = evidence('会话超时提示.png', roi=[510,235,380,150])


def gameplay_states(config):
    return [s for s in config['states'] if s == 'Progress' or s.startswith(('OpeningBuff','SkillCard'))]


def boundary_profile():
    from .diamond_profile import entry_recovery_rules
    cleanup = entry_recovery_rules()
    for rule in cleanup:
        rule['next'] = 'Boundary'
    # A stopped selection may leave details open before the next service wait.
    # Close only this known modal; never press Explore or treat it as a run.
    cleanup.append(dict(name='领取边界关闭遗留副本详情', template='副本详情关闭.png',
        roi=[870,100,120,110], threshold=.96, min_contrast_ratio=.85,
        min_brightness_ratio=.85,
        requires_match=evidence('副本详情标识.png', roi=[450,320,500,100]),
        foreground=True, action='click', next='Boundary', event='none',
        verify_change=True, success_match={k:copy.deepcopy(v) for k,v in LIST_RULE.items() if k != 'name'},
        retry_center_clicks=1, retry_max_clicks=2, retry_radius=0,
        confirm_frames=2, cooldown_seconds=1))
    cleanup.append(dict(name='领取边界从关卡切回地下城',
        **evidence('地下城入口主体.png', roi=[520,560,160,160]),
        requires_match=evidence('关卡导航选中.png', roi=[400,560,150,160]),
        action='click', next='Boundary', event='none', verify_change=True,
        success_match={k:copy.deepcopy(v) for k,v in LIST_RULE.items() if k != 'name'},
        retry_center_clicks=1, retry_max_clicks=2, retry_radius=0,
        confirm_frames=2, cooldown_seconds=1))
    return dict(version=1,initial_state='Boundary',max_minutes=2,max_rounds=0,poll_seconds=.3,
        states={'Boundary':dict(timeout_seconds=30,rules=cleanup+[
            dict(rule,name='领取边界核验 / '+label,action='none',event='task_complete',confirm_frames=2)
            for label,rule in [('主城',HOME),('地下城列表',LIST_RULE)]])})


def active_task(data, tasks):
    progress = data.get('dungeon_progress') or {}
    if progress.get('result') != 'in progress':
        return None
    game_id = progress.get('dungeon_id')
    task = next((t for t in tasks if 1 <= t.get('order',0) <= len(DUNGEON_IDS)
                 and DUNGEON_IDS[t['order']-1] == game_id and t.get('entrance_template')), None)
    if task is None:
        raise ValueError('未完成副本无法映射到已支持的副本，请先手动完成该副本')
    return task


def recovery_rules():
    common = dict(action='click', verify_change=True, confirm_frames=2,
                  foreground=True, max_activations=3, cooldown_seconds=1,
                  retry_max_clicks=1, retry_center_clicks=1, retry_radius=0)
    return [
        dict(common, name='重连：确认服务器 timeout', template='提示确认文字.png',
             roi=[620,365,160,90], threshold=.94, min_contrast_ratio=.65,
             requires_match=copy.deepcopy(TIMEOUT), recovery_action='timeout'),
        dict(common, **evidence('重连开始冒险.png'), name='重连：开始冒险',
             requires_match=copy.deepcopy(LOGIN), recovery_action='login'),
        dict(common, **evidence('未完成地下城好的.png'), name='重连：接续未完成地下城',
             requires_match=copy.deepcopy(UNFINISHED), recovery_action='unfinished'),
        dict(common, name='重连：确认会话超时', template='提示确认文字.png',
             roi=[620,365,160,90], threshold=.94, min_contrast_ratio=.85,
             min_brightness_ratio=.85, requires_match=copy.deepcopy(SESSION_EXPIRED),
             recovery_action='timeout'),
    ]


def add_resume_start(config):
    """Arm exactly one observed round, without clicking a fresh dungeon entry."""
    rules = []
    for state in gameplay_states(config):
        for original in config['states'].get(state,{}).get('rules',[]):
            if original.get('action') == 'stop' or original.get('defeat_action') == 'abandon':
                continue
            rule = {k:copy.deepcopy(v) for k,v in original.items()
                    if k in ('template','roi','threshold','min_contrast_ratio','min_brightness_ratio','requires_match')}
            rule.update(name=f'接续定位 {state} / {original["name"]}', action='none',
                        next=state, event='round_start', confirm_frames=2)
            rules.append(rule)
    config['states']['ResumeStart'] = dict(timeout_seconds=120, rules=rules)
    config['initial_state'] = 'ResumeStart'
    return config


class RecoveryRunner(Runner):
    def __init__(self, backend, config, *args, **kwargs):
        config = copy.deepcopy(config)
        old = [r for r in config.get('interrupts',[]) if
               r.get('requires_match',{}).get('template') != TIMEOUT['template']]
        config['interrupts'] = recovery_rules()+old
        # Temporarily scope the v0.3.0 protocol to bakery tasks only. All other
        # flows keep their original verifier, retries and foreground dispatch.
        super().__init__(backend, config, *args,
                         enable_guard=config.get('profile',{}).get('id') == 'food', **kwargs)
        self.round_armed = False
        self.list_resets = 0
        self.reconnects = 0
        self.arm_after_recovery = False
        self.recovery_elapsed = 0.
        self.raw_clock = self.clock
        self.clock = lambda: self.raw_clock()-self.recovery_elapsed

    def record(self, event, **details):
        if event == 'transition':
            previous, current = details.get('previous'), details.get('next')
            if previous in ('Explore','ResumeStart') and current in gameplay_states(self.config):
                self.round_armed = True
            if previous == 'ReturnEntry' and current == 'Entry':
                self.round_armed = False
        return super().record(event, **details)

    def pending_success(self, image):
        if self.arm_after_recovery:
            if self.counters.get('reconnect_round_started'):
                self.arm_after_recovery = False
            else:
                for original in self.config['states'][self.stats.state]['rules']:
                    if original.get('action') == 'stop': continue
                    match = self._hit(image,original)
                    if match:
                        rule = dict(original, name='重连接续：开始已存在的一轮',action='none',
                                    event='round_start',next=self.stats.state,confirm_frames=2,
                                    increment_counter='reconnect_round_started')
                        return rule,match
                return None
        pending = super().pending_success(image)
        if pending or self.stats.state != 'SelectDungeon' or self.stats.idle_seconds < 5:
            return pending
        names = self.config.get('navigation_names', [])
        if not names or self.list_resets >= 2:
            return None
        # Any known name or foreground modal cancels fallback. No title is
        # required during the subsequent reset drags, only the selected tab.
        if any(self.backend.recognize(image, dict(name='列表重定位 / '+name,
                    **evidence(name)), self.stop_event) for name in names):
            return None
        if any(self.backend.recognize(image,r,self.stop_event) for r in self.ordered_rules()
               if r.get('foreground') or r.get('action') == 'click'):
            return None
        rule = dict(evidence('地下城入口主体.png'), name='列表无名称：回到地下城并拖至最左端',
                    action='click', verify_change=True, confirm_frames=2,
                    recovery_action='reset_list', next='SelectDungeon')
        match = self.backend.recognize(image,rule,self.stop_event)
        return (rule,match) if match else None

    def ordered_rules(self):
        # A verified recovery may change state once. Keep subsequent interrupt
        # configurations state-neutral, including diagnostics and later cycles.
        for rule in self.config.get('interrupts',[]):
            if rule.get('recovery_action'):
                rule.pop('next',None)
        return super().ordered_rules()

    def _capture(self):
        if self.stop_event.is_set():
            raise InterruptedError('恢复操作已停止')
        if self.pause_event.is_set():
            raise RuntimeError('恢复期间暂停，停止并保留诊断')
        self.backend.guard()
        frame = self.backend.capture(self.stop_event)
        self.emit('image',frame)
        return frame

    def _hit(self, frame, rule):
        return self.backend.recognize(frame,dict(rule,name=rule.get('name','恢复页面核验')),self.stop_event)

    def _single_click(self, rule, match):
        frame = self._capture()
        match = self._hit(frame,rule)
        if match is None:
            return False
        self.backend.recovery_click_allowed = rule.get('recovery_action') in ('timeout','login','unfinished')
        try:
            self.backend.click(match,[0,0],self.stop_event,self.pause_event)
        finally:
            self.backend.recovery_click_allowed = False
        self.stats.clicks += 1
        self.record('recovery_click', rule=rule['name'], box=list(match.box))
        return True

    def _reset_list(self, rule, match):
        if self.list_resets >= 2:
            raise RuntimeError('列表重定位已达两次，停止并保存诊断')
        if not self._single_click(rule,match):
            return False
        self.list_resets += 1
        stable = 0
        for _ in range(12):
            if self.stop_event.wait(.3): raise InterruptedError()
            stable = stable+1 if self._hit(self._capture(),LIST_RULE) else 0
            if stable >= 2: break
        if stable < 2:
            raise RuntimeError('点击地下城后未确认列表，不执行拖动')
        for index in range(6):
            frame = self._capture()
            if not self._hit(frame,LIST_RULE):
                raise RuntimeError('列表重定位时页面被遮挡或已离开，停止拖动')
            self.backend.swipe('right',self.stop_event,self.pause_event)
            self.record('list_reset_swipe', index=index+1, limit=6)
            if self.stop_event.wait(.8): raise InterruptedError()
        self.swipes = 0
        self.emit('log','已向右拖动六次回到列表最左端，从一本重新识别目标')
        return True

    def _destination(self, frame):
        provider = getattr(self.backend,'recovery_data',None)
        if not callable(provider):
            raise RuntimeError('恢复缺少有效内存校验接口')
        data = provider()  # None while bounded rebind is in progress.
        if data is None:
            return None
        progress = data.get('dungeon_progress') or {}
        expected = self.config.get('expected_dungeon_id')
        if expected:
            if getattr(self,'abandon_requested',False):
                if progress.get('result') == 'in progress' and progress.get('dungeon_id') != expected:
                    raise RuntimeError('放弃恢复后副本身份不一致')
                for candidate in self.config['states'].get('DefeatReturn',{}).get('rules',[]):
                    if self._hit(frame,candidate): return 'DefeatReturn'
                return None
            if progress.get('result') == 'in progress':
                if progress.get('dungeon_id') != expected:
                    raise RuntimeError('重连后的副本与接续目标不一致，停止并保存诊断')
                for state in gameplay_states(self.config):
                    for candidate in self.config['states'].get(state,{}).get('rules',[]):
                        if candidate.get('action') != 'stop' and self._hit(frame,candidate):
                            return state
                return None
            if self._hit(frame,LIST_RULE) or self._hit(frame,HOME):
                if self.round_armed:
                    if progress.get('result') == 'finished' and progress.get('dungeon_id') == expected:
                        return 'ReturnEntry'
                    raise RuntimeError('重连后进行中的副本已丢失，未计完成')
                if self.config['initial_state'] == 'ResumeStart':
                    raise RuntimeError('待接续副本已消失，未计完成')
                return 'Entry'
        elif progress.get('result') == 'in progress':
            raise RuntimeError('领取任务重连发现未完成副本，停止领取并保留诊断')
        elif self._hit(frame,HOME) or self._hit(frame,LIST_RULE):
            return self.config['initial_state']
        return None

    def _reconnect(self, rule, match):
        if self.reconnects >= 3:
            raise RuntimeError('服务器重连已达三次，停止并保存诊断')
        self.reconnects += 1
        self.record('reconnect_begin', attempt=self.reconnects, pending=self.pending_action[1]['name'] if self.pending_action else None)
        self.emit('log',f'服务器/登录恢复 {self.reconnects}/3：等待重新进入并核验内存')
        if not self._single_click(rule,match):
            return False
        clicked = {rule['recovery_action']}
        deadline = time.monotonic()+60
        last, streak = None,0
        for _ in range(120):
            if self.stop_event.wait(.4): raise InterruptedError('重连已停止')
            if time.monotonic() >= deadline: break
            frame = self._capture()
            hit = None
            for candidate in recovery_rules():
                found = self._hit(frame,candidate)
                if found:
                    hit = candidate,found
                    break
            if hit:
                candidate, match = hit
                key = candidate['recovery_action']
                streak = streak+1 if last == key else 1
                last = key
                if streak >= 2 and key not in clicked:
                    if self._single_click(candidate,match): clicked.add(key)
                    last,streak = None,0
                continue
            destination = self._destination(frame)
            streak = streak+1 if destination and last == destination else 1
            last = destination
            if destination and streak >= 2:
                rule['next'] = destination
                if destination in gameplay_states(self.config) and not self.round_armed:
                    self.arm_after_recovery = True
                    self.round_armed = True
                if self.counters.get('food_collection_requests'):
                    verify = getattr(self.backend,'food_credit_verified',None)
                    if not callable(verify) or not verify():
                        raise RuntimeError('重连后未确认体力请求到账，不重复领取；请检查诊断')
                    self.counters['food_collections'] = 1
                    rule['next'] = 'FoodDone'
                self.pending_action = None
                self.record('reconnect_verified', destination=destination)
                self.emit('log','重连已核验，继续当前副本/领取流程')
                return True
        raise RuntimeError('60 秒内未完成登录与返回核验，停止并保存诊断')

    def click_with_change_confirmation(self, rule, match):
        action = rule.get('recovery_action')
        if not action:
            return super().click_with_change_confirmation(rule,match)
        if self.dry_run: return False
        if action == 'reset_list': return self._reset_list(rule,match)
        started = self.raw_clock()
        try:
            return self._reconnect(rule,match)
        finally:
            # Recovery has its own 60-second deadline. Do not spend the old
            # room's timeout while waiting at the login screen.
            self.recovery_elapsed += max(0,self.raw_clock()-started)
