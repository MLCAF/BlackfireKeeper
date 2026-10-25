"""Keep recognition evidence across the watchdog's capture-before-match boundary."""
import copy
import time
import threading

from vendor_core.backend import MaaBackend
from .version import version_info
from .scene_variants import SceneVariantMixin


class CaptureRecoveryTimeout(RuntimeError):
    pass


class DiagnosticBackend(SceneVariantMixin, MaaBackend):
    CAPTURE_RECOVERY_SECONDS = 6.0
    CAPTURE_MAX_ATTEMPTS = 30

    @property
    def click_hold_seconds(self):
        # PostMessage's immediate down/up can fall in the same game frame.
        # Use the already-tested held-click path without changing user input mode.
        return .15 if self.input_method in ('PostMessage', 'SendMessage') else 0.

    def recognize(self, image, rule, stop):
        if image is getattr(self, '_timed_out_image', None):
            return None
        try:
            return super().recognize(image, rule, stop)
        except TimeoutError:
            # Only recognition is retried. A timed-out click is never replayed.
            self.last_shape, self.last_capture = None, 0
            self._timed_out_image = image
            if getattr(self, 'recognition_restarts', 0) >= 1:
                raise
            self.recognition_restarts = 1
            cancelled = self.cancel()
            self.wait(cancelled, stop, timeout=3)
            if not cancelled.succeeded:
                raise RuntimeError('超时识别任务未确认停止，拒绝继续识别或点击')
            getattr(self, 'emit', lambda *args: None)('log',
                '识别任务超时，已停止旧任务并丢弃旧帧；重新截图后复核一次，不重复发送点击')
            return None

    def __init__(self, root, window, capture_method='FramePool', input_method='Seize', preparation_stop=None):
        from vendor_core.windows import Windows
        from .window_geometry import Geometry, standardize
        from .window_options import load_resolution
        self.geometry_stop = preparation_stop or threading.Event()
        self.window_geometry = standardize(Geometry(Windows()), window, preparation_stop or threading.Event(), preferred=load_resolution(root))
        super().__init__(root, window, capture_method, input_method)

    def guard(self):
        from .window_geometry import Geometry
        expected = tuple(self.window_geometry['actual_client_size'])
        # Maa/native calls may use a different thread DPI context. Both sides
        # of this comparison must be physical client pixels, as at startup.
        with Geometry(self.win).pixels():
            self._guard_physical(expected)

    def _guard_physical(self, expected):
        deadline = time.monotonic() + 3
        stop = getattr(self, 'geometry_stop', threading.Event())
        transient = ('游戏窗口客户区无效', '无法读取游戏客户区',
                     '第一版禁止最小化运行，请恢复游戏窗口')
        report = dict(expected_client_size=list(expected), actual_client_size=None,
                      coordinate_space='physical_pixels', recovery_attempts=0)
        self.window_geometry['runtime_check'] = report
        recovering, stable = False, 0
        while True:
            if stop.is_set():
                raise InterruptedError('窗口恢复等待已停止')
            try:
                observed = self.win.size(self.window.hwnd)
                report['actual_client_size'] = list(observed)
                self.win.check(self.window, expected)
                stable += 1
                if not recovering or stable >= 2:
                    report['status'] = 'recovered' if recovering else 'valid'
                    return
            except RuntimeError as exc:
                if str(exc) not in transient:
                    raise
                # An invalid client rect must not hide process exit/reuse.
                pid, exe = self.win.identity(self.window.hwnd)
                if pid != self.window.pid or exe.casefold() != self.window.executable.casefold():
                    raise RuntimeError('目标窗口的进程身份发生变化') from exc
                report.update(actual_client_size=None, error=str(exc), status='waiting')
                stable = 0
                if not recovering:
                    getattr(self, 'emit', lambda *args: None)('log',
                        '游戏窗口暂不可用，暂停操作最多 3 秒；恢复后重新截图')
                recovering = True
                # Even a successful recovery cannot authorize an old click.
                self.last_shape = None
                self.last_capture = 0
            if time.monotonic() >= deadline:
                report['status'] = 'timed_out'
                raise RuntimeError('游戏窗口 3 秒内未恢复稳定；请恢复窗口后重新连接')
            report['recovery_attempts'] += 1
            stop.wait(.1)

    def capture(self, stop):
        # Upstream clears recognition_trace when capturing. The watchdog may save
        # immediately after that capture, before any rules have been evaluated.
        if self.recognition_trace and self.last_image is not None:
            self._previous_recognition = copy.deepcopy(self.recognition_trace)
            self._previous_image = self.last_image.copy()
            self._previous_capture = self.last_capture
        try:
            return self._capture_valid(stop)
        except CaptureRecoveryTimeout:
            if self.capture_method != 'FramePool' or getattr(self, 'capture_reconnects', 0) >= 1:
                raise
            first = copy.deepcopy(self.capture_health)
            self.capture_reconnects = 1
            self.last_shape, self.last_capture = None, 0
            getattr(self, 'emit', lambda *args: None)('log',
                'FramePool 持续黑屏，暂停点击并重建截图连接一次；仍需连续两帧有效画面')
            try:
                self.reconnect_capture(stop)
                return self._capture_valid(stop, require_pair=True)
            finally:
                self.capture_health.update(reconnects=1, initial_failure=first)

    def reconnect_capture(self, stop):
        from maa.controller import Win32Controller
        from maa.define import MaaWin32ScreencapMethodEnum, MaaWin32InputMethodEnum
        if stop.is_set():raise InterruptedError('截图重连已停止')
        self.guard()
        controller = Win32Controller(self.window.hwnd,
            screencap_method=getattr(MaaWin32ScreencapMethodEnum, self.capture_method),
            mouse_method=getattr(MaaWin32InputMethodEnum, self.input_method),
            keyboard_method=getattr(MaaWin32InputMethodEnum, self.input_method))
        job = controller.post_connection()
        self.wait(job, stop, timeout=3)
        self.guard()
        if not job.succeeded or not controller.set_screenshot_target_short_side(720):
            raise RuntimeError('截图连接重建失败，停止操作')
        if stop.is_set():raise InterruptedError('截图重连已停止')
        if not self.tasker.bind(self.resource, controller):
            raise RuntimeError('截图连接重新绑定失败，停止操作')
        self.controller = controller

    def _capture_valid(self, stop, require_pair=False):
        started = time.monotonic()
        deadline = started + self.CAPTURE_RECOVERY_SECONDS
        health = self.capture_health = dict(attempts=0, rejected_frames=0,
            consecutive_valid=0, status='capturing', elapsed_seconds=0.)
        self.guard()
        if self.capture_method == 'ScreenDC':
            self.win.foreground(self.window.hwnd)
        for attempt in range(self.CAPTURE_MAX_ATTEMPTS):
            if stop.is_set():
                raise InterruptedError('截图等待已停止')
            self.guard()
            remaining = deadline-time.monotonic()
            if remaining <= 0:
                break
            # A blank image must invalidate the preceding click coordinates.
            self.last_shape = None
            job = self.controller.post_screencap()
            self.wait(job, stop, timeout=remaining)
            self.guard()
            if not job.succeeded:
                raise RuntimeError('截图失败，请切换 FramePool / ScreenDC')
            image = job.get()
            if image is None or image.ndim != 3 or min(image.shape[:2]) < 64:
                raise RuntimeError('截图为空或尺寸异常')
            self.last_image = image.copy()
            self.recognition_trace = []
            self.last_capture_attempt = time.monotonic()
            health['attempts'] = attempt+1
            health['elapsed_seconds'] = round(time.monotonic()-started, 4)
            if self.is_nearly_uniform(image):
                health['rejected_frames'] += 1
                health['consecutive_valid'] = 0
                health['status'] = 'waiting_for_valid_frames'
                if health['rejected_frames'] == 1:
                    getattr(self,'emit',lambda *args:None)('log',
                        '截图出现纯色画面，暂停点击并等待恢复（最多 6 秒；连续两帧有效后继续）')
            else:
                health['consecutive_valid'] += 1
                if health['consecutive_valid'] >= (2 if health['rejected_frames'] or require_pair else 1):
                    if time.monotonic() >= deadline:
                        break
                    health['status'] = 'recovered' if health['rejected_frames'] else 'valid'
                    self.last_shape = image.shape[:2]
                    self.last_capture = self.last_capture_attempt
                    if health['rejected_frames']:
                        getattr(self,'emit',lambda *args:None)('log',
                            f"截图已恢复：跳过 {health['rejected_frames']} 帧纯色画面，连续两帧有效")
                    return image
            if stop.wait(min(.2, max(0, deadline-time.monotonic()))):
                raise InterruptedError('截图等待已停止')
        health['status'] = 'timed_out'
        health['elapsed_seconds'] = round(time.monotonic()-started, 4)
        raise CaptureRecoveryTimeout('截图恢复超时：6 秒内未取得连续有效画面，停止而非继续点击')

    def save_diagnostic(self, state, reason, config):
        from PIL import Image
        from .diagnostics import create_bundle, write_bundle_report
        folder = create_bundle(self.diagnostic_root)
        report = dict(version=1, stage='runner', state=state, reason=reason,
            capture_method=getattr(self, 'capture_method', None),
            input_method=getattr(self, 'input_method', None),
            recognition=copy.deepcopy(getattr(self, 'recognition_trace', [])),
            capture_age_seconds=round(time.monotonic()-self.last_capture, 4) if getattr(self, 'last_capture', None) else None,
            execution=copy.deepcopy(getattr(self, 'diagnostic_context', {})), config=config,
            screenshot=None)
        if getattr(self, 'last_image', None) is not None:
            try:
                Image.fromarray(self.last_image[:, :, :3][:, :, ::-1]).save(folder/'screen.png')
                report['screenshot'] = 'screen.png'
            except Exception as exc:
                report['screenshot_error'] = str(exc)
        else:
            report['screenshot_unavailable'] = '当前会话尚未收到截图；诊断不会额外点击或截图'
        report['application'] = version_info()
        report['action_guard'] = copy.deepcopy(getattr(self, 'action_guard_context', {}))
        report['capture_health'] = getattr(self, 'capture_health', None)
        report['window_geometry'] = getattr(self, 'window_geometry', None)
        stamp = getattr(self, 'last_capture_attempt', None)
        report['screenshot_age_seconds'] = None if stamp is None else round(time.monotonic()-stamp, 4)
        previous = getattr(self, '_previous_recognition', None)
        if previous:
            report['previous_frame_recognition'] = {
                'note': '上一个有识别记录的截图；不是本次截图的匹配结果',
                'capture_age_seconds': round(time.monotonic() - self._previous_capture, 4),
                'screenshot': None,
                'recognition': previous,
            }
            try:
                Image.fromarray(self._previous_image[:, :, :3][:, :, ::-1]).save(folder/'previous.png')
                report['previous_frame_recognition']['screenshot'] = 'previous.png'
            except Exception as exc:
                report['previous_frame_recognition']['screenshot_error'] = str(exc)
        return str(write_bundle_report(folder, report))
