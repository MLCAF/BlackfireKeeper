"""Bundled release summary shown on every interactive GUI launch."""
from tkinter import messagebox
from .version import DISPLAY_NAME

UPDATE_TEXT = '''v1.0.0 本次更新：

1. 新增七至十本入口：兽人地堡、巨龙巢穴、古老墓穴、地狱深渊。八至十本默认不勾选，保留已有设置和冷却记录。
2. 修复禁域经验/战利品页面点击后未跳转便一直等待的问题：仅在重新确认同一奖励页面后重试，每个奖励页面最多点击 3 次，间隔至少 4 秒。
3. 补充禁域“继续”按钮的较暗显示模板；维持原识别阈值。探索和免费开箱仍不重复提交。
4. 七本已由用户验证完整流程；八、九、十本已通过入口截图回放，完整战斗流程仍待用户验证。计时器不影响名称识别，冷却状态仍以实际读取结果为准。

错误诊断如何上传：
在主界面切换到“挂机面板”，点击右侧“打开诊断目录”（小窗口可向下滚动）。
找到与报错时间对应的日期时间文件夹，将整个文件夹压缩为 ZIP 后上传；保留 report.json、screen.png、previous.png 等实际生成的全部文件，不要只发单个 JSON、截图或最后一行报错。
若同一次报错生成多个文件夹，请把相关文件夹一起压缩上传。“复制最新报告路径”仅用于定位最新报告，不能代替完整压缩包。

定时关机仍由 Windows 系统任务独立执行；停止 / F8、报错或关闭脚本不取消，仅显式取消才撤销。
现有挂机设置和冷却记录保留。
关闭本提示后继续使用。'''


def show_update_notice(parent):
    messagebox.showinfo(f'{DISPLAY_NAME} · 本次更新', UPDATE_TEXT, parent=parent)
