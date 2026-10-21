"""Independent Golden Kingdom controls; existing dungeon choices are untouched."""
import json
import threading
import tkinter as tk
from tkinter import ttk
from dataclasses import asdict
from .forbidden import ForbiddenOptions, run_forbidden
from .runtime import ROOT


class ForbiddenPanel:
    def __init__(self, app):
        from .dashboard import BG, PANEL, MUTED, card, text, button
        self.app = app
        self.path = ROOT/'config/runtime/forbidden_options.json'
        self.frame = tk.Frame(app.tabs,bg=BG,pady=12)
        app.tabs.insert(1,self.frame,text='禁域 · 黄金古国')
        options = ForbiddenOptions()
        if self.path.exists():
            try:
                options = ForbiddenOptions(**json.loads(self.path.read_text(encoding='utf8'))).validate()
            except (ValueError,TypeError,OSError) as exc:
                app.emit('log',f'禁域设置读取失败，保留原文件；当前显示默认值：{exc}')
        body = card(self.frame,'禁域挂机 · 黄金古国')
        body.pack(fill='x')
        text(body,'探索 → 自动战斗 / 免费宝箱 → 领取 → 返回探索',size=12,bold=True).pack(anchor='w')
        text(body,'宝箱每次只免费打开一个，其余付费宝箱不点击。',color=MUTED).pack(anchor='w',pady=(8,16))
        row = tk.Frame(body,bg=PANEL);row.pack(fill='x')
        self.rounds = tk.StringVar(value=str(options.rounds))
        self.stamina = tk.StringVar(value=str(options.minimum_stamina))
        self.fields = []
        for title,var in [('探索次数',self.rounds),('体力下限',self.stamina)]:
            text(row,title).pack(side='left',padx=(0,8))
            entry=ttk.Entry(row,textvariable=var,width=7)
            entry.pack(side='left',padx=(0,24));self.fields.append(entry)
        controls=tk.Frame(body,bg=PANEL);controls.pack(fill='x',pady=(20,12))
        self.run_button=button(controls,'开始禁域',lambda:self.start(False),True)
        self.preview_button=button(controls,'禁域识别预览',lambda:self.start(True))
        self.save_button=button(controls,'保存禁域设置',self.save)
        for widget in (self.run_button,self.preview_button,self.save_button):
            widget.pack(side='left',padx=(0,8))
        button(controls,'停止 / F8',app.stop_run).pack(side='left')
        self.progress=tk.StringVar(value='尚未开始')
        text(body,var=self.progress,size=12).pack(anchor='w',pady=8)
        text(body,'从主城、底部导航、黄金古国详情或探索页面开始。\n'
                  '探索耗费 3 点体力；低于下限或达到次数后停止。\n'
                  '禁域使用前台点击，会移动鼠标；请保持游戏可见，运行中不要操作鼠标。\n'
                  '本模式独立运行，期间不轮换地下城、不执行定时领取。\n'
                  '未知页面、战败或结果确认超时会停止并保存诊断。',
             color=MUTED,justify='left').pack(anchor='w',pady=(12,0))

    def selected(self):
        return self.app.tabs.select() == str(self.frame)

    def options(self):
        return ForbiddenOptions(int(self.rounds.get()),int(self.stamina.get())).validate()

    def save(self):
        if self.app.worker and self.app.worker.is_alive():
            return
        try:
            options=self.options()
            self.path.parent.mkdir(parents=True,exist_ok=True)
            tmp=self.path.with_suffix('.tmp')
            tmp.write_text(json.dumps(asdict(options),ensure_ascii=False,indent=2),encoding='utf8')
            tmp.replace(self.path)
            self.app.emit('log','禁域设置已单独保存')
        except Exception as exc:
            self.app.report_error(exc)

    def refresh(self,fresh,busy):
        ready=fresh and not busy and not self.app.closing
        for button in (self.run_button,self.preview_button):
            button.configure(state='normal' if ready else 'disabled')
        for widget in (*self.fields,self.save_button):
            widget.configure(state='disabled' if busy else 'normal')

    def start(self,dry_run):
        app=self.app
        if app.worker and app.worker.is_alive():
            return
        if not app.session or not app.session.ready.is_set() or app.session.error:
            app.emit('log','请先连接游戏并等待内存状态')
            return
        try:
            from .automation import boundary_ready
            boundary_ready(app.session)
            options=self.options()
        except Exception as exc:
            app.report_error(exc)
            return
        # Godot map/detail controls did not acknowledge message-only clicks in
        # live validation. Keep this mode's foreground input independent of the
        # user's existing dungeon input preference and saved plan.
        capture,input_method=app.capture.get(),'Seize'
        session=app.session
        session.operation_stop.clear()
        app.completed_runs=0
        app.active_total=options.rounds
        app.task_text.set('禁域 · 黄金古国')
        self.progress.set('识别预览中' if dry_run else '禁域运行中，F8 停止')
        def work():
            try:
                run_forbidden(session,options,dry_run,capture,input_method)
            except Exception as exc:
                app.report_error(exc)
        app.worker=threading.Thread(target=work,daemon=True)
        app.worker.start()
