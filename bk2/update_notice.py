"""Bundled release summary shown on every interactive GUI launch."""
from tkinter import messagebox
from .version import DISPLAY_NAME

UPDATE_TEXT = '''本次更新：

1. 从禁域列表启动地下城或领取任务时，自动切回地下城并核验，修复 Boundary 等待超时。
2. 补充标准窗口的宝箱“已获得”识别，领取后关闭弹窗并继续探索；不会购买付费宝箱。
3. 保持 0.95 识别阈值、后台点击、有限重试与完成次数核验。

定时关机仍由 Windows 系统任务独立执行，停止 / F8、报错或关闭脚本不取消；仅点击“取消定时关机”撤销。
已有挂机设置和冷却记录保留。关闭本提示后继续使用。'''


def show_update_notice(parent):
    messagebox.showinfo(f'{DISPLAY_NAME} · 本次更新', UPDATE_TEXT, parent=parent)
