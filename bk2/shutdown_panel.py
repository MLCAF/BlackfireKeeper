"""UI for the Windows-owned task; stopping or closing the app never cancels it."""
import datetime as dt
import math
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk
from .shutdown_timer import WindowsShutdownSchedule


class ShutdownPanel:
    blocking = False

    def __init__(self, app, service=None):
        from .dashboard import BG, PANEL, MUTED, GOLD, card, text, button
        self.app, self.service = app, service or WindowsShutdownSchedule()
        self.results = queue.Queue()
        self.busy, self.next_query = False, 0
        self.record, self.error = None, ''
        frame=tk.Frame(app.tabs,bg=BG,pady=12);app.tabs.add(frame,text='定时关机')
        body=card(frame,'定时关机 · Windows 系统任务');body.pack(fill='x')
        self.mode=tk.StringVar(value='延时（分钟）');self.value=tk.StringVar(value='120')
        row=tk.Frame(body,bg=PANEL);row.pack(fill='x')
        self.choice=ttk.Combobox(row,textvariable=self.mode,state='readonly',width=23,
                                values=['延时（分钟）','指定时间（HH:MM）'])
        self.choice.pack(side='left',padx=(0,10));self.choice.bind('<<ComboboxSelected>>',self.change_mode)
        self.entry=ttk.Entry(row,textvariable=self.value,width=12);self.entry.pack(side='left',padx=(0,10))
        self.arm_button=button(row,'启用 / 更新计划',self.arm,True);self.arm_button.pack(side='left',padx=(0,10))
        self.cancel_button=button(row,'取消定时关机',self.cancel);self.cancel_button.pack(side='left')
        self.status=tk.StringVar(value='正在读取 Windows 定时任务…')
        text(body,var=self.status,size=13,bold=True).pack(anchor='w',pady=18)
        text(body,'到点由 Windows 直接关机，不等待挂机任务结束，也不额外延迟 60 秒。\n'
                  '挂机报错、停止 / F8、关闭或崩溃均不取消计划；只有上方“取消定时关机”会撤销。\n'
                  '会强制关闭应用，请提前保存文档。',color=GOLD,justify='left').pack(anchor='w')
        text(body,'延时支持 1～10080 分钟；指定时间使用本机 24 小时制，已过时间安排到明天。\n'
                  '两版共用当前 Windows 用户的同一个关机任务，重开脚本可查看、更新或取消。\n'
                  '初次使用没有计划时默认关闭；已有系统计划会保留。请保持 Windows 用户登录。\n'
                  '系统会尝试唤醒睡眠中的电脑；关机、断电或不支持唤醒时无法保证准时执行。',
             color=MUTED,justify='left').pack(anchor='w',pady=12)
        self.refresh()

    def change_mode(self,event=None):
        self.value.set('23:30' if self.mode.get().startswith('指定') else '120')

    def request(self,op,*args):
        if self.busy:return
        self.busy=True;self.error=''
        self.status.set('正在设置 Windows 定时任务…' if op=='arm' else '正在读取 / 取消系统任务…')
        def work():
            try:self.results.put((op,getattr(self.service,op)(*args),None))
            except Exception as exc:self.results.put((op,None,str(exc)))
        threading.Thread(target=work,daemon=True).start()

    def arm(self):
        if not self.app.closing:self.request('arm',self.mode.get(),self.value.get())

    def cancel(self):
        if not self.app.closing:self.request('cancel')

    def refresh(self):
        try:
            op,data,error=self.results.get_nowait()
            self.busy=False;self.next_query=time.monotonic()+10
            self.error=error or ''
            if error:self.app.emit('error',error)
            else:
                self.record=data
                if op!='query':self.app.emit('log','Windows 定时关机已启用，停止或关闭脚本不影响计划' if op=='arm' else 'Windows 定时关机已取消')
        except queue.Empty:pass
        if not self.busy and not self.app.closing and time.monotonic()>=self.next_query:
            self.request('query')
        if not self.busy:
            if self.error:self.status.set(self.error)
            elif self.record and self.record.get('registered'):
                target=dt.datetime.fromisoformat(self.record['at'])
                left=max(0,math.ceil(target.timestamp()-time.time()))
                self.status.set(f'{target:%m-%d %H:%M:%S} 系统关机 · 剩余 {left//3600:02}:{left//60%60:02}:{left%60:02}' if left else
                                '计划时间已到 · Windows 任务状态：'+self.record.get('state','未知'))
            else:self.status.set('未启用定时关机')
        disabled=self.busy or self.app.closing
        self.arm_button.configure(state='disabled' if disabled else 'normal')
        self.cancel_button.configure(state='disabled' if disabled else 'normal')
        self.choice.configure(state='disabled' if disabled else 'readonly')
        self.entry.configure(state='disabled' if disabled else 'normal')
