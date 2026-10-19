"""Bounded action confirmation, enabled only for food by production runners.

Keep the frozen vendor runner intact. Custom battle/reconnection handlers and
the food submit-once/wait states retain their own stronger domain protocols.
"""
import copy
import math
import time
from vendor_core.runner import Runner
from vendor_core.backend import MaaBackend

SINGLE_SUBMIT = {'去探险.png', '钻石免费按钮.png', '领取宝箱.png',
                 '确认buff.png', '确认领取技能卡.png', '选择技能卡.png',
                 '任务领取奖励文字.png'}
# These final destinations already have positive, guarded completion rules.
# Do not infer a successor from arbitrary next-state buttons: they may also
# exist behind a modal on the current page.
DESTINATION_STATES = {'FoodDone'}
STAIR_TEMPLATES = {'去下一层.png', '下一层阶梯核心.png'}
LEAVE_PROMPTS = {'离开祝福弹窗.png', '不要剩余技能卡确定离开.png'}
EVIDENCE_FIELDS = {'template', 'roi', 'threshold', 'min_contrast_ratio',
                   'min_brightness_ratio', 'requires_match'}


def guarded_profile(config):
    result = copy.deepcopy(config)
    for state in list(result['states'].values()) + [{'rules': result.get('interrupts', [])}]:
        for rule in state['rules']:
            if (rule.get('action') != 'click' or not rule.get('verify_change')
                    or rule.get('defeat_action') or rule.get('recovery_action')):
                continue
            single = rule['template'] in SINGLE_SUBMIT or rule.get('event') == 'round_start'
            limit = 1 if single else min(2, int(rule.get('retry_max_clicks', 8)))
            rule.update(action_guard='single_submit' if single else 'navigation',
                        retry_max_clicks=limit, retry_center_clicks=1, retry_radius=0)
            if rule['template'] in STAIR_TEMPLATES:
                # These modals can be caused by the staircase click itself.
                # Hand back to an existing local handler, not a global scan of
                # every foreground action (which would consume the deadline).
                rule['guard_handoffs'] = [candidate['name'] for candidate in state['rules']
                    if candidate.get('foreground') and candidate['template'] in LEAVE_PROMPTS]
            # Reuse already-proven final-state evidence, never relax thresholds
            # or infer success from arbitrary changes/background scene pixels.
            if rule.get('success_match'):
                rule['guard_success'] = [copy.deepcopy(rule['success_match'])]
            elif rule.get('next') in DESTINATION_STATES:
                rule['guard_success'] = [
                    {k:copy.deepcopy(v) for k,v in candidate.items() if k in EVIDENCE_FIELDS}
                    for candidate in result['states'][rule['next']]['rules']
                    if candidate['template'] != rule['template'] and not candidate.get('defeat_action')]
    return result


class GuardedRunner(Runner):
    POLL_SECONDS = .3
    NAVIGATION_WAIT_SECONDS = 3.
    SUBMISSION_WAIT_SECONDS = 15.

    def __init__(self, backend, config, *args, enable_guard=True, **kwargs):
        self.guard_enabled = enable_guard
        super().__init__(backend, guarded_profile(config) if enable_guard else copy.deepcopy(config), *args, **kwargs)
        self.action_guard = {}
        self._action_attempts = {}
        self._foreground_handoffs = {}

    def _resolve_foreground_handoffs(self, state, handler):
        for key, pending in list(self._foreground_handoffs.items()):
            if pending['state'] == state and pending['handler'] == handler:
                self._action_attempts.pop(key, None)
                self._foreground_handoffs.pop(key)
                self.record('foreground_handoff_resolved', original_rule=pending['rule'], handler=handler)

    @staticmethod
    def _budget_key(state, rule):
        group = (rule.get('next'), rule.get('event'), rule.get('increment_counter'),
                 tuple(c['template'] for c in rule.get('guard_success', [])) or (rule['template'],))
        return state, group

    def record(self, event, **details):
        # The vendor's delayed-success path advances only after two frames.
        # Release that completed action's budget, not an interrupted request.
        if (self.guard_enabled and event == 'transition' and details.get('rule', '').endswith(' / 延迟完成确认')
                and self.pending_action):
            state, rule = self.pending_action
            self._action_attempts.pop(self._budget_key(state, rule), None)
            self._guard_status('confirmed', confirmation='delayed_successor', evidence_frames=2)
            self._resolve_foreground_handoffs(state, rule['name'])
        return super().record(event, **details)

    def _guard_status(self, status, **fields):
        self.action_guard.update(status=status, **fields)
        self.backend.action_guard_context = copy.deepcopy(self.action_guard)
        self.record('action_guard', **self.action_guard)

    def _observe_action(self, rule, original, seconds):
        """Observe without clicking; two consistent frames prove the outcome."""
        deadline = time.monotonic() + seconds
        evidence = rule.get('guard_success', [])
        presence = {k:v for k,v in rule.items() if k in EVIDENCE_FIELDS}
        for key in ('requires_match', 'min_brightness_ratio', 'min_contrast_ratio'):
            presence.pop(key, None)  # A dimmed target is not a disappeared target.
        last, streak, source_frames = None, 0, 0
        foreground_last, foreground_frames = None, 0
        handoffs = [candidate for candidate in self.ordered_rules()
                    if candidate['name'] in rule.get('guard_handoffs', [])
                    and candidate.get('foreground') and candidate['name'] != rule['name']]
        for _ in range(math.ceil(seconds / self.POLL_SECONDS)):
            if self.stop_event.wait(self.POLL_SECONDS):
                raise InterruptedError('点击确认期间停止')
            if self.pause_event.is_set():
                raise RuntimeError('点击已提交后暂停，结果尚未确认；停止并保存诊断，避免重复提交')
            if time.monotonic() >= deadline:
                break
            if hasattr(self, '_tick'):
                self._tick()
                if self.runtime_limit_reached():
                    raise RuntimeError('点击确认期间达到运行时限')
            self.backend.guard()
            image = self.backend.capture(self.stop_event)
            self.emit('image', image)
            if self.interrupt_visible(image, rule):
                return 'interrupted', None
            # Production captures already reject blank frames. Repeat the
            # check here so injected/offline backends cannot certify blackouts.
            if hasattr(image, 'shape') and MaaBackend.is_nearly_uniform(image):
                last, streak, source_frames = None, 0, 0
                foreground_last, foreground_frames = None, 0
                continue
            foreground, foreground_match = None, None
            for candidate in handoffs:
                foreground_match = self.backend.recognize(image, candidate, self.stop_event)
                if foreground_match:
                    foreground = candidate
                    break
            if foreground:
                current = (foreground['name'], tuple(foreground_match.box))
                stable = (foreground_last is not None and current[0] == foreground_last[0]
                          and all(abs(a-b) <= 12 for a,b in zip(current[1],foreground_last[1])))
                foreground_frames = foreground_frames+1 if stable else 1
                foreground_last = current
                last, streak, source_frames = None, 0, 0
                if foreground_frames >= 2:
                    self._guard_status('foreground_handoff', foreground_rule=foreground['name'],
                                       foreground_template=foreground['template'], evidence_frames=2)
                    return 'foreground_handoff', foreground
                continue  # Never retry a background target behind a known modal.
            foreground_last, foreground_frames = None, 0
            found, identity = None, None
            for index, candidate in enumerate(evidence):
                found = self.backend.recognize(image, dict(candidate,
                    name=rule['name']+' / 后继页面核验'), self.stop_event)
                if found:
                    identity = index
                    break
            if evidence:
                current = (identity, tuple(found.box)) if found else None
                stable = (current is not None and last is not None and current[0] == last[0]
                          and all(abs(a-b) <= 12 for a,b in zip(current[1],last[1])))
            else:
                remaining = self.backend.recognize(image, dict(presence,
                    name=rule['name']+' / 原目标仍存在'), self.stop_event)
                current = ('target_left', ()) if self.target_left_original_position(original, remaining) else None
                stable = current is not None and current == last
            streak = streak+1 if stable else 1 if current else 0
            last = current
            if streak >= 2:
                self._guard_status('confirmed', confirmation='successor' if evidence else 'target_left_two_frames',
                                   evidence_frames=streak)
                return 'confirmed', found
            source = self.backend.recognize(image, rule, self.stop_event)
            # Retry only if the same complete, unobscured source is still at
            # its original position in consecutive fresh frames.
            source_frames = source_frames+1 if source and not self.target_left_original_position(original,source) else 0
        self._guard_status('waiting_expired', evidence_frames=streak, source_frames=source_frames)
        return ('retryable' if source_frames >= 2 else 'unknown'), None

    def click_with_change_confirmation(self, rule, match):
        if not self.guard_enabled or not rule.get('action_guard'):
            return super().click_with_change_confirmation(rule, match)
        if self.dry_run or self.pause_event.is_set():
            return False
        # Alias templates leading to the same explicit successor share a budget.
        key = self._budget_key(self.stats.state, rule)
        sent = self._action_attempts.get(key, 0)
        limit = rule['retry_max_clicks']
        seconds = self.SUBMISSION_WAIT_SECONDS if rule['action_guard']=='single_submit' else self.NAVIGATION_WAIT_SECONDS
        self.action_guard = dict(rule=rule['name'], source_state=self.stats.state,
                                 template=rule['template'], mode=rule['action_guard'], attempts=sent,
                                 max_attempts=limit, wait_seconds=seconds)
        try:
            # An interrupted request retains its budget. Observe first on reentry.
            observe_first = sent > 0
            while True:
                if self.stop_event.is_set():
                    raise InterruptedError('点击确认已停止')
                if not observe_first:
                    if sent >= limit:
                        raise RuntimeError(f'{rule["name"]}已达到 {limit} 次提交上限，结果未确认')
                    current = self.refresh_match_before_click(rule, match)
                    if current is None:
                        self._guard_status('source_changed')
                        return False
                    if self.pause_event.is_set():
                        return False
                    self.backend.guard()
                    # Reserve before sending: an uncertain backend exception
                    # must not make a possibly-sent request eligible again.
                    sent += 1
                    self._action_attempts[key] = sent
                    self._guard_status('submitting', attempts=sent)
                    offset = rule.get('offset', [0,0])
                    x,y,w,h = current.box
                    self.record('click_attempt', rule=rule['name'], attempt=sent,
                                box=list(current.box), point=[x+w//2+offset[0], y+h//2+offset[1]])
                    self.backend.click(current, offset, self.stop_event, self.pause_event)
                    self.remember_click(rule)
                    self.stats.clicks += 1
                    self._guard_status('awaiting_confirmation')
                    match = current
                observe_first = False
                status, foreground = self._observe_action(rule, match, seconds)
                if status == 'confirmed':
                    self._action_attempts.pop(key, None)
                    self._resolve_foreground_handoffs(self.stats.state, rule['name'])
                    self.emit('log',f'{rule["name"]}：连续两帧核验通过，已提交 {sent}/{limit} 次')
                    return True
                if status == 'foreground_handoff':
                    self._foreground_handoffs[key] = dict(state=self.stats.state,
                        rule=rule['name'], handler=foreground['name'])
                    self.emit('log',f'{rule["name"]}：已确认出现 {foreground["name"]}，交回原流程处理，不重复点击楼梯')
                    return False
                if status == 'interrupted':
                    self._guard_status('interrupted_budget_retained')
                    return False
                if status != 'retryable' or sent >= limit:
                    detail = '不重复提交' if rule['action_guard']=='single_submit' else '停止重试'
                    raise RuntimeError(f'{rule["name"]}已提交 {sent}/{limit} 次，等待后结果仍未确认；{detail}，保存诊断')
                self.emit('log',f'{rule["name"]}：原页面连续两帧仍在，允许最后一次原位置重试')
        except Exception as exc:
            self._guard_status('stopped', error=str(exc))
            raise
