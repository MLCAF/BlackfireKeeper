"""Bundled release summary shown on every interactive GUI launch."""
from tkinter import messagebox
from .version import DISPLAY_NAME

UPDATE_TEXT = '''本次更新：

1. 定时关机改为 Windows 系统任务，脚本关闭或崩溃后仍执行。
2. 停止 / F8、任务报错或完成均不取消；只有“取消定时关机”会撤销。
3. 到点直接关机，不再等待挂机或额外延迟；会强制关闭应用，请先保存文档。
4. 修复持续黑屏后的截图连接恢复，以及完整通关返回列表时的误报。
5. 每次打开主界面仍显示本次更新说明。

在“定时关机”页设置分钟数或 HH:MM；设置、修改或取消时可能需要 Windows 管理员授权。
两版共用当前 Windows 用户的系统任务。重开脚本可查看或取消原计划。
已有挂机设置和冷却记录保留。关闭本提示后继续使用。'''


def show_update_notice(parent):
    messagebox.showinfo(f'{DISPLAY_NAME} · 本次更新', UPDATE_TEXT, parent=parent)
