"""Strict validation. Config is data, never executable Python or shell commands."""

from __future__ import annotations

import json
import math
from pathlib import Path


def number(value, label, minimum, maximum):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} 必须是数字")
    if not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError(f"{label} 必须在 {minimum} 到 {maximum} 之间")
    return value


def template_path(root: Path, name: str) -> Path:
    if not isinstance(name, str) or not name or "\\" in name:
        raise ValueError("模板名必须为相对 PNG 路径，使用 / 分隔目录")
    candidate = (root / name).resolve()
    if not candidate.is_relative_to(root.resolve()) or candidate.suffix.lower() != ".png":
        raise ValueError("模板必须是 assets/image 内的 PNG 文件")
    return candidate


def validate_condition(condition, condition_name, image_root, check_files, missing, depth=0):
    # At most one nested context marker. Reject actions, cycles and arbitrary depth.
    if depth > 1:
        raise ValueError('画面附加校验最多两层')
    allowed = {"template", "threshold", "roi", "min_brightness_ratio", "min_contrast_ratio", "requires_match"}
    if not isinstance(condition, dict) or set(condition) - allowed:
        raise ValueError(f"{condition_name} 只允许模板、阈值、ROI 和亮度/对比度保护")
    condition_path = template_path(image_root, condition.get("template"))
    if check_files and not condition_path.is_file():
        missing.append(condition["template"])
    number(condition.get("threshold", 0.9), "requires_match.threshold", 0.5, 1)
    if "min_brightness_ratio" in condition:
        number(condition["min_brightness_ratio"], "requires_match.min_brightness_ratio", 0.1, 1)
    if "min_contrast_ratio" in condition:
        number(condition["min_contrast_ratio"], f"{condition_name}.min_contrast_ratio", 0.1, 1)
    if "roi" in condition:
        roi = condition["roi"]
        if not isinstance(roi, list) or len(roi) != 4:
            raise ValueError("requires_match.roi 格式为 [x, y, width, height]")
        for i, value in enumerate(roi):
            number(value, "requires_match.roi", 0 if i < 2 else 1, 20000)
            if int(value) != value:
                raise ValueError("requires_match.roi 必须为整数")
    if 'requires_match' in condition:
        validate_condition(condition['requires_match'], 'requires_match', image_root,
                           check_files, missing, depth+1)


def validate(data: dict, image_root: Path, check_files=False) -> dict:
    if not isinstance(data, dict) or data.get("version") != 1:
        raise ValueError("配置 version 必须为 1")
    states = data.get("states")
    if not isinstance(states, dict) or not states or data.get("initial_state") not in states:
        raise ValueError("必须提供 states 和有效的 initial_state")
    number(data.get("poll_seconds", 0.5), "poll_seconds", 0.1, 60)
    minutes = number(data.get("max_minutes", 30), "max_minutes", 0, 1440)
    if 0 < minutes < 0.01:
        raise ValueError("max_minutes 为 0 表示不限总时长，否则必须在 0.01 到 1440 之间")
    rounds = number(data.get("max_rounds", 0), "max_rounds", 0, 100000)
    if int(rounds) != rounds:
        raise ValueError("max_rounds 必须是整数，0 表示不限轮数")
    missing = []
    watchdog = data.get('watchdog')
    if watchdog is not None:
        if not isinstance(watchdog, dict):
            raise ValueError('watchdog 必须为对象')
        warn = number(watchdog.get('warn_seconds'), 'warn_seconds', 1, 3600)
        stop = number(watchdog.get('stop_seconds'), 'stop_seconds', 2, 86400)
        number(watchdog.get('repeat_warning_seconds', 30), 'repeat_warning_seconds', 1, 3600)
        if stop <= warn:
            raise ValueError('stop_seconds 必须大于 warn_seconds')
    interrupts = data.get("interrupts", [])
    if not isinstance(interrupts, list):
        raise ValueError("interrupts 必须为规则列表")
    if interrupts:
        for rule in interrupts:
            if (not isinstance(rule, dict) or not rule.get("foreground")
                    or rule.get("action") not in {'click', 'stop'} or "next" in rule
                    or rule.get("event", "none") != "none" or "increment_counter" in rule):
                raise ValueError("interrupts 只能是保持当前状态、不计轮数的前景点击或停止规则")
            if rule.get('action') == 'click' or 'max_activations' in rule:
                limit = number(rule.get("max_activations"), "max_activations", 1, 10)
                if int(limit) != limit:
                    raise ValueError("max_activations 必须为整数")
        validate({"version": 1, "initial_state": "Interrupt", "states": {
            "Interrupt": {"rules": interrupts}}}, image_root, check_files)
    for state_name, state in states.items():
        if not isinstance(state_name, str) or not state_name or not isinstance(state, dict):
            raise ValueError("状态名必须为非空字符串，状态必须为对象")
        number(state.get("timeout_seconds", 120), f"{state_name}.timeout_seconds", 1, 86400)
        rules = state.get("rules")
        if not isinstance(rules, list) or not rules:
            raise ValueError(f"{state_name} 至少需要一条规则")
        names = {rule['name'] for rule in interrupts}
        for rule in rules:
            if not isinstance(rule, dict):
                raise ValueError("规则必须为对象")
            name = rule.get("name")
            if not isinstance(name, str) or not name or name in names:
                raise ValueError(f"{state_name} 规则名不能为空或重复")
            names.add(name)
            path = template_path(image_root, rule.get("template"))
            if check_files and not path.is_file():
                missing.append(rule["template"])
            number(rule.get("threshold", 0.9), "threshold", 0.5, 1)
            for condition_name in ("requires_match", "success_match"):
                if condition_name not in rule:
                    continue
                validate_condition(rule[condition_name], condition_name, image_root, check_files, missing)
            if not isinstance(rule.get("foreground", False), bool):
                raise ValueError("foreground 必须为布尔值")
            if not isinstance(rule.get('preserve_pending', False), bool):
                raise ValueError('preserve_pending 必须为布尔值')
            if rule.get('preserve_pending', False):
                if (not rule.get('foreground') or rule.get('action') != 'click'
                        or not rule.get('verify_change') or rule.get('next', state_name) != state_name
                        or rule.get('event', 'none') != 'none' or 'increment_counter' in rule
                        or 'success_match' in rule):
                    raise ValueError('preserve_pending 仅允许不转移状态、不计数的前景奖励确认点击')
            if "min_brightness_ratio" in rule:
                number(rule["min_brightness_ratio"], "min_brightness_ratio", 0.1, 1)
            if "min_contrast_ratio" in rule:
                number(rule["min_contrast_ratio"], "min_contrast_ratio", 0.1, 1)
            frames = number(rule.get("confirm_frames", 2), "confirm_frames", 1, 20)
            if int(frames) != frames:
                raise ValueError("confirm_frames 必须为整数")
            number(rule.get("cooldown_seconds", 1), "cooldown_seconds", 0.1, 600)
            if 'after_idle_seconds' in rule:
                number(rule['after_idle_seconds'], 'after_idle_seconds', 0, 3600)
                if not watchdog:
                    raise ValueError('after_idle_seconds 需要启用 watchdog')
            if 'stop_message' in rule and (not isinstance(rule['stop_message'], str) or not rule['stop_message']):
                raise ValueError('stop_message 必须是非空文本')
            if rule.get("action", "none") not in {"click", "none", "stop", "swipe"}:
                raise ValueError("action 只支持 click / none / stop / swipe")
            if rule.get('action') == 'swipe':
                if (rule.get('direction') not in {'left', 'right'} or 'requires_match' not in rule
                        or rule.get('event', 'none') != 'none' or 'next' in rule):
                    raise ValueError('列表拖动必须限定方向、附加页面校验，且不能切状态或计数')
            verify_change = rule.get("verify_change", False)
            if not isinstance(verify_change, bool):
                raise ValueError("verify_change 必须为布尔值")
            if "success_match" in rule and not verify_change:
                raise ValueError("success_match 必须与 verify_change=true 一起使用")
            retry_fields = {"retry_center_clicks", "retry_max_clicks", "retry_radius", "retry_wait_seconds", "transition_wait_seconds"}
            if retry_fields & rule.keys() and not verify_change:
                raise ValueError("点击重试参数必须与 verify_change=true 一起使用")
            if verify_change:
                if rule.get("action", "none") != "click":
                    raise ValueError("verify_change 只能用于 click 动作")
                center_clicks = number(rule.get("retry_center_clicks", 2), "retry_center_clicks", 1, 10)
                max_clicks = number(rule.get("retry_max_clicks", 8), "retry_max_clicks", 1, 12)
                radius = number(rule.get("retry_radius", 18), "retry_radius", 0, 100)
                number(rule.get("retry_wait_seconds", 0.6), "retry_wait_seconds", 0.1, 5)
                number(rule.get("transition_wait_seconds", 0), "transition_wait_seconds", 0, 8)
                if int(center_clicks) != center_clicks or int(max_clicks) != max_clicks or int(radius) != radius:
                    raise ValueError("点击重试次数和半径必须是整数")
                if center_clicks > max_clicks:
                    raise ValueError("retry_center_clicks 不能大于 retry_max_clicks")
            if rule.get('event') == 'diamond_timer' and (
                    data.get('profile', {}).get('id') != 'diamonds'
                    or rule.get('action') != 'none' or rule.get('template') != '钻石免费包标识.png'
                    or rule.get('min_contrast_ratio', 0) < .65
                    or rule.get('min_brightness_ratio', 0) < .65
                    or rule.get('threshold', 0) < .95 or rule.get('next') != 'CloseShop'):
                raise ValueError('钻石计时只允许对无遮挡的免费钻石包进行无点击读取，随后关闭商店')
            if rule.get("event", "none") not in {"none", "round_start", "round_end", "task_complete", "manual_handoff", "dungeon_deferred", "diamond_timer"}:
                raise ValueError("event 只支持 none / round_start / round_end / task_complete / manual_handoff / dungeon_deferred")
            if rule.get('event') == 'dungeon_deferred' and (
                    rule.get('action') != 'click' or not rule.get('verify_change')
                    or not rule.get('requires_match') or not rule.get('success_match')
                    or 'next' in rule or 'increment_counter' in rule):
                raise ValueError('副本延后必须有冷却识别和关闭后的正向确认，不能计数或跳转')
            if rule.get('event') == 'manual_handoff' and (
                    rule.get('action') != 'click' or data.get('profile', {}).get('kind') != 'one_shot'
                    or 'increment_counter' in rule or 'next' in rule):
                raise ValueError('人工交接只能用于单次入口点击，不能计数或跳转')
            increment = rule.get("increment_counter")
            if increment is not None and (not isinstance(increment, str) or not increment or len(increment) > 64):
                raise ValueError("increment_counter 必须是 1 到 64 个字符的非空字符串")
            required = rule.get("requires_counter")
            required_at_least = rule.get("requires_at_least")
            if required is not None and (not isinstance(required, str) or not required or len(required) > 64):
                raise ValueError("requires_counter 必须是 1 到 64 个字符的非空字符串")
            if required_at_least is not None:
                if required is None:
                    raise ValueError("requires_at_least 必须与 requires_counter 一起使用")
                minimum_count = number(required_at_least, "requires_at_least", 1, 100000)
                if int(minimum_count) != minimum_count:
                    raise ValueError("requires_at_least 必须是整数")
            elif required is not None:
                raise ValueError("requires_counter 必须与 requires_at_least 一起使用")
            if rule.get("next", state_name) not in states:
                raise ValueError(f"{name} 的 next 状态不存在")
            if "roi" in rule:
                roi = rule["roi"]
                if not isinstance(roi, list) or len(roi) != 4:
                    raise ValueError("roi 格式为 [x, y, width, height]")
                for i, val in enumerate(roi):
                    number(val, "roi", 0 if i < 2 else 1, 20000)
                    if int(val) != val:
                        raise ValueError("roi 必须为整数")
            if "offset" in rule:
                offset = rule["offset"]
                if not isinstance(offset, list) or len(offset) != 2:
                    raise ValueError("offset 格式为 [dx, dy]")
                for val in offset:
                    number(val, "offset", -20000, 20000)
                    if int(val) != val:
                        raise ValueError("offset 必须为整数")
    if missing:
        raise ValueError("缺少真实游戏模板：" + "、".join(sorted(set(missing))))
    return data


def validate_dungeon_plan(data: dict, profiles_root: Path, image_root: Path, check_files=False) -> dict:
    """Validate the dungeon rotation plan without accepting executable actions."""
    if isinstance(data, dict) and data.get('version') == 2:
        from .unified import validate_plan
        return validate_plan(data, profiles_root, image_root, check_files)
    if not isinstance(data, dict) or data.get("version") != 1 or data.get("scheduler") != "dungeon_rotation":
        raise ValueError("地下城计划必须使用 version=1 和 scheduler=dungeon_rotation")
    total = number(data.get("max_total_runs", 1), "max_total_runs", 1, 100000)
    if int(total) != total:
        raise ValueError("max_total_runs 必须是整数")
    tasks = data.get("tasks")
    if not isinstance(tasks, list) or len(tasks) < 2:
        raise ValueError("地下城计划至少需要一个有冷却副本和一个无冷却副本")
    names, fallback_count, cooled_count = set(), 0, 0
    for task in tasks:
        if not isinstance(task, dict):
            raise ValueError("地下城副本必须为对象")
        name = task.get("id")
        if not isinstance(name, str) or not name or name in names:
            raise ValueError("地下城副本 id 不能为空或重复")
        names.add(name)
        if not isinstance(task.get("display_name"), str) or not task["display_name"]:
            raise ValueError(f"{name} 缺少 display_name")
        cooldown = number(task.get("cooldown_seconds"), f"{name}.cooldown_seconds", 0, 86400)
        fallback = task.get("fallback", False)
        if not isinstance(fallback, bool):
            raise ValueError(f"{name}.fallback 必须为布尔值")
        if fallback:
            fallback_count += 1
            if cooldown != 0:
                raise ValueError("无冷却兜底副本的 cooldown_seconds 必须为 0")
        else:
            cooled_count += 1
            if cooldown <= 0:
                raise ValueError("有冷却副本的 cooldown_seconds 必须大于 0")
        relative = task.get("profile")
        if not isinstance(relative, str) or not relative or "\\" in relative:
            raise ValueError(f"{name}.profile 必须是 profiles 下的相对 JSON 路径")
        path = (profiles_root / relative).resolve()
        if not path.is_relative_to(profiles_root.resolve()) or path.suffix.lower() != ".json" or not path.is_file():
            raise ValueError(f"{name}.profile 不存在或超出 profiles 目录")
        profile = validate(json.loads(path.read_text(encoding="utf-8-sig")), image_root, check_files)
        if profile.get("profile", {}).get("kind") != "dungeon_instance":
            raise ValueError(f"{name}.profile 必须是 kind=dungeon_instance")
    if fallback_count != 1 or cooled_count < 1:
        raise ValueError("地下城计划必须恰好有一个无冷却兜底副本，且至少一个有冷却副本")
    return data


def load(path: Path, image_root: Path, check_files=False):
    return validate(json.loads(path.read_text(encoding="utf-8-sig")), image_root, check_files)
