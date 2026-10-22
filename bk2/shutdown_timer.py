"""Session-only shutdown plan. Never schedule a persistent OS shutdown job."""
import datetime as dt
import math
import os
import re
import subprocess
import time
from pathlib import Path


def deadline(mode, value, now):
    if mode == '延时（分钟）':
        if not re.fullmatch(r'[0-9]+', value.strip()):
            raise ValueError('分钟数须为 1～10080 的整数')
        minutes = int(value)
        if not 1 <= minutes <= 10080:
            raise ValueError('分钟数须为 1～10080 的整数')
        return now + minutes * 60
    if mode != '指定时间（HH:MM）' or not re.fullmatch(r'([01][0-9]|2[0-3]):[0-5][0-9]', value.strip()):
        raise ValueError('时间须为 24 小时制 HH:MM，例如 23:30')
    hour, minute = map(int, value.split(':'))
    target = dt.datetime.fromtimestamp(now).replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target.timestamp() <= now:
        target += dt.timedelta(days=1)
    return target.timestamp()


def request_shutdown():
    if os.name != 'nt':
        raise RuntimeError('定时关机仅支持 Windows')
    # /t > 0 implicitly forces applications closed. Keep countdown in our UI.
    command = str(Path(os.environ['SystemRoot']) / 'System32' / 'shutdown.exe')
    result = subprocess.run([command, '/s', '/t', '0'], capture_output=True,
                            creationflags=subprocess.CREATE_NO_WINDOW, timeout=15)
    if result.returncode:
        detail = (result.stderr or result.stdout).decode(errors='replace').strip()
        raise RuntimeError(f'Windows 未接受关机请求（{result.returncode}）：{detail}')


class ShutdownTimer:
    def __init__(self, stop, idle, submit, clock=time.time, monotonic=time.monotonic):
        self.stop, self.idle, self.submit = stop, idle, submit
        self.clock, self.monotonic = clock, monotonic
        self.state = 'off'
        self.target = 0
        self.grace = 0
        self.message = '未启用定时关机'

    @property
    def blocking(self):
        return self.state in ('grace', 'submitting', 'submitted')

    def arm(self, mode, value):
        if self.state in ('grace', 'submitting', 'submitted'):
            raise ValueError('请先取消现有倒计时；已提交的请求不能由本界面撤销')
        target = deadline(mode, value, self.clock())
        self.target, self.state = target, 'armed'
        self.tick()

    def cancel(self):
        if self.state not in ('armed', 'grace'):
            return False
        self.state, self.message = 'off', '定时关机已取消'
        return True

    def fail(self, error):
        self.state, self.message = 'failed', f'定时关机已停止：{error}'

    def tick(self):
        try:
            if self.state == 'armed':
                left = math.ceil(self.target - self.clock())
                if left > 0:
                    when = dt.datetime.fromtimestamp(self.target).strftime('%m-%d %H:%M:%S')
                    self.message = f'{when} 停止挂机 · 剩余 {left//3600:02}:{left//60%60:02}:{left%60:02}'
                else:
                    self.state, self.grace = 'grace', self.monotonic() + 60
                    self.stop()
            if self.state == 'grace':
                left = max(0, math.ceil(self.grace - self.monotonic()))
                self.message = f'{left} 秒后请求关机 · 可点击取消或按 F8'
                if left == 0:
                    if not self.idle():
                        raise RuntimeError('挂机或窗口操作尚未退出，不执行关机；请检查运行日志')
                    self.state, self.message = 'submitting', '正在向 Windows 提交关机请求…'
                    self.submit()
        except Exception as exc:
            self.fail(exc)

    def submitted(self, error=None):
        if self.state != 'submitting':
            return
        if error:
            self.fail(error)
        else:
            self.state, self.message = 'submitted', 'Windows 已接受关机请求；未保存的应用可能阻止关机'
