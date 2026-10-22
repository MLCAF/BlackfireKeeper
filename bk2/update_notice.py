"""Bundled release summary, displayed on every interactive GUI launch."""
from tkinter import messagebox
from .version import DISPLAY_NAME

UPDATE_TEXT = '''本次新增：

1. 定时关机：在“定时关机”页设置多少分钟后，或指定 HH:MM。
2. 到点停止挂机，等待 60 秒后请求 Windows 关机；倒计时内可取消。
3. 点击“停止 / F8”或关闭脚本，可取消尚未提交的关机计划。
4. 每次启动主界面显示本次更新说明。

关机计划默认关闭，仅在本次运行有效。任务完成或报错不会自动取消计划。
不会强制关闭其他应用，未保存的文档可能阻止关机。
已有挂机设置和冷却记录保留。

关闭本提示后继续使用。'''


def show_update_notice(parent):
    # No read-once flag, network access, config writes or game operations.
    messagebox.showinfo(f'{DISPLAY_NAME} · 本次更新', UPDATE_TEXT, parent=parent)
