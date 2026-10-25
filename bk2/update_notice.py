"""Bundled release summary shown on every interactive GUI launch."""
from tkinter import messagebox
from .version import DISPLAY_NAME

UPDATE_TEXT = '''本次更新：

1. 修复禁域多行战利品弹窗漏识别；停止后重启可接续已显示的经验/战利品奖励，不计入新一轮。
2. 自动战斗按钮重新确认仍未开启时，最多尝试三次；不点击已经开启的按钮。
3. 后台点击增加短按住间隔，改善 Buff、详情按钮偶发不响应；保持原输入模式和点击坐标。
4. 停止挂机后仍可只读重连玩家数据，不恢复挂机、不点击游戏；首次连接扫描预算调整为 60 秒。
5. 识别任务超时后停止旧任务并丢弃旧截图，最多恢复一次；点击发送超时不自动重发。
6. 领取调度等待期间识别到战斗会明确停止提示，避免 Boundary 无说明超时。

定时关机仍由 Windows 系统任务独立执行；停止 / F8、报错或关闭脚本不取消，仅显式取消才撤销。
现有挂机设置和冷却记录保留。
关闭本提示后继续使用。'''


def show_update_notice(parent):
    messagebox.showinfo(f'{DISPLAY_NAME} · 本次更新', UPDATE_TEXT, parent=parent)
