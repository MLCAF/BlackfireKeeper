import json
import unittest
from pathlib import Path

from vendor_core.backend import Match
from vendor_core.runner import Runner


ROOT = Path(__file__).resolve().parents[2]


class Clock:
    def __init__(self, step=0.05):
        self.value, self.step = 0, step

    def __call__(self):
        self.value += self.step
        return self.value


class FakeBackend:
    def __init__(self, frames):
        self.frames, self.index = frames, 0
        self.clicks, self.cancels = 0, 0
        self.click_offsets = []
        self.fresh_results = []
        self.on_match = None

    def guard(self):
        pass

    def capture(self, stop):
        frame = self.frames[min(self.index, len(self.frames) - 1)]
        self.index += 1
        return frame

    def recognize(self, image, rule, stop):
        matched = image == rule["template"] or (
            isinstance(image, (set, frozenset)) and rule["template"] in image
        )
        if matched and "requires_match" in rule:
            matched = isinstance(image, (set, frozenset)) and rule["requires_match"]["template"] in image
        if matched:
            if self.on_match:
                self.on_match()
            return Match((10, 20, 30, 40), 0.99)
        return None

    def click(self, match, offset, stop, pause):
        if stop.is_set() or pause.is_set():
            raise InterruptedError()
        self.clicks += 1
        self.click_offsets.append(list(offset))

    def capture_is_fresh(self, reserve_seconds=0.5):
        return self.fresh_results.pop(0) if self.fresh_results else True

    def cancel(self):
        self.cancels += 1


def config(frames=2, minutes=0.1):
    return {
        "version": 1, "initial_state": "Menu", "max_minutes": minutes,
        "max_rounds": 1, "poll_seconds": 0.001,
        "states": {
            "Menu": {"timeout_seconds": 10, "rules": [{"name": "start", "template": "start", "action": "click", "next": "Battle", "event": "round_start", "confirm_frames": frames, "cooldown_seconds": 0.01}]},
            "Battle": {"timeout_seconds": 10, "rules": [{"name": "end", "template": "end", "action": "click", "next": "Menu", "event": "round_end", "confirm_frames": frames, "cooldown_seconds": 0.01}]},
        },
    }


class RunnerTests(unittest.TestCase):
    def test_refresh_accepts_successful_second_and_final_attempt(self):
        runner, backend, _ = self.make(["start"], config(frames=1))
        backend.fresh_results = [False, False, True]
        rule = runner.ordered_rules()[0]
        original = Match((10, 20, 30, 40), 0.99)
        self.assertEqual(runner.refresh_match_before_click(rule, original), original)
        self.assertEqual(backend.index, 2)
        self.assertEqual(backend.clicks, 0)

    def test_refresh_still_rejects_two_stale_revalidations(self):
        runner, backend, _ = self.make(["start"])
        backend.fresh_results = [False, False, False]
        with self.assertRaisesRegex(RuntimeError, "连续两次超时"):
            runner.refresh_match_before_click(runner.ordered_rules()[0], Match((10, 20, 30, 40), 0.99))
        self.assertEqual(backend.index, 2)
        self.assertEqual(backend.clicks, 0)

    def test_refresh_scans_blockers_not_unrelated_scene_alternatives(self):
        runner, backend, _ = self.make(["start"])
        rule = runner.ordered_rules()[0]
        runner.config["states"]["Menu"]["rules"] = [
            {"name": "other room", "template": "other"},
            {"name": "modal", "template": "modal", "foreground": True}, rule]
        checked = []
        recognize = backend.recognize
        def observed(image, candidate, stop):
            checked.append(candidate["name"])
            return recognize(image, candidate, stop)
        backend.recognize = observed
        backend.fresh_results = [False, True]
        self.assertIsNotNone(runner.refresh_match_before_click(rule, Match((10, 20, 30, 40), 0.99)))
        self.assertEqual(checked, ["modal", "start"])

    def test_local_refresh_miss_never_falls_back_to_old_coordinates(self):
        runner, backend, _ = self.make(["start"])
        backend.fresh_results = [False]
        backend.recognize_near = lambda *args: None
        self.assertIsNone(runner.refresh_match_before_click(runner.ordered_rules()[0], Match((10, 20, 30, 40), 0.99)))
        self.assertEqual(backend.clicks, 0)

    def test_refresh_position_change_returns_to_discovery(self):
        runner, backend, _ = self.make(["start"])
        backend.fresh_results = [False]
        backend.recognize_near = lambda *args: Match((100, 120, 30, 40), 0.99)
        self.assertIsNone(runner.refresh_match_before_click(runner.ordered_rules()[0], Match((10, 20, 30, 40), 0.99)))
        self.assertEqual(backend.clicks, 0)

    def test_completed_click_counts_before_next_foreground_screen(self):
        data = config(frames=1)
        data["states"] = {"Menu": {"timeout_seconds": 10, "rules": [
            {"name": "loot", "template": "loot", "action": "click", "next": "Menu",
             "verify_change": True, "retry_wait_seconds": 0.001, "confirm_frames": 1,
             "cooldown_seconds": 0.01, "increment_counter": "battles"},
            {"name": "done", "template": "done", "foreground": True, "action": "none",
             "event": "task_complete", "confirm_frames": 1},
        ]}}
        runner, backend, _ = self.make(["loot", "done"], data)
        runner.run()
        self.assertTrue(runner.completed)
        self.assertEqual(backend.clicks, 1)
        self.assertEqual(runner.counters["battles"], 1)

    def test_dimming_after_click_is_not_navigation_success(self):
        data = config(frames=1, minutes=0.01)
        data["states"]["Menu"]["rules"][0].update(
            verify_change=True, retry_wait_seconds=0.001, min_brightness_ratio=0.65)
        runner, backend, _ = self.make(["start", "dimmed"], data)
        original = backend.recognize
        def recognize(image, rule, stop):
            if image == "dimmed" and rule["template"] == "start":
                return None if "min_brightness_ratio" in rule else Match((10, 20, 30, 40), 0.99)
            return original(image, rule, stop)
        backend.recognize = recognize
        runner.run()
        self.assertEqual(backend.clicks, 1)
        self.assertEqual(runner.stats.state, "Menu")
        self.assertEqual(runner.stats.rounds, 0)

    def test_pause_at_click_boundary_keeps_runner_resumable(self):
        runner, backend, _ = self.make(["start"], config(frames=1, minutes=0.006))
        def pause_before_click(*args):
            runner.pause()
            raise InterruptedError()
        backend.click = pause_before_click
        runner.run()
        self.assertEqual(backend.clicks, 0)
        self.assertIn("运行时限", runner.reason)
        self.assertNotIn("中止", runner.reason)

    def test_refresh_cancels_old_click_when_modal_appears(self):
        data = config(frames=1)
        data["states"]["Menu"]["rules"].append({
            "name": "modal", "template": "modal", "foreground": True,
            "action": "none", "event": "task_complete", "confirm_frames": 1,
        })
        runner, backend, _ = self.make(["start", frozenset({"start", "modal"})], data)
        backend.fresh_results = [False]
        runner.run()
        self.assertTrue(runner.completed)
        self.assertEqual(backend.clicks, 0)

    def test_retry_yields_when_modal_appears(self):
        data = config(frames=1)
        data["states"]["Menu"]["rules"][0].update(verify_change=True, retry_wait_seconds=0.001)
        data["states"]["Menu"]["rules"].append({
            "name": "modal", "template": "modal", "foreground": True,
            "action": "none", "event": "task_complete", "confirm_frames": 1,
        })
        runner, backend, _ = self.make(["start", frozenset({"start", "modal"})], data)
        runner.run()
        self.assertTrue(runner.completed)
        self.assertEqual(backend.clicks, 1)
        self.assertEqual(runner.stats.state, "Menu")

    def test_verified_disappearance_allows_same_button_on_next_page(self):
        data = config(frames=1)
        data["states"] = {"Menu": {"timeout_seconds": 10, "rules": [
            {"name": "next", "template": "next", "action": "click", "next": "Menu",
             "verify_change": True, "retry_wait_seconds": 0.001, "confirm_frames": 1,
             "cooldown_seconds": 0.01, "increment_counter": "floors"},
            {"name": "done", "template": "done", "action": "none", "event": "task_complete",
             "confirm_frames": 1},
        ]}}
        runner, backend, _ = self.make(["next", "missing", "next", "missing", "done"], data)
        runner.run()
        self.assertTrue(runner.completed)
        self.assertEqual(backend.clicks, 2)
        self.assertEqual(runner.counters["floors"], 2)

    def test_retry_checks_time_limit_before_each_click(self):
        data = config(frames=1, minutes=0.006)
        data["states"]["Menu"]["rules"][0].update(verify_change=True, retry_wait_seconds=0.001)
        runner, backend, _ = self.make(["start"], data)
        runner.run()
        self.assertLess(backend.clicks, 8)
        self.assertIn("运行时限", runner.reason)

    def test_progress_modal_has_priority_over_next_floor(self):
        data = json.loads((ROOT / "config" / "profiles" / "dungeon_unlimited.json").read_text(encoding="utf-8"))
        data["initial_state"] = "Progress"
        data["max_minutes"] = 0.01
        data["poll_seconds"] = 0.001
        for rule in data["states"]["Progress"]["rules"]:
            rule.update(verify_change=False, cooldown_seconds=0.01)
        both = frozenset({"离开祝福弹窗.png", "去下一层.png"})
        runner, backend, events = self.make([both, both, "missing"], data)
        runner.run()
        self.assertEqual(backend.clicks, 1)
        matched = [value["name"] for kind, value in events if kind == "match"]
        self.assertEqual(matched, ["不选择 Buff 并离开"])

    def make(self, frames, data=None, dry=False):
        backend, events = FakeBackend(frames), []
        runner = Runner(backend, data or config(), lambda kind, payload: events.append((kind, payload)), dry_run=dry, clock=Clock())
        return runner, backend, events

    def test_round_completion_and_statistics(self):
        runner, backend, events = self.make(["start", "start", "end", "end"])
        runner.run()
        self.assertEqual(backend.clicks, 2)
        self.assertEqual(runner.stats.rounds, 1)
        self.assertGreater(runner.stats.last_round, 0)
        self.assertEqual(runner.stats.average_round, runner.stats.last_round)
        self.assertIn("轮数", runner.reason)
        self.assertEqual(events[-1][0], "finished")

    def test_wide_loot_claim_is_counted_once_and_requires_title(self):
        data = json.loads((ROOT / "config/profiles/dungeon_unlimited.json").read_text(encoding="utf-8"))
        rule = next(r.copy() for r in data['states']['Progress']['rules'] if '宽弹窗兼容' in r['name'])
        rule.update(confirm_frames=1, cooldown_seconds=.01, retry_wait_seconds=.001)
        data.update(initial_state='Progress', poll_seconds=.001, max_minutes=.1)
        data['states'] = {'Progress': {'timeout_seconds': 10, 'rules': [rule,
            dict(name='done', template='done', action='none', event='task_complete', confirm_frames=1)]}}
        both = {rule['template'], rule['requires_match']['template']}
        runner, backend, _ = self.make([rule['template'], both, 'done', 'done'], data)
        runner.run()
        self.assertTrue(runner.completed)
        self.assertEqual(backend.clicks, 1)
        self.assertEqual(runner.counters, {'battles_completed': 1})

    def test_loot_title_disappearance_alone_does_not_confirm_click(self):
        data = config(frames=1)
        rule = data['states']['Menu']['rules'][0]
        rule.update(requires_match={'template': 'title'}, verify_change=True, retry_wait_seconds=.001)
        runner, backend, _ = self.make([{'start', 'title'}, 'start'], data)
        runner.run()
        self.assertEqual(backend.clicks, 1)
        self.assertEqual(runner.stats.rounds, 0)
        self.assertEqual(runner.stats.state, 'Menu')

    def test_unlimited_runtime_finishes_round_after_more_than_thirty_minutes(self):
        data = config(frames=1, minutes=0)
        for state in data["states"].values():
            state["timeout_seconds"] = 86400
        runner, backend, _ = self.make(["start", "end"], data)
        runner.clock = Clock(step=1900)
        runner.run()
        self.assertGreater(runner.stats.elapsed, 1800)
        self.assertEqual(backend.clicks, 2)
        self.assertEqual(runner.stats.rounds, 1)
        self.assertEqual(runner.reason, "达到完成轮数上限")

    def test_unlimited_runtime_keeps_click_retry_bound(self):
        data = config(frames=1, minutes=0)
        data["states"]["Menu"]["rules"][0].update(verify_change=True, retry_wait_seconds=0.001)
        runner, backend, _ = self.make(["start"], data)
        runner.run()
        self.assertEqual(backend.clicks, 8)
        self.assertIn("连续 8 次点击", runner.reason)
        self.assertNotIn("运行时限", runner.reason)

    def test_unlimited_runtime_keeps_state_timeout(self):
        data = config(minutes=0)
        data["states"]["Menu"]["timeout_seconds"] = 0.2
        runner, backend, _ = self.make(["missing"], data)
        runner.run()
        self.assertIn("状态 Menu 等待超时", runner.reason)
        self.assertEqual(backend.clicks, 0)

    def test_unlimited_runtime_can_be_stopped_while_paused(self):
        runner, backend, _ = self.make(["start"], config(minutes=0))
        clock = Clock(step=1900)
        def paused_clock():
            value = clock()
            if value >= 5700:
                runner.stop("F8")
            return value
        runner.clock = paused_clock
        runner.pause()
        runner.run()
        self.assertGreater(runner.stats.elapsed, 1800)
        self.assertEqual(runner.stats.active, 0)
        self.assertEqual(backend.clicks, 0)
        self.assertEqual(runner.reason, "F8")

    def test_missing_runtime_limit_still_defaults_to_thirty_minutes(self):
        data = config()
        del data["max_minutes"]
        runner, _, _ = self.make(["missing"], data)
        runner.stats.elapsed = 1799
        self.assertFalse(runner.runtime_limit_reached())
        runner.stats.elapsed = 1800
        self.assertTrue(runner.runtime_limit_reached())

    def test_dry_run_never_clicks_or_advances_or_counts(self):
        runner, backend, _ = self.make(["start"], config(minutes=0.005), dry=True)
        runner.run()
        self.assertEqual(backend.clicks, 0)
        self.assertEqual(runner.stats.state, "Menu")
        self.assertEqual(runner.stats.rounds, 0)

    def test_nonconsecutive_matches_do_not_click(self):
        runner, backend, _ = self.make(["start", "missing", "start", "missing"], config(minutes=0.008))
        runner.run()
        self.assertEqual(backend.clicks, 0)

    def test_state_timeout_stops_without_clicks(self):
        data = config()
        data["states"]["Menu"]["timeout_seconds"] = 0.2
        runner, backend, _ = self.make(["missing"], data)
        runner.run()
        self.assertIn("超时", runner.reason)
        self.assertEqual(backend.clicks, 0)

    def test_stop_after_recognition_prevents_click(self):
        runner, backend, _ = self.make(["start"], config(frames=1))
        backend.on_match = lambda: runner.stop("F8")
        runner.run()
        self.assertEqual(backend.clicks, 0)
        self.assertEqual(runner.reason, "F8")

    def test_pause_after_recognition_prevents_click(self):
        runner, backend, _ = self.make(["start"], config(frames=1, minutes=0.006))
        backend.on_match = runner.pause
        runner.run()
        self.assertEqual(backend.clicks, 0)
        self.assertEqual(runner.stats.rounds, 0)

    def test_pause_excluded_from_active_but_time_limit_still_works(self):
        runner, backend, _ = self.make(["start"], config(minutes=0.004))
        runner.pause()
        runner.run()
        self.assertEqual(runner.stats.active, 0)
        self.assertGreater(runner.stats.elapsed, 0)
        self.assertIn("时限", runner.reason)

    def test_click_failure_does_not_advance_or_count(self):
        runner, backend, _ = self.make(["start"], config(frames=1))
        def fail(*args):
            raise RuntimeError("click failed")
        backend.click = fail
        runner.run()
        self.assertEqual(runner.stats.state, "Menu")
        self.assertEqual(runner.stats.clicks, 0)
        self.assertIn("click failed", runner.reason)

    def test_exception_during_capture_stops(self):
        runner, backend, _ = self.make(["start"])
        def fail(*args):
            raise RuntimeError("window closed")
        backend.capture = fail
        runner.run()
        self.assertIn("window closed", runner.reason)
        self.assertGreater(backend.cancels, 0)

    def test_task_complete_marks_success_without_click(self):
        data = config(frames=1)
        data["initial_state"] = "Only"
        data["states"] = {
            "Only": {"timeout_seconds": 10, "rules": [
                {"name": "confirmed", "template": "done", "action": "none", "next": "Only", "event": "task_complete", "confirm_frames": 1, "cooldown_seconds": 1}
            ]}
        }
        runner, backend, _ = self.make(["done"], data)
        runner.run()
        self.assertTrue(runner.completed)
        self.assertEqual(backend.clicks, 0)
        self.assertIn("任务完成", runner.reason)

    def test_round_end_without_start_prevents_action(self):
        data = config(frames=1)
        data["initial_state"] = "Battle"
        runner, backend, _ = self.make(["end"], data)
        runner.run()
        self.assertEqual(backend.clicks, 0)
        self.assertIn("没有 round_start", runner.reason)

    def test_unlimited_dungeon_profile_completes_one_verified_round(self):
        data = json.loads((ROOT / "config" / "profiles" / "dungeon_unlimited.json").read_text(encoding="utf-8"))
        data["max_rounds"] = 1
        data["poll_seconds"] = 0.001
        data["max_minutes"] = 0.2
        for state in data["states"].values():
            for rule in state["rules"]:
                rule["cooldown_seconds"] = 0.01
                rule["verify_change"] = False
        frames = [
            "地下城入口主体.png", "地下城入口主体.png",
            "一本.png", "一本.png",
            "去探险.png", "去探险.png",
            "开局buff.png", "开局buff.png",
            "经验buff.png", "经验buff.png",
            "确认buff.png", "确认buff.png",
            "关闭技能卡.png", "关闭技能卡.png",
            "离开祝福弹窗.png", "离开祝福弹窗.png",
            "技能卡.png", "技能卡.png",
            "选择技能卡.png", "选择技能卡.png",
            "确认领取技能卡.png", "确认领取技能卡.png",
            "关闭技能卡.png", "关闭技能卡.png",
            "不要剩余技能卡确定离开.png", "不要剩余技能卡确定离开.png",
            "去下一层.png", "去下一层.png",
            "离开祝福弹窗.png", "离开祝福弹窗.png",
            "自动战斗.png", "自动战斗.png",
            "战斗结束.png", "战斗结束.png",
            "战斗结束领取战利品.png", "战斗结束领取战利品.png",
            "去下一层.png", "去下一层.png",
            "自动战斗.png", "自动战斗.png",
            "战斗结束.png", "战斗结束.png",
            "战斗结束领取战利品.png", "战斗结束领取战利品.png",
            "去下一层.png", "去下一层.png",
            "自动战斗.png", "自动战斗.png",
            "战斗结束.png", "战斗结束.png",
            "战斗结束领取战利品.png", "战斗结束领取战利品.png",
            "宝箱主体.png", "宝箱主体.png",
            "领取宝箱.png", "领取宝箱.png",
            "确认领取宝箱.png", "确认领取宝箱.png",
            "完成地下城.png", "完成地下城.png",
            "一本.png", "一本.png",
        ]
        runner, backend, _ = self.make(frames, data)
        runner.run()
        self.assertEqual(backend.clicks, 30)
        self.assertEqual(runner.stats.rounds, 1)
        self.assertEqual(runner.stats.state, "Entry")
        self.assertEqual(runner.last_round_counters, {"battles_completed": 3})
        self.assertEqual(runner.reason, "达到完成轮数上限")
        self.assertIn([0, 99], backend.click_offsets)

    def test_change_confirmation_retries_center_then_circle(self):
        data = {
            "version": 1, "initial_state": "Menu", "max_minutes": 0.1, "max_rounds": 0, "poll_seconds": 0.001,
            "states": {
                "Menu": {"timeout_seconds": 10, "rules": [{
                    "name": "select", "template": "select", "action": "click", "next": "Done", "event": "none",
                    "verify_change": True, "retry_center_clicks": 2, "retry_max_clicks": 4,
                    "retry_radius": 10, "retry_wait_seconds": 0.001, "confirm_frames": 1, "cooldown_seconds": 0.01,
                }]},
                "Done": {"timeout_seconds": 10, "rules": [{
                    "name": "done", "template": "done", "action": "none", "next": "Done", "event": "task_complete",
                    "confirm_frames": 1, "cooldown_seconds": 0.01,
                }]},
            },
        }
        runner, backend, _ = self.make(["select", "select", "select", "select", "done", "done"], data)
        runner.run()
        self.assertTrue(runner.completed)
        self.assertEqual(backend.clicks, 4)
        self.assertEqual(backend.click_offsets[:2], [[0, 0], [0, 0]])
        self.assertNotEqual(backend.click_offsets[2], [0, 0])

    def test_change_confirmation_requires_target_to_leave_original_position(self):
        original = Match((100, 200, 80, 40), 0.99)
        same_place = Match((105, 204, 80, 40), 0.99)
        moved = Match((250, 200, 80, 40), 0.99)
        self.assertFalse(Runner.target_left_original_position(original, same_place))
        self.assertTrue(Runner.target_left_original_position(original, moved))
        self.assertTrue(Runner.target_left_original_position(original, None))

    def test_stale_match_is_recaptured_and_revalidated_before_click(self):
        runner, backend, events = self.make(["start", "start", "end"], config(frames=1))
        backend.fresh_results = [False, True, True]
        runner.run()
        self.assertEqual(backend.clicks, 2)
        self.assertEqual(runner.stats.rounds, 1)
        self.assertTrue(any("重新截图复核目标" in payload for kind, payload in events if kind == "log"))

    def test_unlimited_dungeon_finishes_with_fewer_than_three_counted_battles(self):
        for count in (0, 1, 2):
            with self.subTest(battles_completed=count):
                data = json.loads((ROOT / "config" / "profiles" / "dungeon_unlimited.json").read_text(encoding="utf-8"))
                data["max_rounds"] = 1
                data["poll_seconds"] = 0.001
                data["max_minutes"] = 0.1
                for state in data["states"].values():
                    for rule in state["rules"]:
                        rule["cooldown_seconds"] = 0.01
                        rule["verify_change"] = False
                frames = ["地下城入口主体.png", "地下城入口主体.png", "一本.png", "一本.png", "去探险.png", "去探险.png"]
                for _ in range(count):
                    frames += [
                        "去下一层.png", "去下一层.png", "自动战斗.png", "自动战斗.png",
                        "战斗结束.png", "战斗结束.png", "战斗结束领取战利品.png", "战斗结束领取战利品.png",
                    ]
                frames += ["完成地下城.png", "完成地下城.png", "一本.png", "一本.png"]
                runner, backend, _ = self.make(frames, data)
                runner.run()
                self.assertEqual(backend.clicks, 4 + 4 * count)
                self.assertEqual(runner.stats.rounds, 1)
                self.assertEqual(runner.stats.state, "Entry")
                self.assertEqual(runner.last_round_counters.get("battles_completed", 0), count)
                self.assertEqual(runner.reason, "达到完成轮数上限")

    def test_explicit_counter_gate_still_prevents_premature_action(self):
        data = json.loads((ROOT / "config" / "profiles" / "dungeon_unlimited.json").read_text(encoding="utf-8"))
        finish = next(rule for rule in data["states"]["Progress"]["rules"] if rule["name"] == "完成冒险")
        finish["requires_counter"] = "battles_completed"
        finish["requires_at_least"] = 3
        data["poll_seconds"] = 0.001
        data["max_minutes"] = 0.1
        for state in data["states"].values():
            for rule in state["rules"]:
                rule["cooldown_seconds"] = 0.01
                rule["verify_change"] = False
        frames = [
            "地下城入口主体.png", "地下城入口主体.png",
            "一本.png", "一本.png", "去探险.png", "去探险.png",
            "去下一层.png", "去下一层.png", "自动战斗.png", "自动战斗.png",
            "战斗结束.png", "战斗结束.png", "战斗结束领取战利品.png", "战斗结束领取战利品.png",
            "完成地下城.png", "完成地下城.png",
        ]
        runner, backend, _ = self.make(frames, data)
        runner.run()
        self.assertEqual(backend.clicks, 7)
        self.assertIn("battles_completed 至少 3 次", runner.reason)

    def test_every_round_clicks_dungeon_before_selecting_target(self):
        data = json.loads((ROOT / "config/profiles/dungeon_unlimited.json").read_text(encoding="utf-8"))
        data.update(max_rounds=2, poll_seconds=0.001, max_minutes=0.2)
        for state in data["states"].values():
            for rule in state["rules"]:
                rule.update(confirm_frames=1, cooldown_seconds=0.01, verify_change=False)
        # The navigation icon remains visible after clicking it. After each
        # adventure, only main navigation (not the target dungeon) is visible.
        menu = {"地下城入口主体.png", "一本.png"}
        frames = [menu, menu, "去探险.png", "完成地下城.png", "地下城入口主体.png"] * 2
        runner, backend, events = self.make(frames, data)
        runner.run()
        names = [payload["name"] for kind, payload in events if kind == "match"]
        self.assertEqual(names, [
            "每轮先进入地下城", "选择目标副本", "去探险", "完成冒险", "确认冒险结束已返回主导航",
        ] * 2)
        self.assertEqual(backend.clicks, 8)
        self.assertEqual(runner.stats.rounds, 2)
        self.assertEqual(runner.stats.state, "Entry")
        self.assertEqual(runner.reason, "达到完成轮数上限")

    def test_dungeon_target_never_bypasses_initial_navigation(self):
        data = json.loads((ROOT / "config/profiles/dungeon_unlimited.json").read_text(encoding="utf-8"))
        data.update(poll_seconds=0.001, max_minutes=0.1)
        data["states"]["Entry"]["timeout_seconds"] = 1
        runner, backend, _ = self.make(["一本.png"], data)
        runner.run()
        self.assertEqual(backend.clicks, 0)
        self.assertEqual(runner.stats.rounds, 0)
        self.assertIn("状态 Entry 等待超时", runner.reason)

    def test_persistent_navigation_without_target_stops_without_repeated_clicks(self):
        data = json.loads((ROOT / "config/profiles/dungeon_unlimited.json").read_text(encoding="utf-8"))
        data.update(poll_seconds=0.001, max_minutes=0.1)
        data["states"]["SelectDungeon"]["timeout_seconds"] = 1
        data["states"]["Entry"]["rules"][0]["cooldown_seconds"] = 0.01
        runner, backend, _ = self.make(["地下城入口主体.png"], data)
        runner.run()
        self.assertEqual(backend.clicks, 1)
        self.assertEqual(runner.stats.rounds, 0)
        self.assertIn("状态 SelectDungeon 等待超时", runner.reason)

    def test_opening_buff_leave_state_takes_next_floor_without_dialog(self):
        data = json.loads((ROOT / "config" / "profiles" / "dungeon_unlimited.json").read_text(encoding="utf-8"))
        data["initial_state"] = "OpeningBuffLeaveConfirm"
        data["poll_seconds"] = 0.001
        data["max_minutes"] = 0.01
        for state in data["states"].values():
            for rule in state["rules"]:
                rule["cooldown_seconds"] = 0.01
                rule["verify_change"] = False
        runner, backend, events = self.make(["去下一层.png", "去下一层.png", "missing"], data)
        runner.run()
        self.assertEqual(backend.clicks, 1)
        self.assertEqual(runner.stats.state, "Progress")
        self.assertTrue(any("OpeningBuffLeaveConfirm → Progress" in payload for kind, payload in events if kind == "log"))

    def test_opening_buff_leave_modal_beats_background_next_floor(self):
        data = json.loads((ROOT / "config" / "profiles" / "dungeon_unlimited.json").read_text(encoding="utf-8"))
        data["initial_state"] = "OpeningBuffLeaveConfirm"
        data["poll_seconds"] = 0.001
        data["max_minutes"] = 0.01
        for state in data["states"].values():
            for rule in state["rules"]:
                rule["cooldown_seconds"] = 0.01
                rule["verify_change"] = False
        modal_and_background = frozenset({"离开祝福弹窗.png", "去下一层.png"})
        runner, backend, events = self.make([modal_and_background, modal_and_background, "missing"], data)
        runner.run()
        self.assertEqual(backend.clicks, 1)
        self.assertEqual(runner.stats.state, "Progress")
        self.assertTrue(any(
            kind == "match" and payload["name"] == "确认放弃剩余 Buff"
            for kind, payload in events
        ))


if __name__ == "__main__":
    unittest.main()
