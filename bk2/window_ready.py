"""Bounded window preparation at every backend connection, with diagnostic evidence."""
import ctypes
from ctypes import wintypes as W
from pathlib import Path
import time

from .state import require_fresh

WINDOW_TIMEOUT = 3.0
POLL_SECONDS = 0.1
# These errors originate before MaaBackend creates a controller. Retrying them
# cannot replay a game click or duplicate a partially connected controller.
PRE_CONNECTION_ERRORS = {
    '游戏窗口客户区无效', '无法读取游戏客户区',
    '第一版禁止最小化运行，请恢复游戏窗口',
    '运行中窗口尺寸发生变化，请停止并重新连接',
}


def prepare_window(session, windows, pid):
    started = time.monotonic()
    report = {'pid': pid, 'status': 'checking', 'timeout_seconds': WINDOW_TIMEOUT,
              'observations': [], 'restore_requested': []}
    session.diagnostic_context['window_connection'] = report
    previous = None
    announced = False

    def guard():
        if session.stop_event.is_set() or session.operation_stop.is_set():
            raise InterruptedError('窗口连接等待已停止')
        require_fresh(session.snapshot, pid)

    try:
        while time.monotonic() - started < WINDOW_TIMEOUT:
            guard()
            candidates = [w for w in windows.list_game_windows() if w.pid == pid]
            sample = {'elapsed_seconds': round(time.monotonic()-started, 3),
                      'candidate_count': len(candidates)}
            report['observations'].append(sample)
            if len(candidates) > 1:
                raise RuntimeError('同一游戏进程存在多个可见窗口，无法安全选择')
            current = None
            if candidates:
                window = candidates[0]
                actual_pid, exe = windows.identity(window.hwnd)
                sample.update(hwnd=window.hwnd, actual_pid=actual_pid)
                if (actual_pid != pid or exe.casefold() != window.executable.casefold()
                        or Path(exe).name.casefold() != 'blackfire crusade.exe'):
                    raise RuntimeError('目标窗口的进程身份发生变化')
                rect = W.RECT()
                rect_ok = bool(windows.u.GetClientRect(window.hwnd, ctypes.byref(rect)))
                size = (rect.right-rect.left, rect.bottom-rect.top)
                iconic = bool(windows.u.IsIconic(window.hwnd))
                visible = bool(windows.u.IsWindowVisible(window.hwnd))
                sample.update(minimized=iconic, visible=visible, rect_ok=rect_ok,
                              client_size=list(size))
                if iconic and window.hwnd not in report['restore_requested']:
                    guard()
                    windows.u.ShowWindowAsync.argtypes = [W.HWND, ctypes.c_int]
                    windows.u.ShowWindowAsync.restype = W.BOOL
                    sample['restore_accepted'] = bool(windows.u.ShowWindowAsync(window.hwnd, 9))
                    report['restore_requested'].append(window.hwnd)
                if visible and not iconic and rect_ok and min(size) >= 64:
                    current = (window.hwnd, size)
                    if current == previous:
                        guard()
                        if time.monotonic()-started >= WINDOW_TIMEOUT:
                            break
                        report['status'] = 'ready'
                        report['elapsed_seconds'] = round(time.monotonic()-started, 3)
                        if announced:
                            session.publish('log', '游戏窗口已恢复稳定，继续连接截图后端')
                        return window
            previous = current
            if current is None and not announced:
                session.publish('log', '游戏窗口暂不可用，暂停连接并等待恢复（最多 3 秒，不点击游戏）')
                announced = True
            if session.operation_stop.wait(min(POLL_SECONDS,
                    max(0, WINDOW_TIMEOUT-(time.monotonic()-started)))):
                guard()
        report['status'] = 'timed_out'
        raise RuntimeError('游戏窗口恢复超时：3 秒内未取得连续稳定的有效客户区；请恢复游戏窗口后重新开始挂机')
    except Exception as exc:
        if report['status'] != 'timed_out':
            report['status'] = 'stopped' if isinstance(exc, InterruptedError) else 'failed'
        report['error'] = str(exc)
        report['elapsed_seconds'] = round(time.monotonic()-started, 3)
        raise


def connect_backend(session, windows, pid, constructor):
    """Also cover a minimize/resize race between preparation and construction."""
    attempts = []
    session.diagnostic_context['window_connection_attempts'] = attempts
    for attempt in range(2):
        try:
            window = prepare_window(session, windows, pid)
        finally:
            attempts.append(session.diagnostic_context['window_connection'])
        report = attempts[-1]
        try:
            backend = constructor(window)
        except Exception as exc:
            report.update(status='connection_failed', error=str(exc))
            if type(exc) is not RuntimeError or str(exc) not in PRE_CONNECTION_ERRORS or attempt:
                raise
            session.publish('log', '连接前窗口状态再次变化，重新核验一次；尚未发送游戏输入')
        else:
            report['status'] = 'connected'
            return backend
