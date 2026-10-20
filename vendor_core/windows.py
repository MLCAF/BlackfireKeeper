"""Small Win32 identity/safety layer; capture and input stay in MaaFramework."""

from __future__ import annotations

import ctypes
import os
import time
from ctypes import wintypes as W
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Window:
    hwnd: int
    title: str
    pid: int
    executable: str


class Windows:
    def __init__(self):
        if os.name != "nt":
            raise RuntimeError("第一版仅支持 Windows 10/11 x64")
        self.u = ctypes.WinDLL("user32", use_last_error=True)
        self.k = ctypes.WinDLL("kernel32", use_last_error=True)
        for name, args, result in [
            ("IsWindow", [W.HWND], W.BOOL),
            ("IsWindowVisible", [W.HWND], W.BOOL),
            ("IsIconic", [W.HWND], W.BOOL),
            ("GetWindowThreadProcessId", [W.HWND, ctypes.POINTER(W.DWORD)], W.DWORD),
            ("GetClientRect", [W.HWND, ctypes.POINTER(W.RECT)], W.BOOL),
            ("SetForegroundWindow", [W.HWND], W.BOOL),
            ("GetForegroundWindow", [], W.HWND),
            ("GetAsyncKeyState", [ctypes.c_int], ctypes.c_short),
        ]:
            method = getattr(self.u, name)
            method.argtypes, method.restype = args, result
        self.k.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
        self.k.OpenProcess.restype = W.HANDLE
        self.k.QueryFullProcessImageNameW.argtypes = [W.HANDLE, W.DWORD, W.LPWSTR, ctypes.POINTER(W.DWORD)]
        self.k.QueryFullProcessImageNameW.restype = W.BOOL
        self.k.CloseHandle.argtypes, self.k.CloseHandle.restype = [W.HANDLE], W.BOOL

    def identity(self, hwnd):
        pid = W.DWORD()
        if not self.u.IsWindow(hwnd):
            raise RuntimeError("游戏窗口已关闭")
        self.u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        handle = self.k.OpenProcess(0x1000, False, pid.value)
        if not handle:
            raise RuntimeError("无法读取游戏进程身份，请检查权限")
        try:
            size = W.DWORD(32768)
            buf = ctypes.create_unicode_buffer(size.value)
            if not self.k.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                raise RuntimeError("无法验证目标进程")
            return pid.value, buf.value
        finally:
            self.k.CloseHandle(handle)

    def size(self, hwnd):
        rect = W.RECT()
        if not self.u.GetClientRect(hwnd, ctypes.byref(rect)):
            raise RuntimeError("无法读取游戏客户区")
        size = (rect.right - rect.left, rect.bottom - rect.top)
        if min(size) < 64:
            raise RuntimeError("游戏窗口客户区无效")
        return size

    def list_game_windows(self):
        from maa.toolkit import Toolkit

        result = []
        for win in Toolkit.find_desktop_windows():
            hwnd = win.hwnd.value if isinstance(win.hwnd, ctypes.c_void_p) else win.hwnd
            if not hwnd or not self.u.IsWindowVisible(hwnd):
                continue
            try:
                pid, exe = self.identity(hwnd)
                if Path(exe).name.casefold() == "blackfire crusade.exe":
                    result.append(Window(int(hwnd), win.window_name, pid, exe))
            except RuntimeError:
                continue
        return result

    def check(self, window, original_size):
        pid, exe = self.identity(window.hwnd)
        if pid != window.pid or exe.casefold() != window.executable.casefold():
            raise RuntimeError("目标窗口的进程身份发生变化")
        if Path(exe).name.casefold() != "blackfire crusade.exe":
            raise RuntimeError("禁止向非 Blackfire Crusade 进程发送点击")
        if self.u.IsIconic(window.hwnd):
            raise RuntimeError("第一版禁止最小化运行，请恢复游戏窗口")
        if self.size(window.hwnd) != original_size:
            raise RuntimeError("运行中窗口尺寸发生变化，请停止并重新连接")

    def foreground(self, hwnd):
        if self.u.GetForegroundWindow() != hwnd:
            self.u.SetForegroundWindow(hwnd)
        # Windows may apply activation asynchronously. Never send input before
        # the target really owns focus; do not force focus with simulated keys.
        deadline = time.monotonic() + .3
        while self.u.GetForegroundWindow() != hwnd:
            if not self.u.IsWindow(hwnd) or time.monotonic() >= deadline:
                raise RuntimeError("无法激活游戏窗口，请手动点击游戏后重试")
            time.sleep(.02)

    def emergency_pressed(self):
        return bool(self.u.GetAsyncKeyState(0x77) & 0x8000)  # F8
