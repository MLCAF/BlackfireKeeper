"""Interruptible, bounded state machine over MaaFramework recognition and input."""

from __future__ import annotations

import threading
import time
import math
from collections import deque
from dataclasses import dataclass, asdict
from .watchdog import ProgressWatchdog


@dataclass
class Stats:
    state: str = ""
    elapsed: float = 0
    active: float = 0
    rounds: int = 0
    clicks: int = 0
    last_round: float = 0
    average_round: float = 0
    round_elapsed: float = 0
    mode: str = "观察"
    idle_seconds: float = 0


class Runner:
    def __init__(self, backend, config, emit, dry_run=True, clock=time.monotonic):
        self.backend, self.config, self.emit = backend, config, emit
        self.dry_run, self.clock = dry_run, clock
        self.stop_event = threading.Event()
        self.pause_event = threading.Event()
        self.stats = Stats(state=config["initial_state"], mode="观察" if dry_run else "点击")
        self.reason = ""
        self.completed = False
        self.manual_handoff = False
        self.deferred = False
        self.cooldown_estimate = None
        self.swipes = 0
        self.counters = {}
        self.last_round_counters = {}
        self.interrupt_counts = {}
        self.history = deque(maxlen=60)
        self.watchdog = ProgressWatchdog(config['watchdog']) if config.get('watchdog') else None
        self.pending_action = None

    def remember_click(self, rule):
        """Auxiliary reward clicks must not replace an unfinished navigation."""
        if rule in self.config.get('interrupts', []):
            return
        if rule.get('preserve_pending', False):
            if self.pending_action and self.pending_action[0] == self.stats.state:
                self.record('pending_preserved', rule=rule['name'], pending=self.pending_action[1]['name'])
            return
        self.pending_action = (self.stats.state, rule)

    def read_cooldown_before_close(self, rule):
        """Two fresh guarded frames. OCR failure falls back; scene changes re-enter discovery."""
        from .cooldown import confirm_countdown
        self.cooldown_estimate = None
        reader = getattr(self.backend, 'read_cooldown', None)
        if not callable(reader):
            return True
        samples = []
        for index in range(2):
            if self.stop_event.is_set():
                raise InterruptedError('冷却识别已停止')
            if self.pause_event.is_set():
                return False
            if index and self.stop_event.wait(.3):
                raise InterruptedError('冷却识别已停止')
            image = self.backend.capture(self.stop_event)
            captured_at = self.clock()
            self.emit('image', image)
            if self.interrupt_visible(image, rule) or not self.backend.recognize(image, rule, self.stop_event):
                self.emit('log', '冷却详情已变化，重新识别，不使用旧倒计时或旧点击坐标')
                return False
            try:
                sample = reader(image, self.stop_event, captured_at)
            except InterruptedError:
                raise
            except Exception as exc:
                self.emit('log', f'冷却 OCR 暂不可用：{exc}；本次采用保守等待')
                return True
            samples.append(sample)
        estimate = confirm_countdown(*samples)
        self.cooldown_estimate = estimate
        if estimate:
            self.record('cooldown_ocr', text=estimate.text, score=estimate.score,
                        remaining_seconds=round(max(0, estimate.deadline - self.clock()), 2))
            self.emit('log', f'冷却 OCR 连续两帧校验通过：{estimate.text}，置信分数 {estimate.score:.3f}；按读数加 3 秒缓冲调度')
        else:
            self.record('cooldown_ocr_rejected')
            self.emit('log', '冷却 OCR 不完整、置信度不足或两帧不一致；本次采用保守等待')
        return True

    def save_checkpoint(self, reason):
        save = getattr(self.backend, 'save_diagnostic', None)
        if save:
            try:
                self.backend.diagnostic_context = {
                    'history': list(self.history), 'interrupt_counts': self.interrupt_counts,
                    'counters': self.counters, 'rounds': self.stats.rounds,
                    'idle_seconds': self.stats.idle_seconds,
                    'pending_action': self.pending_action[1]['name'] if self.pending_action else None}
                path = save(self.stats.state, reason, self.config)
                self.emit('log', f'诊断已保存：{path}')
            except Exception as exc:
                self.emit('log', f'保存诊断失败：{exc}')

    def pending_success(self, image):
        if self.pending_action is None:
            return None
        state, rule = self.pending_action
        if state != self.stats.state or not rule.get('success_match'):
            return None
        evidence = self.backend.recognize(image, dict(rule['success_match'], name=rule['name'] + ' / 延迟后继校验'), self.stop_event)
        if evidence is None:
            return None
        recovered = dict(rule, action='none', confirm_frames=2, name=rule['name'] + ' / 延迟完成确认')
        self.record('pending_evidence', rule=rule['name'], template=rule['success_match']['template'])
        return recovered, evidence

    def record(self, event, **details):
        self.history.append(dict(event=event, state=self.stats.state,
                                 elapsed=round(self.stats.elapsed, 3), **details))

    def ordered_rules(self):
        rules = self.config["states"][self.stats.state]["rules"]
        return self.config.get("interrupts", []) + sorted(rules, key=lambda rule: not rule.get("foreground", False))

    def interrupt_visible(self, image, rule):
        for candidate in self.config.get("interrupts", []):
            if candidate["name"] == rule["name"]:
                break
            if self.backend.recognize(image, candidate, self.stop_event):
                self.record("interrupt", rule=rule["name"], interrupt=candidate["name"])
                self.emit("log", f"{rule['name']}：检测到 {candidate['name']}，先处理提示，不推进原状态")
                return True
        return False

    def runtime_limit_reached(self):
        """Zero explicitly disables only the overall elapsed-time limit."""
        minutes = self.config.get("max_minutes", 30)
        return minutes > 0 and self.stats.elapsed >= minutes * 60

    def higher_priority_visible(self, image, rule, foreground_only=False):
        for candidate in self.ordered_rules():
            if candidate["name"] == rule["name"]:
                break
            if foreground_only and not candidate.get("foreground", False):
                continue
            if self.backend.recognize(image, candidate, self.stop_event):
                self.emit("log", f"{rule['name']}：出现更高优先级目标 {candidate['name']}，返回识别循环")
                return True
        return False

    def stop(self, reason="用户停止"):
        self.reason = reason
        self.stop_event.set()
        self.backend.cancel()

    def pause(self):
        self.pause_event.set()

    def resume(self):
        self.pause_event.clear()

    @staticmethod
    def target_left_original_position(before_match, after_match):
        """Ignore ambient animation: success requires the clicked target to vanish or move."""
        if after_match is None:
            return True
        before_x, before_y, before_w, before_h = before_match.box
        after_x, after_y, after_w, after_h = after_match.box
        before_center = (before_x + before_w / 2, before_y + before_h / 2)
        after_center = (after_x + after_w / 2, after_y + after_h / 2)
        tolerance = max(12, min(before_w, before_h) * 0.25)
        return (
            abs(before_center[0] - after_center[0]) > tolerance
            or abs(before_center[1] - after_center[1]) > tolerance
        )

    @staticmethod
    def retry_offsets(rule):
        """Two exact-center clicks, followed by deterministic points on a circle."""
        base = tuple(rule.get("offset", [0, 0]))
        center_clicks = int(rule.get("retry_center_clicks", 2))
        max_clicks = int(rule.get("retry_max_clicks", 8))
        radius = int(rule.get("retry_radius", 18))
        offsets = [base] * center_clicks
        remaining = max_clicks - center_clicks
        for index in range(remaining):
            angle = 2 * math.pi * index / max(remaining, 1)
            offsets.append((base[0] + round(radius * math.cos(angle)), base[1] + round(radius * math.sin(angle))))
        return offsets

    def click_with_change_confirmation(self, rule, match):
        """Bounded click retry. It never continues indefinitely on an unchanged page."""
        current_match = match
        offsets = self.retry_offsets(rule)
        wait_seconds = rule.get("retry_wait_seconds", 0.6)
        for attempt, offset in enumerate(offsets, start=1):
            if self.pause_event.is_set():
                return False
            current_match = self.refresh_match_before_click(rule, current_match)
            if current_match is None:
                return False
            if hasattr(self, "_tick"):
                self._tick()
                if self.runtime_limit_reached():
                    raise RuntimeError("点击重试期间达到运行时限")
            try:
                x, y, w, h = current_match.box
                self.record("click_attempt", rule=rule["name"], attempt=attempt,
                            box=list(current_match.box), point=[x + w // 2 + offset[0], y + h // 2 + offset[1]])
                self.backend.click(current_match, offset, self.stop_event, self.pause_event)
                self.remember_click(rule)
            except InterruptedError:
                if self.pause_event.is_set() and not self.stop_event.is_set():
                    return False
                raise
            self.stats.clicks += 1
            if self.stop_event.wait(wait_seconds):
                raise InterruptedError()
            after = self.backend.capture(self.stop_event)
            self.emit("image", after)
            if self.interrupt_visible(after, rule):
                return False
            success = rule.get("success_match")
            if success is not None:
                evidence = self.backend.recognize(after, dict(success, name=f"{rule['name']} / 后继页面"), self.stop_event)
                if evidence is not None:
                    # Require a second fresh frame, not a transient highlight/miss.
                    if self.stop_event.wait(wait_seconds):
                        raise InterruptedError()
                    after = self.backend.capture(self.stop_event)
                    self.emit("image", after)
                    if self.pause_event.is_set() or self.interrupt_visible(after, rule):
                        return False
                    if self.backend.recognize(after, dict(success, name=f"{rule['name']} / 后继页面"), self.stop_event):
                        self.record("success_evidence", rule=rule["name"], template=success["template"])
                        self.emit("log", f"{rule['name']}：连续两帧确认后继页面 {success['template']}")
                        return True
                    return False
            # A dimmed button is still present underneath a modal. Dimming alone
            # must not count as successful navigation.
            presence_rule = dict(rule)
            presence_rule.pop("min_brightness_ratio", None)
            presence_rule.pop("requires_match", None)
            presence_rule.pop("min_contrast_ratio", None)
            same_match = self.backend.recognize(after, presence_rule, self.stop_event)
            if self.target_left_original_position(match, same_match):
                if success is not None:
                    self.emit("log", f"{rule['name']}：旧目标消失但后继页面尚未确认，返回识别循环")
                    return False
                self.emit("log", f"{rule['name']}：第 {attempt} 次点击后原按钮已消失或移位")
                return True
            if self.higher_priority_visible(after, rule):
                return False
            if "requires_match" in rule and not self.backend.recognize(after, rule, self.stop_event):
                self.emit("log", f"{rule['name']}：附加画面条件已失效，取消后续点击并重新识别")
                return False
            if "min_brightness_ratio" in rule and not self.backend.recognize(after, rule, self.stop_event):
                self.emit("log", f"{rule['name']}：按钮已变暗，取消后续点击并重新识别")
                return False
            if "min_contrast_ratio" in rule and not self.backend.recognize(after, rule, self.stop_event):
                self.emit("log", f"{rule['name']}：目标疑似被遮罩覆盖，取消后续点击")
                return False
            current_match = same_match
        raise RuntimeError(f"{rule['name']} 连续 {len(offsets)} 次点击后页面未出现可检测变化，停止而非无限重试")

    def refresh_match_before_click(self, rule, match):
        """Refresh stale discovery with foreground blockers plus a local target check.

        Ordinary scene alternatives are ranked by the main discovery loop; they
        need not be scanned again at the click boundary. Foreground priority,
        target position/brightness and the backend's age limit remain enforced.
        """
        fresh_check = getattr(self.backend, "capture_is_fresh", None)
        if fresh_check is None:
            return match
        if fresh_check():
            return match
        for _ in range(2):
            self.emit("log", f"{rule['name']}：识别截图接近过期，正在重新截图复核目标")
            started = time.monotonic()
            image = self.backend.capture(self.stop_event)
            self.emit("image", image)
            if self.higher_priority_visible(image, rule, foreground_only=True):
                return None
            local_check = getattr(self.backend, "recognize_near", None)
            refreshed = (local_check(image, rule, match, self.stop_event) if local_check
                         else self.backend.recognize(image, rule, self.stop_event))
            if refreshed is None:
                self.emit("log", f"{rule['name']}：新截图中目标已消失，取消本次旧坐标点击")
                return None
            if self.target_left_original_position(match, refreshed):
                self.emit("log", f"{rule['name']}：新截图中目标位置已变化，返回识别循环")
                return None
            match = refreshed
            # Check BOTH completed attempts, including the final permitted one.
            if fresh_check():
                self.emit("log", f"{rule['name']}：前景检查及目标局部复核通过，耗时 {time.monotonic() - started:.2f} 秒")
                return match
        if self.watchdog:
            self.record('refresh_deferred', rule=rule['name'])
            self.emit('log', f"{rule['name']}：本次复核耗时过长，取消点击并重新识别；仍保留过期截图保护")
            self.stop_event.wait(.5)
            return None
        raise RuntimeError(f"{rule['name']} 前景检查及目标局部复核连续两次超时，不执行旧坐标点击")

    def run(self):
        started = last = self.clock()
        state_since = 0.0
        round_started = None
        total_rounds = 0.0
        streak_name, streak = None, 0
        last_box = None
        cooldown_until = 0.0
        blocked_rules = {}
        def tick():
            nonlocal last
            now = self.clock()
            delta, last = now - last, now
            self.stats.elapsed = now - started
            if not self.pause_event.is_set():
                self.stats.active += delta
            self.stats.round_elapsed = 0 if round_started is None else self.stats.active - round_started
            if self.watchdog:
                self.stats.idle_seconds = self.watchdog.idle(self.stats.active)
            return now

        self.emit("log", "开始观察（绝不点击、不推进状态、不计轮数）" if self.dry_run else "开始实际执行")
        minutes = self.config.get("max_minutes", 30)
        self.emit("log", "总运行时限：不限制；状态超时和点击重试保护仍生效" if minutes == 0
                  else f"总运行时限：{minutes:g} 分钟（含暂停时间）")
        if self.watchdog and not self.dry_run:
            self.emit('log', f"无进展保护：{self.watchdog.settings['warn_seconds']:g} 秒预警，"
                      f"{self.watchdog.settings['stop_seconds']:g} 秒停止；暂停不计入，非总运行时限")
        self._tick = tick
        try:
            while not self.stop_event.is_set():
                now = tick()
                self.emit("stats", asdict(self.stats))
                if self.runtime_limit_reached():
                    self.reason = "达到运行时限（含暂停时间）"
                    break
                if self.pause_event.is_set():
                    streak_name, streak = None, 0
                    self.stop_event.wait(0.05)
                    continue
                self.backend.guard()
                state = self.config["states"][self.stats.state]
                if self.stats.active - state_since >= state.get("timeout_seconds", 120):
                    self.reason = f"状态 {self.stats.state} 等待超时，停止而非盲点"
                    break
                if now < cooldown_until:
                    self.stop_event.wait(min(0.1, cooldown_until - now))
                    continue
                image = self.backend.capture(self.stop_event)
                self.emit("image", image)
                if self.watchdog and not self.dry_run:
                    was_warned = self.watchdog.last_warning is not None
                    self.watchdog.observe(image, self.stats.active)
                    if was_warned and self.watchdog.last_warning is None:
                        message = '检测到明显画面变化，已解除无进展预警，继续识别'
                        self.emit('log', message)
                        self.emit('watchdog', message)
                    self.stats.idle_seconds = self.watchdog.idle(self.stats.active)
                    health = self.watchdog.status(self.stats.active)
                    if health == 'stop':
                        raise RuntimeError(f"状态 {self.stats.state} 连续 {self.stats.idle_seconds:.0f} 秒无可确认进展，停止并保存诊断")
                    if health == 'warning':
                        message = f"无进展预警：状态 {self.stats.state} 已停留 {self.stats.idle_seconds:.0f} 秒；继续复核已知弹窗和恢复规则，不盲点"
                        self.emit('log', message)
                        self.emit('watchdog', message)
                        self.record('watchdog_warning', idle_seconds=self.stats.idle_seconds)
                        if not self.watchdog.snapshot_saved:
                            self.save_checkpoint(message)
                            self.watchdog.snapshot_saved = True
                # In a flexible state, a button may remain visible while its click
                # animation is still pending. Do not submit a second click until
                # that exact template has disappeared at least once.
                for name, blocked_rule in list(blocked_rules.items()):
                    if not self.backend.recognize(image, blocked_rule, self.stop_event):
                        blocked_rules.pop(name)
                hit = None
                rules = self.ordered_rules()
                interrupts = self.config.get('interrupts', [])
                for rule in interrupts:
                    match = self.backend.recognize(image, rule, self.stop_event)
                    if match:
                        hit = (rule, match)
                        break
                if hit is None and not self.dry_run:
                    hit = self.pending_success(image)
                for rule in ([] if hit else [r for r in rules if r not in interrupts]):
                    if self.stop_event.is_set() or self.pause_event.is_set():
                        break
                    if rule["name"] in blocked_rules:
                        continue
                    if self.stats.idle_seconds < rule.get('after_idle_seconds', 0):
                        continue
                    match = self.backend.recognize(image, rule, self.stop_event)
                    if match:
                        hit = (rule, match)
                        break
                if hit is None:
                    streak_name, streak = None, 0
                    last_box = None
                else:
                    rule, match = hit
                    stable = last_box is not None and all(abs(a - b) <= 12 for a, b in zip(last_box, match.box))
                    streak = streak + 1 if streak_name == rule["name"] and stable else 1
                    streak_name = rule["name"]
                    last_box = match.box
                    if streak >= rule.get("confirm_frames", 2):
                        self.emit("match", {"name": rule["name"], "box": list(match.box), "score": match.score})
                        streak_name, streak = None, 0
                        if self.stop_event.is_set() or self.pause_event.is_set():
                            continue
                        tick()
                        if self.runtime_limit_reached():
                            self.reason = "达到运行时限"
                            break
                        if self.stats.active - state_since >= state.get("timeout_seconds", 120):
                            self.reason = f"状态 {self.stats.state} 等待超时，停止而非盲点"
                            break
                        if not self.dry_run:
                            action = rule.get("action", "none")
                            if rule in self.config.get("interrupts", []) and action == 'click':
                                count = self.interrupt_counts.get(rule["name"], 0)
                                if count >= rule["max_activations"]:
                                    raise RuntimeError(f"{rule['name']} 本次运行已处理 {count} 次，停止重复恢复")
                                # Count attempts too: a failed acknowledgement must not reset the budget.
                                self.interrupt_counts[rule["name"]] = count + 1
                                self.record("interrupt_attempt", rule=rule["name"], count=count + 1)
                            if action == "stop":
                                self.reason = f"命中停止规则：{rule['name']}；{rule.get('stop_message', '停止操作，请检查当前画面')}"
                                break
                            event = rule.get("event", "none")
                            if event == 'diamond_timer':
                                from .diamonds import read_free_pack_timer
                                if not read_free_pack_timer(self, rule):
                                    continue
                            if action == 'swipe':
                                if self.swipes >= 8:
                                    raise RuntimeError('列表拖动达到 8 次仍未找到副本，停止并保存诊断')
                                match = self.refresh_match_before_click(rule, match)
                                if match is None:
                                    continue
                                self.backend.swipe(rule['direction'], self.stop_event, self.pause_event)
                                self.swipes += 1
                                self.record('swipe', direction=rule['direction'], count=self.swipes)
                                self.emit('log', f"列表拖动 {self.swipes}/8：{rule['direction']}，重新识别副本名称")
                                self.stop_event.wait(1)
                                continue
                            required = rule.get("requires_counter")
                            if required is not None:
                                required_at_least = rule["requires_at_least"]
                                current = self.counters.get(required, 0)
                                if current < required_at_least:
                                    self.reason = (
                                        f"{rule['name']} 前需要 {required} 至少 {required_at_least} 次，"
                                        f"当前仅 {current} 次；停止而非跳过"
                                    )
                                    break
                            if event == "round_start" and round_started is not None:
                                raise RuntimeError("重复 round_start，请检查流程")
                            if event == "round_end" and round_started is None:
                                raise RuntimeError("没有 round_start，不能计入完成轮数")
                            if action == "click":
                                if event == 'dungeon_deferred' and not self.read_cooldown_before_close(rule):
                                    continue
                                if rule.get("verify_change", False):
                                    if not self.click_with_change_confirmation(rule, match):
                                        continue
                                else:
                                    match = self.refresh_match_before_click(rule, match)
                                    if match is None:
                                        continue
                                    try:
                                        x, y, w, h = match.box
                                        dx, dy = rule.get("offset", [0, 0])
                                        self.record("click_attempt", rule=rule["name"], attempt=1,
                                                    box=list(match.box), point=[x + w // 2 + dx, y + h // 2 + dy])
                                        self.backend.click(match, rule.get("offset", [0, 0]), self.stop_event, self.pause_event)
                                        self.remember_click(rule)
                                    except InterruptedError:
                                        if self.pause_event.is_set() and not self.stop_event.is_set():
                                            continue
                                        raise
                                    self.stats.clicks += 1
                                tick()
                            if event == 'manual_handoff':
                                self.manual_handoff = True
                                self.reason = '已发送钻石入口点击；尚未验证领取成功，需要人工接续'
                                break
                            if event == 'dungeon_deferred':
                                if round_started is not None:
                                    raise RuntimeError('副本已开始，不能作为入口冷却延后')
                                self.deferred = True
                                self.reason = '游戏显示副本仍在冷却，已确认关闭详情并返回列表；交回调度，不计完成'
                                self.record('dungeon_deferred', rule=rule['name'])
                                break
                            if event == "round_start":
                                round_started = self.stats.active
                                self.counters.clear()
                            elif event == "round_end":
                                self.stats.last_round = self.stats.active - round_started
                                total_rounds += self.stats.last_round
                                self.stats.rounds += 1
                                self.stats.average_round = total_rounds / self.stats.rounds
                                round_started = None
                                self.last_round_counters = dict(self.counters)
                                self.counters.clear()
                            elif event == "task_complete":
                                self.completed = True
                                self.reason = f"任务完成：{rule['name']}"
                                break
                            increment = rule.get("increment_counter")
                            if increment is not None:
                                self.counters[increment] = self.counters.get(increment, 0) + 1
                                self.emit("log", f"本轮计数：{increment} = {self.counters[increment]}")
                            previous_state = self.stats.state
                            next_state = rule.get("next", previous_state)
                            self.stats.state = next_state
                            if next_state != previous_state:
                                self.record("transition", previous=previous_state, next=next_state, rule=rule["name"])
                                self.emit("log", f"状态转换：{previous_state} → {next_state}")
                            if action == "click" and next_state == previous_state and not rule.get("verify_change", False):
                                blocked_rules[rule["name"]] = rule
                            elif next_state != previous_state:
                                blocked_rules.clear()
                            if rule not in self.config.get("interrupts", []):
                                state_since = self.stats.active
                                if not rule.get('preserve_pending', False) or next_state != previous_state:
                                    self.pending_action = None
                                if self.watchdog:
                                    was_warned = self.watchdog.last_warning is not None
                                    self.watchdog.confirmed((previous_state, rule['name'], next_state), self.stats.active)
                                    if was_warned and self.watchdog.last_warning is None:
                                        message = f"已恢复处理：{rule['name']}，继续运行"
                                        self.emit('log', message)
                                        self.emit('watchdog', message)
                            limit = self.config.get("max_rounds", 0)
                            if limit and self.stats.rounds >= limit:
                                self.reason = "达到完成轮数上限"
                                break
                        cooldown_until = self.clock() + rule.get("cooldown_seconds", 1)
                self.stop_event.wait(self.config.get("poll_seconds", 0.5))
        except InterruptedError:
            self.reason = self.reason or "操作已中止"
        except Exception as exc:
            self.reason = f"错误停止：{exc}"
            self.emit("error", self.reason)
        finally:
            self.stop_event.set()
            tick()
            if "错误停止" in self.reason or "超时" in self.reason or '命中停止规则' in self.reason:
                save = getattr(self.backend, "save_diagnostic", None)
                if save:
                    try:
                        self.backend.diagnostic_context = {"history": list(self.history),
                                                           "interrupt_counts": self.interrupt_counts,
                                                           "counters": self.counters,
                                                           "rounds": self.stats.rounds,
                                                           "idle_seconds": self.stats.idle_seconds,
                                                           "pending_action": self.pending_action[1]['name'] if self.pending_action else None}
                        path = save(self.stats.state, self.reason, self.config)
                        self.emit("log", f"失败诊断已保存：{path}")
                    except Exception as exc:
                        self.emit("log", f"保存诊断失败：{exc}")
            try:
                stop_job = self.backend.cancel()
                if stop_job is not None:
                    deadline = time.monotonic() + 3
                    while not stop_job.done and time.monotonic() < deadline:
                        time.sleep(0.02)
            except Exception as exc:
                self.emit("error", f"停止清理失败：{exc}")
            self.emit("stats", asdict(self.stats))
            self.emit("finished", self.reason or "已停止")
