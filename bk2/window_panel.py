"""User-selected client resolution applied under the shared input lease."""
import threading
import tkinter as tk
from tkinter import ttk
from .window_options import CHOICES,load_resolution,save_resolution
from .runtime import ROOT

class WindowPanel:
    def __init__(self,app):
        from .dashboard import BG,PANEL,MUTED,card,text,button
        self.app=app
        frame=tk.Frame(app.tabs,bg=BG,pady=12)
        self.frame=frame
        app.tabs.add(frame,text='窗口设置')
        body=card(frame,'游戏窗口分辨率');body.pack(fill='x')
        try:size=load_resolution(ROOT)
        except (OSError,ValueError) as exc:
            size=None;app.emit('log',f'窗口设置读取失败，保留原文件：{exc}')
        self.value=tk.StringVar(value=next(k for k,v in CHOICES.items() if v==size))
        row=tk.Frame(body,bg=PANEL);row.pack(fill='x')
        self.choice=ttk.Combobox(row,textvariable=self.value,values=list(CHOICES),state='readonly',width=24)
        self.choice.pack(side='left',padx=(0,12))
        self.apply_button=button(row,'应用并保存',self.apply,True);self.apply_button.pack(side='left')
        text(body,'尺寸指游戏客户区的物理像素，不改变系统显示分辨率。\n'
                  '自动适配按所在显示器工作区选择；手动尺寸放不下时会提示，不自动换档。\n'
                  '地下城和禁域共用此选择。运行中不能调整，请先停止挂机。\n'
                  '截图仍统一为 720 短边；各尺寸下的全部游戏页面并未逐一验证。',
             color=MUTED,justify='left').pack(anchor='w',pady=12)
        audio=card(frame,'任务提示音');audio.pack(fill='x',pady=(12,0))
        self.sound_enabled=tk.BooleanVar(value=app.task_sounds.enabled)
        ttk.Checkbutton(audio,text='任务完成或报错时播放提示音',variable=self.sound_enabled,
                        command=self.save_sound).pack(anchor='w')
        preview=tk.Frame(audio,bg=PANEL);preview.pack(fill='x',pady=10)
        button(preview,'试听完成提示音',lambda:app.task_sounds.play('completed')).pack(side='left',padx=(0,10))
        button(preview,'试听报错提示音',lambda:app.task_sounds.play('error')).pack(side='left')
        text(audio,'开关自动保存。整次任务正常结束时提示；手动停止、识别预览不提示完成。\n'
                   '连续报错最多每 10 秒提示一次。试听不受开关影响。',color=MUTED,justify='left').pack(anchor='w')
    def save_sound(self):
        from .notifications import save_enabled
        try:
            enabled=bool(self.sound_enabled.get())
            save_enabled(ROOT,enabled)
            self.app.task_sounds.enabled=enabled
            self.app.emit('log','任务提示音已开启' if enabled else '任务提示音已关闭')
        except Exception as exc:
            self.sound_enabled.set(self.app.task_sounds.enabled)
            self.app.report_error(exc)
    def refresh(self,busy):
        blocked=busy or self.app.closing or bool(self.app.preparation_thread and self.app.preparation_thread.is_alive())
        self.choice.configure(state='disabled' if blocked else 'readonly')
        self.apply_button.configure(state='disabled' if blocked else 'normal')
    def apply(self):
        app=self.app
        if app.closing or (app.worker and app.worker.is_alive()) or (app.preparation_thread and app.preparation_thread.is_alive()):return
        size=CHOICES[self.value.get()]
        from .automation import input_lease
        from .window_geometry import standardize_open_game
        from .runtime import EXE
        app.preparation_stop.clear()
        def work():
            try:
                with input_lease():
                    result=standardize_open_game(app.preparation_stop,app.emit,EXE,preferred=size)
                    if app.preparation_stop.is_set():raise InterruptedError('尺寸调整已停止，未保存')
                    save_resolution(ROOT,size)
                    app.emit('log','游戏窗口尺寸已应用并保存' if result else '窗口尺寸已保存，下次连接或启动识别时应用')
            except InterruptedError as exc:app.emit('log',str(exc))
            except Exception as exc:app.report_error(exc)
        app.preparation_thread=threading.Thread(target=work,daemon=True)
        app.preparation_thread.start()
