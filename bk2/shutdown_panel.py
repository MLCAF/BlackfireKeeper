"""Shutdown UI; the actual OS request runs off the Tk thread."""
import queue
import os
import threading
import tkinter as tk
from tkinter import ttk
from .shutdown_timer import ShutdownTimer, request_shutdown


class ShutdownPanel:
    def __init__(self, app, submit=None):
        from .dashboard import BG, PANEL, MUTED, card, text, button
        self.app = app
        self.results = queue.Queue()
        self.os_submit = submit or request_shutdown
        self.timer = ShutdownTimer(self.stop_tasks, self.idle, self.submit)
        self.last_message = ''
        self.validation_error = ''
        frame = tk.Frame(app.tabs, bg=BG, pady=12)
        app.tabs.add(frame, text='定时关机')
        body = card(frame, '定时关机 · 本次运行有效'); body.pack(fill='x')
        self.mode = tk.StringVar(value='延时（分钟）')
        self.value = tk.StringVar(value='120')
        row = tk.Frame(body, bg=PANEL); row.pack(fill='x')
        self.choice = ttk.Combobox(row, textvariable=self.mode, state='readonly', width=23,
                                 values=['延时（分钟）', '指定时间（HH:MM）'])
        self.choice.pack(side='left', padx=(0, 10))
        self.choice.bind('<<ComboboxSelected>>', self.change_mode)
        self.entry = ttk.Entry(row, textvariable=self.value, width=12); self.entry.pack(side='left', padx=(0, 10))
        self.arm_button = button(row, '启用 / 更新计划', self.arm, True); self.arm_button.pack(side='left', padx=(0, 10))
        self.cancel_button = button(row, '取消定时关机', self.cancel); self.cancel_button.pack(side='left')
        self.status = tk.StringVar(value=self.timer.message)
        text(body, var=self.status, size=13, bold=True).pack(anchor='w', pady=18)
        text(body, '延时支持 1～10080 分钟；指定时间使用本机 24 小时制，已过时间安排到明天。\n'
                  '到点先停止挂机，再显示 60 秒可取消倒计时，然后请求关机。\n'
                  '停止 / F8、关闭本窗口会取消尚未提交的关机计划。重启后默认不启用。\n'
                  '不会强制关闭其他应用；未保存的文档可能阻止 Windows 关机。\n'
                  '任务完成或报错不会取消计划；电脑休眠时脚本不运行，唤醒后继续检查。',
             color=MUTED, justify='left').pack(anchor='w')
        self.refresh()

    @property
    def blocking(self):
        return self.timer.blocking

    def change_mode(self, event=None):
        self.value.set('23:30' if self.mode.get().startswith('指定') else '120')

    def arm(self):
        if self.app.closing:
            return
        try:
            self.validation_error = ''
            self.timer.arm(self.mode.get(), self.value.get())
            self.app.emit('log', '定时关机已启用：' + self.timer.message)
        except ValueError as exc:
            self.app.emit('log', str(exc))
            self.validation_error = str(exc)
            self.status.set(str(exc))
            return
        self.refresh()

    def cancel(self):
        self.validation_error = ''
        if self.timer.cancel():
            self.app.emit('log', '定时关机已取消')
        self.refresh()

    def stop_tasks(self):
        self.app.preparation_stop.set()
        self.app.stop_run(cancel_shutdown=False)
        self.app.emit('log', '定时关机到点，已请求停止挂机；60 秒内可取消关机')

    def idle(self):
        return not any(t and t.is_alive() for t in (self.app.worker, self.app.preparation_thread))

    def submit(self):
        def work():
            try:
                self.os_submit()
                self.results.put(None)
            except Exception as exc:
                self.results.put(exc)
        threading.Thread(target=work, daemon=True).start()

    def refresh(self):
        if self.timer.state in ('armed', 'grace') and os.name == 'nt':
            import ctypes
            if ctypes.windll.user32.GetAsyncKeyState(0x77) & 0x8000:
                self.app.stop_run()
                return
        try:
            self.timer.submitted(self.results.get_nowait())
        except queue.Empty:
            pass
        self.timer.tick()
        self.status.set(self.validation_error if self.validation_error and not self.blocking else self.timer.message)
        if self.timer.state == 'failed' and self.last_message != self.timer.message:
            self.app.emit('error', self.timer.message)
        elif self.timer.state == 'submitted' and self.last_message != self.timer.message:
            self.app.emit('log', self.timer.message)
        self.last_message = self.timer.message
        blocked = self.blocking or self.app.closing
        self.arm_button.configure(state='disabled' if blocked else 'normal')
        self.choice.configure(state='disabled' if blocked else 'readonly')
        self.entry.configure(state='disabled' if blocked else 'normal')
        self.cancel_button.configure(state='normal' if self.timer.state in ('armed', 'grace') else 'disabled')
