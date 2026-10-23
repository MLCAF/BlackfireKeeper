import json
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
from .runtime import Session
from .version import DISPLAY_NAME
from .automation import load_plan, save_plan, run_plan
from vendor_core.unified import BUFF_OPTIONS
from .food import summarize as summarize_food


class App:
    def __init__(self, root):
        self.root = root
        root.title(f'{DISPLAY_NAME} · 地下城挂机控制台')
        root.geometry('1060x790')
        root.minsize(820, 650)
        self.events = queue.Queue()
        self.session = None
        self.worker = None
        self.preparation_thread = None
        self.preparation_stop = threading.Event()
        self.last_snapshot = None
        self.closing = False
        self.preview_image = None
        self.pending_image = None
        self.status = tk.StringVar(value='未连接；点击「连接 / 只读监视」。F8 停止刷本。')
        self.plan = load_plan()
        self.rounds = tk.StringVar(value=str(self.plan['max_total_runs']))
        self.stamina_threshold = tk.StringVar(value=str(self.plan.get('run_control',{}).get('minimum_stamina',100)))
        self.capture = tk.StringVar(value='FramePool')
        self.input_method = tk.StringVar(value='PostMessage')
        self.completed_runs = 0
        self.active_total = None
        from .food import FoodTimerStore
        from .runtime import ROOT
        from .notifications import TaskSounds,load_enabled
        try:sound_enabled=load_enabled(ROOT)
        except (ValueError,OSError) as exc:
            sound_enabled=True;self.emit('log',f'提示音设置读取失败，保留原文件：{exc}')
        self.task_sounds=TaskSounds(ROOT,sound_enabled,log=lambda message:self.emit('log',message))
        self.food_next_check = FoodTimerStore(ROOT/'config/runtime/food_schedule.json').load(time.time())
        self.last_memory_json = None
        from .dashboard import build
        build(self)
        from .forbidden_panel import ForbiddenPanel
        self.forbidden_panel = ForbiddenPanel(self)
        from .window_panel import WindowPanel
        self.window_panel = WindowPanel(self)
        from .shutdown_panel import ShutdownPanel
        self.shutdown_panel = ShutdownPanel(self)
        root.protocol('WM_DELETE_WINDOW', self.close)
        root.report_callback_exception = lambda typ, value, tb: self.report_error(value, tb)
        root.after(100, self.drain)

    def emit(self, kind, data):
        if kind == 'image':
            self.pending_image = data
        else:
            self.events.put((kind, data))

    def report_error(self, error, tb=None):
        if (isinstance(error, InterruptedError) and self.session
                and (self.session.stop_event.is_set() or self.session.operation_stop.is_set())):
            self.emit('log', str(error) or '操作已停止')
            return
        if self.session:
            if hasattr(error, 'window_geometry'):
                self.session.diagnostic_context['window_geometry'] = error.window_geometry
            self.session.publish('error', str(error))
        else:
            from .diagnostics import save_failure
            from .runtime import ROOT
            path = save_failure(ROOT, 'gui-startup', 'gui', error)
            self.emit('diagnostic_saved', str(path))
            self.emit('error', str(error))

    def prepare_startup(self):
        from .automation import input_lease, InputLeaseBusy
        from .window_geometry import standardize_open_game
        from .runtime import EXE
        try:
            with input_lease():
                from .window_options import load_resolution
                from .runtime import ROOT
                standardize_open_game(self.preparation_stop, self.emit, EXE, preferred=load_resolution(ROOT))
        except InputLeaseBusy:
            self.emit('log', '其他 BK2 正在操作游戏，已跳过启动时的尺寸调整；停止原挂机后再开始本窗口的任务')
        except InterruptedError:
            pass
        except Exception as exc:
            self.report_error(exc)

    def open_diagnostics(self):
        import os
        from .runtime import ROOT
        folder = ROOT/'logs/diagnostics'
        folder.mkdir(parents=True, exist_ok=True)
        os.startfile(folder)

    def copy_diagnostic(self):
        from .runtime import ROOT
        latest = ROOT/'logs/diagnostics/latest.txt'
        if not latest.is_file():
            self.status.set('目前没有错误报告；发生错误后会自动保存。')
            return
        path = latest.read_text(encoding='utf-8')
        self.root.clipboard_clear()
        self.root.clipboard_append(path)
        self.status.set('已复制最新错误报告路径。')

    def select_dungeons(self, mode):
        if self.worker and self.worker.is_alive():
            return
        for task in self.plan['tasks']:
            self.task_flags[task['id']].set(bool(task.get('entrance_template'))
                and (mode == 'all' or (mode == 'first' and task['order'] == 1)))

    @staticmethod
    def timer_text(value):
        import math
        seconds = max(0, math.ceil(value.get('remaining_seconds', 0)))
        return f'{seconds//3600:02}:{seconds//60%60:02}:{seconds%60:02}'

    def refresh_dashboard(self):
        from .dashboard import GREEN, GOLD, MUTED
        from .cooldowns import summarize
        snapshot = self.last_snapshot
        fresh = bool(snapshot and 0 <= time.monotonic() - snapshot['captured_monotonic'] <= 3)
        connecting = bool(self.session and self.session.thread and self.session.thread.is_alive()
                          and not self.session.ready.is_set())
        if fresh:
            self.connection_text.set(f"●  已连接  ·  PID {snapshot['pid']}")
            self.connection_label.configure(fg=GREEN)
            timers = summarize(snapshot['data'].get('cooldown_fields'))
        else:
            self.connection_text.set('●  正在读取内存…' if connecting else '●  未连接 / 状态失效')
            self.connection_label.configure(fg=GOLD if connecting else MUTED)
            timers = summarize({})
        for task in self.plan['tasks']:
            state, timer, label = self.task_rows[task['id']]
            value = timers['dungeons'].get(task['id'], {'status': 'unknown'})
            if not task.get('entrance_template'):
                state.set('缺少入口图'); timer.set('—'); label.configure(fg=MUTED)
            elif value['status'] == 'unknown':
                state.set('未知' if fresh else '未连接'); timer.set('—'); label.configure(fg=MUTED)
            else:
                ready = value['status'] == 'ready'
                state.set('可进入' if ready else '冷却中')
                timer.set('00:00:00' if ready else self.timer_text(value))
                label.configure(fg=GREEN if ready else GOLD)
        value = timers['diamonds']
        food = summarize_food(snapshot['data'].get('food_fields')) if fresh else summarize_food({})
        self.food_text.set(f"{food['count']} / {food['capacity']}" if food['count'] is not None else '— / 320')
        self.food_label.configure(fg=GREEN if food['count'] is not None else MUTED)
        if food['remaining_seconds'] is None:
            self.food_hint.set(food['reason'] if fresh else '等待连接 · 内存失效时不领取')
        else:
            timer = self.timer_text(food)
            self.food_hint.set(f"已制作 {food['produced']}/160 · 整批剩余 {timer} · "
                               + ('体力已满，暂不领取' if food['status']=='full' else f"可领取 {food['claimable']} 点，副本结束后处理"))
        check_timer = self.timer_text({'remaining_seconds':self.food_next_check-time.time()})
        self.food_hint.set(self.food_hint.get() + '\n' +
            (f'每30分钟检查 · 下次 {check_timer} · 仅挂机运行时执行' if self.food_enabled.get()
             else '自动领取已关闭'))
        if value['status'] == 'unknown':
            self.diamond_text.set('计时未知' if fresh else '等待连接')
            self.diamond_hint.set('暂无有效内存计时' if fresh else '连接后读取实际冷却')
            self.diamond_label.configure(fg=MUTED)
        else:
            ready = value['status'] == 'ready'
            self.diamond_text.set('可领取' if ready else self.timer_text(value))
            self.diamond_label.configure(fg=GREEN if ready else GOLD)
            self.diamond_hint.set('到期后在副本结束处处理' if ready else '距离下次可领取 · 内存读取')
        return fresh

    def connect(self):
        if self.preparation_thread and self.preparation_thread.is_alive():
            self.emit('log', '正在核验游戏窗口尺寸，请稍候再连接')
            return
        if self.worker and self.worker.is_alive():
            self.emit('error', '请先停止刷本，再重新连接')
            return
        if self.session and self.session.thread and self.session.thread.is_alive():
            if not self.session.ready.is_set():
                self.emit('log', '正在定位内存，请等待；关闭窗口可取消。')
                return
            self.session.stop()
            self.session.thread.join(timeout=2)
            if self.session.thread.is_alive():
                self.emit('error', '旧连接正在停止，请稍后重试')
                return
        self.last_snapshot = None
        self.session = Session(self.emit)
        self.session.start()
        self.status.set('正在连接并扫描内存…')

    def start_block_reason(self):
        snapshot = self.last_snapshot
        if not snapshot or not 0 <= time.monotonic()-snapshot['captured_monotonic'] <= 3:
            return '请先连接游戏并等待有效内存状态'
        if (snapshot['data'].get('dungeon_progress') or {}).get('result') == 'in progress':
            from .recovery import active_task
            try:
                active_task(snapshot['data'], self.plan['tasks'])
            except ValueError as exc:
                return str(exc)
        return None

    def start(self, dry_run):
        if getattr(self, 'forbidden_panel', None) and self.forbidden_panel.selected():
            return self.forbidden_panel.start(dry_run)
        if self.worker and self.worker.is_alive():
            return
        blocked = self.start_block_reason()
        if blocked:
            self.status.set(blocked)
            self.emit('log', blocked)
            return
        if not self.session or not self.session.ready.is_set() or self.session.error:
            self.emit('error', '请先连接游戏并等待内存状态显示')
            return
        try:
            plan = self.collect_plan()
        except (ValueError, KeyError) as exc:
            self.report_error(exc)
            return
        capture = self.capture.get()
        input_method = self.input_method.get()
        session = self.session
        session.operation_stop.clear()
        self.completed_runs = 0
        self.active_total = None
        self.completed_text.set('已完成 0 轮')
        self.progress['value'] = 0
        self.task_text.set('识别预览 · 不点击游戏' if dry_run else '正在准备本次任务')
        def work():
            try:
                run_plan(session, plan, dry_run, capture, input_method)
            except Exception as exc:
                self.report_error(exc)
        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()
        selected = any(t['enabled'] for t in plan['tasks'])
        self.status.set('识别预览中，不点击' if dry_run else
                        ('挂机运行中，F8 停止' if selected else '仅领取模式运行中，F8 停止'))

    def stop_run(self):
        if self.session:
            self.session.stop_automation('用户停止')
        else:
            self.emit('log', '当前没有正在运行的刷本任务')

    def collect_plan(self):
        from .plan import validate_dungeon_plan
        from .runtime import ROOT
        plan = json.loads(json.dumps(self.plan))
        plan['max_total_runs'] = int(self.rounds.get())
        plan['run_control'] = {'mode':'stamina','minimum_stamina':int(self.stamina_threshold.get())}
        for task in plan['tasks']:
            task['enabled'] = self.task_flags[task['id']].get()
            task['buff'] = next(k for k, v in BUFF_OPTIONS.items() if v['label'] == self.task_buffs[task['id']].get())
        plan['diamonds'].update(enabled=self.diamonds_enabled.get(), run_on_start=self.diamonds_immediate.get())
        plan['food'] = {'enabled': self.food_enabled.get(), 'interval_seconds':1800}
        from .automation import validate_food_options
        validate_food_options(plan)
        return validate_dungeon_plan(plan, ROOT / 'config/profiles', ROOT / 'assets/image', True)

    def save_options(self):
        if getattr(self, 'forbidden_panel', None) and self.forbidden_panel.selected():
            return self.forbidden_panel.save()
        if self.worker and self.worker.is_alive():
            return
        try:
            plan = self.collect_plan()
            save_plan(plan)
            self.plan = plan
            self.emit('log', '设置已保存；保留副本、Buff、钻石、体力阈值和自动领取选项。')
        except Exception as exc:
            self.report_error(exc)

    def confirm_diamonds(self):
        if not self.session or not self.session.waiting_for_diamonds:
            return
        if messagebox.askyesno('确认手动领取', '是否已经手动领取钻石并返回地下城列表？\n确认后才重新计时两小时。'):
            self.session.acknowledge_diamonds()

    def show_memory(self, snapshot):
        self.last_snapshot = snapshot
        if self.status.get().startswith('正在连接'):
            self.status.set(f"已连接 PID {snapshot['pid']}，只读监视中；尚未开始刷本。")
        data = snapshot['data']
        from .cooldowns import summarize
        timers = summarize(data.get('cooldown_fields'))
        def label(value):
            if value['status'] == 'unknown': return '未知'
            seconds = int(value['remaining_seconds'])
            return '可用' if value['status'] == 'ready' else f'{seconds//3600:02}:{seconds//60%60:02}:{seconds%60:02}'
        items = [f"{t['display_name']} {label(timers['dungeons'][t['id']])}" for t in self.plan['tasks']]
        self.cooldown_status.set('内存冷却：' + ' · '.join(items) + '\n免费钻石：' + label(timers['diamonds']))
        display = {'读取来源': '游戏进程内存', '进程': snapshot['pid'],
                   '实时体力': summarize_food(data.get('food_fields')),
                   '冷却时间': timers,
                   '副本': data.get('dungeon_progress'), '普通关卡': data.get('level_battle'),
                   '角色数量': data['heroes']['count'], '队伍': data['heroes']['parties'],
                   '最近缓存奖励（不代表本次到账）': data.get('cached_reward_items', []),
                   '背包普通物品汇总': data.get('bag_item_totals', {})}
        self.memory_text.delete('1.0', 'end')
        self.memory_text.insert('end', json.dumps(display, ensure_ascii=False, indent=2))
        self.refresh_dashboard()

    def drain(self):
        self.shutdown_panel.refresh()
        for _ in range(200):
            try:
                kind, data = self.events.get_nowait()
            except queue.Empty:
                break
            if not self.closing:self.task_sounds.observe(kind,data)
            if kind in ('memory', 'memory_change'):
                self.show_memory(data)
            elif kind == 'stats':
                if data.get('mode') == '禁域':
                    self.forbidden_panel.progress.set(f"{data['state']} · 已完成 {data['rounds']} 次 · 点击 {data['clicks']} 次")
                self.status.set(f"{data['mode']} · 流程 {data['state']} · 已确认 {data['rounds']} 轮 · 点击 {data['clicks']} 次")
            else:
                text = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
                self.logs.insert('end', time.strftime('%H:%M:%S ') + f'[{kind}] {text}\n', kind)
                self.logs.see('end')
                if int(self.logs.index('end-1c').split('.')[0]) > 1500:
                    self.logs.delete('1.0', '301.0')
                if kind in ('error', 'memory_error', 'memory_cancelled', 'finished'):
                    self.status.set(text)
                    if kind in ('error', 'memory_error') and self.session and self.session.diagnostic_context.get('mode') == 'forbidden':
                        self.forbidden_panel.progress.set('禁域已停止：' + text)
                    if kind in ('memory_error', 'memory_cancelled'):
                        self.cooldown_status.set('冷却：内存读取失效，状态未知')
                        self.last_snapshot = None
                        self.memory_text.delete('1.0', 'end')
                        self.memory_text.insert('end', '状态未知：' + text)
                elif kind == 'manual_diamonds':
                    self.status.set(text)
                elif kind == 'schedule':
                    self.status.set(f"轮换：{data['task']} · {data['phase']} · 共完成 {data.get('completed_runs', 0)} 次")
                    phase = {'running': '正在刷本', 'completed': '本轮完成',
                             'waiting': '等待冷却', 'failed': '任务停止',
                             'skipped': '本次已跳过，继续轮换',
                             'rewards_only': '等待领取任务到期'}.get(data['phase'], data['phase'])
                    if data.get('kind') == 'stamina':
                        phase = f"{data['stamina']} / 阈值 {data['threshold']}，补充后自动继续"
                    elif data.get('kind') == 'food':
                        phase = '正在领取'
                    self.task_text.set(f"{data['task']} · {phase}")
                    self.completed_runs = data.get('completed_runs', self.completed_runs)
                elif kind == 'food_schedule':
                    self.food_next_check = data['next_check']
                elif kind == 'batch_result':
                    if self.session and self.session.diagnostic_context.get('mode') == 'forbidden':
                        self.forbidden_panel.progress.set(f"{data['reason']} · 已完成 {data['completed_runs']} 次")
                    self.status.set(f"{data['reason'] or '已停止'} · 共完成 {data['completed_runs']} 次")
                    self.task_text.set('本次任务完成' if data['succeeded'] else '本次任务已停止')
                    self.completed_runs = data['completed_runs']
                elif kind == 'diagnostic_saved':
                    from pathlib import Path
                    self.diagnostic_text.set('错误报告已保存\n' + Path(str(data)).parent.name)
                elif kind == 'diagnostic_save_error':
                    self.diagnostic_text.set('诊断写入失败，请查看运行日志')
        if self.active_total:
            self.completed_text.set(f'{self.completed_runs} / {self.active_total}')
            self.progress['value'] = min(100, self.completed_runs / self.active_total * 100)
        else:
            self.completed_text.set(f'已完成 {self.completed_runs} 轮')
            fresh = bool(self.last_snapshot and 0 <= time.monotonic()-self.last_snapshot['captured_monotonic'] <= 3)
            value = summarize_food(self.last_snapshot['data'].get('food_fields') if fresh else {})
            self.progress['value'] = 0 if value['count'] is None else value['count']/320*100
        if self.pending_image is not None:
            from PIL import Image, ImageTk
            frame, self.pending_image = self.pending_image, None
            im = Image.fromarray(frame[:, :, :3][:, :, ::-1])
            im.thumbnail((max(100, self.screen.winfo_width()), max(100, self.screen.winfo_height())))
            self.preview_image = ImageTk.PhotoImage(im)
            self.screen.configure(image=self.preview_image)
        busy = bool((self.worker and self.worker.is_alive()) or
                    (self.preparation_thread and self.preparation_thread.is_alive()))
        self.save_button.configure(state='disabled' if busy else 'normal')
        self.connect_button.configure(state='disabled' if busy or self.closing else 'normal')
        for widget in self.settings_widgets:
            if isinstance(widget, ttk.Combobox):
                widget.configure(state='disabled' if busy else 'readonly')
            else:
                widget.configure(state='disabled' if busy else 'normal')
        waiting = bool(self.session and self.session.waiting_for_diamonds and not self.session.operation_stop.is_set())
        self.manual_button.configure(state='normal' if waiting else 'disabled')
        if waiting:
            self.manual_button.pack(fill='x', pady=(10, 0))
        else:
            self.manual_button.pack_forget()
        fresh = self.refresh_dashboard()
        self.forbidden_panel.refresh(fresh, busy)
        self.window_panel.refresh(busy)
        blocked = self.start_block_reason()
        for button in (self.preview_button, self.run_button):
            button.configure(state='normal' if fresh and not busy and not blocked else 'disabled')
        if not busy:
            if blocked:
                self.task_text.set(blocked)
                self._start_blocked = True
            elif getattr(self, '_start_blocked', False):
                self.task_text.set('已满足启动条件，可以开始挂机')
                self._start_blocked = False
        if self.closing:
            observing = bool(self.session and self.session.thread and self.session.thread.is_alive())
            if not busy and not observing:
                self.root.destroy()
                return
        self.root.after(100, self.drain)

    def close(self):
        self.closing = True
        self.preparation_stop.set()
        if self.session:
            self.session.stop()
        self.status.set('正在停止读取和操作…')


def launch():
    root = tk.Tk()
    app = App(root)
    from .update_notice import show_update_notice
    show_update_notice(root)
    if app.closing:
        return
    app.preparation_thread = threading.Thread(target=app.prepare_startup, daemon=True)
    app.preparation_thread.start()
    root.mainloop()
