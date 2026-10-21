"""Pinned MaaFramework 5.13.1 adapter. No OpenCV reimplementation."""

from __future__ import annotations

import threading
import time
import json
import math
from datetime import datetime
from dataclasses import dataclass
from pathlib import Path

from .config import template_path


@dataclass(frozen=True)
class Match:
    box: tuple[int, int, int, int]
    score: float


class MaaRecognizer:
    def __init__(self, resource_root: Path, controller):
        from maa.resource import Resource
        from maa.tasker import Tasker

        self.resource = Resource()
        job = self.resource.post_bundle(resource_root)
        self.wait(job, threading.Event(), 15)
        if not job.succeeded:
            raise RuntimeError("MaaFramework 资源加载失败")
        self.tasker = Tasker()
        if not self.tasker.bind(self.resource, controller) or not self.tasker.inited:
            raise RuntimeError("MaaFramework 任务器初始化失败")
        self.image_root = resource_root / "image"
        self._template_cache = {}
        self.recognition_trace = []

    def template_metadata(self, path):
        from PIL import Image
        import numpy as np

        stat = path.stat()
        key = (stat.st_mtime_ns, stat.st_size)
        cached = self._template_cache.get(path)
        if cached is None or cached[0] != key:
            with Image.open(path) as template:
                rgb = template.convert("RGB")
                extrema = rgb.getextrema()
                if all(high - low < 5 for low, high in extrema):
                    raise ValueError("不能使用纯色模板，请包含文字或明显图案")
                pixels = np.asarray(rgb).astype(float)
                cached = (key, rgb.size, float(pixels.mean()), float(pixels.std(axis=(0, 1)).mean()))
            self._template_cache[path] = cached
        return cached[1:]

    @staticmethod
    def wait(job, stop, timeout=10):
        until = time.monotonic() + timeout
        while not job.done:
            if stop.is_set():
                raise InterruptedError("操作已停止")
            if time.monotonic() >= until:
                raise TimeoutError("MaaFramework 操作等待超时")
            stop.wait(0.02)
        if stop.is_set():
            raise InterruptedError("操作已停止")

    def recognize(self, image, rule, stop):
        from maa.pipeline import JRecognitionType, JTemplateMatch

        started = time.monotonic()
        if stop.is_set():
            raise InterruptedError("操作已停止")
        height, width = image.shape[:2]
        roi = rule.get("roi", [0, 0, width, height])
        x, y, w, h = roi
        if min(x, y) < 0 or min(w, h) < 1 or x + w > width or y + h > height:
            raise ValueError(f"{rule['name']} 的 ROI 超出标准截图")
        path = template_path(self.image_root, rule["template"])
        (tw, th), template_mean, template_contrast = self.template_metadata(path)
        if min(tw, th) < 8 or tw > w or th > h:
            raise ValueError(f"{rule['template']} 太小或大于搜索区域")
        param = JTemplateMatch(
            template=[rule["template"]], roi=tuple(roi),
            threshold=[rule.get("threshold", 0.9)], order_by="Score", method=5,
        )
        job = self.tasker.post_recognition(JRecognitionType.TemplateMatch, param, image)
        self.wait(job, stop)
        detail = job.get()
        if detail is None or not detail.nodes:
            raise RuntimeError("MaaFramework 未返回识别详情")
        recognition = detail.nodes[-1].recognition
        if recognition is None:
            raise RuntimeError("MaaFramework 识别详情缺失")
        candidates = recognition.raw_detail.get("all", [])
        trace = {"name": rule["name"], "template": rule["template"],
                 "threshold": rule.get("threshold", 0.9), "roi": list(roi),
                 "candidates": candidates, "accepted": False,
                 "elapsed_seconds": round(time.monotonic() - started, 4)}
        self.recognition_trace.append(trace)
        self.recognition_trace = self.recognition_trace[-100:]
        if not recognition.hit or recognition.best_result is None:
            return None
        best = recognition.best_result
        box = tuple(best.box)
        if "min_brightness_ratio" in rule:
            bx, by, bw, bh = box
            patch = image[by:by + bh, bx:bx + bw, :3]
            ratio = float(patch.mean()) / max(template_mean, 1)
            trace["brightness_ratio"] = ratio
            if ratio < rule["min_brightness_ratio"]:
                trace["rejected"] = "button_dimmed"
                return None
        if "min_contrast_ratio" in rule:
            bx, by, bw, bh = box
            patch = image[by:by + bh, bx:bx + bw, :3].astype(float)
            ratio = float(patch.std(axis=(0, 1)).mean()) / max(template_contrast, 1)
            trace["contrast_ratio"] = ratio
            if ratio < rule["min_contrast_ratio"]:
                trace["rejected"] = "button_occluded"
                return None
        if "requires_match" in rule:
            condition_rule = dict(rule["requires_match"], name=f"{rule['name']} / 附加画面校验")
            condition_match = self.recognize(image, condition_rule, stop)
            trace["condition"] = self.recognition_trace[-1].copy()
            if condition_match is None:
                trace["rejected"] = "required_match_missing"
                return None
        trace["accepted"] = True
        return Match(box, float(best.score))

    def recognize_near(self, image, rule, match, stop):
        """Revalidate near the old box without leaving the configured search ROI.

        A miss returns to full discovery; it never permits using old coordinates.
        Returned boxes remain in full-frame client coordinates.
        """
        height, width = image.shape[:2]
        x, y, w, h = match.box
        margin = math.ceil(max(12, min(w, h) * 0.25))
        rx, ry, rw, rh = rule.get("roi", [0, 0, width, height])
        left, top = max(0, rx, x - margin), max(0, ry, y - margin)
        right = min(width, rx + rw, x + w + margin)
        bottom = min(height, ry + rh, y + h + margin)
        if right - left < w or bottom - top < h:
            return None
        localized = dict(rule, roi=[left, top, right - left, bottom - top])
        return self.recognize(image, localized, stop)

    def read_cooldown(self, image, stop, captured_at):
        """Read only the detail-modal timer, never prices or timers behind the modal.

        Caller must verify the cooling modal on both fresh frames before using this.
        Missing models are recoverable: return None, not zero seconds.
        """
        from .cooldown import TIMER_ROI
        return self.read_timer_roi(image, stop, captured_at, TIMER_ROI)

    def read_timer_roi(self, image, stop, captured_at, roi):
        """OCR one caller-localized clock; never discover clocks across the whole screen."""
        from maa.pipeline import JOCR, JRecognitionType
        from .cooldown import MIN_SCORE, timer_sample

        model = self.image_root.parent / 'model/ocr/cooldown'
        if not all((model / name).is_file() for name in ('rec.onnx', 'det.onnx', 'keys.txt')):
            return None
        if len(roi) != 4 or any(isinstance(v, bool) or not isinstance(v, int) for v in roi):
            return None
        x, y, w, h = roi
        if min(x, y) < 0 or min(w, h) < 8 or image.shape[0] < y + h or image.shape[1] < x + w:
            return None
        job = self.tasker.post_recognition(JRecognitionType.OCR,
            JOCR(roi=tuple(roi), only_rec=True, model='cooldown', threshold=MIN_SCORE,
                 expected=[r'^[0-9]{2}[:：][0-9]{2}[:：][0-9]{2}$']), image)
        self.wait(job, stop)
        result = job.get()
        if result is None or not result.nodes or result.nodes[-1].recognition is None:
            return None
        recognition = result.nodes[-1].recognition
        self.recognition_trace.append(dict(name='副本剩余冷却 OCR', roi=list(roi),
            candidates=recognition.raw_detail.get('all', []), accepted=recognition.hit))
        self.recognition_trace = self.recognition_trace[-100:]
        best = recognition.best_result
        if not recognition.hit or best is None:
            return None
        return timer_sample(best.text, float(best.score), captured_at)

    def cancel(self):
        return self.tasker.post_stop()


class MaaBackend(MaaRecognizer):
    MAX_CAPTURE_AGE = 2.0

    def __init__(self, root, window, capture_method="FramePool", input_method="Seize"):
        from maa.controller import Win32Controller
        from maa.define import MaaWin32ScreencapMethodEnum, MaaWin32InputMethodEnum
        from maa.toolkit import Toolkit
        from .windows import Windows

        self.win = Windows()
        self.window = window
        self.original_size = self.win.size(window.hwnd)
        self.capture_method, self.input_method = capture_method, input_method
        self.diagnostic_root = root / "logs" / "diagnostics"
        self.last_image = None
        self.guard()
        Toolkit.init_option(root / "logs" / "maa")
        self.controller = Win32Controller(
            window.hwnd,
            screencap_method=getattr(MaaWin32ScreencapMethodEnum, capture_method),
            mouse_method=getattr(MaaWin32InputMethodEnum, input_method),
            keyboard_method=getattr(MaaWin32InputMethodEnum, input_method),
        )
        job = self.controller.post_connection()
        self.wait(job, threading.Event(), 15)
        if not job.succeeded:
            raise RuntimeError("连接游戏失败，请检查权限或切换截图方式")
        if not self.controller.set_screenshot_target_short_side(720):
            raise RuntimeError("无法设置标准截图尺寸")
        self.last_shape = None
        self.last_capture = 0.0
        super().__init__(root / "assets", self.controller)

    def guard(self):
        self.win.check(self.window, self.original_size)

    def capture(self, stop):
        import numpy as np

        self.guard()
        if self.capture_method == "ScreenDC":
            self.win.foreground(self.window.hwnd)
        for attempt in range(3):
            job = self.controller.post_screencap()
            self.wait(job, stop)
            if not job.succeeded:
                raise RuntimeError("截图失败，请切换 FramePool / ScreenDC")
            image = job.get()
            if image is None or image.ndim != 3 or min(image.shape[:2]) < 64:
                raise RuntimeError("截图为空或尺寸异常")
            self.last_image = image.copy()
            self.recognition_trace = []
            if not self.is_nearly_uniform(image):
                self.last_shape = image.shape[:2]
                self.last_capture = time.monotonic()
                return image
            if attempt < 2:
                stop.wait(0.2)
        raise RuntimeError("连续 3 帧截图疑似纯色，请检查游戏渲染或截图后端")

    def save_diagnostic(self, state, reason, config):
        from PIL import Image

        self.diagnostic_root.mkdir(parents=True, exist_ok=True)
        stem = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        target = self.diagnostic_root / stem
        if self.last_image is not None:
            Image.fromarray(self.last_image[:, :, :3][:, :, ::-1]).save(target.with_suffix(".png"))
        report = {"state": state, "reason": reason, "capture_method": self.capture_method,
                  "input_method": self.input_method, "recognition": self.recognition_trace,
                  "capture_age_seconds": round(time.monotonic() - self.last_capture, 4),
                  "execution": getattr(self, "diagnostic_context", {}), "config": config}
        target.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return str(target.with_suffix(".json"))

    @staticmethod
    def is_nearly_uniform(image):
        """Reject only genuinely near-solid captures, not a normally dark scene."""
        import numpy as np

        rgb = image[:, :, :3].astype(np.int16, copy=False)
        channel_span = np.ptp(rgb.reshape(-1, 3), axis=0)
        reference = rgb[0, 0]
        changed_fraction = float(np.mean(np.max(np.abs(rgb - reference), axis=2) > 5))
        return int(channel_span.max()) <= 8 and changed_fraction < 0.001

    def click(self, match, offset, stop, pause):
        if stop.is_set() or pause.is_set():
            raise InterruptedError("点击已取消")
        self.guard()
        if self.last_shape is None or time.monotonic() - self.last_capture > self.MAX_CAPTURE_AGE:
            raise RuntimeError("识别截图已过期，不执行点击")
        x, y, w, h = match.box
        point = (x + w // 2 + offset[0], y + h // 2 + offset[1])
        height, width = self.last_shape
        if not (0 <= point[0] < width and 0 <= point[1] < height):
            raise RuntimeError("点击目标超出游戏客户区")
        if self.input_method == "Seize":
            self.win.foreground(self.window.hwnd)
        if stop.is_set() or pause.is_set():
            raise InterruptedError("点击已取消")
        hold = getattr(self, 'click_hold_seconds', 0.0)
        if hold:
            # Keep down/up in separate game frames for controls which poll input.
            # Release even if cancellation or failed acknowledgement interrupts down.
            job = self.controller.post_touch_down(*point)
            try:
                self.wait(job, stop)
                if not job.succeeded:
                    raise RuntimeError("鼠标按下发送失败")
                if stop.wait(hold) or pause.is_set():
                    raise InterruptedError("点击已取消")
            finally:
                release = self.controller.post_touch_up()
                self.wait(release, threading.Event(), timeout=3)
                if not release.succeeded:
                    raise RuntimeError("鼠标释放发送失败，流程停止")
        else:
            job = self.controller.post_click(*point)
            self.wait(job, stop)
            if not job.succeeded:
                raise RuntimeError("点击发送失败，流程已停止")

    def swipe(self, direction, stop, pause):
        """Horizontal list drag in normalized client coordinates, never desktop coordinates."""
        if stop.is_set() or pause.is_set():
            raise InterruptedError('拖动已取消')
        self.guard()
        if direction not in {'left', 'right'} or not self.capture_is_fresh(0):
            raise RuntimeError('列表拖动方向无效或截图过期')
        height, width = self.last_shape
        x1, x2, y = int(width * .78), int(width * .25), int(height * .45)
        if direction == 'right':
            x1, x2 = x2, x1
        if self.input_method == 'Seize':
            self.win.foreground(self.window.hwnd)
        if stop.is_set() or pause.is_set():
            raise InterruptedError('拖动已取消')
        job = self.controller.post_swipe(x1, y, x2, y, 650)
        self.wait(job, stop)
        if not job.succeeded:
            raise RuntimeError('列表拖动发送失败，请检查输入后端')

    def capture_is_fresh(self, reserve_seconds=0.5):
        """Leave enough time for one-template revalidation before click()."""
        if self.last_shape is None:
            return False
        allowed_age = max(0.1, self.MAX_CAPTURE_AGE - reserve_seconds)
        return time.monotonic() - self.last_capture <= allowed_age
