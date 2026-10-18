"""Bounded defeat recovery, layered over the unchanged vendor runner."""
import copy
import time

from vendor_core.backend import MaaBackend
from .recovery import RecoveryRunner as Runner

RESTART_LIMIT = 3
DEFEAT = dict(template='战败标识核心.png', threshold=.95,
              min_contrast_ratio=.75, min_brightness_ratio=.75)
ABANDON_PROMPT = dict(template='放弃冒险提示正文.png', threshold=.95,
                      min_contrast_ratio=.85, min_brightness_ratio=.85)


def add_defeat_recovery(profile):
    common = dict(threshold=.95, min_contrast_ratio=.75, min_brightness_ratio=.75,
                  requires_match=copy.deepcopy(DEFEAT), foreground=True,
                  action='click', verify_change=True, confirm_frames=2,
                  retry_center_clicks=1, retry_max_clicks=1, retry_radius=0,
                  cooldown_seconds=1)
    rules = [
        dict(common, name='战败后重新开始（最多三次）', template='战败重新开始文字.png',
             defeat_action='restart', next='Progress'),
        dict(common, name='三次重试仍失败，放弃冒险', template='战败放弃冒险文字.png',
             defeat_action='abandon', next='DefeatReturn'),
    ]
    profile['states']['Progress']['rules'][0:0] = rules
    for rule in profile['states']['Progress']['rules']:
        if (rule.get('increment_counter') == 'battles_completed'
                or rule['template'] == '战斗结束.png'):
            rule['battle_victory'] = True
    # This task completes exit recovery only, never a dungeon round.
    profile['states']['DefeatReturn'] = dict(timeout_seconds=30, rules=[
        dict(name='已放弃冒险并返回'+label, template=template, threshold=.95,
             min_contrast_ratio=.65, confirm_frames=2, action='none', event='task_complete')
        for label, template in [('地下城列表', '地下城列表通用标识.png'),
                                ('主界面', '主界面锚点.png')]
    ])
    profile['states']['DefeatReturn']['rules'].append(dict(
        common, name='确认放弃当前冒险', template='放弃冒险确认文字.png',
        requires_match=copy.deepcopy(ABANDON_PROMPT),
        defeat_action='confirm_abandon', next='DefeatReturn'))
    return profile


class BattleRunner(Runner):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.battle_restarts = 0
        self.abandon_requested = False
        self.abandon_confirmation_sent = False
        self.abandoned = False

    def run(self):
        super().run()
        if (not self.dry_run and self.completed and self.abandon_requested
                and self.battle_restarts == RESTART_LIMIT and self.stats.state == 'DefeatReturn'):
            self.abandoned = True
            self.completed = False
            self.reason = '已确认放弃冒险并返回；本轮不计通关，交回调度跳过当前副本'
            self.record('dungeon_abandoned', restarts=self.battle_restarts)
            self.emit('log', self.reason)

    def ordered_rules(self):
        return [rule for rule in super().ordered_rules()
                if (rule.get('defeat_action') != 'restart' or self.battle_restarts < RESTART_LIMIT)
                and (rule.get('defeat_action') != 'abandon' or self.battle_restarts >= RESTART_LIMIT)
                and (rule.get('defeat_action') != 'confirm_abandon'
                     or (self.abandon_requested and self.battle_restarts == RESTART_LIMIT
                         and not self.abandon_confirmation_sent))]

    def remember_click(self, rule):
        super().remember_click(rule)
        action = rule.get('defeat_action')
        if action == 'restart':
            self.battle_restarts += 1
            self.counters['battle_restart_attempts'] = self.battle_restarts
            self.record('battle_restart', attempt=self.battle_restarts, limit=RESTART_LIMIT)
            self.emit('log', f'战斗失败：已点击重新开始 {self.battle_restarts}/{RESTART_LIMIT}；等待失败画面退出')
        elif action == 'abandon':
            self.abandon_requested = True
            self.record('battle_abandon', restarts=self.battle_restarts)
            self.emit('log', '三次重新开始后仍失败：已点击放弃冒险，等待专用确认弹窗或返回页面；不计通关')
        elif action == 'confirm_abandon':
            self.abandon_confirmation_sent = True
            self.counters['abandon_confirm_requests'] = 1
            self.record('battle_abandon_confirmation', restarts=self.battle_restarts)
            self.emit('log', '已点击放弃冒险确认，等待返回副本列表或主界面；尚未计为退出成功')

    def click_with_change_confirmation(self, rule, match):
        action = rule.get('defeat_action')
        if action is None:
            confirmed = super().click_with_change_confirmation(rule, match)
            if confirmed and rule.get('battle_victory'):
                if self.battle_restarts:
                    self.emit('log', '已确认战斗胜利并继续，下一场战斗的重试次数重置为 0')
                self.battle_restarts = 0
                self.counters['battle_restart_attempts'] = 0
            return confirmed
        if self.dry_run:
            return False
        if ((action == 'restart' and (self.battle_restarts >= RESTART_LIMIT or self.abandon_requested))
                or (action == 'abandon' and (self.battle_restarts != RESTART_LIMIT or self.abandon_requested))
                or (action == 'confirm_abandon' and (not self.abandon_requested
                    or self.battle_restarts != RESTART_LIMIT or self.abandon_confirmation_sent))):
            raise RuntimeError('战败恢复次数或放弃状态不一致，停止重复输入')
        if self.pause_event.is_set():
            return False
        match = self.refresh_match_before_click(rule, match)
        if match is None:
            return False
        self.backend.guard()
        x, y, w, h = match.box
        self.record('click_attempt', rule=rule['name'], attempt=1,
                    box=list(match.box), point=[x+w//2, y+h//2])
        self.backend.click(match, [0, 0], self.stop_event, self.pause_event)
        self.remember_click(rule)
        self.stats.clicks += 1
        deadline = time.monotonic()+8
        absent_frames = 0
        prompt_frames = 0
        # One click per defeat. A late animation never submits extra restarts.
        for _ in range(20):
            if self.stop_event.wait(.3):
                raise InterruptedError('战败恢复期间停止')
            if self.pause_event.is_set():
                raise RuntimeError('战败点击后暂停，返回页面尚未确认；停止并保存诊断，避免重复重试')
            if time.monotonic() >= deadline:
                break
            if hasattr(self, '_tick'):
                self._tick()
                if self.runtime_limit_reached():
                    raise RuntimeError('战败恢复期间达到运行时限')
            self.backend.guard()
            image = self.backend.capture(self.stop_event)
            self.emit('image', image)
            if self.interrupt_visible(image, rule):
                return False  # Preserve retry/abandon budget; let the guarded recovery interrupt run.
            if action == 'abandon':
                prompt = self.backend.recognize(image, dict(
                    ABANDON_PROMPT, name='放弃冒险 / 等待专用确认弹窗'), self.stop_event)
                prompt_frames = prompt_frames+1 if prompt is not None else 0
                if prompt_frames >= 2:
                    self.record('abandon_prompt_seen', restarts=self.battle_restarts)
                    return True  # Advance to confirmation, not successful exit.
            if action == 'confirm_abandon':
                # Only a recognized destination confirms this click. A vanished
                # prompt, black frame or unrelated dialog is not an exit.
                prompt = self.backend.recognize(image, dict(
                    ABANDON_PROMPT, name='放弃冒险 / 确认后弹窗仍存在'), self.stop_event)
                returned = prompt is None and any(self.backend.recognize(image, candidate, self.stop_event)
                               for candidate in self.config['states']['DefeatReturn']['rules']
                               if candidate.get('event') == 'task_complete')
                absent_frames = absent_frames+1 if returned and not MaaBackend.is_nearly_uniform(image) else 0
                if absent_frames >= 2:
                    self.record('abandon_confirmation_returned', restarts=self.battle_restarts)
                    return True
                continue
            # Ignore contrast/brightness limits when checking disappearance:
            # a dimmed defeat screen behind a confirmation is still present.
            present = self.backend.recognize(image, dict(template=DEFEAT['template'],
                threshold=.95, name='战败恢复 / 原失败画面仍存在'), self.stop_event)
            absent_frames = (absent_frames+1 if present is None
                             and not MaaBackend.is_nearly_uniform(image) else 0)
            if absent_frames >= 2:
                self.record('defeat_screen_left', action=action, restarts=self.battle_restarts)
                return True
        expected = {'confirm_abandon': '返回副本列表或主界面',
                    'abandon': '失败画面退出或放弃确认弹窗出现',
                    'restart': '失败画面退出'}[action]
        raise RuntimeError(f'{rule["name"]}点击一次后，未连续两帧确认{expected}；停止并保存诊断，不重复点击')
