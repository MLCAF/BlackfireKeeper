"""Native Tk dashboard layout. No game IO or scheduler decisions in this module."""
import tkinter as tk
from tkinter import ttk, scrolledtext
from vendor_core.unified import BUFF_OPTIONS
from .version import DISPLAY_NAME

BG = '#101722'
PANEL = '#192331'
ROW = '#202c3c'
INK = '#eef3f9'
MUTED = '#9baec3'
GREEN = '#63d7b0'
GOLD = '#f2c879'
RED = '#f28b96'
FONT = 'Microsoft YaHei UI'


def text(parent, value=None, var=None, size=10, color=INK, bold=False, **kw):
    return tk.Label(parent, text=value, textvariable=var, bg=parent.cget('bg'), fg=color,
                    font=(FONT, size, 'bold' if bold else 'normal'), anchor='w', **kw)


def button(parent, title, command, primary=False):
    return ttk.Button(parent, text=title, command=command,
                      style='Primary.TButton' if primary else 'TButton')


def card(parent, title):
    frame = tk.Frame(parent, bg=PANEL, padx=16, pady=12)
    text(frame, title, size=11, bold=True).pack(anchor='w', pady=(0, 8))
    return frame


def build(app):
    root = app.root
    root.configure(bg=BG)
    root.title(f'{DISPLAY_NAME} · 地下城挂机控制台')
    root.geometry('1180x790')
    root.minsize(1080, 790)
    root.option_add('*Font', (FONT, 10))
    root.option_add('*TCombobox*Listbox.background', ROW)
    root.option_add('*TCombobox*Listbox.foreground', INK)
    style = ttk.Style(root)
    style.theme_use('clam')
    style.configure('.', background=PANEL, foreground=INK, font=(FONT, 10))
    style.configure('TButton', background=ROW, foreground=INK, borderwidth=0,
                    padding=(14, 9), focusthickness=1, focuscolor=MUTED)
    style.map('TButton', background=[('active', '#304359'), ('disabled', '#202936')],
              foreground=[('disabled', '#65768a')])
    style.configure('Primary.TButton', background=GREEN, foreground='#102b25')
    style.configure('Compact.TButton', padding=(9, 5))
    style.map('Primary.TButton', background=[('active', '#8ee8c8'), ('disabled', '#29443f')],
              foreground=[('disabled', '#8aa59c')])
    style.configure('TCheckbutton', background=PANEL, foreground=INK, padding=2)
    style.map('TCheckbutton', background=[('active', PANEL)], foreground=[('disabled', '#64778d')])
    style.configure('TCombobox', fieldbackground=ROW, background=ROW, foreground=INK,
                    arrowcolor=MUTED, padding=5, borderwidth=0, bordercolor=ROW,
                    lightcolor=ROW, darkcolor=ROW)
    style.map('TCombobox', fieldbackground=[('readonly', ROW), ('disabled', PANEL)],
              foreground=[('readonly', INK), ('disabled', '#65768a')],
              selectbackground=[('readonly', ROW)], selectforeground=[('readonly', INK)])
    style.configure('TEntry', fieldbackground=ROW, foreground=INK, insertcolor=INK, padding=7,
                    bordercolor=ROW, lightcolor=ROW, darkcolor=ROW)
    style.configure('TNotebook', background=BG, borderwidth=0, bordercolor=BG,
                    lightcolor=BG, darkcolor=BG)
    style.configure('TNotebook.Tab', background=BG, foreground=MUTED, padding=(20, 10),
                    bordercolor=BG, lightcolor=BG, darkcolor=BG)
    style.map('TNotebook.Tab', background=[('selected', PANEL)], foreground=[('selected', GREEN)])
    style.configure('Horizontal.TProgressbar', troughcolor=ROW, background=GREEN,
                    borderwidth=0, thickness=7, bordercolor=ROW, lightcolor=ROW, darkcolor=ROW)

    outer = tk.Frame(root, bg=BG, padx=24, pady=20)
    outer.pack(fill='both', expand=True)
    header = tk.Frame(outer, bg=BG); header.pack(fill='x')
    brand = tk.Frame(header, bg=BG); brand.pack(side='left')
    text(brand, DISPLAY_NAME, size=23, bold=True).pack(anchor='w')
    text(brand, '选择副本，查看冷却，让领取与冒险按顺序进行。', color=MUTED).pack(anchor='w', pady=(5, 0))
    app.connection_text = tk.StringVar(value='●  未连接游戏')
    app.connection_label = text(header, var=app.connection_text, color=MUTED)
    app.connection_label.pack(side='right', anchor='n', pady=12)

    toolbar = tk.Frame(outer, bg=BG); toolbar.pack(fill='x', pady=(20, 14))
    app.connect_button = button(toolbar, '连接游戏 · 只读', app.connect)
    app.connect_button.pack(side='left', padx=(0, 8))
    app.run_button = button(toolbar, '开始挂机', lambda: app.start(False), True)
    app.run_button.pack(side='left', padx=(0, 8))
    app.preview_button = button(toolbar, '识别预览', lambda: app.start(True))
    app.preview_button.pack(side='left', padx=(0, 8))
    app.stop_button = button(toolbar, '停止  /  F8', app.stop_run)
    app.stop_button.pack(side='left')
    app.save_button = button(toolbar, '保存设置', app.save_options)
    app.save_button.pack(side='right')
    app.run_button.state(['disabled']); app.preview_button.state(['disabled'])

    app.tabs = ttk.Notebook(outer); app.tabs.pack(fill='both', expand=True)
    plan_tab = tk.Frame(app.tabs, bg=BG, pady=12)
    app.tabs.add(plan_tab, text='挂机面板')
    app.plan_canvas = tk.Canvas(plan_tab, bg=BG, highlightthickness=0)
    scrollbar = ttk.Scrollbar(plan_tab, orient='vertical', command=app.plan_canvas.yview)
    scrollbar.pack(side='right', fill='y')
    app.plan_canvas.pack(side='left', fill='both', expand=True)
    app.plan_canvas.configure(yscrollcommand=scrollbar.set)
    body = tk.Frame(app.plan_canvas, bg=BG)
    app.plan_body = body
    body_id = app.plan_canvas.create_window((0,0), window=body, anchor='nw')
    body.bind('<Configure>', lambda event:app.plan_canvas.configure(scrollregion=app.plan_canvas.bbox('all')))
    app.plan_canvas.bind('<Configure>', lambda event:app.plan_canvas.itemconfigure(body_id,width=event.width))
    body.columnconfigure(0, weight=1)
    body.columnconfigure(1, weight=0, minsize=320)
    left = tk.Frame(body, bg=BG); left.grid(row=0, column=0, sticky='nsew', padx=(0, 14))
    right = tk.Frame(body, bg=BG); right.grid(row=0, column=1, sticky='nsew')
    choices = card(left, '地下城轮换')
    choices.pack(fill='x')
    quick = tk.Frame(choices, bg=PANEL); quick.pack(fill='x', pady=(0, 8))
    app.settings_widgets = []
    for title, mode in [('全部可选', 'all'), ('仅无冷却一本', 'first'), ('清空选择', 'none')]:
        widget = button(quick, title, lambda mode=mode: app.select_dungeons(mode))
        widget.configure(style='Compact.TButton')
        widget.pack(side='left', padx=(0, 6)); app.settings_widgets.append(widget)
    grid = tk.Frame(choices, bg=PANEL); grid.pack(fill='x')
    for col, width in enumerate((120, 135, 105, 110)):
        grid.columnconfigure(col, minsize=width, weight=1 if col == 0 else 0)
    for col, title in enumerate(('副本', '开局 Buff', '状态', '剩余冷却')):
        text(grid, title, color=MUTED, size=9).grid(row=0, column=col, sticky='w', pady=(0, 10))
    app.task_flags, app.task_buffs, app.task_rows = {}, {}, {}
    for index, task in enumerate(app.plan['tasks'], 1):
        key = task['id']
        flag = tk.BooleanVar(value=task['enabled']); app.task_flags[key] = flag
        check = ttk.Checkbutton(grid, text=task['display_name'].split('（')[0], variable=flag)
        check.grid(row=index, column=0, sticky='w', pady=3)
        buff = tk.StringVar(value=BUFF_OPTIONS[task.get('buff', 'experience')]['label'])
        app.task_buffs[key] = buff
        combo = ttk.Combobox(grid, textvariable=buff, values=[v['label'] for v in BUFF_OPTIONS.values()],
                             state='readonly', width=11)
        combo.grid(row=index, column=1, sticky='w', padx=(0, 10))
        available = bool(task.get('entrance_template'))
        if available:
            app.settings_widgets.extend((check, combo))
        else:
            check.state(['disabled']); combo.state(['disabled'])
        state = tk.StringVar(value='未连接' if available else '缺少入口图')
        timer = tk.StringVar(value='—')
        state_label = text(grid, var=state, color=MUTED, size=9)
        state_label.grid(row=index, column=2, sticky='w')
        text(grid, var=timer, size=12, bold=True).grid(row=index, column=3, sticky='w')
        app.task_rows[key] = (state, timer, state_label)
    text(choices, '只刷勾选副本；全部冷却时等待。\n一本可取消，开局 Buff 按每个副本独立设置。',
         color=MUTED, size=9, justify='left').pack(anchor='w', pady=(8, 0))

    settings = tk.Frame(left, bg=PANEL, padx=16, pady=12)
    settings.pack(fill='x', pady=(12, 0))
    line = tk.Frame(settings, bg=PANEL); line.pack(fill='x')
    text(line, '体力低于此值时等待', color=MUTED).pack(side='left', padx=(0, 10))
    entry = ttk.Entry(line, textvariable=app.stamina_threshold, width=5)
    app.stamina_entry = entry
    entry.pack(side='left', padx=(0, 18)); app.settings_widgets.append(entry)
    for var, values in [(app.capture, ['FramePool', 'ScreenDC']),
                        (app.input_method, ['PostMessage', 'SendMessage', 'Seize'])]:
        combo = ttk.Combobox(line, textvariable=var, values=values, state='readonly', width=12)
        combo.pack(side='left', padx=(0, 8)); app.settings_widgets.append(combo)
    text(settings, '达到阈值后自动继续；不限制轮数。F8 停止全部自动操作。',
         color=MUTED, size=9).pack(anchor='w', pady=(10, 0))

    food = card(right, '实时体力 · 面包工坊')
    food.pack(fill='x')
    food_line = tk.Frame(food, bg=PANEL); food_line.pack(fill='x')
    app.food_text = tk.StringVar(value='— / 320')
    app.food_label = text(food_line, var=app.food_text, size=21, color=GREEN, bold=True)
    app.food_label.pack(side='left')
    app.food_enabled = tk.BooleanVar(value=app.plan.get('food', {}).get('enabled', True))
    check = ttk.Checkbutton(food_line, text='自动领取免费体力', variable=app.food_enabled)
    check.pack(side='left', padx=(12,0)); app.settings_widgets.append(check)
    app.food_hint = tk.StringVar(value='每30分钟检查领取，刷本中到期则等本轮结束')
    text(food, var=app.food_hint, size=9, color=MUTED, wraplength=280,
         justify='left').pack(anchor='w', pady=(5,0))

    diamond = card(right, '免费钻石')
    diamond.pack(fill='x', pady=(12,0))
    app.diamond_text = tk.StringVar(value='等待连接')
    app.diamond_label = text(diamond, var=app.diamond_text, size=27, color=GREEN, bold=True)
    app.diamond_label.pack(anchor='w')
    app.diamond_hint = tk.StringVar(value='连接后读取实际冷却')
    text(diamond, var=app.diamond_hint, color=MUTED, size=9, wraplength=282,
         justify='left').pack(anchor='w', pady=(4, 12))
    app.diamonds_enabled = tk.BooleanVar(value=app.plan['diamonds']['enabled'])
    app.diamonds_immediate = tk.BooleanVar(value=app.plan['diamonds']['run_on_start'])
    for title, var in [('自动领取免费钻石', app.diamonds_enabled),
                       ('无计时记录时，启动即检查', app.diamonds_immediate)]:
        check = ttk.Checkbutton(diamond, text=title, variable=var)
        check.pack(anchor='w', pady=3); app.settings_widgets.append(check)
    text(diamond, '刷本中到期 → 完成本轮后优先领取', size=9, color=MUTED).pack(anchor='w', pady=(8, 0))
    app.manual_button = button(diamond, '已手动领取，继续', app.confirm_diamonds)
    app.manual_button.state(['disabled'])  # Shown only for legacy manual handoff.

    progress = card(right, '完成统计 · 当前体力比例')
    progress.pack(fill='x', pady=(12, 0))
    app.completed_text = tk.StringVar(value='0 / —')
    text(progress, var=app.completed_text, size=25, bold=True).pack(anchor='w')
    app.progress = ttk.Progressbar(progress, maximum=100)
    app.progress.pack(fill='x', pady=(10, 10))
    app.task_text = tk.StringVar(value='尚未开始挂机')
    text(progress, var=app.task_text, color=MUTED, wraplength=280, justify='left').pack(anchor='w')
    diagnostic = tk.Frame(right, bg=PANEL, padx=16, pady=12)
    diagnostic.pack(fill='both', expand=True, pady=(12, 0))
    app.diagnostic_text = tk.StringVar(value='报错时自动保存状态与已有截图')
    text(diagnostic, var=app.diagnostic_text, color=MUTED, size=9, wraplength=280,
         justify='left').pack(anchor='w', pady=(0, 10))
    for title, command in [('打开诊断目录', app.open_diagnostics), ('复制最新报告路径', app.copy_diagnostic)]:
        widget = button(diagnostic, title, command)
        widget.configure(style='Compact.TButton'); widget.pack(fill='x', pady=3)

    screen_tab = tk.Frame(app.tabs, bg=BG); app.tabs.add(screen_tab, text='游戏画面')
    app.screen = tk.Label(screen_tab, bg=BG, fg=MUTED, text='开始识别预览或挂机后，显示最近一次游戏截图。', font=(FONT, 11))
    app.screen.pack(fill='both', expand=True)
    memory_tab = tk.Frame(app.tabs, bg=BG); app.tabs.add(memory_tab, text='内存详情')
    log_tab = tk.Frame(app.tabs, bg=BG); app.tabs.add(log_tab, text='运行日志')
    for parent, name in [(memory_tab, 'memory_text'), (log_tab, 'logs')]:
        widget = scrolledtext.ScrolledText(parent, bg=BG, fg=INK, insertbackground=INK,
                    selectbackground='#30516a', relief='flat', padx=14, pady=12,
                    font=('Consolas' if name == 'memory_text' else FONT, 10), wrap='word')
        widget.pack(fill='both', expand=True); setattr(app, name, widget)
    app.logs.tag_configure('error', foreground=RED)
    app.logs.tag_configure('diagnostic_saved', foreground=GREEN)
    footer = tk.Frame(outer, bg=BG); footer.pack(side='bottom', fill='x', pady=(10, 0), before=app.tabs)
    text(footer, var=app.status, color=MUTED, size=9, wraplength=1100, justify='left').pack(anchor='w')
    # Retained for programmatic smoke checks and detailed accessibility summaries.
    app.cooldown_status = tk.StringVar(value='冷却：等待连接游戏')
